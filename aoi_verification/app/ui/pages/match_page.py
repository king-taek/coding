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
from ...workers import efficiency_matcher as _eff
from ...utils.prefs import EngineMode
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
    cancelled = pyqtSignal()                    # #8 사용자가 중지 버튼 누름

    # 좁은 창에선 중앙/우측 2-pane 을 세로 스택으로 자동 전환 (#2).
    _RESPONSIVE_THRESH_LO = 840
    _RESPONSIVE_THRESH_HI = 940

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._state: Stage2State | None = None
        self._current: Optional[ImageItem] = None
        self._threshold = config.CONFIG.default_threshold
        self._mode_direction = "A→B"
        self._build()
        self._loading = LoadingOverlay(self)
        self._loading.cancel_requested.connect(self._on_cancel_requested)
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
        # 스트리밍 워커가 이미 ‘처리 완료’ 한 슬롯 목록 — extract 실패로 점수가
        # 누락된 슬롯에서도 무한 대기에 빠지지 않도록 추적 (워커는 슬롯마다
        # 한 번만 slot_finished 를 emit 하므로 두 번째 신호를 기대하면 안 됨).
        self._precompute_processed_slots: set[str] = set()
        # 자동 매치 모드 (#3): True 면 사용자 클릭 없이 임계치 이상 최고 점수 후보를
        # 자동으로 매치 / 후보 없으면 ‘매치 없음’ 으로 자동 처리.
        self._auto_mode: bool = False
        # 유사도 엔진 설정 (기본/고효율 + 중앙 crop 토글).  기본값 = 현행 동작.
        self._engine_cfg = config.DEFAULT_SIM_CONFIG
        # 고효율 모드 활성 여부 — 결과를 _fast_results 에 선계산해 즉시 응답.
        self._fast_mode: bool = False
        self._efficiency_mode: bool = False
        # 고효율 선계산 결과: {(slot, ref_path): [(val_path, score), ...]}.
        self._fast_results: dict = {}
        # 현재 사전 계산 단계 라벨 (#8).
        self._precompute_phase: str = ""
        # 마지막으로 받은 사전 계산 진행도(라벨만 바뀔 때 진행 바 유지용).
        self._precompute_done: int = 0
        self._precompute_total: int = 0

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
        self.btn_view_skipped.setVisible(False)        # (#3) — 기본 숨김
        top.addWidget(self.btn_view_skipped)
        # [보류 재시도] 버튼은 보류 사진이 있을 때만 표시 (#3).
        self.retry_btn = NeonButton(i18n.KO.BTN_RETRY_SKIP, role="warn")
        self.retry_btn.setVisible(False)
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
        # ‘잠시 보류’ 버튼 제거 (#3 — 사용자 요청).  ‘매칭 없음’ 만 남김.
        self.no_match_btn = NeonButton(i18n.KO.BTN_NO_MATCH, role="danger")
        self.no_match_btn.setToolTip(i18n.KO.SHORTCUT_STAGE2_TOOLTIP)
        self.no_match_btn.clicked.connect(self._confirm_no_match)
        bar.addStretch(1)
        bar.addWidget(self.no_match_btn)
        cl.addLayout(bar)

        center.setMinimumWidth(360)
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
        right.setMinimumWidth(360)
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

        # ‘S’ (skip) 단축키 — 잠시 보류 버튼 제거와 함께 비활성 (#3).
        QShortcut(QKeySequence("N"), self, activated=self._confirm_no_match)

    # ------------------------------------------------------------------
    def resizeEvent(self, event):                       # noqa: N802
        super().resizeEvent(event)
        self._update_splitter_orientation()

    def _update_splitter_orientation(self) -> None:
        """창 폭에 따라 H ↔ V splitter 전환 — 가로 스크롤 회피 (#2)."""
        if not hasattr(self, "_h_splitter"):
            return
        cur = self._h_splitter.orientation()
        w = self.width()
        if cur == Qt.Orientation.Horizontal and w < self._RESPONSIVE_THRESH_LO:
            self._h_splitter.setOrientation(Qt.Orientation.Vertical)
            self._h_splitter.setSizes([400, 400])
        elif cur == Qt.Orientation.Vertical and w > self._RESPONSIVE_THRESH_HI:
            self._h_splitter.setOrientation(Qt.Orientation.Horizontal)
            self._h_splitter.setSizes([500, 500])

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
                   auto_mode: bool = False,
                   engine_cfg=None) -> None:
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
        self._engine_cfg = engine_cfg or config.DEFAULT_SIM_CONFIG
        self._fast_results.clear()
        self.phase_label.setText(phase_label)
        self._refresh_skipped_panel()
        # 모든 (ref, val) 쌍 점수를 미리 계산 → 이후 매칭은 캐시 조회만.
        self._start_precompute()

    # ------------------------------------------------------------------
    def _start_precompute(self) -> None:
        """슬롯별 (ref, val) 점수를 사전 계산.

        - 자동 모드: 모든 슬롯의 모든 쌍을 한 번에 (ThreadPoolExecutor 병렬)
          사전 계산한 뒤 자동 매치 시작.  ‘한장한장 따로’ 가 아니라 ‘여러
          장 동시’ 처리.
        - 수동 모드: 첫 슬롯이 끝나면 곧장 매칭 시작 + 나머지 슬롯은
          백그라운드에서 슬롯 단위로 진행 (메모리 절약을 위해 features 는
          슬롯 처리 직후 폐기).
        """
        if self._state is None:
            return
        # 매칭(사전 계산) 소요시간 측정 시작 — run_log 통계용.
        import time as _t
        self._precompute_t0 = _t.perf_counter()
        self._precompute_elapsed = 0.0
        # 사전 계산은 항상 첫 매칭(_advance)보다 앞선다 — 진행 바 갱신 가드
        # (_current is None) 가 직전 세션의 잔여 상태에 흔들리지 않도록 초기화.
        self._current = None
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

        # 이전 워커가 살아있으면 시그널 끊은 뒤 중단 — 늦게 도착할 ``slot_finished``
        # 가 새 워커의 상태(_streaming_precompute / _waiting_for_slot) 를 건드리지
        # 않도록 (MatcherWorker 의 동일 패턴 참고).
        self._stop_precompute_worker()

        # 고효율 모드 — CPU+GPU fusion-zscore.  결과를 _fast_results 에 채워
        # _launch_matcher 가 즉시 응답한다(기본 모드는 score_cache 사용).
        eff_mode = EngineMode.is_efficiency(self._engine_cfg.engine)
        self._efficiency_mode = eff_mode
        self._fast_mode = eff_mode
        if eff_mode and not _eff.has_accel_units():
            # 가속 장치 없음 → CPU 단독으로 고효율 모드 실행 (안내 로그).
            import logging
            logging.getLogger("aoi.match").info(i18n.KO.ENGINE_EFFICIENCY_CPU_ONLY)

        # 수동 = 스트리밍(첫 슬롯 후 곧장 검토 + 나머지 백그라운드).
        # 자동 = 전체 선계산(한 번의 진행 바) → 끝난 뒤 자동 매칭, 백그라운드 없음
        #        (#2/#3: 사진 한 장씩/이중 계산처럼 보이는 현상 제거).
        streaming = not bool(self._auto_mode)
        self._streaming_precompute = streaming
        self._waiting_for_slot = None

        if streaming:
            # 첫 슬롯이 끝날 때까지만 차단 오버레이 — 그 다음은 백그라운드.
            self._loading.show_overlay(i18n.KO.LOAD_PRECOMPUTE_FIRST_SLOT,
                                       cancelable=True)
            self.bg_status_label.setText(
                i18n.KO.PRECOMPUTE_BG_STATUS_FMT.format(idx=0, total=len(tasks))
            )
        else:
            # 자동 — 전체 진행을 하나의 차단 오버레이로 표시.
            self._loading.show_overlay(
                i18n.KO.LOAD_PRECOMPUTE_FMT.format(done=0, total=total_pairs),
                cancelable=True,
            )
            self.bg_status_label.setText("")

        if eff_mode:
            # 고효율 모드 — CPU+GPU 가 협업해 ref 를 처리, 결과를 _fast_results 에 저장.
            self.bg_status_label.setText(_eff.describe_active_units())
            self._precompute_worker = _eff.EfficiencyScheduler(
                tasks, cfg=self._engine_cfg, threshold=self._threshold,
                auto=self._auto_mode, results=self._fast_results, parent=self,
            )
        else:
            self._precompute_worker = SlotPrecomputeWorker(
                tasks, slot_cache=self._slot_cache,
                score_cache=self._score_cache,
                release_after_slot=streaming,
                cfg=self._engine_cfg,
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
        self._precompute_worker.signals.failed.connect(self._on_precompute_failed)
        try:
            self._precompute_worker.signals.phase.connect(self._on_precompute_phase)
        except (AttributeError, TypeError):
            pass
        self._precompute_worker.start()

    def _on_precompute_phase(self, phase: str) -> None:
        """현재 작업 단계 라벨 갱신 (#8) — '이미지 특징 분석'/'유사도 계산' 등.

        선행 단계(특징 분석/임베딩)에서 progress emit 이 늦어도 라벨은 **즉시**
        오버레이에 반영해, '유사도 계산 0' 처럼 보이던 문제를 없앤다.
        """
        self._precompute_phase = phase or i18n.KO.PHASE_SCORING
        if self._current is None:
            # 마지막으로 받은 진행도를 유지하며 라벨만 바꾼다(아직 없으면 busy 표시).
            self._loading.set_progress(
                self._precompute_done, self._precompute_total,
                self._precompute_phase,
            )

    def _on_precompute_progress(self, done: int, total: int) -> None:
        # 첫 슬롯이 끝나 매칭이 시작되기 전(차단 오버레이 표시 중)에는 진행 바를
        # 갱신해 "0% 에서 멈춘 것처럼" 보이지 않게 한다.  매칭이 시작되면
        # (_current 설정) 백그라운드 슬롯의 progress 는 매칭 오버레이를 건드리지
        # 않도록 무시 — bg_status_label 이 슬롯 단위 진행을 대신 보여준다.
        # (set_progress 는 hidden 오버레이를 다시 show 하지 않으므로 안전.)
        self._precompute_done = done
        self._precompute_total = total
        if self._current is None:
            phase = getattr(self, "_precompute_phase", "") or i18n.KO.PHASE_SCORING
            # 라벨엔 현재 작업명, 진행도는 처리 갯수(done / total)로 표시.
            self._loading.set_progress(done, total, phase)

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
        self._precompute_processed_slots.add(slot)
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

    def _on_precompute_failed(self, msg: str) -> None:
        """사전 계산 워커가 실패로 종료 — 사용자가 오버레이에 갇히지 않도록
        대기 상태를 해제하고 ``_advance`` 로 폴백 (MatcherWorker 가 lazy 계산).

        Bug #6: 이전엔 오버레이 텍스트만 갱신하고 _waiting_for_slot 을 풀지
        않아, 사용자가 점수 계산 대기 중인 슬롯에 도달했을 때 워커가 죽으면
        영구히 ‘잠시만 기다려주세요’ 오버레이에 갇혔다.
        """
        try:
            self._loading.hide_overlay()
        except Exception:
            pass
        self._waiting_for_slot = None
        # 스트리밍 모드 종료 표시 — 이후 _launch_matcher 가 MatcherWorker 폴백
        # 경로로 작동.
        self._streaming_precompute = False
        if self.bg_status_label is not None:
            self.bg_status_label.setText(msg or "")
        # 현재 ref 가 있으면 폴백 매칭으로 진행.
        if self._current is not None or (self._state and self._state.queue):
            self._advance()

    def _on_precompute_finished(self) -> None:
        import time as _t
        if getattr(self, "_precompute_t0", None):
            self._precompute_elapsed = _t.perf_counter() - self._precompute_t0
        was_streaming = self._streaming_precompute
        self.bg_status_label.setText(i18n.KO.PRECOMPUTE_BG_DONE)
        self._streaming_precompute = False
        if not was_streaming:
            # 자동(전체 선계산) — 모든 계산이 끝났으니 이제 매칭 시작.
            # _current 가 아직 None 이므로 진행 바가 정상 갱신됐고, 여기서 한 번만
            # _advance → 캐시/결과 조회로 즉시 자동 매칭 (백그라운드 없음).
            self._loading.hide_overlay()
            self._advance()
            return
        # 수동 스트리밍 — 첫 슬롯에서 이미 _advance 됨.  대기 슬롯만 해제.
        if self._waiting_for_slot is not None:
            self._waiting_for_slot = None
            self._loading.hide_overlay()
            self._advance()

    def get_state(self) -> Stage2State | None:
        return self._state

    def build_candidates_by_ref(self, matches) -> dict:
        """매치 검토 화면(#7)용 — 각 ref 의 후보 [(ImageItem, score), ...] 산출.

        고속 모드는 _fast_results(선계산 top-K)에서, 기본 모드는 score_cache
        에서 가져온다.  키는 (slot, ref_path.name) — 점수 내림차순.
        """
        out: dict = {}
        if self._state is None:
            return out
        for m in matches:
            key = (m.slot, m.ref_path.name)
            if key in out:
                continue
            vitems = self._state.val_pool.get(m.slot, []) or []
            by_path = {v.path: v for v in vitems}
            scored: list = []
            fres = self._fast_results.get((m.slot, m.ref_path))
            if fres:
                for vp, s in fres:
                    vi = by_path.get(vp)
                    if vi is not None:
                        scored.append((vi, float(s)))
            else:
                for vi in vitems:
                    s = self._score_cache.get_pair(m.slot, m.ref_path, vi.path)
                    if s is not None:
                        scored.append((vi, float(s)))
            scored.sort(key=lambda x: x[1], reverse=True)
            out[key] = scored
        return out

    # ------------------------------------------------------------------
    def _on_cancel_requested(self) -> None:
        """#8 중지 — 진행 중인 사전계산/매칭 워커를 안전하게 멈추고 세션 중단."""
        self._stop_precompute_worker()
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
        self._loading.hide_overlay()
        self.cancelled.emit()

    # ------------------------------------------------------------------
    def _stop_precompute_worker(self) -> None:
        """현재 precompute 워커의 시그널을 모두 끊고 중단 + 대기.

        - 다음 _start_precompute 호출 전에 호출되어 ‘이전 워커의 늦은 시그널이
          새 워커 상태를 건드리는’ race 를 방지.
        - 매칭 완료 (queue 비움) 시점에도 호출되어 단일 모드 → 결과 페이지로
          넘어갈 때 백그라운드 워커가 헛돌지 않도록 정리.

        호출 후 스트리밍 관련 상태(streaming flag / waiting slot / 처리된 슬롯
        집합 / 상단 상태 라벨) 도 모두 초기화. 워커가 없어도 상태는 항상 비운다.
        """
        w = self._precompute_worker
        if w is not None:
            for sig in (w.signals.progress, w.signals.slot_finished,
                        w.signals.finished, w.signals.failed):
                try:
                    sig.disconnect()
                except (TypeError, RuntimeError):
                    pass
            if w.isRunning():
                w.stop()
                w.wait(500)
        self._streaming_precompute = False
        self._waiting_for_slot = None
        self._precompute_processed_slots.clear()
        self.bg_status_label.setText("")

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
            # 백그라운드 사전 계산이 아직 돌고 있으면 즉시 중단 — 매칭이 끝났으니
            # 이후 슬롯 점수는 더 이상 필요 없음 (CPU/RAM 회수).
            self._stop_precompute_worker()
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
            # 자동 모드는 매 ref 마다 모달이 뜨는 ‘모달 폭격’ 을 피하려고
            # 모달 없이 조용히 ‘매칭 없음’ 처리 (Bug #4).  수동 모드만 안내.
            if not self._auto_mode:
                QMessageBox.information(self, i18n.KO.APP_TITLE,
                                        i18n.KO.INFO_NO_MATCH_FOUND)
            self._confirm_no_match()
            return

        # 스트리밍 모드에서 사용자가 ‘아직 점수 계산 중인 슬롯’ 에 도착하면
        # 짧은 오버레이로 안내 후, 그 슬롯이 끝났다는 시그널이 오면 자동으로
        # 다시 _advance 가 호출된다 (_on_precompute_slot_finished 에서).
        # 단, 워커가 이미 그 슬롯을 처리했는데도 점수가 누락된 경우 (extract
        # 실패 등) 무한 대기에 빠지지 않도록 처리 여부도 함께 확인.
        slot = self._current.slot
        slot_pending = (
            self._streaming_precompute
            and self._precompute_worker is not None
            and self._precompute_worker.isRunning()
            and slot not in self._precompute_processed_slots
            and not self._score_cache.has_all_pairs(
                slot, self._current.path, [v.path for v in val_items],
            )
        )
        if slot_pending:
            self._waiting_for_slot = slot
            self._loading.show_overlay(
                i18n.KO.LOAD_PRECOMPUTE_WAIT_FMT.format(slot=slot),
                cancelable=True,
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

        # 고속 모드 자동 — 선계산된 결과(_fast_results)가 있으면 즉시 응답
        # (사진 한 장씩 백그라운드 계산 없이 바로 매칭, #2/#3).
        if self._fast_mode:
            key = (ref.slot, ref.path)
            if key in self._fast_results:
                by_path = {v.path: v for v in val_items}
                cands = []
                for vp, s in self._fast_results[key]:
                    vitem = by_path.get(vp)
                    if vitem is not None and s >= self._threshold:
                        cands.append(Candidate(item=vitem, score=float(s)))
                cands.sort(key=lambda c: c.score, reverse=True)
                self._on_matcher_done(cands)
                return

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
            loading_fmt.format(done=0, total=len(val_items)),
            cancelable=True,
        )
        self._current_loading_fmt = loading_fmt

        self._worker = MatcherWorker(
            ref, val_items, threshold=self._threshold,
            val_features=val_features,
            slot_cache=self._slot_cache,
            cfg=self._engine_cfg,
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
                self._confirm_no_match()           # ‘잠시 보류’ 제거 (#3)
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

        # 후보는 가로 2 개씩 + mid 캐시 (~800px) 를 소스로 (#5).  표시 크기
        # 도 일반 썸네일보다 크게 (260 px) 잡아 시인성 ↑.
        grid = ThumbGrid(columns=2, select_mode=False, truncate=False,
                         show_expand=True, tile_px=260, prefer_mid=True,
                         parent=self._right_host)
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
        # 기본 표시 크기 = 중앙 기준 사진의 현재 크기 (#1).  사용자가 확대
        # 보기 안에서 슬라이더를 만진 적이 있으면 그 값이 우선 (세션 유지).
        self._expand_view.load_candidates(
            self._current.slot, items, start,
            default_long_edge=self.size_slider.value(),
        )
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
        self._refresh_skipped_panel()
        self.skipped_changed.emit()
        self._advance()

    # ------------------------------------------------------------------
    def _refresh_skipped_panel(self) -> None:
        """상단 [보류된 사진 보기 (n)] / [보류 재시도] 버튼 활성/카운트 갱신.

        ‘잠시 보류’ 버튼 제거 (#3) 이후로는 새로 보류가 만들어지지 않지만,
        과거 autosave 의 보류 항목이 있을 수 있어 두 버튼 자체는 유지하고
        해당 항목 수가 0 이면 ‘완전히 숨김’ 처리 — 화면이 깔끔하게 보임.
        """
        if self._state is None:
            self.btn_view_skipped.setText(
                i18n.KO.BTN_VIEW_SKIPPED_FMT.format(n=0)
            )
            self.btn_view_skipped.setVisible(False)
            self.retry_btn.setVisible(False)
            return
        n_defer = sum(len(v) for v in self._state.skipped.values())
        n_none = sum(len(v) for v in self._state.no_match.values())
        total = n_defer + n_none
        self.btn_view_skipped.setText(
            i18n.KO.BTN_VIEW_SKIPPED_FMT.format(n=total)
        )
        # 0 이면 숨김 — ‘잠시 보류’ 자체가 사라졌으므로.
        self.btn_view_skipped.setVisible(total > 0)
        # ‘보류 재시도’ 는 defer 만 활성 — none 은 영구 미탐
        self.retry_btn.setVisible(n_defer > 0)

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
