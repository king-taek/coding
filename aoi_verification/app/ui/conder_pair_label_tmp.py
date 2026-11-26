"""[Conder짝수집-임시] Conder 짝 확인 버튼 — **확인이 끝나면 지우는 코드**다.

개발자 모드(Ctrl+Shift+D)일 때만 상태바에 버튼 하나가 더 보인다.  누르면 ``dev/conder_pair_candidates.json`` 의
후보(같은 웨이퍼의 KLA 결함 사진 · Camtek 결함 사진 한 쌍)를 한 쌍씩 나란히 보여 주고 사용자가 '매치 / 모름' 을 고른다.
거리 · 오프셋은 일부러 보이지 않는다(눈으로만 판단).  답은 누를 때마다 기본 출력 폴더의 ``conder짝_확인.csv`` 에 저장되고,
다시 열면 첫 미응답부터 이어서 한다.  끝나거나 닫으면 ``<날짜>/conder짝_확인_<HHMM>.zip`` 을 만든다.

지우는 법(전부 ``[Conder짝수집-임시]`` 로 검색된다 — ``test_conder_pair_label_tmp.py`` 가 이 목록을 지킨다):

1. 이 파일 · ``dev/tests/test_conder_pair_label_tmp.py``
2. ``ui/main_window.py`` 의 표시된 두 줄(``conder_pair_label_tmp`` · ``_cpair_label_btn``)
3. ``i18n/ko.py`` 의 ``CPAIR_LABEL_*`` 줄들(표시된 블록 안)
4. 후보 파일이 필요 없으면 ``dev/conder_pair_candidates.json``
"""

from __future__ import annotations

import csv
import importlib.util
import json
import logging
import os
import zipfile
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QPushButton,
                             QSizePolicy, QVBoxLayout)

from .. import i18n
from ..utils import paths, prefs
from .widgets import sheet_host as sheets
from .widgets.neon_button import NeonButton

_LOG = logging.getLogger("aoi.conder_pair_label")
_TOOL = "dev/conder_pair_collect.py"
_CANDIDATES = "dev/conder_pair_candidates.json"
CSV_NAME = i18n.KO.CPAIR_LABEL_CSV_NAME
FIELDS = ("id", "answer", "kla_missing", "camtek_missing", "answered_at")
MATCH = i18n.KO.CPAIR_LABEL_MATCH
UNKNOWN = i18n.KO.CPAIR_LABEL_UNKNOWN


# ── Qt 없이 도는 순수 함수 ─────────────────────────────────────────────
def photo_path(root: str, rel: str) -> Path:
    """Conder 루트 + 상대 경로('/' 구분) → 경로.  루트가 UNC 여도 그대로 붙는다."""
    return Path(root).joinpath(*[p for p in rel.replace("\\", "/").split("/") if p])


def load_answers(csv_path: Path) -> dict:
    """CSV → ``{id: 행}``.  없거나 못 읽으면 빈 dict."""
    answers = {}
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                try:
                    answers[int(row["id"])] = {k: row.get(k, "") for k in FIELDS} | {"id": int(row["id"])}
                except (KeyError, TypeError, ValueError):
                    continue
    except OSError:
        pass
    return answers


