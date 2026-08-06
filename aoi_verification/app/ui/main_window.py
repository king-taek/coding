"""애플리케이션 메인 윈도우 — 전체 흐름 조정자(orchestrator).

다음 페이지를 StackedWidget 로 갈아끼우며 흐름을 관리한다.
1) SetupPage           → 입력
2) SelectPage          → Stage 1 (후보 선별)
3) MatchPage           → Stage 2 (유사도 매칭)
4) ResultPage          → 결과 + 엑셀 저장

세션 자동 저장 / 이어하기, 단계 전환 모달, 진행 상태 라벨도 여기서 처리한다.
"""

from __future__ import annotations

import logging
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QLabel, QMainWindow,
                              QMessageBox, QStackedWidget, QStatusBar,
                              QVBoxLayout, QWidget)

from .. import config, i18n
from . import theme
from ..coords import kla_info
from ..models import session as session_mod
from ..models.result import FinalResult, MatchResult, MissEntry
from ..models.slot import (ImageItem, ScanResult, drop_empty_unmatched,
                           merge_unmatched_by_wafer_id,
                           push_one_sided_to_unmatched, scan)
from ..utils import paths, wafer_id, wakelock
from ..utils import prefs as _prefs
from ..utils.prefs import AutomationLevel, EngineMode
from .pages.setup_page import SetupInput, SetupPage
from .widgets.loading_overlay import LoadingOverlay
from .widgets import sheet_host as sheets
from .widgets.sheet_host import SheetHost
from .widgets.window_controls import add_fullscreen_shortcut

# ★ 무거운 것은 **여기서 import 하지 않는다**(시작 속도).  나머지 네 페이지와
#   썸네일러는 cv2·OpenVINO·numpy·PIL 을 줄줄이 끌고 오는데, 첫 화면(SetupPage)은
#   그중 무엇도 쓰지 않는다.  최상위에 두면 그 import 가 끝날 때까지 창이 뜨지
#   못한다 — 사용자가 지적한 '시작이 느리다' 의 대부분이 이것이었다.
#   대신 `_start_backend_import_async` 가 창을 띄운 뒤 백그라운드에서 불러오고,
#   끝나면 `_on_backend_loaded` 가 메인 스레드에서 나머지 페이지를 만든다.
#   회귀 가드: dev/tests/test_startup_light_import.py
#   ※ `from __future__ import annotations` 덕에 타입 주석은 import 없이도 유효하다.


