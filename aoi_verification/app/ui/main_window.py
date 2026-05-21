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
from ..utils.prefs import AutomationLevel
from ..workers.thumbnailer import (PRIORITY_ACTIVE_SLOT, PRIORITY_BACKGROUND,
                                     ThumbnailPool, ThumbnailWorker)
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

    # 좁은 창에서도 동작하도록 충분히 작게 (#2 — 사용자 요청: 좌우 스크롤
    # 발생하지 않게 상하 스크롤만으로 충분한 상태).  Stage 1/2 페이지는
    # 폭이 좁아지면 H-splitter 가 V-splitter 로 자동 전환되어 reflow.
    _MIN_W = 800
    _MIN_H = 600

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(i18n.KO.APP_TITLE)
        self.setMinimumSize(self._MIN_W, self._MIN_H)
        self._apply_initial_geometry()
        # 사용자가 창 크기를 바꾸면 짧은 debounce 후 자동 저장.
        self._save_geom_timer = QTimer(self)
        self._save_geom_timer.setSingleShot(True)
        self._save_geom_timer.setInterval(400)
        self._save_geom_timer.timeout.connect(self._persist_geometry)

        self._stack = QStackedWidget(self)
        self.setCentralWidget(self._stack)

        # 상태 바 + 메모리 표시 (psutil 가용 시) + 가속 디바이스 표시 (#5).
        self._status_bar = QStatusBar(self)
        self.setStatusBar(self._status_bar)
        # 디바이스 표시 — 'GPU 가속 (...)' / 'CPU N 코어'.
        self._device_label = QLabel("", self._status_bar)
        self._device_label.setStyleSheet(
            "color: #00FFA3; padding: 0 8px; font-weight: 600;"
        )
        try:
            from ..learning import embedder as _emb
            self._device_label.setText(_emb.device_label())
        except Exception:
            self._device_label.setText("")
        self._status_bar.addPermanentWidget(self._device_label)
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
        # 자동 매치 결과 검토 페이지 (auto_all / user_select 모드 공용).
        from .pages.match_review_page import MatchReviewPage
        self._match_review_page = MatchReviewPage()

        for w in (self._setup_page, self._select_page,
                  self._match_page, self._result_page,
                  self._match_review_page):
            self._stack.addWidget(w)

        # 시그널 ---------------------------------------------------------
        self._setup_page.start_requested.connect(self._on_start)
        self._select_page.finished.connect(self._on_select_finished)
        self._select_page.state_changed.connect(self._schedule_autosave)
        self._match_page.match_confirmed.connect(self._on_match_confirmed)
        self._match_page.skipped_changed.connect(self._schedule_autosave)
        self._match_page.finished.connect(self._on_match_finished)
        self._match_page.cancelled.connect(self._on_match_cancelled)
        self._result_page.new_session_requested.connect(self._new_session)
        # 매치 검토 → 결과 페이지
        self._match_review_page.finished.connect(self._on_match_review_done)

        # 자동 저장 타이머 -----------------------------------------------
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(
            config.CONFIG.autosave_interval_s * 1000
        )
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()

        # OpenVINO 자동 설치 안내 — 사용자 요청으로 rollback (시작 시 팝업
        # 띄우지 않음).  설치 도우미 모듈은 남겨두어 향후 수동 호출 가능.
        self._openvino_worker: Optional[QThread] = None

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
        # 올인원/사진 직접 선택 모드의 매치 검토 결과 (#3).
        # 비어있지 않으면 _finish_session 이 _matches_a/_b 대신 이걸 사용한다.
        self._reviewed_matches: list[MatchResult] = []
        self._reviewed_unmatched: list[MissEntry] = []
        self._stage1_a_snapshot: dict | None = None
        self._stage1_b_snapshot: dict | None = None
        self._matched_val_keys_in_a: set[str] = set()
        self._working_xlsx: Optional[Path] = None
        self._template_used: Optional[Path] = None
        self._session_id: str = ""

        # 이어하기 ------------------------------------------------------
        QTimer.singleShot(50, self._maybe_resume)
        # Intel GPU/NPU 가속(OpenVINO) 설치 안내 — 첫 모달(이어하기) 이후 표시.
        # 모달 exec() 가 이벤트 루프를 막으므로 두 모달이 겹치지 않는다.
        QTimer.singleShot(300, self._maybe_offer_openvino)

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
    def _available_geom(self):
        """현재 마우스 커서가 놓인 모니터의 작업 가능 영역."""
        from PyQt6.QtGui import QCursor, QGuiApplication
        screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def _apply_initial_geometry(self) -> None:
        """프로그램 시작 시 창 크기/위치 결정.

        - 저장된 크기가 있으면 그걸 우선하되 현재 모니터 영역을 절대 넘지 않게
          클램프 (14인치 ↔ 23인치 모니터 사이 이동에 안전).
        - 저장된 크기가 없으면 모니터 가용 영역의 약 90% 로 시작 (양옆 5% 마진).
        - 마지막으로 최대화 상태였다면 그대로 최대화.
        """
        geo = self._available_geom()
        avail_w, avail_h = geo.width(), geo.height()
        p = _prefs.load()
        if p.window_maximized:
            # 최대화 전 크기도 합리적인 값으로 세팅해 ‘복원’ 동작이 자연스럽게.
            self.resize(int(avail_w * 0.9), int(avail_h * 0.9))
            self.showMaximized()
            return
        w = p.window_width
        h = p.window_height
        if w < self._MIN_W or h < self._MIN_H:
            # 미설정 / 잘못된 값 — 모니터의 90% 로 시작.
            w = max(self._MIN_W, int(avail_w * 0.9))
            h = max(self._MIN_H, int(avail_h * 0.9))
        else:
            # 모니터 영역 초과 방지 (다른 모니터에서 저장된 값일 수 있음).
            w = min(w, avail_w)
            h = min(h, avail_h)
        self.resize(w, h)
        # 화면 중앙에 배치.
        self.move(
            geo.x() + (avail_w - w) // 2,
            geo.y() + (avail_h - h) // 2,
        )

    def _persist_geometry(self) -> None:
        """현재 창 크기/최대화 여부를 prefs 에 저장."""
        try:
            if self.isMaximized() or self.isFullScreen():
                _prefs.patch(window_maximized=True)
                return
            size = self.size()
            _prefs.patch(
                window_width=int(size.width()),
                window_height=int(size.height()),
                window_maximized=False,
            )
        except Exception:
            pass

    def resizeEvent(self, event):       # noqa: N802
        super().resizeEvent(event)
        # 사용자가 드래그로 크기를 바꾸는 동안 매 이벤트마다 prefs 에 쓰지 않도록
        # debounce — 마지막 변경 후 400ms 가 지나면 한 번만 저장.
        if hasattr(self, "_save_geom_timer"):
            self._save_geom_timer.start()

    def changeEvent(self, event):       # noqa: N802
        from PyQt6.QtCore import QEvent
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            if hasattr(self, "_save_geom_timer"):
                self._save_geom_timer.start()

    # ==================================================================
    # Entry / resume
    # ==================================================================
    def _maybe_resume(self) -> None:
        # 셋업 진입 시 항상 정확도 최신화 + 모델 카드 갱신
        self._refresh_models_safe()

        # 이미 다른 페이지 (e.g. _on_start 가 먼저 GroupReviewPage 로 전환)
        # 로 넘어간 경우엔 setup 으로 되돌리지 않는다.
        if self._stack.currentWidget() is not self._setup_page:
            return

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

    def _on_match_cancelled(self) -> None:
        """#8 매치 페이지에서 중지 — 진행 중 작업을 멈추고 셋업 화면으로 복귀."""
        try:
            from ..utils import wakelock as _wl
            _wl.release()
        except Exception:
            pass
        self._show_page(self._setup_page)

    # ------------------------------------------------------------------
    def _maybe_offer_openvino(self) -> None:
        """Intel 하드웨어인데 OpenVINO 가 없으면 설치를 한 번 안내.

        OpenVINO 를 설치하면 임베딩(고속 모드)이 Intel GPU/NPU 에서 가속된다.
        '다시 보지 않기' 를 고르면 prefs 에 기록해 다음부터 묻지 않는다.
        """
        try:
            from ..learning import openvino_installer as _ovi
        except Exception:
            return
        declined = bool(getattr(_prefs.load(), "openvino_install_declined", False))
        if not _ovi.should_offer_install(declined):
            return
        box = QMessageBox(self)
        box.setWindowTitle(i18n.KO.OPENVINO_OFFER_TITLE)
        box.setText(i18n.KO.OPENVINO_OFFER_BODY)
        btn_install = box.addButton(i18n.KO.OPENVINO_OFFER_BTN_INSTALL,
                                    QMessageBox.ButtonRole.AcceptRole)
        box.addButton(i18n.KO.OPENVINO_OFFER_BTN_LATER,
                      QMessageBox.ButtonRole.RejectRole)
        btn_never = box.addButton(i18n.KO.OPENVINO_OFFER_BTN_NEVER,
                                  QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_never:
            _prefs.patch(openvino_install_declined=True)
        elif clicked is btn_install:
            self._start_openvino_install()
        # '다음에' → 아무것도 하지 않음 (다음 실행 때 다시 안내).

    def _start_openvino_install(self) -> None:
        from ..learning.openvino_installer import OpenVinoInstallWorker
        self._loading.show_overlay(i18n.KO.OPENVINO_INSTALL_PROGRESS)
        self._openvino_worker = OpenVinoInstallWorker(parent=self)
        self._openvino_worker.signals.progress.connect(
            lambda line: self._loading.show_overlay(
                i18n.KO.OPENVINO_INSTALL_PROGRESS + "\n" + line[-80:]
            )
        )
        self._openvino_worker.signals.finished.connect(
            self._on_openvino_install_finished
        )
        self._openvino_worker.start()

    def _on_openvino_install_finished(self, ok: bool, message: str) -> None:
        import importlib
        importlib.invalidate_caches()
        self._loading.hide_overlay()
        if ok:
            # 상태바 가속 표시 갱신 — OpenVINO 는 런타임 호출 시점에 적용된다.
            try:
                from ..learning import embedder as _emb
                self._device_label.setText(_emb.device_label())
            except Exception:
                pass
            QMessageBox.information(self, i18n.KO.OPENVINO_OFFER_TITLE,
                                    i18n.KO.OPENVINO_INSTALL_DONE)
        else:
            QMessageBox.warning(
                self, i18n.KO.OPENVINO_OFFER_TITLE,
                i18n.KO.OPENVINO_INSTALL_FAILED_FMT.format(error=message),
            )

    def _refresh_models_safe(self) -> None:
        """학습 모듈 import / 평가 집계 실패가 셋업 화면을 막지 않도록 wrap."""
        # 사용자 요청 (#4) — ‘기본 탐지 모드’ 가 기본 선택이 되도록 latest 의
        # 자동 활성 로직을 적용하지 않는다.  학습 모델은 사용자가 명시적으로
        # 라디오 버튼을 클릭해야만 활성화.
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

    # ==================================================================
    # Setup → Stage 1
    # ==================================================================
    def _make_sim_cfg(self) -> "config.SimilarityConfig":
        """현재 SetupInput 으로부터 유사도 엔진/전처리 설정 객체 생성."""
        inp = self._input
        if inp is None:
            return config.DEFAULT_SIM_CONFIG
        return config.SimilarityConfig(
            engine=getattr(inp, "engine_mode", "basic"),
            center20_ref=bool(getattr(inp, "center20_ref", False)),
            center20_val=bool(getattr(inp, "center20_val", False)),
            grayscale=bool(getattr(inp, "pre_grayscale", False)),
            contrast=bool(getattr(inp, "pre_contrast", False)),
            kla_crop=bool(getattr(inp, "kla_crop", False)),
            persist_scores=bool(getattr(inp, "persist_scores", False)),
        )

    def _on_start(self, inp: SetupInput) -> None:
        self._input = inp
        # #14 세션 동안 OS 절전/화면보호기 억제.
        try:
            from ..utils import wakelock as _wl
            _wl.acquire()
        except Exception:
            pass
        self._matches_a.clear()
        self._matches_b.clear()
        self._skipped_a.clear()
        self._skipped_b.clear()
        self._matched_val_keys_in_a.clear()
        self._reviewed_matches.clear()
        self._reviewed_unmatched.clear()
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
        # UI 가 무인자로 호출하는 get_thumb_path / get_mid_path 가 백그라운드
        # 풀과 같은 캐시 파일을 가리키도록 세션 티어 등록 (Bug #1 fix).
        from ..utils import image_io as _io
        _io.set_active_tier(self._sizing_tier)

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
        # 빈 큐 (모든 슬롯의 양측이 0 장) 일 때 워커가 한 번도 progress 를
        # 보내지 않아 ``finished`` 가 emit 되지 않는 행 (Bug #5) 을 방지 — 풀을
        # 시작하지 않고 즉시 다음 단계로.
        if not all_items:
            QTimer.singleShot(0, self._on_thumbs_ready)
            return
        self._thumb_pool.start()

    def _on_thumbs_ready(self) -> None:
        """썸네일 풀 finished 시그널 슬롯 — 모달/페이지 전환은 한 틱 뒤로 defer.

        finished 시그널 콜백 안에서 직접 ``QMessageBox`` 를 열거나
        ``QApplication.processEvents()`` 를 호출하면 nested event loop 가 만들
        어져 워커의 stale 시그널이 재진입할 수 있다 (Bug #2).  여기서는 오버
        레이 메시지만 갱신하고, 실제 진행은 ``QTimer.singleShot(0, ...)`` 로
        다음 이벤트 루프 틱에 넘긴다.
        """
        if self._input is None:
            return
        self._loading.set_progress(0, 0, i18n.KO.LOAD_STAGE_PREP)
        QTimer.singleShot(0, self._continue_after_thumbs)

    def _continue_after_thumbs(self) -> None:
        """``_on_thumbs_ready`` 의 안전한 후속 — 모달/페이지 전환 OK."""
        if self._input is None:
            return
        self._loading.set_progress(0, 0, i18n.KO.LOAD_STAGE_PREP)
        if self._input.automation_level == AutomationLevel.AUTO_ALL:
            self._loading.hide_overlay()
            self._enter_stage2_auto_all()
            return
        self._loading.hide_overlay()
        self._phase = PHASE_A_SELECT
        self._enter_stage1_phase_a()

    # ==================================================================
    # 올인원 자동 모드 (auto_all): Stage 1 건너뛰고 모든 ref 자동 매치.
    # ==================================================================
    def _enter_stage2_auto_all(self) -> None:
        assert self._scan is not None and self._input is not None
        slots = [self._scan.slots[n] for n in self._scan.common_slot_names]
        queue: list[ImageItem] = []
        for slot in sorted(slots, key=lambda s: s.name):
            queue.extend(slot.ref_images)
        if not queue:
            QMessageBox.warning(self, i18n.KO.APP_TITLE, i18n.KO.WARN_NO_IMAGES)
            return
        pool: dict[str, list[ImageItem]] = {}
        for name in self._scan.common_slot_names:
            slot = self._scan.slots[name]
            pool[name] = list(slot.val_images)
        self._match_page.load_state(
            queue=queue,
            val_pool_by_slot=pool,
            threshold=self._input.threshold,
            phase_label=i18n.KO.STAGE2_TITLE,
            direction="A→B",
            session_id=self._session_id,
            model_name=self._active_model_name(),
            auto_mode=True,
            engine_cfg=self._make_sim_cfg(),
        )
        self._show_page(self._match_page)
        self._phase = PHASE_A_MATCH
        self._autosave()

    def _on_match_review_done(self,
                              kept: list,
                              unmatched_refs: list) -> None:
        """MatchReviewPage 의 [검토 완료] 시그널 → 결과 페이지 진입."""
        self._reviewed_matches = list(kept)
        self._reviewed_unmatched = list(unmatched_refs)
        self._finish_session()

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
        auto_mode = AutomationLevel.is_auto(self._input.automation_level)
        self._match_page.load_state(
            queue=queue,
            val_pool_by_slot=pool,
            threshold=self._input.threshold,
            phase_label=phase_lab,
            direction=direction,
            session_id=self._session_id,
            model_name=self._active_model_name(),
            auto_mode=auto_mode,
            engine_cfg=self._make_sim_cfg(),
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
            # auto_all 모드는 single 흐름 강제 — Phase B 로 진입하지 않는다.
            auto_all = (
                self._input is not None
                and self._input.automation_level == AutomationLevel.AUTO_ALL
            )
            if self._is_cross() and not auto_all:
                QMessageBox.information(
                    self, i18n.KO.INFO_PHASE_TRANSITION_TITLE,
                    i18n.KO.INFO_PHASE_A_TO_B,
                )
                self._enter_stage1_phase_b()
            else:
                self._proceed_to_review_or_finish()
        elif self._phase == PHASE_B_MATCH:
            st = self._match_page.get_state()
            if st is not None:
                for slot, items in st.no_match.items():
                    self._skipped_b[slot].extend(items)
            self._proceed_to_review_or_finish()

    def _proceed_to_review_or_finish(self) -> None:
        """자동 모드(user_select / auto_all)면 MatchReviewPage 로,
        수동 모드면 곧장 결과 페이지로."""
        if self._input is None:
            self._finish_session()
            return
        auto_mode = AutomationLevel.is_auto(self._input.automation_level)
        if not auto_mode:
            self._finish_session()
            return
        merged = self._merge_matches()
        # MatchPage 가 들고 있는 점수 캐시 + val_pool 을 매치 검토 페이지에
        # 넘겨 차순위 후보를 행마다 표시한다 (참고용 시각 정보).
        score_cache = getattr(self._match_page, "_score_cache", None)
        match_state = self._match_page.get_state()
        val_pool = match_state.val_pool if match_state is not None else None
        # 고속 모드는 score_cache 가 비어 있으므로 후보를 별도 산출해 전달 (#7).
        candidates_by_ref = None
        try:
            candidates_by_ref = self._match_page.build_candidates_by_ref(merged)
        except Exception:
            candidates_by_ref = None
        self._match_review_page.load_state(
            merged, score_cache=score_cache, val_pool=val_pool,
            candidates_by_ref=candidates_by_ref,
        )
        self._show_page(self._match_review_page)

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
        auto_mode = AutomationLevel.is_auto(self._input.automation_level)
        self._match_page.load_state(
            queue=queue,
            val_pool_by_slot=pool,
            threshold=self._input.threshold,
            phase_label=i18n.KO.PHASE_B_MATCH,
            direction=direction,
            session_id=self._session_id,
            model_name=self._active_model_name(),
            auto_mode=auto_mode,
            engine_cfg=self._make_sim_cfg(),
        )
        self._show_page(self._match_page)
        self._phase = PHASE_B_MATCH
        self._autosave()

    # ==================================================================
    # Result
    # ==================================================================
    def _finish_session(self) -> None:
        assert self._scan is not None and self._input is not None
        # 자동 모드 + 매치 검토를 거친 경우 reviewed_matches 가 우선.
        if self._reviewed_matches:
            merged = list(self._reviewed_matches)
        else:
            merged = self._merge_matches()
        miss_fast, miss_slow = self._compute_miss_lists()
        unmatched_refs = self._compute_unmatched_refs()
        # 사용자가 매치 검토에서 ‘매치 없음’ 으로 표시한 ref 들 합치기.
        if self._reviewed_unmatched:
            unmatched_refs.extend(self._reviewed_unmatched)

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
        auto_mode = (
            self._input is not None
            and AutomationLevel.is_auto(self._input.automation_level)
        )
        # 매치 실패 사진 검토(#8) 용 후보 풀 + 점수 캐시.
        # cross 모드는 Phase A/B 에서 ref/val 양쪽 모두 미매칭이 생길 수 있어
        # ‘unmatched.side 기준의 반대편 사진들’ 을 슬롯별로 만든다:
        #   side == "ref"  → 후보 = 같은 슬롯의 val_images
        #   side == "val"  → 후보 = 같은 슬롯의 ref_images
        # 단일 모드는 unmatched.side 가 항상 "ref" 라 val_images 만 쓰인다.
        review_pool: dict[tuple[str, str], list] = {}
        for slot_name in self._scan.common_slot_names:
            slot = self._scan.slots[slot_name]
            review_pool[(slot_name, "ref")] = list(slot.val_images)
            review_pool[(slot_name, "val")] = list(slot.ref_images)
        review_score_cache = getattr(self._match_page, "_score_cache", None)
        self._result_page.show_result(
            result,
            template_path=self._template_used,
            target_path=self._working_xlsx,
            auto_mode=auto_mode,
            val_pool=review_pool,
            score_cache=review_score_cache,
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
        # #14 세션 종료 — 절전 억제 해제.
        try:
            from ..utils import wakelock as _wl
            _wl.release()
        except Exception:
            pass
        self._matches_a.clear()
        self._matches_b.clear()
        self._skipped_a.clear()
        self._skipped_b.clear()
        self._matched_val_keys_in_a.clear()
        self._stage1_a_snapshot = None
        self._stage1_b_snapshot = None
        self._reviewed_matches.clear()
        self._reviewed_unmatched.clear()
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
        # 종료 직전 마지막 크기/최대화 상태 저장 → 다음 실행에서 그대로 복원.
        self._persist_geometry()
        # #14 절전 억제 해제 (남아 있을 경우).
        try:
            from ..utils import wakelock as _wl
            _wl.release()
        except Exception:
            pass
        if self._thumb_worker is not None and self._thumb_worker.isRunning():
            self._thumb_worker.stop()
            self._thumb_worker.wait(1000)
        if self._thumb_pool is not None:
            self._thumb_pool.stop()
            self._thumb_pool.wait(1000)
        # MatchPage 의 점수 사전 계산 워커도 안전 종료.
        try:
            pre = getattr(self._match_page, "_precompute_worker", None)
            if pre is not None and pre.isRunning():
                pre.stop()
                pre.wait(500)
        except Exception:
            pass
        # 학습 워커도 안전 종료 (#17)
        try:
            self._setup_page.stop_training()
        except Exception:
            pass
        # ResultPage 의 자동 재학습 워커도 함께 종료 — 사용자가 검증 도중
        # 자동 학습이 백그라운드로 시작됐을 수 있음.
        try:
            auto = getattr(self._result_page, "_auto_retrain_worker", None)
            if auto is not None and auto.isRunning():
                auto.stop()
                auto.wait(500)
        except Exception:
            pass
        # OpenVINO 설치 워커 정리.
        try:
            if (self._openvino_worker is not None
                    and self._openvino_worker.isRunning()):
                self._openvino_worker.stop()
                self._openvino_worker.wait(500)
        except Exception:
            pass
        super().closeEvent(event)
