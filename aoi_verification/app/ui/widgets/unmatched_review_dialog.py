"""매치 실패 사진 검토 다이얼로그 (#8).

엑셀 저장 전, ``FinalResult.unmatched_refs`` 의 사진들을 하나씩 다시 검토.
같은 슬롯의 검증 장비 후보를 ``SlotScoreCache`` 점수 내림차순으로 보여주고,
사용자가 클릭으로 매칭을 확정하면 새 ``MatchResult`` 가 누적된다.

- 다이얼로그가 닫힐 때 ``new_matches`` 와 ``resolved_refs`` 가 호출자에게 노출.
- 점수 캐시에 없는 (ref, val) 쌍은 그 자리에서 ``pipeline.score`` 로 계산
  (대부분 Stage 2 precompute 단계에서 이미 캐싱되어 있음).
- 이미 다른 매칭에 쓰인 val 은 후보에서 자동 제외 → 중복 매칭 방지.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QPixmap
from PyQt6.QtWidgets import (QApplication, QDialog, QFrame, QGridLayout,
                              QHBoxLayout, QLabel, QMessageBox, QScrollArea,
                              QSizePolicy, QVBoxLayout, QWidget)

from ... import i18n
from ...models.result import MatchResult, MissEntry
from ...models.slot import ImageItem
from .neon_button import NeonButton

_REF_PX = 420           # 좌측 기준 사진 크기 — 원본 화질에서 다운스케일.
_CAND_PX = 260          # 우측 후보 타일 — 썸네일 대신 원본을 lazy 로드.
_CAND_CAP_PX = 28       # 캡션 한 줄


# ---------------------------------------------------------------------------
def _load_full_pixmap_scaled(path: Path, size: int) -> QPixmap:
    """원본 파일을 그대로 디코드한 뒤 ``size`` 박스에 맞춰 축소.

    캐시된 썸네일/mid 가 아닌 ‘원본 화질’ 을 그대로 보고 싶을 때 사용 — JPEG
    압축이 한 번만 적용된 결과를 사용자가 보게 된다.  full pixmap 은 함수
    스코프 안에서만 살아 있다가 GC 되므로 메모리는 축소된 사본만 유지.
    """
    fallback = QPixmap(size, size)
    fallback.fill(QColor(20, 28, 40))
    try:
        full = QPixmap(str(path))
        if full.isNull():
            return fallback
        return full.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    except Exception:
        return fallback


class _CandidateTile(QFrame):
    """후보 사진 — 클릭하면 매칭 확정. 원본 화질 lazy 로드 (paintEvent 트리거)."""

    picked = pyqtSignal(object)            # ImageItem

    def __init__(self, item: ImageItem, score: float, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self.score = float(score)
        self._image_loaded = False
        self.setProperty("role", "card-soft")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(_CAND_PX + 16, _CAND_PX + _CAND_CAP_PX + 32)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)

        self._img_label = QLabel(self)
        self._img_label.setFixedSize(_CAND_PX, _CAND_PX)
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 우선 placeholder — paintEvent 첫 발생 시 원본을 비동기로 로드.
        ph = QPixmap(_CAND_PX, _CAND_PX)
        ph.fill(QColor(20, 28, 40))
        self._img_label.setPixmap(ph)
        lay.addWidget(self._img_label, alignment=Qt.AlignmentFlag.AlignCenter)

        score_text = f"유사도 {int(round(self.score * 100))}%"
        sc = QLabel(score_text, self)
        sc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sc.setStyleSheet(
            "color: #00FFA3; font-weight: 700; padding: 2px;"
        )
        lay.addWidget(sc)

        from PyQt6.QtGui import QFontMetrics
        cap = QLabel(self)
        cap.setFixedHeight(_CAND_CAP_PX)
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setProperty("role", "muted")
        cap.setWordWrap(False)
        fm = QFontMetrics(cap.font())
        cap.setText(fm.elidedText(
            item.filename, Qt.TextElideMode.ElideMiddle, _CAND_PX - 4,
        ))
        cap.setToolTip(item.filename)
        lay.addWidget(cap)

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        if not self._image_loaded:
            self._image_loaded = True
            # 첫 paint 이벤트 시점 = 위젯이 실제 viewport 에 들어온 시점.
            # 무거운 디코드를 paintEvent 안에서 동기로 돌리면 스크롤이 끊기므로
            # 다음 이벤트 루프 tick 에 지연 실행.
            QTimer.singleShot(0, self._load_full)

    def _load_full(self) -> None:
        try:
            pix = _load_full_pixmap_scaled(Path(self.item.path), _CAND_PX)
            self._img_label.setPixmap(pix)
        except Exception:
            pass

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.picked.emit(self.item)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
class UnmatchedReviewDialog(QDialog):
    """매치 실패한 ref 들을 하나씩 검토해 신규 매칭을 만든다."""

    def __init__(self,
                 unmatched: list[MissEntry],
                 val_pool,
                 already_used_vals: Iterable[Path] = (),
                 score_cache=None,
                 parent=None) -> None:
        """``val_pool`` 키는 두 형태를 모두 지원:

        - ``(slot, side)`` → list[ImageItem]  : cross 모드 양방향 후보
        - ``slot``          → list[ImageItem]  : 단일 모드 호환 (side 무시)
        """
        super().__init__(parent)
        self._unmatched = list(unmatched)
        # (slot, side) 또는 slot 키 모두 받아들이도록 통일.
        self._val_pool_keyed: dict = {}
        for k, v in (val_pool or {}).items():
            self._val_pool_keyed[k] = list(v)
        self._used_vals: set[Path] = {Path(p) for p in already_used_vals}
        self._score_cache = score_cache
        self._idx = 0
        # 결과: 호출자가 다이얼로그가 끝난 뒤 가져갈 데이터.
        self.new_matches: list[MatchResult] = []
        self.resolved_refs: list[MissEntry] = []     # 매칭 찾음
        self.skipped_refs: list[MissEntry] = []      # 사용자가 종료한 것

        # 닫는 즉시 C++ 위젯 해제 — 매번 열 때마다 부모에 누적되지 않도록.
        # exec() 직후엔 Python 측 new_matches/resolved_refs 접근이 여전히 안전.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(
            i18n.KO.UNMATCHED_REVIEW_TITLE.format(n=len(self._unmatched))
        )
        self.setModal(True)
        scr = (self.parent().screen() if self.parent() is not None
               and hasattr(self.parent(), "screen") else None) \
            or QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.resize(min(1400, int(g.width() * 0.92)),
                        min(900, int(g.height() * 0.88)))
        else:
            self.resize(1400, 900)
        self._build()
        self._render_current()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # 상단 진행 + 안내
        head = QHBoxLayout()
        self.progress_label = QLabel("", self)
        self.progress_label.setStyleSheet(
            "color: #00D4FF; font-weight: 700; font-size: 15px;"
        )
        head.addWidget(self.progress_label)
        head.addStretch(1)
        # 네비게이션 버튼
        self.btn_prev = NeonButton(i18n.KO.BTN_UNMATCHED_PREV, role="ghost")
        self.btn_prev.clicked.connect(self._go_prev)
        head.addWidget(self.btn_prev)
        self.btn_skip = NeonButton(i18n.KO.BTN_UNMATCHED_NEXT, role="warn")
        self.btn_skip.clicked.connect(self._skip)
        head.addWidget(self.btn_skip)
        self.btn_close = NeonButton(i18n.KO.BTN_UNMATCHED_CLOSE, role="primary")
        self.btn_close.clicked.connect(self.accept)
        head.addWidget(self.btn_close)
        root.addLayout(head)

        hint = QLabel(i18n.KO.UNMATCHED_REVIEW_HINT, self)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #7FB3D5; padding: 4px;")
        root.addWidget(hint)

        # 본문: 좌(기준 사진) + 우(후보 그리드)
        body = QHBoxLayout()
        body.setSpacing(16)

        # LEFT: 기준 사진
        left = QFrame(self)
        left.setProperty("role", "section")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(12, 12, 12, 12)
        ll.setSpacing(6)
        ref_title = QLabel(i18n.KO.PANEL_MATCH_REF, left)
        ref_title.setStyleSheet("color: #00D4FF; font-weight: 700;")
        ref_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ll.addWidget(ref_title)
        self.ref_filename = QLabel("", left)
        self.ref_filename.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ref_filename.setStyleSheet("color: #7FB3D5; padding: 2px;")
        self.ref_filename.setWordWrap(True)
        ll.addWidget(self.ref_filename)
        self.ref_img = QLabel(left)
        self.ref_img.setFixedSize(_REF_PX, _REF_PX)
        self.ref_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ref_img.setStyleSheet(
            "background: #050810; border: 1px solid #1F2A3F; border-radius: 6px;"
        )
        ll.addWidget(self.ref_img, alignment=Qt.AlignmentFlag.AlignCenter)
        ll.addStretch(1)
        left.setFixedWidth(_REF_PX + 40)
        body.addWidget(left)

        # RIGHT: 후보 그리드 (스크롤)
        right = QFrame(self)
        right.setProperty("role", "section")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(12, 12, 12, 12)
        rl.setSpacing(6)
        cand_title = QLabel(i18n.KO.PANEL_MATCH_CANDIDATES, right)
        cand_title.setStyleSheet("color: #00D4FF; font-weight: 700;")
        rl.addWidget(cand_title)
        self.candidates_summary = QLabel("", right)
        self.candidates_summary.setStyleSheet("color: #7FB3D5; padding: 2px;")
        rl.addWidget(self.candidates_summary)
        self._scroll = QScrollArea(right)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._host = QWidget()
        self._grid = QGridLayout(self._host)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setSpacing(10)
        self._scroll.setWidget(self._host)
        rl.addWidget(self._scroll, stretch=1)
        body.addWidget(right, stretch=1)

        root.addLayout(body, stretch=1)

    # ------------------------------------------------------------------
    def _clear_grid(self) -> None:
        while self._grid.count():
            it = self._grid.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

    # ------------------------------------------------------------------
    def _current(self) -> Optional[MissEntry]:
        if self._idx < 0 or self._idx >= len(self._unmatched):
            return None
        return self._unmatched[self._idx]

    def _render_current(self) -> None:
        cur = self._current()
        if cur is None:
            self._show_done()
            return

        total = len(self._unmatched)
        self.progress_label.setText(
            i18n.KO.UNMATCHED_REVIEW_PROGRESS_FMT.format(
                idx=self._idx + 1, total=total, slot=cur.slot,
            )
        )
        self.btn_prev.setEnabled(self._idx > 0)
        self.ref_filename.setText(Path(cur.path).name)

        # 기준 사진 — 원본 파일을 직접 디코드해서 ‘원본 화질’ 그대로 보여준다.
        # 현재 ref 한 장만 로드되므로 비용은 낮다.
        pm = _load_full_pixmap_scaled(Path(cur.path), _REF_PX)
        self.ref_img.setPixmap(pm)

        # 후보 = 같은 슬롯의 val_pool 중 (a) 이미 다른 매칭에 쓰이지 않은 항목.
        pool = (self._val_pool_keyed.get((cur.slot, cur.side))
                or self._val_pool_keyed.get(cur.slot)
                or [])
        candidates = [
            v for v in pool
            if Path(v.path) not in self._used_vals
        ]
        scored: list[tuple[float, ImageItem]] = []
        if candidates:
            # 점수 캐시 hit 이 대부분이지만, miss 시 pipeline.score 가 무거워
            # UI 가 잠깐 굳을 수 있다. 모래시계 커서로 사용자에게 알린다.
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
            try:
                for v in candidates:
                    s = self._lookup_or_compute_score(cur, v)
                    scored.append((s, v))
            finally:
                QApplication.restoreOverrideCursor()
            scored.sort(key=lambda x: x[0], reverse=True)

        self._clear_grid()
        if not scored:
            empty = QLabel(i18n.KO.UNMATCHED_REVIEW_NO_CANDIDATES, self._host)
            empty.setStyleSheet("color: #7FB3D5; padding: 20px;")
            self._grid.addWidget(empty, 0, 0)
            self.candidates_summary.setText("후보 0 장")
            return

        self.candidates_summary.setText(f"후보 {len(scored)} 장 (유사도 순)")
        cols = max(3, min(5, self._scroll.viewport().width() // (_CAND_PX + 24)))
        if cols <= 0:
            cols = 4
        for i, (score, v) in enumerate(scored):
            tile = _CandidateTile(v, score, parent=self._host)
            tile.picked.connect(self._on_pick)
            self._grid.addWidget(tile, i // cols, i % cols)

    # ------------------------------------------------------------------
    def _lookup_or_compute_score(self,
                                  ref: MissEntry,
                                  val: ImageItem) -> float:
        """캐시 우선, 없으면 즉석 계산."""
        ref_path = Path(ref.path)
        val_path = Path(val.path)
        if self._score_cache is not None:
            s = self._score_cache.get_pair(ref.slot, ref_path, val_path)
            if s is not None:
                return float(s)
        # 캐시 miss — pipeline 으로 직접 계산. 캐시에 저장해서 재방문 시 빠르게.
        try:
            from ...similarity import pipeline as _pipeline
            rf = _pipeline.extract(ref_path)
            vf = _pipeline.extract(val_path)
            s = float(_pipeline.score(rf, vf))
        except Exception:
            s = 0.0
        if self._score_cache is not None:
            try:
                self._score_cache.put(ref.slot, ref_path, val_path, s)
            except Exception:
                pass
        return s

    # ------------------------------------------------------------------
    def _on_pick(self, val_item: ImageItem) -> None:
        cur = self._current()
        if cur is None:
            return
        cur_path = Path(cur.path)
        cand_path = Path(val_item.path)
        score = self._lookup_or_compute_score(cur, val_item)
        # MatchResult 컨벤션 (main_window._merge_matches 와 일치):
        #   ref_path = ‘낮은 호기 (또는 ref 측)’ 경로,
        #   val_path = ‘높은 호기 (또는 val 측)’ 경로.
        # Phase A 미매칭(side="ref")은 cur 가 ref 측 → 그대로 둔다.
        # Phase B 미매칭(side="val")은 cur 가 val 측(높은 호기), candidate 가
        # ref 측(낮은 호기) → ref/val 을 교환해서 엑셀의 C/D 컬럼이 호기 라벨
        # 과 일치하도록.
        if cur.side == "val":
            ref_path, val_path = cand_path, cur_path
            direction = "B→A"
        else:
            ref_path, val_path = cur_path, cand_path
            direction = "A→B"
        self.new_matches.append(MatchResult(
            slot=cur.slot,
            ref_path=ref_path,
            val_path=val_path,
            score=float(score),
            direction=direction,
        ))
        self.resolved_refs.append(cur)
        self._used_vals.add(cand_path)
        # 같은 ref 가 다른 곳에서 다시 나오지 않도록 idx 만 전진.
        self._idx += 1
        self._render_current()

    def _skip(self) -> None:
        cur = self._current()
        if cur is not None:
            self.skipped_refs.append(cur)
        self._idx += 1
        self._render_current()

    def _go_prev(self) -> None:
        if self._idx <= 0:
            return
        # 이전 ref 로 가면서, 그 ref 가 (a) 이전에 매칭으로 확정됐다면 그 매칭을
        # 되돌리고 val 을 다시 사용 가능으로 풀어준다.
        self._idx -= 1
        cur = self._current()
        if cur is None:
            return
        # 되돌릴 신규 매칭이 있으면 제거. side="val" 매칭은 ref/val 을 교환해
        # 저장하므로 cur 가 m.ref_path / m.val_path 중 어디에 있는지 양쪽 모두 검사.
        for i in range(len(self.new_matches) - 1, -1, -1):
            m = self.new_matches[i]
            if m.slot != cur.slot:
                continue
            mr = Path(m.ref_path)
            mv = Path(m.val_path)
            cp = Path(cur.path)
            if cp == mr:
                self._used_vals.discard(mv)
            elif cp == mv:
                self._used_vals.discard(mr)
            else:
                continue
            self.new_matches.pop(i)
            # resolved_refs 에서도 동일 ref 한 건 제거
            for j, r in enumerate(self.resolved_refs):
                if r.slot == cur.slot and Path(r.path) == cp:
                    self.resolved_refs.pop(j)
                    break
            break
        # skip 으로 마크된 경우엔 그 항목만 풀어준다.
        for i in range(len(self.skipped_refs) - 1, -1, -1):
            r = self.skipped_refs[i]
            if (r.slot == cur.slot
                    and Path(r.path) == Path(cur.path)):
                self.skipped_refs.pop(i)
                break
        self._render_current()

    # ------------------------------------------------------------------
    def _show_done(self) -> None:
        self._clear_grid()
        self.progress_label.setText(
            i18n.KO.UNMATCHED_REVIEW_DONE_FMT.format(n=len(self.new_matches))
        )
        self.ref_filename.setText("")
        self.ref_img.clear()
        self.candidates_summary.setText("")
        self.btn_prev.setEnabled(self._idx > 0)
        self.btn_skip.setEnabled(False)

    # ------------------------------------------------------------------
    @staticmethod
    def show_empty_message(parent) -> None:
        QMessageBox.information(
            parent, i18n.KO.APP_TITLE, i18n.KO.UNMATCHED_REVIEW_EMPTY,
        )