# ---------------------------------------------------------------------------
# Phase identifiers
# ---------------------------------------------------------------------------
# 조용히 삼키던 실패를 남기는 로거 — 기존 관례(`aoi.coords`·`aoi.openvino`)와 같은
# 이름 규칙.  출력은 `main._setup_logging` 이 캐시 폴더의 app.log 로 보낸다.
_LOG = logging.getLogger("aoi.ui")

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
    _update_progress = pyqtSignal(int, int, str)   # 다운로드/적용 진행(로딩바)
    _update_none = pyqtSignal(str)          # 수동 확인: 최신/확인불가 안내
    _startup_proceed = pyqtSignal()         # 업데이트 흐름 종료 → 나머지 시작 팝업
    # 무거운 모듈(cv2·OpenVINO·numpy·PIL)을 백그라운드에서 다 불러왔다는 신호.
    # 스레드에서 위젯을 만들 수 없으므로 시그널로 메인 스레드에 넘긴다(CLAUDE.md).
    _backend_loaded = pyqtSignal()

    def __init__(self, progress=None) -> None:
        """``progress(done, total, message)`` 를 주면 페이지 생성 진행을 보고한다
        (시작 스플래시의 로딩 표시).  창 생성은 메인 스레드를 막으므로, 진행을
        보고하지 않으면 로딩 표시가 그 구간 내내 멈춰 보인다."""
        super().__init__()
        self.setWindowTitle(i18n.KO.APP_TITLE)
        self.setMinimumSize(self._MIN_W, self._MIN_H)
        self._apply_initial_geometry()
        # 사용자가 창 크기를 바꾸면 짧은 debounce 후 자동 저장.
        self._save_geom_timer = QTimer(self)
        self._save_geom_timer.setSingleShot(True)
        self._save_geom_timer.setInterval(400)
        self._save_geom_timer.timeout.connect(self._persist_geometry)

        # 페이지 스택만 둔다.  ★ 상단 로고는 **각 페이지가 자기 콘텐츠 맨 위에**
        #   놓는다(widgets/app_logo.py) — 스택 밖에 고정해 두면 아래를 스크롤해도
        #   따라오지 않고 그 칸이 영영 자리를 차지한다(사용자 지적).
        central = QWidget(self)
        col = QVBoxLayout(central)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self._stack = QStackedWidget(central)
        col.addWidget(self._stack, 1)
        self.setCentralWidget(central)

        # 상태 바 — 개발자 크레딧 + 메모리 사용량(psutil 가용 시).
        # ★ 'Intel GPU 가속' 디바이스 표시와 'CPU n% · GPU 가동' 사용량 표시는 제거했다.
        #   상태바는 사용자가 **행동을 바꿀 수 있는** 정보만 담아야 한다: 가속 장치는
        #   세션 중 바뀌지 않고, CPU/GPU 가동 여부로 사용자가 할 수 있는 일이 없다.
        #   메모리는 다르다 — 압박 토스트가 '슬롯을 나눠 돌리라'는 행동을 유발한다.
        #   진단이 필요할 때 쓰는 embedder_openvino 의 device_label()·
        #   accelerator_presence()·unit_busy()·compile_diagnostics() 자체는 그대로
        #   남아 있다(개발자 벤치마크용).
        self._status_bar = QStatusBar(self)
        self.setStatusBar(self._status_bar)
        # 개발자 크레딧 — 모든 화면 공통(상태바 좌측).
        self._credit_label = QLabel(i18n.KO.CREDIT, self._status_bar)
        self._apply_statusbar_theme()      # 색 모드 전환 때와 같은 코드로 칠한다
        self._status_bar.addWidget(self._credit_label)
        self._mem_label = QLabel("", self._status_bar)
        self._mem_label.setProperty("role", "muted")
        self._status_bar.addPermanentWidget(self._mem_label)
        self._mem_timer = QTimer(self)
        self._mem_timer.setInterval(2000)
        self._mem_timer.timeout.connect(self._update_memory_label)
        self._mem_pressure_shown = False
        # 색 모드 전환 중 재진입 차단 — 연타로 페이지 재생성이 겹치면 스냅샷·페이지가
        # 어긋나고, 최악에는 잠금이 풀리지 않는다.
        self._appearance_busy = False
        # 타이머는 psutil 유무와 무관하게 구동 — 콜백이 안전 가드한다.
        # ★ 여기서 첫 갱신을 직접 부르지 않는다.  콜백이 `import psutil` 을 하는데,
        #   그것이 창이 뜨기 전 시작 경로 위에 얹힌다(사용자 체감: 시작이 느리다).
        #   2초 뒤 첫 틱이 같은 일을 하므로 표시 내용은 달라지지 않는다.
        self._mem_timer.start()

        # 페이지 — 생성+스택추가+시그널 배선을 _build_pages 단일 출처로 (재구축용).
        # ★ 2단계로 만든다: 지금은 첫 화면(SetupPage)만, 나머지 넷은 무거운 모듈이
        #   백그라운드에서 다 올라온 뒤에.  준비 전에는 '검증 시작' 이 비활성이다.
        self._backend_ready = False
        self._select_page = None
        self._match_page = None
        self._result_page = None
        self._match_review_page = None
        self._build_pages(progress)

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

        # ★ F11 전체화면 — 뷰어·검토 팝업이 **창 안 시트**가 된 뒤로 '사진을 화면 가득
        #   보는' 유일한 경로다(옛 뷰어별 F11 의 대체).  되돌리면 들어올 때의 상태
        #   (최대화/보통)로 돌아간다.
        self._fullscreen_shortcut = add_fullscreen_shortcut(self)

        # 앱 내 창(시트) 호스트 — 모든 팝업이 이 창 안에서 뜬다(별도 OS 창 금지).
        # ★ 이름 `_sheets` 는 `sheet_host.host_for` 가 찾는 **약속된 속성**이다.
        #   못 찾으면 네이티브 QMessageBox 로 폴백하므로 앱이 멈추지는 않지만, 팝업이
        #   다시 창으로 뜬다.  로딩 오버레이보다 **먼저** 만들어 z-order 상 로딩이 위에
        #   오게 한다(작업 진행 표시가 시트에 가리면 안 된다).
        self._sheets = SheetHost(self)

        # 상태 -----------------------------------------------------------
        self._loading = LoadingOverlay(self)
        # 썸네일 사전생성 단계 취소(#C2) — 풀을 멈추고 곧바로 다음 단계로.
        self._loading.cancel_requested.connect(self._on_loading_cancel)
        # 썸네일 완료 처리 one-shot 가드 — finished 시그널/취소가 이중 진입해
        # Stage 1 에 두 번 들어가는 것을 방지.
        self._thumbs_handled = False
        self._thumb_pool = None              # Optional[ThumbnailPool]
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
        # 1일 지난 썸네일/중간이미지 캐시 정리 — 백그라운드 데몬으로 UI 비차단.
        self._prune_old_cache_async()
        # 무거운 모듈을 백그라운드에서 불러온다 → 끝나면 나머지 페이지를 만들고
        # '검증 시작' 을 연다.  GPU 워밍업은 그 뒤에 이어 붙인다(같은 모듈이 필요).
        self._backend_loaded.connect(self._on_backend_loaded)
        self._start_backend_import_async()
        # 시작 팝업 순서: ① 자동 업데이트 확인 → (업데이트 처리 후) ② 이어하기 →
        # ③ OpenVINO 안내.  팝업이 겹치지 않도록 ②③ 은 업데이트 흐름이 끝난 뒤에만 띄운다.
        self._startup_done = False
        self._update_found.connect(self._on_update_found)
        self._update_applied.connect(self._on_update_applied)
        self._update_progress.connect(self._on_update_progress)
        self._update_none.connect(self._on_update_none)
        self._startup_proceed.connect(self._run_startup_popups)
        QTimer.singleShot(400, self._check_for_update_async)

    def _run_startup_popups(self) -> None:
        """업데이트 흐름이 끝난 뒤(없음/거절/실패) 나머지 시작 팝업을 순서대로 1회만."""
        if self._startup_done:
            return
        self._startup_done = True
        self._maybe_resume()                 # 모달(exec) — 닫힌 뒤 OpenVINO 안내.
        QTimer.singleShot(0, self._maybe_offer_openvino)

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
            info = None
            try:
                from ..utils import updater
                info = updater.check_for_update()
            except Exception:
                info = None
            if info:
                self._update_found.emit(info)
            else:
                self._startup_proceed.emit()   # 업데이트 없음 → 나머지 시작 팝업 진행

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
        sheets.info(self, i18n.KO.UPDATE_AVAILABLE_TITLE, msg)

    def _on_update_found(self, info: dict) -> None:
        """'업데이트 있음' 안내 → 동의하면 백그라운드로 다운로드/교체."""
        # 사용자에겐 개발자용 커밋 메시지/SSL 멘트 대신 간단한 안내만.
        body = (i18n.KO.UPDATE_UNKNOWN_CURRENT if (info or {}).get("current_unknown")
                else i18n.KO.UPDATE_AVAILABLE_BODY)
        ans = sheets.ask(
            self, i18n.KO.UPDATE_AVAILABLE_TITLE, body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ans != QMessageBox.StandardButton.Yes:
            self._run_startup_popups()       # 거절 → 나머지 시작 팝업 진행
            return
        # 개발(git) 작업트리에서는 자동 덮어쓰기로 로컬 변경을 날릴 수 있어 막는다.
        try:
            from ..utils import updater
            if updater.is_git_checkout():
                sheets.info(
                    self, i18n.KO.UPDATE_AVAILABLE_TITLE, i18n.KO.UPDATE_GIT_HINT)
                self._run_startup_popups()
                return
        except Exception:
            pass
        self._loading.show_overlay(i18n.KO.UPDATE_DOWNLOADING)
        self._loading.set_progress(0, 0, i18n.KO.UPDATE_DOWNLOADING)   # busy → 0 에서 안 멈춤

        import threading

        def _report(done: int, total: int, phase: str) -> None:
            # 백그라운드 스레드 → 메인 스레드로 시그널 전달(큐).  로딩바가 단계별로 움직인다.
            self._update_progress.emit(int(done), int(total), str(phase))

        def _work() -> None:
            ok = False
            try:
                from ..utils import updater
                ok = updater.download_and_apply(
                    info["repo"], info["branch"], info["sha"], progress=_report)
            except Exception:
                ok = False
            self._update_applied.emit(bool(ok), info or {})

        threading.Thread(target=_work, name="update-apply", daemon=True).start()

    def _on_update_progress(self, done: int, total: int, phase: str) -> None:
        """업데이트 진행(다운로드/압축해제/적용) → 로딩바 갱신(메인 스레드)."""
        self._loading.set_progress(done, total, phase)

    def _on_update_applied(self, ok: bool, info: dict) -> None:
        """다운로드/교체 결과 — 성공 시 안내 후 **프로그램 자동 종료**(재시작은 사용자가)."""
        self._loading.hide_overlay()
        if not ok:
            msg = i18n.KO.UPDATE_FAILED
            try:
                from ..utils import updater
                if updater.deps_blocked():
                    # 실패가 아니라 '의도적 보류' 다 — 무엇을 받아야 하는지 정확히 알린다.
                    msg = i18n.KO.UPDATE_NEEDS_NEW_BUNDLE
                elif updater.last_error():
                    msg = f"{msg}\n\n[원인] {updater.last_error()}"
            except Exception:
                pass
            sheets.warn(self, i18n.KO.UPDATE_AVAILABLE_TITLE, msg)
            self._run_startup_popups()       # 실패 → 나머지 시작 팝업 진행
            return
        # 안내 후 프로그램을 자동 종료한다(자동 재실행은 하지 않음 — 사용자가 다시 실행).
        msg = i18n.KO.UPDATE_DONE_RESTART
        try:
            from ..utils import updater
            if updater.update_pending():     # exe 모드 — 교체는 다음 실행 때 런처가 한다
                msg = i18n.KO.UPDATE_DONE_RESTART_STAGED
            elif updater.deps_changed():     # 필요한 패키지 목록이 바뀐 경우 갱신 안내 추가
                msg = msg + i18n.KO.UPDATE_DEPS_CHANGED
        except Exception:
            pass
        sheets.info(
            self, i18n.KO.UPDATE_AVAILABLE_TITLE, msg)
        QApplication.quit()

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
        # 이미 다른 페이지 (e.g. _on_start 가 먼저 GroupReviewPage 로 전환)
        # 로 넘어간 경우엔 setup 으로 되돌리지 않는다.
        if self._stack.currentWidget() is not self._setup_page:
            return

        state = session_mod.load()
        if state is None or state.stage in ("setup", "result"):
            self._show_page(self._setup_page)
            return
        r = sheets.ask(
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
            threshold=state.threshold,
        )
        self._show_page(self._setup_page)

    def _on_match_cancelled(self) -> None:
        """#8 매치 페이지에서 중지 — 진행 중 작업을 멈추고 셋업 화면으로 복귀."""
        wakelock.release()
        self._show_page(self._setup_page)

    # ------------------------------------------------------------------
    def _maybe_offer_openvino(self) -> None:
        """Intel 하드웨어인데 OpenVINO 가 없으면 설치를 한 번 안내.

        OpenVINO 를 설치하면 임베딩(고속 모드)이 Intel GPU 에서 가속된다.
        '다시 보지 않기' 를 고르면 prefs 에 기록해 다음부터 묻지 않는다.
        """
        try:
            from ..learning import openvino_installer as _ovi
        except Exception:
            return
        declined = bool(getattr(_prefs.load(), "openvino_install_declined", False))
        if not _ovi.should_offer_install(declined):
            return
        # 선택지 3개 — 앱 내 선택 시트(옛 QMessageBox.addButton).
        picked = sheets.choose(
            self, i18n.KO.OPENVINO_OFFER_TITLE, i18n.KO.OPENVINO_OFFER_BODY,
            [("never", i18n.KO.OPENVINO_OFFER_BTN_NEVER, "danger"),
             ("later", i18n.KO.OPENVINO_OFFER_BTN_LATER, "ghost"),
             ("install", i18n.KO.OPENVINO_OFFER_BTN_INSTALL, "primary")],
            default="install",
        )
        if picked == "never":
            _prefs.patch(openvino_install_declined=True)
        elif picked == "install":
            self._start_openvino_install()
        # '다음에'/닫기 → 아무것도 하지 않음 (다음 실행 때 다시 안내).

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
            sheets.info(self, i18n.KO.OPENVINO_OFFER_TITLE,
                        i18n.KO.OPENVINO_INSTALL_DONE)
        else:
            sheets.warn(
                self, i18n.KO.OPENVINO_OFFER_TITLE,
                i18n.KO.OPENVINO_INSTALL_FAILED_FMT.format(error=message),
            )

    def _resolve_slot_mismatch(self, sr: ScanResult) -> None:
        """ref/val 한쪽에만 있는 슬롯이 있을 때 사용자에게 수동 매핑을 묻는다 (#23)."""
        from PyQt6.QtWidgets import QDialog

        from .widgets.slot_mapping_dialog import SlotMappingDialog
        # 안내 → 다이얼로그 열기 여부 묻기
        r = sheets.ask(
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
        if sheets.run(dlg, full_bleed=True) != QDialog.DialogCode.Accepted:
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

        반환 "ref"/"val"/"both" 또는 None(KLA 아님 → 파일명/OCR 자동 매칭 건너뜀)."""
        # ★ 네 경우(기준/검증/둘다/KLA 아님)를 모두 유지한다 — 한쪽만 추가하지 말 것
        #   (CLAUDE.md 규칙).  강조 문장은 인라인 HTML 대신 시트의 warn 라벨이 담당한다
        #   (색을 f-string 으로 굽지 않으므로 다크 모드 전환에도 따라온다).
        # ★ `tiles=True` — 넷은 **대등한 선택지**다.  예전엔 '기준' 만 파란 주 버튼이라
        #   "왜 저것만 강조돼 있지?" 로 읽혔다(사용자 지적).  정답이 정해져 있지 않으면
        #   기본값을 세우지도 않는다(`default` 없음).  순서는 읽는 순서대로.
        return sheets.choose(
            self, i18n.KO.KLA_ASK_TITLE, i18n.KO.KLA_ASK_SIDE_BODY,
            [("ref", i18n.KO.KLA_SIDE_REF, "option"),
             ("val", i18n.KO.KLA_SIDE_VAL, "option"),
             ("both", i18n.KO.KLA_SIDE_BOTH, "option"),
             (None, i18n.KO.KLA_SIDE_NONE, "option")],
            heading=i18n.KO.KLA_ASK_SIDE_HEADING, tiles=True,
        )

    def _resolve_and_merge_kla(self, sr: ScanResult, kla_side: str,
                               on_done) -> None:
        """KLA(``kla_side``) 미매칭 폴더의 slot명(WaferID)을 **정보파일 우선·OCR 폴백**
        으로 해석해 ref↔val 을 자동 병합한다.

        OCR 은 **메인 스레드를 막지 않도록 백그라운드 워커**에서 돌리므로, 이 메서드는
        OCR 이 끝난 뒤(또는 OCR 불필요 시 즉시) ``on_done()`` 콜백으로 다음 단계를
        잇는다.  OCR 은 **정보파일에서 WaferID 를 못 읽은 폴더에만** 돈다(불필요한 OCR 방지)."""
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

        def dir_of(name: str, is_ref: bool):
            slot = sr.slots.get(name)
            if slot is None:
                return None
            return slot.ref_dir if is_ref else slot.val_dir

        # 1) [정보파일] KLA 쪽 폴더의 정보파일 헤더에 있는 `WaferID "XXXX";` 를 읽어
        #    slot명으로 쓴다.  사진과 무관하게 읽히므로 **사진 0장 폴더도 식별**된다.
        #    비-KLA 쪽은 폴더명이 곧 slot명.
        self._loading.show_overlay(i18n.KO.LOAD_KLA_INFO)
        QApplication.processEvents()
        info_ref: dict[str, str] = {}
        info_val: dict[str, str] = {}
        img0_ref: dict[str, Path] = {}
        img0_val: dict[str, Path] = {}
        for n in list(sr.ref_only):
            ii = imgs_of(n, True)
            if ii:
                img0_ref[n] = ii[0].path
            if do_ref:
                d = dir_of(n, True)
                w = kla_info.read_wafer_id(d) if d else None
                if w:
                    info_ref[n] = w
        for n in list(sr.val_only):
            ii = imgs_of(n, False)
            if ii:
                img0_val[n] = ii[0].path
            if do_val:
                d = dir_of(n, False)
                w = kla_info.read_wafer_id(d) if d else None
                if w:
                    info_val[n] = w
        merge_unmatched_by_wafer_id(sr, info_ref, info_val)

        # 메타 작성 + 다음 단계 — OCR 결과(있으면)를 반영해 최종 메타를 만든다.
        # 판독에 성공한 폴더는 **사진이 없어도**(image=None) slot명을 갖는다 → 수동
        # 매핑에서 미리보기만 없고 선택은 가능하다.
        def build_meta(names, is_kla, info, ocr, img0) -> dict:
            meta: dict[str, dict] = {}
            for n in names:
                img = img0.get(n)
                if is_kla and n in info:
                    meta[n] = {"slot": info[n], "method": "info", "image": img}
                elif is_kla and n in ocr:
                    meta[n] = {"slot": ocr[n], "method": "ocr", "image": img}
                elif img is None:
                    meta[n] = {"slot": None, "method": "none", "image": None}
                elif is_kla:
                    meta[n] = {"slot": None, "method": "unread", "image": img}
                else:
                    meta[n] = {"slot": None, "method": "plain", "image": img}
            return meta

        def finalize(ocr_ref=None, ocr_val=None) -> None:
            ocr_ref = ocr_ref or {}
            ocr_val = ocr_val or {}
            # 병합된 slot명(WaferID) → KLA 하위폴더명 매핑(엑셀 B열 회색 표기용).
            # 둘 다 KLA 면 같은 WaferID 에 ref/val 두 폴더명이 걸리므로 **병기**한다
            # (덮어쓰면 검증 쪽만 남아 어느 폴더인지 알 수 없다).
            kla: dict[str, str] = {}

            def add(wid, folder: str) -> None:
                key = str(wid).upper()
                prev = kla.get(key)
                kla[key] = f"{prev} / {folder}" if prev and prev != folder else folder

            if do_ref:
                for n, w in {**info_ref, **ocr_ref}.items():
                    if w:
                        add(w, n)
            if do_val:
                for n, w in {**info_val, **ocr_val}.items():
                    if w:
                        add(w, n)
            self._kla_folders = kla
            self._slot_meta_ref = build_meta(list(sr.ref_only), do_ref, info_ref,
                                             ocr_ref, img0_ref)
            self._slot_meta_val = build_meta(list(sr.val_only), do_val, info_val,
                                             ocr_val, img0_val)
            self._ocr_worker = None
            on_done()

        # 2) [OCR] **정보파일에서 WaferID 를 못 읽은 폴더에만** 헤더 OCR (사진이 있어야
        #    가능).  정보파일에서 읽혔으면 그 값을 신뢰하고 OCR 을 건너뛴다(불필요한
        #    OCR·응답없음 방지).  OCR 은 백그라운드 워커에서 → UI 비차단.
        jobs: list = []
        if do_ref:
            for n in list(sr.ref_only):
                if n in img0_ref and n not in info_ref:
                    jobs.append(("ref", n, [it.path for it in imgs_of(n, True)]))
        if do_val:
            for n in list(sr.val_only):
                if n in img0_val and n not in info_val:
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
                    # 1차(정보파일) 결과도 **함께** 넘긴다 — OCR dict 만 넘기면 정보파일로
                    # 읽은 쪽의 WaferID 키가 사라져 같은 WaferID 인데도 병합에 실패한다.
                    merge_unmatched_by_wafer_id(
                        sr, {**info_ref, **ocr_ref}, {**info_val, **ocr_val})
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
        engine = getattr(inp, "engine_mode", EngineMode.COORDINATE)
        # 실측 최적 프로파일 적용:
        #   기본 모드  = rr_parallel  → 전수 고전(pHash+ORB+SSIM)·CPU 멀티코어 병렬(현행 동작).
        #   고효율 모드 = rr_orb_center50 → GPU 임베딩 추림 + 상위 후보를 ORB 단독·중앙(defect)
        #                가중으로 재채점.  defect 가 정중앙인 특성을 활용한 최속·정확도 보존 조합.
        if engine == "efficiency":
            rerank_components = frozenset({"orb"})
            orb_center_weight = 0.5
        else:
            rerank_components = None       # 전체 항(전수 고전)
            orb_center_weight = 0.0
        return config.SimilarityConfig(
            engine=engine,
            center_crop=False,
            persist_scores=bool(getattr(inp, "persist_scores", False)),
            accel_concurrency=int(getattr(inp, "accel_concurrency", 32)),
            use_cpu=bool(getattr(inp, "use_cpu", True)),
            use_gpu=bool(getattr(inp, "use_gpu", True)),
            embed_batch=int(getattr(inp, "embed_batch", 1)),
            rerank_components=rerank_components,
            orb_center_weight=orb_center_weight,
            coord_tolerance=float(getattr(inp, "coord_tolerance", 500.0)),
        )


    def _on_start(self, inp: SetupInput) -> None:
        self._input = inp
        # #14 세션 동안 OS 절전/화면보호기 억제.
        wakelock.acquire()
        self._matches_a.clear()
        self._skipped_a.clear()
        self._reviewed_matches.clear()
        self._reviewed_unmatched.clear()
        self._thumbs_handled = False        # 썸네일 완료 one-shot 가드 리셋(#C2)
        # 타일 픽스맵 캐시 비우기 — 폴더가 바뀌어도 stale 픽스맵이 남지 않게(#렉).
        try:
            from ..utils import image_io as _io
            _io.clear_tile_cache()
        except Exception as exc:
            _LOG.debug("타일 픽스맵 캐시 비우기 실패: %s", exc)
        self._session_id = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

        # 양식 폴더의 양식.xlsx 를 결과 폴더로 복사 → 작업 파일 준비 ----
        self._prepare_working_file(inp)

        # 원본 mtime 메모이즈 초기화 — 이번 세션 동안 캐시 키용 stat() 을 경로당 1회로(#5).
        from ..utils import cache as _cache
        _cache.reset_mtime_cache()
        self._kla_folders: dict[str, str] = {}      # KLA slot명→폴더명(엑셀 회색 표기)

        self._loading.show_overlay(i18n.KO.LOAD_SCAN)
        QApplication.processEvents()

        # 폴더 스캔 — NAS 처럼 폴더가 많아도 진행 개수를 실시간 표시(#6).
        def _scan_progress(done: int, total: int) -> None:
            self._loading.set_progress(
                done, total, i18n.KO.LOAD_SCAN_FMT.format(done=done, total=total))
            QApplication.processEvents()

        sr = scan(inp.ref_root, inp.val_root, progress=_scan_progress)

        # '일부 슬롯만 진행' 옵션 — 선택된 슬롯만 남긴다.  slots dict 만 줄이면
        # common_slot_names / ref_only / val_only 가 모두 이를 기반으로 계산되어
        # 다운스트림 전체가 자연히 선택 슬롯으로 제한된다.
        sel = getattr(inp, "selected_slots", None)
        if sel:
            sr.slots = {n: s for n, s in sr.slots.items() if n in sel}
            sr.ref_only = [n for n in sr.ref_only if n in sel]
            sr.val_only = [n for n in sr.val_only if n in sel]

        self._scan = sr
        # ※ 사진 0장 폴더 정리(drop_empty_unmatched)는 **KLA 해석 뒤**(_after_slot_resolved)
        #   로 미룬다 — 정보파일은 사진이 없어도 WaferID 를 주므로, 여기서 미리 버리면
        #   짝지을 수 있는 폴더를 놓친다.

        # slot(폴더)명이 ref/val 간 일치하지 않으면, KLA 장비의 위치(기준/검증)를
        # 정한다 — 호기가 'K-n' 이면 그 쪽이 KLA(묻지 않음), 아니면 사용자에게 묻는다.
        # KLA 쪽은 정보파일→OCR 순으로 WaferID 를 읽어 자동 매칭하고,
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
        # 사진이 한 장도 없는데 짝도 못 찾은 폴더는 손댈 게 없으므로 제외(그냥 넘어감).
        drop_empty_unmatched(sr)
        if sr.ref_only or sr.val_only:
            self._resolve_slot_mismatch(sr)
        # 짝은 찾았지만 한쪽 사진이 0장인 슬롯을 '기준/검증 전용' 으로 되돌려 결과에 남긴다
        # (그대로 두면 common 에도 *_only 에도 없어 결과에서 통째로 사라진다).
        push_one_sided_to_unmatched(sr)
        common = sr.common_slot_names
        if not common:
            self._loading.hide_overlay()
            sheets.warn(self, i18n.KO.APP_TITLE, i18n.KO.WARN_NO_SLOTS)
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
        # 썸네일 단계는 가장 오래 걸리므로 [중지] 로 건너뛸 수 있게 한다(#C2).
        self._loading.show_overlay(
            i18n.KO.LOAD_THUMBNAIL_FMT.format(done=0, total=0), cancelable=True)
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
        # (썸네일러는 모듈 최상위가 아니라 여기서 불러온다 — 위 import 주석 참조.)
        from ..workers.thumbnailer import (PRIORITY_ACTIVE_SLOT,
                                           PRIORITY_BACKGROUND, ThumbnailPool)
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

        one-shot 가드(#C2): finished 시그널과 취소(_on_loading_cancel) 가 모두
        이 함수를 부를 수 있으므로, 단 한 번만 다음 단계로 진행하게 한다.
        """
        if self._input is None:
            return
        if self._thumbs_handled:
            return
        self._thumbs_handled = True
        self._loading.set_progress(0, 0, i18n.KO.LOAD_STAGE_PREP)
        QTimer.singleShot(0, self._continue_after_thumbs)

    def _on_loading_cancel(self) -> None:
        """로딩 오버레이의 [중지] — 썸네일 사전생성만 취소 대상.

        썸네일은 비필수(이후 UI 가 필요 시 생성)이므로, 풀을 멈추고 곧바로
        다음 단계로 진행한다.  다른 단계의 오버레이는 cancelable=False 라 이
        핸들러가 호출되지 않는다(버튼 자체가 없음).

        ``ThumbnailPool`` 은 QObject(워커만 QThread)라 ``isRunning()`` 이 없다.
        이미 다음 단계로 넘어갔는지는 one-shot 플래그로 판별하고, ``stop()`` 은
        이미 끝난 풀에 호출해도 무해하므로 그 조합으로 가드한다."""
        pool = self._thumb_pool
        if pool is not None and not self._thumbs_handled:
            pool.stop()                 # 이미 완료된 풀이어도 무해(플래그만 set)
            self._on_thumbs_ready()     # one-shot 가드로 1회만 진행

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
    def _build_val_pool_by_slot(self) -> dict[str, list[ImageItem]]:
        """공통 슬롯의 검증(val) 후보 풀 — Stage 2 매칭 대상 (중복 제거 #D1).

        ``_enter_stage2_auto_all`` / ``_enter_stage2_phase_a`` 가 동일하게 만들던
        풀 구성을 한 곳으로 모은다.  값은 동일(공통 슬롯의 val_images 복사본).
        """
        assert self._scan is not None
        return {name: list(self._scan.slots[name].val_images)
                for name in self._scan.common_slot_names}

    def _enter_stage2_auto_all(self) -> None:
        assert self._scan is not None and self._input is not None
        slots = [self._scan.slots[n] for n in self._scan.common_slot_names]
        queue: list[ImageItem] = []
        for slot in sorted(slots, key=lambda s: s.name):
            queue.extend(slot.ref_images)
        if not queue:
            sheets.warn(self, i18n.KO.APP_TITLE, i18n.KO.WARN_NO_IMAGES)
            return
        pool = self._build_val_pool_by_slot()
        _sim_cfg = self._make_sim_cfg()
        self._match_page.load_state(
            queue=queue,
            val_pool_by_slot=pool,
            threshold=self._input.threshold,
            session_id=self._session_id,
            auto_mode=True,
            engine_cfg=_sim_cfg,
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

        # 이전에 이 기준 폴더로 고른 기준 사진이 있으면 재사용할지 물어본다 (#6).
        restored = self._maybe_restore_ref_selection(queue)
        self._select_page.load_state(
            queue=queue,
            targets=restored, excluded={}, history=[],
        )
        self._phase = PHASE_A_SELECT
        # 판단할 후보가 하나도 없으면(예: 기준 사진 재사용으로 큐가 전부 비워짐)
        # 빈 선별 화면에 사용자를 세워두지 않고 곧장 매칭으로 넘어간다.  큐가
        # 비면 왼쪽(대기)·중앙(현재 사진)이 모두 비어 할 수 있는 조작이 없다.
        if not queue:
            self._on_select_finished()
            return
        self._show_page(self._select_page)
        self._autosave()

    def _on_select_finished(self) -> None:
        if self._phase == PHASE_A_SELECT:
            self._stage1_a_snapshot = {
                "targets": self._collect_panel(self._select_page.get_state().targets),
                "excluded": self._collect_panel(self._select_page.get_state().excluded),
            }
            # 기준 폴더로 직접 고른 기준 사진을 기록 (다음에 재사용 질의용, #6).
            self._save_ref_selection(self._stage1_a_snapshot["targets"])
            sheets.info(
                self, i18n.KO.INFO_PHASE_TRANSITION_TITLE,
                i18n.KO.INFO_PHASE_A_TO_MATCH,
            )
            self._enter_stage2_phase_a()

    # ------------------------------------------------------------------
    # 기준 사진 재사용 기록 (#6)
    # ------------------------------------------------------------------
    def _save_ref_selection(
        self, targets: dict[str, list[ImageItem]]
    ) -> None:
        """직접 고른 기준 사진(검증 대상)을 기준 폴더 절대경로로 영속화."""
        if self._input is None:
            return
        try:
            from ..utils import ref_history
            slots_to_names = {
                slot: [it.filename for it in items]
                for slot, items in (targets or {}).items() if items
            }
            ref_history.save_chosen(self._input.ref_root, slots_to_names)
        except Exception:
            pass

    def _maybe_restore_ref_selection(
        self, queue: list[ImageItem]
    ) -> dict[str, list[ImageItem]]:
        """기록이 있으면 사용자에게 재사용 여부를 묻고, 예이면 해당 사진을
        queue 에서 빼서 targets(검증 대상) 로 옮긴 매핑을 반환한다.

        반환된 항목은 ``queue`` 에서 제거된다 (in-place).  아니오/기록 없음이면
        빈 dict 반환(현행대로 빈 targets).
        """
        if self._input is None:
            return {}
        try:
            from ..utils import ref_history
            if not ref_history.has_history(self._input.ref_root):
                return {}
            chosen = ref_history.get_chosen(self._input.ref_root)
        except Exception:
            return {}
        if not chosen:
            return {}
        wanted = {(slot, name) for slot, names in chosen.items() for name in names}
        matched = [it for it in queue if (it.slot, it.filename) in wanted]
        if not matched:
            return {}
        r = sheets.ask(
            self, i18n.KO.REF_REUSE_TITLE,
            i18n.KO.REF_REUSE_BODY_FMT.format(n=len(matched)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if r != QMessageBox.StandardButton.Yes:
            return {}
        restored: dict[str, list[ImageItem]] = {}
        matched_set = set(matched)
        for it in matched:
            restored.setdefault(it.slot, []).append(it)
        queue[:] = [it for it in queue if it not in matched_set]
        return restored

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
        # ★ 빈 큐로 Stage 2 에 들어가지 않는다(`_enter_stage2_auto_all` 과 같은 가드).
        #   들어가면 `load_state` 안에서 `_advance()` 가 **동기로** finished 를 내
        #   검토 화면으로 갔다가, 돌아와서 아래 `_show_page(self._match_page)` 가
        #   그걸 덮어써 **텅 빈 Stage 2** 에 사용자를 세워 둔다.
        if not queue:
            sheets.warn(self, i18n.KO.APP_TITLE, i18n.KO.WARN_NO_IMAGES)
            return

        # 매칭 대상 풀 = 같은 Slot 의 검증(val) 쪽 모든 사진
        pool = self._build_val_pool_by_slot()

        _sim_cfg = self._make_sim_cfg()
        auto_mode = AutomationLevel.is_auto(self._input.automation_level)
        self._match_page.load_state(
            queue=queue,
            val_pool_by_slot=pool,
            threshold=self._input.threshold,
            session_id=self._session_id,
            auto_mode=auto_mode,
            engine_cfg=_sim_cfg,
        )
        self._show_page(self._match_page)
        self._phase = PHASE_A_MATCH
        self._autosave()

    def _on_match_confirmed(self, match: MatchResult) -> None:
        if self._phase == PHASE_A_MATCH:
            self._matches_a.append(match)
        self._schedule_autosave()

    def _on_match_undone(self, match: MatchResult) -> None:
        """Stage 2 되돌리기(#C1) — 집계된 매칭에서도 해당 항목을 제거.

        같은 ``key``(slot/ref명/val명) 의 마지막 항목을 제거한다.
        """
        if self._phase == PHASE_A_MATCH:
            for i in range(len(self._matches_a) - 1, -1, -1):
                if self._matches_a[i].key == match.key:
                    del self._matches_a[i]
                    break
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
        ctx = self._match_page.review_context()
        match_state = self._match_page.get_state()
        val_pool = match_state.val_pool if match_state is not None else None
        # 효율 모드는 score_cache 가 비어 있으므로 후보를 별도 산출해 전달 (#7).
        candidates_by_ref = None
        try:
            candidates_by_ref = self._match_page.build_candidates_by_ref(merged)
        except Exception:
            candidates_by_ref = None
        # 좌표 매칭 모드 정보 전달 — 검토 화면에서 거리(µm) 표시 + 통계 3분류.
        self._match_review_page.load_state(
            merged, score_cache=ctx.score_cache, val_pool=val_pool,
            candidates_by_ref=candidates_by_ref,
            coord_mode=ctx.coord_mode,
            tolerance=ctx.tolerance,
            coord_failed_count=ctx.coord_failed_count,
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
            slot_only_ref=list(self._scan.ref_only),
            slot_only_val=list(self._scan.val_only),
            unmatched_refs=unmatched_refs,
            kla_folders=dict(getattr(self, "_kla_folders", {})),
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
        ctx = self._match_page.review_context()
        self._result_page.show_result(
            result,
            template_path=self._template_used,
            target_path=self._working_xlsx,
            auto_mode=auto_mode,
            val_pool=review_pool,
            score_cache=ctx.score_cache,
            fast_results=ctx.fast_results,
            coord_mode=ctx.coord_mode,
            tolerance=ctx.tolerance,
        )
        self._show_page(self._result_page)
        self._phase = PHASE_NONE
        self._write_run_log()

    def _write_run_log(self) -> None:
        """검증 1회의 사용 통계를 컴퓨터별 폴더에 기록(캐시 빠른 매치는 제외)."""
        try:
            from ..utils import run_log
            sr, inp = self._scan, self._input
            if sr is None or inp is None:
                return
            common = sr.common_slot_names
            ref_photos = sum(len(sr.slots[n].ref_images) for n in common)
            val_photos = sum(len(sr.slots[n].val_images) for n in common)
            elapsed = self._match_page.review_context().elapsed_s
            options = {
                "mode": getattr(inp, "mode", ""),
                "automation": getattr(inp, "automation_level", ""),
                "engine": getattr(inp, "engine_mode", ""),
                "threshold": getattr(inp, "threshold", None),
                "center_crop": False,
                "use_gpu": bool(getattr(inp, "use_gpu", True)),
            }
            kla_used = bool(getattr(self, "_slot_meta_ref", None)
                            or getattr(self, "_slot_meta_val", None))
            ocr_used = any((m or {}).get("method") == "ocr"
                           for m in {**getattr(self, "_slot_meta_ref", {}),
                                     **getattr(self, "_slot_meta_val", {})}.values())
            rec = run_log.build_record(
                options=options, ref_root=inp.ref_root, val_root=inp.val_root,
                slot_count=len(common), ref_photos=ref_photos, val_photos=val_photos,
                elapsed_s=elapsed, kla_used=kla_used, ocr_used=ocr_used)
            run_log.record(rec, elapsed_s=elapsed, uploader=None)
        except Exception as exc:
            _LOG.debug("실행 로그 기록 실패: %s", exc)
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
            sheets.info(
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
        wakelock.release()
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
    def _build_pages(self, progress=None) -> None:
        """페이지 생성 + 스택 추가 + 시그널 배선 (단일 출처 — 재구축 재사용).

        ★ **첫 화면만 먼저** 만든다.  나머지 넷은 cv2·OpenVINO·numpy·PIL 을 끌고
        오므로, 다 불러온 뒤(``_on_backend_loaded``)에 만든다.  색 모드 전환의
        재구축은 이미 준비가 끝난 뒤이므로 그 자리에서 다섯을 모두 만든다.

        ``progress(done, total, message)`` 를 주면 진행을 보고한다(시작 스플래시).
        색 모드 전환의 재구축 때는 주지 않는다 — 그때는 로딩 표시가 없고,
        크로스페이드가 전환을 대신 알린다."""
        report = progress or (lambda *_: None)
        report(0, 1, i18n.KO.SPLASH_PAGES)
        self._build_setup_page()
        report(1, 1)
        if self._backend_ready:
            self._build_remaining_pages()

    def _build_setup_page(self) -> None:
        """첫 화면 — 무거운 의존성이 전혀 없어 창을 즉시 띄울 수 있다.

        셋업 배치는 **하나**다(순서형).  한때 A/B/C 3안을 상단 스위처로 비교했는데,
        사용자가 순서형을 고르면서 나머지 2안과 스위처·setup_layouts 를 제거했다."""
        self._setup_page = SetupPage()
        self._stack.addWidget(self._setup_page)
        self._setup_page.start_requested.connect(self._on_start)
        self._setup_page.update_check_requested.connect(self._manual_update_check)
        # 색 모드/배치 변경 → 페이지 재생성(세션 시작 전에만).
        if hasattr(self._setup_page, "appearance_changed"):
            self._setup_page.appearance_changed.connect(self._on_appearance_changed)
        # 백엔드가 아직이면 '검증 시작' 을 잠그고 버튼에 준비 중을 표기한다.
        self._setup_page.set_backend_ready(self._backend_ready)

    def _build_remaining_pages(self) -> None:
        """나머지 네 페이지 — **메인 스레드에서만** 부른다(Qt 위젯 규칙).

        모듈 import 는 이 시점에 이미 끝나 있다(백그라운드 스레드가 했다).  여기서
        하는 것은 위젯 생성뿐이다."""
        from .pages.match_page import MatchPage
        from .pages.match_review_page import MatchReviewPage
        from .pages.result_page import ResultPage
        from .pages.select_page import SelectPage

        self._select_page = SelectPage()
        self._match_page = MatchPage()
        self._result_page = ResultPage()
        self._match_review_page = MatchReviewPage()
        for w in (self._select_page, self._match_page,
                  self._result_page, self._match_review_page):
            self._stack.addWidget(w)

        self._select_page.finished.connect(self._on_select_finished)
        self._select_page.state_changed.connect(self._schedule_autosave)
        self._match_page.match_confirmed.connect(self._on_match_confirmed)
        self._match_page.match_undone.connect(self._on_match_undone)
        self._match_page.skipped_changed.connect(self._schedule_autosave)
        self._match_page.finished.connect(self._on_match_finished)
        self._match_page.cancelled.connect(self._on_match_cancelled)
        self._result_page.new_session_requested.connect(self._new_session)
        self._match_review_page.finished.connect(self._on_match_review_done)

    def _start_backend_import_async(self) -> None:
        """무거운 모듈을 **백그라운드 스레드에서 import 만** 한다.

        위젯은 만들지 않는다 — Qt 위젯은 GUI 스레드 전용이다.  다 끝나면
        ``_backend_loaded`` 를 emit 해 메인 스레드가 이어받는다(큐 연결).
        실패는 여기서 삼키고, 메인 스레드가 같은 import 를 하며 원래 오류를 낸다."""
        import importlib
        import threading

        def _work() -> None:
            try:
                for name in (
                    "aoi_verification.app.similarity.slot_features",   # cv2
                    "aoi_verification.app.workers.efficiency_matcher",  # openvino
                    "aoi_verification.app.workers.thumbnailer",         # numpy/PIL
                    "aoi_verification.app.ui.pages.select_page",
                    "aoi_verification.app.ui.pages.match_page",
                    "aoi_verification.app.ui.pages.result_page",
                    "aoi_verification.app.ui.pages.match_review_page",
                ):
                    importlib.import_module(name)
            except Exception:
                pass
            finally:
                self._backend_loaded.emit()

        threading.Thread(target=_work, name="backend-import", daemon=True).start()

    def _on_backend_loaded(self) -> None:
        """무거운 모듈이 다 올라왔다 — 나머지 페이지를 만들고 시작 버튼을 연다."""
        if self._select_page is None:
            try:
                self._build_remaining_pages()
            except Exception as exc:          # import 실패가 여기서 표면화된다
                sheets.error(self, i18n.KO.APP_TITLE,
                             i18n.KO.BACKEND_LOAD_FAILED_FMT.format(err=exc))
                return                        # 시작 버튼은 '준비 중' 그대로 둔다
        self._backend_ready = True
        try:
            self._setup_page.set_backend_ready(True)
        except RuntimeError:
            return                            # 창이 이미 닫혔다
        # GPU 임베딩 모델을 미리 컴파일/워밍업 — 첫 슬롯의 커널 JIT 지연 제거(#3).
        # ★ 무거운 import 뒤에 이어 붙인다(같은 모듈이 필요하고, 시작 경로를 비운다).
        self._warmup_accel_async()

    def _on_appearance_changed(self, mode: str = "") -> None:
        """색 모드(어두운 화면) 변경을 화면에 반영한다 — **옛 화면을 걷어내는 크로스페이드**.

        위젯이 생성 시점에 ``theme.INK`` 같은 색을 f-string 으로 굽기 때문에 QSS 재적용만
        으로는 부족하다 → 페이지를 다시 만든다.  **세션 시작 전(PHASE_NONE)에만** 허용해
        진행 중 상태가 사라지지 않게 이중으로 막는다.

        전환이 한 프레임에 튀지 않도록: 옛 색 화면을 스냅샷으로 떠 두고, 아래에서 새 색
        페이지로 즉시 갈아 끼운 뒤 스냅샷을 ``motion.DUR_RECOLOR``(OutQuart)로 빼낸다.

        ★ ``mode`` 는 셋업 페이지가 실어 보낸 새 색 모드다.  **prefs 를 다시 읽지 않는다**
        (전환 경로의 디스크 왕복을 2회 → 1회로).  빈 문자열이면(옛 연결·직접 호출)
        prefs 에서 읽어 오는 예전 경로로 폴백한다.

        ★ 전환 중에는 토글이 **눌리지 않아야 한다.**  두 겹으로 막는다:
        (a) ``_appearance_busy`` 로 재진입 자체를 차단하고,
        (b) 새로 만든 페이지의 스위치를 비활성해 눌리지 않는 것이 **눈에 보이게** 한다.
        (a) 만 있으면 눌러도 아무 일이 없는 '먹은 클릭'이 되고, (b) 만 있으면 페이지
        재생성 사이의 틈으로 두 번째 요청이 새어 든다."""
        if self._phase != PHASE_NONE or self._appearance_busy:
            return
        from . import motion
        self._appearance_busy = True
        try:
            # ★ 스냅샷은 **창 전체**다(옛날엔 `self._stack` 만 찍었다).  상태바는 스택
            #   밖이라 크로스페이드에 덮이지 않았고, QSS 재적용으로 **첫 프레임에 즉시**
            #   새 색이 됐다 — 그래서 전환 700ms 동안 하단바만 혼자 다른 모드로 보였다
            #   (실측: 0ms 에 상태바 (28,26,21) / 본문 (236,233,226)).
            #   `_loading`·`_sheets` 도 창의 자식으로 창 전체를 덮으므로 같은 방식이다.
            snapshot = self.grab() if motion.enabled() else None
            try:
                if not mode:
                    p = _prefs.load()
                    mode = getattr(p, "color_mode", theme.DEFAULT_COLOR_MODE)
                theme.set_color_mode(mode)
            except Exception:
                self._appearance_busy = False
                return
            # ★ **페이지를 다시 만들지 않는다.**  위젯이 색을 f-string 으로 굽던
            #   자리를 전부 QSS role 로 옮겨서(style.qss '인라인에서 옮겨 온 규칙'),
            #   시트를 다시 적용하고 폴리시만 다시 태우면 살아 있는 화면이 새 색을
            #   입는다.  재생성 124ms 가 통째로 빠진다(실측).
            #   ※ QSS 로 못 바꾸는 것 둘은 따로 손본다 —
            #     로고는 **픽스맵 반전**이라 다시 칠해야 하고,
            #     상태바는 인라인 스타일이라 같은 코드로 다시 칠한다.
            self._recolor_in_place()
            self._set_appearance_controls_enabled(False)
            motion.crossfade_from(self, snapshot,
                                  on_done=self._end_appearance_transition)
        except Exception:
            # ★ 어떤 경로로 실패해도 잠금은 반드시 풀린다 — 잠긴 채 남으면 다크 모드를
            #   **영구히** 못 바꾼다(원래 버그보다 나쁘다).
            self._end_appearance_transition()
            raise

    def _recolor_in_place(self) -> None:
        """페이지를 다시 만들지 않고 **살아 있는 화면의 색만** 갈아 끼운다.

        세 가지를 한다:
        1. ``theme.apply_to_app`` — 새 팔레트로 렌더한 style.qss 를 앱에 적용.
        2. **폴리시 재적용** — QSS 를 새로 적용해도 Qt 는 이미 polish 된 위젯을
           자동으로 다시 계산하지 않는 경우가 있다.  트리를 돌며 unpolish/polish 한다.
        3. QSS 로 **못 바꾸는 것** 두 가지:
           · 로고는 픽스맵 RGB 반전이라 다시 칠해야 한다(``app_logo.refresh_all``).
           · 상태바 크레딧은 인라인 스타일이라 같은 코드로 다시 칠한다.

        ★ 3번을 빼먹으면 다크 화면에 어두운 로고가 그대로 남아 안 보인다 — 재생성
        방식과 픽셀 단위로 비교해 잡아낸 자리다(회귀 가드
        ``dev/tests/test_recolor_in_place.py``).
        """
        from .widgets import app_logo

        app = QApplication.instance()
        if app is not None:
            theme.apply_to_app(app)
        self._repolish_tree(self)
        app_logo.refresh_all(self)
        self._apply_statusbar_theme()

    @staticmethod
    def _repolish_tree(root) -> None:
        """위젯 트리 전체에 스타일을 다시 태운다(자식까지).

        ``setStyleSheet`` 만으로는 이미 polish 된 위젯의 속성이 갱신되지 않는 경우가
        있어, 명시적으로 unpolish → polish 한다."""
        style = root.style()
        stack = [root]
        while stack:
            w = stack.pop()
            style.unpolish(w)
            style.polish(w)
            w.update()
            stack.extend(c for c in w.children() if isinstance(c, QWidget))

    def _set_appearance_controls_enabled(self, on: bool) -> None:
        """색 모드 토글의 활성 상태 — 페이지가 재생성되므로 **새** 위젯에 걸어야 한다."""
        sw = getattr(self._setup_page, "_dark_switch", None)
        if sw is not None:
            sw.setEnabled(bool(on))

    def _end_appearance_transition(self) -> None:
        self._appearance_busy = False
        self._set_appearance_controls_enabled(True)

    def _apply_statusbar_theme(self) -> None:
        """상태바 라벨 색을 테마 토큰으로 적용(페이지 밖 위젯).

        ★ 생성자도 이 메서드를 쓴다 — 같은 f-string 을 양쪽에 두면 한쪽만 고쳤을 때
        색 모드를 한 번 바꾼 뒤에야 크레딧 라벨 모양이 달라진다(발견이 늦다).
        토큰(`theme.MUTE`)은 모드에 따라 바뀌므로 상수로 굳힐 수 없다."""
        self._credit_label.setStyleSheet(
            f"color: {theme.MUTE}; padding: 0 8px; font-weight: 600;")

    # ==================================================================
    def _page_order(self, w: QWidget) -> int:
        """흐름 순서(셋업→선별→매칭→검토→결과) — 전환 방향 결정용.

        아직 만들지 않은 페이지(백엔드 로딩 전)는 자리만 비워 둔다 — ``None`` 이
        섞여도 실제 위젯의 순번은 달라지지 않는다."""
        order = (self._setup_page, self._select_page, self._match_page,
                 self._match_review_page, self._result_page)
        try:
            return order.index(w)
        except ValueError:
            return 0

    def _show_page(self, w: QWidget, *, animate: bool = True) -> None:
        """페이지 전환 — 들어오는 화면이 흐름 방향으로 슬라이드+페이드 진입(ease-out).

        나가는 화면은 아래에 두고 새 화면 스냅샷을 안착시킨 뒤 실제 전환(무플래시).
        offscreen/모션 줄이기·최초 표시·동일 페이지면 즉시 스왑."""
        from . import motion
        old = self._stack.currentWidget()
        if old is w or old is None or not animate or not motion.enabled():
            self._stack.setCurrentWidget(w)
            return
        forward = self._page_order(w) >= self._page_order(old)
        self._stack.setCurrentWidget(w)        # 레이아웃 확정 후 스냅샷
        # ★ `w.grab()` 을 쓰지 마라 — 페이지는 배경을 안 칠하고 창이 뒤에서 칠하므로,
        #   떼어 찍으면 빈 자리가 Qt 기본 팔레트(#efefef, 밝은 회색)로 채워진다.
        #   다크 모드에서 '밝은 화면이 먼저 보였다가 어두워지는' 원인이었다
        #   (실측 밝기 85.7 vs 실제 30.4).  자세한 근거는 `motion.snapshot`.
        new_pix = motion.snapshot(w)
        self._stack.setCurrentWidget(old)      # 리페인트 전 원복(사용자엔 불가시)
        motion.transition_in(
            self._stack, new_pix, forward=forward,
            on_commit=lambda: self._stack.setCurrentWidget(w))

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
            } for m in self._matches_a],
        )
        try:
            session_mod.save(state)
        except Exception as exc:
            # ★ 이건 WARNING 이다 — 자동 저장이 죽으면 '이어하기' 가 조용히 사라진다.
            _LOG.warning("세션 자동 저장 실패 — 이어하기가 동작하지 않을 수 있습니다: %s",
                         exc)

    # ==================================================================
    # Cleanup
    # ==================================================================
    def closeEvent(self, event):  # noqa: N802
        # 종료 직전 마지막 크기/최대화 상태 저장 → 다음 실행에서 그대로 복원.
        self._persist_geometry()
        # #14 절전 억제 해제 (남아 있을 경우).
        wakelock.release()
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
