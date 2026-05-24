"""Stage 1 — 후보 선별 화면.

레이아웃: 상단 컨트롤 바 (검증 제외 사진 보기 버튼 포함)
        / 좌 (남은 후보) · 중앙 (결정 대상) · 우 (검증 대상).
검증에서 제외한 사진들은 화면을 차지하지 않고, 상단 버튼을 누르면 팝업으로
모아 볼 수 있다.

키보드 단축키:
  ← 또는 1 → 검증
  → 또는 2 → 제외
  Z       → 되돌리기
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QByteArray, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea,
                              QSizePolicy, QSlider, QSplitter, QVBoxLayout,
                              QWidget)

from ... import config, i18n
from ...models.slot import ImageItem
from ...utils import image_io
from ...utils import prefs as _prefs
from ..widgets.neon_button import NeonButton
from ..widgets.neon_card import NeonCard
from ..widgets.scalable_image import ScalableImage
from ..widgets.slot_section import SlotSection
from ..widgets.thumb_grid import ThumbEntry
from ..widgets.zoom_window import (ZoomWindow, SOURCE_TARGET, SOURCE_EXCLUDED,
                                    SOURCE_CANDIDATES)


# ---------------------------------------------------------------------------
@dataclass
class Stage1State:
    """페이지가 들고 있는 상태 (외부에서 주입/회수)."""
    queue: list[ImageItem]                       # 남은 후보 (앞에서 pop)
    targets: dict[str, list[ImageItem]] = field(default_factory=lambda: defaultdict(list))
    excluded: dict[str, list[ImageItem]] = field(default_factory=lambda: defaultdict(list))
    history: list[tuple[str, ImageItem]] = field(default_factory=list)
    # history: ("verify"|"exclude", item)


class _SidePanel(QFrame):
    """Slot 별 누적 표시 패널 (좌/우/하단 공용).

    [선택 모드] 버튼 클릭 시 inline 체크박스가 아니라 큰 팝업 다이얼로그가
    뜬다 — 사진을 가리지 않고 시원하게 다중 선택 가능.
    """

    selection_action = pyqtSignal(str, str, list)
    # (panel_name, action_id, [ImageItem])

    tile_clicked = pyqtSignal(str, str, object)        # (panel_name, slot, ImageItem)
    plus_clicked = pyqtSignal(str, str)                # (panel_name, slot)
    expand_requested = pyqtSignal(str, str, object)    # (panel_name, slot, ImageItem)

    def __init__(self, name: str, title: str,
                 *, vertical_scroll: bool = True,
                 actions: Optional[list[tuple[str, str, str]]] = None,
                 columns: int = 4,
                 tile_px: Optional[int] = None,
                 inline_select: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self._name = name
        self._title = title
        self._actions = list(actions or [])
        self._tile_px = tile_px
        self._inline_select = bool(inline_select)
        self._sections: dict[str, SlotSection] = {}
        self._cached: dict[str, list[ImageItem]] = {}

        self.setProperty("role", "section")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # 헤더 — 제목 + (인라인 선택 도구) + ‘선택 모드’ 버튼
        head = QHBoxLayout()
        ttl = QLabel(title, self)
        ttl.setProperty("role", "subtitle")
        ttl.setStyleSheet("font-weight: 700; color: #00D4FF;")
        head.addWidget(ttl)
        head.addStretch(1)

        # 인라인 선택 일괄작업·전체선택 등은 ‘선택 모드’ 팝업으로 일원화 —
        # 메인 헤더는 ‘선택 모드’ 버튼만 둔다.  타일 클릭=선택 / 더블클릭=해제는
        # inline_select 로 계속 동작(헤더 도구 없이).
        if self._actions:
            self._select_btn = NeonButton(i18n.KO.BTN_SELECT_MODE, role="ghost")
            self._select_btn.clicked.connect(self._open_bulk_select)
            head.addWidget(self._select_btn)
        # 외부에서 추가 헤더 버튼을 꽂을 수 있도록 head 레이아웃을 노출.
        self._head_layout = head
        outer.addLayout(head)

        # 스크롤 영역 ---------------------------------------------------
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        host = QWidget()
        self._scroll.setWidget(host)
        self._host_layout = QVBoxLayout(host)
        self._host_layout.setContentsMargins(4, 4, 4, 4)
        self._host_layout.setSpacing(10)
        self._host_layout.addStretch(1)

        # 후보 영역은 가로 스크롤이 절대 생기지 않도록 — 타일은 ThumbGrid 가
        # 패널 폭에 맞춰 열 수를 자동 reflow 한다.
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        if not vertical_scroll:
            self._scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            )

        outer.addWidget(self._scroll, stretch=1)
        self._columns = columns

    # ------------------------------------------------------------------
    def update_data(self, data: dict[str, list[ImageItem]]) -> None:
        """Slot → ImageItem 리스트 매핑으로 패널 갱신."""
        self._cached = {k: list(v) for k, v in data.items() if v}
        self._sections = {}

        while self._host_layout.count():
            item = self._host_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        for slot in sorted(self._cached.keys()):
            sec = SlotSection(slot, columns=self._columns,
                              select_mode=False,
                              inline_select=self._inline_select,
                              truncate=not self._inline_select,
                              tile_px=self._tile_px, parent=self)
            entries = [ThumbEntry(item=it) for it in self._cached[slot]]
            sec.set_entries(entries)
            sec.tile_clicked.connect(
                lambda ent, s=slot: self.tile_clicked.emit(self._name, s, ent.item)
            )
            sec.plus_clicked.connect(
                lambda s: self.plus_clicked.emit(self._name, s)
            )
            sec.expand_requested.connect(
                lambda ent, s=slot: self.expand_requested.emit(
                    self._name, s, ent.item)
            )
            self._sections[slot] = sec
            self._host_layout.addWidget(sec)
        self._host_layout.addStretch(1)

    def cached(self) -> dict[str, list[ImageItem]]:
        return {k: list(v) for k, v in self._cached.items()}

    # ------------------------------------------------------------------
    # 인라인 선택 — 타일 클릭=선택 / 더블클릭=해제.  일괄작업·드래그는
    # ‘선택 모드’ 팝업으로 일원화했으므로 여기엔 전체선택(Ctrl+A) 헬퍼만 둔다.
    # ------------------------------------------------------------------
    def _set_all_inline(self, selected: bool) -> None:
        for sec in self._sections.values():
            sec.grid.set_all_inline_selected(selected)

    # ------------------------------------------------------------------
    def _open_bulk_select(self) -> None:
        """[선택 모드] 클릭 → 큰 팝업 다이얼로그 띄움."""
        from ..widgets.bulk_select_dialog import BulkSelectDialog
        if not self._cached:
            return
        dlg = BulkSelectDialog(
            title=i18n.KO.BULK_SELECT_TITLE_FMT.format(panel=self._title),
            data=self._cached,
            actions=self._actions,
            parent=self,
        )
        dlg.selection_action.connect(
            lambda action_id, items: self.selection_action.emit(
                self._name, action_id, items,
            )
        )
        dlg.exec()


# ---------------------------------------------------------------------------
class SelectPage(QWidget):
    """Stage 1 메인 위젯."""

    # 외부로 전달되는 시그널
    decision_made = pyqtSignal(str, object)            # ("verify"|"exclude", ImageItem)
    finished = pyqtSignal()                             # 큐가 모두 비었을 때
    state_changed = pyqtSignal()                        # 자동 저장 트리거

    PANEL_LEFT = "left"
    PANEL_RIGHT = "right"
    PANEL_BOTTOM = "bottom"

    # 좁은 창 (≤ THRESH_LO) 에선 좌/중/우 3-pane 을 위→아래 세로 스택으로
    # 자동 전환 → 가로 스크롤 회피.  넓은 창 (≥ THRESH_HI) 에선 원래 가로
    # 배치.  hysteresis 갭으로 임계 근처에서 flicker 방지 (#2).
    _RESPONSIVE_THRESH_LO = 960
    _RESPONSIVE_THRESH_HI = 1080

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._state: Stage1State | None = None
        self._current: Optional[ImageItem] = None
        self._phase_label_text = ""
        self._phase_b_already_matched: dict[str, list[ImageItem]] = {}
        # 스플리터 방향을 첫 showEvent 에서 한 번만 확정했는지 (#cold-start).
        self._orientation_seeded = False
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # 상단 바 -------------------------------------------------------
        top = QHBoxLayout()
        self.title = QLabel(i18n.KO.STAGE1_TITLE, self)
        self.title.setProperty("role", "title")
        top.addWidget(self.title)
        top.addStretch(1)
        # [검증 제외 사진 보기 (n)] — 제외된 사진은 화면에서 숨기고,
        # 이 버튼으로 팝업에서 모아 본다. 0 장이면 비활성.
        self.btn_view_excluded = NeonButton(
            i18n.KO.BTN_VIEW_EXCLUDED_FMT.format(n=0), role="ghost",
        )
        self.btn_view_excluded.clicked.connect(self._open_excluded_dialog)
        self.btn_view_excluded.setEnabled(False)
        top.addWidget(self.btn_view_excluded)
        # [선택 종료] — 남은 미결정 사진을 모두 ‘검증 제외’ 로 처리하고
        # Stage 2 로 진행 (사용자 결정).  큐가 비어 있으면 자동 비활성.
        self.btn_end_selection = NeonButton(
            i18n.KO.BTN_END_SELECTION, role="warn",
        )
        self.btn_end_selection.clicked.connect(self._end_selection_now)
        self.btn_end_selection.setEnabled(False)
        top.addWidget(self.btn_end_selection)
        top.addSpacing(20)
        self.phase_label = QLabel("", self)
        self.phase_label.setProperty("role", "subtitle")
        top.addWidget(self.phase_label)
        top.addSpacing(20)
        self.progress_label = QLabel("", self)
        self.progress_label.setProperty("role", "muted")
        top.addWidget(self.progress_label)
        root.addLayout(top)

        # 중앙 3-pane — QSplitter 로 사용자 조절 + 상태 영속 -------------
        self._h_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._h_splitter.setHandleWidth(6)
        self._h_splitter.setChildrenCollapsible(False)

        # LEFT --------------------------------------------------------
        # 측면 패널 타일 — 기본(240px)의 50% (사용자 요청).  같은 패널 폭에서
        # 한 줄에 더 많은 사진이 들어가고 한눈에 더 많은 후보를 비교할 수 있다.
        side_tile = config.Sizing.THUMB_PX // 2     # 240 → 120
        self.left_panel = _SidePanel(
            self.PANEL_LEFT, i18n.KO.PANEL_LEFT_CANDIDATES,
            actions=[
                ("batch_verify", i18n.KO.BTN_BATCH_VERIFY, "primary"),
                ("batch_exclude", i18n.KO.BTN_BATCH_EXCLUDE, "danger"),
            ],
            # 타일 절반 크기 → 같은 폭에 3 열 그리드 깔리도록.
            columns=3,
            tile_px=side_tile,
            inline_select=True,        # 타일 클릭=선택 / 더블클릭=해제 (Ctrl+A=전체)
        )
        self.left_panel.selection_action.connect(self._on_batch_action)
        self.left_panel.tile_clicked.connect(self._on_tile_click)
        self.left_panel.plus_clicked.connect(self._on_plus_click)
        # 후보 패널은 확대(줌) 모드를 두지 않는다 — 더블클릭은 선택 해제용.
        # 3 col × (120 thumb + 14 padding) + spacing + 패널 padding 을 담을 최소
        # 너비.  좁은 창에선 세로 스택으로 reflow 되어 무관.
        self.left_panel.setMinimumWidth(220)
        self._h_splitter.addWidget(self.left_panel)

        # CENTER ------------------------------------------------------
        center_card = NeonCard(role="card", parent=self)
        cl = center_card.body()
        center_title = QLabel(i18n.KO.PANEL_CENTER_DECIDE, center_card)
        center_title.setProperty("role", "subtitle")
        center_title.setStyleSheet("font-weight: 700; color: #00D4FF;")
        center_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(center_title)

        # Slot 명 (파일명은 표시하지 않음) -----------------------------
        self.slot_label = QLabel("", center_card)
        self.slot_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.slot_label.setStyleSheet(
            "color: #7FB3D5; font-size: 14px; font-weight: 600; padding: 2px;"
        )
        cl.addWidget(self.slot_label)

        # 사진 크기 슬라이더 -------------------------------------------
        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        size_label = QLabel(i18n.KO.IMAGE_SIZE_LABEL, center_card)
        size_label.setProperty("role", "muted")
        self.size_slider = QSlider(Qt.Orientation.Horizontal, center_card)
        self.size_slider.setRange(ScalableImage.MIN_LONG_EDGE,
                                   ScalableImage.MAX_LONG_EDGE)
        # 모니터 크기에 맞춰 자동 시작값. 사용자가 바꾸면 세션 동안만 유지되고
        # 프로그램 재시작 시 다시 자동 맞춤으로 초기화 (prefs 저장 안 함).
        self.size_slider.setValue(ScalableImage.auto_fit_long_edge())
        self.size_slider.setSingleStep(20)
        self.size_slider.setPageStep(80)
        self.size_value = QLabel(f"{self.size_slider.value()} px", center_card)
        self.size_value.setProperty("role", "muted")
        self.size_value.setFixedWidth(64)
        self.size_value.setAlignment(Qt.AlignmentFlag.AlignRight
                                     | Qt.AlignmentFlag.AlignVCenter)
        self.size_slider.valueChanged.connect(self._on_size_changed)
        size_row.addWidget(size_label)
        size_row.addWidget(self.size_slider, stretch=1)
        size_row.addWidget(self.size_value)
        cl.addLayout(size_row)

        # 이미지 (스크롤 영역) -----------------------------------------
        self.center_img = ScalableImage(center_card)
        self._img_scroll = QScrollArea(center_card)
        self._img_scroll.setWidgetResizable(False)
        self._img_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_scroll.setWidget(self.center_img)
        self._img_scroll.setStyleSheet(
            "QScrollArea { background: #050810; border: 1px solid #1F2A3F; "
            "border-radius: 8px; }"
        )
        self._img_scroll.setMinimumHeight(300)
        self._img_scroll.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Expanding)
        cl.addWidget(self._img_scroll, stretch=1)

        # 버튼 줄 (사진 밑에 명확히 분리) -------------------------------
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.setContentsMargins(0, 6, 0, 0)
        self.btn_verify = NeonButton("✓  " + i18n.KO.BTN_VERIFY, role="primary")
        self.btn_exclude = NeonButton("✕  " + i18n.KO.BTN_EXCLUDE, role="danger")
        self.btn_undo = NeonButton(i18n.KO.BTN_UNDO, role="ghost")
        self.btn_verify.setToolTip(i18n.KO.SHORTCUT_TOOLTIP)
        self.btn_exclude.setToolTip(i18n.KO.SHORTCUT_TOOLTIP)
        self.btn_undo.setToolTip(i18n.KO.SHORTCUT_TOOLTIP)
        self.btn_verify.clicked.connect(lambda: self._decide("verify"))
        self.btn_exclude.clicked.connect(lambda: self._decide("exclude"))
        self.btn_undo.clicked.connect(self._undo)
        btn_row.addWidget(self.btn_undo)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_verify)
        btn_row.addWidget(self.btn_exclude)
        cl.addLayout(btn_row)

        center_card.setMinimumWidth(360)
        self._h_splitter.addWidget(center_card)

        # RIGHT — 좌측과 동일한 절반 타일 크기 + 3열 그리드 (사용자 요청).
        self.right_panel = _SidePanel(
            self.PANEL_RIGHT, i18n.KO.PANEL_RIGHT_TARGETS,
            actions=[
                ("to_exclude", i18n.KO.BTN_MOVE_TO_EXCLUDE, "warn"),
                ("recenter", i18n.KO.BTN_BACK_TO_CENTER, "ghost"),
            ],
            columns=3,
            tile_px=side_tile,
        )
        self.right_panel.selection_action.connect(self._on_batch_action)
        self.right_panel.tile_clicked.connect(self._on_tile_click)
        self.right_panel.plus_clicked.connect(self._on_plus_click)
        self.right_panel.setMinimumWidth(220)
        self._h_splitter.addWidget(self.right_panel)

        self._h_splitter.setStretchFactor(0, 2)
        self._h_splitter.setStretchFactor(1, 4)
        self._h_splitter.setStretchFactor(2, 2)

        root.addWidget(self._h_splitter, stretch=1)

        # 저장된 분할 비율 복원 + 변경 시 영속화 -------------------------
        _p2 = _prefs.load()
        if _p2.splitter_state_select_h:
            self._h_splitter.restoreState(
                QByteArray.fromBase64(_p2.splitter_state_select_h.encode("ascii"))
            )
        self._h_splitter.splitterMoved.connect(self._save_splitter_state)

        # 단축키 --------------------------------------------------------
        for key in ("Left", "1"):
            QShortcut(QKeySequence(key), self,
                      activated=lambda: self._decide("verify"))
        for key in ("Right", "2"):
            QShortcut(QKeySequence(key), self,
                      activated=lambda: self._decide("exclude"))
        QShortcut(QKeySequence("Z"), self, activated=self._undo)
        # Ctrl+A — 좌측 후보 패널 전체 선택 (#2).
        QShortcut(QKeySequence.StandardKey.SelectAll, self,
                  activated=self._select_all_candidates)

    def _select_all_candidates(self) -> None:
        if self.isVisible():
            self.left_panel._set_all_inline(True)

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def resizeEvent(self, event):                       # noqa: N802
        super().resizeEvent(event)
        self._update_splitter_orientation()

    def showEvent(self, event):                         # noqa: N802
        super().showEvent(event)
        # 실제로 보여질 때의 너비로 방향을 한 번 확정한다. 구성 중 잠깐 좁아졌다
        # 다시 넓어지는 과도기 때문에 세로로 굳는 버그를 방지 — 히스테리시스는
        # 그 이후의 사용자 리사이즈에만 적용된다.
        if not self._orientation_seeded:
            self._seed_splitter_orientation()

    def _seed_splitter_orientation(self) -> None:
        """히스테리시스 없이 중점 기준으로 초기 방향을 확정 (#cold-start)."""
        if not hasattr(self, "_h_splitter"):
            return
        w = self.width()
        mid = (self._RESPONSIVE_THRESH_LO + self._RESPONSIVE_THRESH_HI) // 2
        target = (Qt.Orientation.Horizontal if w >= mid
                  else Qt.Orientation.Vertical)
        if self._h_splitter.orientation() != target:
            self._h_splitter.setOrientation(target)
            self._h_splitter.setSizes([300, 600, 300]
                                      if target == Qt.Orientation.Horizontal
                                      else [200, 500, 200])
        self._orientation_seeded = True

    def _update_splitter_orientation(self) -> None:
        """창 폭에 따라 H ↔ V splitter 전환 — 가로 스크롤 없이 reflow."""
        if not hasattr(self, "_h_splitter"):
            return
        # 첫 표시(showEvent)로 방향이 확정되기 전의 구성 중 리사이즈는 무시 —
        # 과도기 너비로 방향이 잘못 굳는 것을 막는다.
        if not self._orientation_seeded:
            return
        cur = self._h_splitter.orientation()
        w = self.width()
        # hysteresis — 임계 근처에서 토글이 깜빡이지 않도록.
        if cur == Qt.Orientation.Horizontal and w < self._RESPONSIVE_THRESH_LO:
            self._h_splitter.setOrientation(Qt.Orientation.Vertical)
            self._h_splitter.setSizes([200, 500, 200])
        elif cur == Qt.Orientation.Vertical and w > self._RESPONSIVE_THRESH_HI:
            self._h_splitter.setOrientation(Qt.Orientation.Horizontal)
            self._h_splitter.setSizes([300, 600, 300])

    # ------------------------------------------------------------------
    def _save_splitter_state(self, *args) -> None:
        try:
            _prefs.patch(
                splitter_state_select_h=bytes(
                    self._h_splitter.saveState().toBase64()
                ).decode("ascii"),
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load_state(self,
                   queue: list[ImageItem],
                   targets: dict[str, list[ImageItem]] | None = None,
                   excluded: dict[str, list[ImageItem]] | None = None,
                   history: list[tuple[str, ImageItem]] | None = None,
                   phase_label: str = "",
                   phase_b_already_matched: dict[str, list[ImageItem]] | None = None,
                   ) -> None:
        self._state = Stage1State(
            queue=list(queue),
            targets=defaultdict(list, {k: list(v) for k, v in (targets or {}).items()}),
            excluded=defaultdict(list, {k: list(v) for k, v in (excluded or {}).items()}),
            history=list(history or []),
        )
        self._phase_label_text = phase_label
        self.phase_label.setText(phase_label)
        self._phase_b_already_matched = phase_b_already_matched or {}
        self._refresh_all()
        self._advance_to_next()

    def get_state(self) -> Stage1State | None:
        return self._state

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _refresh_all(self) -> None:
        if self._state is None:
            return
        # left = 남은 큐를 Slot 별로 그룹화
        left_groups: dict[str, list[ImageItem]] = defaultdict(list)
        for it in self._state.queue:
            left_groups[it.slot].append(it)
        # 현재 결정 중인 사진은 left 에서 제외
        if self._current is not None and self._current in left_groups[self._current.slot]:
            left_groups[self._current.slot].remove(self._current)
            if not left_groups[self._current.slot]:
                left_groups.pop(self._current.slot, None)
        self.left_panel.update_data(left_groups)

        self.right_panel.update_data({k: list(v) for k, v in self._state.targets.items()})
        self._refresh_excluded_button()
        self._refresh_end_selection_button()

    def _refresh_end_selection_button(self) -> None:
        """큐에 미결정 사진이 남아 있으면 활성, 비면 비활성."""
        n_remaining = len(self._state.queue) if self._state else 0
        self.btn_end_selection.setEnabled(n_remaining > 0)

    def _refresh_excluded_button(self) -> None:
        if self._state is None:
            self.btn_view_excluded.setText(
                i18n.KO.BTN_VIEW_EXCLUDED_FMT.format(n=0)
            )
            self.btn_view_excluded.setEnabled(False)
            return
        n = sum(len(v) for v in self._state.excluded.values())
        self.btn_view_excluded.setText(
            i18n.KO.BTN_VIEW_EXCLUDED_FMT.format(n=n)
        )
        self.btn_view_excluded.setEnabled(n > 0)

    def _advance_to_next(self) -> None:
        if self._state is None:
            return
        if not self._state.queue:
            self._current = None
            self.center_img.clear_image()
            self.slot_label.setText("")
            self.progress_label.setText("")
            self.finished.emit()
            return
        self._current = self._state.queue[0]
        self._show_center(self._current)
        self.progress_label.setText(
            i18n.KO.PROGRESS_SLOT_FMT.format(
                slot=self._current.slot,
                done=self._already_decided_count(),
                total=self._total_count(),
            )
        )
        self._refresh_all()

    def _already_decided_count(self) -> int:
        if self._state is None:
            return 0
        n = 0
        for v in self._state.targets.values():
            n += len(v)
        for v in self._state.excluded.values():
            n += len(v)
        return n

    def _total_count(self) -> int:
        if self._state is None:
            return 0
        return self._already_decided_count() + len(self._state.queue)

    def _show_center(self, item: ImageItem) -> None:
        self.center_img.set_image(item.path)
        # 파일명은 표시하지 않고 Slot 명만 노출한다 (요청 사항).
        self.slot_label.setText(i18n.KO.SLOT_LABEL_FMT.format(slot=item.slot))

    def _on_size_changed(self, value: int) -> None:
        self.size_value.setText(f"{value} px")
        self.center_img.set_target_size(value)
        # 사용자 변경은 세션 동안만 유지 — 재시작 시 자동 맞춤으로 초기화.

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------
    def _decide(self, action: str) -> None:
        # QShortcut 의 기본 context 가 WindowShortcut 이라 다른 페이지가
        # 보이는 상태에서도 ←/→/1/2 가 여기로 전달된다. 보이지 않을 땐 무시.
        if not self.isVisible():
            return
        if self._state is None or self._current is None:
            return
        item = self._current
        # 큐에서 제거
        try:
            self._state.queue.remove(item)
        except ValueError:
            pass
        target_pool = self._state.targets if action == "verify" else self._state.excluded
        target_pool[item.slot].append(item)
        self._state.history.append((action, item))
        self.decision_made.emit(action, item)
        self.state_changed.emit()
        self._advance_to_next()

    def _undo(self) -> None:
        # Z 가 MatchPage 가 보일 때도 SelectPage 로 전달되는 것을 차단.
        if not self.isVisible():
            return
        if self._state is None or not self._state.history:
            return
        action, item = self._state.history.pop()
        pool = self._state.targets if action == "verify" else self._state.excluded
        try:
            pool[item.slot].remove(item)
        except ValueError:
            pass
        self._state.queue.insert(0, item)
        self.state_changed.emit()
        self._advance_to_next()

    # ------------------------------------------------------------------
    # Batch actions from panels
    # ------------------------------------------------------------------
    def _on_batch_action(self, panel: str, action_id: str,
                          items: list[ImageItem]) -> None:
        if self._state is None:
            return
        if panel == self.PANEL_RIGHT:
            for it in items:
                if it in self._state.targets[it.slot]:
                    self._state.targets[it.slot].remove(it)
                if action_id == "to_exclude":
                    self._state.excluded[it.slot].append(it)
                elif action_id == "recenter":
                    self._state.queue.insert(0, it)
                # remove → nothing additional
        elif panel == self.PANEL_BOTTOM:
            for it in items:
                if it in self._state.excluded[it.slot]:
                    self._state.excluded[it.slot].remove(it)
                if action_id == "to_target":
                    self._state.targets[it.slot].append(it)
                elif action_id == "recenter":
                    self._state.queue.insert(0, it)
        elif panel == self.PANEL_LEFT:
            for it in items:
                if it in self._state.queue:
                    self._state.queue.remove(it)
                if action_id == "batch_verify":
                    self._state.targets[it.slot].append(it)
                elif action_id == "batch_exclude":
                    self._state.excluded[it.slot].append(it)
        self.state_changed.emit()
        self._advance_to_next()

    # ------------------------------------------------------------------
    # Zoom-view window
    # ------------------------------------------------------------------
    def _on_tile_click(self, panel: str, slot: str, _item: ImageItem) -> None:
        self._open_zoom(panel, slot)

    def _on_plus_click(self, panel: str, slot: str) -> None:
        self._open_zoom(panel, slot)

    def _open_zoom(self, panel: str, slot: str) -> None:
        if self._state is None:
            return
        view_only = False
        if panel == self.PANEL_RIGHT:
            items = list(self._state.targets.get(slot, []))
            source = SOURCE_TARGET
            already = self._phase_b_already_matched.get(slot, [])
        elif panel == self.PANEL_BOTTOM:
            items = list(self._state.excluded.get(slot, []))
            source = SOURCE_EXCLUDED
            already = []
        else:
            # Stage 1 의 검증 후보 — 단순 확대 뷰어로만 동작 (액션 없음).
            items = [it for it in self._state.queue if it.slot == slot]
            source = SOURCE_CANDIDATES
            already = []
            view_only = True
        if not items and not already:
            return
        win = ZoomWindow(slot, items, source,
                         already_matched_items=already,
                         view_only=view_only, parent=self)
        win.action_requested.connect(
            lambda act, sel: self._apply_zoom_action(panel, act, sel)
        )
        win.exec()

    def _apply_zoom_action(self, panel: str, action: str,
                            items: list[ImageItem]) -> None:
        if self._state is None:
            return
        if panel == self.PANEL_RIGHT:
            for it in items:
                if it in self._state.targets[it.slot]:
                    self._state.targets[it.slot].remove(it)
                if action == "exclude":
                    self._state.excluded[it.slot].append(it)
                elif action == "recenter":
                    self._state.queue.insert(0, it)
        elif panel == self.PANEL_BOTTOM:
            for it in items:
                if it in self._state.excluded[it.slot]:
                    self._state.excluded[it.slot].remove(it)
                if action == "verify":
                    self._state.targets[it.slot].append(it)
                elif action == "recenter":
                    self._state.queue.insert(0, it)
        self.state_changed.emit()
        self._advance_to_next()

    # ------------------------------------------------------------------
    # 선택 종료 — 남은 미결정 사진을 모두 ‘검증 제외’ 로 처리하고 진행
    # ------------------------------------------------------------------
    def _end_selection_now(self) -> None:
        """[선택 종료] — 남은 큐를 모두 excluded 로 옮기고 Stage 2 로."""
        if self._state is None:
            return
        n_remaining = len(self._state.queue)
        if n_remaining == 0:
            return
        from PyQt6.QtWidgets import QMessageBox
        ret = QMessageBox.question(
            self, i18n.KO.END_SELECTION_CONFIRM_TITLE,
            i18n.KO.END_SELECTION_CONFIRM_FMT.format(n=n_remaining),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        # 큐의 모든 항목을 슬롯별 excluded 에 추가 + history 기록.
        # 큐의 사본을 만든 뒤 비운다 (반복 중 mutate 방지).
        for it in list(self._state.queue):
            self._state.excluded[it.slot].append(it)
            self._state.history.append(("exclude", it))
            self.decision_made.emit("exclude", it)
        self._state.queue.clear()
        # 현재 결정 중인 사진은 _advance_to_next 에서 자연스럽게 None 으로
        # 떨어지면서 finished 시그널이 emit 된다.
        self._current = None
        self.state_changed.emit()
        self._advance_to_next()

    # ------------------------------------------------------------------
    # 검증 제외 사진 팝업 다이얼로그
    # ------------------------------------------------------------------
    def _open_excluded_dialog(self) -> None:
        """[검증 제외 사진 보기] 클릭 → 큰 팝업으로 표시 + 다중 액션."""
        from ..widgets.bulk_select_dialog import BulkSelectDialog
        if self._state is None:
            return
        data = {k: list(v) for k, v in self._state.excluded.items() if v}
        if not data:
            return
        dlg = BulkSelectDialog(
            title=i18n.KO.BULK_SELECT_EXCLUDED_TITLE,
            data=data,
            actions=[
                ("to_target", i18n.KO.BTN_MOVE_TO_TARGET, "primary"),
                ("recenter", i18n.KO.BTN_BACK_TO_CENTER, "ghost"),
            ],
            parent=self,
        )
        dlg.selection_action.connect(
            lambda action_id, items: self._on_batch_action(
                self.PANEL_BOTTOM, action_id, items,
            )
        )
        dlg.exec()
