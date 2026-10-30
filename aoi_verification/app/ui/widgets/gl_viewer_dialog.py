"""GL 히스토그램 뷰어 — 사진 한 장의 밝기(GL) 분포를 ROI 별로 본다.

흐름: 사진 열기(파일·끌어놓기) → ROI(수평선·수직선·사각형·정사각형·원) → 임계값(하한 이하 파랑·
상한 이상 빨강) → 히스토그램·선 프로파일·통계 → 엑셀·PNG 저장.

★ 컬러 결함 사진을 열면 같은 폴더의 Scan 사진에서 결함 영역(300 µm)을 찾아 그 Scan 을 분석
  대상으로 삼고 결함 위치를 확대해 보여 준다(``gl.loader.load_for_view``).
★ 분석은 원본 비트심도 그대로다.  화면용 8 bit 변환은 보기 전용이다.
★ 긴 작업(사진 읽기·Scan 찾기)은 백그라운드 스레드에서 하고 로딩바는 busy 로 띄운다.

화면 구성(2026-10-30 개편 — "버튼이 많고 복잡하고 공간 효율이 나쁘다"):
- 본체는 :class:`GlViewerPanel`(QWidget) 이고 :class:`GlViewerDialog` 는 그것을 담는 얇은 껍데기다.
  팝업 대신 페이지(메뉴 탭 이동)로 올리는 작업은 별도 브랜치에서 하며, 그쪽이 다이얼로그든 패널이든
  그대로 올릴 수 있게 생성자 · 창 제목을 유지한다.
- 위 줄은 열기 · 파일 이름 · 비교 · 저장(메뉴) 뿐이다.  도구 6개와 맞춤 · 1:1 · 되돌리기 · 지우기는
  사진 왼쪽의 **아이콘 레일**(:class:`_RailTile`)로 내려가 사진이 그만큼 넓어진다.
- 선택한 도형의 크기 ± 는 **선택했을 때만** 상태 줄에 나타나고, 선 프로파일은 **선을 그었을 때만**
  나타난다(빈 칸이 자리를 차지하지 않는다).  8 bit 사진에는 GL 범위 선택이 아예 없다.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from PyQt6.QtCore import QObject, QPoint, QRect, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QImage, QPainter, QPen
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFileDialog, QFormLayout,
                             QFrame, QHBoxLayout, QHeaderView, QLabel, QMenu, QSpinBox,
                             QSizePolicy, QSplitter, QStyle, QStyleOptionButton, QStylePainter,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ... import i18n
from ...config import CONFIG
from ...gl import analysis, loader
from ...gl import roi as R
from ...gl.export import build_workbook, save_workbook
from .. import theme
from ..deferred import call_later
from ..rail_icons import rail_icon
from . import sheet_host as sheets
from .gl_canvas import TOOLS, GlCanvas
from .gl_charts import HistogramChart, ProfileChart
from .loading_overlay import LoadingOverlay
from .neon_button import NeonButton
from .option_group import OptionGroup
from .switch_row import SwitchRow

_LOG = logging.getLogger("aoi.gl")
_KO = i18n.KO

_TOOL_LABEL = {"move": _KO.GL_TOOL_MOVE, R.HLINE: _KO.GL_TOOL_HLINE, R.VLINE: _KO.GL_TOOL_VLINE,
               R.RECT: _KO.GL_TOOL_RECT, R.SQUARE: _KO.GL_TOOL_SQUARE,
               R.CIRCLE: _KO.GL_TOOL_CIRCLE}
_TOOL_ICON = {"move": "move", R.HLINE: "hline", R.VLINE: "vline", R.RECT: "rect",
              R.SQUARE: "square", R.CIRCLE: "circle"}
_RIGHT_W = 352                  # 오른쪽 패널 폭 — 나머지는 전부 사진이 가진다
_TABLE_H = 190                  # ROI 통계 표 — 머리줄 + 5줄, 그 이상은 스크롤
_TABLE_H_CMP = 280              # 비교 때는 ROI 마다 A · B · B−A 세 줄이라 더 준다
_ICON = 18
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


class _RailTile(NeonButton):
    """레일의 정사각 타일 — 아이콘 위, 짧은 이름 아래.  QSS ``segment`` 상자 위에 직접 그린다.

    글자만 있는 버튼 15개가 두 줄을 차지하던 것을 48px 타일 한 열로 줄이면서도, 아이콘만
    남기면 뜻을 못 읽으니 이름을 작게 같이 적고 단축키는 구석에 둔다."""

    def __init__(self, icon: str, caption: str, tip: str, parent=None, *, key: str = "") -> None:
        super().__init__("", role="glTile", parent=parent)      # 크기는 QSS 가 48px 로 고정한다
        self._icon_name, self._caption, self._key = icon, caption, key
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)         # 방향키·숫자키는 사진 화면이 받는다
        self.setToolTip(tip)
        self.setAccessibleName(caption)

    def paintEvent(self, e):  # noqa: N802
        p = QStylePainter(self)
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        p.drawControl(QStyle.ControlElement.CE_PushButtonBevel, opt)
        on = self.isChecked()
        ink = (theme.ACCENT if on else theme.INK) if self.isEnabled() else theme.MUTE
        f = self.font()
        f.setBold(on)
        p.setPen(QColor(ink))
        if self._icon_name:
            pm = rail_icon(self._icon_name, ink).pixmap(QSize(_ICON, _ICON))
            p.drawPixmap(QPoint((self.width() - _ICON) // 2, 7), pm)
            f.setPixelSize(11)
            p.setFont(f)
            p.drawText(QRect(0, 7 + _ICON + 2, self.width(), 16),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self._caption)
        else:                                   # 아이콘 없는 타일(1:1)은 이름이 곧 아이콘
            f.setPixelSize(14)
            f.setBold(True)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._caption)
        if self._key:                           # 단축키 번호 — 오른쪽 위 구석에 작게(툴팁 없이도 보이게)
            f.setPixelSize(9)
            f.setBold(False)
            p.setFont(f)
            p.setPen(QColor(theme.MUTE))
            p.drawText(QRect(0, 2, self.width() - 4, 11),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, self._key)
        p.end()

    def caption(self) -> str:
        return self._caption


class _Swatch(QWidget):
    """임계값 색 견본(파랑·빨강) — 글자 대신 색 자체로 '이 값 아래/위는 이 색' 을 말한다."""

    def __init__(self, color: QColor, parent=None) -> None:
        super().__init__(parent)
        self._color = color
        self.setFixedSize(12, 12)

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawRoundedRect(self.rect(), 3, 3)
        p.end()


class GlViewerPanel(QWidget):
    """GL 뷰어 본체.  시트(:class:`GlViewerDialog`)에도, 페이지에도 그대로 올린다."""

    def __init__(self, parent=None, *, image_path: str = "") -> None:
        super().__init__(parent)
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
        self._zoom_pending = False
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
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)
        root.addLayout(self._build_head())
        self.banner = QLabel("", self)
        self.banner.setWordWrap(True)
        self.banner.setProperty("role", "muted")
        self.banner.setVisible(False)
        root.addWidget(self.banner)
        body = QHBoxLayout()
        body.setSpacing(10)
        body.addWidget(self._build_rail())
        body.addLayout(self._build_center(), 1)
        body.addWidget(self._build_right())
        root.addLayout(body, 1)

    def _build_head(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.open_btn = NeonButton(_KO.GL_OPEN, role="primary")
        self.open_btn.clicked.connect(self._on_pick)
        self.file_label = QLabel("", self)
        self.file_label.setProperty("role", "subtitle")
        self.compare_btn = NeonButton(_KO.GL_COMPARE, role="ghost")
        self.compare_btn.clicked.connect(self._on_compare)
        # 저장은 메뉴 하나 — 엑셀 · PNG · 'PNG 에 ROI 도' 가 머리줄에 셋이나 늘어서 있었다.
        self.save_btn = NeonButton(_KO.GL_SAVE_MENU, role="ghost")
        self.save_btn.clicked.connect(self._open_save_menu)
        self.save_menu = QMenu(self)
        self.xlsx_act = QAction(_KO.GL_SAVE_XLSX, self)
        self.xlsx_act.triggered.connect(self._on_save_xlsx)
        self.png_act = QAction(_KO.GL_SAVE_PNG, self)
        self.png_act.triggered.connect(self._on_save_png)
        self.png_rois = QAction(_KO.GL_PNG_ROIS, self)
        self.png_rois.setCheckable(True)
        self.png_rois.setChecked(True)
        self.save_menu.addAction(self.xlsx_act)
        self.save_menu.addAction(self.png_act)
        self.save_menu.addSeparator()
        self.save_menu.addAction(self.png_rois)
        for b in (self.open_btn, self.compare_btn, self.save_btn):
            b.setMinimumHeight(theme.PROFILE.control_h_lg)
        bar.addWidget(self.open_btn)
        bar.addWidget(self.file_label, 1)
        bar.addWidget(self.compare_btn)
        bar.addWidget(self.save_btn)
        return bar

    def _open_save_menu(self) -> None:
        self.save_menu.exec(self.save_btn.mapToGlobal(QPoint(0, self.save_btn.height())))

    def _build_rail(self) -> QWidget:
        """사진 왼쪽의 도구 레일 — 그리기 도구 6개, 보기 2개, 편집 2개."""
        host = QWidget(self)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        self._tool_btns: dict[str, _RailTile] = {}
        for i, tool in enumerate(TOOLS, start=1):
            b = _RailTile(_TOOL_ICON[tool], _TOOL_LABEL[tool],
                          _KO.GL_TOOL_TIP_FMT.format(name=_TOOL_LABEL[tool], key=i), host, key=str(i))
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, t=tool: self._set_tool(t))
            self._tool_btns[tool] = b
            col.addWidget(b)
        self._tool_btns["move"].setChecked(True)
        col.addSpacing(10)
        self.fit_btn = _RailTile("fit", _KO.GL_FIT, _KO.GL_FIT_TIP, host, key="F")
        self.fit_btn.clicked.connect(lambda _c=False: self.canvas.fit())
        self.one_btn = _RailTile("", _KO.GL_ONE_TO_ONE, _KO.GL_ONE_TO_ONE_TIP, host)
        self.one_btn.clicked.connect(lambda _c=False: self.canvas.set_scale(1.0))
        col.addWidget(self.fit_btn)
        col.addWidget(self.one_btn)
        col.addSpacing(10)
        self.undo_btn = _RailTile("undo", _KO.GL_UNDO, _KO.GL_UNDO_TIP, host)
        self.undo_btn.clicked.connect(lambda _c=False: self.canvas.undo())
        self.clear_btn = _RailTile("trash", _KO.GL_CLEAR, _KO.GL_CLEAR_TIP, host)
        self.clear_btn.clicked.connect(lambda _c=False: self._clear_rois())
        col.addWidget(self.undo_btn)
        col.addWidget(self.clear_btn)
        col.addStretch(1)
        return host

    def _chip(self, text: str, slot, *, checked: bool, tip: str = "") -> NeonButton:
        """작은 켜기/끄기 칩(세그먼트 등급) — 체크박스 대신."""
        b = NeonButton(text, role="segment")
        b.setCheckable(True)
        b.setChecked(checked)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if tip:
            b.setToolTip(tip)
        b.toggled.connect(slot)
        return b

    def _build_center(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(6)
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
        # 상태 줄: 커서 위치·GL | 배율 | (선택한 도형의 크기 ±) | 알림
        row = QHBoxLayout()
        row.setSpacing(10)
        self.status = QLabel("", self)
        self.status.setProperty("role", "mono")
        # ★ 높이를 크기 ± 버튼과 같게 못 박는다 — 도형을 선택할 때 ± 가 나타나며 줄이 커지면
        #   사진 화면이 몇 px 줄어 **맞춤 배율이 바뀌고 그리던 도형이 밀린다**(실측).
        #   stepper 등급은 QSS min-height(input_h) 가 내용 상자라 보더 2px 이 더해져 그려진다.
        self.status.setMinimumHeight(theme.PROFILE.input_h + 2)
        self.sel_label = QLabel("", self)
        self.sel_label.setProperty("role", "muted")
        self.size_minus = NeonButton(_KO.GL_SIZE_MINUS, role="stepper")
        self.size_plus = NeonButton(_KO.GL_SIZE_PLUS, role="stepper")
        for b, step in ((self.size_minus, -2), (self.size_plus, 2)):
            b.setFixedWidth(theme.PROFILE.input_h)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setToolTip(_KO.GL_SIZE_TIP)
            b.clicked.connect(lambda _c=False, st=step: self.canvas.grow(st))
        self.toast = QLabel("", self)
        self.toast.setProperty("role", "muted")
        self.toast.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)   # 긴 파일명이 줄을 밀지 않게
        self.toast.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.status, 1)
        row.addWidget(self.sel_label)
        row.addWidget(self.size_minus)
        row.addWidget(self.size_plus)
        row.addWidget(self.toast, 1)
        col.addLayout(row)
        # 표 머리줄: 제목 + 선 보이기 칩
        self.table_head = QWidget(self)
        head = QHBoxLayout(self.table_head)
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self._section(_KO.GL_TABLE_TITLE))
        head.addStretch(1)
        show_lab = QLabel(_KO.GL_SHOW_LABEL, self)
        show_lab.setProperty("role", "muted")
        self.show_h = self._chip(_KO.GL_TOOL_HLINE, self._on_show_lines, checked=True,
                                 tip=_KO.GL_SHOW_HLINE)
        self.show_v = self._chip(_KO.GL_TOOL_VLINE, self._on_show_lines, checked=True,
                                 tip=_KO.GL_SHOW_VLINE)
        head.addWidget(show_lab)
        head.addWidget(self.show_h)
        head.addWidget(self.show_v)
        col.addWidget(self.table_head)
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
        # ★ 높이는 고정이다(줄 수에 맞춰 늘리지 않는다).  표가 ROI 를 더할 때마다 자라면 사진
        #   화면이 그만큼 줄어 맞춤 배율이 바뀌고, 끌어서 옮기던 도형이 밀린다(실측).
        self.table.setFixedHeight(_TABLE_H)
        self.table.cellClicked.connect(self._on_table_click)
        col.addWidget(self.table)
        self._sync_selection_widgets()
        self._set_table_visible(False)                  # 사진이 없으면 빈 표가 자리를 차지하지 않는다
        self._set_image_controls(False)
        return col

    def _set_image_controls(self, on: bool) -> None:
        """사진이 없으면 레일 · 비교 · 저장을 흐리게 — 눌러 봐야 '먼저 사진을 열어 주세요' 뿐이다."""
        for w in list(self._tool_btns.values()) + [self.fit_btn, self.one_btn, self.clear_btn,
                                                   self.compare_btn, self.save_btn]:
            w.setEnabled(on)
        self.undo_btn.setEnabled(on and self.canvas.rois.can_undo())

    def _set_table_visible(self, on: bool) -> None:
        self.table_head.setVisible(on)
        self.table.setVisible(on)

    def _build_right(self) -> QWidget:
        host = QWidget(self)
        host.setFixedWidth(_RIGHT_W)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        # 임계값 — 제목 줄 오른쪽에 [자동 추천], 아래 줄에 ■하한 [값]  ■상한 [값]
        thead = QHBoxLayout()
        thead.addWidget(self._section(_KO.GL_THRESHOLD), 1)
        self.auto_btn = NeonButton(_KO.GL_THR_AUTO, role="segment")
        self.auto_btn.setToolTip(_KO.GL_THR_AUTO_TIP)
        self.auto_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.auto_btn.clicked.connect(self._auto_thresholds)
        thead.addWidget(self.auto_btn)
        col.addLayout(thead)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.lo_spin, self.hi_spin = QSpinBox(self), QSpinBox(self)
        for sp, name, short, color in ((self.lo_spin, _KO.GL_THR_LOW, _KO.GL_THR_LOW_SHORT, QColor(47, 111, 224)),
                                       (self.hi_spin, _KO.GL_THR_HIGH, _KO.GL_THR_HIGH_SHORT, QColor(229, 50, 42))):
            sp.setAccessibleName(name)
            sp.setToolTip(name)
            sp.setKeyboardTracking(False)
            sp.setRange(0, 255)
            sp.setStyleSheet(f"QSpinBox {{ color: {color.name()}; }}")
            row.addWidget(_Swatch(color, self))
            lab = QLabel(short, self)
            lab.setProperty("role", "muted")
            lab.setToolTip(name)
            row.addWidget(lab)
            row.addWidget(sp, 1)
        self.lo_spin.valueChanged.connect(lambda v: self._set_thresholds(v, self._hi, "lo"))
        self.hi_spin.valueChanged.connect(lambda v: self._set_thresholds(self._lo, v, "hi"))
        col.addLayout(row)
        self.thr_note = QLabel("", self)
        self.thr_note.setProperty("role", "muted")
        self.thr_note.setWordWrap(True)
        self.thr_note.setVisible(False)
        col.addWidget(self.thr_note)
        # GL 범위 — 16 bit 사진에서만 보인다(8 bit 는 고를 것이 없다).
        self.bits_row = QWidget(host)
        br = QHBoxLayout(self.bits_row)
        br.setContentsMargins(0, 0, 0, 0)
        bits_lab = QLabel(_KO.GL_BITS, self)
        bits_lab.setProperty("role", "muted")
        self.bits_combo = QComboBox(self)
        self.bits_combo.setAccessibleName(_KO.GL_BITS)
        self.bits_combo.currentIndexChanged.connect(self._on_bits_changed)
        br.addWidget(bits_lab)
        br.addWidget(self.bits_combo, 1)
        col.addWidget(self.bits_row)
        scope_row = QHBoxLayout()
        scope_lab = QLabel(_KO.GL_SCOPE_LABEL, self)
        scope_lab.setProperty("role", "muted")
        self.scope = OptionGroup([(analysis.SCOPE_ALL, _KO.GL_SCOPE_ALL),
                                  (analysis.SCOPE_ROI, _KO.GL_SCOPE_ROI),
                                  (analysis.SCOPE_OFF, _KO.GL_SCOPE_OFF)],
                                 current=analysis.SCOPE_ALL, fixed_cols=3, role="segment",
                                 min_tile_w=60)
        self.scope.selection_changed.connect(lambda _k: self._schedule_refresh())
        scope_row.addWidget(scope_lab)
        scope_row.addWidget(self.scope, 1)
        col.addLayout(scope_row)
        self.auto_contrast = SwitchRow(_KO.GL_AUTO_CONTRAST, description=_KO.GL_AUTO_CONTRAST_DESC,
                                       compact=True, parent=host)
        self.auto_contrast.toggled.connect(lambda _c: self._schedule_refresh())
        col.addWidget(self.auto_contrast)
        col.addWidget(self._rule())
        # 히스토그램 — 제목 · 대상 이름 · 보기 칩 셋이 한 줄
        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(self._section(_KO.GL_HIST))
        self.hist_name = QLabel("", self)
        self.hist_name.setProperty("role", "muted")
        head.addWidget(self.hist_name, 1)
        col.addLayout(head)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.overlay_cb = self._chip(_KO.GL_HIST_OVERLAY, lambda _c: self._schedule_refresh(),
                                     checked=False, tip=_KO.GL_HIST_OVERLAY_TIP)
        self.log_cb = self._chip(_KO.GL_HIST_LOG, lambda _c: self._schedule_refresh(), checked=False)
        self.range_cb = self._chip(_KO.GL_HIST_DATA_RANGE, lambda _c: self._schedule_refresh(),
                                   checked=False, tip=_KO.GL_HIST_DATA_RANGE_TIP)
        for cb in (self.overlay_cb, self.log_cb, self.range_cb):
            chips.addWidget(cb)
        chips.addStretch(1)
        col.addLayout(chips)
        self.hist = HistogramChart(self)
        self.hist.thresholdDragged.connect(self._on_hist_drag)
        col.addWidget(self.hist, 3)
        self.hist_info = QLabel("", self)
        self.hist_info.setProperty("role", "mono")
        # 호버 문구는 길어질 수 있다(ROI 여러 개) — 패널 폭(고정)을 밀지 않게 폭을 요구하지 않는다.
        self.hist_info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.hist.hoverText.connect(self.hist_info.setText)
        col.addWidget(self.hist_info)
        # 선 프로파일 — 선을 그었을 때만 나타난다.
        self.profile_rule = self._rule()
        self.profile_head = self._section(_KO.GL_PROFILE)
        self.profile = ProfileChart(self)
        self.profile.hoverText.connect(self.hist_info.setText)
        col.addWidget(self.profile_rule)
        col.addWidget(self.profile_head)
        col.addWidget(self.profile, 2)
        self._set_profile_visible(False)
        self._sync_bits_combo(8)
        return host

    def _rule(self) -> QFrame:
        f = QFrame(self)
        f.setProperty("role", "hrule")
        f.setFixedHeight(1)
        return f

    def _set_profile_visible(self, on: bool) -> None:
        for w in (self.profile_rule, self.profile_head, self.profile):
            w.setVisible(on)

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
        self._set_table_visible(True)
        self._set_image_controls(True)
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
        self._update_file_label(Path(v.source_path or self._gl.path).name)
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
            self._zoom_pending = True
            call_later(self, 0, self._zoom_to_target)
        self._refresh()
        self.canvas.setFocus()

    def _update_file_label(self, name: str) -> None:
        self.file_label.setText(_KO.GL_FILE_LABEL_FMT.format(
            name=name, w=self._gl.shape[1], h=self._gl.shape[0], bits=self._bits))

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
        self.table.setFixedHeight(_TABLE_H_CMP)       # 비교 열고 닫을 때만 바뀐다(끌기 중이 아니다)
        self._refresh()

    def _close_compare(self) -> None:
        self._gl_b = self._view_b = self._full_hist_b = None
        self.pane_b.setVisible(False)
        self.cap_a.setVisible(False)
        self.cap_b.setVisible(False)
        self.compare_btn.setText(_KO.GL_COMPARE)
        self.overlay_cb.setEnabled(True)
        self.table.setFixedHeight(_TABLE_H)

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
            self._zoom_pending = False

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        # ★ 사진을 연 뒤 **처음 보일 때 한 번만** 결함 위치로 간다.  페이지로 올리면 탭을 오갈
        #   때마다 showEvent 가 오는데, 그때마다 되돌리면 사용자가 옮겨 둔 화면이 초기화된다.
        if self._zoom_pending:
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
        self.thr_note.setVisible(bool(note))
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
        self.thr_note.setVisible(False)
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
        self.bits_row.setVisible(len(choices) > 1)      # 8 bit 는 고를 것이 없다 — 행을 아예 없앤다
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
        self._update_file_label(Path(self._view.source_path or self._gl.path).name)
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
        rng = analysis.display_range(data, self._bits, self.auto_contrast.is_on())
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
        self._sync_selection_widgets()
        self._update_status()

    def _sync_selection_widgets(self) -> None:
        """크기 ± 는 **도형을 선택했을 때만** 상태 줄에 나타난다(선은 크기가 없다)."""
        r = self.canvas.rois.active() if self._gl is not None else None
        shape = r is not None and not r.is_line
        self.sel_label.setText(_KO.GL_SEL_SIZE_FMT.format(name=r.name) if shape else "")
        for w in (self.sel_label, self.size_minus, self.size_plus):
            w.setVisible(shape)

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
        self.hist_name.setText(_KO.GL_HIST_NAME_FMT.format(name=name))
        lines = []
        for r in (x for x in rois if x.is_line):
            if self._gl_b is None:
                lines.append((r.name, r.color, analysis.profile(data, r)))
            else:
                lines.append((f"{r.name} A", _CMP_A, analysis.profile(data, r)))
                lines.append((f"{r.name} B", _CMP_B, analysis.profile(self._gl_b.data, r)))
        self.profile.set_lines(lines, levels)
        self.profile.set_thresholds(self._lo, self._hi)
        self._set_profile_visible(bool(lines))

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


class GlViewerDialog(QDialog):
    """GL 뷰어 창(시트) — :class:`GlViewerPanel` 을 담는 껍데기.  ``image_path`` 를 주면 바로 연다."""

    def __init__(self, parent=None, *, image_path: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(_KO.GL_TITLE)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.panel = GlViewerPanel(self)
        lay.addWidget(self.panel)
        if image_path:
            self.open_path(Path(image_path))

    def open_path(self, path: Path, *, raw: Optional[dict] = None) -> None:
        self.panel.open_path(path, raw=raw)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.panel.sizeHint()
