"""사이드 패널의 ‘선택 모드’ 를 위한 다중 선택 다이얼로그.

기존 inline 체크박스가 사진을 가리는 문제를 해결하기 위해 별도 큰 팝업 창에서
여러 사진을 클릭/드래그로 선택 / 해제하고 액션을 실행한다.  하단의 액션 버튼들은
사이드 패널의 actions 메뉴와 1:1 대응.

대량 표시 대응:
- 총 표시 수가 ``_PAGINATE_THRESHOLD`` (1000) 이상이면 ``_PAGE_SIZE`` (200) 장씩
  페이지로 나눠 한 번에 한 페이지만 렌더한다.  선택 상태는 key 기반이라 페이지를
  넘겨도 유지된다.
- 상단에 사진 크기 슬라이더를 두어 타일 크기를 즉시 조절.
- 타일 우클릭 시 풀스크린 뷰어로 크게 본다.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QDialog, QFrame, QGridLayout,
                             QHBoxLayout, QLabel, QRubberBand, QScrollArea,
                             QSizePolicy, QSlider, QVBoxLayout, QWidget)

from ... import config, i18n
from ...models.slot import ImageItem
from ...utils import image_io
from .neon_button import NeonButton
from .window_controls import add_fullscreen_shortcut, enable_window_controls


_TILE_PX = config.Sizing.BULK_TILE_PX   # 다중 선택 그리드 기본 타일 (= 180)
_CAP_PX = 28            # 파일명 한 줄 — 사진을 가리지 않도록 충분히 확보
# 가로 최대 5 컬럼 + 6 번째부터 다음 행으로 wrap (사용자 요청 — 가로 스크롤
# 발생하지 않도록).  좁은 창에선 viewport 폭 기반으로 더 적게 동적 계산.
_COLS = 5
# 슬라이더로 조절 가능한 타일 크기 범위.
_TILE_MIN = 120
_TILE_MAX = 320
# 대량 표시 페이지네이션.
_PAGINATE_THRESHOLD = 1000
_PAGE_SIZE = 200


class _SelectTile(QFrame):
    """클릭 토글 가능한 큰 썸네일. 선택 시 네온 사이언 보더로 강조.

    좌클릭 = 선택 토글, 우클릭 = 풀스크린 확대 뷰.
    """

    toggled = pyqtSignal(object, bool)        # (ImageItem, selected)
    zoom_requested = pyqtSignal(object)       # ImageItem

    def __init__(self, item: ImageItem, *, tile_px: int = _TILE_PX,
                 parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self._tile_px = int(tile_px)
        self._selected = False
        # objectName 스코프 셀렉터로 테두리가 내부 라벨까지 번지지 않게 한다.
        self.setObjectName("selTile")
        self.setProperty("role", "card-soft")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # 사진 정사각 영역(tile_px) + 캡션 한 줄(_CAP_PX) + 마진/스페이싱.
        self.setFixedSize(self._tile_px + 14, self._tile_px + _CAP_PX + 18)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        # 이미지 영역 — 정사각 박스에 KeepAspectRatio 로 들어가므로 가로/세로
        # 사진 모두 잘림 없이 원본 비율 그대로 표시된다.
        self._img = QLabel(self)
        self._img.setFixedSize(self._tile_px, self._tile_px)
        self._img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img.setPixmap(image_io.load_thumb_qpixmap(item.path, self._tile_px))
        lay.addWidget(self._img, alignment=Qt.AlignmentFlag.AlignCenter)

        # 파일명 — 한 줄 고정, 너무 길면 가운데 ‘…’ 으로 elide (사진을 가리지 않게).
        from PyQt6.QtGui import QFontMetrics
        cap = QLabel(self)
        cap.setFixedHeight(_CAP_PX)
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setProperty("role", "muted")
        cap.setWordWrap(False)
        fm = QFontMetrics(cap.font())
        cap.setText(fm.elidedText(
            item.filename, Qt.TextElideMode.ElideMiddle, self._tile_px - 4,
        ))
        cap.setToolTip(i18n.KO.BULK_TILE_ZOOM_TOOLTIP + "\n" + item.filename)
        lay.addWidget(cap)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._selected = not self._selected
            self._refresh_visual()
            self.toggled.emit(self.item, self._selected)
        elif event.button() == Qt.MouseButton.RightButton:
            # 우클릭 → 크게 보기 (선택 상태는 건드리지 않음).
            self.zoom_requested.emit(self.item)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        if selected == self._selected:
            return
        self._selected = bool(selected)
        self._refresh_visual()

    def _refresh_visual(self) -> None:
        if self._selected:
            self.setStyleSheet(
                "#selTile { border: 3px solid #00D4FF; border-radius: 8px;"
                " background: rgba(0, 212, 255, 0.06); }"
            )
        else:
            self.setStyleSheet("")


class BulkSelectDialog(QDialog):
    """패널의 슬롯별 사진을 큰 그리드로 보여주고 다중 선택 후 액션 실행.

    actions = [(action_id, label, role), ...]  — 패널의 _SidePanel 와 동일 포맷.
    accepted 시 ``chosen()`` 으로 (action_id, [ImageItem]) 을 얻거나
    ``selection_action`` 시그널을 구독.
    """

    selection_action = pyqtSignal(str, list)      # (action_id, [ImageItem])

    def __init__(self,
                 title: str,
                 data: dict[str, list[ImageItem]],
                 actions: list[tuple[str, str, str]],
                 parent=None) -> None:
        super().__init__(parent)
        # 닫는 즉시 C++ 위젯 해제 — 매번 열 때마다 부모에 누적되지 않도록.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(title)
        self.setModal(True)
        # 노트북 등 작은 화면에서 하단 액션 버튼이 화면 밖으로 잘려
        # ‘버튼이 안 보인다’ 라고 느껴지지 않도록 화면 작업영역의 90% 로 클램프.
        want_w = _COLS * (_TILE_PX + 22) + 80
        want_h = 800
        scr = (parent.screen() if parent is not None and hasattr(parent, "screen")
               else None) or QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            want_w = min(want_w, int(g.width() * 0.92))
            want_h = min(want_h, int(g.height() * 0.88))
        self.resize(want_w, want_h)
        # 창에 최소화/최대화 버튼 + F11 전체화면 토글 (#9). 첫 show 이전에 설정.
        enable_window_controls(self)
        add_fullscreen_shortcut(self)

        # 전체 선택 상태 (페이지 전환에도 유지) — key 기반.
        self._selected_keys: set[str] = set()
        self._selected_items_by_key: dict[str, ImageItem] = {}
        # 현재 페이지에 그려진 타일만 보관 (페이지 전환 시 교체).
        self._tiles_by_key: dict[str, _SelectTile] = {}
        self._rubber: Optional[QRubberBand] = None
        self._rubber_origin: Optional[QPoint] = None

        # 슬롯 순서를 보존한 평면 (slot, item) 리스트 → 페이지 분할의 기준.
        self._flat: list[tuple[str, ImageItem]] = []
        for slot in sorted(data.keys()):
            for item in data[slot]:
                self._flat.append((slot, item))
        self._total_items = len(self._flat)
        self._paginated = self._total_items >= _PAGINATE_THRESHOLD
        self._page = 0
        self._page_count = (
            max(1, (self._total_items + _PAGE_SIZE - 1) // _PAGE_SIZE)
            if self._paginated else 1
        )
        self._tile_px = _TILE_PX
        self._slot_grids: list[tuple[list[ImageItem], QGridLayout]] = []

        self._build(title, actions)
        self._render_page()

    # ------------------------------------------------------------------
    def _page_slice(self) -> list[tuple[str, ImageItem]]:
        if not self._paginated:
            return self._flat
        start = self._page * _PAGE_SIZE
        return self._flat[start:start + _PAGE_SIZE]

    # ------------------------------------------------------------------
    def _build(self,
               title: str,
               actions: list[tuple[str, str, str]]) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # 헤더 / 안내
        head = QLabel(title, self)
        head.setStyleSheet(
            "color: #00D4FF; font-weight: 700; font-size: 16px;"
        )
        root.addWidget(head)

        hint = QLabel(i18n.KO.BULK_SELECT_HINT, self)
        hint.setProperty("role", "subtitle")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #7FB3D5;")
        root.addWidget(hint)

        # 상단 바: 선택 요약 + 사진 크기 슬라이더 -----------------------
        top = QHBoxLayout()
        top.setSpacing(10)
        self._summary_label = QLabel(
            i18n.KO.BULK_SELECT_SUMMARY_FMT.format(n=0), self,
        )
        self._summary_label.setStyleSheet("color: #00FFA3; font-weight: 700;")
        top.addWidget(self._summary_label)
        top.addStretch(1)
        size_label = QLabel(i18n.KO.BULK_SIZE_LABEL, self)
        size_label.setProperty("role", "muted")
        top.addWidget(size_label)
        self._size_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._size_slider.setRange(_TILE_MIN, _TILE_MAX)
        self._size_slider.setValue(self._tile_px)
        self._size_slider.setSingleStep(10)
        self._size_slider.setPageStep(40)
        self._size_slider.setFixedWidth(200)
        self._size_slider.valueChanged.connect(self._on_size_changed)
        top.addWidget(self._size_slider)
        self._size_value = QLabel(f"{self._tile_px} px", self)
        self._size_value.setProperty("role", "muted")
        self._size_value.setFixedWidth(64)
        top.addWidget(self._size_value)
        root.addLayout(top)

        # 슬롯별 섹션 (스크롤) — 가로 스크롤 절대 발생하지 않게 AlwaysOff.
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll = scroll
        # 드래그(러버밴드) 다중 선택 — viewport 빈 영역에서 시작 (페이지 교체와
        # 무관하게 viewport 는 유지되므로 이벤트필터는 한 번만 설치).
        scroll.viewport().installEventFilter(self)
        root.addWidget(scroll, stretch=1)

        if self._total_items == 0:
            empty = QLabel(i18n.KO.BULK_SELECT_EMPTY, self)
            empty.setStyleSheet("color: #7FB3D5; padding: 20px;")
            root.addWidget(empty)

        # 페이지네이션 바 (대량일 때만 노출) ----------------------------
        if self._paginated:
            page_bar = QHBoxLayout()
            page_bar.setSpacing(8)
            self._btn_prev = NeonButton(i18n.KO.BULK_PAGE_PREV, role="default")
            self._btn_prev.clicked.connect(lambda: self._go_page(self._page - 1))
            self._btn_next = NeonButton(i18n.KO.BULK_PAGE_NEXT, role="default")
            self._btn_next.clicked.connect(lambda: self._go_page(self._page + 1))
            self._page_label = QLabel("", self)
            self._page_label.setStyleSheet("color: #7FB3D5; font-weight: 700;")
            page_bar.addStretch(1)
            page_bar.addWidget(self._btn_prev)
            page_bar.addWidget(self._page_label)
            page_bar.addWidget(self._btn_next)
            page_bar.addStretch(1)
            root.addLayout(page_bar)

        # 하단 액션 바
        bar = QHBoxLayout()
        bar.setSpacing(8)
        # 전체 선택 / 해제 보조 버튼 — 가독성 위해 대비 높은 role.
        self.btn_select_all = NeonButton(i18n.KO.BULK_SELECT_ALL, role="primary")
        self.btn_select_all.clicked.connect(self._select_all)
        bar.addWidget(self.btn_select_all)
        self.btn_clear = NeonButton(i18n.KO.BULK_DESELECT_ALL, role="default")
        self.btn_clear.clicked.connect(self._clear_selection)
        bar.addWidget(self.btn_clear)
        bar.addStretch(1)

        # 액션 버튼들 — sizeHint 보다 작게 줄어들지 않도록 최소 폭을 명시.
        self._action_buttons: list[NeonButton] = []
        for action_id, label, role in actions:
            btn = NeonButton(label, role=role)
            btn.clicked.connect(
                lambda _c=False, a=action_id: self._fire(a)
            )
            btn.setMinimumWidth(max(btn.sizeHint().width(), 160))
            bar.addWidget(btn)
            self._action_buttons.append(btn)

        # 닫기
        btn_close = NeonButton(i18n.KO.BTN_CANCEL, role="ghost")
        btn_close.clicked.connect(self.reject)
        bar.addWidget(btn_close)
        root.addLayout(bar)

    # ------------------------------------------------------------------
    def _render_page(self) -> None:
        """현재 페이지의 타일을 새로 그린다 (선택 상태는 key 기반으로 복원)."""
        old = self._scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self._tiles_by_key.clear()
        self._slot_grids = []

        host = QWidget()
        host.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.MinimumExpanding)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(12)

        # 현재 페이지 항목을 슬롯별로 묶는다 (순서 보존).
        by_slot: dict[str, list[ImageItem]] = {}
        for slot, item in self._page_slice():
            by_slot.setdefault(slot, []).append(item)

        for slot, items in by_slot.items():
            slot_label = QLabel(
                i18n.KO.GROUP_HEADER_FMT.format(slot=slot, count=len(items)),
                host,
            )
            slot_label.setStyleSheet(
                "color: #00D4FF; font-weight: 700; padding-top: 4px;"
            )
            host_layout.addWidget(slot_label)

            grid_host = QWidget(host)
            grid = QGridLayout(grid_host)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(8)
            for item in items:
                tile = _SelectTile(item, tile_px=self._tile_px, parent=grid_host)
                tile.toggled.connect(self._on_tile_toggle)
                tile.zoom_requested.connect(self._open_zoom)
                if item.key in self._selected_keys:
                    tile.set_selected(True)
                self._tiles_by_key[item.key] = tile
            host_layout.addWidget(grid_host)
            self._slot_grids.append((items, grid))

        host_layout.addStretch(1)
        self._scroll.setWidget(host)
        QTimer.singleShot(0, self._relayout_grids)
        self._update_page_label()

    def _update_page_label(self) -> None:
        if not self._paginated:
            return
        self._page_label.setText(
            i18n.KO.BULK_PAGE_LABEL_FMT.format(
                page=self._page + 1, total=self._page_count,
            )
        )
        self._btn_prev.setEnabled(self._page > 0)
        self._btn_next.setEnabled(self._page < self._page_count - 1)

    def _go_page(self, page: int) -> None:
        page = max(0, min(self._page_count - 1, page))
        if page == self._page:
            return
        self._page = page
        self._render_page()

    def _on_size_changed(self, value: int) -> None:
        self._tile_px = int(value)
        self._size_value.setText(f"{value} px")
        # 타일 크기 변경 → 현재 페이지 재렌더 (선택 상태 유지).
        self._render_page()

    def _open_zoom(self, item: ImageItem) -> None:
        """우클릭 → 풀스크린 확대 뷰 (휠 줌 + 드래그 팬)."""
        from .zoom_window import FullscreenViewer
        viewer = FullscreenViewer(item.path, self)
        viewer.exec()

    # ------------------------------------------------------------------
    def _relayout_grids(self) -> None:
        """viewport 폭에 맞춰 슬롯별 grid columns 자동 계산 — 가로 스크롤 회피."""
        if not getattr(self, "_slot_grids", None):
            return
        vp_w = self._scroll.viewport().width() if hasattr(self, "_scroll") else 0
        if vp_w <= 0:
            vp_w = self.width()
        tile_w = self._tile_px + 22  # 타일 1 개의 폭 + spacing
        cols = max(1, min(_COLS, max(1, vp_w // tile_w)))
        for items, grid in self._slot_grids:
            # 현재 grid 의 위젯들을 한 번 비우고 cols 로 재배치 (위젯 자체는
            # 보존 — 선택 상태 유지).
            widgets = []
            for i in reversed(range(grid.count())):
                it = grid.takeAt(i)
                w = it.widget()
                if w is not None:
                    widgets.append(w)
            widgets.reverse()
            ordered = [self._tiles_by_key.get(item.key) for item in items]
            ordered = [w for w in ordered if w is not None]
            for i, w in enumerate(ordered):
                grid.addWidget(w, i // cols, i % cols)
            # 왼쪽 정렬 — 사용 컬럼은 stretch 0, 트레일링 컬럼에 여백을 몰아준다.
            for c in range(cols):
                grid.setColumnStretch(c, 0)
            grid.setColumnStretch(cols, 1)

    def resizeEvent(self, event):                       # noqa: N802
        super().resizeEvent(event)
        QTimer.singleShot(0, self._relayout_grids)

    # ------------------------------------------------------------------
    # 드래그(러버밴드) 다중 선택
    # ------------------------------------------------------------------
    def eventFilter(self, obj, event):                  # noqa: N802
        if not hasattr(self, "_scroll") or obj is not self._scroll.viewport():
            return super().eventFilter(obj, event)
        et = event.type()
        if et == QEvent.Type.MouseButtonPress \
                and event.button() == Qt.MouseButton.LeftButton:
            self._rubber_origin = event.pos()
            if self._rubber is None:
                self._rubber = QRubberBand(QRubberBand.Shape.Rectangle,
                                           self._scroll.viewport())
            self._rubber.setGeometry(QRect(self._rubber_origin, QSize()))
            self._rubber.show()
            return True
        if et == QEvent.Type.MouseMove and self._rubber_origin is not None:
            self._rubber.setGeometry(
                QRect(self._rubber_origin, event.pos()).normalized())
            return True
        if et == QEvent.Type.MouseButtonRelease and self._rubber_origin is not None:
            rect = self._rubber.geometry()
            self._rubber.hide()
            self._rubber_origin = None
            # 드래그 거리가 작으면(사실상 클릭) 타일의 클릭 토글에 맡긴다.
            if rect.width() > 6 or rect.height() > 6:
                self._select_in_rect(rect)
            return True
        return super().eventFilter(obj, event)

    def _select_in_rect(self, rect: QRect) -> None:
        vp = self._scroll.viewport()
        changed = False
        for item_key, tile in self._tiles_by_key.items():
            tl = tile.mapTo(vp, QPoint(0, 0))
            if rect.intersects(QRect(tl, tile.size())):
                tile.set_selected(True)
                self._selected_keys.add(item_key)
                self._selected_items_by_key[item_key] = tile.item
                changed = True
        if changed:
            self._refresh_summary()

    def _on_tile_toggle(self, item: ImageItem, selected: bool) -> None:
        if selected:
            self._selected_keys.add(item.key)
            self._selected_items_by_key[item.key] = item
        else:
            self._selected_keys.discard(item.key)
            self._selected_items_by_key.pop(item.key, None)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        self._summary_label.setText(
            i18n.KO.BULK_SELECT_SUMMARY_FMT.format(n=len(self._selected_keys))
        )

    def _select_all(self) -> None:
        # 전체(모든 페이지) 항목 선택.
        for _slot, item in self._flat:
            self._selected_keys.add(item.key)
            self._selected_items_by_key[item.key] = item
        # 현재 페이지에 그려진 타일은 시각 상태도 갱신.
        for tile in self._tiles_by_key.values():
            tile.set_selected(True)
        self._refresh_summary()

    def _clear_selection(self) -> None:
        self._selected_keys.clear()
        self._selected_items_by_key.clear()
        for tile in self._tiles_by_key.values():
            tile.set_selected(False)
        self._refresh_summary()

    def _fire(self, action_id: str) -> None:
        items = [self._selected_items_by_key[k] for k in self._selected_keys
                 if k in self._selected_items_by_key]
        if not items:
            return
        self.selection_action.emit(action_id, items)
        self.accept()
