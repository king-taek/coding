"""애플리케이션 메인 윈도우 — 전체 흐름 조정자(orchestrator).

다음 페이지를 StackedWidget 로 갈아끼우며 흐름을 관리한다.
1) SetupPage           → 입력
2) SelectPage          → Stage 1 (Phase A / Phase B 양쪽 모두에서 재사용)
3) MatchPage           → Stage 2 (방향만 바꿔 재사용)
4) ResultPage          → 결과 + 엑셀 저장

세션 자동 저장 / 이어하기, 단계 전환 모달, 진행 상태 라벨도 여기서 처리한다.
"""

from __future__ import annotations

import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow,
                              QMessageBox, QStackedWidget, QStatusBar,
                              QVBoxLayout, QWidget)

from .. import config, i18n
from ..models import session as session_mod
from ..models.result import FinalResult, MatchResult, MissEntry
from ..models.slot import ImageItem, ScanResult, Slot, scan
from ..utils import paths
from ..utils import prefs as _prefs
from ..workers.thumbnailer import (PRIORITY_ACTIVE_SLOT, PRIORITY_BACKGROUND,
                                     ThumbnailPool, ThumbnailWorker)
from .pages.match_page import MatchPage
from .pages.result_page import ResultPage
from .pages.select_page import SelectPage
from .pages.setup_page import SetupInput, SetupPage
from .widgets.loading_overlay import LoadingOverlay
from .widgets.window_size_dialog import (MIN_HEIGHT, MIN_WIDTH,
                                           WindowSizeDialog,
                                           suggest_default_size)


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
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        self._apply_saved_window_size()

        self._stack = QStackedWidget(self)
        self.setCentralWidget(self._stack)

        # 상태 바 + 메모리 표시 (psutil 가용 시) ----------------------------
        self._status_bar = QStatusBar(self)
        self.setStatusBar(self._status_bar)
        self._mem_label = QLabel("", self._status_bar)
        self._mem_label.setProperty("role", "muted")
        self._status_bar.addPermanentWidget(self._mem_label)
        self._mem_timer = QTimer(self)
        self._mem_timer.setInterval(2000)
        self._mem_timer.timeout.connect(self._update_memory_label)
        self._mem_pressure_shown = False
        try:
            import psutil  # noqa: F401
            self._mem_timer.start()
            self._update_memory_label()
        except Exception:
            pass

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
        self._setup_page.window_size_requested.connect(self._open_window_size_dialog)
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
        self._thumb_pool: Optional[ThumbnailPool] = None
        self._sizing_tier: Optional[config.SizingTier] = None
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
        self._working_xlsx: Optional[Path] = None
        self._template_used: Optional[Path] = None
        self._session_id: str = ""

        # 이어하기 ------------------------------------------------------
        QTimer.singleShot(50, self._maybe_resume)

    # ==================================================================
    # 메모리 사용량 표시
    # ==================================================================
    def _update_memory_label(self) -> None:
        try:
            import psutil
            rss = psutil.Process().memory_info().rss
        except Exception:
            return
        self._mem_label.setText(
            i18n.KO.MEMORY_USAGE_FMT.format(mb=int(rss / (1024 * 1024)))
        )
        # 한도 초과 시 단발 토스트.
        if rss > config.MEMORY_PRESSURE_BYTES and not self._mem_pressure_shown:
            self._mem_pressure_shown = True
            self._status_bar.showMessage(
                i18n.KO.MEMORY_PRESSURE_TOAST, 4000
            )
        elif rss < int(config.MEMORY_PRESSURE_BYTES * 0.9):
            # 압박이 해제되면 다시 알릴 수 있도록 플래그 재설정.
            self._mem_pressure_shown = False

    # ==================================================================
    # 창 크기 — 사용자 선택값 복원 / 모달
    # ==================================================================
    def _apply_saved_window_size(self) -> None:
        """저장된 크기를 복원하거나, 없으면 모니터 영역에 맞춰 추천."""
        p = _prefs.load()
        if p.fullscreen:
            # 우선 합리적인 normal 크기로 resize 한 뒤 fullscreen 진입 — 토글 시
            # 자연스러운 복귀 크기 확보.
            dw, dh = suggest_default_size()
            self.resize(dw, dh)
            self.showFullScreen()
            return
        if p.window_width >= MIN_WIDTH and p.window_height >= MIN_HEIGHT:
            self.resize(p.window_width, p.window_height)
            return
        # 미설정 → 모니터 영역에 맞는 권장값.
        dw, dh = suggest_default_size()
        self.resize(dw, dh)

    def _open_window_size_dialog(self) -> None:
        from PyQt6.QtWidgets import QDialog
        p = _prefs.load()
        dlg = WindowSizeDialog(
            current_width=p.window_width,
            current_height=p.window_height,
            current_fullscreen=p.fullscreen,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        choice = dlg.chosen()
        _prefs.patch(
            window_width=int(choice.width),
            window_height=int(choice.height),
            fullscreen=bool(choice.fullscreen),
        )
        if choice.fullscreen:
            self.showFullScreen()
        else:
            if self.isFullScreen():
                self.showNormal()
            self.resize(int(choice.width), int(choice.height))

    # ==================================================================
    # Entry / resume
    # ==================================================================
    def _maybe_resume(self) -> None:
        # 셋업 진입 시 항상 정확도 최신화 + 모델 카드 갱신
        self._refresh_models_safe()

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

    def _refresh_models_safe(self) -> None:
        """학습 모듈 import / 평가 집계 실패가 셋업 화면을 막지 않도록 wrap."""
        # 첫 실행에서 active.txt 가 없으면 latest.txt 로 fallback (스펙 §8.2-c).
        try:
            from ..learning import registry as _reg
            _reg.apply_latest_if_active_unset()
        except Exception:
            pass
        try:
            from ..learning import evaluator as _ev
            _ev.refresh_accuracy()
        except Exception:
            pass
        try:
            self._setup_page.refresh_models()
        except Exception:
            pass

    def _active_model_name(self) -> str:
        """현재 active 모델 이름 (없으면 ``basic``)."""
        try:
            from ..learning import registry as _reg
            return _reg.get_active()
        except Exception:
            return "basic"

    def _resolve_slot_mismatch(self, sr: ScanResult) -> None:
        """ref/val 한쪽에만 있는 슬롯이 있을 때 사용자에게 매핑을 묻는다 (#23)."""
        from .widgets.slot_mapping_dialog import SlotMappingDialog
        # 안내 → 다이얼로그 열기 여부 묻기
        r = QMessageBox.question(
            self, i18n.KO.WARN_SLOT_MISMATCH_TITLE,
            i18n.KO.WARN_SLOT_MISMATCH_FMT.format(
                ref_only=", ".join(sr.ref_only) or "없음",
                val_only=", ".join(sr.val_only) or "없음",
            ) + "\n\n" + i18n.KO.SLOT_MAP_OPEN + " ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if r != QMessageBox.StandardButton.Yes:
            return

        from PyQt6.QtWidgets import QDialog
        dlg = SlotMappingDialog(sr.ref_only, sr.val_only, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        mapping = dlg.mapping
        if not mapping.pairs:
            return

        # 매핑된 슬롯 쌍을 ScanResult.slots 에 통합 (val 측을 ref 측 이름으로 합침)
        for ref_name, val_name in mapping.pairs:
            ref_slot = sr.slots.get(ref_name)
            val_slot = sr.slots.get(val_name)
            if ref_slot is None or val_slot is None:
                continue
            # val 사진을 ref 슬롯에 결합 — side 는 유지하되 slot 명만 일치시킴.
            from ..models.slot import ImageItem
            rebuilt_val_imgs = [
                ImageItem(slot=ref_name, path=it.path, side="val")
                for it in val_slot.val_images
            ]
            ref_slot.val_images = rebuilt_val_imgs
            # 매핑 적용된 val 슬롯은 제거
            sr.slots.pop(val_name, None)

        # ref_only / val_only 목록 갱신
        sr.ref_only = [s for s in sr.ref_only
                       if s not in {a for a, _ in mapping.pairs}]
        sr.val_only = [s for s in sr.val_only
                       if s not in {b for _, b in mapping.pairs}]

    def _make_groups(self, items: list[ImageItem]):
        """ImageItem 목록을 슬롯별로 pHash 그룹화 (#15)."""
        try:
            from ..models import group as _grp
            from ..utils import prefs as _prefs
        except Exception:
            return None
        if not items:
            return None
        by_slot: dict[str, list[ImageItem]] = defaultdict(list)
        for it in items:
            by_slot[it.slot].append(it)
        p = _prefs.load()
        try:
            return _grp.cluster(
                by_slot,
                similarity_threshold=float(p.group_similarity),
                min_group_size=int(p.group_min_size),
            )
        except Exception:
            return None

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
        self._session_id = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

        # 양식 폴더의 양식.xlsx 를 결과 폴더로 복사 → 작업 파일 준비 ----
        self._prepare_working_file(inp)

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

        # 한쪽 전용 Slot 이 있으면 사용자에게 수동 매핑을 물어본다 (#23) ------
        if sr.ref_only or sr.val_only:
            self._resolve_slot_mismatch(sr)

        # 썸네일 캐시 사전 생성 (백그라운드) -----------------------------
        all_items: list[ImageItem] = []
        for name in common:
            slot = sr.slots[name]
            all_items.extend(slot.ref_images)
            all_items.extend(slot.val_images)

        # 이미지 수에 따라 화질 티어 자동 선택 (사용자 강제 빠른 모드 우선).
        _ui = _prefs.load()
        per_side_total = max(
            sum(len(sr.slots[n].ref_images) for n in common),
            sum(len(sr.slots[n].val_images) for n in common),
        )
        self._sizing_tier = config.pick_tier(
            per_side_total, speed_mode=bool(_ui.speed_mode)
        )

        # 기본 티어보다 낮은 화질이 적용되면 한 번만 안내.
        if self._sizing_tier is not config.SIZING_TIERS[0]:
            self._loading.set_progress(
                0, len(all_items),
                i18n.KO.SIZE_TIER_NOTICE_FMT.format(
                    thumb=self._sizing_tier.thumb_px,
                    q=self._sizing_tier.thumb_q,
                ),
            )

        self._loading.set_progress(
            0, len(all_items),
            i18n.KO.LOAD_THUMBNAIL_FMT.format(done=0, total=len(all_items)),
        )

        # 다중 스레드 + 우선순위 큐 풀 사용. 첫 슬롯 (사전식으로 가장 앞)
        # 의 작업을 ACTIVE_SLOT 우선순위로 끌어올린다.
        if self._thumb_pool is not None:
            self._thumb_pool.stop()
        self._thumb_pool = ThumbnailPool(
            tier=self._sizing_tier, also_mid=True, parent=self,
        )
        self._thumb_pool.enqueue(all_items, priority=PRIORITY_BACKGROUND)
        if common:
            self._thumb_pool.reprioritize_slot(common[0], PRIORITY_ACTIVE_SLOT)
        self._thumb_pool.signals.progress.connect(
            lambda d, t, _p: self._loading.set_progress(
                d, t, i18n.KO.LOAD_THUMBNAIL_FMT.format(done=d, total=t),
            )
        )
        self._thumb_pool.signals.finished.connect(self._on_thumbs_ready)
        self._thumb_pool.start()

    def _on_thumbs_ready(self) -> None:
        self._loading.hide_overlay()
        assert self._input is not None
        # 임계치 자동 추천 ----------------------------------------------
        self._maybe_offer_threshold_suggestion()
        # Phase 결정 → Stage 1 진입 -------------------------------------
        self._phase = PHASE_A_SELECT
        self._enter_stage1_phase_a()

    def _maybe_offer_threshold_suggestion(self) -> None:
        """스캔 데이터의 점수 분포를 분석해 임계치를 추천."""
        if self._scan is None or self._input is None:
            return
        try:
            from ..similarity import threshold as _thr
            sug = _thr.suggest_threshold(self._scan)
        except Exception:
            return
        if sug is None:
            return
        # 현재 임계치와 5%p 이상 차이날 때만 물어본다.
        if abs(sug.suggested - self._input.threshold) < 0.05:
            return
        r = QMessageBox.question(
            self, i18n.KO.THRESHOLD_SUGGESTION_TITLE,
            i18n.KO.THRESHOLD_SUGGESTION_FMT.format(
                suggested=sug.suggested, current=self._input.threshold,
                same_median=sug.same_median, same_min=sug.same_min,
                diff_median=sug.diff_median, diff_max=sug.diff_max,
                margin=sug.margin,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if r == QMessageBox.StandardButton.Yes:
            self._input.threshold = float(sug.suggested)
            try:
                from ..utils import prefs as _prefs
                _prefs.patch(threshold=float(sug.suggested))
            except Exception:
                pass

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
        queue_full: list[ImageItem] = []
        for slot in sorted(slots, key=lambda s: s.name):
            queue_full.extend(slot.ref_images if lower == "ref" else slot.val_images)

        # 같은 슬롯 안 거의 동일한 사진은 그룹으로 묶어 대표만 큐에 (#15)
        grouping = self._make_groups(queue_full)
        queue = grouping.representatives if grouping else queue_full

        phase_lab = (
            i18n.KO.PHASE_A_SELECT if self._is_cross()
            else i18n.KO.STAGE1_TITLE
        )
        self._select_page.load_state(
            queue=queue,
            targets={}, excluded={}, history=[],
            phase_label=phase_lab,
            groups=grouping,
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
            session_id=self._session_id,
            model_name=self._active_model_name(),
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
                # 미탐(=상대 호기가 놓침) 으로 기록할 것은 ‘매칭 없음 확정’ 만.
                # ‘잠시 보류’ 는 사용자 결정 미정 → 미탐 시트에 넣지 않는다.
                for slot, items in st.no_match.items():
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
                for slot, items in st.no_match.items():
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

        grouping_b = self._make_groups(queue)
        queue_b = grouping_b.representatives if grouping_b else queue
        self._select_page.load_state(
            queue=queue_b,
            targets={}, excluded={}, history=[],
            phase_label=i18n.KO.PHASE_B_SELECT,
            phase_b_already_matched=dict(already_by_slot),
            groups=grouping_b,
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
            session_id=self._session_id,
            model_name=self._active_model_name(),
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
        unmatched_refs = self._compute_unmatched_refs()

        result = FinalResult(
            mode=self._input.mode,
            ref_machine=self._input.ref_machine,
            val_machine=self._input.val_machine,
            matches=merged,
            miss_fast=miss_fast,
            miss_slow=miss_slow,
            slot_only_ref=list(self._scan.ref_only),
            slot_only_val=list(self._scan.val_only),
            unmatched_refs=unmatched_refs,
        )
        # 결과 페이지에는 ‘이미 복사해둔 작업 파일’ 과 ‘템플릿 원본’ 둘 다 전달.
        self._result_page.show_result(
            result,
            template_path=self._template_used,
            target_path=self._working_xlsx,
        )
        QMessageBox.information(self, i18n.KO.APP_TITLE, i18n.KO.INFO_ALL_DONE)
        self._show_page(self._result_page)
        self._phase = PHASE_NONE
        session_mod.clear()

    # ------------------------------------------------------------------
    # 양식 → 결과 파일 복사
    # ------------------------------------------------------------------
    def _prepare_working_file(self, inp: SetupInput) -> None:
        """`양식/양식.xlsx` 를 결과 폴더로 복사해서 작업 파일을 만든다.

        결과 파일 이름: ``AOI {val} 검증 ({ref} 기준).xlsx``.
        이미 존재하면 타임스탬프를 붙여 충돌을 피한다.
        """
        template = paths.template_path()
        if not template.exists():
            QMessageBox.information(
                self, i18n.KO.TEMPLATE_NOT_FOUND_TITLE,
                i18n.KO.TEMPLATE_NOT_FOUND_BODY.format(path=str(template)),
            )
            self._template_used = None
        else:
            self._template_used = template

        # 파일 이름
        dst_name = i18n.KO.RESULT_FILE_TITLE_FMT.format(
            val=inp.val_machine, ref=inp.ref_machine,
        )
        dst = paths.results_dir() / dst_name
        if dst.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dst = dst.with_name(dst.stem + f"_{ts}" + dst.suffix)

        # 템플릿이 있으면 복사, 없으면 빈 파일 자리 표시만 (저장 시점에 생성)
        try:
            if self._template_used is not None:
                shutil.copyfile(str(self._template_used), str(dst))
        except Exception:
            # 복사 실패 시에도 경로는 보존 — 저장 시점에 새 워크북 생성
            pass

        self._working_xlsx = dst

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

    def _compute_unmatched_refs(self) -> list[MissEntry]:
        """Stage 2 에서 매칭 못 찾은 기준 사진들 (Skip + No-match).

        교차 검증 모드에서는 Phase A 와 Phase B 양쪽에서 수집된다.
        엑셀에 ‘기준 이미지 + 빨간 파일명’ 행으로 표기되는 정보.
        """
        out: list[MissEntry] = []
        for slot, items in self._skipped_a.items():
            for it in items:
                out.append(MissEntry(
                    slot=slot, side="ref", path=it.path, note="미매칭",
                ))
        for slot, items in self._skipped_b.items():
            for it in items:
                out.append(MissEntry(
                    slot=slot, side="val", path=it.path, note="미매칭",
                ))
        return out

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
        # 세션 종료 직후 평가 집계를 갱신해서 모델 카드의 정확도를 새로 반영.
        self._refresh_models_safe()
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
        # Stage 1 / Stage 2 의 현재 상태도 함께 직렬화 (#19)
        decisions: dict[str, str] = {}
        no_match_keys: list[str] = []
        skipped_keys: list[str] = []
        matches_dump: list[dict] = []
        st1 = self._select_page.get_state()
        if st1 is not None:
            for slot, items in st1.targets.items():
                for it in items:
                    decisions[it.key] = "verify"
            for slot, items in st1.excluded.items():
                for it in items:
                    decisions[it.key] = "exclude"
        st2 = self._match_page.get_state()
        if st2 is not None:
            for m in st2.matches:
                matches_dump.append({
                    "slot": m.slot,
                    "ref_path": str(m.ref_path),
                    "val_path": str(m.val_path),
                    "score": float(m.score),
                    "direction": str(m.direction),
                })
            for slot, items in st2.skipped.items():
                for it in items:
                    skipped_keys.append(it.key)
            for slot, items in st2.no_match.items():
                for it in items:
                    no_match_keys.append(it.key)

        state = session_mod.SessionState(
            mode=self._input.mode,
            ref_root=str(self._input.ref_root),
            val_root=str(self._input.val_root),
            ref_machine=self._input.ref_machine,
            val_machine=self._input.val_machine,
            threshold=self._input.threshold,
            session_id=self._session_id,
            stage=self._phase or "setup",
            phase=("B" if self._phase in (PHASE_B_SELECT, PHASE_B_MATCH) else "A"),
            decisions=decisions,
            matches=matches_dump,
            skipped=skipped_keys,
            no_match=no_match_keys,
            phase_a_matched_val_keys=sorted(self._matched_val_keys_in_a),
            phase_a_matches=[{
                "slot": m.slot,
                "ref_path": str(m.ref_path),
                "val_path": str(m.val_path),
                "score": float(m.score),
                "direction": str(m.direction),
            } for m in self._matches_a],
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
        if self._thumb_pool is not None:
            self._thumb_pool.stop()
            self._thumb_pool.wait(1000)
        # 학습 워커도 안전 종료 (#17)
        try:
            self._setup_page.stop_training()
        except Exception:
            pass
        super().closeEvent(event)
