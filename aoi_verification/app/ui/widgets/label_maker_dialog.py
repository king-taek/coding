"""정답 라벨 만들기 다이얼로그 — 기준 사진별 정답 검증 사진을 클릭으로 지정.

개발자 모드 전용.  헤드리스 코어(``app.dev.labels.LabelMakerModel``)를 그대로
소비한다.  정답은 **여러 개**일 수도, **없을** 수도 있다(‘정답 없음’ 버튼).
후보는 파일명순(기본) 또는 고전 유사도순으로 표시하며, 표시 순서는 정답 판정과
무관하다.  저장한 라벨 JSON 은 개발자 벤치마크의 실제 정확도 측정에 쓰인다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog,
                              QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                              QMessageBox, QPushButton, QScrollArea,
                              QToolButton, QVBoxLayout, QWidget)

from ... import i18n
from ...utils import image_io as _io

_REF_PX = 360
_CAND_PX = 150


class LabelMakerDialog(QDialog):
    def __init__(self, parent=None, *, default_ref: str = "",
                 default_val: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.DEV_LABEL_TITLE)
        self._model = None
        self._labels_path: str = ""
        self._cand_buttons: list = []
        self._build(default_ref, default_val)
        # 두 폴더가 이미 유효하면 바로 데이터셋을 만든다.
        if default_ref and default_val and Path(default_ref).is_dir() \
                and Path(default_val).is_dir():
            self._start()

    # ------------------------------------------------------------------
    def _build(self, default_ref: str, default_val: str) -> None:
        root = QVBoxLayout(self)

        hint = QLabel(i18n.KO.DEV_LABEL_HINT, self)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #7FB3D5;")
        root.addWidget(hint)

        # 폴더 입력 + 시작 -------------------------------------------------
        top = QHBoxLayout()
        top.addWidget(QLabel(i18n.KO.DEV_BENCH_REF_LABEL, self))
        self.ref_edit = QLineEdit(default_ref, self)
        top.addWidget(self.ref_edit, stretch=1)
        rb = QPushButton("…", self); rb.setFixedWidth(34)
        rb.clicked.connect(lambda: self._browse(self.ref_edit))
        top.addWidget(rb)
        top.addWidget(QLabel(i18n.KO.DEV_BENCH_VAL_LABEL, self))
        self.val_edit = QLineEdit(default_val, self)
        top.addWidget(self.val_edit, stretch=1)
        vb = QPushButton("…", self); vb.setFixedWidth(34)
        vb.clicked.connect(lambda: self._browse(self.val_edit))
        top.addWidget(vb)
        self.start_btn = QPushButton(i18n.KO.DEV_BENCH_RUN, self)
        self.start_btn.clicked.connect(self._start)
        top.addWidget(self.start_btn)
        root.addLayout(top)

        # 본문: 좌(기준) / 우(후보 그리드) ---------------------------------
        body = QHBoxLayout()
        left = QVBoxLayout()
        self.progress_lbl = QLabel("", self)
        self.progress_lbl.setStyleSheet("color: #00D4FF; font-weight: 700;")
        self.progress_lbl.setWordWrap(True)
        left.addWidget(self.progress_lbl)
        self.ref_img = QLabel(self)
        self.ref_img.setFixedSize(_REF_PX, _REF_PX)
        self.ref_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ref_img.setStyleSheet("background:#0E1424; border:1px solid #1F2A3F;")
        left.addWidget(self.ref_img)
        self.sel_lbl = QLabel("", self)
        self.sel_lbl.setStyleSheet("color: #00FFA3; font-weight: 600;")
        left.addWidget(self.sel_lbl)
        left.addStretch(1)
        body.addLayout(left)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.scroll.setWidget(self.grid_host)
        body.addWidget(self.scroll, stretch=1)
        root.addLayout(body, stretch=1)

        # 하단 컨트롤 ------------------------------------------------------
        bar = QHBoxLayout()
        self.none_btn = QPushButton(i18n.KO.DEV_LABEL_NONE_BTN, self)
        self.none_btn.clicked.connect(self._on_none)
        bar.addWidget(self.none_btn)
        self.sim_sort = QCheckBox(i18n.KO.DEV_LABEL_SORT_SIM, self)
        self.sim_sort.toggled.connect(self._on_sort_toggled)
        bar.addWidget(self.sim_sort)
        bar.addStretch(1)
        self.prev_btn = QPushButton(i18n.KO.DEV_LABEL_PREV, self)
        self.prev_btn.clicked.connect(self._on_prev)
        self.next_btn = QPushButton(i18n.KO.DEV_LABEL_NEXT, self)
        self.next_btn.clicked.connect(self._on_next)
        bar.addWidget(self.prev_btn)
        bar.addWidget(self.next_btn)
        bar.addStretch(1)
        self.load_btn = QPushButton(i18n.KO.DEV_LABEL_LOAD, self)
        self.load_btn.clicked.connect(self._on_load)
        self.save_btn = QPushButton(i18n.KO.DEV_LABEL_SAVE, self)
        self.save_btn.clicked.connect(self._on_save)
        self.close_btn = QPushButton(i18n.KO.DEV_LABEL_CLOSE, self)
        self.close_btn.clicked.connect(self.close)
        for b in (self.load_btn, self.save_btn, self.close_btn):
            bar.addWidget(b)
        root.addLayout(bar)
        self._set_labeling_enabled(False)

    # ------------------------------------------------------------------
    def _browse(self, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, i18n.KO.DEV_BENCH_REF_LABEL)
        if path:
            edit.setText(path)

    def _set_labeling_enabled(self, on: bool) -> None:
        for b in (self.none_btn, self.prev_btn, self.next_btn,
                  self.save_btn, self.sim_sort):
            b.setEnabled(on)

    def _start(self) -> None:
        ref = self.ref_edit.text().strip()
        val = self.val_edit.text().strip()
        if not ref or not val or not Path(ref).is_dir() or not Path(val).is_dir():
            QMessageBox.warning(self, i18n.KO.DEV_LABEL_TITLE,
                                i18n.KO.DEV_LABEL_NEED_FOLDERS)
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            from ...dev import benchmark as _bm
            from ...dev import labels as _lab
            ds = _bm.build_dataset(ref, val)
            if not ds.tasks:
                QApplication.restoreOverrideCursor()
                QMessageBox.warning(self, i18n.KO.DEV_LABEL_TITLE,
                                    i18n.KO.DEV_LABEL_NO_COMMON)
                return
            self._model = _lab.LabelMakerModel(ds.tasks)
        finally:
            QApplication.restoreOverrideCursor()
        self._set_labeling_enabled(True)
        self._refresh()

    # ------------------------------------------------------------------
    def _clear_grid(self) -> None:
        for b in self._cand_buttons:
            b.setParent(None)
            b.deleteLater()
        self._cand_buttons = []

    def _rebuild_candidates(self) -> None:
        self._clear_grid()
        if self._model is None:
            return
        vals = self._model.current_vals()
        cols = 5
        for i, vi in enumerate(vals):
            btn = QToolButton(self.grid_host)
            btn.setCheckable(True)
            btn.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.setIconSize(QSize(_CAND_PX, _CAND_PX))
            try:
                pix = _io.load_thumb_qpixmap(vi.path, _CAND_PX)
                btn.setIcon(QIcon(pix))
            except Exception:
                pass
            btn.setText(Path(vi.path).name[:24])
            btn.setChecked(self._model.is_selected(str(vi.path)))
            btn.setToolTip(str(vi.path))
            btn.setStyleSheet(
                "QToolButton{border:2px solid #1F2A3F; padding:4px; color:#7FB3D5;}"
                "QToolButton:checked{border:2px solid #00FFA3; color:#00FFA3;}")
            vp = str(vi.path)
            btn.clicked.connect(lambda _c=False, p=vp: self._on_toggle(p))
            self.grid.addWidget(btn, i // cols, i % cols)
            self._cand_buttons.append(btn)

    def _refresh(self) -> None:
        if self._model is None:
            return
        cur = self._model.current()
        if cur is None:
            return
        slot, ref = cur
        self.progress_lbl.setText(i18n.KO.DEV_LABEL_PROGRESS_FMT.format(
            idx=self._model.index() + 1, total=self._model.count(),
            slot=slot, name=Path(ref.path).name))
        try:
            self.ref_img.setPixmap(_io.load_thumb_qpixmap(ref.path, _REF_PX))
        except Exception:
            pass
        self._rebuild_candidates()
        self._update_sel_label()

    def _update_sel_label(self) -> None:
        if self._model is None:
            return
        n = len(self._model.selected())
        state = "" if self._model.is_reviewed() else f"  ·  {i18n.KO.DEV_LABEL_UNREVIEWED}"
        self.sel_lbl.setText(i18n.KO.DEV_LABEL_SELECTED_FMT.format(n=n) + state)

    # -- 핸들러 ---------------------------------------------------------
    def _on_toggle(self, val_path: str) -> None:
        if self._model is not None:
            self._model.toggle(val_path)
            self._update_sel_label()

    def _on_none(self) -> None:
        if self._model is None:
            return
        self._model.set_none()
        for b in self._cand_buttons:
            b.setChecked(False)
        self._update_sel_label()

    def _on_prev(self) -> None:
        if self._model is not None:
            self._model.prev()
            self._refresh()

    def _on_next(self) -> None:
        if self._model is not None:
            self._model.next()
            self._refresh()

    def _on_sort_toggled(self, on: bool) -> None:
        if self._model is None:
            return
        self._model.set_ordering("sim" if on else "name")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._rebuild_candidates()
        finally:
            QApplication.restoreOverrideCursor()

    def _on_load(self) -> None:
        from ...dev import labels as _lab
        path, _ = QFileDialog.getOpenFileName(
            self, i18n.KO.DEV_LABEL_LOAD, "", "JSON (*.json)")
        if not path or self._model is None:
            return
        self._model.load_labels(_lab.load(path))
        self._labels_path = path
        self._refresh()

    def _on_save(self) -> None:
        from ...dev import labels as _lab
        if self._model is None:
            return
        path = self._labels_path
        if not path:
            path, _ = QFileDialog.getSaveFileName(
                self, i18n.KO.DEV_LABEL_SAVE, "labels.json", "JSON (*.json)")
            if not path:
                return
            self._labels_path = path
        saved = _lab.save(path, self._model.to_labels())
        self._model.dirty = False
        st = self._model.stats()
        QMessageBox.information(
            self, i18n.KO.DEV_LABEL_TITLE,
            i18n.KO.DEV_LABEL_SAVED_FMT.format(
                path=str(saved), labeled=st["labeled"], none=st["none"],
                multi=st["multi"]))

    def labels_path(self) -> str:
        """저장된 라벨 파일 경로(없으면 빈 문자열) — 호출자가 벤치마크에 넘길 수 있음."""
        return self._labels_path

    # ------------------------------------------------------------------
    def closeEvent(self, event):        # noqa: N802
        if self._model is not None and getattr(self._model, "dirty", False):
            r = QMessageBox.question(
                self, i18n.KO.DEV_LABEL_TITLE, i18n.KO.DEV_LABEL_DISCARD_CONFIRM,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        event.accept()
