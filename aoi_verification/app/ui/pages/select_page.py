"""Stage 1 — 후보 선별 화면.

레이아웃: 상단 컨트롤 바 / 좌 (남은 후보) · 중앙 (결정 대상) · 우 (검증 대상)
        / 하단 (제외됨). 모두 Slot 별로 그룹화.

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

from ... import i18n
from ...models.group import GroupingResult, PhotoGroup
from ...models.slot import ImageItem
from ...utils import image_io
from ...utils import prefs as _prefs
from ..widgets.group_dialog import GroupDialog
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
    """Slot 별 누적 표시 패널 (좌/우/하단 공용)."""

    selection_action = pyqtSignal(str, str, list)
    # (panel_name, action_id, [ImageItem])

    tile_clicked = pyqtSignal(str, str, object)        # (panel_name, slot, ImageItem)
    plus_clicked = pyqtSignal(str, str)                # (panel_name, slot)

    def __init__(self, name: str, title: str,
                 *, vertical_scroll: bool = True,
                 actions: Optional[list[tuple[str, str, str]]] = None,
                 columns: int = 4,
                 parent=None) -> None:
        super().__init__(parent)
        self._name = name
        self._select_mode = False
        self._sections: dict[str, SlotSection] = {}
        self._cached: dict[str, list[ImageItem]] = {}

        self.setProperty("role", "section")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # 헤더 ----------------------------------------------------------
        head = QHBoxLayout()
        ttl = QLabel(title, self)
        ttl.setProperty("role", "subtitle")
        ttl.setStyleSheet("font-weight: 700; color: #00D4FF;")
        head.addWidget(ttl)
        head.addStretch(1)

        self._select_btn = NeonButton(i18n.KO.BTN_SELECT_MODE, role="ghost")
        self._select_btn.setCheckable(True)
        self._select_btn.toggled.connect(self._on_select_mode)
        head.addWidget(self._select_btn)
        outer.addLayout(head)

        # 일괄 액션 바 (선택 모드 ON 일 때만 노출) ----------------------
        self._action_bar = QHBoxLayout()
        self._action_bar.setSpacing(6)
        self._action_buttons: list[NeonButton] = []
        for action_id, label, role in actions or []:
            btn = NeonButton(label, role=role)
            btn.clicked.connect(
                lambda _checked=False, a=action_id: self._fire_batch(a)
            )
            self._action_bar.addWidget(btn)
            self._action_buttons.append(btn)
        self._action_bar.addStretch(1)
        self._action_host = QWidget(self)
        self._action_host.setLayout(self._action_bar)
        self._action_host.hide()
        outer.addWidget(self._action_host)

        # 스크롤 영역 ---------------------------------------------------
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        host = QWidget()
        self._scroll.setWidget(host)
        self._host_layout = QVBoxLayout(host)
        self._host_layout.setContentsMargins(4, 4, 4, 4)
        self._host_layout.setSpacing(10)
        self._host_layout.addStretch(1)

        if not vertical_scroll:
            self._scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            )
            self._scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded,
            )

        outer.addWidget(self._scroll, stretch=1)
        self._columns = columns

    # ------------------------------------------------------------------
    def update_data(self, data: dict[str, list[ImageItem]]) -> None:
        """Slot → ImageItem 리스트 매핑으로 패널 갱신."""
        self._cached = {k: list(v) for k, v in data.items() if v}

        # 기존 Slot 섹션 + 종료 stretch 제거
        while self._host_layout.count():
            item = self._host_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        for slot in sorted(self._cached.keys()):
            sec = SlotSection(slot, columns=self._columns,
                              select_mode=self._select_mode, parent=self)
            entries = [ThumbEntry(item=it) for it in self._cached[slot]]
            sec.set_entries(entries)
            sec.tile_clicked.connect(
                lambda ent, s=slot: self.tile_clicked.emit(self._name, s, ent.item)
            )
            sec.plus_clicked.connect(
                lambda s: self.plus_clicked.emit(self._name, s)
            )
            self._sections[slot] = sec
            self._host_layout.addWidget(sec)
        self._host_layout.addStretch(1)

    def cached(self) -> dict[str, list[ImageItem]]:
        return {k: list(v) for k, v in self._cached.items()}

    # ------------------------------------------------------------------
    def _on_select_mode(self, on: bool) -> None:
        self._select_mode = on
        self._select_btn.setText(
            i18n.KO.BTN_CANCEL_SELECT_MODE if on else i18n.KO.BTN_SELECT_MODE
        )
        self._action_host.setVisible(on)
        for sec in self._sections.values():
            sec.set_select_mode(on)

    def _fire_batch(self, action_id: str) -> None:
        items: list[ImageItem] = []
        for sec in self._sections.values():
            for ent in sec.grid.selected():
                items.append(ent.item)
        if not items:
            return
        self.selection_action.emit(self._name, action_id, items)


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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._state: Stage1State | None = None
        self._current: Optional[ImageItem] = None
        self._phase_label_text = ""
        self._phase_b_already_matched: dict[str, list[ImageItem]] = {}
        self._groups: GroupingResult | None = None
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
        self.left_panel = _SidePanel(
            self.PANEL_LEFT, i18n.KO.PANEL_LEFT_CANDIDATES,
            actions=[
                ("batch_verify", i18n.KO.BTN_BATCH_VERIFY, "primary"),
                ("batch_exclude", i18n.KO.BTN_BATCH_EXCLUDE, "danger"),
            ],
            columns=2,
        )
        self.left_panel.selection_action.connect(self._on_batch_action)
        self.left_panel.tile_clicked.connect(self._on_tile_click)
        self.left_panel.plus_clicked.connect(self._on_plus_click)
        # 2 col 그리드 (120px thumb + 14 padding) × 2 + spacing + 패널 padding 을
        # 모두 담을 최소 너비. 작게(1100) preset 에서도 가로 스크롤 없이 보이게.
        self.left_panel.setMinimumWidth(280)
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
        # 마지막 사용 값(#13) 복원
        _p = _prefs.load()
        self.size_slider.setValue(
            max(ScalableImage.MIN_LONG_EDGE,
                min(ScalableImage.MAX_LONG_EDGE,
                    int(_p.image_long_edge_select)))
        )
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
        # 그룹 보기 — 현재 사진이 그룹 대표일 때만 활성 (#15)
        self.btn_group = NeonButton("", role="warn")
        self.btn_group.hide()
        self.btn_group.clicked.connect(self._open_group_dialog)
        self.btn_verify.setToolTip(i18n.KO.SHORTCUT_TOOLTIP)
        self.btn_exclude.setToolTip(i18n.KO.SHORTCUT_TOOLTIP)
        self.btn_undo.setToolTip(i18n.KO.SHORTCUT_TOOLTIP)
        self.btn_verify.clicked.connect(lambda: self._decide("verify"))
        self.btn_exclude.clicked.connect(lambda: self._decide("exclude"))
        self.btn_undo.clicked.connect(self._undo)
        btn_row.addWidget(self.btn_undo)
        btn_row.addWidget(self.btn_group)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_verify)
        btn_row.addWidget(self.btn_exclude)
        cl.addLayout(btn_row)

        center_card.setMinimumWidth(420)
        self._h_splitter.addWidget(center_card)

        # RIGHT -------------------------------------------------------
        self.right_panel = _SidePanel(
            self.PANEL_RIGHT, i18n.KO.PANEL_RIGHT_TARGETS,
            actions=[
                ("remove", i18n.KO.BTN_REMOVE_FROM_TARGET, "danger"),
                ("to_exclude", i18n.KO.BTN_MOVE_TO_EXCLUDE, "warn"),
                ("recenter", i18n.KO.BTN_BACK_TO_CENTER, "ghost"),
            ],
            columns=2,
        )
        self.right_panel.selection_action.connect(self._on_batch_action)
        self.right_panel.tile_clicked.connect(self._on_tile_click)
        self.right_panel.plus_clicked.connect(self._on_plus_click)
        self.right_panel.setMinimumWidth(280)
        self._h_splitter.addWidget(self.right_panel)

        self._h_splitter.setStretchFactor(0, 2)
        self._h_splitter.setStretchFactor(1, 4)
        self._h_splitter.setStretchFactor(2, 2)

        # BOTTOM ------------------------------------------------------
        self.bottom_panel = _SidePanel(
            self.PANEL_BOTTOM, i18n.KO.PANEL_BOTTOM_EXCLUDED,
            vertical_scroll=False,
            actions=[
                ("to_target", i18n.KO.BTN_MOVE_TO_TARGET, "primary"),
                ("recenter", i18n.KO.BTN_BACK_TO_CENTER, "ghost"),
            ],
            # 7 col × 134px = 938 → 기본 폭 1280 에서 가로 스크롤 없이 보임.
            columns=7,
        )
        self.bottom_panel.selection_action.connect(self._on_batch_action)
        self.bottom_panel.tile_clicked.connect(self._on_tile_click)
        self.bottom_panel.plus_clicked.connect(self._on_plus_click)
        self.bottom_panel.setMinimumHeight(180)

        # 상하 QSplitter (3-pane 위쪽 / 제외 패널 아래쪽) -----------------
        self._v_splitter = QSplitter(Qt.Orientation.Vertical, self)
        self._v_splitter.setHandleWidth(6)
        self._v_splitter.setChildrenCollapsible(False)
        self._v_splitter.addWidget(self._h_splitter)
        self._v_splitter.addWidget(self.bottom_panel)
        self._v_splitter.setStretchFactor(0, 4)
        self._v_splitter.setStretchFactor(1, 1)
        root.addWidget(self._v_splitter, stretch=1)

        # 저장된 분할 비율 복원 + 변경 시 영속화 -------------------------
        _p2 = _prefs.load()
        if _p2.splitter_state_select_h:
            self._h_splitter.restoreState(
                QByteArray.fromBase64(_p2.splitter_state_select_h.encode("ascii"))
            )
        if _p2.splitter_state_select_v:
            self._v_splitter.restoreState(
                QByteArray.fromBase64(_p2.splitter_state_select_v.encode("ascii"))
            )
        self._h_splitter.splitterMoved.connect(self._save_splitter_state)
        self._v_splitter.splitterMoved.connect(self._save_splitter_state)

        # 단축키 --------------------------------------------------------
        for key in ("Left", "1"):
            QShortcut(QKeySequence(key), self,
                      activated=lambda: self._decide("verify"))
        for key in ("Right", "2"):
            QShortcut(QKeySequence(key), self,
                      activated=lambda: self._decide("exclude"))
        QShortcut(QKeySequence("Z"), self, activated=self._undo)

    # ------------------------------------------------------------------
    def _save_splitter_state(self, *args) -> None:
        try:
            _prefs.patch(
                splitter_state_select_h=bytes(
                    self._h_splitter.saveState().toBase64()
                ).decode("ascii"),
                splitter_state_select_v=bytes(
                    self._v_splitter.saveState().toBase64()
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
                   groups: GroupingResult | None = None,
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
        self._groups = groups
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
        self.bottom_panel.update_data({k: list(v) for k, v in self._state.excluded.items()})

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
        # 현재 사진이 그룹 대표라면 ‘그룹 보기’ 버튼 활성 (#15)
        g = self._groups.group_for(item) if self._groups else None
        if g is not None and item.key == g.rep.key and g.siblings:
            self.btn_group.setText(
                i18n.KO.GROUP_BTN_VIEW_FMT.format(n=g.size())
            )
            self.btn_group.show()
        else:
            self.btn_group.hide()

    def _on_size_changed(self, value: int) -> None:
        self.size_value.setText(f"{value} px")
        self.center_img.set_target_size(value)
        _prefs.patch(image_long_edge_select=int(value))

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

        # 그룹 대표인 경우 sibling 들에도 동일한 결정을 자동 적용 (#15)
        applied_siblings: list[ImageItem] = []
        if self._groups is not None:
            g = self._groups.group_for(item)
            if g is not None and item.key == g.rep.key and g.siblings:
                for sib in list(g.siblings):
                    target_pool[sib.slot].append(sib)
                    applied_siblings.append(sib)
                    self.decision_made.emit(action, sib)
                # 그룹 자체 해체
                self._groups.remove_from_group(item)

        # undo 단순화: sibling 일괄 결정은 한 번에 되돌릴 수 있도록 별도 마커
        if applied_siblings:
            self._state.history.append(("_siblings", applied_siblings))  # type: ignore[arg-type]

        self.state_changed.emit()
        self._advance_to_next()

    def _open_group_dialog(self) -> None:
        if self._groups is None or self._current is None:
            return
        g = self._groups.group_for(self._current)
        if g is None or self._current.key != g.rep.key:
            return
        dlg = GroupDialog(g, parent=self)
        dlg.exec()
        # 다이얼로그에서 분리된 항목은 ‘대표 바로 뒤’ 의 큐 위치에 끼워넣어
        # 사용자가 곧장 그것들에 대한 결정을 이어할 수 있도록.
        removed = dlg.removed_items
        if not removed or self._state is None:
            return
        try:
            pos = self._state.queue.index(self._current) + 1
        except ValueError:
            pos = 1
        for r in removed:
            self._state.queue.insert(pos, r)
            pos += 1
        # 화면도 갱신 (그룹 사이즈 표시가 바뀌었을 수 있음)
        self._show_center(self._current)
        self._refresh_all()
        self.state_changed.emit()

    def _undo(self) -> None:
        # Z 가 MatchPage 가 보일 때도 SelectPage 로 전달되는 것을 차단.
        if not self.isVisible():
            return
        if self._state is None or not self._state.history:
            return
        action, item = self._state.history.pop()
        # 그룹 일괄 결정 마커가 직전에 있으면 sibling 들도 함께 되돌린다.
        if action == "_siblings":
            # _siblings 마커는 두 번째 요소가 list[ImageItem]
            siblings: list[ImageItem] = item  # type: ignore[assignment]
            # 직전 action 한 번 더 pop
            if self._state.history:
                action, item = self._state.history.pop()
            else:
                action, item = ("verify", None)  # type: ignore[assignment]
            if item is None:
                return
            for s in siblings:
                pool = self._state.targets if action == "verify" else self._state.excluded
                try:
                    pool[s.slot].remove(s)
                except ValueError:
                    pass
                self._state.queue.insert(0, s)
        # 본 항목 되돌리기
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
        if panel == self.PANEL_RIGHT:
            items = list(self._state.targets.get(slot, []))
            source = SOURCE_TARGET
            already = self._phase_b_already_matched.get(slot, [])
        elif panel == self.PANEL_BOTTOM:
            items = list(self._state.excluded.get(slot, []))
            source = SOURCE_EXCLUDED
            already = []
        else:
            items = [it for it in self._state.queue if it.slot == slot]
            source = SOURCE_CANDIDATES
            already = []
        if not items and not already:
            return
        win = ZoomWindow(slot, items, source,
                         already_matched_items=already, parent=self)
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
