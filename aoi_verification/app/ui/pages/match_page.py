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
                              QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ... import config, i18n
from ...models.result import MatchResult
from ...models.slot import ImageItem, Slot
from ...utils import image_io
from ...workers.matcher import Candidate, MatcherWorker
from ..widgets.loading_overlay import LoadingOverlay
from ..widgets.neon_button import NeonButton
from ..widgets.neon_card import NeonCard
from ..widgets.slot_section import SlotSection
from ..widgets.thumb_grid import ThumbEntry, ThumbGrid
from ..widgets.zoom_window import (ZoomWindow, SOURCE_TARGET, SOURCE_CANDIDATES)


# ---------------------------------------------------------------------------
@dataclass
class Stage2State:
    queue: list[ImageItem]                        # 매칭해야 할 기준 사진 큐
    matches: list[MatchResult] = field(default_factory=list)
    skipped: dict[str, list[ImageItem]] = field(default_factory=lambda: defaultdict(list))
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

        self.center_img = QLabel(center)
        self.center_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.center_img.setMinimumSize(520, 520)
        self.center_img.setSizePolicy(QSizePolicy.Policy.Expanding,
                                      QSizePolicy.Policy.Expanding)
        self.center_img.setStyleSheet(
            "background:#050810; border:1px solid #1F2A3F; border-radius:8px;"
        )
        cl.addWidget(self.center_img, stretch=1)

        self.center_caption = QLabel("", center)
        self.center_caption.setProperty("role", "muted")
        self.center_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(self.center_caption)

        bar = QHBoxLayout()
        self.skip_btn = NeonButton(i18n.KO.BTN_SKIP, role="warn")
        self.skip_btn.setToolTip(i18n.KO.SHORTCUT_STAGE2_TOOLTIP)
        self.skip_btn.clicked.connect(self._skip_current)
        bar.addStretch(1)
        bar.addWidget(self.skip_btn)
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
                   direction: str = "A→B") -> None:
        self._state = Stage2State(
            queue=list(queue),
            matches=list(matches or []),
            skipped=defaultdict(list, {k: list(v) for k, v in (skipped or {}).items()}),
            val_pool={k: list(v) for k, v in val_pool_by_slot.items()},
        )
        self._threshold = threshold
        self._mode_direction = direction
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
            self.center_img.clear()
            self.center_caption.setText("")
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
        try:
            mid = image_io.get_mid_path(item.path)
            pix = QPixmap(str(mid))
        except Exception:
            pix = QPixmap(560, 560)
            pix.fill(QColor(8, 16, 32))
        if pix.isNull():
            pix = QPixmap(560, 560)
            pix.fill(QColor(8, 16, 32))
        pix = pix.scaled(
            self.center_img.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.center_img.setPixmap(pix)
        self.center_caption.setText(f"{item.slot}  ·  {item.filename}")

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
        self.match_confirmed.emit(match)
        self.skipped_changed.emit()
        self._advance()

    def _open_all_candidates(self) -> None:
        if self._current is None:
            return
        items = [c.item for c in self._candidates]
        win = ZoomWindow(self._current.slot, items, SOURCE_CANDIDATES, parent=self)
        # 후보 윈도우는 액션이 없고 단순 검토용 (선택해 picked 처리하고 싶은 경우)
        # 더블 클릭으로 풀스크린만 가능
        win.exec()

    # ------------------------------------------------------------------
    def _skip_current(self) -> None:
        if self._state is None or self._current is None:
            return
        item = self._current
        self._state.queue.pop(0)
        self._state.skipped[item.slot].append(item)
        self._refresh_skipped_panel()
        self.skipped_changed.emit()
        self._advance()

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
        for slot in sorted(self._state.skipped.keys()):
            items = self._state.skipped[slot]
            if not items:
                continue
            has_any = True
            sec = SlotSection(slot, columns=3, select_mode=False,
                              parent=self._left_host)
            sec.set_entries([ThumbEntry(item=it) for it in items])
            self._left_layout.addWidget(sec)
        self._left_layout.addStretch(1)
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
