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
                             QSizePolicy, QVBoxLayout, QWidget)

from ... import config, i18n
from .. import theme
from ...models.slot import ImageItem
from ...utils import image_io
from .neon_button import NeonButton
from .no_wheel_slider import NoWheelSlider
from .option_group import columns_for_width
from . import sheet_host as sheets


_TILE_PX = config.Sizing.BULK_TILE_PX   # 다중 선택 그리드 기본 타일 (= 180)
_CAP_PX = 28            # 파일명 한 줄 — 사진을 가리지 않도록 충분히 확보
# 가로 최대 5 컬럼 + 6 번째부터 다음 행으로 wrap (사용자 요청 — 가로 스크롤
# 발생하지 않도록).  좁은 창에선 viewport 폭 기반으로 더 적게 동적 계산.
_COLS = 5
# 타일 실폭 = 사진 정사각(tile_px) + 좌우 마진/보더, 타일 사이 간격은 grid spacing.
# 열 수 계산(columns_for_width)과 창 폭 산정이 같은 값을 봐야 어긋나지 않는다.
_TILE_CHROME_W = 14
_GRID_SPACING = 8
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
        self.setFixedSize(self._tile_px + _TILE_CHROME_W,
                          self._tile_px + _CAP_PX + 18)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        # 이미지 영역 — 정사각 박스에 KeepAspectRatio 로 들어가므로 가로/세로
        # 사진 모두 잘림 없이 원본 비율 그대로 표시된다.
        self._img = QLabel(self)
        self._img.setFixedSize(self._tile_px, self._tile_px)
        self._img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # ★ 소스는 **가장 큰 표시 크기 하나**로만 읽는다.  `load_thumb_qpixmap` 의 LRU
        #   키에 size 가 들어가므로, 슬라이더가 움직일 때마다 새 크기로 부르면 매번
        #   캐시 미스 → 디스크 재로드 + 스무스 스케일이었고(1 tick 당 209~247ms 실측)
        #   크기별 엔트리가 쌓여 512MB 캐시까지 오염시켰다.  한 번 읽어 두고 재스케일한다.
        self._source_pix = image_io.load_thumb_qpixmap(item.path, _TILE_MAX)
        self._apply_scaled()
        lay.addWidget(self._img, alignment=Qt.AlignmentFlag.AlignCenter)

        # 파일명 — 한 줄 고정, 너무 길면 가운데 ‘…’ 으로 elide (사진을 가리지 않게).
        cap = QLabel(self)
        cap.setFixedHeight(_CAP_PX)
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setProperty("role", "muted")
        cap.setWordWrap(False)
        cap.setToolTip(i18n.KO.BULK_TILE_ZOOM_TOOLTIP + "\n" + item.filename)
        self._cap = cap
        self._elide_caption()
        lay.addWidget(cap)

    # ------------------------------------------------------------------
    def _apply_scaled(self) -> None:
        if self._source_pix is None or self._source_pix.isNull():
            return
        self._img.setPixmap(self._source_pix.scaled(
            self._tile_px, self._tile_px,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def _elide_caption(self) -> None:
        from PyQt6.QtGui import QFontMetrics
        fm = QFontMetrics(self._cap.font())
        self._cap.setText(fm.elidedText(
            self.item.filename, Qt.TextElideMode.ElideMiddle, self._tile_px - 4,
        ))

    def set_display_size(self, size: int) -> None:
        """크기 슬라이더 — 타일을 **재생성하지 않고** 보관 픽스맵만 재스케일한다.

        참조 구현: `unmatched_review_dialog._CandidateTile.set_display_size`."""
        self._tile_px = int(size)
        self.setFixedSize(self._tile_px + _TILE_CHROME_W,
                          self._tile_px + _CAP_PX + 18)
        self._img.setFixedSize(self._tile_px, self._tile_px)
        self._apply_scaled()
        self._elide_caption()

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

    @staticmethod
    def _sel_style() -> str:
        """★ 모듈/클래스 상수로 굽지 않는다 — 호출 시점에 팔레트를 읽어야 다크 전환이
        따라온다.  배경도 강조색 틴트를 쓴다(예전엔 현 팔레트에 없는 네온 초록이라
        파란 강조 체계에서 이 화면만 연둣빛으로 어긋났다)."""
        return (f"#selTile {{ border: 2px solid {theme.ACCENT}; border-radius: 8px;"
                f" background: {theme.ACCENT_TINT_SOFT}; }}")

    def _refresh_visual(self) -> None:
        self.setStyleSheet(self._sel_style() if self._selected else "")


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
        want_w = _COLS * (_TILE_PX + _TILE_CHROME_W + _GRID_SPACING) + 80
        want_h = 800
        scr = (parent.screen() if parent is not None and hasattr(parent, "screen")
               else None) or QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            want_w = min(want_w, int(g.width() * 0.92))
            want_h = min(want_h, int(g.height() * 0.88))
        self.resize(want_w, want_h)
        # ★ 창 제어(최소화/최대화/F11) 헬퍼를 부르지 않는다 — 이 다이얼로그는
        #   별도 OS 창이 아니라 **메인 창 안의 시트**로 뜬다(widgets/sheet_host.py).
        #   최대화·전체화면은 메인 창이 담당한다.

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
        # 크기 슬라이더 디바운스 — ★ `self` 를 부모로 둔다.  정적 `QTimer.singleShot` 은
        #   위젯이 지연 시간 안에 파괴되면 죽은 위젯으로 발화한다(하우스 규칙).
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._apply_tile_size)

        self._build(title, actions)
        self._render_page()          # 끝에서 `_refresh_summary` — 초기 0 장 → 액션 버튼 비활성

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

        # ★ 제목을 본문에 그리지 않는다 — `setWindowTitle` 을 sheet_host 가 시트
        #   상단 chrome 에 이미 그리므로, 여기 또 그리면 같은 문장이 상하로 두 번
        #   보인다(image_info_dialog 가 같은 이유로 내부 표제를 안 그린다).
        hint = QLabel(i18n.KO.BULK_SELECT_HINT, self)
        hint.setProperty("role", "subtitle")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.MUTE};")
        root.addWidget(hint)

        # 상단 바: 선택 요약 + 사진 크기 슬라이더 -----------------------
        top = QHBoxLayout()
        top.setSpacing(10)
        self._summary_label = QLabel(
            i18n.KO.BULK_SELECT_SUMMARY_FMT.format(n=0), self,
        )
        # ★ 선택 개수는 '합격 판정' 이 아니다 — 성공색(PASS)이 아니라 강조색을 쓴다.
        self._summary_label.setStyleSheet(f"color: {theme.ACCENT}; font-weight: 700;")
        top.addWidget(self._summary_label)
        top.addStretch(1)
        size_label = QLabel(i18n.KO.BULK_SIZE_LABEL, self)
        size_label.setProperty("role", "muted")
        top.addWidget(size_label)
        self._size_slider = NoWheelSlider(Qt.Orientation.Horizontal, self)
        self._size_slider.setRange(_TILE_MIN, _TILE_MAX)
        self._size_slider.setValue(self._tile_px)
        # 스텝/페이지는 '사진 크기' 슬라이더 공통 규약(20/80) — 범위는 화면마다
        # 용도가 달라(타일 vs 원본) 유지하지만, 손맛이 화면마다 달라질 이유는 없다.
        self._size_slider.setSingleStep(20)
        self._size_slider.setPageStep(80)
        self._size_slider.setFixedWidth(200)
        self._size_slider.valueChanged.connect(self._on_size_changed)
        top.addWidget(self._size_slider)
        self._size_value = QLabel(f"{self._tile_px} px", self)
        self._size_value.setProperty("role", "monoMuted")
        self._size_value.setFixedWidth(64)
        self._size_value.setAlignment(Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
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
            empty.setStyleSheet(f"color: {theme.MUTE}; padding: 20px;")
            root.addWidget(empty)

        # 페이지네이션 바 (대량일 때만 노출) ----------------------------
        if self._paginated:
            page_bar = QHBoxLayout()
            page_bar.setSpacing(8)
            self._btn_prev = NeonButton(i18n.KO.BULK_PAGE_PREV, role="ghost")
            self._btn_prev.clicked.connect(lambda: self._go_page(self._page - 1))
            self._btn_next = NeonButton(i18n.KO.BULK_PAGE_NEXT, role="ghost")
            self._btn_next.clicked.connect(lambda: self._go_page(self._page + 1))
            self._page_label = QLabel("", self)
            self._page_label.setStyleSheet(f"color: {theme.MUTE}; font-weight: 700;")
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
        # ★ 한 화면에 채운 강조 버튼은 하나뿐이어야 한다 — 주 액션(우측)과 시선이
        #   갈리지 않게 보조 버튼 둘 다 ghost 로 둔다(role="default" 는 QSS 에 없는 등급).
        self.btn_select_all = NeonButton(i18n.KO.BULK_SELECT_ALL, role="ghost")
        self.btn_select_all.clicked.connect(self._select_all)
        bar.addWidget(self.btn_select_all)
        self.btn_clear = NeonButton(i18n.KO.BULK_DESELECT_ALL, role="ghost")
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
            # 슬롯 이름은 판정이 아니라 그룹 제목이다 — paneTitle 등급을 쓴다.
            slot_label.setProperty("role", "paneTitle")
            slot_label.setStyleSheet("padding-top: 4px;")
            host_layout.addWidget(slot_label)

            grid_host = QWidget(host)
            grid = QGridLayout(grid_host)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(_GRID_SPACING)
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
        # ★ 요약은 페이지가 바뀌면 반드시 다시 계산해야 한다 — '이 페이지 밖 m 장' 은
        #   현재 페이지와의 차집합이라 선택을 건드리지 않아도 **페이지만 넘기면 값이 바뀐다**.
        #   안 불렀을 때는 전체 선택 뒤 다음 페이지로 가도 경고 없이 "선택됨: N 장" 만 남아,
        #   보이는 타일만 동작한다고 믿고 액션을 누를 수 있었다.
        self._refresh_summary()

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
        self._resize_timer.stop()      # 새 페이지는 이미 현재 크기로 그린다(경합 방지)
        self._render_page()

    def _on_size_changed(self, value: int) -> None:
        """드래그 중에는 라벨만 즉시 따라가고, 타일 적용은 150ms 디바운스한다.

        ★ 예전엔 tick 마다 `_render_page()` 로 페이지를 통째로 다시 만들었다.  1000장
        미만이면 페이지네이션이 없어 한 tick 에 수백~999개 위젯을 파괴·생성했고,
        한 번 드래그에 수 초가 사라졌다.  이제 (1) 디바운스로 횟수를 줄이고
        (2) 재생성 대신 보관 픽스맵을 재스케일한다.
        참조 구현: `match_review_page` 의 `_resize_timer`(150ms)."""
        self._tile_px = int(value)          # 즉시 갱신 — 다른 계산이 이 값을 본다
        self._size_value.setText(f"{value} px")
        self._resize_timer.start(150)

    def _apply_tile_size(self) -> None:
        for tile in self._tiles_by_key.values():
            tile.set_display_size(self._tile_px)
        # 타일이 커지면 열 수가 줄어야 가로 스크롤이 안 생긴다.
        self._relayout_grids()

    def _open_zoom(self, item: ImageItem) -> None:
        """우클릭 → 풀스크린 확대 뷰 (휠 줌 + 드래그 팬)."""
        from .zoom_window import FullscreenViewer
        viewer = FullscreenViewer(item.path, self)
        sheets.run(viewer, full_bleed=True)

    # ------------------------------------------------------------------
    def _relayout_grids(self) -> None:
        """viewport 폭에 맞춰 슬롯별 grid columns 자동 계산 — 가로 스크롤 회피."""
        if not getattr(self, "_slot_grids", None):
            return
        vp_w = self._scroll.viewport().width() if hasattr(self, "_scroll") else 0
        if vp_w <= 0:
            vp_w = self.width()
        # ★ 타일 실폭·간격은 _SelectTile 의 setFixedSize·grid.setSpacing 과 **같은
        #   상수**를 봐야 한다.  여기만 옛 값을 들고 있으면 타일은 넓어졌는데 열은
        #   그대로라 가로 스크롤이 난다.  공식 자체는 columns_for_width 한 곳에 있다.
        cols = min(_COLS, columns_for_width(vp_w, self._tile_px + _TILE_CHROME_W,
                                            _GRID_SPACING))
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
        n = len(self._selected_keys)
        # 화면 밖(다른 페이지)에서 함께 선택돼 있는 장수 — 현재 페이지 key 와의
        # 차집합.  페이지네이션 중이고 실제로 밖에 있을 때만 덧붙인다.
        off = 0
        if self._paginated:
            here = {item.key for _slot, item in self._page_slice()}
            off = len(self._selected_keys - here)
        if off > 0:
            self._summary_label.setText(
                i18n.KO.BULK_SELECT_SUMMARY_OFFPAGE_FMT.format(n=n, m=off))
        else:
            self._summary_label.setText(
                i18n.KO.BULK_SELECT_SUMMARY_FMT.format(n=n))
        # ★ 0 장일 때 액션 버튼을 눌러도 조용히 아무 일도 안 일어났다(‘먹은 클릭’).
        #   ZoomWindow 처럼 **선택 전에는 비활성**으로 두어 왜 안 되는지 보이게 한다.
        for btn in getattr(self, "_action_buttons", ()):
            btn.setEnabled(n > 0)

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
