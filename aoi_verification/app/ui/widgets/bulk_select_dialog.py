"""사이드 패널의 ‘선택 모드’ 를 위한 다중 선택 다이얼로그.

기존 inline 체크박스가 사진을 가리는 문제를 해결하기 위해 별도 큰 팝업 창에서
여러 사진을 클릭으로 선택 / 해제하고 액션을 실행한다.  하단의 액션 버튼들은
사이드 패널의 actions 메뉴와 1:1 대응.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                              QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ... import i18n
from ...models.slot import ImageItem
from ...utils import image_io
from .neon_button import NeonButton


_TILE_PX = 180          # 시원하게 보이는 다중 선택 그리드 썸네일 (원본 비율 유지)
_CAP_PX = 28            # 파일명 한 줄 — 사진을 가리지 않도록 충분히 확보
_COLS = 6


class _SelectTile(QFrame):
    """클릭 토글 가능한 큰 썸네일. 선택 시 네온 사이언 보더로 강조."""

    toggled = pyqtSignal(object, bool)        # (ImageItem, selected)

    def __init__(self, item: ImageItem, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self._selected = False
        self.setProperty("role", "card-soft")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # 사진 정사각 영역(_TILE_PX) + 캡션 한 줄(_CAP_PX) + 마진/스페이싱.
        self.setFixedSize(_TILE_PX + 14, _TILE_PX + _CAP_PX + 18)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        # 이미지 영역 — 정사각 박스에 KeepAspectRatio 로 들어가므로 가로/세로
        # 사진 모두 잘림 없이 원본 비율 그대로 표시된다.
        self._img = QLabel(self)
        self._img.setFixedSize(_TILE_PX, _TILE_PX)
        self._img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img.setPixmap(image_io.load_thumb_qpixmap(item.path, _TILE_PX))
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
            item.filename, Qt.TextElideMode.ElideMiddle, _TILE_PX - 4,
        ))
        cap.setToolTip(item.filename)
        lay.addWidget(cap)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._selected = not self._selected
            self._refresh_visual()
            self.toggled.emit(self.item, self._selected)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        if selected == self._selected:
            return
        self._selected = bool(selected)
        self._refresh_visual()

    def _refresh_visual(self) -> None:
        if self._selected:
            self.setStyleSheet(
                "QFrame { border: 3px solid #00D4FF; border-radius: 8px;"
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
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(_COLS * (_TILE_PX + 22) + 80, 800)
        self._tiles_by_key: dict[str, _SelectTile] = {}
        self._selected_keys: set[str] = set()
        self._selected_items_by_key: dict[str, ImageItem] = {}
        self._build(title, data, actions)

    # ------------------------------------------------------------------
    def _build(self,
               title: str,
               data: dict[str, list[ImageItem]],
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

        self._summary_label = QLabel(
            i18n.KO.BULK_SELECT_SUMMARY_FMT.format(n=0), self,
        )
        self._summary_label.setStyleSheet("color: #00FFA3; font-weight: 700;")
        root.addWidget(self._summary_label)

        # 슬롯별 섹션 (스크롤)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        host = QWidget()
        host.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.MinimumExpanding)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(12)

        total_items = 0
        for slot in sorted(data.keys()):
            items = list(data[slot])
            if not items:
                continue
            total_items += len(items)
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
            for i, item in enumerate(items):
                tile = _SelectTile(item, parent=grid_host)
                tile.toggled.connect(self._on_tile_toggle)
                self._tiles_by_key[item.key] = tile
                grid.addWidget(tile, i // _COLS, i % _COLS)
            host_layout.addWidget(grid_host)

        host_layout.addStretch(1)
        scroll.setWidget(host)
        root.addWidget(scroll, stretch=1)

        if total_items == 0:
            empty = QLabel(i18n.KO.BULK_SELECT_EMPTY, self)
            empty.setStyleSheet("color: #7FB3D5; padding: 20px;")
            root.addWidget(empty)

        # 하단 액션 바
        bar = QHBoxLayout()
        bar.setSpacing(8)
        # 전체 선택 / 해제 보조 버튼
        self.btn_select_all = NeonButton(i18n.KO.BULK_SELECT_ALL, role="ghost")
        self.btn_select_all.clicked.connect(self._select_all)
        bar.addWidget(self.btn_select_all)
        self.btn_clear = NeonButton(i18n.KO.BULK_DESELECT_ALL, role="ghost")
        self.btn_clear.clicked.connect(self._clear_selection)
        bar.addWidget(self.btn_clear)
        bar.addStretch(1)

        # 액션 버튼들
        self._action_buttons: list[NeonButton] = []
        for action_id, label, role in actions:
            btn = NeonButton(label, role=role)
            btn.clicked.connect(
                lambda _c=False, a=action_id: self._fire(a)
            )
            bar.addWidget(btn)
            self._action_buttons.append(btn)

        # 닫기
        btn_close = NeonButton(i18n.KO.BTN_CANCEL, role="ghost")
        btn_close.clicked.connect(self.reject)
        bar.addWidget(btn_close)
        root.addLayout(bar)

    # ------------------------------------------------------------------
    def _on_tile_toggle(self, item: ImageItem, selected: bool) -> None:
        if selected:
            self._selected_keys.add(item.key)
            self._selected_items_by_key[item.key] = item
        else:
            self._selected_keys.discard(item.key)
            self._selected_items_by_key.pop(item.key, None)
        self._summary_label.setText(
            i18n.KO.BULK_SELECT_SUMMARY_FMT.format(n=len(self._selected_keys))
        )

    def _select_all(self) -> None:
        for key, tile in self._tiles_by_key.items():
            tile.set_selected(True)
            self._selected_keys.add(key)
            self._selected_items_by_key[key] = tile.item
        self._summary_label.setText(
            i18n.KO.BULK_SELECT_SUMMARY_FMT.format(n=len(self._selected_keys))
        )

    def _clear_selection(self) -> None:
        for tile in self._tiles_by_key.values():
            tile.set_selected(False)
        self._selected_keys.clear()
        self._selected_items_by_key.clear()
        self._summary_label.setText(
            i18n.KO.BULK_SELECT_SUMMARY_FMT.format(n=0)
        )

    def _fire(self, action_id: str) -> None:
        items = [self._selected_items_by_key[k] for k in self._selected_keys
                 if k in self._selected_items_by_key]
        if not items:
            return
        self.selection_action.emit(action_id, items)
        self.accept()
