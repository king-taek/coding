"""GL 히스토그램 뷰어 — 사진 한 장의 밝기(GL) 분포를 ROI 별로 본다.

흐름: 사진 열기(파일·끌어놓기) → ROI(수평선·수직선·사각형·정사각형·원) → 임계값(하한 이하 파랑·
상한 이상 빨강) → 히스토그램·선 프로파일·통계 → 엑셀·PNG 저장.

★ 컬러 결함 사진을 열면 같은 폴더의 Scan 사진에서 결함 영역(300 µm)을 찾아 그 Scan 을 분석
  대상으로 삼고 결함 위치를 확대해 보여 준다(``gl.loader.load_for_view``).
★ 분석은 원본 비트심도 그대로다.  화면용 8 bit 변환은 보기 전용이다.
★ 긴 작업(사진 읽기·Scan 찾기)은 백그라운드 스레드에서 하고 로딩바는 busy 로 띄운다.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from PyQt6.QtCore import QObject, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog,
                             QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel,
                             QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from ... import i18n
from ...config import CONFIG
from ...gl import analysis, loader
from ...gl import roi as R
from ...gl.export import build_workbook, save_workbook
from .. import theme
from ..deferred import call_later
from . import sheet_host as sheets
from .gl_canvas import TOOLS, GlCanvas
from .gl_charts import HistogramChart, ProfileChart
from .loading_overlay import LoadingOverlay
from .neon_button import NeonButton
from .option_group import OptionGroup

_LOG = logging.getLogger("aoi.gl")
_KO = i18n.KO

_TOOL_LABEL = {"move": _KO.GL_TOOL_MOVE, R.HLINE: _KO.GL_TOOL_HLINE, R.VLINE: _KO.GL_TOOL_VLINE,
               R.RECT: _KO.GL_TOOL_RECT, R.SQUARE: _KO.GL_TOOL_SQUARE,
               R.CIRCLE: _KO.GL_TOOL_CIRCLE}
_BITS_CHOICES = (8, 10, 12, 14, 16)
_WARN_TEXT = {loader.W_COLOR: _KO.GL_WARN_COLOR, loader.W_JPEG: _KO.GL_WARN_JPEG,
              loader.W_HUGE: _KO.GL_WARN_HUGE}

# 열려 있는 읽기 스레드 — 창이 먼저 닫혀도 QThread 가 도는 채로 지워지지 않게 붙들어 둔다.
_LIVE_WORKERS: set = set()

# 두 사진 비교 — A(기준)는 청사진 파랑, B(검증)는 주황.  명도 차가 커서 색만으로도 갈린다.
_CMP_A, _CMP_B = "#2C5A86", "#C2410C"


def _shape_txt(img: loader.GlImage) -> str:
    return f"{img.shape[1]}×{img.shape[0]}"


def _signed(v, nd: int) -> str:
    return "—" if v is None else (f"{v:+,.{nd}f}" if nd else f"{int(v):+,}")


def _delta_row(a: dict, b: dict) -> dict:
    """B − A.  어느 쪽이든 비어 있으면 비교할 수 없는 항목은 ``None`` 이다."""
    def d(k):
        return None if a[k] is None or b[k] is None else b[k] - a[k]
    return {"name": f"{a['name']} · B−A", "box": "", "delta": True, "n": b["n"] - a["n"],
            "mean": d("mean"), "std": d("std"), "min": d("min"), "max": d("max"),
            "below": b["below"] - a["below"], "above": b["above"] - a["above"]}


class _Worker(QThread):
    class _Signals(QObject):
        done = pyqtSignal(int, object, str)        # (세대, 결과, 오류 문구)

    def __init__(self, gen: int, fn: Callable[[], object]) -> None:
        super().__init__()
        self._gen, self._fn = gen, fn
        self.signals = self._Signals()
        _LIVE_WORKERS.add(self)
        self.finished.connect(self._forget)

    def _forget(self) -> None:
        _LIVE_WORKERS.discard(self)

    def run(self) -> None:      # type: ignore[override]
        try:
            self.signals.done.emit(self._gen, self._fn(), "")
        except loader.GlLoadError as exc:
            self.signals.done.emit(self._gen, None, str(exc))
        except Exception as exc:                    # 예상 밖의 실패도 화면에 알린다
            _LOG.exception("GL 사진 읽기 실패")
            self.signals.done.emit(self._gen, None, str(exc) or exc.__class__.__name__)


class _RawDialog(QDialog):
    """RAW 사진은 크기를 파일에서 알 수 없어 직접 받는다(파일 크기로 정사각형을 추정해 채운다)."""

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_KO.GL_RAW_TITLE)
        self._size = path.stat().st_size
        form = QFormLayout(self)
        self.bits = QComboBox(self)
        self.bits.addItems(["16", "8"])
        self.width_ = QSpinBox(self)
        self.height_ = QSpinBox(self)
        for sp in (self.width_, self.height_):
            sp.setRange(1, 100_000)
        self.endian = QComboBox(self)
        self.endian.addItems([_KO.GL_RAW_LITTLE, _KO.GL_RAW_BIG])
        self.header = QSpinBox(self)
        self.header.setRange(0, 1 << 30)
        side = loader.guess_raw_side(self._size, 16, 0)
        if side:
            self.width_.setValue(side)
            self.height_.setValue(side)
        form.addRow(_KO.GL_RAW_WIDTH, self.width_)
        form.addRow(_KO.GL_RAW_HEIGHT, self.height_)
        form.addRow(_KO.GL_RAW_BITS, self.bits)
        form.addRow(_KO.GL_RAW_ENDIAN, self.endian)
        form.addRow(_KO.GL_RAW_HEADER, self.header)
        row = QHBoxLayout()
        ok = NeonButton(_KO.GL_RAW_OK, role="primary")
        cancel = NeonButton(_KO.GL_RAW_CANCEL, role="ghost")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(ok)
        form.addRow(row)

    def settings(self) -> dict:
        return {"width": self.width_.value(), "height": self.height_.value(),
                "bits": int(self.bits.currentText()),
                "little_endian": self.endian.currentIndex() == 0,
                "header": self.header.value()}


def _mono_item(text: str, right: bool = True) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFont(QFont(theme.FONT_MONO.split(",")[0].strip('" '), 10))
    it.setTextAlignment((Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft)
                        | Qt.AlignmentFlag.AlignVCenter)
    return it


class GlViewerDialog(QDialog):
    """GL 뷰어 창.  ``image_path`` 를 주면 바로 연다."""

    def __init__(self, parent=None, *, image_path: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(_KO.GL_TITLE)
        self.setAcceptDrops(True)
        self._gl: Optional[loader.GlImage] = None
        self._view: Optional[loader.ViewLoad] = None
        self._bits = 8
        self._lo, self._hi = 0, 1
        self._full_hist: Optional[np.ndarray] = None
        # 두 사진 비교 — B 사진(같은 크기·같은 비트여야 ROI 를 그대로 겹칠 수 있다)
        self._gl_b: Optional[loader.GlImage] = None
        self._view_b: Optional[loader.ViewLoad] = None
        self._full_hist_b: Optional[np.ndarray] = None
        self._syncing_view = False
        self._gen = 0
        self._gen_b = 0
        self._hover_text = _KO.GL_STATUS_OUT
        self._loading = LoadingOverlay(self)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self._refresh)
        self._build()
        if image_path:
            self.open_path(Path(image_path))

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(1280, 820)

    # ------------------------------------------------------------------
    # 화면 구성
    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        root.addLayout(self._build_head())
        root.addLayout(self._build_toolbar())
        self.banner = QLabel("", self)
        self.banner.setWordWrap(True)
        self.banner.setProperty("role", "muted")
        self.banner.setVisible(False)
        root.addWidget(self.banner)
        body = QHBoxLayout()
        body.setSpacing(theme.PROFILE.card_pad)
        body.addLayout(self._build_left(), 3)
        body.addWidget(self._build_right(), 2)
        root.addLayout(body, 1)

    def _build_head(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        self.file_label = QLabel("", self)
        self.file_label.setProperty("role", "muted")
        self.open_btn = NeonButton(_KO.GL_OPEN, role="primary")
        self.open_btn.setMinimumHeight(theme.PROFILE.control_h_lg)
        self.open_btn.clicked.connect(self._on_pick)
        self.xlsx_btn = NeonButton(_KO.GL_SAVE_XLSX, role="ghost")
        self.xlsx_btn.setMinimumHeight(theme.PROFILE.control_h_lg)
        self.xlsx_btn.clicked.connect(self._on_save_xlsx)
        self.png_btn = NeonButton(_KO.GL_SAVE_PNG, role="ghost")
        self.png_btn.setMinimumHeight(theme.PROFILE.control_h_lg)
        self.png_btn.clicked.connect(self._on_save_png)
        self.compare_btn = NeonButton(_KO.GL_COMPARE, role="ghost")
        self.compare_btn.setMinimumHeight(theme.PROFILE.control_h_lg)
        self.compare_btn.clicked.connect(self._on_compare)
        self.png_rois = QCheckBox(_KO.GL_PNG_ROIS, self)
        self.png_rois.setChecked(True)
        self.toast = QLabel("", self)
        self.toast.setProperty("role", "muted")
        bar.addWidget(self.open_btn)
        bar.addWidget(self.file_label, 1)
        bar.addWidget(self.toast)
        bar.addWidget(self.compare_btn)
        bar.addWidget(self.png_rois)
        bar.addWidget(self.png_btn)
        bar.addWidget(self.xlsx_btn)
        return bar

    def _build_toolbar(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(6)
        self._tool_btns: dict[str, NeonButton] = {}
        for i, tool in enumerate(TOOLS, start=1):
            b = NeonButton(f"{_TOOL_LABEL[tool]} {i}", role="ghost")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)        # 방향키는 사진 화면이 받는다
            b.setToolTip(_KO.GL_TOOL_TIP_FMT.format(name=_TOOL_LABEL[tool], key=i))
            b.clicked.connect(lambda _c=False, t=tool: self._set_tool(t))
            self._tool_btns[tool] = b
            row.addWidget(b)
        self._tool_btns["move"].setChecked(True)
        row.addSpacing(10)
        self.show_h = self._toggle(_KO.GL_SHOW_HLINE, self._on_show_lines)
        self.show_v = self._toggle(_KO.GL_SHOW_VLINE, self._on_show_lines)
        row.addWidget(self.show_h)
        row.addWidget(self.show_v)
        row.addSpacing(10)
        lab = QLabel(_KO.GL_SIZE_LABEL, self)
        lab.setProperty("role", "muted")
        self.size_minus = self._plain(_KO.GL_SIZE_MINUS, lambda: self.canvas.grow(-2), _KO.GL_SIZE_TIP)
        self.size_plus = self._plain(_KO.GL_SIZE_PLUS, lambda: self.canvas.grow(2), _KO.GL_SIZE_TIP)
        for b in (self.size_minus, self.size_plus):
            b.setMinimumWidth(40)
        row.addWidget(lab)
        row.addWidget(self.size_minus)
        row.addWidget(self.size_plus)
        row.addSpacing(10)
        row.addWidget(self._plain(_KO.GL_FIT, lambda: self.canvas.fit(), "F"))
        row.addWidget(self._plain(_KO.GL_ONE_TO_ONE, lambda: self.canvas.set_scale(1.0), ""))
        self.undo_btn = self._plain(_KO.GL_UNDO, lambda: self.canvas.undo(), "Ctrl+Z")
        row.addWidget(self.undo_btn)
        row.addWidget(self._plain(_KO.GL_CLEAR, self._clear_rois, ""))
        row.addStretch(1)
        col.addLayout(row)
        hint = QLabel(_KO.GL_TOOLBAR_HINT, self)
        hint.setProperty("role", "muted")
        col.addWidget(hint)
        return col

    def _toggle(self, text: str, slot) -> NeonButton:
        b = NeonButton(text, role="ghost")
        b.setCheckable(True)
        b.setChecked(True)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.toggled.connect(slot)
        return b

    def _plain(self, text: str, slot, tip: str) -> NeonButton:
        b = NeonButton(text, role="ghost")
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if tip:
            b.setToolTip(tip)
        b.clicked.connect(lambda _c=False: slot())
        return b

    def _build_left(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(8)
        self.canvas = GlCanvas(self)
        self.canvas_b = GlCanvas(self)
        self.canvas_b.rois = self.canvas.rois          # 같은 ROI 를 두 사진에 함께 쓴다
        for c in (self.canvas, self.canvas_b):
            c.roisChanged.connect(self._schedule_refresh)
            c.selectionChanged.connect(self._on_selection)
            c.hoverInfo.connect(self._on_hover)
            c.toolRequested.connect(self._sync_tool_buttons)
            c.visibilityChanged.connect(self._sync_show_buttons)
            c.viewStateChanged.connect(self._sync_views)
        self.canvas.viewChanged.connect(self._update_status)
        self.cap_a = QLabel("", self)
        self.cap_b = QLabel("", self)
        for cap in (self.cap_a, self.cap_b):
            cap.setProperty("role", "muted")
        self.split = QSplitter(Qt.Orientation.Horizontal, self)
        self.split.setChildrenCollapsible(False)
        for cap, cv in ((self.cap_a, self.canvas), (self.cap_b, self.canvas_b)):
            pane = QWidget(self.split)
            lay = QVBoxLayout(pane)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(2)
            lay.addWidget(cap)
            lay.addWidget(cv, 1)
            self.split.addWidget(pane)
            cap.setVisible(False)
        self.pane_b = self.split.widget(1)
        self.pane_b.setVisible(False)
        col.addWidget(self.split, 1)
        self.status = QLabel("", self)
        self.status.setProperty("role", "mono")
        self.status.setMinimumHeight(theme.PROFILE.target_min)
        col.addWidget(self.status)
        self.table = QTableWidget(0, 9, self)
        self.table.setHorizontalHeaderLabels([
            _KO.GL_COL_ROI, _KO.GL_COL_BOX, _KO.GL_COL_COUNT, _KO.GL_COL_MEAN, _KO.GL_COL_STD,
            _KO.GL_COL_MIN, _KO.GL_COL_MAX, _KO.GL_COL_BELOW, _KO.GL_COL_ABOVE])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)     # 위치·크기
        self.table.setMinimumHeight(150)
        self.table.setMaximumHeight(210)
        self.table.cellClicked.connect(self._on_table_click)
        col.addWidget(self.table)
        return col

    def _build_right(self) -> QWidget:
        host = QWidget(self)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        # 임계값
        col.addWidget(self._section(_KO.GL_THRESHOLD))
        row = QHBoxLayout()
        self.lo_spin, self.hi_spin = QSpinBox(self), QSpinBox(self)
        for sp, name in ((self.lo_spin, _KO.GL_THR_LOW), (self.hi_spin, _KO.GL_THR_HIGH)):
            sp.setAccessibleName(name)
            sp.setKeyboardTracking(False)
            sp.setRange(0, 255)
        self.lo_spin.setStyleSheet(f"QSpinBox {{ color: {QColor(47, 111, 224).name()}; }}")
        self.hi_spin.setStyleSheet(f"QSpinBox {{ color: {QColor(229, 50, 42).name()}; }}")
        self.lo_spin.valueChanged.connect(lambda v: self._set_thresholds(v, self._hi, "lo"))
        self.hi_spin.valueChanged.connect(lambda v: self._set_thresholds(self._lo, v, "hi"))
        for lab, sp in ((_KO.GL_THR_LOW, self.lo_spin), (_KO.GL_THR_HIGH, self.hi_spin)):
            c = QVBoxLayout()
            l = QLabel(lab, self)
            l.setProperty("role", "muted")
            c.addWidget(l)
            c.addWidget(sp)
            row.addLayout(c, 1)
        col.addLayout(row)
        self.thr_note = QLabel("", self)
        self.thr_note.setProperty("role", "muted")
        col.addWidget(self.thr_note)
        r2 = QHBoxLayout()
        self.auto_btn = NeonButton(_KO.GL_THR_AUTO, role="ghost")
        self.auto_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.auto_btn.clicked.connect(self._auto_thresholds)
        self.bits_combo = QComboBox(self)
        self.bits_combo.setAccessibleName(_KO.GL_BITS)
        self.bits_combo.setMinimumWidth(130)
        self.bits_combo.currentIndexChanged.connect(self._on_bits_changed)
        r2.addWidget(self.auto_btn, 1)
        r2.addWidget(QLabel(_KO.GL_BITS, self))
        r2.addWidget(self.bits_combo)
        col.addLayout(r2)
        self.scope = OptionGroup([(analysis.SCOPE_ALL, _KO.GL_SCOPE_ALL),
                                  (analysis.SCOPE_ROI, _KO.GL_SCOPE_ROI),
                                  (analysis.SCOPE_OFF, _KO.GL_SCOPE_OFF)],
                                 current=analysis.SCOPE_ALL, fixed_cols=3, role="segment")
        self.scope.selection_changed.connect(lambda _k: self._schedule_refresh())
        col.addWidget(self.scope)
        self.auto_contrast = QCheckBox(_KO.GL_AUTO_CONTRAST, self)
        self.auto_contrast.toggled.connect(lambda _c: self._schedule_refresh())
        col.addWidget(self.auto_contrast)
        # 히스토그램
        head = QHBoxLayout()
        head.addWidget(self._section(_KO.GL_HIST))
        self.hist_name = QLabel("", self)
        self.hist_name.setProperty("role", "muted")
        head.addWidget(self.hist_name, 1)
        col.addLayout(head)
        opts = QHBoxLayout()
        self.overlay_cb = QCheckBox(_KO.GL_HIST_OVERLAY, self)
        self.log_cb = QCheckBox(_KO.GL_HIST_LOG, self)
        self.range_cb = QCheckBox(_KO.GL_HIST_DATA_RANGE, self)
        for cb in (self.overlay_cb, self.log_cb, self.range_cb):
            cb.toggled.connect(lambda _c: self._schedule_refresh())
            opts.addWidget(cb)
        opts.addStretch(1)
        col.addLayout(opts)
        self.hist = HistogramChart(self)
        self.hist.thresholdDragged.connect(self._on_hist_drag)
        col.addWidget(self.hist, 3)
        self.hist_info = QLabel("", self)
        self.hist_info.setProperty("role", "mono")
        self.hist.hoverText.connect(self.hist_info.setText)
        col.addWidget(self.hist_info)
        # 프로파일
        col.addWidget(self._section(_KO.GL_PROFILE))
        self.profile = ProfileChart(self)
        self.profile.hoverText.connect(self.hist_info.setText)
        col.addWidget(self.profile, 2)
        self._sync_bits_combo(8)
        return host

    def _section(self, text: str) -> QLabel:
        l = QLabel(text, self)
        l.setProperty("role", "subtitle")
        return l

    # ------------------------------------------------------------------
    # 도구·보기 버튼
    # ------------------------------------------------------------------
    def _set_tool(self, tool: str) -> None:
        self.canvas.set_tool(tool)
        self.canvas_b.set_tool(tool)
        self._sync_tool_buttons(tool)
        self.canvas.setFocus()

    def _sync_tool_buttons(self, tool: str) -> None:
        for t, b in self._tool_btns.items():
            b.setChecked(t == tool)

    def _on_show_lines(self, _checked: bool = False) -> None:
        self.canvas.rois.show_h = self.show_h.isChecked()
        self.canvas.rois.show_v = self.show_v.isChecked()
        self.canvas.update()
        self.canvas_b.update()
        self._schedule_refresh()

    def _sync_show_buttons(self) -> None:
        for b, v in ((self.show_h, self.canvas.rois.show_h), (self.show_v, self.canvas.rois.show_v)):
            b.blockSignals(True)
            b.setChecked(v)
            b.blockSignals(False)

    def _clear_rois(self) -> None:
        if self.canvas.rois.clear():
            self.canvas.roisChanged.emit()
            self.canvas.selectionChanged.emit()
            self.canvas.update()

    # ------------------------------------------------------------------
    # 사진 열기
    # ------------------------------------------------------------------
    def _on_pick(self) -> None:
        patterns = " ".join(f"*{e}" for e in sorted(set(CONFIG.image_extensions) | {".tif", ".tiff", ".png", ".bmp", ".raw", ".bin", ".img"}))
        start = str(Path(self._gl.path).parent) if self._gl and self._gl.path else ""
        path, _ = QFileDialog.getOpenFileName(self, _KO.GL_OPEN, start,
                                              _KO.GL_OPEN_FILTER_FMT.format(patterns=patterns))
        if path:
            self.open_path(Path(path))

    def open_path(self, path: Path, *, raw: Optional[dict] = None) -> None:
        """사진을 연다(백그라운드).  RAW 는 설정을 묻는다."""
        path = Path(path)
        if loader.is_raw_path(path) and raw is None:
            dlg = _RawDialog(path, self)
            if sheets.run(dlg) != QDialog.DialogCode.Accepted:
                return
            raw = dlg.settings()
        if raw is not None:
            fn = lambda: loader.ViewLoad(loader.load_raw(path, **raw), source_path=str(path))  # noqa: E731
        else:
            fn = lambda: loader.load_for_view(path)  # noqa: E731
        self._gen += 1
        w = _Worker(self._gen, fn)
        w.signals.done.connect(self._on_loaded)
        self._loading.show_overlay(_KO.GL_LOADING)
        self._loading.set_progress(0, 0, _KO.GL_LOADING)           # busy — 0 에 멈추지 않는다
        w.start()

    def _on_loaded(self, gen: int, result, err: str) -> None:
        if gen != self._gen:
            return
        self._loading.hide_overlay()
        if err or result is None:
            self._toast(_KO.GL_OPEN_FAILED_FMT.format(why=err), error=True)
            return
        self.show_view(result)

    def show_view(self, v: loader.ViewLoad) -> None:
        """읽은 사진을 화면에 올린다(ROI·되돌리기는 비운다)."""
        if self._gl_b is not None and v.image.shape != self._gl_b.shape:
            self._close_compare()                 # 크기가 달라지면 B 와 ROI 를 겹칠 수 없다
            self._toast(_KO.GL_CMP_CLOSED_FMT.format(a=_shape_txt(v.image)), error=True)
        self._view = v
        self._gl = v.image
        self.canvas.rois.reset()
        self._bits = v.image.bits
        self._sync_bits_combo(self._bits)
        self._full_hist = analysis.histogram(self._gl.data, self._bits)
        if self._gl_b is not None:
            self._full_hist_b = analysis.histogram(self._gl_b.data, self._bits)
        self._set_range_widgets()
        self._lo, self._hi = analysis.auto_thresholds(self._full_hist)
        self._sync_spins()
        rgb = self._make_rgb()
        self.canvas.set_image(self._gl.data, rgb)
        self.canvas.set_thresholds(self._lo, self._hi)
        name = Path(v.source_path or self._gl.path).name
        self.file_label.setText(f"{name} · {self._gl.shape[1]}×{self._gl.shape[0]} · {self._bits} bit")
        notes = []
        if v.scan_name:
            notes.append(_KO.GL_SCAN_FOUND_FMT.format(name=v.scan_name))
        elif v.scan_missing:
            notes.append(_KO.GL_SCAN_MISSING)
        notes += [_WARN_TEXT[w] for w in self._gl.warnings
                  if w in _WARN_TEXT and not (v.scan_name and w == loader.W_COLOR)]
        self.banner.setText("\n".join(notes))
        self.banner.setVisible(bool(notes))
        if v.target_box:
            self.canvas.set_target_box(v.target_box)
            call_later(self, 0, self._zoom_to_target)
        self._refresh()
        self.canvas.setFocus()

    # ------------------------------------------------------------------
    # 두 사진 비교
    # ------------------------------------------------------------------
    def _on_compare(self) -> None:
        if self._gl_b is not None:
            self._close_compare()
            self._schedule_refresh()
            return
        if self._gl is None:
            self._toast(_KO.GL_NO_IMAGE, error=True)
            return
        start = str(Path(self._gl.path).parent) if self._gl.path else ""
        patterns = " ".join(f"*{e}" for e in sorted(set(CONFIG.image_extensions)
                                                    | {".tif", ".tiff", ".png", ".bmp"}))
        path, _ = QFileDialog.getOpenFileName(self, _KO.GL_CMP_PICK, start,
                                              _KO.GL_OPEN_FILTER_FMT.format(patterns=patterns))
        if path:
            self.open_path_b(Path(path))

    def open_path_b(self, path: Path) -> None:
        """B 사진을 연다(백그라운드).  A 와 같은 크기·같은 비트여야 ROI 를 그대로 겹칠 수 있다."""
        path = Path(path)
        self._gen_b += 1
        w = _Worker(self._gen_b, lambda: loader.load_for_view(path))
        w.signals.done.connect(self._on_loaded_b)
        self._loading.show_overlay(_KO.GL_LOADING)
        self._loading.set_progress(0, 0, _KO.GL_LOADING)
        w.start()

    def _on_loaded_b(self, gen: int, result, err: str) -> None:
        if gen != self._gen_b:
            return
        self._loading.hide_overlay()
        if err or result is None:
            self._toast(_KO.GL_OPEN_FAILED_FMT.format(why=err), error=True)
            return
        self.show_compare(result)

    def show_compare(self, v: loader.ViewLoad) -> None:
        if self._gl is None:
            return
        b = v.image
        if b.shape != self._gl.shape or b.bits != self._gl.bits:
            self._toast(_KO.GL_CMP_MISMATCH_FMT.format(
                a=f"{_shape_txt(self._gl)} · {self._gl.bits} bit",
                b=f"{_shape_txt(b)} · {b.bits} bit"), error=True)
            return
        self._view_b, self._gl_b = v, b
        self._full_hist_b = analysis.histogram(b.data, self._bits)
        self.canvas_b.set_image(b.data, self._make_rgb(b), fit=False)
        self.canvas_b.set_target_box(self.canvas._target)
        self.canvas_b.set_view_state(self.canvas.view_state())
        self.pane_b.setVisible(True)
        self.cap_a.setText(f"A · {Path(self._view.source_path or self._gl.path).name}")
        self.cap_b.setText(f"B · {Path(v.source_path or b.path).name}")
        self.cap_a.setVisible(True)
        self.cap_b.setVisible(True)
        self.compare_btn.setText(_KO.GL_COMPARE_CLOSE)
        self.overlay_cb.setEnabled(False)
        self.split.setSizes([1, 1])
        self._refresh()

    def _close_compare(self) -> None:
        self._gl_b = self._view_b = self._full_hist_b = None
        self.pane_b.setVisible(False)
        self.cap_a.setVisible(False)
        self.cap_b.setVisible(False)
        self.compare_btn.setText(_KO.GL_COMPARE)
        self.overlay_cb.setEnabled(True)

    def _sync_views(self) -> None:
        """한쪽을 확대·이동하면 다른 쪽도 같은 자리를 본다(비교는 같은 곳을 보는 일이다)."""
        if self._gl_b is None or self._syncing_view:
            return
        src = self.sender()
        dst = self.canvas_b if src is self.canvas else self.canvas
        self._syncing_view = True
        try:
            dst.set_view_state(src.view_state())
        finally:
            self._syncing_view = False

    def _zoom_to_target(self) -> None:
        # 창 크기가 정해진 뒤에 맞춘다(처음 그려질 때는 캔버스 크기가 아직 작다).
        if self._view and self._view.target_box and self.isVisible():
            self.canvas.zoom_to_box(self._view.target_box)

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        if self._view and self._view.target_box:
            self._zoom_to_target()

    # -- 끌어놓기 --------------------------------------------------------
    def dragEnterEvent(self, e):  # noqa: N802
        if self._dropped(e) is not None:
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        p = self._dropped(e)
        if p is not None:
            e.acceptProposedAction()
            self.open_path(p)

    @staticmethod
    def _dropped(e) -> Optional[Path]:
        if not e.mimeData().hasUrls():
            return None
        for u in e.mimeData().urls():
            p = Path(u.toLocalFile())
            if p.is_file():
                return p
        return None

    # ------------------------------------------------------------------
    # 임계값·비트
    # ------------------------------------------------------------------
    def _levels(self) -> int:
        return analysis.levels(self._bits)

    def _set_range_widgets(self) -> None:
        top = self._levels() - 1
        for sp in (self.lo_spin, self.hi_spin):
            sp.blockSignals(True)
            sp.setRange(0, top)
            sp.blockSignals(False)

    def _sync_spins(self) -> None:
        for sp, v in ((self.lo_spin, self._lo), (self.hi_spin, self._hi)):
            sp.blockSignals(True)
            sp.setValue(v)
            sp.blockSignals(False)
        self.canvas.set_thresholds(self._lo, self._hi)
        self.hist.set_thresholds(self._lo, self._hi)
        self.profile.set_thresholds(self._lo, self._hi)

    def _set_thresholds(self, lo: int, hi: int, moved: str) -> None:
        """하한 < 상한을 지킨다 — 움직인 쪽이 반대편을 넘으면 그 자리에서 멈추고 알린다."""
        top = self._levels() - 1
        note = ""
        if moved == "lo" and lo >= hi:
            lo = max(0, hi - 1)
            note = _KO.GL_THR_ORDER_NOTE
        elif moved == "hi" and hi <= lo:
            hi = min(top, lo + 1)
            note = _KO.GL_THR_ORDER_NOTE
        self._lo, self._hi = analysis.clamp_thresholds(lo, hi, self._bits)
        self.thr_note.setText(note)
        self._sync_spins()
        self._schedule_refresh()

    def _on_hist_drag(self, which: str, gl: int) -> None:
        if self._gl is None:
            return
        if which == "lo":
            self._set_thresholds(gl, self._hi, "lo")
        else:
            self._set_thresholds(self._lo, gl, "hi")

    def _auto_thresholds(self) -> None:
        if self._full_hist is None:
            return
        self._lo, self._hi = analysis.auto_thresholds(self._full_hist)
        self.thr_note.setText("")
        self._sync_spins()
        self._schedule_refresh()

    def _sync_bits_combo(self, auto_bits: int) -> None:
        self.bits_combo.blockSignals(True)
        self.bits_combo.clear()
        if auto_bits == 8:
            choices = (8,)
        else:
            choices = tuple(b for b in _BITS_CHOICES if b >= 10)
        for b in choices:
            self.bits_combo.addItem(
                (_KO.GL_BITS_AUTO_FMT if b == auto_bits else _KO.GL_BITS_FMT).format(bits=b), b)
        self.bits_combo.setCurrentIndex(max(0, choices.index(auto_bits)) if auto_bits in choices else 0)
        self.bits_combo.setEnabled(len(choices) > 1)
        self.bits_combo.blockSignals(False)

    def _on_bits_changed(self, _i: int) -> None:
        """16 bit 사진의 GL 범위(10/12/14/16)를 바꾼다 — 분석 범위·판정 기준이 함께 바뀐다."""
        b = self.bits_combo.currentData()
        if self._gl is None or b is None or int(b) == self._bits:
            return
        self._bits = int(b)
        self._full_hist = analysis.histogram(self._gl.data, self._bits)
        self._set_range_widgets()
        self._lo, self._hi = analysis.auto_thresholds(self._full_hist)
        self._sync_spins()
        self._schedule_refresh()

    # ------------------------------------------------------------------
    # 갱신
    # ------------------------------------------------------------------
    def _schedule_refresh(self) -> None:
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _visible_rois(self) -> list[R.Roi]:
        rs = self.canvas.rois
        return [r for r in rs.rois if rs.visible(r)]

    def _make_rgb(self, gl: Optional[loader.GlImage] = None) -> np.ndarray:
        data = (gl or self._gl).data
        rng = analysis.display_range(data, self._bits, self.auto_contrast.isChecked())
        return analysis.overlay_rgb(data, rng, self._lo, self._hi,
                                    self.scope.current_key(), self._visible_rois())

    def _refresh(self) -> None:
        if self._gl is None:
            return
        self.canvas.set_display(self._make_rgb())
        self.canvas.set_thresholds(self._lo, self._hi)
        if self._gl_b is not None:
            self.canvas_b.set_display(self._make_rgb(self._gl_b))
            self.canvas_b.set_thresholds(self._lo, self._hi)
        self._refresh_charts()
        self._refresh_table()
        self.undo_btn.setEnabled(self.canvas.rois.can_undo())
        self._update_status()

    def _refresh_charts(self) -> None:
        data = self._gl.data
        levels = self._levels()
        rois = self._visible_rois()
        active = self.canvas.rois.active()
        series, name = [], _KO.GL_HIST_ALL
        if self._gl_b is not None:
            # 비교: 선택한 ROI(없으면 사진 전체)의 A·B 분포를 % 로 겹친다.
            if active is not None and self.canvas.rois.visible(active):
                ha = analysis.histogram(analysis.roi_values(data, active), self._bits)
                hb = analysis.histogram(analysis.roi_values(self._gl_b.data, active), self._bits)
                name = active.name
            else:
                ha, hb = self._full_hist, self._full_hist_b
            series = [("A", _CMP_A, ha), ("B", _CMP_B, hb)]
        elif self.overlay_cb.isChecked() and len(rois) >= 2:
            series = [(r.name, r.color, analysis.histogram(analysis.roi_values(data, r), self._bits))
                      for r in rois]
            name = " · ".join(r.name for r in rois)
        elif active is not None and self.canvas.rois.visible(active):
            series = [(active.name, theme.MUTE or "#5A574E",
                       analysis.histogram(analysis.roi_values(data, active), self._bits))]
            name = active.name
        else:
            series = [(_KO.GL_HIST_ALL, theme.MUTE or "#5A574E", self._full_hist)]
        self.hist.set_log(self.log_cb.isChecked())
        self.hist.set_range_data(self.range_cb.isChecked())
        self.hist.set_series(series, levels, normalize=len(series) > 1)
        self.hist.set_thresholds(self._lo, self._hi)
        self.hist_name.setText(name)
        lines = []
        for r in (x for x in rois if x.is_line):
            if self._gl_b is None:
                lines.append((r.name, r.color, analysis.profile(data, r)))
            else:
                lines.append((f"{r.name} A", _CMP_A, analysis.profile(data, r)))
                lines.append((f"{r.name} B", _CMP_B, analysis.profile(self._gl_b.data, r)))
        self.profile.set_lines(lines, levels)
        self.profile.set_thresholds(self._lo, self._hi)

    def _stat_row(self, r: Optional[R.Roi], gl: Optional[loader.GlImage] = None) -> dict:
        data = (gl or self._gl).data
        if r is None:
            vals, name, box = data.reshape(-1), _KO.GL_ROW_ALL, ""
        else:
            vals = analysis.roi_values(data, r)
            name = r.name
            if r.kind == R.HLINE:
                box = _KO.GL_ROW_LINE_FMT.format(axis=_KO.GL_AXIS_ROW, pos=r.pos)
            elif r.kind == R.VLINE:
                box = _KO.GL_ROW_LINE_FMT.format(axis=_KO.GL_AXIS_COL, pos=r.pos)
            else:
                box = f"({r.x}, {r.y}) {r.w}×{r.h}"
        st = analysis.stats(vals, self._lo, self._hi)
        st["name"], st["box"] = name, box
        return st

    def _stat_rows(self) -> list[dict]:
        rois = [None] + self.canvas.rois.rois
        if self._gl_b is None:
            return [self._stat_row(r) for r in rois]
        out = []
        for r in rois:                       # 비교: ROI 마다 A · B · B−A 세 줄
            a, b = self._stat_row(r), self._stat_row(r, self._gl_b)
            out += [dict(a, name=f"{a['name']} · A"), dict(b, name=f"{b['name']} · B"),
                    _delta_row(a, b)]
        return out

    def _refresh_table(self) -> None:
        rows = self._stat_rows()
        rois = [None] + self.canvas.rois.rois
        if self._gl_b is not None:
            rois = [r for r in rois for _ in range(3)]
        self.table.setRowCount(len(rows))
        sel = self.canvas.rois.selected
        for i, (st, r) in enumerate(zip(rows, rois)):
            if st.get("delta"):
                cells = [st["name"], "", f"{st['n']:+,}",
                         _signed(st["mean"], 1), _signed(st["std"], 1),
                         _signed(st["min"], 0), _signed(st["max"], 0),
                         _signed(st["below"], 0), _signed(st["above"], 0)]
                for c, text in enumerate(cells):
                    it = _mono_item(text, right=c >= 2)
                    it.setForeground(QColor(theme.MUTE or "#5A574E"))
                    if c == 0 and r is not None:
                        it.setData(Qt.ItemDataRole.UserRole, r.id)
                    self.table.setItem(i, c, it)
                continue

            def pct(c):
                return f"{c:,} ({c / st['n'] * 100:.1f}%)" if st["n"] else "—"
            cells = [st["name"], st["box"], f"{st['n']:,}",
                     "—" if st["mean"] is None else f"{st['mean']:.1f}",
                     "—" if st["std"] is None else f"{st['std']:.1f}",
                     "—" if st["min"] is None else f"{st['min']:,}",
                     "—" if st["max"] is None else f"{st['max']:,}",
                     pct(st["below"]), pct(st["above"])]
            for c, text in enumerate(cells):
                it = _mono_item(text, right=c >= 2)
                if c == 0 and r is not None:
                    it.setForeground(QColor(r.color if self._dark_ok(r.color) else theme.INK))
                    it.setData(Qt.ItemDataRole.UserRole, r.id)
                self.table.setItem(i, c, it)
            if r is not None and r.id == sel:
                self.table.selectRow(i)
        if sel is None:
            self.table.clearSelection()

    @staticmethod
    def _dark_ok(color: str) -> bool:
        """밝은 ROI 색(흰색·연두)은 밝은 표 바탕에서 안 보인다 — 그런 색은 잉크로 바꾼다."""
        c = QColor(color)
        return (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) < 150

    def _on_table_click(self, row: int, _col: int) -> None:
        it = self.table.item(row, 0)
        rid = it.data(Qt.ItemDataRole.UserRole) if it else None
        self.canvas.rois.selected = rid
        self.canvas.update()
        self._on_selection()

    def _on_selection(self) -> None:
        self._schedule_refresh()

    def _on_hover(self, x: int, y: int, gl: int) -> None:
        self._hover_text = (_KO.GL_STATUS_FMT.format(x=x, y=y, gl=gl) if x >= 0
                            else _KO.GL_STATUS_OUT)
        self._update_status()

    def _update_status(self, *_a) -> None:
        if self._gl is None:
            self.status.setText("")
            return
        h, w = self._gl.shape
        view = _KO.GL_STATUS_VIEW_FMT.format(w=w, h=h, bits=self._bits,
                                             zoom=int(round(self.canvas.scale() * 100)))
        self.status.setText(f"{self._hover_text}    {view}")

    def _toast(self, text: str, *, error: bool = False) -> None:
        self.toast.setText(text)
        call_later(self, 6000 if error else 4000, lambda: self.toast.setText(""))

    # ------------------------------------------------------------------
    # 저장
    # ------------------------------------------------------------------
    def _default_name(self, suffix: str) -> str:
        base = Path(self._view.source_path if self._view and self._view.source_path
                    else (self._gl.path if self._gl else "gl")).stem
        folder = Path(self._gl.path).parent if self._gl and self._gl.path else Path.home()
        return str(folder / f"{base}_GL{suffix}")

    def _on_save_xlsx(self) -> None:
        if self._gl is None:
            self._toast(_KO.GL_NO_IMAGE, error=True)
            return
        path, _ = QFileDialog.getSaveFileName(self, _KO.GL_SAVE_XLSX_TITLE,
                                              self._default_name(".xlsx"), "Excel (*.xlsx)")
        if not path:
            return
        try:
            self.save_xlsx(Path(path))
        except Exception as exc:
            _LOG.exception("GL 엑셀 저장 실패")
            self._toast(_KO.GL_SAVE_FAILED_FMT.format(why=exc), error=True)
            return
        self._toast(_KO.GL_SAVED_FMT.format(name=Path(path).name))

    def save_xlsx(self, path: Path) -> None:
        """통계·히스토그램·선 프로파일을 엑셀 한 장으로 저장한다(헤드리스 호출 가능)."""
        data = self._gl.data
        rois = self.canvas.rois.rois
        info = [(_KO.GL_INFO_PHOTO, self._view.source_path if self._view else self._gl.path),
                (_KO.GL_INFO_ANALYZED, self._gl.path),
                (_KO.GL_INFO_SIZE, f"{data.shape[1]}×{data.shape[0]}"),
                (_KO.GL_INFO_BITS, f"{self._bits} bit (0~{self._levels() - 1})"),
                (_KO.GL_INFO_LOW, f"≤ {self._lo}"), (_KO.GL_INFO_HIGH, f"≥ {self._hi}")]
        if self._view and self._view.scan_name:
            info.append((_KO.GL_INFO_SCAN, self._view.scan_name))
        if self._gl_b is None:
            hist_cols = [(_KO.GL_HIST_ALL, self._full_hist)] + [
                (r.name, analysis.histogram(analysis.roi_values(data, r), self._bits))
                for r in rois]
            profiles = [(r.name, analysis.profile(data, r)) for r in rois if r.is_line]
        else:
            info.insert(1, (_KO.GL_INFO_B, self._view_b.source_path if self._view_b else self._gl_b.path))
            db = self._gl_b.data
            hist_cols = [(f"{_KO.GL_HIST_ALL} A", self._full_hist),
                         (f"{_KO.GL_HIST_ALL} B", self._full_hist_b)]
            for r in rois:
                hist_cols += [(f"{r.name} A", analysis.histogram(analysis.roi_values(data, r), self._bits)),
                              (f"{r.name} B", analysis.histogram(analysis.roi_values(db, r), self._bits))]
            profiles = []
            for r in (x for x in rois if x.is_line):
                profiles += [(f"{r.name} A", analysis.profile(data, r)),
                             (f"{r.name} B", analysis.profile(db, r))]
        titles = {"info": _KO.GL_XLSX_SHEET_INFO, "stats": _KO.GL_XLSX_SHEET_STATS,
                  "hist": _KO.GL_XLSX_SHEET_HIST, "profile": _KO.GL_XLSX_SHEET_PROFILE,
                  "gl": _KO.GL_XLSX_GL, "pos": _KO.GL_XLSX_POS,
                  "stats_cols": [_KO.GL_COL_ROI, _KO.GL_COL_BOX, _KO.GL_COL_COUNT, _KO.GL_COL_MEAN,
                                 _KO.GL_COL_STD, _KO.GL_COL_MIN, _KO.GL_COL_MAX,
                                 _KO.GL_COL_BELOW, _KO.GL_COL_BELOW_PCT, _KO.GL_COL_ABOVE,
                                 _KO.GL_COL_ABOVE_PCT]}
        wb = build_workbook(info, self._stat_rows(), hist_cols, profiles, titles)
        save_workbook(wb, path)

    def _on_save_png(self) -> None:
        if self._gl is None:
            self._toast(_KO.GL_NO_IMAGE, error=True)
            return
        path, _ = QFileDialog.getSaveFileName(self, _KO.GL_SAVE_PNG_TITLE,
                                              self._default_name(".png"), "PNG (*.png)")
        if not path:
            return
        try:
            self.save_png(Path(path), with_rois=self.png_rois.isChecked())
        except Exception as exc:
            _LOG.exception("GL PNG 저장 실패")
            self._toast(_KO.GL_SAVE_FAILED_FMT.format(why=exc), error=True)
            return
        self._toast(_KO.GL_SAVED_FMT.format(name=Path(path).name))

    def render_overlay(self, with_rois: bool, gl: Optional[loader.GlImage] = None) -> QImage:
        """색 칠한 사진을 **원본 해상도**로 그린다(선·이름은 선택)."""
        rgb = np.ascontiguousarray(self._make_rgb(gl))
        h, w = rgb.shape[:2]
        img = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        if with_rois:
            p = QPainter(img)
            lw = max(1, round(min(w, h) / 500))
            font = QFont(theme.FONT_MONO.split(",")[0].strip('" '), max(8, lw * 6))
            p.setFont(font)
            for r in self._visible_rois():
                pen = QPen(QColor(r.color), lw)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                if r.kind == R.HLINE:
                    p.drawLine(0, r.pos, w, r.pos)
                    p.drawText(6, max(r.pos - 4, lw * 8), r.name)
                elif r.kind == R.VLINE:
                    p.drawLine(r.pos, 0, r.pos, h)
                    p.drawText(r.pos + 4, lw * 8, r.name)
                else:
                    if r.kind == R.CIRCLE:
                        p.drawEllipse(r.x, r.y, r.w, r.h)
                    else:
                        p.drawRect(r.x, r.y, r.w, r.h)
                    p.drawText(r.x, max(r.y - 4, lw * 8), r.name)
            p.end()
        return img

    def save_png(self, path: Path, *, with_rois: bool = True) -> None:
        """색 칠한 PNG.  비교 중이면 B 도 같은 이름 뒤에 ``_B`` 를 붙여 함께 저장한다."""
        self._write_png(path, self.render_overlay(with_rois))
        if self._gl_b is not None:
            self._write_png(path.with_name(f"{path.stem}_B{path.suffix}"),
                            self.render_overlay(with_rois, self._gl_b))

    @staticmethod
    def _write_png(path: Path, image: QImage) -> None:
        tmp = path.with_name(path.name + ".part")
        try:
            if not image.save(str(tmp), "PNG"):
                raise OSError(_KO.GL_PNG_WRITE_FAILED)
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
