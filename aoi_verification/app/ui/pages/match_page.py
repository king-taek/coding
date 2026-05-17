"""Stage 2 — 유사도 기반 매칭 화면.

중앙: 기준 사진 1장 / 우: 검증 장비 후보 (점수 정렬).
9장 이상이면 8장 + +N. 우측 사진 클릭 → 매칭 확정.

보류/매칭없음 사진들은 상단 [보류된 사진 보기 (n)] 버튼으로 팝업에서 모아 본다
(좁은 화면에서 좌측 패널이 자리만 차지하던 문제 해결).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QByteArray, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                              QMessageBox, QScrollArea, QSizePolicy, QSlider,
                              QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from ... import config, i18n
from ...models.result import MatchResult
from ...models.slot import ImageItem, Slot
from ...utils import image_io
from ...utils import prefs as _prefs
from ...similarity.slot_features import (SlotFeatureCache, SlotPrecomputeWorker,
                                            SlotScoreCache)
from ...workers.matcher import Candidate, MatcherWorker
from ..widgets.loading_overlay import LoadingOverlay
from ..widgets.match_expand_view import MatchExpandView
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
        # 슬롯 단위 검증측 특징 캐시 — 같은 슬롯의 reference 들이 공유.
        self._slot_cache = SlotFeatureCache(keep_lookahead=False)
        # (ref, val) 쌍 점수 사전 계산 캐시 — load_state 시 한 번에 채워서
        # 매 reference 마다 점수 재계산을 회피한다.
        self._score_cache = SlotScoreCache()
        self._precompute_worker: Optional[SlotPrecomputeWorker] = None
        # 수동 모드 한정: 슬롯 단위 스트리밍 사전 계산 상태.
        self._streaming_precompute: bool = False
        self._waiting_for_slot: Optional[str] = None
        # 자동 매치 모드 (#3): True 면 사용자 클릭 없이 임계치 이상 최고 점수 후보를
        # 자동으로 매치 / 후보 없으면 ‘매치 없음’ 으로 자동 처리.
        self._auto_mode: bool = False

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
        # [보류된 사진 보기 (n)] — 좌측 패널 대신 팝업으로 모아 보기.
        self.btn_view_skipped = NeonButton(
            i18n.KO.BTN_VIEW_SKIPPED_FMT.format(n=0), role="ghost",
        )
        self.btn_view_skipped.clicked.connect(self._open_skipped_dialog)
        self.btn_view_skipped.setEnabled(False)
        top.addWidget(self.btn_view_skipped)
        # [보류 재시도] 버튼은 보류 사진이 있을 때만 활성.
        self.retry_btn = NeonButton(i18n.KO.BTN_RETRY_SKIP, role="warn")
        self.retry_btn.setEnabled(False)
        self.retry_btn.clicked.connect(self._retry_skipped)
        top.addWidget(self.retry_btn)
        top.addSpacing(20)
        self.phase_label = QLabel("", self)
        self.phase_label.setProperty("role", "subtitle")
        top.addWidget(self.phase_label)
        top.addSpacing(20)
        self.progress_label = QLabel("", self)
        self.progress_label.setProperty("role", "muted")
        top.addWidget(self.progress_label)
        top.addSpacing(20)
        # 백그라운드 사전 계산 상태 — 수동 모드에서만 표시. 매칭 화면이 이미
        # 열려 있는 동안에도 ‘나머지 슬롯이 X / Y 완료’ 임을 알려준다.
        self.bg_status_label = QLabel("", self)
        self.bg_status_label.setStyleSheet(
            "color: #00FFA3; padding: 2px 8px;"
        )
        top.addWidget(self.bg_status_label)
        root.addLayout(top)

        # 2 pane — QSplitter 로 사용자가 분할 비율 조절 -------------------
        self._h_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._h_splitter.setHandleWidth(6)
        self._h_splitter.setChildrenCollapsible(False)

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
        # 모니터 크기에 맞춰 자동 시작값 — 세션 한정 (재시작 시 다시 자동맞춤).
        self.size_slider.setValue(ScalableImage.auto_fit_long_edge())
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
        self._img_scroll.setMinimumHeight(300)
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

        center.setMinimumWidth(420)
        self._h_splitter.addWidget(center)

        # RIGHT: 후보들 — 내부에 ‘그리드 ↔ 더 크게 보기’ 스택을 두어
        # 좌측 skip 패널과 중앙 기준 사진은 그대로 두고 후보 패널 안에서만 확대.
        right = QFrame(self)
        right.setProperty("role", "section")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(10, 10, 10, 10)
        rl.setSpacing(8)
        rt = QLabel(i18n.KO.PANEL_MATCH_CANDIDATES, right)
        rt.setProperty("role", "subtitle")
        rt.setStyleSheet("font-weight:700; color:#00D4FF;")
        rl.addWidget(rt)

        # 후보 패널 내부 스택: page 0 = 썸네일 그리드, page 1 = 확대 보기
        self._right_stack = QStackedWidget(right)

        # page 0 — 기존 그리드 (scroll area 를 host widget 으로 감싼다)
        grid_host = QWidget(self._right_stack)
        grid_host_layout = QVBoxLayout(grid_host)
        grid_host_layout.setContentsMargins(0, 0, 0, 0)
        grid_host_layout.setSpacing(0)
        self._right_scroll = QScrollArea(grid_host)
        self._right_scroll.setWidgetResizable(True)
        self._right_host = QWidget()
        self._right_grid = QGridLayout(self._right_host)
        self._right_grid.setContentsMargins(4, 4, 4, 4)
        self._right_grid.setSpacing(8)
        self._right_scroll.setWidget(self._right_host)
        grid_host_layout.addWidget(self._right_scroll, stretch=1)
        self._right_stack.addWidget(grid_host)

        # page 1 — 확대 보기 (기준 사진과 나란히 비교 가능하도록 후보 칸 안에서만)
        self._expand_view = MatchExpandView(self._right_stack)
        self._expand_view.confirm_match.connect(self._on_expand_confirm)
        self._expand_view.back_requested.connect(self._exit_expand_view)
        self._right_stack.addWidget(self._expand_view)

        rl.addWidget(self._right_stack, stretch=1)
        # 3 col × 134(tile) + spacing 16 + 패널 padding 20 = 438 → 후보 9 장이
        # 가로 스크롤 없이 한 화면에 깔리도록.
        right.setMinimumWidth(440)
        self._h_splitter.addWidget(right)

        # 좌측 skip 패널을 제거했으므로 splitter index 가 0/1 로 줄었다.
        self._h_splitter.setStretchFactor(0, 4)
        self._h_splitter.setStretchFactor(1, 3)
        root.addWidget(self._h_splitter, stretch=1)

        # 저장된 분할 비율 복원 + 변경 시 영속화 -------------------------
        _p_match = _prefs.load()
        if _p_match.splitter_state_match_h:
            self._h_splitter.restoreState(
                QByteArray.fromBase64(
                    _p_match.splitter_state_match_h.encode("ascii")
                )
            )
        self._h_splitter.splitterMoved.connect(self._save_splitter_state)

        QShortcut(QKeySequence("S"), self, activated=self._skip_current)
        QShortcut(QKeySequence("N"), self, activated=self._confirm_no_match)

    # ------------------------------------------------------------------
    def _save_splitter_state(self, *args) -> None:
        try:
            _prefs.patch(
                splitter_state_match_h=bytes(
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
                   val_pool_by_slot: dict[str, list[ImageItem]],
                   threshold: float,
                   *,
                   matches: list[MatchResult] | None = None,
                   skipped: dict[str, list[ImageItem]] | None = None,
                   phase_label: str = "",
                   direction: str = "A→B",
                   session_id: str = "",
                   model_name: str = "basic",
                   auto_mode: bool = False) -> None:
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
        self._auto_mode = bool(auto_mode)
        self.phase_label.setText(phase_label)
        self._refresh_skipped_panel()
        # 모든 (ref, val) 쌍 점수를 미리 계산 → 이후 매칭은 캐시 조회만.
        self._start_precompute()

    # ------------------------------------------------------------------
    def _start_precompute(self) -> None:
        """슬롯별 (ref, val) 점수를 사전 계산.

        - 자동 모드: 기존처럼 전체 슬롯이 끝날 때까지 기다린 뒤 매칭 시작.
        - 수동 모드: 첫 슬롯이 끝나면 곧장 매칭 시작 + 나머지 슬롯은
          백그라운드에서 슬롯 단위로 진행 (메모리 절약을 위해 features 는
          슬롯 처리 직후 폐기).
        """
        if self._state is None:
            return
        # 슬롯별 ref 수집 — queue 순서를 따라 사용자가 마주칠 순서대로 처리.
        refs_by_slot: dict[str, list[ImageItem]] = defaultdict(list)
        for r in self._state.queue:
            refs_by_slot[r.slot].append(r)
        tasks: list[tuple[str, list[ImageItem], list[ImageItem]]] = []
        for slot, refs in refs_by_slot.items():
            vals = self._state.val_pool.get(slot, [])
            if refs and vals:
                tasks.append((slot, refs, vals))
        total_pairs = sum(len(r) * len(v) for _, r, v in tasks)
        if total_pairs == 0:
            # 계산할 게 없으면 바로 진행
            self.bg_status_label.setText("")
            self._streaming_precompute = False
            self._waiting_for_slot = None
            self._advance()
            return

        # 이전 precompute 워커가 살아있으면 중단 + 상태 초기화
        if self._precompute_worker is not None and self._precompute_worker.isRunning():
            self._precompute_worker.stop()
            self._precompute_worker.wait(500)

        streaming = not bool(self._auto_mode)
        self._streaming_precompute = streaming
        self._waiting_for_slot = None

        if streaming:
            # 첫 슬롯이 끝날 때까지만 차단 오버레이 — 그 다음은 백그라운드.
            self._loading.show_overlay(i18n.KO.LOAD_PRECOMPUTE_FIRST_SLOT)
            self.bg_status_label.setText(
                i18n.KO.PRECOMPUTE_BG_STATUS_FMT.format(idx=0, total=len(tasks))
            )
        else:
            self._loading.show_overlay(
                i18n.KO.LOAD_PRECOMPUTE_FMT.format(done=0, total=total_pairs)
            )
            self.bg_status_label.setText("")

        self._precompute_worker = SlotPrecomputeWorker(
            tasks, slot_cache=self._slot_cache,
            score_cache=self._score_cache,
            release_after_slot=streaming,
            parent=self,
        )
        self._precompute_worker.signals.progress.connect(
            self._on_precompute_progress
        )
        self._precompute_worker.signals.slot_finished.connect(
            self._on_precompute_slot_finished
        )
        self._precompute_worker.signals.finished.connect(
            self._on_precompute_finished
        )
        self._precompute_worker.signals.failed.connect(
            lambda msg: self._loading.set_progress(0, 0, msg)
        )
        self._precompute_worker.start()

    def _on_precompute_progress(self, done: int, total: int) -> None:
        # 자동 모드 (기존 동작) 에서만 차단 오버레이 진행률 갱신.
        # 수동/스트리밍 모드에서는 슬롯 단위 라벨만 갱신하므로 noop.
        if not self._streaming_precompute:
            self._loading.set_progress(
                done, total,
                i18n.KO.LOAD_PRECOMPUTE_FMT.format(done=done, total=total),
            )

    def _on_precompute_slot_finished(self,
                                      slot: str,
                                      idx: int,
                                      total: int) -> None:
        """슬롯 1 개의 점수 계산이 끝났을 때 호출.

        수동 모드에서는:
        - 첫 슬롯 완료 시 매칭 화면을 즉시 활성화 (_advance).
        - 사용자가 ‘아직 점수 안 끝난 슬롯’ 에 도착해 기다리는 중이라면
          그 슬롯이 끝났을 때 자동으로 _advance 를 재시도.
        - 상단 상태 라벨 갱신 (백그라운드 진행 표시).
        """
        self.bg_status_label.setText(
            i18n.KO.PRECOMPUTE_BG_STATUS_FMT.format(idx=idx, total=total)
        )
        if not self._streaming_precompute:
            return
        if idx == 1:
            # 첫 슬롯 끝 → 차단 오버레이를 내리고 매칭 시작.
            self._loading.hide_overlay()
            self._advance()
            return
        if self._waiting_for_slot == slot:
            self._waiting_for_slot = None
            self._loading.hide_overlay()
            self._advance()

    def _on_precompute_finished(self) -> None:
        # 자동 모드면 곧장 자동 매치 진행 표시로 전환, 수동 모드면 ‘완료’ 라벨로.
        if self._auto_mode:
            total = len(self._state.queue) if self._state else 0
            self._loading.set_progress(
                0, total,
                i18n.KO.LOAD_AUTO_MATCH_FMT.format(done=0, total=total),
            )
            self._advance()
            return
        # 수동/스트리밍 모드: 첫 슬롯 시점에 이미 _advance 가 호출돼 매칭이
        # 진행 중이므로 여기서 추가 _advance 는 불필요. 상태 라벨만 정리.
        self.bg_status_label.setText(i18n.KO.PRECOMPUTE_BG_DONE)
        self._streaming_precompute = False

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
            self._loading.hide_overlay()
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

        # 스트리밍 모드에서 사용자가 ‘아직 점수 계산 중인 슬롯’ 에 도착하면
        # 짧은 오버레이로 안내 후, 그 슬롯이 끝났다는 시그널이 오면 자동으로
        # 다시 _advance 가 호출된다 (_on_precompute_slot_finished 에서).
        slot = self._current.slot
        if (self._streaming_precompute
                and self._precompute_worker is not None
                and self._precompute_worker.isRunning()
                and not self._score_cache.has_all_pairs(
                    slot, self._current.path, [v.path for v in val_items],
                )):
            self._waiting_for_slot = slot
            self._loading.show_overlay(
                i18n.KO.LOAD_PRECOMPUTE_WAIT_FMT.format(slot=slot)
            )
            return

        self._launch_matcher(self._current, val_items)

    def _launch_matcher(self,
                        ref: ImageItem,
                        val_items: list[ImageItem]) -> None:
        self._clear_right_grid()
        # 이전 워커가 살아있으면 시그널부터 끊는다. wait() 가 timeout 으로
        # 끝나도 ‘늦게 도착한 done’ 이 새 후보 리스트를 덮어쓰지 않게.
        if self._worker is not None:
            try:
                self._worker.signals.progress.disconnect()
                self._worker.signals.done.disconnect()
                self._worker.signals.failed.disconnect()
            except (TypeError, RuntimeError):
                pass
            if self._worker.isRunning():
                self._worker.stop()
                self._worker.wait(500)

        # 점수 사전 계산이 끝나 있으면 캐시 조회만으로 즉시 응답.
        val_paths = [v.path for v in val_items]
        if self._score_cache.has_all_pairs(ref.slot, ref.path, val_paths):
            cached: list = []
            for v in val_items:
                s = self._score_cache.get_pair(ref.slot, ref.path, v.path)
                if s is not None and s >= self._threshold:
                    cached.append(Candidate(item=v, score=float(s)))
            cached.sort(key=lambda c: c.score, reverse=True)
            self._on_matcher_done(cached)
            return

        # 사전 계산이 안 됐거나 누락된 경우 — 기존 워커 fallback.
        self._slot_cache.set_active(ref.slot)
        val_features = self._slot_cache.get_features(ref.slot) or {}
        # 모든 val 이 이미 캐시에 들어 있으면 ‘유사도 계산’ 모드.
        # 누락이 있으면 ‘특징 추출’ 모드로 표시.
        self._slot_features_ready = (
            len(val_features) >= len(val_items)
            and all(it.path in val_features for it in val_items)
        )
        loading_fmt = (
            i18n.KO.LOAD_SCORING_FMT if self._slot_features_ready
            else i18n.KO.LOAD_FEATURE_FMT
        )
        self._loading.show_overlay(
            loading_fmt.format(done=0, total=len(val_items))
        )
        self._current_loading_fmt = loading_fmt

        self._worker = MatcherWorker(
            ref, val_items, threshold=self._threshold,
            val_features=val_features,
            slot_cache=self._slot_cache,
        )
        self._worker.signals.progress.connect(self._on_matcher_progress)
        self._worker.signals.done.connect(self._on_matcher_done)
        self._worker.signals.failed.connect(
            lambda msg: self._loading.set_progress(0, 0, msg)
        )
        self._worker.start()

    def _on_matcher_progress(self, done: int, total: int) -> None:
        fmt = getattr(self, "_current_loading_fmt", i18n.KO.LOAD_FEATURE_FMT)
        self._loading.set_progress(
            done, total, fmt.format(done=done, total=total),
        )

    def _on_matcher_done(self, candidates: list) -> None:
        self._candidates = list(candidates)
        if not self._candidates:
            # 자동 모드면 모달 없이 ‘매칭 없음’ 으로 즉시 처리 (이벤트 루프에
            # 한 번 양보해 진행 라벨이 보이도록 QTimer.singleShot 으로 defer).
            if self._auto_mode:
                self._update_auto_progress()
                QTimer.singleShot(0, self._confirm_no_match)
            else:
                self._loading.hide_overlay()
                QMessageBox.information(self, i18n.KO.APP_TITLE,
                                        i18n.KO.INFO_NO_MATCH_FOUND)
                self._skip_current()
            return

        # 자동 매치 모드: 최고 점수 후보를 즉시 확정하고 다음 ref 로.
        if self._auto_mode:
            top = self._candidates[0]
            self._update_auto_progress()
            QTimer.singleShot(0, lambda: self._on_pick(
                ThumbEntry(item=top.item, extra={"score": float(top.score)})
            ))
            return

        self._loading.hide_overlay()
        self._populate_right(self._candidates)

    def _update_auto_progress(self) -> None:
        """자동 모드에서 진행 상황 표시 — done / total ref."""
        if self._state is None:
            return
        done = len(self._state.matches) + sum(
            len(v) for v in self._state.no_match.values()
        )
        total = done + len(self._state.queue)
        self._loading.set_progress(
            done, total,
            i18n.KO.LOAD_AUTO_MATCH_FMT.format(done=done, total=total),
        )

    # ------------------------------------------------------------------
    def _show_center(self, item: ImageItem) -> None:
        self.center_img.set_image(item.path)
        # Stage 2 도 파일명은 미표시, Slot 만 노출 (요청 사항).
        self.slot_label.setText(i18n.KO.SLOT_LABEL_FMT.format(slot=item.slot))

    def _on_size_changed(self, value: int) -> None:
        self.size_value.setText(f"{value} px")
        self.center_img.set_target_size(value)
        # 사용자 변경은 세션 동안만 유지 — 재시작 시 자동 맞춤으로 초기화.

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
                         show_expand=True, parent=self._right_host)
        entries = [ThumbEntry(item=c.item, extra={"score": c.score}) for c in visible]
        grid.set_entries(entries)
        grid.tile_clicked.connect(self._on_pick)
        grid.expand_requested.connect(self._on_expand_requested)
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

    # ------------------------------------------------------------------
    # ‘더 크게 보기’ 모드
    # ------------------------------------------------------------------
    def _on_expand_requested(self, entry: ThumbEntry) -> None:
        if self._current is None or not self._candidates:
            return
        items = [c.item for c in self._candidates]
        start = 0
        for i, c in enumerate(self._candidates):
            if c.item.path == entry.item.path:
                start = i
                break
        self._expand_view.load_candidates(self._current.slot, items, start)
        self._right_stack.setCurrentIndex(1)
        # 단축키가 동작하도록 포커스 이동.
        self._expand_view.setFocus()

    def _exit_expand_view(self) -> None:
        self._right_stack.setCurrentIndex(0)

    def _on_expand_confirm(self, item: ImageItem) -> None:
        """확대 모드에서 [이 사진으로 매칭] 또는 Enter."""
        score = 0.0
        for c in self._candidates:
            if c.item.path == item.path:
                score = float(c.score)
                break
        # 그리드 복귀 후 매칭 확정 흐름 재사용.
        self._exit_expand_view()
        self._on_pick(ThumbEntry(item=item, extra={"score": score}))

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
        # QShortcut("S") 는 WindowShortcut 컨텍스트라 SelectPage 가 보일 때도
        # 이 핸들러로 전달된다. 보이지 않을 땐 조용히 무시.
        if not self.isVisible():
            return
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
        if not self.isVisible():
            return
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
        """상단 [보류된 사진 보기 (n)] / [보류 재시도] 버튼 활성/카운트 갱신."""
        if self._state is None:
            self.btn_view_skipped.setText(
                i18n.KO.BTN_VIEW_SKIPPED_FMT.format(n=0)
            )
            self.btn_view_skipped.setEnabled(False)
            self.retry_btn.setEnabled(False)
            return
        n_defer = sum(len(v) for v in self._state.skipped.values())
        n_none = sum(len(v) for v in self._state.no_match.values())
        total = n_defer + n_none
        self.btn_view_skipped.setText(
            i18n.KO.BTN_VIEW_SKIPPED_FMT.format(n=total)
        )
        self.btn_view_skipped.setEnabled(total > 0)
        # ‘보류 재시도’ 는 defer 만 활성 — none 은 영구 미탐
        self.retry_btn.setEnabled(n_defer > 0)

    def _open_skipped_dialog(self) -> None:
        """[보류된 사진 보기] 클릭 → 보류/매칭없음 사진을 큰 팝업에 모아 표시."""
        if self._state is None:
            return
        from PyQt6.QtWidgets import QDialog as _QDialog
        dlg = _QDialog(self)
        # 닫는 즉시 C++ 위젯 해제 — 버튼 클릭마다 누적되지 않도록.
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.setWindowTitle(i18n.KO.SKIPPED_DIALOG_TITLE)
        dlg.setModal(True)
        from PyQt6.QtWidgets import QApplication as _QA
        scr = (self.screen() if hasattr(self, "screen") else None) \
            or _QA.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            dlg.resize(min(1200, int(g.width() * 0.9)),
                       min(800, int(g.height() * 0.85)))
        else:
            dlg.resize(1200, 800)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        defer_slots = sorted(s for s, v in self._state.skipped.items() if v)
        none_slots = sorted(s for s, v in self._state.no_match.items() if v)
        if not defer_slots and not none_slots:
            lab = QLabel(i18n.KO.SKIPPED_DIALOG_EMPTY, dlg)
            lab.setProperty("role", "muted")
            layout.addWidget(lab)
        else:
            scroll = QScrollArea(dlg)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            host = QWidget()
            hl = QVBoxLayout(host)
            hl.setContentsMargins(4, 4, 4, 4)
            hl.setSpacing(10)
            if defer_slots:
                n = sum(len(self._state.skipped[s]) for s in defer_slots)
                hdr = QLabel(
                    i18n.KO.SKIPPED_SECTION_DEFER_FMT.format(n=n), host,
                )
                hdr.setStyleSheet(
                    "color: #FFD600; font-weight: 700; padding: 6px 2px;"
                )
                hl.addWidget(hdr)
                for slot in defer_slots:
                    sec = SlotSection(slot, columns=4, select_mode=False,
                                      parent=host)
                    sec.set_entries([
                        ThumbEntry(item=it)
                        for it in self._state.skipped[slot]
                    ])
                    hl.addWidget(sec)
            if none_slots:
                n = sum(len(self._state.no_match[s]) for s in none_slots)
                hdr = QLabel(
                    i18n.KO.SKIPPED_SECTION_NO_MATCH_FMT.format(n=n), host,
                )
                hdr.setStyleSheet(
                    "color: #FF2D55; font-weight: 700; padding: 6px 2px;"
                )
                hl.addWidget(hdr)
                for slot in none_slots:
                    sec = SlotSection(slot, columns=4, select_mode=False,
                                      parent=host)
                    sec.set_entries([
                        ThumbEntry(item=it)
                        for it in self._state.no_match[slot]
                    ])
                    hl.addWidget(sec)
            hl.addStretch(1)
            scroll.setWidget(host)
            layout.addWidget(scroll, stretch=1)

        bar = QHBoxLayout()
        bar.addStretch(1)
        close = NeonButton(i18n.KO.BTN_OK, role="ghost")
        close.clicked.connect(dlg.accept)
        bar.addWidget(close)
        layout.addLayout(bar)
        dlg.exec()

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
