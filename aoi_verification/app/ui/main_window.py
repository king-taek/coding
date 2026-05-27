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

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow,
                              QMessageBox, QStackedWidget, QStatusBar,
                              QVBoxLayout, QWidget)

from .. import config, i18n
from ..models import session as session_mod
from ..models.result import FinalResult, MatchResult, MissEntry
from ..models.slot import (ImageItem, ScanResult, Slot, drop_empty_unmatched,
                           scan)
from ..utils import paths, wafer_id
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


class MainWindow(QMainWindow):

    # 좁은 창에서도 동작하도록 충분히 작게 (#2 — 사용자 요청: 좌우 스크롤
    # 발생하지 않게 상하 스크롤만으로 충분한 상태).  Stage 1/2 페이지는
    # 폭이 좁아지면 H-splitter 가 V-splitter 로 자동 전환되어 reflow.
    _MIN_W = 800
    _MIN_H = 600

    # 자동 업데이트 — 백그라운드 스레드에서 메인 스레드로 결과를 넘기는 시그널.
    _update_found = pyqtSignal(dict)
    _update_applied = pyqtSignal(bool, dict)
    _update_none = pyqtSignal(str)          # 수동 확인: 최신/확인불가 안내

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
        # 개발자 크레딧 — 모든 화면 공통(상태바 좌측).
        self._credit_label = QLabel(i18n.KO.CREDIT, self._status_bar)
        self._credit_label.setStyleSheet(
            "color: #7FB3D5; padding: 0 8px; font-weight: 600;"
        )
        self._status_bar.addWidget(self._credit_label)
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
        # CPU/GPU/NPU 사용량 — CPU 실제 %, GPU/NPU 가동/대기.
        self._usage_label = QLabel("", self._status_bar)
        self._usage_label.setStyleSheet(
            "color: #00D4FF; padding: 0 8px; font-weight: 600;"
        )
        self._status_bar.addPermanentWidget(self._usage_label)
        # 가속 장치(Intel GPU/NPU) 존재 여부 — 세션 중 불변이라 1회만 조회.
        # torch 설치와 무관하게 OpenVINO 만으로 존재 여부를 본다(상태바 표시용).
        self._accel_present = {"GPU": False}
        # 세션 불변인 ‘감지’ 부분 툴팁 — 동적 컴파일 진단은 매 틱 덧붙인다.
        self._accel_tip_base = ""
        try:
            from ..learning import embedder_openvino as _ovw
            info = _ovw.accelerator_presence()
            self._accel_present = {"GPU": bool(info.get("GPU"))}
            # 자가 진단 — 마우스오버로 감지 디바이스/원인을 확인.
            devs = info.get("devices") or []
            reason = info.get("reason") or ""
            self._accel_tip_base = (
                "OpenVINO 감지: " + (", ".join(devs) if devs else "(없음)")
            )
            if reason:
                self._accel_tip_base += f"\n사유: {reason}"
        except Exception:
            self._accel_tip_base = "가속 장치 조회 실패"
        self._usage_label.setToolTip(self._accel_tip_base)
        self._proc = None
        self._mem_label = QLabel("", self._status_bar)
        self._mem_label.setProperty("role", "muted")
        self._status_bar.addPermanentWidget(self._mem_label)
        self._mem_timer = QTimer(self)
        self._mem_timer.setInterval(2000)
        self._mem_timer.timeout.connect(self._update_memory_label)
        self._mem_timer.timeout.connect(self._update_usage_label)
        self._mem_pressure_shown = False
        try:
            import psutil
            self._proc = psutil.Process()
            self._proc.cpu_percent(None)        # prime — 첫 호출은 0.0 반환
        except Exception:
            self._proc = None
        # 타이머는 psutil 유무와 무관하게 구동 — 콜백이 각자 안전 가드한다
        # (메모리/CPU 는 psutil 가용 시, GPU/NPU 가동표시는 항상).
        self._mem_timer.start()
        self._update_memory_label()
        self._update_usage_label()

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
        self._setup_page.update_check_requested.connect(self._manual_update_check)
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
        self._skipped_a: dict[str, list[ImageItem]] = defaultdict(list)
        # 올인원/사진 직접 선택 모드의 매치 검토 결과 (#3).
        # 비어있지 않으면 _finish_session 이 _matches_a/_b 대신 이걸 사용한다.
        self._reviewed_matches: list[MatchResult] = []
        self._reviewed_unmatched: list[MissEntry] = []
        self._stage1_a_snapshot: dict | None = None
        self._working_xlsx: Optional[Path] = None
        self._template_used: Optional[Path] = None
        self._session_id: str = ""

        # 이어하기 ------------------------------------------------------
        QTimer.singleShot(50, self._maybe_resume)
        # Intel GPU/NPU 가속(OpenVINO) 설치 안내 — 첫 모달(이어하기) 이후 표시.
        # 모달 exec() 가 이벤트 루프를 막으므로 두 모달이 겹치지 않는다.
        QTimer.singleShot(300, self._maybe_offer_openvino)
        # 1일 지난 썸네일/중간이미지 캐시 정리 — 백그라운드 데몬으로 UI 비차단.
        self._prune_old_cache_async()
        # GPU 임베딩 모델을 미리 컴파일/워밍업 — 첫 슬롯의 커널 JIT 지연 제거(#3).
        self._warmup_accel_async()
        # 자동 업데이트 확인 — 백그라운드로 GitHub 현재 브랜치 최신 커밋과 비교.
        self._update_found.connect(self._on_update_found)
        self._update_applied.connect(self._on_update_applied)
        self._update_none.connect(self._on_update_none)
        QTimer.singleShot(800, self._check_for_update_async)

    @staticmethod
    def _prune_old_cache_async() -> None:
        """1일 지난 썸네일/중간이미지 캐시를 백그라운드 스레드에서 1회 정리."""
        import threading

        from ..utils import cache as _cache

        def _work() -> None:
            try:
                _cache.prune_old_cache(max_age_days=1.0)
            except Exception:
                pass

        threading.Thread(target=_work, name="cache-prune", daemon=True).start()

    @staticmethod
    def _warmup_accel_async() -> None:
        """가속(GPU)이 있으면 임베딩 모델을 백그라운드에서 미리 컴파일/워밍업한다."""
        import threading

        def _work() -> None:
            try:
                from ..workers import efficiency_matcher as _eff
                if _eff.has_accel_units():
                    _eff.warmup()
            except Exception:
                pass

        threading.Thread(target=_work, name="accel-warmup", daemon=True).start()

    # ==================================================================
    # 자동 업데이트 (GitHub 공개 저장소의 현재 브랜치)
    # ==================================================================
    def _check_for_update_async(self) -> None:
        """백그라운드로 업데이트 확인 → 있으면 _update_found 시그널로 UI 에 알림."""
        import threading

        def _work() -> None:
            try:
                from ..utils import updater
                info = updater.check_for_update()
                if info:
                    self._update_found.emit(info)
            except Exception:
                pass

        threading.Thread(target=_work, name="update-check", daemon=True).start()

    def _manual_update_check(self) -> None:
        """도움말 > '업데이트 확인' — 소스/포터블 모두에서 결과를 명시적으로 안내."""
        import threading
        self._status_bar.showMessage(i18n.KO.UPDATE_CHECKING, 3000)

        def _work() -> None:
            status, info = "unknown", {}
            try:
                from ..utils import updater
                status, info = updater.manual_check()
            except Exception:
                status, info = "unknown", {}
            if status == "update":
                self._update_found.emit(info)
            elif status == "latest":
                self._update_none.emit(i18n.KO.UPDATE_LATEST)
            else:
                reason = (info or {}).get("error", "")
                msg = i18n.KO.UPDATE_UNKNOWN
                if reason:
                    msg = f"{msg}\n\n[원인] {reason}"
                self._update_none.emit(msg)

        threading.Thread(target=_work, name="update-check-manual",
                         daemon=True).start()

    def _on_update_none(self, msg: str) -> None:
        QMessageBox.information(self, i18n.KO.UPDATE_AVAILABLE_TITLE, msg)

    def _on_update_found(self, info: dict) -> None:
        """'업데이트 있음' 안내 → 동의하면 백그라운드로 다운로드/교체."""
        msg = (info or {}).get("message", "")
        body = i18n.KO.UPDATE_AVAILABLE_BODY
        if msg:
            body = f"{body}\n\n· {msg}"
        ans = QMessageBox.question(
            self, i18n.KO.UPDATE_AVAILABLE_TITLE, body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        # 개발(git) 작업트리에서는 자동 덮어쓰기로 로컬 변경을 날릴 수 있어 막는다.
        try:
            from ..utils import updater
            if updater.is_git_checkout():
                QMessageBox.information(
                    self, i18n.KO.UPDATE_AVAILABLE_TITLE, i18n.KO.UPDATE_GIT_HINT)
                return
        except Exception:
            pass
        self._loading.show_overlay(i18n.KO.UPDATE_DOWNLOADING)

        import threading

        def _work() -> None:
            ok = False
            try:
                from ..utils import updater
                ok = updater.download_and_apply(
                    info["repo"], info["branch"], info["sha"])
            except Exception:
                ok = False
            self._update_applied.emit(bool(ok), info or {})

        threading.Thread(target=_work, name="update-apply", daemon=True).start()

    def _on_update_applied(self, ok: bool, info: dict) -> None:
        """다운로드/교체 결과 처리 — 성공 시 재시작 안내."""
        self._loading.hide_overlay()
        if not ok:
            msg = i18n.KO.UPDATE_FAILED
            try:
                from ..utils import updater
                if updater.last_error():
                    msg = f"{msg}\n\n[원인] {updater.last_error()}"
            except Exception:
                pass
            QMessageBox.warning(self, i18n.KO.UPDATE_AVAILABLE_TITLE, msg)
            return
        ans = QMessageBox.question(
            self, i18n.KO.UPDATE_AVAILABLE_TITLE, i18n.KO.UPDATE_DONE_RESTART,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            from ..utils import updater
            if updater.restart_app():
                QApplication.quit()
        except Exception:
            pass

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

    def _update_usage_label(self) -> None:
        """상태바 CPU/GPU 표시 갱신.

        CPU 는 프로세스 실제 사용률(코어 수로 정규화한 0~100%), GPU 는
        OpenVINO 추론 활동 기준 '가동/대기'(없으면 '없음')."""
        parts: list[str] = []
        # CPU — 프로그램 프로세스 사용률을 코어 수로 나눠 0~100% 로 표시.
        try:
            import psutil
            ncpu = psutil.cpu_count() or 1
            if self._proc is not None:
                pct = self._proc.cpu_percent(None) / ncpu
            else:
                pct = psutil.cpu_percent(None)
            parts.append(i18n.KO.USAGE_CPU_FMT.format(pct=int(round(pct))))
        except Exception:
            pass
        # GPU — 존재하면 가동/대기, 없으면 '없음'. (NPU 표시는 제거됨.)
        try:
            from ..learning import embedder_openvino as _ovw
            if not self._accel_present.get("GPU"):
                state = i18n.KO.USAGE_STATE_NONE
            elif _ovw.unit_busy("GPU"):
                state = i18n.KO.USAGE_STATE_BUSY
            else:
                state = i18n.KO.USAGE_STATE_IDLE
            parts.append(i18n.KO.USAGE_GPU_FMT.format(state=state))
            # 툴팁에 컴파일 진단을 덧붙임 — 매칭을 한 번 돌린 뒤 GPU 가 '가동'
            # 으로 안 바뀌면, 여기에 실제 컴파일 에러가 떠서 원인을 알 수 있다.
            diag = _ovw.compile_diagnostics()
            tip = self._accel_tip_base
            compiled = diag.get("compiled") or []
            if compiled:
                tip += "\n추론 컴파일 성공: " + ", ".join(compiled)
            for dev, msg in (diag.get("errors") or {}).items():
                tip += f"\n{dev} 컴파일 실패: {msg}"
            self._usage_label.setToolTip(tip)
        except Exception:
            pass
        if parts:
            self._usage_label.setText(i18n.KO.USAGE_SEP.join(parts))

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

    def _active_model_name(self) -> str:
        """학습 모델 기능 제거됨 — 항상 기본(``basic``)."""
        return "basic"

    def _resolve_slot_mismatch(self, sr: ScanResult) -> None:
        """ref/val 한쪽에만 있는 슬롯이 있을 때 사용자에게 수동 매핑을 묻는다 (#23)."""
        from PyQt6.QtWidgets import QDialog

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

        dlg = SlotMappingDialog(
            sr.ref_only, sr.val_only,
            ref_meta=getattr(self, "_slot_meta_ref", None),
            val_meta=getattr(self, "_slot_meta_val", None),
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if dlg.mapping.pairs:
            self._apply_slot_pairs(sr, dlg.mapping.pairs)

    @staticmethod
    def _kla_machine_side(inp) -> Optional[str]:
        """호기 번호가 'K-n' 또는 'KLA-n'(예: K-6, KLA-1, 대소문자 무관)이면 그 쪽을
        KLA 로 자동 판정.

        반환 "ref"/"val"/"both" 또는 None(둘 다 아님 → 사용자에게 물어봐야 함)."""
        import re

        def is_kla(label) -> bool:
            return bool(re.fullmatch(r"(?:KLA|K)\s*-\s*\d+",
                                     str(label or "").strip(), re.IGNORECASE))

        ref_k = is_kla(getattr(inp, "ref_machine", ""))
        val_k = is_kla(getattr(inp, "val_machine", ""))
        if ref_k and val_k:
            return "both"
        if ref_k:
            return "ref"
        if val_k:
            return "val"
        return None

    def _ask_kla_side(self) -> Optional[str]:
        """매칭 실패 폴더가 있을 때 'KLA 가 어느 쪽인가?' 를 묻는다.

        반환 "ref"/"val" 또는 None(KLA 아님 → 파일명/OCR 자동 매칭 건너뜀)."""
        box = QMessageBox(self)
        box.setWindowTitle(i18n.KO.KLA_ASK_TITLE)
        box.setIcon(QMessageBox.Icon.Question)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            "<div style='font-size:18pt; font-weight:800; color:#F39C12;'>"
            f"{i18n.KO.KLA_ASK_SIDE_HEADING}</div>"
        )
        box.setInformativeText(
            "<div style='font-size:11pt; color:#E8E8E8;'>"
            + i18n.KO.KLA_ASK_SIDE_BODY.replace("\n", "<br>") + "</div>"
        )
        ref_btn = box.addButton(i18n.KO.KLA_SIDE_REF,
                                QMessageBox.ButtonRole.YesRole)
        val_btn = box.addButton(i18n.KO.KLA_SIDE_VAL,
                                QMessageBox.ButtonRole.NoRole)
        box.addButton(i18n.KO.KLA_SIDE_NONE, QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(ref_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is ref_btn:
            return "ref"
        if clicked is val_btn:
            return "val"
        return None

    def _resolve_and_merge_kla(self, sr: ScanResult, kla_side: str,
                               on_done) -> None:
        """KLA(``kla_side``) 미매칭 폴더의 slot명(WaferID)을 **파일명 우선·OCR 폴백**
        으로 해석해 ref↔val 을 자동 병합한다.

        OCR 은 **메인 스레드를 막지 않도록 백그라운드 워커**에서 돌리므로, 이 메서드는
        OCR 이 끝난 뒤(또는 OCR 불필요 시 즉시) ``on_done()`` 콜백으로 다음 단계를
        잇는다.  OCR 은 **파일명에서 WaferID 를 못 읽은 폴더에만** 돈다(불필요한 OCR 방지)."""
        try:
            self._kla_resolve_impl(sr, kla_side, on_done)
        except Exception:
            on_done()

    def _kla_resolve_impl(self, sr: ScanResult, kla_side: str, on_done) -> None:
        do_ref = kla_side in ("ref", "both")
        do_val = kla_side in ("val", "both")

        def imgs_of(name: str, is_ref: bool) -> list:
            slot = sr.slots.get(name)
            if slot is None:
                return []
            return slot.ref_images if is_ref else slot.val_images

        # 1) [파일명] KLA 쪽 폴더의 사진 파일명 prefix(첫 '_' 이전 전체)를 slot명
        #    후보로 읽어 먼저 매치(형식 검증 없음).  비-KLA 쪽은 폴더명이 곧 slot명.
        self._loading.show_overlay(i18n.KO.LOAD_KLA_FILENAME)
        QApplication.processEvents()
        fn_ref: dict[str, str] = {}
        fn_val: dict[str, str] = {}
        img0_ref: dict[str, Path] = {}
        img0_val: dict[str, Path] = {}
        for n in list(sr.ref_only):
            ii = imgs_of(n, True)
            if ii:
                img0_ref[n] = ii[0].path
                if do_ref:
                    w = wafer_id.folder_wafer_id_from_filenames(ii)
                    if w:
                        fn_ref[n] = w
        for n in list(sr.val_only):
            ii = imgs_of(n, False)
            if ii:
                img0_val[n] = ii[0].path
                if do_val:
                    w = wafer_id.folder_wafer_id_from_filenames(ii)
                    if w:
                        fn_val[n] = w
        wafer_id.merge_unmatched_by_wafer_id(sr, fn_ref, fn_val)

        # 메타 작성 + 다음 단계 — OCR 결과(있으면)를 반영해 최종 메타를 만든다.
        def build_meta(names, is_kla, fn, ocr, img0) -> dict:
            meta: dict[str, dict] = {}
            for n in names:
                if n not in img0:
                    meta[n] = {"slot": None, "method": "none", "image": None}
                elif is_kla and n in ocr:
                    meta[n] = {"slot": ocr[n], "method": "ocr", "image": img0[n]}
                elif is_kla and n in fn:
                    meta[n] = {"slot": fn[n], "method": "filename", "image": img0[n]}
                elif is_kla:
                    meta[n] = {"slot": None, "method": "unread", "image": img0[n]}
                else:
                    meta[n] = {"slot": None, "method": "plain", "image": img0[n]}
            return meta

        def finalize(ocr_ref=None, ocr_val=None) -> None:
            ocr_ref = ocr_ref or {}
            ocr_val = ocr_val or {}
            self._slot_meta_ref = build_meta(list(sr.ref_only), do_ref, fn_ref,
                                             ocr_ref, img0_ref)
            self._slot_meta_val = build_meta(list(sr.val_only), do_val, fn_val,
                                             ocr_val, img0_val)
            self._ocr_worker = None
            on_done()

        # 2) [OCR] **파일명에서 WaferID 를 못 읽은(형식이 아닌) 폴더에만** 헤더 OCR.
        #    파일명이 WaferID 형식이면 그 값을 신뢰하고 OCR 을 건너뛴다(불필요한 OCR·
        #    응답없음 방지).  OCR 은 백그라운드 워커에서 → UI 비차단.
        jobs: list = []
        if do_ref:
            for n in list(sr.ref_only):
                if n in img0_ref and not wafer_id.looks_like_wafer_id(fn_ref.get(n)):
                    jobs.append(("ref", n, [it.path for it in imgs_of(n, True)]))
        if do_val:
            for n in list(sr.val_only):
                if n in img0_val and not wafer_id.looks_like_wafer_id(fn_val.get(n)):
                    jobs.append(("val", n, [it.path for it in imgs_of(n, False)]))

        if jobs and wafer_id.ocr_available():
            from ..workers.wafer_id_ocr import WaferIdOcrWorker
            self._loading.show_overlay(
                i18n.KO.LOAD_KLA_OCR_FMT.format(done=0, total=len(jobs)))
            worker = WaferIdOcrWorker(jobs, parent=self)
            self._ocr_worker = worker          # GC 방지 참조 보관

            def _on_progress(d: int, t: int) -> None:
                self._loading.set_progress(
                    d, t, i18n.KO.LOAD_KLA_OCR_FMT.format(done=d, total=t))

            def _on_ocr_done(ocr_ref: dict, ocr_val: dict) -> None:
                try:
                    wafer_id.merge_unmatched_by_wafer_id(sr, ocr_ref, ocr_val)
                finally:
                    finalize(ocr_ref, ocr_val)

            worker.signals.progress.connect(_on_progress)
            worker.signals.done.connect(_on_ocr_done)
            worker.signals.failed.connect(lambda _msg: finalize())
            worker.start()
            return

        finalize()

    def _apply_slot_pairs(self, sr: ScanResult, pairs) -> None:
        """(ref폴더명, val폴더명) 쌍을 통합 — val 사진을 ref slot명으로 합치고 제거."""
        from ..models.slot import ImageItem
        ref_used = {a for a, _ in pairs}
        val_used = {b for _, b in pairs}
        for ref_name, val_name in pairs:
            ref_slot = sr.slots.get(ref_name)
            val_slot = sr.slots.get(val_name)
            if ref_slot is None or val_slot is None:
                continue
            ref_slot.val_images = [
                ImageItem(slot=ref_name, path=it.path, side="val")
                for it in val_slot.val_images
            ]
            sr.slots.pop(val_name, None)
        sr.ref_only = [s for s in sr.ref_only if s not in ref_used]
        sr.val_only = [s for s in sr.val_only if s not in val_used]

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
            center_crop=bool(getattr(inp, "center_crop", False)),
            persist_scores=bool(getattr(inp, "persist_scores", False)),
            accel_concurrency=int(getattr(inp, "accel_concurrency", 32)),
            use_cpu=bool(getattr(inp, "use_cpu", True)),
            use_gpu=bool(getattr(inp, "use_gpu", True)),
            use_npu=bool(getattr(inp, "use_npu", True)),
            embed_batch=int(getattr(inp, "embed_batch", 1)),
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
        self._skipped_a.clear()
        self._reviewed_matches.clear()
        self._reviewed_unmatched.clear()
        self._session_id = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

        # 양식 폴더의 양식.xlsx 를 결과 폴더로 복사 → 작업 파일 준비 ----
        self._prepare_working_file(inp)

        # 원본 mtime 메모이즈 초기화 — 이번 세션 동안 캐시 키용 stat() 을 경로당 1회로(#5).
        from ..utils import cache as _cache
        _cache.reset_mtime_cache()

        self._loading.show_overlay(i18n.KO.LOAD_SCAN)
        QApplication.processEvents()

        # 폴더 스캔 — NAS 처럼 폴더가 많아도 진행 개수를 실시간 표시(#6).
        def _scan_progress(done: int, total: int) -> None:
            self._loading.set_progress(
                done, total, i18n.KO.LOAD_SCAN_FMT.format(done=done, total=total))
            QApplication.processEvents()

        sr = scan(inp.ref_root, inp.val_root, progress=_scan_progress)
        self._scan = sr
        # 사진이 한 장도 없는 한쪽 전용 폴더는 매칭 대상에서 제외(그냥 넘어감).
        drop_empty_unmatched(sr)

        # slot(폴더)명이 ref/val 간 일치하지 않으면, KLA 장비의 위치(기준/검증)를
        # 정한다 — 호기가 'K-n' 이면 그 쪽이 KLA(묻지 않음), 아니면 사용자에게 묻는다.
        # KLA 쪽은 파일명(첫 '_' 이전)→OCR 순으로 WaferID 를 읽어 자동 매칭하고,
        # 나머지는 수동 매핑.  '공통 slot 없음' 검사는 매칭 확정 이후로 미룬다.
        if sr.ref_only or sr.val_only:
            side = self._kla_machine_side(inp)
            if side is None:
                side = self._ask_kla_side()
            if side:
                # OCR 은 백그라운드 워커에서 → 끝나면 on_done 으로 다음 단계 진행.
                self._resolve_and_merge_kla(
                    sr, side, on_done=lambda: self._after_slot_resolved(sr))
                return
        self._after_slot_resolved(sr)

    def _after_slot_resolved(self, sr: ScanResult) -> None:
        """slot 매칭 확정 후 — 남은 미매칭은 수동 매핑, 그 다음 썸네일 단계."""
        if sr.ref_only or sr.val_only:
            self._resolve_slot_mismatch(sr)
        common = sr.common_slot_names
        if not common:
            self._loading.hide_overlay()
            QMessageBox.warning(self, i18n.KO.APP_TITLE, i18n.KO.WARN_NO_SLOTS)
            return
        self._continue_start_after_scan(common)

    def _continue_start_after_scan(self, common: list[str]) -> None:
        """slot 확정 후 썸네일 캐시 사전 생성(백그라운드) → 다음 단계."""
        sr = self._scan
        if sr is None:
            return
        # 매핑/OCR 단계에서 오버레이가 숨겨졌을 수 있으므로 **반드시 다시 띄운다** —
        # 그렇지 않으면 썸네일 생성 동안 메인 창이 클릭 가능 상태로 남아 버그 유발.
        # (set_progress 는 숨겨진 오버레이를 다시 띄우지 않으므로 show_overlay 필수.)
        self._loading.show_overlay(i18n.KO.LOAD_THUMBNAIL_FMT.format(done=0, total=0))
        QApplication.processEvents()
        all_items: list[ImageItem] = []
        for name in common:
            slot = sr.slots[name]
            all_items.extend(slot.ref_images)
            all_items.extend(slot.val_images)

        # 이미지 수에 따라 화질 티어 자동 선택 — 빠른 모드(썸네일 화질↓)는 상시 적용.
        per_side_total = max(
            sum(len(sr.slots[n].ref_images) for n in common),
            sum(len(sr.slots[n].val_images) for n in common),
        )
        self._sizing_tier = config.pick_tier(per_side_total, speed_mode=True)
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
    # Stage 1
    # ==================================================================
    def _enter_stage1_phase_a(self) -> None:
        assert self._scan is not None and self._input is not None
        slots = [self._scan.slots[n] for n in self._scan.common_slot_names]
        # queue: 기준(ref) 사진 전부 (Slot 명 / 파일명 오름차순)
        queue: list[ImageItem] = []
        for slot in sorted(slots, key=lambda s: s.name):
            queue.extend(slot.ref_images)

        self._select_page.load_state(
            queue=queue,
            targets={}, excluded={}, history=[],
            phase_label=i18n.KO.STAGE1_TITLE,
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

    @staticmethod
    def _collect_panel(
        panel: dict[str, list[ImageItem]]
    ) -> dict[str, list[ImageItem]]:
        return {k: list(v) for k, v in panel.items() if v}

    # ==================================================================
    # Stage 2
    # ==================================================================
    def _enter_stage2_phase_a(self) -> None:
        assert self._scan is not None and self._input is not None
        # 기준 큐 = Stage 1 에서 verify 로 분류된 기준 사진들
        targets = self._stage1_a_snapshot["targets"] if self._stage1_a_snapshot else {}
        queue: list[ImageItem] = []
        for slot in sorted(targets.keys()):
            queue.extend(targets[slot])

        # 매칭 대상 풀 = 같은 Slot 의 검증(val) 쪽 모든 사진
        pool: dict[str, list[ImageItem]] = {}
        for name in self._scan.common_slot_names:
            slot = self._scan.slots[name]
            pool[name] = slot.val_images

        direction = "A→B"
        auto_mode = AutomationLevel.is_auto(self._input.automation_level)
        self._match_page.load_state(
            queue=queue,
            val_pool_by_slot=pool,
            threshold=self._input.threshold,
            phase_label=i18n.KO.STAGE2_TITLE,
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
        self._schedule_autosave()

    def _on_match_finished(self) -> None:
        if self._phase == PHASE_A_MATCH:
            st = self._match_page.get_state()
            if st is not None:
                # 미탐으로 기록할 것은 ‘매칭 없음 확정’ 만. ‘잠시 보류’ 는 사용자
                # 결정 미정 → 미탐 시트에 넣지 않는다.
                for slot, items in st.no_match.items():
                    self._skipped_a[slot].extend(items)
            self._proceed_to_review_or_finish()

    def _proceed_to_review_or_finish(self) -> None:
        """자동 매치 결과를 MatchReviewPage 로 넘겨 검토하게 한다."""
        if self._input is None:
            self._finish_session()
            return
        merged = self._merge_matches()
        # MatchPage 가 들고 있는 점수 캐시 + val_pool 을 매치 검토 페이지에
        # 넘겨 차순위 후보를 행마다 표시한다 (참고용 시각 정보).
        score_cache = getattr(self._match_page, "_score_cache", None)
        match_state = self._match_page.get_state()
        val_pool = match_state.val_pool if match_state is not None else None
        # 효율 모드는 score_cache 가 비어 있으므로 후보를 별도 산출해 전달 (#7).
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
    # Result
    # ==================================================================
    def _finish_session(self) -> None:
        assert self._scan is not None and self._input is not None
        # 자동 모드 + 매치 검토를 거친 경우 reviewed_matches 가 우선.
        if self._reviewed_matches:
            merged = list(self._reviewed_matches)
        else:
            merged = self._merge_matches()
        unmatched_refs = self._compute_unmatched_refs()
        # 사용자가 매치 검토에서 ‘매치 없음’ 으로 표시한 ref 들 합치기.
        if self._reviewed_unmatched:
            unmatched_refs.extend(self._reviewed_unmatched)

        result = FinalResult(
            mode=self._input.mode,
            ref_machine=self._input.ref_machine,
            val_machine=self._input.val_machine,
            matches=merged,
            miss_fast=[],
            miss_slow=[],
            slot_only_ref=list(self._scan.ref_only),
            slot_only_val=list(self._scan.val_only),
            unmatched_refs=unmatched_refs,
        )
        # 결과 페이지에는 ‘이미 복사해둔 작업 파일’ 과 ‘템플릿 원본’ 둘 다 전달.
        auto_mode = (
            self._input is not None
            and AutomationLevel.is_auto(self._input.automation_level)
        )
        # 매치 실패 사진 검토(#8) 용 후보 풀 + 점수 캐시.  단일 모드는
        # unmatched.side 가 항상 "ref" 라 val_images 가 후보가 된다.
        review_pool: dict[tuple[str, str], list] = {}
        for slot_name in self._scan.common_slot_names:
            slot = self._scan.slots[slot_name]
            review_pool[(slot_name, "ref")] = list(slot.val_images)
            review_pool[(slot_name, "val")] = list(slot.ref_images)
        review_score_cache = getattr(self._match_page, "_score_cache", None)
        review_fast_results = getattr(self._match_page, "_fast_results", None)
        self._result_page.show_result(
            result,
            template_path=self._template_used,
            target_path=self._working_xlsx,
            auto_mode=auto_mode,
            val_pool=review_pool,
            score_cache=review_score_cache,
            fast_results=review_fast_results,
        )
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
        return list(self._matches_a)

    def _compute_unmatched_refs(self) -> list[MissEntry]:
        """Stage 2 에서 매칭 못 찾은 기준 사진들 (Skip + No-match).

        엑셀에 ‘기준 이미지 + 빨간 파일명’ 행으로 표기되는 정보.
        """
        out: list[MissEntry] = []
        for slot, items in self._skipped_a.items():
            for it in items:
                out.append(MissEntry(
                    slot=slot, side="ref", path=it.path, note="미매칭",
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
        self._skipped_a.clear()
        self._stage1_a_snapshot = None
        self._reviewed_matches.clear()
        self._reviewed_unmatched.clear()
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
            phase="A",
            decisions=decisions,
            matches=matches_dump,
            skipped=skipped_keys,
            no_match=no_match_keys,
            phase_a_matched_val_keys=[],
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
        # OpenVINO 설치 워커 정리.
        try:
            if (self._openvino_worker is not None
                    and self._openvino_worker.isRunning()):
                self._openvino_worker.stop()
                self._openvino_worker.wait(500)
        except Exception:
            pass
        super().closeEvent(event)
