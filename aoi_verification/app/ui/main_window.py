"""애플리케이션 메인 윈도우 — 전체 흐름 조정자(orchestrator).

다음 페이지를 StackedWidget 로 갈아끼우며 흐름을 관리한다.
1) SetupPage           → 입력
2) SelectPage          → Stage 1 (Phase A / Phase B 양쪽 모두에서 재사용)
3) MatchPage           → Stage 2 (방향만 바꿔 재사용)
4) ResultPage          → 결과 + 엑셀 저장

세션 자동 저장 / 이어하기, 단계 전환 모달, 진행 상태 라벨도 여기서 처리한다.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow,
                              QMessageBox, QStackedWidget, QVBoxLayout, QWidget)

from .. import config, i18n
from ..models import session as session_mod
from ..models.result import FinalResult, MatchResult, MissEntry
from ..models.slot import ImageItem, ScanResult, Slot, scan
from ..utils import paths
from ..workers.thumbnailer import ThumbnailWorker
from .pages.match_page import MatchPage
from .pages.result_page import ResultPage
from .pages.select_page import SelectPage
from .pages.setup_page import SetupInput, SetupPage
from .widgets.loading_overlay import LoadingOverlay


# ---------------------------------------------------------------------------
# Phase identifiers
# ---------------------------------------------------------------------------
PHASE_NONE = "none"
PHASE_A_SELECT = "A_select"
PHASE_A_MATCH = "A_match"
PHASE_B_SELECT = "B_select"
PHASE_B_MATCH = "B_match"


class MainWindow(QMainWindow):

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(i18n.KO.APP_TITLE)
        self.resize(1500, 940)

        self._stack = QStackedWidget(self)
        self.setCentralWidget(self._stack)

        # 페이지 ---------------------------------------------------------
        self._setup_page = SetupPage()
        self._select_page = SelectPage()
        self._match_page = MatchPage()
        self._result_page = ResultPage()

        for w in (self._setup_page, self._select_page,
                  self._match_page, self._result_page):
            self._stack.addWidget(w)

        # 시그널 ---------------------------------------------------------
        self._setup_page.start_requested.connect(self._on_start)
        self._select_page.finished.connect(self._on_select_finished)
        self._select_page.state_changed.connect(self._schedule_autosave)
        self._match_page.match_confirmed.connect(self._on_match_confirmed)
        self._match_page.skipped_changed.connect(self._schedule_autosave)
        self._match_page.finished.connect(self._on_match_finished)
        self._result_page.new_session_requested.connect(self._new_session)

        # 자동 저장 타이머 -----------------------------------------------
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(
            config.CONFIG.autosave_interval_s * 1000
        )
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()

        # 상태 -----------------------------------------------------------
        self._loading = LoadingOverlay(self)
        self._thumb_worker: Optional[ThumbnailWorker] = None
        self._scan: Optional[ScanResult] = None
        self._input: Optional[SetupInput] = None
        self._phase: str = PHASE_NONE
        self._matches_a: list[MatchResult] = []
        self._matches_b: list[MatchResult] = []
        self._skipped_a: dict[str, list[ImageItem]] = defaultdict(list)
        self._skipped_b: dict[str, list[ImageItem]] = defaultdict(list)
        self._stage1_a_snapshot: dict | None = None
        self._stage1_b_snapshot: dict | None = None
        self._matched_val_keys_in_a: set[str] = set()

        # 이어하기 ------------------------------------------------------
        QTimer.singleShot(50, self._maybe_resume)

    # ==================================================================
    # Entry / resume
    # ==================================================================
    def _maybe_resume(self) -> None:
        state = session_mod.load()
        if state is None or state.stage in ("setup", "result"):
            self._show_page(self._setup_page)
            return
        r = QMessageBox.question(
            self, i18n.KO.INFO_RESUME_TITLE, i18n.KO.INFO_RESUME_BODY,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if r != QMessageBox.StandardButton.Yes:
            session_mod.clear()
            self._show_page(self._setup_page)
            return

        # 입력 페이지에 값을 복원해두고 사용자가 검증 시작을 다시 누르도록 한다.
        # (스캔 결과/디렉토리 상태가 바뀌었을 수 있으므로 안전한 재시작.)
        self._setup_page.apply_state(
            ref_root=state.ref_root,
            val_root=state.val_root,
            ref_machine=state.ref_machine,
            val_machine=state.val_machine,
            mode=state.mode,
            threshold=state.threshold,
        )
        self._show_page(self._setup_page)

    # ==================================================================
    # Setup → Stage 1
    # ==================================================================
    def _on_start(self, inp: SetupInput) -> None:
        self._input = inp
        self._matches_a.clear()
        self._matches_b.clear()
        self._skipped_a.clear()
        self._skipped_b.clear()
        self._matched_val_keys_in_a.clear()

        self._loading.show_overlay(i18n.KO.LOAD_SCAN)
        QApplication.processEvents()

        # 폴더 스캔 (저비용 — 메인 스레드에서 진행)
        sr = scan(inp.ref_root, inp.val_root)
        self._scan = sr

        common = sr.common_slot_names
        if not common:
            self._loading.hide_overlay()
            QMessageBox.warning(self, i18n.KO.APP_TITLE, i18n.KO.WARN_NO_SLOTS)
            return

        # 한쪽 전용 Slot 알림 ---------------------------------------------
        if sr.ref_only or sr.val_only:
            QMessageBox.information(
                self, i18n.KO.WARN_SLOT_MISMATCH_TITLE,
                i18n.KO.WARN_SLOT_MISMATCH_FMT.format(
                    ref_only=", ".join(sr.ref_only) or "없음",
                    val_only=", ".join(sr.val_only) or "없음",
                ),
            )

        # 썸네일 캐시 사전 생성 (백그라운드) -----------------------------
        all_items: list[ImageItem] = []
        for name in common:
            slot = sr.slots[name]
            all_items.extend(slot.ref_images)
            all_items.extend(slot.val_images)

        self._loading.set_progress(
            0, len(all_items),
            i18n.KO.LOAD_THUMBNAIL_FMT.format(done=0, total=len(all_items)),
        )

        self._thumb_worker = ThumbnailWorker(all_items, also_mid=True, parent=self)
        self._thumb_worker.signals.progress.connect(
            lambda d, t, _p: self._loading.set_progress(
                d, t, i18n.KO.LOAD_THUMBNAIL_FMT.format(done=d, total=t),
            )
        )
        self._thumb_worker.signals.finished.connect(self._on_thumbs_ready)
        self._thumb_worker.start()

    def _on_thumbs_ready(self) -> None:
        self._loading.hide_overlay()
        # Phase 결정 → Stage 1 진입 -------------------------------------
        assert self._input is not None
        if self._input.mode == "single":
            self._phase = PHASE_A_SELECT
            self._enter_stage1_phase_a()
        else:
            self._phase = PHASE_A_SELECT
            self._enter_stage1_phase_a()

    # ==================================================================
    # Phase 식별 helpers
    # ==================================================================
    def _is_cross(self) -> bool:
        return self._input is not None and self._input.mode == "cross"

    def _lower_machine_side(self) -> str:
        """교차검증에서 '낮은 호기' 가 ref 인지 val 인지 추정 ('ref'/'val')."""
        if self._input is None:
            return "ref"
        # 호기명에서 숫자만 뽑아서 비교 → 실패하면 ref 우선
        def num(s: str) -> int:
            digits = "".join(ch for ch in s if ch.isdigit())
            return int(digits) if digits else 9999
        return "ref" if num(self._input.ref_machine) <= num(self._input.val_machine) else "val"

    # ==================================================================
    # Stage 1 — Phase A
    # ==================================================================
    def _enter_stage1_phase_a(self) -> None:
        assert self._scan is not None and self._input is not None
        # 낮은 호기 쪽이 Phase A 의 기준
        lower = self._lower_machine_side() if self._is_cross() else "ref"
        slots = [self._scan.slots[n] for n in self._scan.common_slot_names]
        # Phase A 의 queue: 낮은 호기 쪽 사진 전부 (Slot 명 / 파일명 오름차순)
        queue: list[ImageItem] = []
        for slot in sorted(slots, key=lambda s: s.name):
            queue.extend(slot.ref_images if lower == "ref" else slot.val_images)

        phase_lab = (
            i18n.KO.PHASE_A_SELECT if self._is_cross()
            else i18n.KO.STAGE1_TITLE
        )
        self._select_page.load_state(
            queue=queue,
            targets={}, excluded={}, history=[],
            phase_label=phase_lab,
        )
        self._show_page(self._select_page)
        self._phase = PHASE_A_SELECT
        self._autosave()

    def _on_select_finished(self) -> None:
        if self._phase == PHASE_A_SELECT:
            self._stage1_a_snapshot = {
                "targets": self._collect_panel(self._select_page.get_state().targets),
                "excluded": self._collect_panel(self._select_page.get_state().excluded),
            }
            QMessageBox.information(
                self, i18n.KO.INFO_PHASE_TRANSITION_TITLE,
                i18n.KO.INFO_PHASE_A_TO_MATCH,
            )
            self._enter_stage2_phase_a()
        elif self._phase == PHASE_B_SELECT:
            self._stage1_b_snapshot = {
                "targets": self._collect_panel(self._select_page.get_state().targets),
                "excluded": self._collect_panel(self._select_page.get_state().excluded),
            }
            QMessageBox.information(
                self, i18n.KO.INFO_PHASE_TRANSITION_TITLE,
                i18n.KO.INFO_PHASE_B_TO_MATCH,
            )
            self._enter_stage2_phase_b()

    @staticmethod
    def _collect_panel(
        panel: dict[str, list[ImageItem]]
    ) -> dict[str, list[ImageItem]]:
        return {k: list(v) for k, v in panel.items() if v}

    # ==================================================================
    # Stage 2 — Phase A
    # ==================================================================
    def _enter_stage2_phase_a(self) -> None:
        assert self._scan is not None and self._input is not None
        # Phase A 의 기준 큐 = Stage 1 에서 verify 로 분류된 낮은 호기 사진들
        lower = self._lower_machine_side() if self._is_cross() else "ref"
        targets = self._stage1_a_snapshot["targets"] if self._stage1_a_snapshot else {}
        queue: list[ImageItem] = []
        for slot in sorted(targets.keys()):
            queue.extend(targets[slot])

        # 매칭 대상 풀 = 같은 Slot 의 높은 호기 쪽 모든 사진
        higher = "val" if lower == "ref" else "ref"
        pool: dict[str, list[ImageItem]] = {}
        for name in self._scan.common_slot_names:
            slot = self._scan.slots[name]
            pool[name] = slot.val_images if higher == "val" else slot.ref_images

        direction = "A→B"
        phase_lab = i18n.KO.PHASE_A_MATCH if self._is_cross() else i18n.KO.STAGE2_TITLE
        self._match_page.load_state(
            queue=queue,
            val_pool_by_slot=pool,
            threshold=self._input.threshold,
            phase_label=phase_lab,
            direction=direction,
        )
        self._show_page(self._match_page)
        self._phase = PHASE_A_MATCH
        self._autosave()

    def _on_match_confirmed(self, match: MatchResult) -> None:
        if self._phase == PHASE_A_MATCH:
            self._matches_a.append(match)
            # Phase A 에서 매칭된 검증 쪽 파일을 기록 (Phase B 에서 자동 제외)
            self._matched_val_keys_in_a.add(self._val_key(match))
        elif self._phase == PHASE_B_MATCH:
            self._matches_b.append(match)
        self._schedule_autosave()

    @staticmethod
    def _val_key(match: MatchResult) -> str:
        return f"{match.slot}::{match.val_path.name}"

    def _on_match_finished(self) -> None:
        if self._phase == PHASE_A_MATCH:
            st = self._match_page.get_state()
            if st is not None:
                for slot, items in st.skipped.items():
                    self._skipped_a[slot].extend(items)
            if self._is_cross():
                QMessageBox.information(
                    self, i18n.KO.INFO_PHASE_TRANSITION_TITLE,
                    i18n.KO.INFO_PHASE_A_TO_B,
                )
                self._enter_stage1_phase_b()
            else:
                self._finish_session()
        elif self._phase == PHASE_B_MATCH:
            st = self._match_page.get_state()
            if st is not None:
                for slot, items in st.skipped.items():
                    self._skipped_b[slot].extend(items)
            self._finish_session()

    # ==================================================================
    # Stage 1 — Phase B (reverse direction)
    # ==================================================================
    def _enter_stage1_phase_b(self) -> None:
        assert self._scan is not None and self._input is not None
        lower = self._lower_machine_side()
        higher = "val" if lower == "ref" else "ref"

        # 큐 = 높은 호기의 모든 사진, 단 이미 Phase A 에서 매칭된 항목은 제외
        # 그리고 Phase 1 화면에는 "이미 매칭됨" 섹션으로 표시 가능하도록 전달.
        queue: list[ImageItem] = []
        already_by_slot: dict[str, list[ImageItem]] = defaultdict(list)
        for name in self._scan.common_slot_names:
            slot = self._scan.slots[name]
            higher_imgs = slot.val_images if higher == "val" else slot.ref_images
            for it in higher_imgs:
                if f"{name}::{it.path.name}" in self._matched_val_keys_in_a:
                    already_by_slot[name].append(it)
                else:
                    queue.append(it)

        self._select_page.load_state(
            queue=queue,
            targets={}, excluded={}, history=[],
            phase_label=i18n.KO.PHASE_B_SELECT,
            phase_b_already_matched=dict(already_by_slot),
        )
        self._show_page(self._select_page)
        self._phase = PHASE_B_SELECT
        self._autosave()

    # ==================================================================
    # Stage 2 — Phase B
    # ==================================================================
    def _enter_stage2_phase_b(self) -> None:
        assert self._scan is not None and self._input is not None
        lower = self._lower_machine_side()
        higher = "val" if lower == "ref" else "ref"
        targets = self._stage1_b_snapshot["targets"] if self._stage1_b_snapshot else {}

        queue: list[ImageItem] = []
        for slot in sorted(targets.keys()):
            queue.extend(targets[slot])

        # 매칭 대상 풀 = 같은 Slot 의 낮은 호기 사진들 (반대 방향)
        pool: dict[str, list[ImageItem]] = {}
        for name in self._scan.common_slot_names:
            slot = self._scan.slots[name]
            pool[name] = slot.ref_images if lower == "ref" else slot.val_images

        direction = "B→A"
        self._match_page.load_state(
            queue=queue,
            val_pool_by_slot=pool,
            threshold=self._input.threshold,
            phase_label=i18n.KO.PHASE_B_MATCH,
            direction=direction,
        )
        self._show_page(self._match_page)
        self._phase = PHASE_B_MATCH
        self._autosave()

    # ==================================================================
    # Result
    # ==================================================================
    def _finish_session(self) -> None:
        assert self._scan is not None and self._input is not None
        merged = self._merge_matches()
        miss_fast, miss_slow = self._compute_miss_lists()

        result = FinalResult(
            mode=self._input.mode,
            ref_machine=self._input.ref_machine,
            val_machine=self._input.val_machine,
            matches=merged,
            miss_fast=miss_fast,
            miss_slow=miss_slow,
            slot_only_ref=list(self._scan.ref_only),
            slot_only_val=list(self._scan.val_only),
        )
        template = paths.resource_path("양식.xlsx")
        self._result_page.show_result(
            result,
            template_path=template if Path(template).exists() else None,
        )
        QMessageBox.information(self, i18n.KO.APP_TITLE, i18n.KO.INFO_ALL_DONE)
        self._show_page(self._result_page)
        self._phase = PHASE_NONE
        session_mod.clear()

    def _merge_matches(self) -> list[MatchResult]:
        if not self._is_cross():
            return list(self._matches_a)

        # 키 = (Slot, 낮은호기 파일명, 높은호기 파일명).
        # Phase A: ref_path = 낮은호기 / val_path = 높은호기
        # Phase B: ref_path = 높은호기 / val_path = 낮은호기  ← 이걸 정규화한다.
        lower = self._lower_machine_side()
        higher = "val" if lower == "ref" else "ref"

        def _norm(m: MatchResult) -> tuple[str, str, str, MatchResult]:
            # Phase A 는 그대로, Phase B 는 ref/val 을 뒤집어 정규화
            if m.direction == "A→B":
                low_path = m.ref_path
                high_path = m.val_path
            else:
                low_path = m.val_path
                high_path = m.ref_path
            norm = MatchResult(
                slot=m.slot,
                ref_path=low_path,
                val_path=high_path,
                score=m.score,
                direction=m.direction,
            )
            return (m.slot, low_path.name, high_path.name, norm)

        bag: dict[tuple[str, str, str], MatchResult] = {}
        for m in self._matches_a + self._matches_b:
            k0, k1, k2, norm = _norm(m)
            key = (k0, k1, k2)
            if key in bag:
                bag[key].direction = "양방향"
            else:
                bag[key] = norm
        return sorted(bag.values(), key=lambda x: (x.slot, x.ref_path.name.lower()))

    def _compute_miss_lists(self) -> tuple[list[MissEntry], list[MissEntry]]:
        if not self._is_cross():
            return [], []
        # 빠른(낮은) 호기 미탐 = Phase A 에서 skip 된 사진들
        miss_fast: list[MissEntry] = []
        for slot, items in self._skipped_a.items():
            for it in items:
                miss_fast.append(MissEntry(
                    slot=slot, side="ref", path=it.path,
                    note="Phase A 매칭 실패",
                ))
        # 느린(높은) 호기 미탐 = Phase B 에서 skip 된 사진들
        miss_slow: list[MissEntry] = []
        for slot, items in self._skipped_b.items():
            for it in items:
                miss_slow.append(MissEntry(
                    slot=slot, side="val", path=it.path,
                    note="Phase B 매칭 실패",
                ))
        return miss_fast, miss_slow

    def _new_session(self) -> None:
        session_mod.clear()
        self._matches_a.clear()
        self._matches_b.clear()
        self._skipped_a.clear()
        self._skipped_b.clear()
        self._matched_val_keys_in_a.clear()
        self._stage1_a_snapshot = None
        self._stage1_b_snapshot = None
        self._phase = PHASE_NONE
        self._show_page(self._setup_page)

    # ==================================================================
    # Page switching
    # ==================================================================
    def _show_page(self, w: QWidget) -> None:
        self._stack.setCurrentWidget(w)

    # ==================================================================
    # Auto-save
    # ==================================================================
    def _schedule_autosave(self) -> None:
        # 결정이 있을 때마다 즉시 저장한다 (가벼움)
        self._autosave()

    def _autosave(self) -> None:
        if self._input is None:
            return
        state = session_mod.SessionState(
            mode=self._input.mode,
            ref_root=str(self._input.ref_root),
            val_root=str(self._input.val_root),
            ref_machine=self._input.ref_machine,
            val_machine=self._input.val_machine,
            threshold=self._input.threshold,
            stage=self._phase or "setup",
            phase=("B" if self._phase in (PHASE_B_SELECT, PHASE_B_MATCH) else "A"),
        )
        try:
            session_mod.save(state)
        except Exception:
            pass

    # ==================================================================
    # Cleanup
    # ==================================================================
    def closeEvent(self, event):  # noqa: N802
        if self._thumb_worker is not None and self._thumb_worker.isRunning():
            self._thumb_worker.stop()
            self._thumb_worker.wait(1000)
        super().closeEvent(event)
