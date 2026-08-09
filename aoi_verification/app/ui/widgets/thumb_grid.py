"""썸네일 그리드 (+N 처리, 선택 모드 지원)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QLabel,
                              QToolButton, QVBoxLayout, QWidget)

from ... import config, i18n
from .. import theme
from ...models.slot import ImageItem
from ...utils import image_io
from .option_group import columns_for_width


THUMB_PX = config.Sizing.THUMB_PX
# 타일 실폭 = 사진 정사각(tile_px) + 좌우 마진/보더.  열 수 계산이 같은 값을 봐야
# 한다 — 어긋나면 가로 스크롤이 나거나 한 열을 낭비한다.
_TILE_CHROME_W = 14


# ---------------------------------------------------------------------------
@dataclass
class ThumbEntry:
    item: ImageItem
    extra: dict | None = None     # 추가 메타 (예: score, dim_overlay)


class _ThumbTile(QFrame):
    """단일 썸네일 박스."""

    clicked = pyqtSignal(object)              # ThumbEntry
    toggled = pyqtSignal(object, bool)        # (ThumbEntry, selected)
    expand_requested = pyqtSignal(object)     # ThumbEntry — ‘더 크게 보기’
    sel_toggled = pyqtSignal(object, bool)    # (ThumbEntry, selected) — 인라인 선택

    def __init__(self,
                 entry: ThumbEntry,
                 *,
                 select_mode: bool = False,
                 inline_select: bool = False,
                 dim: bool = False,
                 footer: str = "",
                 show_expand: bool = False,
                 tile_px: Optional[int] = None,
                 prefer_mid: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self.entry = entry
        self._dim = dim
        self._inline_select = bool(inline_select)
        self._inline_selected = False
        self._tile_px = int(tile_px) if tile_px else THUMB_PX
        # 후보 패널은 prefer_mid=True 로 mid 캐시 (~800px) 를 소스로 사용 →
        # 같은 표시 크기에서도 더 선명 (#5).
        self._prefer_mid = bool(prefer_mid)
        self.setFixedSize(self._tile_px + _TILE_CHROME_W,
                          self._tile_px + (40 if footer else 18))
        self.setProperty("role", "card-soft")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        self._img = QLabel(self)
        self._img.setFixedSize(self._tile_px, self._tile_px)
        self._img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._load_pix()
        lay.addWidget(self._img, alignment=Qt.AlignmentFlag.AlignCenter)

        if footer:
            cap = QLabel(footer, self)
            cap.setProperty("role", "muted")
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(cap)

        self._checkbox: Optional[QCheckBox] = None
        if select_mode:
            self._enable_checkbox()

        # ‘더 크게 보기’ 버튼 (선택 사항 — Stage 2 후보 타일에만 표시) ----
        self._expand_btn: Optional[QToolButton] = None
        if show_expand:
            btn = QToolButton(self)
            # ★ 이모지(🔍)가 아니라 텍스트 글리프를 쓴다 — 이모지는 OS·폰트에 따라
            #   컬러/외곽선으로 갈려 앱의 다른 아이콘(✓ ✕ → ↩ ▾)과 어긋난다.
            btn.setText("⤢")
            btn.setToolTip(i18n.KO.EXPAND_VIEW_TOOLTIP)
            btn.setAutoRaise(True)
            # 클릭 대상 하한(theme.PROFILE.target_min) 을 지킨다 — 24px 은 미달이었다.
            btn.setProperty("role", "tileExpand")
            btn.setFixedSize(QSize(26, 26))
            btn.move(self.width() - 30, 4)
            btn.show()
            btn.clicked.connect(
                lambda: self.expand_requested.emit(self.entry)
            )
            self._expand_btn = btn

        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ------------------------------------------------------------------
    def _load_pix(self) -> None:
        # 스케일(+dim) 완료 픽스맵을 RAM 캐시에서 가져온다(#렉) — 후보 선별에서
        # 결정마다 타일을 재생성해도 디스크 재로딩/스케일을 반복하지 않는다.
        # prefer_mid: 후보 패널처럼 더 선명한 표시가 필요하면 mid 캐시(~800px) 소스.
        pix = image_io.cached_tile_pixmap(
            self.entry.item.path, self._tile_px,
            prefer_mid=self._prefer_mid, dim=self._dim,
        )
        self._img.setPixmap(pix)

    def _enable_checkbox(self) -> None:
        cb = QCheckBox(self)
        cb.move(8, 8)
        cb.stateChanged.connect(
            lambda st: self.toggled.emit(
                self.entry, st == Qt.CheckState.Checked.value,
            )
        )
        cb.show()
        self._checkbox = cb

    # 인라인 선택(체크박스 없이 클릭 토글) ---------------------------------
    def set_inline_selected(self, selected: bool) -> None:
        """선택 표시는 **동적 프로퍼티 + QSS** 로 한다.

        ★ 예전엔 클래스 상수 `_SEL_STYLE` 을 인스턴스 스타일시트로 걸었다.  세 가지가
        동시에 틀렸다: (1) 클래스 본문에서 `theme.ACCENT` 를 `.format()` 했으므로 색이
        **import 시점에 굳어** 다크 전환이 영영 안 먹었고, (2) 선택자가 스코프 없는
        `QFrame {…}` 이라 자식 QLabel(파일명·이미지)까지 캐스케이드돼 타일이 3중 액자로
        보였으며, (3) 배경이 현 팔레트에 없는 옛 네온 팔레트의 주황이었다.
        게다가 한 화면에 수백 개인 위젯이라 인스턴스 스타일시트 자체가 렉의 원인이다.

        ★ `setProperty` 만으로는 화면이 안 바뀐다 — `setStyleSheet` 과 달리 동적
        프로퍼티는 재계산을 트리거하지 않으므로 unpolish/polish 를 반드시 함께 부른다."""
        self._inline_selected = bool(selected)
        self.setProperty("inlineSelected", "true" if self._inline_selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def is_inline_selected(self) -> bool:
        return self._inline_selected

    # 마우스 클릭 → 시그널 (체크박스/확대 버튼 클릭과 분리) -----------------
    def mousePressEvent(self, event):  # noqa: N802
        if self._checkbox is not None and self._checkbox.geometry().contains(event.pos()):
            return super().mousePressEvent(event)
        if self._expand_btn is not None and self._expand_btn.geometry().contains(event.pos()):
            return super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            if self._inline_select:
                # 인라인 선택 모드: 클릭=선택 토글(파란 테두리), 즉시 동작 없음 (#2).
                self.set_inline_selected(not self._inline_selected)
                self.sel_toggled.emit(self.entry, self._inline_selected)
            else:
                self.clicked.emit(self.entry)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        # 인라인 선택 모드에서 더블클릭 = 선택 해제 (확대 모드 제거).
        # 더블클릭 직전의 단일 press 가 토글했더라도 여기서 OFF 로 고정 →
        # 더블클릭은 항상 ‘해제’로 끝난다.  super() 를 호출하면 QWidget 기본
        # 구현이 press 를 재생성해 다시 토글하므로 여기서 종료한다.
        if self._inline_select and event.button() == Qt.MouseButton.LeftButton:
            self.set_inline_selected(False)
            self.sel_toggled.emit(self.entry, False)
            return
        super().mouseDoubleClickEvent(event)


class _PlusTile(QFrame):
    """+N 표시 타일."""

    clicked = pyqtSignal()

    def __init__(self, n: int, *, tile_px: Optional[int] = None,
                 parent=None) -> None:
        super().__init__(parent)
        size = int(tile_px) if tile_px else THUMB_PX
        self.setProperty("role", "card-soft")
        # 같은 격자에 들어가므로 _ThumbTile 과 **같은 실폭**이어야 한다.
        self.setFixedSize(size + _TILE_CHROME_W, size + 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lab = QLabel(i18n.KO.COUNT_PLUS_N_FMT.format(n=n), self)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 색은 QSS 가 준다(role="plusTile") — 구우면 색 모드 전환 때 이 타일만 남는다.
        lab.setProperty("role", "plusTile")
        lab.setMinimumHeight(size)
        lay.addWidget(lab)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
class ThumbGrid(QWidget):
    """+N 처리(5장 이상 → 첫 4장 + +N) 가 들어간 그리드.

    selected_changed: 선택 모드에서 체크 변경될 때 emit.
    """

    tile_clicked = pyqtSignal(object)                  # ThumbEntry
    plus_clicked = pyqtSignal()
    selected_changed = pyqtSignal(list)                # list[ThumbEntry]
    expand_requested = pyqtSignal(object)              # ThumbEntry
    inline_changed = pyqtSignal()                      # 인라인 선택 변경 알림

    def __init__(self,
                 *,
                 columns: int = 4,
                 select_mode: bool = False,
                 inline_select: bool = False,
                 truncate: bool = True,
                 show_expand: bool = False,
                 tile_px: Optional[int] = None,
                 prefer_mid: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self._columns = columns
        self._select_mode = select_mode
        self._inline_select = bool(inline_select)
        self._truncate = truncate
        self._show_expand = show_expand
        self._tile_px = tile_px
        self._prefer_mid = bool(prefer_mid)
        self._entries: list[ThumbEntry] = []
        self._selected: list[ThumbEntry] = []
        self._tiles: list[_ThumbTile] = []         # 인라인 선택용 타일 참조
        self._active_cols = columns                # 현재 적용 중인 열 수(반응형)

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(8)

    # ------------------------------------------------------------------
    def set_entries(self, entries: Iterable[ThumbEntry]) -> None:
        self._entries = list(entries)
        self._selected.clear()
        self.selected_changed.emit([])
        self._rebuild()

    def set_select_mode(self, on: bool) -> None:
        self._select_mode = on
        self._selected.clear()
        self.selected_changed.emit([])
        self._rebuild()

    def selected(self) -> list[ThumbEntry]:
        return list(self._selected)

    # ------------------------------------------------------------------
    def _rebuild(self) -> None:
        # clear
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._tiles = []

        threshold = config.CONFIG.show_n_threshold
        max_visible = config.CONFIG.max_thumbs_per_row

        # 인라인 선택 모드는 +N 생략 없이 전체를 표시(전체 선택/드래그가 모두
        # 유효하도록, #2). 그 외에는 기존 +N 트렁케이션 유지.
        if self._truncate and not self._inline_select \
                and len(self._entries) >= threshold:
            visible = self._entries[:max_visible]
            extra = len(self._entries) - max_visible
        else:
            visible = self._entries
            extra = 0

        cols = self._effective_columns()
        self._active_cols = cols
        row = 0
        col = 0
        for ent in visible:
            tile = _ThumbTile(ent, select_mode=self._select_mode,
                              inline_select=self._inline_select,
                              footer=ent.item.filename,
                              show_expand=self._show_expand,
                              tile_px=self._tile_px,
                              prefer_mid=self._prefer_mid)
            tile.clicked.connect(self.tile_clicked.emit)
            tile.toggled.connect(self._on_toggle)
            tile.sel_toggled.connect(self._on_sel_toggle)
            tile.expand_requested.connect(self.expand_requested.emit)
            self._tiles.append(tile)
            self._grid.addWidget(tile, row, col)
            col += 1
            if col >= cols:
                col = 0
                row += 1
        if extra > 0:
            plus = _PlusTile(extra, tile_px=self._tile_px)
            plus.clicked.connect(self.plus_clicked.emit)
            self._grid.addWidget(plus, row, col)

    # ------------------------------------------------------------------
    # 반응형 열 수 — 패널 폭에 맞춰 가로 스크롤 없이 자동 reflow.
    # ------------------------------------------------------------------
    def _effective_columns(self) -> int:
        avail = self.width()
        if avail <= 0:
            return self._columns          # 아직 배치 전 — 설정된 열 수를 유지
        # 타일 실폭 = 사진 정사각 + 좌우 마진/보더(_ThumbTile 의 setFixedSize 와 동일).
        return min(self._columns,
                   columns_for_width(avail,
                                     (self._tile_px or THUMB_PX) + _TILE_CHROME_W,
                                     self._grid.spacing()))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        cols = self._effective_columns()
        if cols != self._active_cols:
            self._active_cols = cols
            self._relayout_columns(cols)

    def _relayout_columns(self, cols: int) -> None:
        """기존 그리드 위젯(타일 + +N)을 순서 보존하여 새 cols 로 재배치 —
        위젯 재생성/재디코드 없음."""
        widgets = []
        for i in reversed(range(self._grid.count())):
            it = self._grid.takeAt(i)
            w = it.widget()
            if w is not None:
                widgets.append(w)
        widgets.reverse()
        for i, w in enumerate(widgets):
            self._grid.addWidget(w, i // cols, i % cols)

    def _on_toggle(self, entry: ThumbEntry, selected: bool) -> None:
        if selected:
            if entry not in self._selected:
                self._selected.append(entry)
        else:
            if entry in self._selected:
                self._selected.remove(entry)
        self.selected_changed.emit(list(self._selected))

    # ------------------------------------------------------------------
    # 인라인 선택 (체크박스 없이 클릭/드래그/전체선택) (#2)
    # ------------------------------------------------------------------
    def _on_sel_toggle(self, entry: ThumbEntry, selected: bool) -> None:
        self.inline_changed.emit()

    def set_all_inline_selected(self, selected: bool) -> None:
        for t in self._tiles:
            t.set_inline_selected(selected)
        self.inline_changed.emit()

    def inline_selected(self) -> list[ThumbEntry]:
        """인라인으로 고른 항목들.

        ★ `selected()` 와 섞지 마라 — 그쪽은 **체크박스 모드** 전용이고 여기는
        체크박스 없이 타일을 눌러 고르는 경로다(두 모드는 동시에 켜지지 않는다)."""
        return [t.entry for t in self._tiles if t.is_inline_selected()]

    def remove_entry(self, entry: ThumbEntry) -> bool:
        """항목 **한 개**만 지우고 나머지는 그대로 둔다(전체 재생성 회피).

        ★ `truncate`(+N 타일)가 켜진 그리드에서는 쓰지 않는다 — 한 장이 빠지면
        숨어 있던 한 장이 올라와야 해서 단건 경로가 성립하지 않는다.
        Stage 1 좌측 패널처럼 전량을 그리는 그리드에서만 안전하다."""
        if self._truncate:
            return False
        tile = next((x for x in self._tiles if x.entry is entry), None)
        if tile is None:
            return False
        self._grid.removeWidget(tile)      # ★ 먼저 레이아웃에서 떼야 재배치가 어긋나지 않는다
        tile.setParent(None)
        tile.deleteLater()
        self._tiles.remove(tile)
        if entry in self._entries:
            self._entries.remove(entry)
        if entry in self._selected:
            self._selected.remove(entry)
        self._relayout_columns(self._active_cols)
        return True
