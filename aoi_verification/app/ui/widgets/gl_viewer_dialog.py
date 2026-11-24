"""GL 히스토그램 뷰어 — 사진 한 장의 밝기(GL) 분포를 ROI 별로 본다.

흐름: 사진 열기(파일·끌어놓기) → ROI(사각형·원) → 임계값(하한 이하 파랑·
상한 이상 빨강) → 히스토그램 → 엑셀(통계·히스토그램)·PNG 저장.

★ 컬러 결함 사진을 열면 같은 폴더의 Scan 사진에서 결함 영역(300 µm)을 찾아 그 Scan 을 분석
  대상으로 삼고 결함 위치를 확대해 보여 준다(``gl.loader.load_for_view``).
★ 분석은 원본 비트심도 그대로다.  화면용 8 bit 변환은 보기 전용이다.
★ 긴 작업(사진 읽기·Scan 찾기)은 백그라운드 스레드에서 하고 로딩바는 busy 로 띄운다.
"""

from __future__ import annotations

import html
import logging
import os
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from PyQt6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QComboBox, QDialog,
                             QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QMenu, QPushButton, QSpinBox, QSplitter, QToolButton, QVBoxLayout,
                             QWidget)

from ... import i18n
from ...config import CONFIG
from ...gl import analysis, loader
from ...gl import roi as R
from ...gl.export import build_workbook, save_workbook
from .. import theme
from ..deferred import call_later
from ..drop_overlay import drop_clear, drop_feedback
from ..drop_paths import guess_kinds, install_paste, local_paths
from ..folder_picker import FilePicker, SavePicker, last_dir
from . import sheet_host as sheets
from .gl_canvas import TOOLS, GlCanvas
from .gl_charts import ALL_ROW, HistogramChart
from .loading_overlay import LoadingOverlay
from .neon_button import NeonButton
from .option_group import OptionGroup

_LOG = logging.getLogger("aoi.gl")
_KO = i18n.KO

_TOOL_LABEL = {"move": _KO.GL_TOOL_MOVE, R.RECT: _KO.GL_TOOL_RECT, R.CIRCLE: _KO.GL_TOOL_CIRCLE}
_WARN_TEXT = {loader.W_COLOR: _KO.GL_WARN_COLOR, loader.W_JPEG: _KO.GL_WARN_JPEG,
              loader.W_HUGE: _KO.GL_WARN_HUGE}

# 열려 있는 읽기 스레드 — 창이 먼저 닫혀도 QThread 가 도는 채로 지워지지 않게 붙들어 둔다.
_LIVE_WORKERS: set = set()

# 두 사진 비교 — A(기준)는 청사진 파랑, B(검증)는 주황.  명도 차가 커서 색만으로도 갈린다.
_CMP_A, _CMP_B = "#2C5A86", "#C2410C"


_INSPECTOR_W = 360      # 오른쪽 인스펙터 고정 폭


def _tool_icon(kind: str) -> QIcon:
    """팔레트 그림(16 px, 선 1.5) — 검은 사진 위라 밝은 잉크로 그린다."""
    pm = QPixmap(32, 32)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(theme.BG or "#ECE9E2"), 1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    L = lambda *v: p.drawLine(QPointF(v[0], v[1]), QPointF(v[2], v[3]))  # noqa: E731
    if kind == "move":
        L(8, 2, 8, 14); L(2, 8, 14, 8)
        for ax, ay, bx, by, cx, cy in ((6, 4, 8, 2, 10, 4), (6, 12, 8, 14, 10, 12),
                                       (4, 6, 2, 8, 4, 10), (12, 6, 14, 8, 12, 10)):
            L(ax, ay, bx, by); L(bx, by, cx, cy)
    elif kind == R.RECT:
        p.drawRoundedRect(QRectF(2.5, 3.5, 11, 9), 1, 1)
    elif kind == R.CIRCLE:
        p.drawEllipse(QRectF(2.5, 2.5, 11, 11))
    elif kind == "undo":
        p.drawArc(QRectF(3, 5, 10, 8), 90 * 16, -230 * 16)
        L(8, 5, 3.5, 5); L(3.5, 5, 6, 2.5); L(3.5, 5, 6, 7.5)
    elif kind == "clear":
        L(3, 4.5, 13, 4.5); L(6.5, 2.5, 9.5, 2.5)
        L(4.5, 4.5, 5.2, 13.5); L(5.2, 13.5, 10.8, 13.5); L(10.8, 13.5, 11.5, 4.5)
    p.end()
    return QIcon(pm)


def _view_exts() -> set:
    """열 수 있는 사진 확장자(파일 열기 창과 끌어놓기가 같이 쓴다)."""
    return set(CONFIG.image_extensions) | {".tif", ".tiff", ".png", ".bmp", ".raw", ".bin", ".img"}


