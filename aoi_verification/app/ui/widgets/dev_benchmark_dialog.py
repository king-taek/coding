"""개발자 벤치마크 다이얼로그 — 매칭 가속 조합(레시피) 실험·기록 GUI.

개발자 모드(환경변수 ``AOI_DEV_MODE`` 또는 prefs.dev_mode)에서만 진입 가능.
헤드리스 코어(``app.dev.benchmark``)를 그대로 호출하고, 실행은 워커 스레드에서
돌려 UI 를 막지 않는다.  **유사도 캐시를 우회**(처음 매칭처럼)하며 정확도가
떨어지는 조합은 추천하지 않는다.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFormLayout,
                              QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                              QMessageBox, QPushButton, QScrollArea, QSpinBox,
                              QTableWidget, QTableWidgetItem, QVBoxLayout,
                              QWidget)

from ... import i18n
from ...dev import benchmark as _bm
from ...dev import recipes as _rx
from ...utils import paths as _paths
from ...utils import prefs as _prefs


def dev_mode_enabled() -> bool:
    """개발자 모드 켜짐 여부 — 환경변수 또는 prefs 플래그."""
    env = str(os.environ.get("AOI_DEV_MODE", "")).strip()
    if env not in ("", "0", "false", "False"):
        return True
    try:
        return bool(getattr(_prefs.load(), "dev_mode", False))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 워커 — 데이터셋 구성 + 스위트 실행 + 기록 (UI 비차단)
# ---------------------------------------------------------------------------
class _BenchSignals(QObject):
    progress = pyqtSignal(str, int, int)        # name, done, total
    finished = pyqtSignal(object, object, str)   # suite, ds, run_dir
    failed = pyqtSignal(str)


class _BenchWorker(QThread):
    """레시피 실행을 **자식 프로세스로 격리**해 스위트를 돌리는 워커.

    레시피 실행은 OpenVINO/NPU 등 네이티브 호출이라 드라이버 버그로 **프로세스가
    통째로 죽을(segfault)** 수 있다.  파이썬 예외/스레드 타임아웃으로는 못 막으므로,
    실행을 자식으로 분리한다.  자식이 죽어도 (a) 이 GUI 프로세스는 안 죽고, (b) 어느
    레시피가 죽였는지 기록하며, (c) 살아남은 레시피는 이어서 측정한다(``drive_isolated_suite``).
    데이터셋 준비(폴더 스캔·self-test 증강)는 네이티브 추론이 아니므로 부모에서 한 번만 한다.
    """

    def __init__(self, *, ref_root: str, val_root: str, self_test: bool,
                 recipe_keys: List[str], timeout: float, max_slots: int,
                 max_images: int, labels_path: str = "",
                 all_recipes: bool = False, explicit_keys=None,
                 parent=None) -> None:
        super().__init__(parent)
        self._ref = ref_root
        self._val = val_root
        self._self_test = self_test
        self._keys = recipe_keys
        self._timeout = timeout
        self._max_slots = max_slots
        self._max_images = max_images
        self._labels_path = labels_path
        self._all_recipes = all_recipes
        self._explicit_keys = set(explicit_keys or [])
        self._stop = threading.Event()
        self.signals = _BenchSignals()
        self._tmp: Optional[str] = None
        self._out_dir = Path(tempfile.mkdtemp(prefix="aoi_bench_runs_"))
        self._proc: Optional[subprocess.Popen] = None

    def stop(self) -> None:
        self._stop.set()
        p = self._proc
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def run(self) -> None:        # type: ignore[override]
        try:
            labels = None
            val_root = self._val
            if self._self_test:
                self._tmp = tempfile.mkdtemp(prefix="aoi_bench_val_")
                labels = _bm.synthesize_val(self._ref, self._tmp)
                val_root = self._tmp
            elif self._labels_path:
                # 사용자가 만든 정답 라벨 → 실제 정확도(recall@K) 측정.
                from ...dev import labels as _lab
                loaded = _lab.load(self._labels_path)
                labels = loaded or None

            # 부모가 데이터셋을 한 번 만든다(보고서 메타·자식과 동일 입력 확인용).
            ds = _bm.build_dataset(self._ref, val_root, labels=labels,
                                   max_slots=self._max_slots,
                                   max_images_per_side=self._max_images)
            if not ds.tasks:
                self.signals.failed.emit(i18n.KO.DEV_BENCH_NO_COMMON)
                return

            # 자식에 넘길 고정 라벨 경로 — self-test 로 합성한 라벨을 임시 JSON 으로 저장.
            labels_path = self._labels_path
            if self._self_test and labels:
                from ...dev import labels as _lab
                labels_path = str(_lab.save(
                    Path(self._tmp) / "_bench_labels.json", labels))

            keys = [r.key for r in (_rx.select(self._keys) if self._keys
                                    else list(_rx.REGISTRY))]

            spawn = self._make_spawn(val_root, labels_path)
            suite = _bm.drive_isolated_suite(keys, spawn=spawn,
                                             stop=self._stop.is_set)
            run_dir = _bm.write_report(suite, ds)
            self.signals.finished.emit(suite, ds, str(run_dir))
        except Exception as exc:        # pragma: no cover - 방어
            self.signals.failed.emit(str(exc))

    # ------------------------------------------------------------------
    def _make_spawn(self, val_root: str, labels_path: str):
        """``drive_isolated_suite`` 가 부를 spawn(keys)->ChildOutcome 을 만든다."""
        root = str(_paths._project_root())

        def _spawn(keys_subset: List[str]) -> "_bm.ChildOutcome":
            return self._run_child(keys_subset, val_root, labels_path, root)

        return _spawn

    def _run_child(self, keys_subset: List[str], val_root: str,
                   labels_path: str, root: str) -> "_bm.ChildOutcome":
        explicit = [k for k in keys_subset if k in self._explicit_keys]
        cmd = [sys.executable, "-m", "aoi_verification.app.dev.benchmark",
               "--ref", self._ref, "--val", val_root,
               "--recipes", ",".join(keys_subset),
               "--explicit", ",".join(explicit),
               "--out", str(self._out_dir), "--emit-progress"]
        if labels_path:
            cmd += ["--labels", labels_path]
        if self._max_slots:
            cmd += ["--max-slots", str(self._max_slots)]
        if self._max_images:
            cmd += ["--max-images", str(self._max_images)]
        if self._timeout:
            cmd += ["--timeout", str(self._timeout)]
        if self._all_recipes:
            cmd += ["--all-recipes"]

        env = dict(os.environ)
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("PYTHONUNBUFFERED", "1")
        proc = subprocess.Popen(
            cmd, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env)
        self._proc = proc

        # stdout 을 별도 스레드로 읽어 큐에 넣고, 메인은 큐를 타임아웃으로 폴링한다
        # (한 레시피가 너무 오래 무응답이면 멈춘 것으로 보고 강제 종료 — 안전망).
        q: "queue.Queue" = queue.Queue()

        def _reader():
            try:
                for line in proc.stdout:    # type: ignore[union-attr]
                    q.put(line)
            finally:
                q.put(None)

        rt = threading.Thread(target=_reader, daemon=True)
        rt.start()

        # 무응답 허용 시간 — 레시피별 타임아웃(자식이 자체로 끊음)보다 넉넉히.
        if self._timeout and self._timeout > 0:
            grace = self._timeout * 3 + 180
        else:
            grace = 1800.0                  # 무제한 타임아웃이면 30분 안전망

        run_dir = ""
        last_key = ""
        killed = False
        while True:
            if self._stop.is_set():
                self._kill(proc)
                killed = True
                break
            try:
                line = q.get(timeout=grace)
            except queue.Empty:
                self._kill(proc)            # 무응답 → 멈춘 것으로 보고 종료(크래시 취급)
                killed = True
                break
            if line is None:
                break                       # EOF — 자식 종료
            last_key, rd = self._handle_line(line, last_key)
            if rd:
                run_dir = rd

        try:
            rc = proc.wait(timeout=10)
        except Exception:
            self._kill(proc)
            rc = -9
        if killed and rc == 0:
            rc = -9                         # 우리가 죽였으면 비정상으로 본다

        payload = self._read_payload(run_dir)
        return _bm.ChildOutcome(returncode=int(rc), last_started_key=last_key,
                                payload=payload)

    # ------------------------------------------------------------------
    def _handle_line(self, line: str, last_key: str):
        """자식 stdout 한 줄 처리 — 진행률/런디렉토리 파싱.  (last_key, run_dir) 반환."""
        line = line.rstrip("\n")
        run_dir = ""
        if line.startswith("@@AOI_RUNDIR\t"):
            run_dir = line.split("\t", 1)[1]
        elif line.startswith("@@AOI_PROG\t"):
            parts = line.split("\t")
            if len(parts) >= 6:
                tag, done, total, key, name = parts[1:6]
                if tag == "start" and key:
                    last_key = key
                try:
                    self.signals.progress.emit(str(name), int(done), int(total))
                except Exception:
                    pass
        return last_key, run_dir

    def _read_payload(self, run_dir: str) -> Optional[dict]:
        if not run_dir:
            return None
        rj = Path(run_dir) / "result.json"
        if not rj.exists():
            return None
        try:
            return json.loads(rj.read_text(encoding="utf-8"))
        except Exception:
            return None

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 다이얼로그
# ---------------------------------------------------------------------------
class DevBenchmarkDialog(QDialog):
    def __init__(self, parent=None, *, default_ref: str = "",
                 default_val: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.DEV_BENCH_TITLE)
        self._worker: Optional[_BenchWorker] = None
        self._recipe_checks: dict = {}
        self._build(default_ref, default_val)

    # ------------------------------------------------------------------
    def _build(self, default_ref: str, default_val: str) -> None:
        root = QVBoxLayout(self)

        hint = QLabel(i18n.KO.DEV_BENCH_HINT, self)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #7FB3D5;")
        root.addWidget(hint)

        form = QFormLayout()
        self.ref_edit = QLineEdit(default_ref, self)
        form.addRow(i18n.KO.DEV_BENCH_REF_LABEL, self._with_browse(self.ref_edit))
        self.val_edit = QLineEdit(default_val, self)
        form.addRow(i18n.KO.DEV_BENCH_VAL_LABEL, self._with_browse(self.val_edit))

        self.self_test = QCheckBox(i18n.KO.DEV_BENCH_SELFTEST, self)
        self.self_test.setChecked(not default_val)
        self.self_test.toggled.connect(self._on_selftest_toggled)
        self.val_edit.setEnabled(not self.self_test.isChecked())
        form.addRow("", self.self_test)

        # 불필요 스킵 해제 — 기본은 함정/대조·폴백중복·과거저성능을 자동 제외한다.
        self.all_recipes = QCheckBox(i18n.KO.DEV_BENCH_ALL_RECIPES, self)
        self.all_recipes.setChecked(False)
        self.all_recipes.setToolTip(i18n.KO.DEV_BENCH_ALL_RECIPES_TIP)
        form.addRow("", self.all_recipes)

        # 정답 라벨 파일(선택) — 있으면 recall@K(실제 정확도)로 측정.  옆 버튼으로
        # 라벨 만들기 다이얼로그를 연다.  자기검증이 켜져 있으면 무시된다.
        self.labels_edit = QLineEdit("", self)
        labels_row = self._with_browse(self.labels_edit, folder=False)
        make_btn = QPushButton(i18n.KO.DEV_LABEL_BUTTON, self)
        make_btn.clicked.connect(self._open_label_maker)
        labels_row.layout().addWidget(make_btn)
        form.addRow(i18n.KO.DEV_LABEL_PATH_LABEL, labels_row)

        self.timeout_spin = QSpinBox(self)
        self.timeout_spin.setRange(0, 3600)
        self.timeout_spin.setValue(120)
        form.addRow(i18n.KO.DEV_BENCH_TIMEOUT_LABEL, self.timeout_spin)

        self.maxslots_spin = QSpinBox(self)
        self.maxslots_spin.setRange(0, 100000)
        form.addRow(i18n.KO.DEV_BENCH_MAXSLOTS_LABEL, self.maxslots_spin)

        self.maximg_spin = QSpinBox(self)
        self.maximg_spin.setRange(0, 100000)
        form.addRow(i18n.KO.DEV_BENCH_MAXIMG_LABEL, self.maximg_spin)
        root.addLayout(form)

        root.addWidget(QLabel(i18n.KO.DEV_BENCH_RECIPES, self))

        # 프리셋 — 기본은 '빠른'(핵심 소수).  실측상 임베딩 장치 교체는 속도 이득이
        # 거의 없고(×1.02), 3배의 레버는 'CPU 재채점 축소'라 빠른 프리셋은 현행·기준선과
        # 재채점 경량/병렬 후보를 함께 본다.  '표준'=core 13, '전체'=확장 그룹까지.
        preset_hint = QLabel(i18n.KO.DEV_BENCH_PRESET_HINT, self)
        preset_hint.setWordWrap(True)
        preset_hint.setStyleSheet("color: #9AA;")
        root.addWidget(preset_hint)
        preset_row = QHBoxLayout()
        for label, name in ((i18n.KO.DEV_BENCH_PRESET_QUICK, "quick"),
                            (i18n.KO.DEV_BENCH_PRESET_FACEOFF, "faceoff"),
                            (i18n.KO.DEV_BENCH_PRESET_CORE, "core"),
                            (i18n.KO.DEV_BENCH_PRESET_ALL, "all")):
            btn = QPushButton(label, self)
            btn.clicked.connect(lambda _=False, n=name: self._apply_preset(n))
            preset_row.addWidget(btn)
        preset_row.addStretch(1)
        root.addLayout(preset_row)

        # 확장 그룹 토글 — 체크하면 그 그룹 전체(NPU 사용방식 스윕/NPU 단독/고속
        # 재채점/모델 주머니)를 실험에 포함한다(개별 레시피는 아래 목록).
        grp_row = QHBoxLayout()
        self._group_checks = {}
        for gkey, glabel in (
            ("center", i18n.KO.DEV_BENCH_GROUP_CENTER),
            ("npu-sweep", i18n.KO.DEV_BENCH_GROUP_NPU_SWEEP),
            ("npu-only", i18n.KO.DEV_BENCH_GROUP_NPU_ONLY),
            ("fast-rerank", i18n.KO.DEV_BENCH_GROUP_FAST_RERANK),
            ("model-zoo", i18n.KO.DEV_BENCH_GROUP_MODEL_ZOO),
        ):
            n = len(_rx.group(gkey))
            cb = QCheckBox(f"{glabel} (+{n})", self)
            cb.setChecked(False)
            self._group_checks[gkey] = cb
            grp_row.addWidget(cb)
        grp_row.addStretch(1)
        root.addLayout(grp_row)

        # 개별 레시피 목록 — core 13 + (빠른 프리셋에 든 fast-rerank 후보).  빠른 프리셋
        # 키만 기본 체크해 항목 수를 줄인다.  나머지는 그룹 토글/전체 프리셋으로 펼친다.
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(200)
        host = QWidget()
        hl = QVBoxLayout(host)
        core_set = {r.key for r in _rx.REGISTRY}
        # core 외 추가 체크박스 = 빠른(린) + 대결 프리셋이 쓰는 생존자/중앙-인식 키.
        extra, _seen = [], set()
        for k in list(_rx.QUICK_KEYS) + list(_rx.FACEOFF_KEYS):
            if k not in core_set and k not in _seen:
                _seen.add(k)
                extra.append(_rx.by_key(k))
        for r in list(_rx.REGISTRY) + extra:
            tag = "" if r.key in core_set else f"  ({r.tag})"
            cb = QCheckBox(f"{r.name}  [{r.key}]{tag}", host)
            cb.setChecked(r.key in _rx.QUICK_KEYS)
            cb.setToolTip(r.desc)
            self._recipe_checks[r.key] = cb
            hl.addWidget(cb)
        scroll.setWidget(host)
        root.addWidget(scroll)

        bar = QHBoxLayout()
        self.run_btn = QPushButton(i18n.KO.DEV_BENCH_RUN, self)
        self.run_btn.clicked.connect(self._on_run)
        self.stop_btn = QPushButton(i18n.KO.DEV_BENCH_STOP, self)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        bar.addWidget(self.run_btn)
        bar.addWidget(self.stop_btn)
        bar.addStretch(1)
        root.addLayout(bar)

        self.status = QLabel(i18n.KO.DEV_BENCH_CACHE_NOTE, self)
        self.status.setStyleSheet("color: #00D4FF; font-weight: 600;")
        root.addWidget(self.status)

        cols = [i18n.KO.DEV_BENCH_COL_RECIPE, i18n.KO.DEV_BENCH_COL_TOTAL,
                i18n.KO.DEV_BENCH_COL_EMBED, i18n.KO.DEV_BENCH_COL_SCORE,
                i18n.KO.DEV_BENCH_COL_IPS, i18n.KO.DEV_BENCH_COL_PEAK,
                i18n.KO.DEV_BENCH_COL_ACC, i18n.KO.DEV_BENCH_COL_NOTE]
        self.table = QTableWidget(0, len(cols), self)
        self.table.setHorizontalHeaderLabels(cols)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, stretch=1)

    def _with_browse(self, edit: QLineEdit, *, folder: bool = True) -> QWidget:
        host = QWidget(self)
        lay = QHBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(edit, stretch=1)
        btn = QPushButton("…", host)
        btn.setFixedWidth(36)
        btn.clicked.connect(lambda: self._browse(edit, folder))
        lay.addWidget(btn)
        return host

    def _browse(self, edit: QLineEdit, folder: bool = True) -> None:
        if folder:
            path = QFileDialog.getExistingDirectory(self, i18n.KO.DEV_BENCH_REF_LABEL)
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, i18n.KO.DEV_LABEL_PATH_LABEL, "", "JSON (*.json)")
        if path:
            edit.setText(path)

    def _open_label_maker(self) -> None:
        """정답 라벨 만들기 다이얼로그 — 닫힌 뒤 저장 경로를 라벨 필드에 채운다."""
        from .label_maker_dialog import LabelMakerDialog
        dlg = LabelMakerDialog(self, default_ref=self.ref_edit.text().strip(),
                               default_val=self.val_edit.text().strip())
        dlg.showMaximized()
        dlg.exec()
        if dlg.labels_path():
            self.labels_edit.setText(dlg.labels_path())
            self.self_test.setChecked(False)

    def _on_selftest_toggled(self, on: bool) -> None:
        self.val_edit.setEnabled(not on)

    # ------------------------------------------------------------------
    def _apply_preset(self, name: str) -> None:
        """프리셋 버튼 — 체크박스/그룹 토글을 일괄 설정.

        - ``quick``: 빠른(린) 프리셋(QUICK_KEYS)만 체크, 그룹 해제.
        - ``faceoff``: 현행 vs 재채점 생존자(FACEOFF_KEYS)만 체크, 그룹 해제.
        - ``core``: core 레지스트리 전부 체크(추가분 해제), 그룹 해제.
        - ``all``: 모든 개별 체크 + 모든 그룹 토글 on.
        """
        core_set = {r.key for r in _rx.REGISTRY}
        for key, cb in self._recipe_checks.items():
            if name == "quick":
                cb.setChecked(key in _rx.QUICK_KEYS)
            elif name == "faceoff":
                cb.setChecked(key in _rx.FACEOFF_KEYS)
            elif name == "core":
                cb.setChecked(key in core_set)
            else:                                # all
                cb.setChecked(True)
        for gcb in getattr(self, "_group_checks", {}).values():
            gcb.setChecked(name == "all")

    def _selected_keys(self) -> List[str]:
        # 개별 레시피 + 체크된 확장 그룹 전체(중복 제거, 순서 보존).
        keys = [k for k, cb in self._recipe_checks.items() if cb.isChecked()]
        seen = set(keys)
        for gkey, cb in getattr(self, "_group_checks", {}).items():
            if cb.isChecked():
                for r in _rx.group(gkey):
                    if r.key not in seen:
                        seen.add(r.key)
                        keys.append(r.key)
        return keys

    def _on_run(self) -> None:
        ref = self.ref_edit.text().strip()
        self_test = self.self_test.isChecked()
        val = self.val_edit.text().strip()
        if not ref or (not self_test and not val) or not Path(ref).is_dir():
            QMessageBox.warning(self, i18n.KO.DEV_BENCH_TITLE,
                                i18n.KO.DEV_BENCH_NEED_FOLDER)
            return
        self.table.setRowCount(0)
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._worker = _BenchWorker(
            ref_root=ref, val_root=val, self_test=self_test,
            recipe_keys=self._selected_keys(),
            timeout=float(self.timeout_spin.value()),
            max_slots=int(self.maxslots_spin.value()),
            max_images=int(self.maximg_spin.value()),
            labels_path=self.labels_edit.text().strip(),
            all_recipes=self.all_recipes.isChecked(),
            explicit_keys=[k for k, cb in self._recipe_checks.items()
                           if cb.isChecked()],
            parent=self,
        )
        self._worker.signals.progress.connect(self._on_progress)
        self._worker.signals.finished.connect(self._on_finished)
        self._worker.signals.failed.connect(self._on_failed)
        self._worker.start()

    def _on_stop(self) -> None:
        if self._worker is not None:
            self._worker.stop()
        self.stop_btn.setEnabled(False)

    def _on_progress(self, name: str, done: int, total: int) -> None:
        self.status.setText(i18n.KO.DEV_BENCH_RUNNING_FMT.format(
            name=name, done=done, total=total))

    def _on_failed(self, msg: str) -> None:
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        QMessageBox.warning(self, i18n.KO.DEV_BENCH_TITLE, msg)

    def _on_finished(self, suite, ds, run_dir: str) -> None:
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._populate(suite)
        rec = next((r for r in suite.runs if r.key == suite.recommended_key), None)
        rec_name = rec.name if rec else "-"
        txt = i18n.KO.DEV_BENCH_DONE_FMT.format(rec=rec_name, path=run_dir)
        if suite.speedup_vs_production:
            txt += "  ·  " + i18n.KO.DEV_BENCH_SPEEDUP_FMT.format(
                x=suite.speedup_vs_production)
        self.status.setText(txt)

    def _populate(self, suite) -> None:
        runs = sorted(suite.runs, key=lambda r: (not r.ok, r.total_sec or 1e9))
        self.table.setRowCount(len(runs))
        for i, r in enumerate(runs):
            star = " ⭐" if r.key == suite.recommended_key else ""
            if r.recall1 is not None:
                acc = f"{r.recall1 * 100:.1f}%"
            elif r.agree1 is not None:
                acc = f"{r.agree1 * 100:.1f}%"
            else:
                acc = "-"
            peak = f"{r.peak_mb:.0f}" if r.peak_mb else "-"
            vals = [f"{r.name}{star}", f"{r.total_sec:.2f}", f"{r.embed_sec:.2f}",
                    f"{r.score_sec:.2f}", f"{r.img_per_sec:.1f}", peak, acc,
                    r.note or ("폴백" if r.fell_back_classical else "")]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if r.key == suite.recommended_key:
                    item.setForeground(Qt.GlobalColor.green)
                self.table.setItem(i, c, item)
