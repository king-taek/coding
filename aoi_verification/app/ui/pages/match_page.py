"""Stage 2 — 유사도 기반 매칭 화면.

좌: Skip 된 사진 누적 / 중앙: 기준 사진 1장 / 우: 검증 장비 후보 (점수 정렬)
9장 이상이면 8장 + +N. 우측 사진 클릭 → 매칭 확정.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
                              QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget)

from ... import config, i18n
from ...models.result import MatchResult
from ...models.slot import ImageItem, Slot
from ...utils import image_io
from ...utils import prefs as _prefs
from ...workers.matcher import Candidate, MatcherWorker
from ..widgets.loading_overlay import LoadingOverlay
from ..widgets.neon_button import NeonButton
from ..widgets.neon_card import NeonCard
from ..widgets.scalable_image import ScalableImage
from ..widgets.slot_section import SlotSection
from ..widgets.thumb_grid import ThumbEntry, ThumbGrid
from ..widgets.zoom_window import (ZoomWindow, SOURCE_TARGET, SOURCE_CANDIDATES)


# ---------------------------------------------------------------------------
@dataclass
class Stage2State:
    queue: list[ImageItem]                        # 매칭해야 할 기준 사진 큐
    matches: list[MatchResult] = field(default_factory=list)
    # ‘잠시 보류’ — Skip 재시도 대상
    skipped: dict[str, list[ImageItem]] = field(default_factory=lambda: defaultdict(list))
    # ‘매칭 없음 확정’ — 미탐으로 영구 기록 (재시도 대상 아님)
    no_match: dict[str, list[ImageItem]] = field(default_factory=lambda: defaultdict(list))
    # slot → 매칭에 사용할 검증 후보 풀
    val_pool: dict[str, list[ImageItem]] = field(default_factory=dict)


class MatchPage(QWidget):
    """Stage 2 의 메인 페이지."""

    match_confirmed = pyqtSignal(object)        # MatchResult
    skipped_changed = pyqtSignal()              # 외부 자동 저장 트리거
    finished = pyqtSignal()                     # 모든 큐 처리 완료

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._state: Stage2State | None = None
        self._current: Optional[ImageItem] = None
        self._threshold = config.CONFIG.default_threshold
        self._mode_direction = "A→B"
        self._build()
        self._loading = LoadingOverlay(self)
        self._worker: Optional[MatcherWorker] = None
        self._candidates: list[Candidate] = []

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        top = QHBoxLayout()
        self.title = QLabel(i18n.KO.STAGE2_TITLE, self)
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

        # 3 pane --------------------------------------------------------
        row = QHBoxLayout()
        row.setSpacing(10)

        # LEFT: skip pool
        left = QFrame(self)
        left.setProperty("role", "section")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(10, 10, 10, 10)
        ll.setSpacing(8)
        lt = QLabel(i18n.KO.PANEL_SKIP_LIST, left)
        lt.setProperty("role", "subtitle")
        lt.setStyleSheet("font-weight:700; color:#00D4FF;")
        ll.addWidget(lt)

        head = QHBoxLayout()
        head.addStretch(1)
        self.retry_btn = NeonButton(i18n.KO.BTN_RETRY_SKIP, role="warn")
        self.retry_btn.setEnabled(False)
        self.retry_btn.clicked.connect(self._retry_skipped)
        head.addWidget(self.retry_btn)
        ll.addLayout(head)

        self._left_scroll = QScrollArea(left)
        self._left_scroll.setWidgetResizable(True)
        self._left_host = QWidget()
        self._left_layout = QVBoxLayout(self._left_host)
        self._left_layout.setSpacing(10)
        self._left_layout.addStretch(1)
        self._left_scroll.setWidget(self._left_host)
        ll.addWidget(self._left_scroll, stretch=1)
        left.setMinimumWidth(260)
        row.addWidget(left, stretch=2)

        # CENTER: 기준 사진 1장
        center = NeonCard(role="card", parent=self)
        cl = center.body()
        title = QLabel(i18n.KO.PANEL_MATCH_REF, center)
        title.setProperty("role", "subtitle")
        title.setStyleSheet("font-weight:700; color:#00D4FF;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(title)

        # Slot 명만 표시 (파일명 미표시) -------------------------------
        self.slot_label = QLabel("", center)
        self.slot_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.slot_label.setStyleSheet(
            "color: #7FB3D5; font-size: 14px; font-weight: 600; padding: 2px;"
        )
        cl.addWidget(self.slot_label)

        # 사진 크기 슬라이더 -------------------------------------------
        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        size_label = QLabel(i18n.KO.IMAGE_SIZE_LABEL, center)
        size_label.setProperty("role", "muted")
        self.size_slider = QSlider(Qt.Orientation.Horizontal, center)
        self.size_slider.setRange(ScalableImage.MIN_LONG_EDGE,
                                   ScalableImage.MAX_LONG_EDGE)
        _p = _prefs.load()
        self.size_slider.setValue(
            max(ScalableImage.MIN_LONG_EDGE,
                min(ScalableImage.MAX_LONG_EDGE,
                    int(_p.image_long_edge_match)))
        )
        self.size_slider.setSingleStep(20)
        self.size_slider.setPageStep(80)
        self.size_value = QLabel(f"{self.size_slider.value()} px", center)
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
        self.center_img = ScalableImage(center)
        self._img_scroll = QScrollArea(center)
        self._img_scroll.setWidgetResizable(False)
        self._img_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_scroll.setWidget(self.center_img)
        self._img_scroll.setStyleSheet(
            "QScrollArea { background: #050810; border: 1px solid #1F2A3F; "
            "border-radius: 8px; }"
        )
        self._img_scroll.setMinimumHeight(360)
        self._img_scroll.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Expanding)
        cl.addWidget(self._img_scroll, stretch=1)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 6, 0, 0)
        self.skip_btn = NeonButton(i18n.KO.BTN_SKIP, role="warn")
        self.skip_btn.setToolTip(i18n.KO.SHORTCUT_STAGE2_TOOLTIP)
        self.skip_btn.clicked.connect(self._skip_current)
        self.no_match_btn = NeonButton(i18n.KO.BTN_NO_MATCH, role="danger")
        self.no_match_btn.setToolTip(i18n.KO.SHORTCUT_STAGE2_TOOLTIP)
        self.no_match_btn.clicked.connect(self._confirm_no_match)
        bar.addStretch(1)
        bar.addWidget(self.skip_btn)
        bar.addWidget(self.no_match_btn)
        cl.addLayout(bar)

        center.setMinimumWidth(540)
        row.addWidget(center, stretch=4)

        # RIGHT: 후보들
        right = QFrame(self)
        right.setProperty("role", "section")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(10, 10, 10, 10)
        rl.setSpacing(8)
        rt = QLabel(i18n.KO.PANEL_MATCH_CANDIDATES, right)
        rt.setProperty("role", "subtitle")
        rt.setStyleSheet("font-weight:700; color:#00D4FF;")
        rl.addWidget(rt)

        self._right_scroll = QScrollArea(right)
        self._right_scroll.setWidgetResizable(True)
        self._right_host = QWidget()
        self._right_grid = QGridLayout(self._right_host)
        self._right_grid.setContentsMargins(4, 4, 4, 4)
        self._right_grid.setSpacing(8)
        self._right_scroll.setWidget(self._right_host)
        rl.addWidget(self._right_scroll, stretch=1)
        right.setMinimumWidth(420)
        row.addWidget(right, stretch=3)

        root.addLayout(row, stretch=1)

        QShortcut(QKeySequence("S"), self, activated=self._skip_current)
        QShortcut(QKeySequence("N"), self, activated=self._confirm_no_match)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load_state(self,
                   queue: list[ImageItem],
                   val_pool_by_slot: dict[str, list[ImageItem]],
                   threshold: float,
                   *,
                   matches: list[MatchResult] | None = None,
                   skipped: dict[str, list[ImageItem]] | None = None,
                   phase_label: str = "",
                   direction: str = "A→B",
                   session_id: str = "",
                   model_name: str = "basic") -> None:
        self._state = Stage2State(
            queue=list(queue),
            matches=list(matches or []),
            skipped=defaultdict(list, {k: list(v) for k, v in (skipped or {}).items()}),
            val_pool={k: list(v) for k, v in val_pool_by_slot.items()},
        )
        self._threshold = threshold
        self._mode_direction = direction
        self._session_id = session_id or ""
        self._model_name = model_name or "basic"
        self.phase_label.setText(phase_label)
        self._refresh_skipped_panel()
        self._advance()

    def get_state(self) -> Stage2State | None:
        return self._state

    # ------------------------------------------------------------------
    def _advance(self) -> None:
        if self._state is None:
            return
        if not self._state.queue:
            self._current = None
            self.center_img.clear_image()
            self.slot_label.setText("")
            self._clear_right_grid()
            self.finished.emit()
            return

        self._current = self._state.queue[0]
        self._show_center(self._current)
        self.progress_label.setText(
            i18n.KO.PROGRESS_SLOT_FMT.format(
                slot=self._current.slot,
                done=len(self._state.matches),
                total=len(self._state.matches) + len(self._state.queue),
            )
        )

        val_items = self._state.val_pool.get(self._current.slot, [])
        if not val_items:
            QMessageBox.information(self, i18n.KO.APP_TITLE,
                                    i18n.KO.INFO_NO_MATCH_FOUND)
            self._skip_current()
            return

        self._launch_matcher(self._current, val_items)

    def _launch_matcher(self,
                        ref: ImageItem,
                        val_items: list[ImageItem]) -> None:
        self._clear_right_grid()
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(500)

        self._loading.show_overlay(
            i18n.KO.LOAD_FEATURE_FMT.format(done=0, total=len(val_items))
        )

        self._worker = MatcherWorker(ref, val_items, threshold=self._threshold)
        self._worker.signals.progress.connect(self._on_matcher_progress)
        self._worker.signals.done.connect(self._on_matcher_done)
        self._worker.signals.failed.connect(
            lambda msg: self._loading.set_progress(0, 0, msg)
        )
        self._worker.start()

    def _on_matcher_progress(self, done: int, total: int) -> None:
        self._loading.set_progress(
            done, total,
            i18n.KO.LOAD_FEATURE_FMT.format(done=done, total=total),
        )

    def _on_matcher_done(self, candidates: list) -> None:
        self._loading.hide_overlay()
        self._candidates = list(candidates)
        if not self._candidates:
            QMessageBox.information(self, i18n.KO.APP_TITLE,
                                    i18n.KO.INFO_NO_MATCH_FOUND)
            self._skip_current()
            return
        self._populate_right(self._candidates)

    # ------------------------------------------------------------------
    def _show_center(self, item: ImageItem) -> None:
        self.center_img.set_image(item.path)
        # Stage 2 도 파일명은 미표시, Slot 만 노출 (요청 사항).
        self.slot_label.setText(i18n.KO.SLOT_LABEL_FMT.format(slot=item.slot))

    def _on_size_changed(self, value: int) -> None:
        self.size_value.setText(f"{value} px")
        self.center_img.set_target_size(value)
        _prefs.patch(image_long_edge_match=int(value))

    def _clear_right_grid(self) -> None:
        while self._right_grid.count():
            it = self._right_grid.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

    def _populate_right(self, candidates: list[Candidate]) -> None:
        self._clear_right_grid()
        visible = candidates[: config.CONFIG.match_top_visible]
        extra = len(candidates) - len(visible)

        grid = ThumbGrid(columns=3, select_mode=False, truncate=False,
                         parent=self._right_host)
        entries = [ThumbEntry(item=c.item, extra={"score": c.score}) for c in visible]
        grid.set_entries(entries)
        grid.tile_clicked.connect(self._on_pick)
        self._right_grid.addWidget(grid, 0, 0)

        if extra > 0:
            # +N 카드 — 클릭 시 줌 윈도우로 전체 표시
            from ..widgets.thumb_grid import _PlusTile
            plus = _PlusTile(extra, parent=self._right_host)
            plus.clicked.connect(self._open_all_candidates)
            self._right_grid.addWidget(plus, 0, 1)

    def _on_pick(self, entry: ThumbEntry) -> None:
        if self._state is None or self._current is None:
            return
        ref = self._current
        val = entry.item
        score = float(entry.extra.get("score", 0.0)) if entry.extra else 0.0
        match = MatchResult(
            slot=ref.slot,
            ref_path=ref.path,
            val_path=val.path,
            score=score,
            direction=self._mode_direction,    # type: ignore[arg-type]
        )
        self._state.matches.append(match)
        self._state.queue.pop(0)
        self._log_decision(decision="pick", picked_item=val)
        self.match_confirmed.emit(match)
        self.skipped_changed.emit()
        self._advance()

    def _open_all_candidates(self) -> None:
        if self._current is None:
            return
        items = [c.item for c in self._candidates]
        win = ZoomWindow(self._current.slot, items, SOURCE_CANDIDATES, parent=self)
        win.action_requested.connect(self._on_zoom_candidates_action)
        win.exec()

    def _on_zoom_candidates_action(self, action: str, items: list[ImageItem]) -> None:
        """줌-뷰에서 ‘이 사진을 매칭으로 확정’ 을 누른 경우 → _on_pick 흐름 재사용."""
        if action != "pick" or not items:
            return
        picked = items[0]
        # 후보 리스트에서 해당 item 의 ThumbEntry 를 만들어 _on_pick 으로 위임
        score = 0.0
        for c in self._candidates:
            if c.item.path == picked.path:
                score = float(c.score)
                break
        self._on_pick(ThumbEntry(item=picked, extra={"score": score}))

    # ------------------------------------------------------------------
    def _skip_current(self) -> None:
        """잠시 보류 — Skip 재시도 풀로 들어감. 미탐 시트엔 들어가지 않음."""
        if self._state is None or self._current is None:
            return
        item = self._current
        self._state.queue.pop(0)
        self._state.skipped[item.slot].append(item)
        self._log_decision(decision="defer")
        self._refresh_skipped_panel()
        self.skipped_changed.emit()
        self._advance()

    def _confirm_no_match(self) -> None:
        """매칭 없음 확정 — 미탐 시트에 들어가고, Skip 재시도 대상이 아님."""
        if self._state is None or self._current is None:
            return
        item = self._current
        self._state.queue.pop(0)
        self._state.no_match[item.slot].append(item)
        self._log_decision(decision="none")
        self._refresh_skipped_panel()
        self.skipped_changed.emit()
        self._advance()

    # ------------------------------------------------------------------
    def _log_decision(self, *, decision: str, picked_item=None) -> None:
        """Evaluator 로 한 결정 로그 append (실패는 무시).

        decision: "pick" | "defer" | "none"
        """
        if self._state is None or self._current is None:
            return
        try:
            from ...learning import evaluator as _ev
        except Exception:
            return
        candidates = [(c.item.path, float(c.score)) for c in self._candidates]
        if decision == "pick" and picked_item is not None:
            picked_path = picked_item.path
            rank = None
            for i, (p, _) in enumerate(candidates):
                if p == picked_item.path:
                    rank = i
                    break
        else:
            picked_path = None
            rank = None
        _ev.log_decision(
            model_name=self._model_name or "basic",
            session_id=self._session_id or "",
            slot=self._current.slot,
            ref_path=self._current.path,
            threshold=self._threshold,
            candidates=candidates,
            picked_path=picked_path,
            picked_rank=rank,
            decision=decision,
        )

    def _refresh_skipped_panel(self) -> None:
        # clear
        while self._left_layout.count():
            it = self._left_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

        if self._state is None:
            self.retry_btn.setEnabled(False)
            return
        has_any = False

        def _add_header(text: str) -> None:
            from PyQt6.QtWidgets import QLabel
            lab = QLabel(text, self._left_host)
            lab.setStyleSheet(
                "color: #7FB3D5; font-weight: 700; padding: 6px 2px;"
            )
            self._left_layout.addWidget(lab)

        # ‘잠시 보류’ 섹션 ------------------------------------------------
        defer_slots = [s for s, v in self._state.skipped.items() if v]
        if defer_slots:
            _add_header(i18n.KO.BTN_SKIP)
            for slot in sorted(defer_slots):
                items = self._state.skipped[slot]
                has_any = True
                sec = SlotSection(slot, columns=3, select_mode=False,
                                  parent=self._left_host)
                sec.set_entries([ThumbEntry(item=it) for it in items])
                self._left_layout.addWidget(sec)

        # ‘매칭 없음 확정’ 섹션 -------------------------------------------
        none_slots = [s for s, v in self._state.no_match.items() if v]
        if none_slots:
            _add_header(i18n.KO.PANEL_NO_MATCH_LIST)
            for slot in sorted(none_slots):
                items = self._state.no_match[slot]
                sec = SlotSection(slot, columns=3, select_mode=False,
                                  parent=self._left_host)
                sec.set_entries([ThumbEntry(item=it) for it in items])
                self._left_layout.addWidget(sec)

        self._left_layout.addStretch(1)
        # ‘보류 재시도’ 는 defer 만 활성 — none 은 영구 미탐
        self.retry_btn.setEnabled(has_any)

    def _retry_skipped(self) -> None:
        """Skip 된 항목들을 큐 앞으로 다시 밀어넣고, 임계치를 낮춰 재시도."""
        if self._state is None:
            return
        flat: list[ImageItem] = []
        for slot, items in self._state.skipped.items():
            flat.extend(items)
        self._state.skipped.clear()
        # 임계치 70% → 55% 로 한 단계 완화 (한 번에 0.15 씩)
        self._threshold = max(0.0, self._threshold - 0.15)
        # 큐 앞에 삽입
        self._state.queue = flat + self._state.queue
        self._refresh_skipped_panel()
        self._advance()