def _shape_txt(img: loader.GlImage) -> str:
    return f"{img.shape[1]}×{img.shape[0]}"


def _delta_row(a: dict, b: dict) -> dict:
    """B − A.  어느 쪽이든 비어 있으면 비교할 수 없는 항목은 ``None`` 이다."""
    def d(k):
        return None if a[k] is None or b[k] is None else b[k] - a[k]
    return {"name": f"{a['name']} · B−A", "box": "", "delta": True, "n": b["n"] - a["n"],
            "mean": d("mean"), "std": d("std"), "min": d("min"), "max": d("max"),
            "below": b["below"] - a["below"], "above": b["above"] - a["above"]}


DEFECT_MARK_UM = 50.0     # 'Defect' ROI 한 변(µm) — Scan 상자(CROP_SIDE_UM=300)에 대한 비율로 환산한다


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

    #: 작은 팝업 — 시트 제목줄 ✕ 를 뺀다(자기 [취소] 가 있다, `sheet_host.POPUP_SMALL_ATTR`).
    POPUP_SMALL = True

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


class GlViewerDialog(QDialog):
    """GL 뷰어 창.  ``image_path`` 를 주면 바로 연다."""

    def __init__(self, parent=None, *, image_path: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(_KO.GL_TITLE)
        self.setAcceptDrops(True)
        install_paste(self, self._open_first_file, self._on_paste_no_path)
        self._gl: Optional[loader.GlImage] = None
        self._view: Optional[loader.ViewLoad] = None
        self._defect_id: Optional[int] = None      # Scan 결함 상자에 만든 ROI 의 id
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
        self._hover: Optional[tuple[int, int, int]] = None      # 마우스 아래 (x, y, GL)
        self._loading = LoadingOverlay(self)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self._refresh)
        # OS 파일 창은 전부 비동기(`ui/folder_picker.py`) — `exec()` 는 Windows 에서
        # UI 스레드를 세운다.  열기 · 비교는 같은 사진 폴더를, 저장 둘은 같은 저장 폴더를 기억한다.
        self._open_picker = FilePicker(self, purpose="gl_open")
        self._open_picker.picked.connect(lambda paths: self.open_path(Path(paths[0])))
        self._cmp_picker = FilePicker(self, purpose="gl_open")
        self._cmp_picker.picked.connect(self._on_cmp_picked)
        self._xlsx_picker = SavePicker(self, purpose="gl_save")
        self._xlsx_picker.picked.connect(self._on_xlsx_picked)
        self._png_picker = SavePicker(self, purpose="gl_save")
        self._png_picker.picked.connect(self._on_png_picked)
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
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addLayout(self._build_head())
        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self._build_left(), 1)
        body.addWidget(self._build_right())
        root.addLayout(body, 1)
        root.addWidget(self._build_status())
        self._sync_empty_state()

    def _build_head(self) -> QHBoxLayout:
        """머리줄 — 남색 채움은 '엑셀 저장' 하나, PNG 는 외곽선, 나머지는 글자 버튼."""
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 12, 16, 12)
        bar.setSpacing(8)
        self.file_label = QLabel("", self)
        self.open_btn = NeonButton(_KO.GL_OPEN, role="textAccent")
        self.open_btn.clicked.connect(self._on_pick)
        self.xlsx_btn = NeonButton(_KO.GL_SAVE_XLSX, role="primary")
        self.xlsx_btn.clicked.connect(self._on_save_xlsx)
        self.png_btn = NeonButton(_KO.GL_SAVE_PNG_SHORT, role="ghost")
        menu = QMenu(self.png_btn)
        menu.addAction(_KO.GL_SAVE_PNG, self._on_save_png)
        menu.addSeparator()
        self.png_rois = menu.addAction(_KO.GL_PNG_ROIS)
        self.png_rois.setCheckable(True)
        self.png_rois.setChecked(True)
        self.png_btn.setMenu(menu)
        self.compare_btn = NeonButton(_KO.GL_COMPARE, role="textAction")
        self.compare_btn.clicked.connect(self._on_compare)
        self.toast = QLabel("", self)
        self.toast.setProperty("role", "muted")
        for b in (self.open_btn, self.compare_btn, self.png_btn, self.xlsx_btn):
            b.setProperty("grade", "panel")             # 머리줄 버튼은 모두 32 px
        bar.addWidget(self.file_label)
        bar.addWidget(self.open_btn)
        bar.addStretch(1)
        # ★ 긴 안내가 한 줄로 서면 그 폭이 창 최소 폭이 돼 1100px 창에 가로 스크롤이 생겼다
        #   (최소 1332px, 실측 2026-11-24) — 줄을 바꿔 남는 폭 안에 담는다.
        self.toast.setWordWrap(True)
        self.toast.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar.addWidget(self.toast, 1)
        bar.addWidget(self.compare_btn)
        bar.addWidget(self.png_btn)
        bar.addWidget(self.xlsx_btn)
        return bar

    @staticmethod
    def _cap_html(tag: str, color: str, name: str) -> str:
        return f'<span style="color:{color}">■</span> {tag} · {html.escape(name)}'

    def _set_note(self, notes: list, *, warn: bool = False) -> None:
        """사진 안내를 상태줄 오른쪽에 한 줄로 둔다(열린 동안 유지).  경고가 있으면 경고색."""
        text = " · ".join(notes)
        self.note.setText(text)
        self.note.setToolTip(text)
        self.note.setProperty("role", "warn" if warn else "mutedSmall")
        self.note.style().unpolish(self.note)
        self.note.style().polish(self.note)
        self.note.setVisible(bool(notes))

    @staticmethod
    def _set_role(btn: QPushButton, role: str) -> None:
        if btn.property("role") == role:
            return
        btn.setProperty("role", role)
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def _sync_empty_state(self) -> None:
        """사진이 없으면 '사진 열기' 만 남색 채움이고 사진이 필요한 것은 모두 꺼 둔다."""
        has = self._gl is not None
        self._set_role(self.open_btn, "textAccent" if has else "primary")
        self._set_role(self.xlsx_btn, "primary" if has else "ghost")
        for b in (self.xlsx_btn, self.png_btn, self.compare_btn, self.clear_btn):
            b.setEnabled(has)
        for b in self._tool_btns.values():
            b.setEnabled(has)
        if not has:
            self.undo_btn.setEnabled(False)
            self.defect_btn.setEnabled(False)
        self.scope.setEnabled(has)
        self._zoom_bar.setVisible(has)

    def _tool_button(self, text: str, slot, *, icon: str = "", checkable: bool = False,
                     tip: str = "") -> QToolButton:
        """사진 위 팔레트의 버튼.  방향키는 사진 화면이 받으므로 포커스를 가져가지 않는다."""
        b = QToolButton(self)
        b.setProperty("role", "viewerTool")
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setCheckable(checkable)
        b.setAccessibleName(text)
        if icon:
            b.setIcon(_tool_icon(icon))
            b.setIconSize(QSize(16, 16))
            b.setToolTip(tip or text)           # 그림만 있는 버튼이라 이름을 띄운다
        else:
            b.setText(text)
            if tip:
                b.setToolTip(tip)
        b.clicked.connect(lambda _c=False: slot())
        return b

    def _build_overlays(self) -> None:
        """사진 위에 뜨는 것 둘 — 왼쪽 위 도구 팔레트, 오른쪽 아래 배율 막대."""
        tools = QFrame(self.canvas)
        tools.setProperty("role", "viewerTools")
        col = QVBoxLayout(tools)
        col.setContentsMargins(4, 4, 4, 4)
        col.setSpacing(2)
        self._tool_btns: dict[str, QToolButton] = {}
        for tool in TOOLS:
            b = self._tool_button(_TOOL_LABEL[tool], lambda t=tool: self._set_tool(t),
                                  icon=tool, checkable=True)
            self._tool_btns[tool] = b
            col.addWidget(b)
        self._tool_btns["move"].setChecked(True)
        sep = QFrame(tools)
        sep.setProperty("role", "viewerSep")
        sep.setFixedHeight(1)
        col.addSpacing(2)
        col.addWidget(sep)
        col.addSpacing(2)
        self.undo_btn = self._tool_button(_KO.GL_UNDO, lambda: self.canvas.undo(), icon="undo",
                                          tip=_KO.GL_UNDO)
        col.addWidget(self.undo_btn)
        self.clear_btn = self._tool_button(_KO.GL_CLEAR, self._clear_rois, icon="clear")
        col.addWidget(self.clear_btn)

        zoom = self._zoom_bar = QFrame(self.canvas)
        zoom.setProperty("role", "viewerTools")
        row = QHBoxLayout(zoom)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(2)
        self.zoom_label = QLabel("", zoom)
        self.zoom_label.setProperty("role", "viewerZoom")
        row.addWidget(self.zoom_label)
        row.addWidget(self._tool_button(_KO.GL_FIT, lambda: self.canvas.fit(), tip="F"))
        self.defect_btn = self._tool_button(_KO.GL_DEFECT_GO, self._go_defect,
                                            tip=_KO.GL_DEFECT_GO_OFF_TIP)
        self.defect_btn.setEnabled(False)
        row.addWidget(self.defect_btn)

        over = QGridLayout(self.canvas)
        over.setContentsMargins(12, 12, 12, 12)
        over.addWidget(tools, 0, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        over.addWidget(zoom, 0, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

    def _build_left(self) -> QWidget:
        self.canvas = GlCanvas(self)
        self.canvas_b = GlCanvas(self)
        self.canvas_b.rois = self.canvas.rois          # 같은 ROI 를 두 사진에 함께 쓴다
        for c in (self.canvas, self.canvas_b):
            c.roisChanged.connect(self._schedule_refresh)
            c.selectionChanged.connect(self._on_selection)
            c.hoverInfo.connect(self._on_hover)
            c.viewStateChanged.connect(self._sync_views)
        self.canvas.viewChanged.connect(self._update_status)
        self._build_overlays()
        self.cap_a = QLabel("", self)
        self.cap_b = QLabel("", self)
        for cap in (self.cap_a, self.cap_b):
            cap.setProperty("role", "muted")
            cap.setTextFormat(Qt.TextFormat.RichText)      # 앞의 ■ 견본이 차트 선 색과 같다
            cap.setContentsMargins(16, 0, 8, 4)
        self.split = QSplitter(Qt.Orientation.Horizontal, self)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(1)
        for cap, cv in ((self.cap_a, self.canvas), (self.cap_b, self.canvas_b)):
            pane = QWidget(self.split)
            lay = QVBoxLayout(pane)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            lay.addWidget(cap)
            lay.addWidget(cv, 1)
            self.split.addWidget(pane)
            cap.setVisible(False)
        self.pane_b = self.split.widget(1)
        self.pane_b.setVisible(False)
        return self.split

    def _build_status(self) -> QFrame:
        """상태줄 — 왼쪽은 마우스 아래 읽기값(x y GL · 구역), 오른쪽은 배율만."""
        bar = QFrame(self)
        bar.setProperty("role", "statusBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 4, 16, 4)
        row.setSpacing(0)
        self.status = QLabel("", bar)
        self.status.setProperty("role", "mono")
        self.status.setMinimumHeight(theme.PROFILE.target_min)
        row.addWidget(self.status)
        self.zone_word = QLabel("", bar)            # 하한 이하(파랑) · 사이(중립) · 상한 이상(빨강) — 색은 QSS 역할
        self.zone_word.setProperty("role", "glZoneMid")
        row.addWidget(self.zone_word)
        row.addStretch(1)
        # 사진 안내(Scan 찾음·경고)는 그 사진이 열려 있는 동안만 나온다.
        self.note = QLabel("", bar)
        self.note.setProperty("role", "mutedSmall")
        self.note.setVisible(False)
        # 긴 안내(Scan 못 찾음 · 밝기 분석 …)는 줄을 바꾼다 — 한 줄이면 창 최소 폭을 키운다.
        self.note.setWordWrap(True)
        self.note.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.note, 1)
        self.zoom_status = QLabel("", bar)
        self.zoom_status.setProperty("role", "mono")
        row.addSpacing(16)
        row.addWidget(self.zoom_status)
        return bar

    def _build_right(self) -> QFrame:
        """오른쪽 인스펙터 — 폭을 고정해 창이 커지면 사진만 커진다."""
        host = QFrame(self)
        host.setProperty("role", "inspector")
        host.setFixedWidth(_INSPECTOR_W)
        col = QVBoxLayout(host)
        col.setContentsMargins(24, 16, 16, 12)
        col.setSpacing(8)
        # 색칠 범위가 맨 위 — 임계값은 히스토그램 아래 축(손잡이 · 값 칩)이 조작한다.
        scope_lab = QLabel(_KO.GL_SCOPE_LABEL, host)
        scope_lab.setProperty("role", "muted")
        col.addWidget(scope_lab)
        self.scope = OptionGroup([(analysis.SCOPE_ALL, _KO.GL_SCOPE_ALL),
                                  (analysis.SCOPE_ROI, _KO.GL_SCOPE_ROI),
                                  (analysis.SCOPE_OFF, _KO.GL_SCOPE_OFF)],
                                 current=analysis.SCOPE_ALL, fixed_cols=3, role="segment",
                                 min_tile_w=64)
        self.scope.selection_changed.connect(lambda _k: self._schedule_refresh())
        for k in self.scope.keys():
            self.scope.button(k).setProperty("grade", "gl")      # 인스펙터 안 32 px · 같은 글자 크기
        col.addWidget(self.scope)
        col.addSpacing(16)                       # 묶음 사이 24
        # 값 입력칸은 칩을 눌렀을 때만 그 자리에 뜬다.
        self.lo_spin, self.hi_spin = QSpinBox(host), QSpinBox(host)
        for sp, name in ((self.lo_spin, _KO.GL_THR_LOW), (self.hi_spin, _KO.GL_THR_HIGH)):
            sp.setAccessibleName(name)
            sp.setKeyboardTracking(False)
            sp.setRange(0, 255)
            sp.setVisible(False)
            sp.editingFinished.connect(sp.hide)
        self.lo_spin.valueChanged.connect(lambda v: self._set_thresholds(v, self._hi, "lo"))
        self.hi_spin.valueChanged.connect(lambda v: self._set_thresholds(self._lo, v, "hi"))
        self.thr_note = QLabel("", self)
        self.thr_note.setProperty("role", "muted")
        self.thr_note.setWordWrap(True)
        self.thr_note.setVisible(False)
        # 히스토그램
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self._section(_KO.GL_HIST))
        self.hist_name = QLabel("", self)
        self.hist_name.setProperty("role", "muted")
        self.hist_name.setMinimumWidth(0)
        head.addWidget(self.hist_name, 1)
        self.by_roi_btn = QPushButton(_KO.GL_HIST_BY_ROI, self)
        self.by_roi_btn.setProperty("role", "histToggle")
        self.by_roi_btn.setCheckable(True)
        self.by_roi_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.by_roi_btn.setEnabled(False)
        self.by_roi_btn.setToolTip(_KO.GL_HIST_BY_ROI_NEED)
        self.by_roi_btn.toggled.connect(lambda _c: self._on_by_roi_toggled())
        head.addWidget(self.by_roi_btn)
        col.addLayout(head)
        self.hist = HistogramChart(self)
        self.hist.setEnabled(False)                 # 사진을 열면 켠다
        self.hist.thresholdDragged.connect(self._on_hist_drag)
        self.hist.editRequested.connect(self._edit_threshold)
        self.hist.rowClicked.connect(self._on_hist_row)
        col.addWidget(self.hist, 1)
        self.hist_info = QLabel("", self)
        self.hist_info.setProperty("role", "mono")
        self.hist.hoverText.connect(self.hist_info.setText)
        col.addWidget(self.hist_info)
        col.addWidget(self.thr_note)
        return host

    def _on_by_roi_toggled(self) -> None:
        self._sync_hist_controls()
        self._schedule_refresh()

    def _on_hist_row(self, rid: int) -> None:
        """줄을 누르면 그 ROI 를 고른다 — 캔버스에서 고른 것과 같은 흐름(selectionChanged)을 탄다."""
        self.canvas._select(None if rid == ALL_ROW else rid)

    def _sync_hist_controls(self) -> None:
        """'ROI 별로 보기' 의 켜짐 조건 — ROI 가 있고 비교 중이 아닐 때만 줄 쌓기."""
        n = len(self.canvas.rois.rois)
        comparing = self._gl_b is not None
        ok = n >= 1 and not comparing
        btn = self.by_roi_btn
        if not ok and btn.isChecked():
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.blockSignals(False)
        btn.setEnabled(ok)
        btn.setToolTip("" if ok else (_KO.GL_HIST_BY_ROI_CMP if comparing else _KO.GL_HIST_BY_ROI_NEED))

    def _edit_threshold(self, which: str, at) -> None:
        """히스토그램 값 칩 자리에 입력칸을 띄운다(Enter 나 다른 곳을 누르면 닫힌다)."""
        if self._gl is None:
            return
        sp = self.lo_spin if which == "lo" else self.hi_spin
        (self.hi_spin if which == "lo" else self.lo_spin).hide()
        host = sp.parentWidget()
        w, h = 104, sp.sizeHint().height()
        origin = self.hist.mapTo(host, at.toRect().center())
        x = max(8, min(origin.x() - w // 2, host.width() - w - 8))
        y = max(8, min(origin.y() - h // 2, host.height() - h - 8))
        sp.setGeometry(x, y, w, h)
        sp.show()
        sp.raise_()
        sp.setFocus()
        sp.selectAll()

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

    def _clear_rois(self) -> None:
        if self.canvas.rois.clear():
            self.canvas.roisChanged.emit()
            self.canvas.selectionChanged.emit()
            self.canvas.update()

    def _ensure_defect_roi(self) -> None:
        """Scan 결함 상자에 'Defect' 사각형 ROI 를 둔다(이미 있으면 그대로) — 선택까지 한다."""
        box = self._view.target_box if self._view else None
        if not box:
            return
        r = self.canvas.rois.get(self._defect_id)
        if r is None:
            w, h = self.canvas.image_size()
            l, t, bw, bh = (int(round(v)) for v in box)
            # 300 µm 상자 한가운데에 50 µm 정사각 표시 — 상자 전체를 칠하면 결함이 어디인지 안 보인다.
            cx, cy = l + bw / 2.0, t + bh / 2.0
            mw = max(3, int(round(bw * DEFECT_MARK_UM / 300.0)))
            mh = max(3, int(round(bh * DEFECT_MARK_UM / 300.0)))
            r = R.clamp_shape(R.Roi(0, R.RECT, "", "", x=int(round(cx - mw / 2.0)),
                                    y=int(round(cy - mh / 2.0)), w=mw, h=mh), w, h)
            self._defect_id = self.canvas.rois.add(r, _KO.GL_ROI_DEFECT, theme.VIEWER_BOX).id
        else:
            self.canvas.rois.selected = r.id
        self.canvas.roisChanged.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()

    def _go_defect(self) -> None:
        if self._view and self._view.target_box:
            self._ensure_defect_roi()
            self.canvas.zoom_to_box(self._view.target_box)

    # ------------------------------------------------------------------
    # 사진 열기
    # ------------------------------------------------------------------
    def _on_pick(self) -> None:
        patterns = " ".join(f"*{e}" for e in sorted(_view_exts()))
        start = str(Path(self._gl.path).parent) if self._gl and self._gl.path else ""
        self._open_picker.open(_KO.GL_OPEN, start,
                               _KO.GL_OPEN_FILTER_FMT.format(patterns=patterns))

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
        self._defect_id = None
        self._bits = v.image.bits
        self._full_hist = analysis.histogram(self._gl.data, self._bits)
        if self._gl_b is not None:
            self._full_hist_b = analysis.histogram(self._gl_b.data, self._bits)
        self._set_range_widgets()
        self.hist.setEnabled(True)
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
            why = _KO.GL_SCAN_WHY.get(v.scan_status, _KO.GL_SCAN_WHY_UNKNOWN)
            notes.append(_KO.GL_SCAN_MISSING_FMT.format(why=why))
            self._toast(notes[-1], error=True)
        notes += [_WARN_TEXT[w] for w in self._gl.warnings
                  if w in _WARN_TEXT and not (v.scan_name and w == loader.W_COLOR)]
        self._set_note(notes, warn=bool(notes) and not (v.scan_name and len(notes) == 1))
        self.defect_btn.setEnabled(bool(v.target_box))
        self.defect_btn.setToolTip("" if v.target_box else _KO.GL_DEFECT_GO_OFF_TIP)
        self._sync_empty_state()
        if v.target_box:
            self._ensure_defect_roi()
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
        self._cmp_picker.open(_KO.GL_CMP_PICK, start,
                              _KO.GL_OPEN_FILTER_FMT.format(patterns=patterns))

    def _on_cmp_picked(self, paths: list) -> None:
        if self._gl is not None and self._gl_b is None:     # 고르는 사이 상태가 바뀌었으면 무시
            self.open_path_b(Path(paths[0]))

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
        self.canvas_b.set_view_state(self.canvas.view_state())
        self.pane_b.setVisible(True)
        self.cap_a.setText(self._cap_html("A", _CMP_A, Path(self._view.source_path or self._gl.path).name))
        self.cap_b.setText(self._cap_html("B", _CMP_B, Path(v.source_path or b.path).name))
        self.cap_a.setVisible(True)
        self.cap_b.setVisible(True)
        self.compare_btn.setText(_KO.GL_COMPARE_CLOSE)
        self._sync_hist_controls()
        self.split.setSizes([1, 1])
        self._refresh()

    def _close_compare(self) -> None:
        self._gl_b = self._view_b = self._full_hist_b = None
        self.pane_b.setVisible(False)
        self.cap_a.setVisible(False)
        self.cap_b.setVisible(False)
        self.compare_btn.setText(_KO.GL_COMPARE)
        self._sync_hist_controls()

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
    def hideEvent(self, event):         # noqa: N802
        drop_clear(self)                # 끌던 중에 숨겨져도 표시가 남지 않게
        super().hideEvent(event)

    def dragEnterEvent(self, e):  # noqa: N802
        paths = local_paths(e.mimeData()) if e.mimeData().hasUrls() else []   # 디스크는 drop 에서만 본다
        if not paths:
            drop_clear(self)
            e.ignore()
            return
        ok = "image" in guess_kinds(paths)
        drop_feedback(self, e, None, _KO.DROP_PILL_OPEN_IMAGE if ok else _KO.DROP_PILL_ONLY_IMAGE, ok)

    def dragMoveEvent(self, e):  # noqa: N802
        self.dragEnterEvent(e)

    def dragLeaveEvent(self, e):  # noqa: N802
        drop_clear(self)
        e.accept()

    def dropEvent(self, e):  # noqa: N802
        drop_clear(self)
        paths = local_paths(e.mimeData())
        if paths:
            e.acceptProposedAction()
            self._open_first_file(paths)

    def _open_first_file(self, paths) -> None:
        """경로 중 첫 사진 파일을 연다.  (A 가 백그라운드로 읽히는 중이라 B 는 같이 열지 않는다.)"""
        for s in paths:
            p = Path(s)
            if p.suffix.lower() in _view_exts() and p.is_file():
                self.open_path(p)
                return
        self._toast(_KO.DROP_WRONG_KIND_IMAGE, error=True)

    def _on_paste_no_path(self, has_image: bool) -> None:
        self._toast(_KO.DROP_NO_PATH_IMAGE if has_image else _KO.DROP_NOTHING_TO_PASTE, error=True)

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

    # ------------------------------------------------------------------
    # 갱신
    # ------------------------------------------------------------------
    def _schedule_refresh(self) -> None:
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _make_rgb(self, gl: Optional[loader.GlImage] = None) -> np.ndarray:
        data = (gl or self._gl).data
        rng = analysis.display_range(self._bits)
        return analysis.overlay_rgb(data, rng, self._lo, self._hi,
                                    self.scope.current_key(), self.canvas.rois.rois)

    def _refresh(self) -> None:
        if self._gl is None:
            return
        self.canvas.set_display(self._make_rgb())
        self.canvas.set_thresholds(self._lo, self._hi)
        if self._gl_b is not None:
            self.canvas_b.set_display(self._make_rgb(self._gl_b))
            self.canvas_b.set_thresholds(self._lo, self._hi)
        self._refresh_charts()
        self.undo_btn.setEnabled(self.canvas.rois.can_undo())
        self._update_status()

    def _refresh_charts(self) -> None:
        data = self._gl.data
        levels = self._levels()
        rois = self.canvas.rois.rois
        active = self.canvas.rois.active()
        series, name = [], _KO.GL_HIST_ALL
        self._sync_hist_controls()
        if self.by_roi_btn.isChecked():
            rows = [(ALL_ROW, _KO.GL_HIST_ALL, theme.INK2 or "#3D3B35", self._full_hist)]
            rows += [(r.id, r.name, r.color,
                      analysis.histogram(analysis.roi_values(data, r), self._bits)) for r in rois]
            self.hist.set_rows(rows, levels, ALL_ROW if active is None else active.id)
            self.hist.set_thresholds(self._lo, self._hi)
            self.hist_name.setText("")
            return
        if self._gl_b is not None:
            # 비교: 선택한 ROI(없으면 사진 전체)의 A·B 분포를 % 로 겹친다.
            if active is not None:
                ha = analysis.histogram(analysis.roi_values(data, active), self._bits)
                hb = analysis.histogram(analysis.roi_values(self._gl_b.data, active), self._bits)
                name = active.name
            else:
                ha, hb = self._full_hist, self._full_hist_b
            na = Path(self._view.source_path or self._gl.path).name
            nb = Path(self._view_b.source_path or self._gl_b.path).name
            self.hist.set_compare([(0, f"A · {na}", _CMP_A, ha), (1, f"B · {nb}", _CMP_B, hb)], levels)
            self.hist.set_thresholds(self._lo, self._hi)
            self.hist_name.setText(self._elide_name(name))
            self.hist_name.setToolTip(name)
            return
        elif active is not None:
            series = [(active.name, theme.MUTE or "#5A574E",
                       analysis.histogram(analysis.roi_values(data, active), self._bits))]
            name = active.name
        else:
            series = [(_KO.GL_HIST_ALL, theme.MUTE or "#5A574E", self._full_hist)]
        self.hist.set_series(series, levels)
        self.hist.set_thresholds(self._lo, self._hi)
        self.hist_name.setText(self._elide_name(name))
        self.hist_name.setToolTip(name)

    def _elide_name(self, name: str) -> str:
        fm = self.hist_name.fontMetrics()
        return fm.elidedText(name, Qt.TextElideMode.ElideRight, 88)

    def _stat_row(self, r: Optional[R.Roi], gl: Optional[loader.GlImage] = None) -> dict:
        data = (gl or self._gl).data
        if r is None:
            vals, name, box = data.reshape(-1), _KO.GL_ROW_ALL, ""
        else:
            vals = analysis.roi_values(data, r)
            name = r.name
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

    def _on_selection(self) -> None:
        self._schedule_refresh()

    def _on_hover(self, x: int, y: int, gl: int) -> None:
        """사진 위 마우스 읽기값 — x y GL 과 구역 글자, 히스토그램에는 그 GL 의 세로선."""
        self._hover = (x, y, gl) if x >= 0 else None
        self.hist.set_hover_gl(gl if x >= 0 else None)
        self._update_status()

    def _zone_of(self, gl: int) -> tuple[str, str]:
        if gl <= self._lo:
            return _KO.GL_ZONE_LOW, "glZoneLow"
        if gl >= self._hi:
            return _KO.GL_ZONE_HIGH, "glZoneHigh"
        return _KO.GL_ZONE_MID, "glZoneMid"

    def _update_status(self, *_a) -> None:
        if self._gl is None:
            self.status.setText("")
            self.zone_word.setText("")
            self.zoom_label.setText("")
            self.zoom_status.setText("")
            return
        zoom = int(round(self.canvas.scale() * 100))
        if self._hover is None:
            self.status.setText("")
            self.zone_word.setText("")
        else:
            x, y, gl = self._hover
            word, role = self._zone_of(gl)
            self.status.setText(_KO.GL_STATUS_FMT.format(x=x, y=y, gl=gl))
            self.zone_word.setText(f" · {word}")
            if self.zone_word.property("role") != role:
                self._set_role(self.zone_word, role)
        self.zoom_status.setText(_KO.GL_STATUS_ZOOM_FMT.format(zoom=zoom))
        self.zoom_label.setText(f"{zoom}%")

    def _toast(self, text: str, *, error: bool = False) -> None:
        self.toast.setText(text)
        call_later(self, 6000 if error else 4000, lambda: self.toast.setText(""))

    # ------------------------------------------------------------------
    # 저장
    # ------------------------------------------------------------------
    def _default_name(self, suffix: str) -> str:
        """기본 저장 이름 — 다른 도구와 같은 규칙 ``<도구>_<대상>_<YYYYMMDD_HHMM>``, 폴더도 같은
        결과 폴더(``paths.results_dir``, 마지막 저장 폴더가 있으면 그곳).  예전엔 사진이 있는 NAS 폴더에 ``<사진>_GL`` 로 썼다."""
        from datetime import datetime

        from ...utils import paths

        base = Path(self._view.source_path if self._view and self._view.source_path
                    else (self._gl.path if self._gl else "gl")).stem
        # 마지막으로 저장한 폴더가 먼저, 없으면 결과 폴더.
        last = last_dir("gl_save")
        folder = Path(last) if last else paths.results_dir()
        return str(folder / f"GL_{base}_{datetime.now():%Y%m%d_%H%M}{suffix}")

    def _on_save_xlsx(self) -> None:
        if self._gl is None:
            self._toast(_KO.GL_NO_IMAGE, error=True)
            return
        self._xlsx_picker.open(_KO.GL_SAVE_XLSX_TITLE, self._default_name(".xlsx"),
                               "Excel (*.xlsx)", "xlsx")

    def _on_xlsx_picked(self, path: str) -> None:
        if not path or self._gl is None:
            return
        try:
            self.save_xlsx(Path(path))
        except Exception as exc:
            _LOG.exception("GL 엑셀 저장 실패")
            self._toast(_KO.GL_SAVE_FAILED_FMT.format(why=exc), error=True)
            return
        self._toast(_KO.GL_SAVED_FMT.format(name=Path(path).name))

    def save_xlsx(self, path: Path) -> None:
        """통계·히스토그램을 엑셀 한 장으로 저장한다(헤드리스 호출 가능)."""
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
        else:
            info.insert(1, (_KO.GL_INFO_B, self._view_b.source_path if self._view_b else self._gl_b.path))
            db = self._gl_b.data
            hist_cols = [(f"{_KO.GL_HIST_ALL} A", self._full_hist),
                         (f"{_KO.GL_HIST_ALL} B", self._full_hist_b)]
            for r in rois:
                hist_cols += [(f"{r.name} A", analysis.histogram(analysis.roi_values(data, r), self._bits)),
                              (f"{r.name} B", analysis.histogram(analysis.roi_values(db, r), self._bits))]
        titles = {"info": _KO.GL_XLSX_SHEET_INFO, "stats": _KO.GL_XLSX_SHEET_STATS,
                  "hist": _KO.GL_XLSX_SHEET_HIST, "gl": _KO.GL_XLSX_GL,
                  "stats_cols": [_KO.GL_COL_ROI, _KO.GL_COL_BOX, _KO.GL_COL_COUNT, _KO.GL_COL_MEAN,
                                 _KO.GL_COL_STD, _KO.GL_COL_MIN, _KO.GL_COL_MAX,
                                 _KO.GL_COL_BELOW, _KO.GL_COL_BELOW_PCT, _KO.GL_COL_ABOVE,
                                 _KO.GL_COL_ABOVE_PCT]}
        wb = build_workbook(info, self._stat_rows(), hist_cols, titles)
        save_workbook(wb, path)

    def _on_save_png(self) -> None:
        if self._gl is None:
            self._toast(_KO.GL_NO_IMAGE, error=True)
            return
        self._png_picker.open(_KO.GL_SAVE_PNG_TITLE, self._default_name(".png"),
                              "PNG (*.png)", "png")

    def _on_png_picked(self, path: str) -> None:
        if not path or self._gl is None:
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
            for r in self.canvas.rois.rois:
                pen = QPen(QColor(r.color), lw)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
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
