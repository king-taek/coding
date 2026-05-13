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

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea,
                              QSizePolicy, QVBoxLayout, QWidget)

from ... import i18n
from ...models.slot import ImageItem
from ...utils import image_io
from ..widgets.neon_button import NeonButton
from ..widgets.neon_card import NeonCard
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

        # 중앙 3-pane ---------------------------------------------------
        center_row = QHBoxLayout()
        center_row.setSpacing(10)

        # LEFT --------------------------------------------------------
        self.left_panel = _SidePanel(
            self.PANEL_LEFT, i18n.KO.PANEL_LEFT_CANDIDATES,
            actions=[
                ("batch_verify", i18n.KO.BTN_BATCH_VERIFY, "primary"),
                ("batch_exclude", i18n.KO.BTN_BATCH_EXCLUDE, "danger"),
            ],
        )
        self.left_panel.selection_action.connect(self._on_batch_action)
        self.left_panel.tile_clicked.connect(self._on_tile_click)
        self.left_panel.plus_clicked.connect(self._on_plus_click)
        self.left_panel.setMinimumWidth(260)
        center_row.addWidget(self.left_panel, stretch=2)

        # CENTER ------------------------------------------------------
        center_card = NeonCard(role="card", parent=self)
        cl = center_card.body()
        center_title = QLabel(i18n.KO.PANEL_CENTER_DECIDE, center_card)
        center_title.setProperty("role", "subtitle")
        center_title.setStyleSheet("font-weight: 700; color: #00D4FF;")
        center_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(center_title)

        self.center_img = QLabel(center_card)
        self.center_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.center_img.setMinimumSize(560, 560)
        self.center_img.setStyleSheet(
            "background: #050810; border: 1px solid #1F2A3F; border-radius: 8px;"
        )
        self.center_img.setSizePolicy(QSizePolicy.Policy.Expanding,
                                      QSizePolicy.Policy.Expanding)
        cl.addWidget(self.center_img, stretch=1)

        self.center_caption = QLabel("", center_card)
        self.center_caption.setProperty("role", "muted")
        self.center_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(self.center_caption)

        # 버튼 줄
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
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

        center_card.setMinimumWidth(560)
        center_row.addWidget(center_card, stretch=4)

        # RIGHT -------------------------------------------------------
        self.right_panel = _SidePanel(
            self.PANEL_RIGHT, i18n.KO.PANEL_RIGHT_TARGETS,
            actions=[
                ("remove", i18n.KO.BTN_REMOVE_FROM_TARGET, "danger"),
                ("to_exclude", i18n.KO.BTN_MOVE_TO_EXCLUDE, "warn"),
                ("recenter", i18n.KO.BTN_BACK_TO_CENTER, "ghost"),
            ],
        )
        self.right_panel.selection_action.connect(self._on_batch_action)
        self.right_panel.tile_clicked.connect(self._on_tile_click)
        self.right_panel.plus_clicked.connect(self._on_plus_click)
        self.right_panel.setMinimumWidth(260)
        center_row.addWidget(self.right_panel, stretch=2)

        root.addLayout(center_row, stretch=1)

        # BOTTOM ------------------------------------------------------
        self.bottom_panel = _SidePanel(
            self.PANEL_BOTTOM, i18n.KO.PANEL_BOTTOM_EXCLUDED,
            vertical_scroll=False,
            actions=[
                ("to_target", i18n.KO.BTN_MOVE_TO_TARGET, "primary"),
                ("recenter", i18n.KO.BTN_BACK_TO_CENTER, "ghost"),
            ],
            columns=8,
        )
        self.bottom_panel.selection_action.connect(self._on_batch_action)
        self.bottom_panel.tile_clicked.connect(self._on_tile_click)
        self.bottom_panel.plus_clicked.connect(self._on_plus_click)
        self.bottom_panel.setMinimumHeight(240)
        root.addWidget(self.bottom_panel)

        # 단축키 --------------------------------------------------------
        for key in ("Left", "1"):
            QShortcut(QKeySequence(key), self,
                      activated=lambda: self._decide("verify"))
        for key in ("Right", "2"):
            QShortcut(QKeySequence(key), self,
                      activated=lambda: self._decide("exclude"))
        QShortcut(QKeySequence("Z"), self, activated=self._undo)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load_state(self,
                   queue: list[ImageItem],
                   targets: dict[str, list[ImageItem]] | None = None,
                   excluded: dict[str, list[ImageItem]] | None = None,
                   history: list[tuple[str, ImageItem]] | None = None,
                   phase_label: str = "",
                   phase_b_already_matched: dict[str, list[ImageItem]] | None = None
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
        self.bottom_panel.update_data({k: list(v) for k, v in self._state.excluded.items()})

    def _advance_to_next(self) -> None:
        if self._state is None:
            return
        if not self._state.queue:
            self._current = None
            self.center_img.clear()
            self.center_caption.setText("")
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
        try:
            mid = image_io.get_mid_path(item.path)
            pix = QPixmap(str(mid))
        except Exception:
            pix = QPixmap(560, 560)
            pix.fill(QColor(8, 16, 32))
        if pix.isNull():
            pix = QPixmap(560, 560)
            pix.fill(QColor(8, 16, 32))
        target = self.center_img.size()
        scaled = pix.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.center_img.setPixmap(scaled)
        self.center_caption.setText(f"{item.slot}  ·  {item.filename}")

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------
    def _decide(self, action: str) -> None:
        if self._state is None or self._current is None:
            return
        item = self._current
        # 큐에서 제거
        try:
            self._state.queue.remove(item)
        except ValueError:
            pass
        if action == "verify":
            self._state.targets[item.slot].append(item)
        else:
            self._state.excluded[item.slot].append(item)
        self._state.history.append((action, item))
        self.decision_made.emit(action, item)
        self.state_changed.emit()
        self._advance_to_next()

    def _undo(self) -> None:
        if self._state is None or not self._state.history:
            return
        action, item = self._state.history.pop()
        if action == "verify":
            try:
                self._state.targets[item.slot].remove(item)
            except ValueError:
                pass
        else:
            try:
                self._state.excluded[item.slot].remove(item)
            except ValueError:
                pass
        # 큐 맨 앞에 되돌려 놓기
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
