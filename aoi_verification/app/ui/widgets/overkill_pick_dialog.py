"""Overkill 튜닝 — Defect 고르기 팝업.

후보 선별 화면(`pages/select_page.py`)과 같은 짜임이다: 왼쪽 Overkill · 가운데 지금 사진 ·
오른쪽 Defect, 아래에 **전체 사진** 띠.  사진 한 장씩 ← Overkill / Defect → 로 가르고,
끝나면 [적용] 으로 (picked, overkill) 두 dict 를 돌려준다.

순수 콜백만 받는다(`pixmap_of`·`preview`·`enlarge` …) — 부모가 무엇을 하든 이 위젯은 사진
로딩·추천 계산을 모른다.  그래서 가짜 콜백만으로 헤드리스 테스트된다.

키: ← Overkill · → Defect · Z / Ctrl+Z 되돌리기 · Enter 적용 · Esc 취소(자동 반복 끔).
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout,
                             QLabel, QMenu, QScrollArea, QSizePolicy, QSplitter,
                             QVBoxLayout, QWidget)

from ... import i18n
from .. import motion, theme
from ..deferred import call_later
from .neon_button import NeonButton

_OVER, _DEF = "over", "def"
_THUMB = 240           # 미리 불러 두는 '썸네일 화질' — 띠·패널 타일과 큰 사진의 즉시 표시를 이 한 장으로 한다
#                        (디스크 썸네일이 긴 변 240 이라 더 크게 부탁해도 얻는 게 없다 — 같은 파일을 한 번만 읽는다)
_STRIP_PX = 72         # 전체 사진 띠 타일 한 변
_STRIP_GAP = 4         # 띠 타일 간격
_STRIP_WIN = 40        # 띠는 지금 사진 앞뒤로 이만큼만 위젯으로 만든다(수천 장 구역에서 위젯 수만 개 → 창 전환이 수 초)
_PANEL_PX = 88         # 패널 타일 한 변
_PANEL_MAX = 120       # 패널이 위젯으로 보이는 최근 결정 수(개수 표시는 전체)
_BIG = 800             # 가운데 큰 사진 요청 크기(= 디스크 중간 이미지 긴 변, 더 키워도 소용없다)
_PRELOAD_MAX = 1500    # 열 때 한 번에 미리 불러 둘 사진 수(지금 사진에서 가까운 순) — 움직일 때마다 앞쪽을 더 채운다
_PREFETCH_BIG = 3      # 지금 사진 뒤로 큰 그림을 미리 읽어 둘 장 수
_PREVIEW_MS = 150      # 결과 미리보기(전체 Defect 훑기) 계산을 결정이 멎은 뒤로 미루는 시간


def _fit(pm: QPixmap, w: int, h: int, dpr: float) -> QPixmap:
    """w×h(논리 px) 안에 비율 유지로 맞춘다."""
    out = pm.scaled(int(w * dpr), int(h * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
    out.setDevicePixelRatio(dpr)
    return out


class _Tile(QFrame):
    """사진 한 칸 — 사진 · (현재면) 링 · (결정됐으면) 아래 막대.  클릭 = 이 사진으로 이동."""

    clicked = pyqtSignal(object)
    context_requested = pyqtSignal(object, QPoint)

    def __init__(self, d, px: int, tip: str, parent=None) -> None:
        super().__init__(parent)
        self.d = d
        self._px = px
        self._has_pix = False
        self.setFixedSize(px, px)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tip)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_menu)
        self.pic = QLabel(self)
        self.pic.setProperty("role", "tilePic")
        self.pic.setGeometry(0, 0, px, px)
        self.pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bar = QFrame(self)
        self.bar.setGeometry(0, px - 5, px, 5)
        self.bar.hide()
        self.ring = QFrame(self)
        self.ring.setProperty("role", "tileRing")
        self.ring.setGeometry(0, 0, px, px)
        self.ring.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.ring.hide()

    @property
    def has_pix(self) -> bool:
        return self._has_pix

    def set_pixmap(self, pm: Optional[QPixmap]) -> None:
        if pm is None or pm.isNull():
            return
        self._has_pix = True
        self.pic.setPixmap(_fit(pm, self._px, self._px, self.devicePixelRatioF()))

    def set_state(self, state: Optional[str]) -> None:
        self.state = state
        if state is None:
            self.bar.hide()
            return
        self.bar.setProperty("role", "pickBarOver" if state == _OVER else "pickBarDef")
        self.bar.style().unpolish(self.bar)
        self.bar.style().polish(self.bar)
        self.bar.show()

    def set_current(self, on: bool) -> None:
        self.ring.setVisible(on)

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.d)
        super().mousePressEvent(e)

    def _on_menu(self, pos) -> None:
        self.context_requested.emit(self.d, self.mapToGlobal(pos))


class _BigPhoto(QLabel):
    """가운데 큰 사진 — 비율 유지로 자리를 채운다.  우클릭은 신호로만 알린다."""

    context_requested = pyqtSignal(QPoint)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("role", "tilePic")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(240, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._pm: Optional[QPixmap] = None
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.context_requested.emit(self.mapToGlobal(pos)))

    def has_photo(self) -> bool:
        return self._pm is not None

    def set_photo(self, pm: Optional[QPixmap], placeholder: str = "") -> None:
        if pm is None or pm.isNull():
            self._pm = None
            self.clear()
            self.setText(placeholder)
            return
        self._pm = pm
        self.setText("")
        self._rescale()

    def _rescale(self) -> None:
        if self._pm is not None:
            self.setPixmap(_fit(self._pm, max(1, self.width() - 4),
                                max(1, self.height() - 4), self.devicePixelRatioF()))

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._rescale()


class _Panel(QFrame):
    """왼쪽(Overkill)/오른쪽(Defect) 패널 — 결정한 사진 타일을 최근 것이 위로 쌓는다.

    위젯으로 만드는 것은 최근 ``_PANEL_MAX`` 장뿐이다(개수·결과는 전체).  수천 장을 고르면 타일 위젯이
    수천 개가 되어 팝업을 닫는(재부모화) 데만 수 초가 걸렸다."""

    def __init__(self, name: str, make_tile: Callable, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("role", "section")
        self._name = name
        self._make_tile = make_tile
        self._tiles: dict = {}          # key -> _Tile (보이는 것만)
        self._data: dict = {}           # key -> d (전부)
        self._order: list = []          # 오래된 것 → 최근 것
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)
        self.title = QLabel(self)
        self.title.setProperty("role", "paneTitle")
        v.addWidget(self.title)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        self._grid = QGridLayout(host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(6)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(host)
        v.addWidget(self.scroll, 1)
        self._set_count()

    def _set_count(self) -> None:
        self.title.setText(i18n.KO.OVERKILL_PICK_PANE_FMT.format(
            name=self._name, n=len(self._order)))

    def count(self) -> int:
        return len(self._order)

    def tile(self, key) -> Optional[_Tile]:
        return self._tiles.get(key)

    def _drop_tile(self, key) -> None:
        t = self._tiles.pop(key, None)
        if t is not None:
            t.hide()
            t.setParent(None)
            t.deleteLater()

    def _sync(self) -> None:
        """보여야 할 것(최근 ``_PANEL_MAX``)만 위젯으로 둔다."""
        want = self._order[-_PANEL_MAX:]
        keep = set(want)
        for key in [k for k in self._tiles if k not in keep]:
            self._drop_tile(key)
        for key in want:
            if key not in self._tiles:
                self._tiles[key] = self._make_tile(self._data[key])
        self._relayout()
        self._set_count()

    def add(self, key, d) -> Optional[_Tile]:
        self.remove(key, sync=False)
        self._order.append(key)
        self._data[key] = d
        self._sync()
        return self._tiles.get(key)

    def bulk(self, pairs) -> None:
        """처음 채울 때 — 한 번만 배치한다."""
        for key, d in pairs:
            if key in self._data:
                self._order.remove(key)
            self._order.append(key)
            self._data[key] = d
        self._sync()

    def remove(self, key, *, sync: bool = True) -> None:
        if key not in self._data:
            return
        self._order.remove(key)
        del self._data[key]
        self._drop_tile(key)
        if sync:
            self._sync()

    def _cols(self) -> int:
        w = self.scroll.viewport().width()
        return max(1, (w + 6) // (_PANEL_PX + 6))

    def _relayout(self) -> None:
        cols = self._cols()
        shown = [k for k in reversed(self._order) if k in self._tiles]
        for i, key in enumerate(shown):
            self._grid.addWidget(self._tiles[key], i // cols, i % cols)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._relayout()

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        self._relayout()


class OverkillPickDialog(QDialog):
    """Defect 고르기 팝업.  `sheet_host.run(dlg)` 로 띄운다."""

    applied = pyqtSignal(dict, dict)       # (picked, overkill)

    def __init__(self, *, zones: list, zone, items_of: Callable, key_of: Callable,
                 pixmap_of: Callable, request_pics: Callable, caption_of: Callable,
                 picked: dict, overkill: dict, preview: Callable,
                 enlarge: Callable, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.OVERKILL_PICK_TITLE)
        self.setMinimumSize(900, 600)
        self._zones = list(zones)
        self._items_of, self._key_of = items_of, key_of
        self._pixmap_of, self._request_pics = pixmap_of, request_pics
        self._caption_of, self._preview, self._enlarge = caption_of, preview, enlarge
        self._picked: dict = dict(picked)
        self._over: dict = dict(overkill)
        self._history: list = []          # (key, d, 이전 상태, zone_key)
        self._zone = zone
        self._items: list = []
        self._strip: dict = {}            # 전체 사진 띠(현재 구역) — 인덱스 → _Tile, 지금 사진 앞뒤 창만
        self._lo = self._hi = 0           # 띠에 위젯이 있는 구역 인덱스 범위 [lo, hi)
        self._big_final = False           # 큰 사진이 최종 화질(중간 이미지)인가 — 아니면 썸네일 화질로 먼저 보인 것
        self._cur = -1
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(_PREVIEW_MS)
        self._preview_timer.timeout.connect(self._update_preview)
        # 사진 → 구역: 다른 구역의 결정도 패널에 보이고, 눌렀을 때 그 구역으로 간다.
        self._zone_of: dict = {}
        self._zone_items: dict = {}
        for zk, _ in self._zones:
            lst = list(items_of(zk))
            self._zone_items[zk] = lst
            for d in lst:
                self._zone_of[key_of(d)] = zk
        self._build()
        self._load_zone(zone, first=True)
        self.left.bulk(self._over.items())
        self.right.bulk(self._picked.items())
        self._refresh_panel_pics()
        self._refresh_all()
        self._update_preview()

    # ------------------------------------------------------------ 구성
    def _build(self) -> None:
        K = i18n.KO
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        top = QHBoxLayout()
        self.combo = QComboBox(self)
        self.combo.setToolTip(K.OVERKILL_PICK_ZONE_TIP)
        self.combo.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        for zk, label in self._zones:
            self.combo.addItem(label, zk)
        idx = max(0, self.combo.findData(self._zone))
        self.combo.setCurrentIndex(idx)
        self.combo.activated.connect(self._on_zone_picked)
        top.addWidget(self.combo)
        self.counts = QLabel(self)
        self.counts.setProperty("role", "muted")
        top.addWidget(self.counts)
        top.addStretch(1)
        self.btn_cancel = NeonButton(K.OVERKILL_PICK_BTN_CANCEL, role="ghost")
        self.btn_cancel.clicked.connect(self.reject)
        top.addWidget(self.btn_cancel)
        self.btn_apply = NeonButton("", role="primary")
        self.btn_apply.clicked.connect(self._apply)
        top.addWidget(self.btn_apply)
        root.addLayout(top)

        self.left = _Panel(K.OVERKILL_PICK_PANE_OVERKILL, self._make_panel_tile)
        self.right = _Panel(K.OVERKILL_PICK_PANE_DEFECT, self._make_panel_tile)

        center = QWidget(self)
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(8)
        self.big = _BigPhoto(center)
        self.big.context_requested.connect(self._on_big_menu)
        cv.addWidget(self.big, 1)
        self.caption = QLabel(center)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setProperty("role", "muted")
        cv.addWidget(self.caption)
        self.done_label = QLabel(K.OVERKILL_PICK_DONE, center)
        self.done_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.done_label.setProperty("role", "muted")
        self.done_label.setVisible(False)
        cv.addWidget(self.done_label)
        btns = QHBoxLayout()
        self.btn_over = NeonButton(K.OVERKILL_PICK_BTN_OVERKILL, role="warn")
        self.btn_over.clicked.connect(self._decide_over)
        self.btn_def = NeonButton(K.OVERKILL_PICK_BTN_DEFECT, role="primary")
        self.btn_def.clicked.connect(self._decide_def)
        self.btn_undo = NeonButton(K.OVERKILL_PICK_BTN_UNDO, role="ghost")
        self.btn_undo.clicked.connect(self._undo)
        for b in (self.btn_over, self.btn_def, self.btn_undo):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for b in (self.btn_cancel, self.btn_apply):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btns.addWidget(self.btn_over, 1)
        btns.addWidget(self.btn_undo)
        btns.addWidget(self.btn_def, 1)
        cv.addLayout(btns)

        strip_title = QLabel(K.OVERKILL_PICK_ALL_PHOTOS, center)
        strip_title.setProperty("role", "paneTitle")
        cv.addWidget(strip_title)
        self.strip_scroll = QScrollArea(center)
        self.strip_scroll.setWidgetResizable(True)
        self.strip_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.strip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.strip_scroll.setFixedHeight(_STRIP_PX + 22)
        self.strip_host = QWidget()
        self.strip_lay = QHBoxLayout(self.strip_host)
        self.strip_lay.setContentsMargins(0, 0, 0, 0)
        self.strip_lay.setSpacing(_STRIP_GAP)
        self.strip_scroll.setWidget(self.strip_host)
        cv.addWidget(self.strip_scroll)

        split = QSplitter(Qt.Orientation.Horizontal, self)
        split.addWidget(self.left)
        split.addWidget(center)
        split.addWidget(self.right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 4)
        split.setStretchFactor(2, 1)
        split.setSizes([300, 700, 300])
        split.setChildrenCollapsible(False)
        root.addWidget(split, 1)

        foot = QHBoxLayout()
        # ★ 이름이 `result` 면 `QDialog.result()` 를 가린다 — 시트가 닫힐 때 `widget.result()` 가
        #   TypeError 를 던지고, 슬롯 안의 처리 안 된 예외는 PyQt6 가 앱을 조용히 끝낸다.
        self.result_label = QLabel(self)
        self.result_label.setProperty("role", "muted")
        foot.addWidget(self.result_label)
        self.missed = QLabel(self)
        self.missed.setProperty("role", "pickMissed")
        foot.addWidget(self.missed)
        foot.addStretch(1)
        root.addLayout(foot)

        for key, fn in (("Right", self._decide_def), ("Left", self._decide_over),
                        ("Z", self._undo)):
            sc = QShortcut(QKeySequence(key), self, activated=fn)
            sc.setAutoRepeat(False)
        for key, step in (("Up", -1), ("Down", 1)):         # ← → 는 결정이라, 결정 없이 사진만 넘겨 볼 때는 ↑ ↓
            QShortcut(QKeySequence(key), self, activated=lambda s=step: self._browse(s))
        sc = QShortcut(QKeySequence.StandardKey.Undo, self, activated=self._undo)
        sc.setAutoRepeat(False)
        for key in ("Return", "Enter"):
            sc = QShortcut(QKeySequence(key), self, activated=self._on_enter)
            sc.setAutoRepeat(False)

    def _make_panel_tile(self, d) -> _Tile:
        return self._wire(_Tile(d, _PANEL_PX, self._caption_of(d)))

    def _wire(self, t: _Tile) -> _Tile:
        t.clicked.connect(self._goto)
        t.context_requested.connect(self._on_tile_menu)
        t.set_state(None)
        pm = self._pixmap_of(t.d, _THUMB)
        if pm is not None:
            t.set_pixmap(pm)
        return t

    # ------------------------------------------------------------ 띠(창만 위젯으로)
    def _tile_at(self, i: int) -> Optional[_Tile]:
        return self._strip.get(i)

    def _clear_strip(self) -> None:
        while self.strip_lay.count():
            it = self.strip_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._strip = {}
        self._lo = self._hi = 0

    def _build_strip(self, center: int) -> None:
        """center 앞뒤 ``_STRIP_WIN`` 장만 위젯으로 만들고, 앞뒤 빈 자리는 스페이서로 메운다(스크롤 길이는 그대로)."""
        self._clear_strip()
        n = len(self._items)
        lo, hi = max(0, center - _STRIP_WIN), min(n, center + _STRIP_WIN + 1)
        self._lo, self._hi = lo, hi
        step = _STRIP_PX + _STRIP_GAP
        self._pad_l = QWidget()
        self._pad_l.setFixedSize(lo * step, 1)
        self.strip_lay.addWidget(self._pad_l)
        for i in range(lo, hi):
            self.strip_lay.addWidget(self._make_strip_tile(i))
        self._pad_r = QWidget()
        self._pad_r.setFixedSize((n - hi) * step, 1)
        self.strip_lay.addWidget(self._pad_r)
        self.strip_lay.addStretch(1)

    def _make_strip_tile(self, i: int) -> _Tile:
        d = self._items[i]
        t = self._wire(_Tile(d, _STRIP_PX, self._caption_of(d)))
        t.set_state(self._state(self._key_of(d)))
        self._strip[i] = t
        return t

    def _ensure_strip(self, i: int) -> None:
        """i 가 창 가장자리에 다가오면 그쪽으로만 타일을 덧붙인다(전체를 다시 만들지 않는다) — 멀리 건너뛰면 새로 만든다."""
        n = len(self._items)
        margin = _STRIP_WIN // 3
        if not self._strip or i < self._lo - _STRIP_WIN or i >= self._hi + _STRIP_WIN:
            self._build_strip(i)
            return
        step = _STRIP_PX + _STRIP_GAP
        if i < self._lo + margin and self._lo > 0:
            new_lo = max(0, i - _STRIP_WIN)
            for j in range(self._lo - 1, new_lo - 1, -1):
                self.strip_lay.insertWidget(1, self._make_strip_tile(j))
            self._lo = new_lo
            self._pad_l.setFixedSize(new_lo * step, 1)
        if i >= self._hi - margin and self._hi < n:
            new_hi = min(n, i + _STRIP_WIN + 1)
            at = 1 + (self._hi - self._lo)
            for j in range(self._hi, new_hi):
                self.strip_lay.insertWidget(at + (j - self._hi), self._make_strip_tile(j))
            self._hi = new_hi
            self._pad_r.setFixedSize((n - new_hi) * step, 1)
        self._trim_strip(i)

    def _trim_strip(self, i: int) -> None:
        """지금 사진에서 멀어진 쪽 타일은 걷어 낸다 — 걸어 다니기만 해도 위젯이 계속 늘던 것."""
        step = _STRIP_PX + _STRIP_GAP
        keep = 2 * _STRIP_WIN
        n = len(self._items)
        drop_lo = [j for j in range(self._lo, min(self._hi, i - keep))]
        for j in drop_lo:
            self._drop_strip_tile(j)
        if drop_lo:
            self._lo = drop_lo[-1] + 1
            self._pad_l.setFixedSize(self._lo * step, 1)
        drop_hi = [j for j in range(max(self._lo, i + keep + 1), self._hi)]
        for j in drop_hi:
            self._drop_strip_tile(j)
        if drop_hi:
            self._hi = drop_hi[0]
            self._pad_r.setFixedSize((n - self._hi) * step, 1)

    def _drop_strip_tile(self, j: int) -> None:
        t = self._strip.pop(j, None)
        if t is not None:
            self.strip_lay.removeWidget(t)
            t.hide()
            t.setParent(None)
            t.deleteLater()

    # ------------------------------------------------------------ 상태
    def _state(self, key) -> Optional[str]:
        if key in self._over:
            return _OVER
        if key in self._picked:
            return _DEF
        return None

    def _cur_item(self):
        return self._items[self._cur] if 0 <= self._cur < len(self._items) else None

    def picked(self) -> dict:
        return dict(self._picked)

    def overkill(self) -> dict:
        return dict(self._over)

    def current_key(self):
        d = self._cur_item()
        return None if d is None else self._key_of(d)

    # ------------------------------------------------------------ 구역
    def _on_zone_picked(self, _i: int) -> None:
        self._load_zone(self.combo.currentData())
        self.setFocus()                   # 콤보가 방향키를 가져가지 않게
        self._refresh_all()

    def _load_zone(self, zone, *, first: bool = False) -> None:
        self._zone = zone
        if not first and self.combo.currentData() != zone:
            self.combo.setCurrentIndex(self.combo.findData(zone))
        self._items = self._zone_items.get(zone, [])
        self._clear_strip()
        self._cur = -1
        self._set_cur(self._next_undecided(0, include_start=True))
        if self._cur < 0 and self._items:
            self._set_cur(0)
        self._preload_zone()

    def _next_undecided(self, start: int, *, include_start: bool = False) -> int:
        n = len(self._items)
        for step in range(0 if include_start else 1, n + (0 if include_start else 1)):
            i = (start + step) % n
            if self._state(self._key_of(self._items[i])) is None:
                return i
        return -1

    # ------------------------------------------------------------ 현재 사진
    def _set_cur(self, i: int, *, animate: bool = False) -> None:
        old = self._tile_at(self._cur)
        if old is not None:
            old.set_current(False)
        self._cur = i
        d = self._cur_item()
        if d is None:
            self.big.set_photo(None, i18n.KO.OVERKILL_PICK_EMPTY)
            self.caption.setText("")
            return
        self._ensure_strip(i)
        self._tile_at(i).set_current(True)
        self._show_big(d)
        if animate:
            motion.fade_in(self.big)
        self.caption.setText(self._caption_of(d))
        self.caption.setToolTip(self._caption_of(d))
        self._prefetch(i)
        call_later(self, 0, self._scroll_strip)

    def _show_big(self, d) -> None:
        """다음 사진은 **바로** 보인다 — 중간 이미지가 아직이면 이미 불러 둔 썸네일 화질(없으면 띠 타일의 그림)로 먼저 채우고,
        중간 이미지가 오면 `refresh_pics` 가 바꿔 끼운다."""
        pm = self._pixmap_of(d, _BIG)
        self._big_final = pm is not None
        if pm is None:
            pm = self._pixmap_of(d, _THUMB)
            self._request_pics([d], _BIG)
        if pm is None:
            self.big.set_photo(None, i18n.KO.OVERKILL_PICK_LOADING)
        else:
            self.big.set_photo(pm)

    def _prefetch(self, i: int) -> None:
        """지금 사진 앞쪽의 썸네일 · 큰 그림을 미리 줄 세운다(이미 있거나 줄 선 것은 부모가 거른다)."""
        n = len(self._items)
        if n <= 1:
            return
        ahead = [self._items[(i + k) % n] for k in range(1, _PREFETCH_BIG + 1) if n > k]
        back = self._items[i - 1] if i > 0 else None
        self._request_pics(ahead + ([back] if back is not None else []), _BIG)
        near = [self._items[j] for j in range(max(0, i - 5), min(n, i + _STRIP_WIN))]
        self._request_pics([x for x in near if self._pixmap_of(x, _THUMB) is None], _THUMB)

    def _preload_zone(self) -> None:
        """구역 사진 전부(지금 사진에서 가까운 순, ``_PRELOAD_MAX`` 장까지)를 썸네일 화질로 미리 부탁한다."""
        n = len(self._items)
        if n == 0:
            return
        c = max(self._cur, 0)
        order = sorted(range(n), key=lambda j: (abs(j - c), j))[:_PRELOAD_MAX]
        self._request_pics([self._items[j] for j in order], _THUMB)

    def _scroll_strip(self) -> None:
        if not (0 <= self._cur < len(self._items)):
            return
        bar = self.strip_scroll.horizontalScrollBar()
        x = self._cur * (_STRIP_PX + _STRIP_GAP)
        motion.animate_scroll(bar, x - (self.strip_scroll.viewport().width() - _STRIP_PX) // 2)

    def _browse(self, step: int) -> None:
        """↑ ↓ — 결정하지 않고 앞 · 뒤 사진으로 간다(맨 끝에서는 멈춤).  띠는 그 사진이 보이게 따라 간다."""
        i = self._cur + step
        if self._combo_open() or not 0 <= i < len(self._items):
            return
        self._set_cur(i)
        self._refresh_all()

    def _combo_open(self) -> bool:
        return self.combo.hasFocus()

    def _goto(self, d) -> None:
        key = self._key_of(d)
        zk = self._zone_of.get(key, self._zone)
        if zk != self._zone:
            self._load_zone(zk)
        for i, it in enumerate(self._items):
            if self._key_of(it) == key:
                self._set_cur(i)
                break
        self._refresh_all()

    # ------------------------------------------------------------ 결정
    def _decide_over(self) -> None:
        self._decide(_OVER)

    def _decide_def(self) -> None:
        self._decide(_DEF)

    def _decide(self, side: str) -> None:
        d = self._cur_item()
        if d is None:
            return
        key = self._key_of(d)
        prev = self._state(key)
        if prev == side:
            self._advance()
            return
        self._history.append((key, d, prev, self._zone))
        self._put(key, d, side)
        self._tile_at(self._cur).set_state(side)
        panel = self.left if side == _OVER else self.right
        motion.flash_outline(panel, theme.WARN if side == _OVER else theme.ACCENT)
        self._advance()

    def _put(self, key, d, state: Optional[str]) -> None:
        """key 를 state 로 옮긴다(패널 타일 포함).  None = 미결정."""
        self._over.pop(key, None)
        self._picked.pop(key, None)
        self.left.remove(key)
        self.right.remove(key)
        if state == _OVER:
            self._over[key] = d
            self.left.add(key, d)
        elif state == _DEF:
            self._picked[key] = d
            self.right.add(key, d)
        self._zone_of.setdefault(key, self._zone)

    def _advance(self) -> None:
        nxt = self._next_undecided(self._cur)
        if nxt >= 0:
            self._set_cur(nxt)               # 결정하면 다음 사진은 애니메이션 없이 바로 바뀐다(Z 되돌리기만 페이드인)
        self._refresh_all()

    def _undo(self) -> None:
        if not self._history:
            return
        key, d, prev, zk = self._history.pop()
        if zk != self._zone:
            self._load_zone(zk)
        self._put(key, d, prev)
        for i, it in enumerate(self._items):
            if self._key_of(it) == key:
                self._set_cur(i, animate=True)
                self._tile_at(i).set_state(prev)
                break
        self._refresh_all()

    # ------------------------------------------------------------ 표시 갱신
    def _refresh_all(self) -> None:
        K = i18n.KO
        left = sum(1 for d in self._items if self._state(self._key_of(d)) is None)
        self.counts.setText(K.OVERKILL_PICK_COUNTS_FMT.format(
            total=len(self._items), left=left))
        self.done_label.setVisible(bool(self._items) and left == 0)
        self.btn_apply.setText(K.OVERKILL_PICK_BTN_APPLY_FMT.format(n=len(self._picked)))
        self.btn_undo.setEnabled(bool(self._history))
        self._preview_timer.start()      # 전체 Defect 를 훑는 미리보기는 결정이 멎은 뒤 한 번만

    def _update_preview(self) -> None:
        K = i18n.KO
        self._preview_timer.stop()
        new_drop, gone, missed = self._preview(list(self._picked.values()),
                                               list(self._over.values()))
        self.result_label.setText(K.OVERKILL_PICK_RESULT_FMT.format(
            drop=new_drop, n=len(self._over), gone=gone))
        self.missed.setText(K.OVERKILL_PICK_MISSED_FMT.format(k=missed) if missed > 0 else "")

    def flush_preview(self) -> None:
        """미루던 미리보기가 있으면 지금 계산한다(테스트 · 적용 직전)."""
        if self._preview_timer.isActive():
            self._update_preview()

    def refresh_pics(self) -> None:
        """부모가 새 사진을 읽어 왔을 때 — 아직 비어 있는 칸을 채운다."""
        d = self._cur_item()
        if d is not None and not (self.big.has_photo() and self._big_final):
            self._show_big(d)             # 썸네일 화질로 떠 있던 것을 중간 이미지로 바꾼다
        for t in self._strip.values():
            if not t.has_pix:
                t.set_pixmap(self._pixmap_of(t.d, _THUMB))
        self._refresh_panel_pics()

    def _refresh_panel_pics(self) -> None:
        for panel in (self.left, self.right):
            for t in panel._tiles.values():
                if not t.has_pix:
                    t.set_pixmap(self._pixmap_of(t.d, _THUMB))
        # 아직 없는 그림은 한 번에 요청한다.
        want = [t.d for t in self._strip.values() if not t.has_pix]
        want += [t.d for p in (self.left, self.right) for t in p._tiles.values()
                 if not t.has_pix]
        if want:
            self._request_pics(want, _THUMB)

    # ------------------------------------------------------------ 우클릭 · 키
    def _view_menu(self, d, gpos: QPoint) -> None:
        menu = QMenu(self)
        act = menu.addAction(i18n.KO.CTX_VIEW_LARGER)
        if menu.exec(gpos) is act:
            self._enlarge(d)

    def _on_tile_menu(self, d, gpos: QPoint) -> None:
        self._view_menu(d, gpos)

    def _on_big_menu(self, gpos: QPoint) -> None:
        d = self._cur_item()
        if d is not None:
            self._view_menu(d, gpos)

    def _on_enter(self) -> None:
        if self.combo.hasFocus():
            return
        self._apply()

    def _apply(self) -> None:
        self._preview_timer.stop()
        self.applied.emit(dict(self._picked), dict(self._over))
        self.accept()
