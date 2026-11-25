"""'어느 Zone에서 왔니?' — LOT · 웨이퍼 폴더의 Defect 를 분류 정보로 골라 사진으로 훑는다(현택님 2026-11-23, 1안).

화면(작동 시안 1안 — 현택님 결정 2026-11-23):
  * 위 줄 = 폴더 칩(✕ 로 빼기, 5초 [되돌리기]) · [+ 폴더] · [엑셀로 저장] · 지금 고른 수 / 전체.
  * 왼쪽 열 = 고르기 칸(분류 이름 · 검출 종류 · 잡힌 곳 · 레시피 · Zone · 웨이퍼 + 그 밖에).  누르면 바로 줄어든다.
    칸 숫자는 '그걸 누르면 몇 개가 되는지' (:func:`threshold.browse.facet_counts`), 같은 칸 안은 '또는'.
    칸 줄 순서는 폴더를 읽을 때 한 번 정한다 — 누를 때마다 줄이 튀지 않게.
  * 가운데 = 사진(분류 이름별 묶음, :data:`PAGE_TILES` 장씩 [더 보기]) 또는 표.  정렬은 Overkill 튜닝과 같은 방식.
  * 오른쪽 = 고른 Defect 값 + 컬러 사진 + Scan 300 µm(뒤에서 찾는다) + [크게 보기] · [사진 정보로 보기].

읽기 · 계산은 :mod:`threshold.browse` 가 하고(Overkill 튜닝과 같은 파일 · 같은 규칙), 화면은 숫자를 다시 만들지 않는다.
사진 타일 · 크게보기 · Scan Crop 은 Overkill 튜닝 부품을 그대로 쓴다.  엑셀은 Defect 추출과 같은 출력(:func:`extract_result`).
좌표(col · row)는 이 화면에서 계산하지 않는다 — [사진 정보로 보기] 가 사진 정보 화면과 같은 계산으로 보인다.

읽기는 워커 스레드, 진행은 시그널로 :class:`LoadingOverlay` 에 보낸다(CLAUDE.md 로딩 계약).
"""

from __future__ import annotations

import html
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QEvent, QObject, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QDoubleValidator, QImage, QIntValidator, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from ... import i18n
from ...config import CONFIG
from ...threshold import browse as B
from ...utils import image_io, paths
from .. import motion, theme
from ..drop_overlay import drop_clear, drop_feedback
from ..drop_paths import guess_kinds, install_paste, local_paths, split_paths
from ..folder_picker import FolderPicker
from . import sheet_host as sheets
from .loading_overlay import LoadingOverlay
from .neon_button import NeonButton
from ..deferred import call_later
from ..teardown import tolerate_teardown
from .threshold_preview_dialog import (_CountBar, _OnMark, _ThumbBus, _Tile, _mid_key, _tile_key, expand_root,
                                       no_flt_dirs, scan_crops)

_LOG = logging.getLogger("aoi.zone_origin")
_KO = i18n.KO

LOAD_WORKERS = 6            # 웨이퍼를 동시에 읽는 수(Overkill 튜닝과 같다 — NAS 왕복 지연을 겹친다)
THUMB_WORKERS = 3
SCAN_WAIT_MS = 150          # 고른 Defect 에 이만큼 머물면 Scan 을 찾는다(화살표로 지나가는 것은 건너뜀)
PAGE_TILES = 60
TILE_PX = 118
TILE_GAP = 2
SIDE_W = 260
DETAIL_W = 300
DETAIL_PIC = 136
TABLE_MAX = 2000            # 표는 앞 이만큼 줄까지(그 뒤는 사진처럼 [더 보기] 없이 안내만)
NOTICE_MS = 5000
_LIVE: set = set()          # 화면이 닫혀도 도는 읽기 스레드를 붙들어 둔다

# 정렬 — (키, 이름, 소수 자리).  browse.SORT_KEYS 중 값 정렬만(웨이퍼 순은 기본 묶음이 이미 그렇다).
SORTS = (("area", "Area", 1), ("width", "Width", 2), ("length", "Length", 2), ("contrast", "Contrast", 1),
         ("height", "Height", 1))
# 왼쪽 바 아래 하한 칸 — (Flags 키, 이름).  [파라미터 열기] 로 펴야 보인다.
PARAMS = (("min_area", _KO.ZO_MIN_AREA), ("min_width", _KO.ZO_MIN_WIDTH), ("min_length", _KO.ZO_MIN_LENGTH),
          ("min_contrast", _KO.ZO_MIN_CONTRAST))
TABLE_COLS = (("lot", _KO.ZO_COL_LOT, None), ("wafer", _KO.ZO_COL_WAFER, None),
              ("colrow", _KO.ZO_COL_COLROW, None), ("recipe", _KO.ZO_COL_RECIPE, None), ("zone", _KO.ZO_COL_ZONE, None),
              ("first_name", _KO.ZO_COL_CLASS, None), ("cur_name", _KO.ZO_COL_CUR, None),
              ("type_name", _KO.ZO_COL_TYPE, None),
              ("area", _KO.ZO_COL_AREA, 1), ("width", _KO.ZO_COL_WIDTH, 2), ("length", _KO.ZO_COL_LENGTH, 2),
              ("contrast", _KO.ZO_COL_CONTRAST, 1), ("height", _KO.ZO_COL_HEIGHT, 1), ("photo", _KO.ZO_COL_PHOTO, None))


def item_key(it: B.Item) -> tuple:
    return (it.wafer, it.kind, it.index)


def fmt_num(v, dec: int) -> str:
    return "—" if v is None else f"{v:,.{dec}f}"


class _Load(QThread):
    """고른 폴더들 → 웨이퍼 목록 · 웨이퍼마다 :func:`browse.load_folder`.  다 읽은 웨이퍼는 끊겨도 올린다."""

    class _Signals(QObject):
        progress = pyqtSignal(int, int, int, str)     # token, done, total, msg
        expanded = pyqtSignal(int, object, object)    # token, 고른 폴더, [웨이퍼 폴더]
        no_flt = pyqtSignal(int, object, object)      # token, 고른 폴더, [(분류 정보 없는 폴더, 사진만인가)]
        wafer = pyqtSignal(int, object, object)       # token, 웨이퍼 폴더, WaferItems
        done = pyqtSignal(int, bool)

    def __init__(self, token: int, roots: list, expand: dict, todo_ids: dict) -> None:
        super().__init__()
        self._token, self._roots, self._expand = token, list(roots), dict(expand)
        self._ids = dict(todo_ids)        # 이미 번호를 붙인 웨이퍼 폴더(다시 읽지 않는다)
        self.signals = self._Signals()
        _LIVE.add(self)
        self.finished.connect(lambda: _LIVE.discard(self))

    def run(self) -> None:      # type: ignore[override]
        t = self._token
        stop = self.isInterruptionRequested
        try:
            self.signals.progress.emit(t, 0, 0, _KO.ZO_LOADING)
            todo: list = []
            for r in self._roots:
                if r not in self._expand:
                    self._expand[r] = [d for d, _real in expand_root(r)]
                    self.signals.expanded.emit(t, r, self._expand[r])
                    self.signals.no_flt.emit(t, r, no_flt_dirs(r))
                todo += [d for d in self._expand[r] if d not in self._ids and d not in todo]
            n = len(todo)
            if n:
                self.signals.progress.emit(t, 0, n, _KO.ZO_LOADING_WAFERS.format(i=0, n=n))
                with ThreadPoolExecutor(max_workers=min(LOAD_WORKERS, n)) as pool:
                    futs = {pool.submit(B.load_folder, d): d for d in todo}
                    for i, fut in enumerate(as_completed(futs), start=1):
                        if stop():
                            for f in futs:
                                f.cancel()
                        if fut.cancelled():
                            continue
                        try:
                            got = fut.result()
                        except Exception as e:      # noqa: BLE001 — 한 웨이퍼 실패가 나머지를 막지 않는다
                            _LOG.exception("어느 Zone에서 왔니? 웨이퍼 읽기 실패: %s", futs[fut])
                            got = B.WaferItems(futs[fut], note=_KO.ZO_READ_FAIL.format(err=e))
                        self.signals.wafer.emit(t, futs[fut], got)
                        self.signals.progress.emit(t, i, n, _KO.ZO_LOADING_WAFERS.format(i=i, n=n))
            if stop():
                return
        except Exception:       # noqa: BLE001 — 실패를 삼키지 않고 화면에 알린다
            _LOG.exception("어느 Zone에서 왔니? 읽기 실패: %s", self._roots)
            self.signals.done.emit(t, False)
            return
        self.signals.done.emit(t, True)


class _ScanFind(QThread):
    """이 Defect 의 Scan 300 µm Crop 을 뒤에서 찾는다 — Overkill 튜닝 · 사진 정보와 같은 :func:`scan_crops`(Crop 캐시 포함).
    ``done(키, QImage | None)`` — 키를 같이 보내 화면이 바뀐 뒤 늦게 온 결과를 가린다."""

    class _Signals(QObject):
        done = pyqtSignal(object, object)

    def __init__(self, key: tuple, folder: Path, xy: tuple) -> None:
        super().__init__()
        self._item = (key, folder, xy)
        self.signals = self._Signals()
        _LIVE.add(self)
        self.finished.connect(lambda: _LIVE.discard(self))

    def run(self) -> None:      # type: ignore[override]
        got = None
        try:
            got = scan_crops([self._item]).get(self._item[0])
        except Exception:       # noqa: BLE001 — 못 찾으면 '없음'
            _LOG.exception("어느 Zone에서 왔니? Scan 찾기 실패")
        self.signals.done.emit(self._item[0], got)