def save_answers(csv_path: Path, answers: dict) -> None:
    """임시 파일에 쓰고 바꿔 끼운다 — 쓰다 멈춰도 옛 파일이 남는다.  id 순서로 쓴다."""
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = csv_path.with_name(csv_path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for i in sorted(answers):
            w.writerow({k: answers[i].get(k, "") for k in FIELDS})
    os.replace(tmp, csv_path)


def make_row(cand_id: int, answer: str, kla_missing: bool, camtek_missing: bool, now: datetime) -> dict:
    return {"id": int(cand_id), "answer": answer, "kla_missing": int(bool(kla_missing)),
            "camtek_missing": int(bool(camtek_missing)), "answered_at": now.isoformat(timespec="seconds")}


def next_unanswered(cands: list, answers: dict, after: int = -1):
    """``after`` 다음 칸부터 끝까지, 없으면 처음부터 훑어 첫 미응답 위치.  전부 답했으면 None."""
    n = len(cands)
    for k in range(1, n + 1):
        i = (after + k) % n
        if cands[i]["id"] not in answers:
            return i
    return None


def write_zip(csv_path: Path, base: Path, now: datetime):
    """CSV 를 ``<base>/<YYYYMMDD>/conder짝_확인_<HHMM>.zip`` 에 담는다.  답이 없으면 만들지 않고 None."""
    if not load_answers(csv_path):
        return None
    folder = Path(base) / now.strftime("%Y%m%d")
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / i18n.KO.CPAIR_LABEL_ZIP_FMT.format(hhmm=now.strftime("%H%M"))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(csv_path, CSV_NAME)
    return out


def _load_tool():
    path = paths.resource_path(_TOOL)
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("conder_pair_collect", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_candidates():
    path = paths.resource_path(_CANDIDATES)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) and data else None


# ── 화면 ──────────────────────────────────────────────────────────────
class _PhotoView(QLabel):
    """사진 한 장 — 칸에 맞춰 비율 유지로 줄인다.  못 읽으면 안내 문구."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pix = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def load(self, path: Path) -> bool:
        """읽으면 True.  없거나 깨졌으면 문구를 띄우고 False."""
        pix = QPixmap(str(path)) if Path(path).is_file() else QPixmap()
        if pix.isNull():
            self._pix = None
            self.setPixmap(QPixmap())
            self.setText(i18n.KO.CPAIR_LABEL_PHOTO_MISSING)
            return False
        self._pix = pix
        self.setText("")
        self._rescale()
        return True

    def _rescale(self) -> None:
        if self._pix is not None:
            self.setPixmap(self._pix.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._rescale()


class PairLabelDialog(QDialog):
    def __init__(self, parent, cands: list, root: str, base: Path, now=datetime.now) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(i18n.KO.CPAIR_LABEL_TITLE)
        self.resize(1400, 800)
        self._cands, self._root, self._base, self._now = cands, root, Path(base), now
        self._csv = self._base / CSV_NAME
        self._answers = load_answers(self._csv)
        self._finished = False
        self.zip_path = None
        self._missing = (False, False)
        self.save_error = ""

        lay = QVBoxLayout(self)
        self._progress = QLabel()
        self._progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = self._progress.font()
        f.setPointSize(f.pointSize() + 4)
        f.setBold(True)
        self._progress.setFont(f)
        self._meta = QLabel()
        self._meta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._meta.setStyleSheet("color: #888;")
        lay.addWidget(self._progress)
        lay.addWidget(self._meta)

        photos = QHBoxLayout()
        self._views = []
        for title in (i18n.KO.CPAIR_LABEL_KLA, i18n.KO.CPAIR_LABEL_CAMTEK):
            col = QVBoxLayout()
            head = QLabel(title)
            head.setAlignment(Qt.AlignmentFlag.AlignCenter)
            view = _PhotoView()
            col.addWidget(head)
            col.addWidget(view, 1)
            photos.addLayout(col, 1)
            self._views.append(view)
        lay.addLayout(photos, 1)

        row = QHBoxLayout()
        self.back_btn = QPushButton(i18n.KO.CPAIR_LABEL_BACK)
        self.back_btn.clicked.connect(self.go_back)
        self.match_btn = NeonButton(i18n.KO.CPAIR_LABEL_MATCH, "primary")
        self.unknown_btn = NeonButton(i18n.KO.CPAIR_LABEL_UNKNOWN, "ghost")
        for b in (self.back_btn, self.match_btn, self.unknown_btn):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for b in (self.match_btn, self.unknown_btn):
            b.setMinimumHeight(56)
        self.match_btn.clicked.connect(lambda: self.answer(MATCH))
        self.unknown_btn.clicked.connect(lambda: self.answer(UNKNOWN))
        row.addWidget(self.back_btn)
        row.addStretch(1)
        row.addWidget(self.match_btn, 2)
        row.addWidget(self.unknown_btn, 2)
        row.addStretch(1)
        lay.addLayout(row)

        first = next_unanswered(cands, self._answers)
        self.index = first if first is not None else len(cands) - 1
        self._show()

    # 화면 갱신
    def _show(self) -> None:
        c = self._cands[self.index]
        self._progress.setText(i18n.KO.CPAIR_LABEL_PROGRESS_FMT.format(n=self.index + 1, total=len(self._cands)))
        self._meta.setText(i18n.KO.CPAIR_LABEL_META_FMT.format(
            lot=str(c.get("lot", "")).replace("\\", "/").split("/")[-1], wafer=c.get("wafer", ""),
            kla=c.get("kla_step", ""), camtek=c.get("camtek_step", "")))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            ok_k = self._views[0].load(photo_path(self._root, c.get("kla", "")))
            ok_c = self._views[1].load(photo_path(self._root, c.get("camtek", "")))
        finally:
            QApplication.restoreOverrideCursor()
        self._missing = (not ok_k, not ok_c)
        self.back_btn.setEnabled(self.index > 0)

    # 동작
    def answer(self, value: str) -> None:
        c = self._cands[self.index]
        self._answers[c["id"]] = make_row(c["id"], value, self._missing[0], self._missing[1], self._now())
        try:
            save_answers(self._csv, self._answers)
        except OSError as e:
            self.save_error = f"{type(e).__name__}: {e}"
            _LOG.exception("Conder 짝 확인 저장 실패")
        nxt = next_unanswered(self._cands, self._answers, self.index)
        if nxt is None:
            self.accept()
            return
        self.index = nxt
        self._show()

    def go_back(self) -> None:
        if self.index > 0:
            self.index -= 1
            self._show()

    @property
    def done_count(self) -> int:
        return len(self._answers)

    @property
    def all_done(self) -> bool:
        return self.done_count >= len(self._cands)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_1:
            self.answer(MATCH)
        elif key == Qt.Key.Key_2:
            self.answer(UNKNOWN)
        elif key == Qt.Key.Key_Backspace:
            self.go_back()
        else:
            super().keyPressEvent(event)

    def done(self, result: int) -> None:
        """닫는 길이 여럿(끝 · 닫기 · Esc)이라 한 곳에서 zip 을 만든다."""
        if not self._finished:
            self._finished = True
            try:
                self.zip_path = write_zip(self._csv, self._base, self._now())
            except OSError:
                _LOG.exception("Conder 짝 확인 zip 실패")
        super().done(result)


def open_dialog(window) -> None:
    tool = _load_tool()
    cands = _load_candidates()
    if tool is None or cands is None:
        sheets.warn(window, i18n.KO.CPAIR_LABEL_TITLE, i18n.KO.CPAIR_LABEL_NO_TOOL)
        return
    dlg = PairLabelDialog(window, cands, tool.CONDER_ROOTS[0], tool.default_out_base())
    sheets.run(dlg, full_bleed=True)
    if dlg.save_error:
        sheets.warn(window, i18n.KO.CPAIR_LABEL_TITLE, i18n.KO.CPAIR_LABEL_SAVE_FAIL_FMT.format(error=dlg.save_error))
    if dlg.zip_path is None:
        return
    folder = str(dlg.zip_path.parent)
    text = (i18n.KO.CPAIR_LABEL_DONE_FMT.format(path=folder) if dlg.all_done else
            i18n.KO.CPAIR_LABEL_EARLY_FMT.format(done=dlg.done_count, total=len(cands), path=folder))
    sheets.info(window, i18n.KO.CPAIR_LABEL_TITLE, text)


def attach(window) -> None:
    """상태바에 버튼을 단다.  ``window._cpair_label_btn`` 으로 보이기/숨기기만 한다."""
    window._cpair_label_btn = QPushButton(window)
    window._cpair_label_btn.hide()
    try:
        btn = QPushButton(i18n.KO.CPAIR_LABEL_BUTTON, window.statusBar())
        btn.setFlat(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda: open_dialog(window))
        window.statusBar().addPermanentWidget(btn)
        btn.setVisible(prefs.load().dev_mode)
    except Exception:                                    # noqa: BLE001 — 임시 버튼이 앱 시작을 막으면 안 된다
        _LOG.exception("Conder 짝 확인 버튼을 붙이지 못했습니다")
        return
    window._cpair_label_btn.deleteLater()
    window._cpair_label_btn = btn