class _FacetRow(QFrame):
    """고르기 칸 한 줄 — 표시 · 이름 · 수.  줄 전체가 누르는 자리(Overkill 튜닝 왼쪽 열과 같은 모양)."""

    clicked = pyqtSignal()
    disc_clicked = pyqtSignal()

    def __init__(self, name: str, count: int, on: bool, note: str = "", parent=None,
                 disc: Optional[bool] = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "ovRow")
        self.setProperty("selected", "true" if on else "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(30)
        self.on, self.count = on, count
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)
        lay.addWidget(_OnMark(on, self))
        self.name = QLabel(name, self)
        self.name.setProperty("role", "ovRowName")
        self.name.setToolTip(name)
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.name, 1)
        if note:
            n = QLabel(note, self)
            n.setProperty("role", "ovRowNote")
            n.setProperty("warn", "true")
            lay.addWidget(n)
        self.num = QLabel(f"{count:,}", self)
        self.num.setProperty("role", "ovRowCount")
        lay.addWidget(self.num)
        self.disc = None
        if disc is not None:                          # LOT 줄 — 맨 끝 ▾/▸ 로 웨이퍼 목록을 펴고 접는다
            lay.setContentsMargins(8, 4, 0, 4)
            self.disc = _nf(QPushButton("▾" if disc else "▸", self))
            self.disc.setProperty("role", "ovRowDisc")
            self.disc.setFixedSize(22, 24)
            self.disc.setCursor(Qt.CursorShape.PointingHandCursor)
            self.disc.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.disc.setToolTip(_KO.ZO_LOT_TOGGLE_TIP)
            self.disc.clicked.connect(self.disc_clicked.emit)
            lay.addWidget(self.disc)
        if count == 0 and not on:
            self.setProperty("excluded", "true")      # 눌러도 0 개 — 옅게

    def mousePressEvent(self, e):   # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class _Pic(QLabel):
    """오른쪽 값 칸의 사진 한 장 — 누르면 크게."""

    clicked = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(DETAIL_PIC, DETAIL_PIC)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(f"background: #000; color: {theme.MUTE};")
        self.setWordWrap(True)

    def mousePressEvent(self, e):   # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class ZoneOriginDialog(QDialog):
    """왼쪽 메뉴 '어느 Zone에서 왔니?' 화면."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_KO.ZO_TITLE)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        install_paste(self, self._add_dropped, self._on_paste_no_path)
        self._roots: list = []            # 고른 폴더(칩 하나씩)
        self._expand: dict = {}           # 고른 폴더 → [웨이퍼 폴더]
        self._no_flt: dict = {}           # 고른 폴더 → [(분류 정보 없는 폴더, 사진만인가)]
        self._data: dict = {}             # 웨이퍼 폴더 → WaferItems (빼고 다시 더해도 다시 읽지 않는다)
        self._ids: dict = {}              # 웨이퍼 폴더 → 번호(Item.wafer)
        self._folder_of: dict = {}        # 번호 → 웨이퍼 폴더
        self._order: dict = {}            # 칸 → 값 순서(읽을 때 한 번)
        self._sel: dict = {k: set() for k, _n in B.FACETS}
        self._flags = B.Flags()
        self._params_open = False         # [파라미터 열기] — 기본 접힘
        self._lot_open: dict = {}         # LOT → 웨이퍼 목록을 펼쳤나(없으면 LOT 이 하나일 때만 펼침)
        self._sort: Optional[tuple] = None          # (키, 큰 순?)
        self._view = "tiles"
        self._shown = PAGE_TILES
        self._open: Optional[tuple] = None          # 고른 Defect(item_key)
        self._passed: list = []
        self._tile_order: list = []
        self._tile_rows: list = []
        self._tiles: dict = {}
        self._last_cols = 0                # 타일을 그린 열 수 — 창 폭이 열 수를 바꿀 때만 다시 그린다
        self._keep_pos = 0
        self._scans: dict = {}            # item_key → Scan Crop QImage | None(없음)
        self._undo_snap: Optional[tuple] = None
        self._token = 0
        self._thread: Optional[_Load] = None
        self._overlay_on = False
        self._exporter = None
        self._thumb_bus = _ThumbBus(self)
        self._thumb_bus.ready.connect(self._on_thumb)
        self._thumb_pool = ThreadPoolExecutor(max_workers=THUMB_WORKERS, thread_name_prefix="zone-origin-thumb")
        self._wanted: set = set()
        self._scan_threads: dict = {}       # 찾는 중인 Scan — 같은 Defect 를 두 번 찾지 않는다
        self._scan_viewer = None            # (키, 크게보기 뷰어) — 찾는 중에 연 뷰어에 결과를 넘긴다
        # 화살표를 누르고 있으면 지나가는 Defect 마다 Scan 을 찾으러 갔다(NAS 읽기 수백 개) — 멈춘 Defect 만 찾는다.
        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.setInterval(SCAN_WAIT_MS)
        self._scan_timer.timeout.connect(self._scan_current)
        self._pending: set = set()
        self._picker = FolderPicker(self)
        self._picker.picked.connect(lambda p: p and self.add_folders([Path(p)]))
        self._build()
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
        self._loading = LoadingOverlay(self)
        self._loading.cancel_requested.connect(self._cancel_load)
        self._render()

    # ------------------------------------------------------------------ 틀
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        head = QFrame(self)
        head.setProperty("role", "ovHeader")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 10, 16, 10)
        hl.setSpacing(8)
        self._chip_lay = QHBoxLayout()
        self._chip_lay.setSpacing(6)
        hl.addLayout(self._chip_lay)
        self.pick_btn = _nf(NeonButton(_KO.ZO_ADD_FOLDER, role="ghost"))
        self.pick_btn.setToolTip(_KO.ZO_ADD_FOLDER_TIP)
        self.pick_btn.clicked.connect(self._on_pick)
        hl.addWidget(self.pick_btn)
        self.excel_btn = _nf(NeonButton(_KO.ZO_BTN_EXCEL, role="ghost"))
        self.excel_btn.setToolTip(_KO.ZO_BTN_EXCEL_TIP)
        self.excel_btn.clicked.connect(self.save_excel)
        hl.addWidget(self.excel_btn)
        hl.addStretch(1)
        self.big_count = QLabel("", head)
        self.big_count.setTextFormat(Qt.TextFormat.RichText)
        hl.addWidget(self.big_count)
        root.addWidget(head)

        self.notice = QFrame(self)
        self.notice.setObjectName("notice")
        self.notice.setStyleSheet(f"QFrame#notice {{ background: {theme.ACCENT_TINT}; }}")
        nl = QHBoxLayout(self.notice)
        nl.setContentsMargins(16, 4, 8, 4)
        self.notice_label = QLabel("", self.notice)
        self.notice_label.setStyleSheet("font-weight: 600; background: transparent;")
        nl.addWidget(self.notice_label, 1)
        self.undo_btn = _nf(NeonButton(_KO.OVERKILL_UNDO, role="ghost"))
        self.undo_btn.clicked.connect(self._undo)
        nl.addWidget(self.undo_btn)
        self.notice.setVisible(False)
        root.addWidget(self.notice)
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(self._fade_notice)

        self.alert = QFrame(self)
        self.alert.setObjectName("alert")
        self.alert.setStyleSheet(f"QFrame#alert {{ background: {theme.DANGER_TINT}; }}")
        al = QHBoxLayout(self.alert)
        al.setContentsMargins(16, 6, 16, 6)
        self.alert_label = QLabel("", self.alert)
        self.alert_label.setWordWrap(True)
        self.alert_label.setStyleSheet(f"font-weight: 600; color: {theme.DANGER}; background: transparent;")
        al.addWidget(self.alert_label, 1)
        self.alert.setVisible(False)
        root.addWidget(self.alert)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        # 왼쪽 — 고르기 칸
        side = QFrame(self)
        side.setProperty("role", "ovSide")
        side.setFixedWidth(SIDE_W)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        self.side_scroll = QScrollArea(side)
        self.side_scroll.setProperty("role", "ovSideScroll")
        self.side_scroll.setWidgetResizable(True)
        self.side_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sl.addWidget(self.side_scroll)
        body.addWidget(side)

        # 가운데 — 정렬 줄 · 고른 것 · 사진/표
        center = QWidget(self)
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        bar = QFrame(center)
        bar.setProperty("role", "ovHeader")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 6, 14, 6)
        bl.setSpacing(4)
        self.count_label = QLabel("", bar)
        self.count_label.setTextFormat(Qt.TextFormat.RichText)
        bl.addWidget(self.count_label)
        bl.addSpacing(12)
        sort_cap = QLabel(_KO.ZO_SORT_TIP, bar)
        sort_cap.setProperty("role", "muted")
        bl.addWidget(sort_cap)
        self.sort_btns: dict = {}
        for key, name, _dec in SORTS:
            b = _nf(NeonButton(name, role="ovView"))
            b.setProperty("sort", "true")
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, k=key: self.set_sort(k))
            bl.addWidget(b)
            self.sort_btns[key] = b
        bl.addStretch(1)
        self.view_btns: dict = {}
        for key, name in (("tiles", _KO.ZO_VIEW_TILES), ("table", _KO.ZO_VIEW_TABLE)):
            b = _nf(NeonButton(name, role="ovView"))
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, k=key: self.set_view(k))
            bl.addWidget(b)
            self.view_btns[key] = b
        cl.addWidget(bar)
        self.chips_host = QWidget(center)
        self._sel_lay = QHBoxLayout(self.chips_host)
        self._sel_lay.setContentsMargins(14, 6, 14, 6)
        self._sel_lay.setSpacing(6)
        cl.addWidget(self.chips_host)
        self.grid_scroll = QScrollArea(center)
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_host = QWidget()
        self.grid = QVBoxLayout(self.grid_host)
        self.grid.setContentsMargins(14, 10, 14, 24)
        self.grid.setSpacing(TILE_GAP)
        self.grid_scroll.setWidget(self.grid_host)
        self.grid_scroll.viewport().installEventFilter(self)      # 폭이 바뀌면 한 줄 사진 수를 다시 맞춘다
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(120)
        self._relayout_timer.timeout.connect(self._relayout_now)
        cl.addWidget(self.grid_scroll, 1)
        self.table = QTableWidget(0, len(TABLE_COLS), center)
        self.table.setHorizontalHeaderLabels([n for _k, n, _d in TABLE_COLS])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().sectionClicked.connect(self._on_table_header)
        self.table.itemSelectionChanged.connect(self._on_table_pick)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self.enlarge_open())
        self.table.setVisible(False)
        cl.addWidget(self.table, 1)
        self.table_note = QLabel("", center)
        self.table_note.setProperty("role", "muted")
        self.table_note.setContentsMargins(14, 4, 14, 4)
        self.table_note.setVisible(False)
        cl.addWidget(self.table_note)
        body.addWidget(center, 1)

        # 오른쪽 — 값
        det = QFrame(self)
        det.setProperty("role", "ovSide")
        det.setStyleSheet(f"QFrame[role=\"ovSide\"] {{ border-right: none; border-left: 1px solid {theme.LINE2}; }}")
        det.setFixedWidth(DETAIL_W)
        dl = QVBoxLayout(det)
        dl.setContentsMargins(12, 12, 12, 12)
        dl.setSpacing(10)
        pics = QHBoxLayout()
        pics.setSpacing(6)
        self.color_pic, self.scan_pic = _Pic(det), _Pic(det)
        self.color_pic.clicked.connect(self.enlarge_open)
        self.scan_pic.clicked.connect(self._enlarge_scan)
        for pic, cap in ((self.color_pic, _KO.ZO_DETAIL_COLOR), (self.scan_pic, _KO.ZO_DETAIL_SCAN)):
            col = QVBoxLayout()
            col.setSpacing(3)
            col.addWidget(pic)
            c = QLabel(cap, det)
            c.setProperty("role", "muted")
            col.addWidget(c)
            pics.addLayout(col)
        self.detail_pics = QWidget(det)
        self.detail_pics.setLayout(pics)
        dl.addWidget(self.detail_pics)
        self.kv_host = QWidget(det)
        self.kv = QGridLayout(self.kv_host)
        self.kv.setContentsMargins(0, 0, 0, 0)
        self.kv.setHorizontalSpacing(12)
        self.kv.setVerticalSpacing(4)
        self.kv.setColumnStretch(1, 1)
        dl.addWidget(self.kv_host)
        self.file_label = QLabel("", det)
        self.file_label.setProperty("role", "muted")
        self.file_label.setWordWrap(True)
        self.file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        dl.addWidget(self.file_label)
        acts = QHBoxLayout()
        acts.setSpacing(6)
        self.enlarge_btn = _nf(NeonButton(_KO.ZO_BTN_ENLARGE, role="ghost"))
        self.enlarge_btn.clicked.connect(self.enlarge_open)
        self.info_btn = _nf(NeonButton(_KO.ZO_BTN_INFO, role="ghost"))
        self.info_btn.clicked.connect(self.open_info)
        acts.addWidget(self.enlarge_btn)
        acts.addWidget(self.info_btn)
        acts.addStretch(1)
        dl.addLayout(acts)
        self.detail_empty = QLabel(_KO.ZO_DETAIL_EMPTY, det)
        self.detail_empty.setProperty("role", "muted")
        self.detail_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dl.addWidget(self.detail_empty)
        dl.addStretch(1)
        body.addWidget(det)
        self.detail = det
        root.addLayout(body, 1)

    # ------------------------------------------------------------------ 폴더
    def _on_pick(self) -> None:
        self._picker.open(_KO.ZO_ADD_FOLDER_TIP, str(self._roots[-1]) if self._roots else "")

    def add_folders(self, folders) -> None:
        new = [f for f in dict.fromkeys(map(Path, folders)) if f not in self._roots]
        dup = [f for f in map(Path, folders) if f in self._roots]
        if dup:
            self._show_notice(_KO.OVERKILL_NOTICE_DUP.format(name=dup[0].name))
        if new:
            self._roots += new
            self._start_load()

    def remove_folder(self, folder: Path) -> None:
        if folder not in self._roots:
            return
        i = self._roots.index(folder)
        self._undo_snap = (i, folder)
        self._roots.pop(i)
        self._after_roots()
        self._show_notice(_KO.ZO_NOTICE_REMOVED.format(name=folder.name), undo=True)

    def _undo(self) -> None:
        if self._undo_snap is None:
            return
        i, folder = self._undo_snap
        self._undo_snap = None
        self.notice.setVisible(False)
        if folder not in self._roots:
            self._roots.insert(min(i, len(self._roots)), folder)
            # 읽는 중에 뺐다 되돌리면 반만 읽힌 채일 수 있다 — 빠진 웨이퍼가 있으면 마저 읽는다.
            if folder not in self._expand or any(d not in self._data for d in self._expand[folder]):
                self._start_load()
            else:
                self._after_roots()

    def _after_roots(self) -> None:
        """폴더 목록이 바뀌었다(읽을 것 없음) — 빠진 웨이퍼의 고름을 풀고 다시 그린다."""
        live = {self._ids[d] for d in self._wafer_folders() if d in self._ids}
        self._sel["wafer"] &= live
        if self._open is not None and self._open[0] not in live:
            self._open = None
        self._refresh_order()
        self._shown = PAGE_TILES
        self._render()

    def _wafer_folders(self) -> list:
        out: list = []
        for r in self._roots:
            for d in self._expand.get(r, ()):
                if d not in out:
                    out.append(d)
        return out

    def _show_notice(self, text: str, undo: bool = False) -> None:
        self.notice_label.setText(text)
        self.undo_btn.setVisible(undo)
        motion.banner_in(self.notice)
        self._notice_timer.start(NOTICE_MS)

    def _fade_notice(self) -> None:
        motion.banner_out(self.notice)

    # ------------------------------------------------------------------ 읽기
    def _start_load(self) -> None:
        self._token += 1
        if self._thread is not None and self._thread.isRunning():
            self._thread.requestInterruption()
        self._loading.show_overlay(_KO.ZO_LOADING, cancelable=True)
        self._loading.set_progress(0, 0, _KO.ZO_LOADING)
        self._overlay_on = True
        th = _Load(self._token, self._roots, self._expand, self._ids)
        th.signals.progress.connect(self._on_progress)
        th.signals.expanded.connect(self._on_expanded)
        th.signals.no_flt.connect(self._on_no_flt)
        th.signals.wafer.connect(self._on_wafer)
        th.signals.done.connect(self._on_loaded)
        self._thread = th
        th.start()

    def _cancel_load(self) -> None:
        """[중지] — 아직 다 읽지 못한 폴더는 빼고, 다 읽은 폴더로 화면을 그린다."""
        if not self._overlay_on:
            return
        self._token += 1
        if self._thread is not None:
            self._thread.requestInterruption()
        self._roots = [r for r in self._roots if r in self._expand and all(d in self._data for d in self._expand[r])]
        self._hide_overlay()
        self._after_roots()

    def _hide_overlay(self) -> None:
        if self._overlay_on:
            self._loading.hide_overlay()
            self._overlay_on = False

    def _on_progress(self, token: int, done: int, total: int, msg: str) -> None:
        if token == self._token and self._overlay_on:
            self._loading.set_progress(done, total, msg)

    def _on_expanded(self, _token: int, root, folders) -> None:
        self._expand[root] = list(folders)

    def _on_no_flt(self, _token: int, root, dirs) -> None:
        self._no_flt[root] = list(dirs)

    def _on_wafer(self, _token: int, folder, data) -> None:
        """웨이퍼 하나를 다 읽었다 — 번호를 붙여 둔다(끊겨도 버리지 않는다)."""
        if folder in self._ids:
            return
        n = len(self._ids)
        self._ids[folder] = n
        self._folder_of[n] = folder
        self._data[folder] = replace(data, items=tuple(_with_wafer(it, n) for it in data.items))

    def _on_loaded(self, token: int, ok: bool) -> None:
        if token != self._token:
            return
        self._hide_overlay()
        if not ok:
            self._show_notice(_KO.ZO_READ_FAIL.format(err="—"))
        self._after_roots()

    def set_wafers(self, roots: list, wafers: dict) -> None:
        """테스트 · 바로 넣기 — 스레드 없이 ``{고른 폴더: [WaferItems]}`` 를 올린다."""
        for r in roots:
            r = Path(r)
            self._roots.append(r)
            self._expand[r] = []
            for w in wafers.get(r, ()):
                self._expand[r].append(w.folder)
                self._on_wafer(0, w.folder, w)
        self._after_roots()

    # ------------------------------------------------------------------ 계산
    def items(self) -> list:
        out: list = []
        for d in self._wafer_folders():
            w = self._data.get(d)
            if w is not None:
                out += w.items
        return out

    def wafer_label(self, n: int) -> str:
        folder = self._folder_of.get(n)
        if folder is None:
            return "?"
        w = self._data.get(folder)
        return _KO.ZO_WAFER_FMT.format(slot=w.slot or "?", name=w.wafer_id or folder.name) if w else folder.name

    def wafer_of(self, n: int) -> Optional[B.WaferItems]:
        return self._data.get(self._folder_of.get(n))

    def _wafer_rank(self, n: int) -> tuple:
        """LOT 안 웨이퍼 순서 — 슬롯 번호 순(모르면 뒤), 같으면 읽은 순."""
        w = self.wafer_of(n)
        slot = w.slot if w is not None else ""
        return (int(slot) if slot.isdecimal() else 1 << 30, n)

    def lot_open(self, lot: str) -> bool:
        return self._lot_open.get(lot, len(self._order.get("lot", [])) <= 1)

    def toggle_lot(self, lot: str) -> None:
        self._lot_open[lot] = not self.lot_open(lot)
        self._render(keep_scroll=True)

    def facet_label(self, k: str, v) -> str:
        return self.wafer_label(v) if k == "wafer" else str(v)

    def _refresh_order(self) -> None:
        items = self.items()
        for k, _n in B.FACETS:
            cnt: dict = {}
            for it in items:
                if it.good and not self._flags.good:
                    continue
                v = getattr(it, k)
                cnt[v] = cnt.get(v, 0) + 1
            keep = [v for v in self._order.get(k, []) if v in cnt]
            if k == "wafer":
                self._order[k] = sorted(cnt, key=self._wafer_rank)
            elif k == "lot":
                self._order[k] = sorted(cnt)
            else:
                new = sorted((v for v in cnt if v not in keep), key=lambda v: -cnt[v])
                self._order[k] = keep + new
            self._sel[k] &= set(cnt)

    def compute(self) -> tuple:
        return B.facet_counts(self.items(), self._sel, self._flags)

    def _sorted(self, passed: list) -> list:
        if self._sort is None:
            rank = {v: i for i, v in enumerate(self._order.get("first_name", []))}
            return sorted(passed, key=lambda it: (rank.get(it.first_name, 1 << 30), self._wafer_rank(it.wafer),
                                                  it.kind, it.index))
        return B.sort_items(passed, self._sort[0], self._sort[1])

    # ------------------------------------------------------------------ 그리기
    def _render(self, keep_scroll: bool = False) -> None:
        passed, counts = self.compute()
        self._passed = self._sorted(passed)
        if self._passed and (self._open is None or not any(item_key(i) == self._open for i in self._passed)):
            first = next((i for i in self._passed if i.photo is not None), self._passed[0])
            self._open = item_key(first)
        elif not self._passed:
            self._open = None
        top = self.window()
        frozen = top.updatesEnabled()
        top.setUpdatesEnabled(False)
        try:
            self._render_head()
            self._render_alert()
            self._render_side(counts)
            self._render_sel()
            self._render_bar()
            if self._view == "table":
                self._render_table()
            else:
                self._render_tiles(keep_scroll)
            self._render_detail()
        finally:
            top.setUpdatesEnabled(frozen)

    def _render_head(self) -> None:
        _clear(self._chip_lay)
        for r in self._roots:
            chip = QFrame(self)
            chip.setProperty("role", "ovRow")
            chip.setStyleSheet(f"QFrame[role=\"ovRow\"] {{ border: 1px solid {theme.LINE2}; background: {theme.ELEV}; }}")
            lay = QHBoxLayout(chip)
            lay.setContentsMargins(10, 2, 2, 2)
            lay.setSpacing(6)
            wafers = [self._data[d] for d in self._expand.get(r, ()) if d in self._data]
            n = sum(1 for w in wafers for it in w.items if not it.good)
            name = QLabel(f"<b>{html.escape(r.name)}</b>", chip)
            name.setProperty("role", "ovRowName")
            lay.addWidget(name)
            cnt = QLabel(_KO.ZO_CHIP_COUNT_FMT.format(w=len(wafers), n=f"{n:,}"), chip)
            cnt.setProperty("role", "ovRowCount")
            lay.addWidget(cnt)
            x = _nf(NeonButton("✕", role="ovRowX"))
            x.setFixedSize(24, 24)
            x.setToolTip(_KO.ZO_CHIP_REMOVE_TIP)
            x.clicked.connect(lambda _c=False, f=r: self.remove_folder(f))
            lay.addWidget(x)
            self._chip_lay.addWidget(chip)
        self.excel_btn.setEnabled(bool(self._roots))
        total = sum(1 for it in self.items() if not it.good or self._flags.good)
        mono = theme.FONT_MONO.split(",")[0].strip('" ')
        self.big_count.setText(
            f'<span style="font-family: {mono}; font-size: 20px; font-weight: 700; color: {theme.INK};">'
            f'{len(self._passed):,}</span><span style="color: {theme.MUTE};">  / {total:,}</span>' if self._roots else "")

    def _render_alert(self) -> None:
        lines = []
        conder = [d.name for r in self._roots for d, _only in self._no_flt.get(r, ())]
        if conder:
            lines.append(_KO.ZO_NO_INFO_FMT.format(n=len(conder), names=" · ".join(conder[:6])
                                                   + (" …" if len(conder) > 6 else "")))
        cut = [self._data[d] for d in self._wafer_folders() if d in self._data and self._data[d].cap]
        if cut:
            lines.append(_KO.ZO_CAP_FMT.format(cap=cut[0].cap, n=len(cut)))
        notes = [f"{d.name}: {self._data[d].note}" for d in self._wafer_folders()
                 if d in self._data and self._data[d].note]
        lines += notes[:3]
        self.alert_label.setText("\n".join(lines))
        self.alert.setVisible(bool(lines))

    def _edits(self) -> dict:
        """지금 왼쪽 바의 입력칸 ``{키: QLineEdit}`` (좌표 · 파라미터)."""
        return {**getattr(self, "coord_edits", {}), **getattr(self, "min_edits", {})}

    def _edit_done(self, key: str, value) -> None:
        """입력칸 editingFinished — **바로 그리지 않고 한 박자 뒤에** 적용한다.

        그 자리에서 다시 그리면 신호를 낸 입력칸(포커스를 옮기던 중인 칸)을 지워 프로그램이 꺼졌다
        (좌표를 넣고 다른 칸을 누를 때, 재현 2026-11-24)."""
        call_later(self, 0, lambda: self.set_flag(key, value))

    def _render_side(self, counts: dict) -> None:
        bar = self.side_scroll.verticalScrollBar()
        pos = bar.value()
        fw = QApplication.focusWidget()
        focus_key = next((k for k, e in self._edits().items() if e is fw), None)
        # 쓰다 만 글자(Enter 전 · '-' 처럼 아직 수가 아닌 것)는 다시 그려도 남긴다 — 그 칸 값이 그대로일 때만.
        drafts = {k: (e.text(), getattr(self._flags, k)) for k, e in self._edits().items()
                  if e.property("shown") != e.text()}
        self.coord_edits, self.min_edits = {}, {}
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setContentsMargins(8, 12, 8, 20)
        il.setSpacing(1)
        self.facet_rows: dict = {}
        if not self._roots:
            hint = QLabel(_KO.ZO_EMPTY, inner)
            hint.setProperty("role", "muted")
            hint.setWordWrap(True)
            hint.setContentsMargins(8, 0, 8, 0)
            il.addWidget(hint)
        else:
            for k, title in B.FACETS:
                if k == "wafer":
                    continue                        # LOT 아래에 펼쳐 그린다
                head = QLabel(title, inner)
                head.setProperty("role", "ovGroupHead")
                head.setContentsMargins(8, 10, 0, 4)
                il.addWidget(head)
                vals = self._order.get(k, [])
                top = max([counts[k].get(v, 0) for v in vals] or [1]) or 1
                for v in vals:
                    if k == "lot":
                        self._render_lot(inner, il, v, counts)
                        continue
                    n, on = counts[k].get(v, 0), v in self._sel[k]
                    row = _FacetRow(self.facet_label(k, v), n, on, "", inner)
                    row.clicked.connect(lambda k=k, v=v: self.toggle(k, v))
                    self.facet_rows[(k, v)] = row
                    il.addWidget(row)
                    if k == "first_name":
                        cb = _CountBar(n / top, on, inner)
                        cb.setContentsMargins(30, 0, 8, 0)
                        wrap = QHBoxLayout()
                        wrap.setContentsMargins(30, 0, 8, 2)
                        wrap.addWidget(cb)
                        il.addLayout(wrap)
            self._render_coord(inner, il)
            self.param_btn = _nf(NeonButton(_KO.ZO_PARAM_CLOSE if self._params_open else _KO.ZO_PARAM_OPEN,
                                        role="textAction", parent=inner))
            self.param_btn.clicked.connect(self.toggle_params)
            il.addSpacing(8)
            il.addWidget(self.param_btn, 0, Qt.AlignmentFlag.AlignLeft)
            self.min_edits: dict = {}
            for key, text in PARAMS if self._params_open else ():
                row = QHBoxLayout()
                row.setContentsMargins(8, 1, 8, 1)
                lab = QLabel(text, inner)
                lab.setProperty("role", "ovRowName")
                row.addWidget(lab, 1)
                ed = QLineEdit(inner)
                ed.setProperty("role", "zoParam")
                ed.setFixedWidth(76)
                ed.setAlignment(Qt.AlignmentFlag.AlignRight)
                ed.setPlaceholderText("0")
                ed.setValidator(QDoubleValidator(0.0, 1e9, 2, ed))
                v = getattr(self._flags, key)
                ed.setText(_num_text(v) if v else "")
                ed.editingFinished.connect(lambda key=key, ed=ed: self._edit_done(key, _float(ed.text())))
                row.addWidget(ed)
                il.addLayout(row)
                self.min_edits[key] = ed
        for k, e in self._edits().items():
            e.setProperty("shown", e.text())
            text, was = drafts.get(k, (None, None))
            if text is not None and was == getattr(self._flags, k):
                e.setText(text)
        il.addStretch(1)
        # 옛 칸은 바로 지우지 않는다 — 이 그리기가 그 칸의 신호 안에서 불릴 수 있다.  신호를 막고 숨긴 뒤 나중에 지운다.
        old = self.side_scroll.takeWidget()
        if old is not None:
            for e in old.findChildren(QLineEdit):
                e.blockSignals(True)
            old.hide()
            old.deleteLater()
        self.side_scroll.setWidget(inner)
        bar.setValue(pos)
        if focus_key is not None and focus_key in self._edits():
            ed = self._edits()[focus_key]                 # 쓰던 칸에 커서를 돌려준다(Col 다음 Row 로 바로 넘어가게)
            ed.setFocus()
            ed.end(False)

    def _render_coord(self, inner: QWidget, il: QVBoxLayout) -> None:
        """LOT 목록 아래 '좌표' — 장비 화면 번호를 넣으면 그 die 의 Defect 만.  빈칸 = 안 거름(음수도 된다).
        아래 파라미터 칸과 같은 모양(이름 왼쪽 · 입력칸 오른쪽)으로 한 줄씩."""
        head = QLabel(_KO.ZO_COORD_HEAD, inner)
        head.setProperty("role", "ovGroupHead")
        head.setContentsMargins(8, 10, 0, 4)
        il.addWidget(head)
        self.coord_edits: dict = {}
        for key, text in (("col", _KO.ZO_COORD_COL), ("row", _KO.ZO_COORD_ROW)):
            line = QHBoxLayout()
            line.setContentsMargins(8, 1, 8, 1)
            lab = QLabel(text, inner)
            lab.setProperty("role", "ovRowName")
            line.addWidget(lab, 1)
            ed = QLineEdit(inner)
            ed.setProperty("role", "zoParam")
            ed.setFixedWidth(76)
            ed.setAlignment(Qt.AlignmentFlag.AlignRight)
            ed.setValidator(QIntValidator(-9999, 99999, ed))
            v = getattr(self._flags, key)
            ed.setText("" if v is None else str(v))
            ed.editingFinished.connect(lambda key=key, ed=ed: self._edit_done(key, _int_or_none(ed.text())))
            line.addWidget(ed)
            il.addLayout(line)
            self.coord_edits[key] = ed
        self.coord_warn = None
        if self._flags.col is not None or self._flags.row is not None:
            n = sum(1 for d in self._wafer_folders() if (w := self._data.get(d)) is not None and w.number_warn)
            if n:
                self.coord_warn = QLabel(_KO.ZO_COORD_WARN_FMT.format(n=n), inner)
                self.coord_warn.setProperty("role", "ovRowNote")
                self.coord_warn.setProperty("warn", "true")
                self.coord_warn.setWordWrap(True)
                self.coord_warn.setContentsMargins(8, 2, 8, 0)
                il.addWidget(self.coord_warn)

    def _render_lot(self, inner: QWidget, il: QVBoxLayout, lot: str, counts: dict) -> None:
        """LOT 한 줄(이름 · 수 · 끝의 ▾/▸) + 펼치면 그 LOT 웨이퍼 줄들(왼쪽 가는 선 안에 들여 씀)."""
        wafers = [wn for wn in self._order.get("wafer", [])
                  if (w := self.wafer_of(wn)) is not None and w.lot == lot]
        opened = self.lot_open(lot)
        row = _FacetRow(lot, counts["lot"].get(lot, 0),
                        lot in self._sel["lot"], "", inner, disc=opened)
        row.clicked.connect(lambda lot=lot: self.toggle("lot", lot))
        row.disc_clicked.connect(lambda lot=lot: self.toggle_lot(lot))
        il.addWidget(row)
        self.facet_rows[("lot", lot)] = row
        if not opened:
            return
        box = QFrame(inner)
        box.setProperty("role", "zoLotWafers")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(6, 0, 0, 2)
        bl.setSpacing(1)
        for wn in wafers:
            w = self.wafer_of(wn)
            n, on = counts["wafer"].get(wn, 0), wn in self._sel["wafer"]
            wr = _FacetRow(self.wafer_label(wn), n, on, _KO.ZO_FACET_CAP if w.cap else "", box)
            wr.clicked.connect(lambda wn=wn: self.toggle("wafer", wn))
            bl.addWidget(wr)
            self.facet_rows[("wafer", wn)] = wr
        il.addWidget(box)

    def _render_sel(self) -> None:
        _clear(self._sel_lay)
        chips = []
        for k, _t in B.FACETS:
            for v in self._order.get(k, []):
                if v in self._sel[k]:
                    chips.append((self.facet_label(k, v), lambda k=k, v=v: self.toggle(k, v)))
        f = self._flags
        for key, name in (("col", "Col"), ("row", "Row")):
            if getattr(f, key) is not None:
                chips.append((f"{name} = {getattr(f, key)}", lambda key=key: self.set_flag(key, None)))
        for key, name in (("min_area", "Area"), ("min_width", "Width"), ("min_length", "Length"),
                          ("min_contrast", "Contrast")):
            if getattr(f, key):
                chips.append((f"{name} ≥ {_num_text(getattr(f, key))}", lambda key=key: self.set_flag(key, 0.0)))
        for text, fn in chips:
            b = _nf(NeonButton(f"{text}  ✕", role="ovView"))
            b.setCheckable(True)
            b.setChecked(True)
            b.clicked.connect(lambda _c=False, fn=fn: fn())
            self._sel_lay.addWidget(b)
        if chips:
            c = _nf(NeonButton(_KO.ZO_CLEAR, role="textAction"))
            c.clicked.connect(self.clear_all)
            self._sel_lay.addWidget(c)
        self._sel_lay.addStretch(1)
        self.chips_host.setVisible(bool(chips))

    def _render_bar(self) -> None:
        n = len(self._passed)
        mono = theme.FONT_MONO.split(",")[0].strip('" ')
        shown = min(self._shown, n)
        tail = (" · " + _KO.ZO_SHOWN_FMT.format(shown=f"{shown:,}")) if self._view == "tiles" and n > shown else ""
        self.count_label.setText(f'<span style="font-family: {mono}; font-size: 16px; font-weight: 700;">{n:,}</span>'
                                 f'<span style="color: {theme.MUTE};"> {_KO.ZO_UNIT}{tail}</span>')
        for k, b in self.sort_btns.items():
            on = self._sort is not None and self._sort[0] == k
            name = next(nm for kk, nm, _d in SORTS if kk == k)
            b.setChecked(on)
            b.setText(f"{name} {'▼' if self._sort[1] else '▲'}" if on else name)
        for k, b in self.view_btns.items():
            b.setChecked(k == self._view)

    def _cols(self) -> int:
        avail = max(TILE_PX, self.grid_scroll.viewport().width() - 28)
        return max(1, (avail + TILE_GAP) // (TILE_PX + TILE_GAP))

    def _render_tiles(self, keep_scroll: bool) -> None:
        self.table.setVisible(False)
        self.table_note.setVisible(False)
        self.grid_scroll.setVisible(True)
        pos = self.grid_scroll.verticalScrollBar().value()
        _clear(self.grid)
        self._tiles = {}
        self._tile_rows: list = []
        self._wanted = set()
        if not self._passed:
            e = QLabel(_KO.ZO_NONE if self._roots else _KO.ZO_EMPTY, self.grid_host)
            e.setProperty("role", "muted")
            e.setAlignment(Qt.AlignmentFlag.AlignCenter)
            e.setContentsMargins(0, 48, 0, 0)
            self.grid.addWidget(e)
            self.grid.addStretch(1)
            self._tile_order = []
            return
        shown = self._passed[:self._shown]
        self._tile_order = []
        self._grp_total: dict = {}
        if self._sort is None:
            for it in self._passed:
                self._grp_total[it.first_name] = self._grp_total.get(it.first_name, 0) + 1
        self._grp_name = None                 # 마지막으로 그린 묶음 · 줄 — [더 보기] 가 그 뒤에 이어 붙인다
        self._grp_row = None
        self._grp_index = 0
        self._add_tiles(shown)
        self._add_tail()
        self._keep_pos = pos if keep_scroll else 0
        call_later(self, 0, self._restore_scroll)

    def _add_tiles(self, items: list) -> None:
        """타일을 격자 끝에 이어 붙인다(묶음 머리 · 마지막 줄의 빈자리를 이어서 채운다)."""
        cols = self._cols()
        self._last_cols = cols
        mono = theme.FONT_MONO.split(",")[0].strip('" ')
        dec = dict((k, d) for k, _n, d in SORTS)
        for it in items:
            gname = it.first_name if self._sort is None else ""
            if gname != self._grp_name:
                self._grp_name, self._grp_row = gname, None
                if gname:
                    head = QLabel(f'{html.escape(gname)}<span style="font-family: {mono}; font-weight: 400;'
                                  f' color: {theme.MUTE};">  {self._grp_total.get(gname, 0):,}</span>', self.grid_host)
                    head.setProperty("role", "ovGroupName")
                    head.setTextFormat(Qt.TextFormat.RichText)
                    head.setContentsMargins(0, 0 if self._grp_index == 0 else 18, 0, 6)
                    self.grid.addWidget(head)
                    self._grp_index += 1
            if self._grp_row is None or len(self._tile_rows[-1]) >= cols:
                row = QWidget(self.grid_host)
                row.setFixedHeight(TILE_PX)
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 0, 0, 0)
                rl.setSpacing(TILE_GAP)
                rl.addStretch(1)
                self.grid.addWidget(row)
                self._grp_row = (row, rl)
                self._tile_rows.append([])
            row, rl = self._grp_row
            k = item_key(it)
            tile = _Tile(it.photo, TILE_PX, row, tip=f"{it.first_name}\n{it.type_name}\n"
                                                     f"{self.wafer_label(it.wafer)}")
            if it.photo is None:
                tile.pic.setText(_KO.ZO_NO_PHOTO)
                tile.pic.setStyleSheet(f"color: {theme.MUTE};")
            elif tile.wait_path is not None:
                self._want(tile.wait_path, TILE_PX, "thumb")
            if self._sort is not None:
                tile.set_caption(fmt_num(getattr(it, self._sort[0]), dec.get(self._sort[0], 1)))
            tile.set_open(self._open == k)
            tile.clicked.connect(lambda k=k: self.open_item(k))
            tile.enlarge.connect(lambda k=k: (self.open_item(k), call_later(self, 0, self.enlarge_open)))
            rl.insertWidget(rl.count() - 1, tile)
            self._tiles[k] = tile
            self._tile_rows[-1].append(k)
            self._tile_order.append(k)

    def _add_tail(self) -> None:
        """격자 끝 — 남은 게 있으면 [더 보기] 와 늘어나는 빈칸.  :meth:`more` 가 떼고 다시 붙인다."""
        n0 = self.grid.count()
        if len(self._passed) > self._shown:
            more = _nf(NeonButton(_KO.ZO_MORE_FMT.format(n=f"{min(PAGE_TILES, len(self._passed) - self._shown):,}"),
                              role="ghost"))
            more.clicked.connect(self.more)
            self.grid.addSpacing(10)
            self.grid.addWidget(more, 0, Qt.AlignmentFlag.AlignLeft)
            self.more_btn = more
        self.grid.addStretch(1)
        self._tail_n = self.grid.count() - n0

    def _restore_scroll(self) -> None:
        self.grid_scroll.verticalScrollBar().setValue(self._keep_pos)

    def _render_table(self) -> None:
        self.grid_scroll.setVisible(False)
        self.table.setVisible(True)
        rows = self._passed[:TABLE_MAX]
        self._tile_order = [item_key(i) for i in rows]
        self._tile_rows = [[k] for k in self._tile_order]
        t = self.table
        t.blockSignals(True)
        t.setSortingEnabled(False)
        # 채우는 동안은 칸 폭을 맞추지 않는다 — '내용에 맞춤' 이 켜진 채 보이는 표에 칸을 넣으면 한 칸마다 전체를
        # 다시 재서 2천 줄이 몇 분 걸렸다(무작위 클릭 시험).  다 넣고 한 번만 맞춘다.
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        t.clearContents()
        t.setRowCount(len(rows))
        hdr = []
        for k, name, d in TABLE_COLS:
            on = self._sort is not None and self._sort[0] == k
            hdr.append(f"{name} {'▼' if self._sort[1] else '▲'}" if on else name)
        t.setHorizontalHeaderLabels(hdr)
        sel_row = -1
        for r, it in enumerate(rows):
            for c, (k, _n, d) in enumerate(TABLE_COLS):
                if k == "wafer":
                    text = self.wafer_label(it.wafer)
                elif k == "colrow":
                    text = _colrow(it)
                elif k == "photo":
                    text = "●" if it.photo is not None else ""
                elif d is not None:
                    text = fmt_num(getattr(it, k), d)
                else:
                    text = str(getattr(it, k))
                cell = QTableWidgetItem(text)
                if d is not None:
                    cell.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
                t.setItem(r, c, cell)
            if item_key(it) == self._open:
                sel_row = r
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        if sel_row >= 0:
            t.selectRow(sel_row)
        else:
            t.clearSelection()          # 고른 Defect 가 표 밖(2천 줄 뒤)이면 옛 줄을 골라 둔 채 두지 않는다
        t.blockSignals(False)
        over = len(self._passed) - len(rows)
        self.table_note.setText(_KO.ZO_TABLE_CAP_FMT.format(n=f"{TABLE_MAX:,}") if over > 0 else "")
        self.table_note.setVisible(over > 0)

    def _render_detail(self) -> None:
        it = self.current()
        show = it is not None
        for w in (self.detail_pics, self.kv_host, self.file_label, self.enlarge_btn, self.info_btn):
            w.setVisible(show)
        self.detail_empty.setVisible(not show)
        if it is None:
            return
        _clear(self.kv)
        w = self.wafer_of(it.wafer)
        rows = [(_KO.ZO_ROW_LOT, it.lot or "—"), (_KO.ZO_ROW_WAFER, (w.wafer_id if w else "") or "—"),
                (_KO.ZO_ROW_SLOT, (w.slot if w else "") or "—"), (_KO.ZO_ROW_COLROW, _colrow(it))]
        if w is not None and w.number_warn and it.col is not None:
            rows.append(("", _KO.ZO_ROW_NUMBER_WARN, "warn"))
        rows += [None, (_KO.ZO_ROW_RECIPE, it.recipe),
                (_KO.ZO_ROW_ZONE, it.zone), None, (_KO.ZO_ROW_CLASS, _class_name(it.first_name)),
                (_KO.ZO_ROW_CUR, _class_name(it.cur_name)),
                (_KO.ZO_ROW_TYPE, it.type_name), None]
        if it.kind == B.KIND_HEIGHT:
            rows.append(("Height", f"{fmt_num(it.height, 2)} µm"))
        else:
            rows += [("Area", f"{fmt_num(it.area, 1)} µm²"), ("Width", f"{fmt_num(it.width, 2)} µm"),
                     ("Length", f"{fmt_num(it.length, 2)} µm"), ("Contrast", fmt_num(it.contrast, 1))]
        r = 0
        for row in rows:
            if row is None:
                line = QFrame(self.kv_host)
                line.setFixedHeight(1)
                line.setStyleSheet(f"background: {theme.LINE2}; border: none;")
                self.kv.addWidget(line, r, 0, 1, 2)
            else:
                k = QLabel(row[0], self.kv_host)
                k.setProperty("role", "muted")
                v = QLabel(row[1], self.kv_host)
                v.setWordWrap(True)
                if row[0] in ("Area", "Width", "Length", "Contrast", "Height", _KO.ZO_ROW_COLROW):
                    v.setProperty("role", "mono")
                elif len(row) > 2:                              # 번호 경고 — 주황 작은 글씨
                    v.setProperty("role", "ovRowNote")
                    v.setProperty("warn", "true")
                self.kv.addWidget(k, r, 0, Qt.AlignmentFlag.AlignTop)
                self.kv.addWidget(v, r, 1)
            r += 1
        self.file_label.setText(it.photo.name if it.photo is not None else _KO.ZO_NO_PHOTO)
        self.enlarge_btn.setEnabled(it.photo is not None)
        self.info_btn.setEnabled(it.photo is not None)
        # 컬러 사진 — 캐시에 있으면 바로, 없으면 뒤에서
        self.color_pic.clear()
        if it.photo is None:
            self.color_pic.setText(_KO.ZO_NO_PHOTO)
        else:
            pm = image_io._get_tile_cache().get(_mid_key(it.photo, DETAIL_PIC))
            if pm is not None:
                self.color_pic.setPixmap(pm)
            else:
                self.color_pic.setText("…")
                self._want(it.photo, DETAIL_PIC, "mid")
        self._show_scan(it)

    def _show_scan(self, it: B.Item) -> None:
        k = item_key(it)
        self.scan_pic.clear()
        w = self._data.get(self._folder_of.get(it.wafer))
        if w is None or not w.has_scan or it.kind == B.KIND_HEIGHT:
            self.scan_pic.setText(_KO.ZO_DETAIL_SCAN_NONE)
            return
        if k in self._scans:
            crop = self._scans[k]
            if crop is None:
                self.scan_pic.setText(_KO.ZO_DETAIL_SCAN_NONE)
            else:
                self.scan_pic.setPixmap(QPixmap.fromImage(crop).scaled(
                    DETAIL_PIC, DETAIL_PIC, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            return
        self.scan_pic.setText(_KO.ZO_DETAIL_SCAN_WAIT)
        if k not in self._scan_threads:
            self._scan_timer.start()

    def _scan_current(self) -> None:
        it = self.current()
        w = self._data.get(self._folder_of.get(it.wafer)) if it is not None else None
        if w is not None and w.has_scan and it.kind != B.KIND_HEIGHT and item_key(it) not in self._scans:
            self._scan_thread(item_key(it), w.folder, (it.x, it.y))

    def _scan_thread(self, k: tuple, folder: Path, xy: tuple) -> "_ScanFind":
        """이 Defect 의 Scan 찾기 — 이미 찾는 중이면 그 스레드를 돌려준다(목록을 오가거나 크게보기를 눌러도 한 번만)."""
        th = self._scan_threads.get(k)
        if th is None:
            th = _ScanFind(k, folder, xy)
            th.signals.done.connect(self._on_scan)
            self._scan_threads[k] = th
            th.start()
        return th

    def _on_scan(self, k: tuple, crop) -> None:
        self._scan_threads.pop(k, None)
        self._scans[k] = crop
        if self._scan_viewer is not None and self._scan_viewer[0] == k:
            self._scan_viewer[1].set_scan_crop(crop)
        if k == self._open:
            it = self.current()
            if it is not None:
                self._show_scan(it)

    # ------------------------------------------------------------------ 썸네일
    def _want(self, photo, size: int, kind: str) -> None:
        key = (str(photo), int(size), kind)
        self._wanted.add(key)
        if key in self._pending:
            return
        self._pending.add(key)
        self._thumb_pool.submit(self._thumb_job, Path(photo), int(size), kind)

    def _thumb_job(self, path: Path, size: int, kind: str) -> None:
        """워커 스레드 — QImage 까지만(QPixmap 은 GUI 스레드에서)."""
        key = (str(path), size, kind)
        sent = skipped = False
        try:
            if key not in self._wanted:
                skipped = True
                return
            # 목록 썸네일은 칸(118px)만 채우면 된다 — Wafer map 툴팁용 저화질(120px · Q60) 캐시를 쓴다(현택님 2026-11-24).
            tp = image_io.get_mid_path(path) if kind == "mid" else image_io.get_map_thumb_path(path)
            img = QImage(str(tp))
            if img.isNull():
                return
            img = img.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self._thumb_bus.ready.emit(path, size, kind, img)
            sent = True
        except Exception:       # noqa: BLE001 — 한 장 실패는 자리표시로 남는다
            _LOG.debug("썸네일 실패: %s", path, exc_info=True)
        finally:
            if not sent:
                self._pending.discard(key)
                # 건너뛴 사이 새 그리기가 다시 원했으면(그때는 '이미 하는 중' 이라 안 맡겼다) 여기서 다시 맡긴다.
                if skipped and key in self._wanted:
                    self._want(path, size, kind)

    def _on_thumb(self, path, size: int, kind: str, img) -> None:
        self._pending.discard((str(path), size, kind))
        pm = QPixmap.fromImage(img)
        cache = image_io._get_tile_cache()
        if kind == "mid":
            cache.put(_mid_key(path, size), pm)
            it = self.current()
            if it is not None and it.photo is not None and str(it.photo) == str(path):
                self.color_pic.setPixmap(pm)
            return
        cache.put(_tile_key(path, size), pm)
        for t in self._tiles.values():
            if t.wait_path is not None and str(t.wait_path) == str(path):
                t.set_pixmap(pm)

    # ------------------------------------------------------------------ 조작
    def toggle(self, k: str, v) -> None:
        s = self._sel[k]
        s.discard(v) if v in s else s.add(v)
        # LOT 과 그 아래 웨이퍼는 한 묶음 — 다른 LOT 의 웨이퍼를 고르면 고른 LOT 이 그 웨이퍼를 가려 0 개가 됐다.
        if k == "wafer" and v in s:
            lot = getattr(self.wafer_of(v), "lot", None)
            self._sel["lot"] &= {lot}
        elif k == "lot" and v in s:
            self._sel["wafer"] = {n for n in self._sel["wafer"] if getattr(self.wafer_of(n), "lot", None) == v}
        self._shown = PAGE_TILES
        self._render()

    def set_flag(self, key: str, value) -> None:
        if getattr(self._flags, key) == value:
            return
        setattr(self._flags, key, value)
        if key == "good":
            self._refresh_order()
        self._shown = PAGE_TILES
        self._render()

    def toggle_params(self) -> None:
        """[파라미터 열기] — Area · Width · Length · Contrast 하한 칸을 펴고 접는다(기본 접힘)."""
        self._params_open = not self._params_open
        self._render(keep_scroll=True)

    def clear_all(self) -> None:
        for e in self._edits().values():        # 쓰다 만 글자도 지운다
            e.setProperty("shown", e.text())
        for s in self._sel.values():
            s.clear()
        self._flags = B.Flags()
        self._refresh_order()
        self._shown = PAGE_TILES
        self._render()

    def set_sort(self, key: str) -> None:
        """한 번 = 큰 순, 두 번 = 작은 순, 세 번 = 끄기(분류 이름별 묶음으로)."""
        if self._sort is None or self._sort[0] != key:
            self._sort = (key, True)
        elif self._sort[1]:
            self._sort = (key, False)
        else:
            self._sort = None
        self._render()

    def set_view(self, view: str) -> None:
        if view != self._view:
            self._view = view
            self._render()

    def more(self) -> None:
        """[더 보기] — 다음 묶음만 이어 붙인다(전부 다시 그리면 2천 장에서 누를 때마다 2초씩 걸렸다)."""
        if self._view != "tiles" or not self._tile_order:
            self._shown += PAGE_TILES
            self._render(keep_scroll=True)
            return
        old = self._shown
        self._shown += PAGE_TILES
        for _ in range(self._tail_n):                  # 끝의 간격 · [더 보기] · 빈칸을 뗀다(누른 버튼은 나중에 지운다)
            item = self.grid.takeAt(self.grid.count() - 1)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.deleteLater()
        self._add_tiles(self._passed[old:self._shown])
        self._add_tail()
        self._render_bar()

    def _on_table_header(self, col: int) -> None:
        k, _n, d = TABLE_COLS[col]
        if d is not None:
            self.set_sort(k)

    def _on_table_pick(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows and 0 <= rows[0].row() < len(self._tile_order):
            self.open_item(self._tile_order[rows[0].row()])

    def current(self) -> Optional[B.Item]:
        if self._open is None:
            return None
        return next((i for i in self._passed if item_key(i) == self._open), None)

    def open_item(self, k: tuple) -> None:
        old = self._open
        self._open = k
        if old in self._tiles:
            self._tiles[old].set_open(False)
        if k in self._tiles:
            self._tiles[k].set_open(True)
        if old != k:
            self._render_detail()

    def move_current(self, dx: int, dy: int) -> bool:
        rows = [r for r in self._tile_rows if r]
        if not rows:
            return False
        cur = self._open
        pos = next(((ri, ci) for ri, r in enumerate(rows) for ci, k in enumerate(r) if k == cur), None)
        if pos is None:
            new = rows[0][0]
        elif dx or self._view == "table":
            flat = [k for r in rows for k in r]
            j = flat.index(cur) + (1 if (dx or dy) > 0 else -1)
            if not 0 <= j < len(flat):
                return False
            new = flat[j]
        else:
            ri = pos[0] + (1 if dy > 0 else -1)
            if not 0 <= ri < len(rows):
                return False
            new = rows[ri][min(pos[1], len(rows[ri]) - 1)]
        if new == cur:
            return False
        if self._view == "table":
            self.table.selectRow(self._tile_order.index(new))
        else:
            self.open_item(new)
            if new in self._tiles:
                motion.ensure_visible_animated(self.grid_scroll, self.grid_host, self._tiles[new])
        return True

    def keyPressEvent(self, e):         # noqa: N802
        step = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1),
                Qt.Key.Key_Down: (0, 1)}.get(e.key())
        if step is not None and not isinstance(self.focusWidget(), QLineEdit):
            self.move_current(*step)
            e.accept()
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not isinstance(self.focusWidget(), QLineEdit):
            self.enlarge_open()
            e.accept()
            return
        super().keyPressEvent(e)

    # ------------------------------------------------------------------ 크게 · 사진 정보 · 엑셀
    def enlarge_open(self) -> None:
        it = self.current()
        if it is None or it.photo is None:
            return
        from .zoom_window import FullscreenViewer
        w = self._data.get(self._folder_of.get(it.wafer))
        scan = w is not None and w.has_scan and it.kind != B.KIND_HEIGHT
        viewer = FullscreenViewer(it.photo, self, scan_slot=scan)
        if scan:
            k = item_key(it)
            if k in self._scans:
                viewer.set_scan_crop(self._scans[k])
            else:
                self._scan_viewer = (k, viewer)     # 결과는 _on_scan 이 넘긴다(이미 끝난 스레드의 신호를 놓치지 않게)
                self._scan_thread(k, w.folder, (it.x, it.y))
        try:
            sheets.run(viewer, full_bleed=True)
        finally:
            self._scan_viewer = None
        viewer.deleteLater()            # 닫힌 뷰어가 이 창 아래 쌓이지 않게(sheets.run 은 닫힐 때까지 기다린다)

    def _enlarge_scan(self) -> None:
        it = self.current()
        crop = self._scans.get(item_key(it)) if it is not None else None
        if crop is None:
            return
        from .zoom_window import FullscreenViewer
        viewer = FullscreenViewer(Path(crop.text("src") or _KO.ZO_DETAIL_SCAN), self, image=crop)
        sheets.run(viewer, full_bleed=True)
        viewer.deleteLater()

    def open_info(self) -> None:
        it = self.current()
        if it is None or it.photo is None:
            return
        from .image_info_dialog import ImageInfoDialog
        sheets.run(ImageInfoDialog(self, image_path=str(it.photo)))

    def excel_picks(self) -> tuple:
        """지금 고른 Defect 중 사진 있는 것 → ``({slot명: [사진]}, {slot명: 카세트 슬롯})`` — Defect 추출과 같은 입력."""
        by_folder: dict = {}
        for it in self._passed:
            if it.photo is not None:
                by_folder.setdefault(self._folder_of[it.wafer], []).append(it.photo)
        # 이름은 폴더 순서로 붙인다 — 화면 정렬을 바꿔도 같은 웨이퍼가 같은 이름이 되게.
        live = self._wafer_folders()
        order = [f for f in live if f in by_folder] + [f for f in by_folder if f not in live]
        picked: dict = {}
        numbers: dict = {}
        for folder in order:
            name = folder.name if folder.name not in picked else f"{folder.parent.name}/{folder.name}"
            base, k = name, 2
            while name in picked:        # 부모 이름까지 같으면 번호를 붙인다 — 한 slot 에 섞이지 않게
                name, k = f"{base} ({k})", k + 1
            picked[name] = by_folder[folder]
            w = self._data.get(folder)
            if w is not None and w.slot:
                numbers[name] = w.slot
        return picked, numbers

    def save_excel(self) -> None:
        from ...models.result import extract_result
        from ...workers import exporter as ex
        picked, numbers = self.excel_picks()
        n = sum(len(v) for v in picked.values())
        if not n:
            self._show_notice(_KO.ZO_EXCEL_NONE)
            return
        layouts = [(ex.RECIPE_LAYOUT_SINGLE, _KO.EXTRACT_SAVE_SINGLE), (ex.RECIPE_LAYOUT_SHEETS, _KO.EXTRACT_SAVE_SHEETS),
                   (ex.RECIPE_LAYOUT_COLUMNS, _KO.EXTRACT_SAVE_COLUMNS)]
        choice = sheets.choose(self, _KO.ZO_BTN_EXCEL, _KO.ZO_EXCEL_BODY_FMT.format(n=f"{n:,}", w=len(picked)),
                               [(k, label, "primary" if k == ex.RECIPE_LAYOUT_SINGLE else "ghost") for k, label in layouts]
                               + [("cancel", _KO.MSG_BTN_CLOSE, "ghost")], default=ex.RECIPE_LAYOUT_SINGLE)
        if choice not in dict(layouts):
            return
        from .save_name_dialog import SaveNameDialog
        lot = self._roots[0].name if self._roots else "Defect"
        name = _KO.ZO_EXCEL_NAME_FMT.format(lot=lot, ts=datetime.now().strftime("%Y%m%d_%H%M"))
        dlg = SaveNameDialog(name, paths.results_dir(), parent=self)
        if not sheets.run(dlg) or dlg.chosen is None:
            return
        dst = paths.results_dir() / dlg.chosen
        template = paths.template_path()
        result = extract_result("", picked, slot_numbers=numbers)
        from .wafer_map_view import render_map_png
        exporter = ex.ExcelExporter(result, dst, template_path=template if template.exists() else None,
                                    map_renderer=render_map_png, recipe_layout=choice)
        self._exporter = exporter
        _LIVE.add(exporter)             # 창이 먼저 지워져도 저장 스레드가 끝날 때까지 산다
        exporter.finished.connect(lambda: _LIVE.discard(exporter))
        self._loading.show_overlay(_KO.LOAD_EXPORT)
        exporter.signals.progress.connect(self._on_excel_progress)
        exporter.signals.done.connect(self._on_excel_done)
        exporter.signals.failed.connect(self._on_excel_failed)
        exporter.start()

    def done(self, r: int) -> None:     # type: ignore[override]
        """창을 닫으면 도는 엑셀 저장도 멈춘다 — 반쯤 쓴 파일이 남거나 닫힌 창이 저장 끝 안내를 띄우지 않게."""
        self._stop_excel()
        super().done(r)

    def _stop_excel(self) -> None:
        ex = self._exporter
        if ex is None or not ex.isRunning():
            return
        for sig in (ex.signals.progress, ex.signals.done, ex.signals.failed):
            try:
                sig.disconnect()
            except TypeError:
                pass
        ex.stop()

    def _on_excel_progress(self, done: int, total: int, msg: str) -> None:
        self._loading.set_progress(done, total, msg or _KO.LOAD_EXPORT)

    def _on_excel_done(self, path: str) -> None:
        if not self._overlay_on:        # 저장 중에 폴더를 더 넣어 읽기 막이 떠 있으면 그 막은 읽기가 끝날 때 걷는다
            self._loading.hide_overlay()
        self._saved = Path(path)
        QTimer.singleShot(0, self._offer_open)

    def _offer_open(self) -> None:
        path = self._saved
        choice = sheets.choose(self, _KO.ZO_BTN_EXCEL, _KO.SAVE_SUCCESS_FMT.format(path=path),
                               [("open_file", _KO.BTN_OPEN_SAVED_FILE, "primary"),
                                ("open_dir", _KO.BTN_OPEN_SAVED_FOLDER, "ghost"),
                                ("close", _KO.MSG_BTN_CLOSE, "ghost")], default="open_file")
        if choice in ("open_file", "open_dir"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path if choice == "open_file" else path.parent)))

    def _on_excel_failed(self, msg: str) -> None:
        if not self._overlay_on:
            self._loading.hide_overlay()
        sheets.info(self, _KO.ZO_BTN_EXCEL, _KO.SAVE_FAIL_FMT.format(error=msg))

    # ------------------------------------------------------------------ 끌어다 놓기
    def hideEvent(self, event):         # noqa: N802
        drop_clear(self)
        super().hideEvent(event)

    @tolerate_teardown
    def eventFilter(self, obj, ev):     # noqa: N802
        if ev.type() == QEvent.Type.Resize and obj is self.grid_scroll.viewport() and self._view == "tiles" \
                and self._tile_order and self._last_cols != self._cols():
            self._relayout_timer.start()
        return super().eventFilter(obj, ev)

    def _relayout_now(self) -> None:
        self._render(keep_scroll=True)

    def dragEnterEvent(self, e):    # noqa: N802
        p = local_paths(e.mimeData())
        if not p:
            drop_clear(self)
            e.ignore()
            return
        ok = bool(guess_kinds(p) & {"folder", "image"})
        drop_feedback(self, e, None, _KO.DROP_PILL_ADD_FOLDER if ok else _KO.DROP_PILL_ONLY_FOLDER_PHOTO, ok)

    def dragMoveEvent(self, e):     # noqa: N802
        self.dragEnterEvent(e)

    def dragLeaveEvent(self, e):    # noqa: N802
        drop_clear(self)
        e.accept()

    def dropEvent(self, e):         # noqa: N802
        drop_clear(self)
        p = local_paths(e.mimeData())
        if p:
            e.acceptProposedAction()
            self._add_dropped(p)

    def _add_dropped(self, paths_) -> None:
        folders, images, _ = split_paths(paths_, is_dir=lambda p: Path(p).is_dir(),
                                         is_image=lambda p: CONFIG.is_image(Path(p).name))
        dirs = [Path(f) for f in folders]
        for im in images:
            if Path(im).parent not in dirs:
                dirs.append(Path(im).parent)
        if dirs:
            self.add_folders(dirs)
        else:
            self._show_notice(_KO.DROP_WRONG_KIND_FOLDER)

    def _on_paste_no_path(self, has_image: bool) -> None:
        self._show_notice(_KO.DROP_NO_PATH_IMAGE if has_image else _KO.DROP_NOTHING_TO_PASTE)



def _with_wafer(it: B.Item, n: int) -> B.Item:
    return it if it.wafer == n else replace(it, wafer=n)


def _class_name(name: str) -> str:
    """'29 · B R Surface Dark' → 'B R Surface Dark' — 값 칸의 첫 분류 · 재 분류(현택님 예시 그대로)."""
    return name.split(" · ", 1)[-1]


def _colrow(it: B.Item) -> str:
    return f"{it.col}, {it.row}" if it.col is not None and it.row is not None else "—"


def _int_or_none(text: str) -> Optional[int]:
    try:
        return int(text.strip())
    except ValueError:
        return None


def _nf(b):
    """버튼이 키보드 포커스를 가져가지 않게 — 누른 뒤 화살표가 버튼 사이를 옮겨 다녀 사진이 안 움직였다(Overkill 튜닝과 같게)."""
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return b


def _num_text(v: float) -> str:
    """하한 칸 · 칩 글자 — 칸이 받는 소수 2자리까지 그대로(``:g`` 는 6자리에서 잘라 12345.25 가 12345.2 로 바뀌었다)."""
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _float(text: str) -> float:
    try:
        return max(0.0, float(text))
    except ValueError:
        return 0.0


def _clear(lay) -> None:
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()                # 부모에서 떼지 않는다 — 떼면 잠깐 맨 위 창이 돼 입력칸의 포커스(Enter 전 적용)를 가져갔다
            w.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())
