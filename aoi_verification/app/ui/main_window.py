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

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal
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
from .widgets.journey_rail import JourneyRail
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


# 스캔 워커가 도는 동안 파이썬 참조를 붙잡아 두는 곳 — 지역 변수로만 두면 함수가
# 끝나는 순간 GC 가 QThread 를 파괴한다(`pages/setup_page.py` 의 `_LIVE_DIE_SCANS`).
_LIVE_SCANS: set = set()


class _FolderScan(QThread):
    """기준/검증 폴더를 **워커 스레드에서** 훑는다 (U-05).

    `slot.scan` 은 폴더를 하나씩 열어 사진을 열거하므로 NAS 에서는 폴더 수에
    비례해 초 단위로 걸린다.  예전에는 이걸 GUI 스레드에서 돌리면서 진행을
    보여 주려고 콜백마다 ``processEvents`` 를 불렀는데, 그 사이 사용자의 클릭이
    **스캔 도중에 재진입**할 수 있었다.  이제 진행은 시그널로만 올라온다.

    ``stop()`` 은 협조적 중지다 — `scan` 을 중간에 끊을 수단은 없으므로 진행
    콜백에서 예외를 던져 빠져나온다(부분 결과는 버린다)."""

    class _Stopped(BaseException):
        """중지 신호.

        ★ `Exception` 이 아니라 `BaseException` 을 상속한다 — `models/slot.py` 의
        `scan` 은 진행 콜백을 `except Exception: pass` 로 감싸 두었기 때문에
        (콜백이 깨져도 스캔은 끝나야 한다는 의도) 보통 예외로는 **중지가 통째로
        삼켜진다.**  그러면 [중지] 를 눌러도 느린 NAS 스캔이 끝까지 도는데,
        그게 바로 이 기능이 없애려던 상황이다."""

    class _Signals(QObject):
        progress = pyqtSignal(int, int, int)     # token, done, total
        done = pyqtSignal(int, object)           # token, ScanResult
        failed = pyqtSignal(int, str)            # token, message

    def __init__(self, token: int, ref_root, val_root) -> None:
        super().__init__()                  # 부모 없음(위 주석)
        self._token = token
        self._ref_root = ref_root
        self._val_root = val_root
        self._stop = False
        self.signals = self._Signals()

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:      # type: ignore[override]
        def _progress(done: int, total: int) -> None:
            if self._stop:
                raise _FolderScan._Stopped()
            # 25 폴더마다만 보고한다 — 매 폴더 신호는 큐만 채운다.
            if done % 25 == 0 or done == total:
                self.signals.progress.emit(self._token, done, total)

        try:
            sr = scan(self._ref_root, self._val_root, progress=_progress)
        except _FolderScan._Stopped:
            return                          # 취소 — 아무것도 보고하지 않는다
        except Exception as exc:
            # ★ 실패를 알려야 한다.  예전엔 예외가 나면 오버레이가 **켜진 채** 남아
            #   앱이 잠긴 것처럼 보였다(폴더가 그 사이 사라진 경우 등).
            self.signals.failed.emit(self._token, str(exc))
            return
        if not self._stop:
            self.signals.done.emit(self._token, sr)


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
        # 여정 레일 — 5단계 진행 지도.  ★ 스택 **밖**의 고정 칸이다.  로고와 달리
        #   이건 스크롤을 따라 사라지면 안 된다: '지금 몇 번째인지' 는 화면을 스크롤한
        #   순간에도 답이 있어야 하는 질문이라 상시 가시성이 이 위젯의 존재 이유다.
        #   대신 42px 을 영구히 쓴다(구조개편 1안-A 가 명시한 대가).
        self._rail = JourneyRail(central)
        self._rail.step_clicked.connect(self._on_rail_step_clicked)
        col.addWidget(self._rail)
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
        # 오버레이가 기다리는 몫 = **첫 슬롯의 사진 수**(나머지는 뒤에서 계속 데운다).
        self._thumb_wait_n = 0
        self._thumb_wait_slot = ""
        self._thumb_wait_msg = ""
        self._sizing_tier: Optional[config.SizingTier] = None
        # 폴더 스캔 워커 (U-05) + [중지] 가 무엇을 멈춰야 하는지 알려 주는 현재 단계 (P-09).
        self._scan_token = 0
        self._scan_worker: Optional[_FolderScan] = None
        self._stage = ""
        self._scan: Optional[ScanResult] = None
        self._input: Optional[SetupInput] = None
        self._phase: str = PHASE_NONE
        self._matches_a: list[MatchResult] = []
        self._skipped_a: dict[str, list[ImageItem]] = defaultdict(list)
        # 올인원/사진 직접 선택 모드의 매치 검토 결과 (#3).
        # 비어있지 않으면 _finish_session 이 _matches_a/_b 대신 이걸 사용한다.
        self._reviewed_matches: list[MatchResult] = []
        self._reviewed_unmatched: list[MissEntry] = []
        self._reviewed_unmatched_keys: set = set()
        # 검토 대상 전체('매치 없음' 표시분 포함) — 결과↔검토 왕복의 기반.
        self._reviewed_all_matches: list[MatchResult] = []
        self._run_log_written = False
        # 기준 사진 기록 저장 디바운스 — ★ self 를 부모로(정적 singleShot 금지).
        self._ref_history_timer = QTimer(self)
        self._ref_history_timer.setSingleShot(True)
        self._ref_history_timer.timeout.connect(self._flush_ref_history)
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
                    msg = f"{msg}{i18n.KO.CAUSE_PREFIX}{reason}"
                self._update_none.emit(msg)

        threading.Thread(target=_work, name="update-check-manual",
                         daemon=True).start()

    def _on_update_none(self, msg: str) -> None:
        # 최신이거나 확인 불가인 결과다 — 중립 제목을 쓴다('업데이트 있음' 은 실제로
        # 새 버전이 있는 _on_update_found 전용).
        sheets.info(self, i18n.KO.UPDATE_CHECK_TITLE, msg)

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
                    msg = f"{msg}{i18n.KO.CAUSE_PREFIX}{updater.last_error()}"
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

    def _on_select_cancelled(self) -> None:
        """Stage 1 에서 설정 화면으로 복귀 — 진행 중이던 선별 상태를 버린다."""
        self._phase = PHASE_NONE
        self._stage1_a_snapshot = None
        session_mod.clear()
        # 설정으로 돌아가는 다른 경로들과 같게 절전 억제를 푼다 — 빠뜨리면
        # 검증을 접었는데도 화면보호기가 세션 내내 막힌 채로 남는다.
        wakelock.release()
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
                ref_only=", ".join(sr.ref_only) or i18n.KO.VALUE_NONE,
                val_only=", ".join(sr.val_only) or i18n.KO.VALUE_NONE,
            # ★ 버튼 라벨용 명사구에 " ?" 를 붙여 만든 비문("… 열기 ?")이었다.
            #   완결된 질문 문장을 i18n 에 두고 그대로 쓴다.
            ) + "\n\n" + i18n.KO.SLOT_MAP_ASK,
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
        # ★ 단계를 **다시 준다**.  `_ask_kla_side` 모달이 오버레이를 내렸다 올리는데,
        #   step 없이 띄우면 `_apply_stage(None, None)` 이 서수 줄과 여정 행을 지운다 —
        #   KLA 해석은 스캔(1단계)의 뒷부분이므로 그 자리를 그대로 유지한다
        #   (i18n `LOAD_JOURNEY_STEPS` 주석의 '단계 수를 바꾸지 않는다' 가 이 뜻이다).
        self._loading.show_overlay(i18n.KO.LOAD_KLA_INFO,
                                   step=(1, 3),
                                   steps=i18n.KO.LOAD_JOURNEY_STEPS)
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
                i18n.KO.LOAD_KLA_OCR, step=(1, 3),
                steps=i18n.KO.LOAD_JOURNEY_STEPS)
            worker = WaferIdOcrWorker(jobs, parent=self)
            self._ocr_worker = worker          # GC 방지 참조 보관

            def _on_progress(d: int, t: int) -> None:
                self._loading.set_progress(
                    d, t, i18n.KO.LOAD_KLA_OCR)

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
            coord_tolerance=float(getattr(inp, "coord_tolerance",
                                          config.DEFAULT_COORD_TOLERANCE)),
        )


    def _on_start(self, inp: SetupInput) -> None:
        self._input = inp
        # 레일 오른쪽의 판정 기준 — 이 세션의 기준은 지금 확정된다.
        self._refresh_rail_criteria()
        # #14 세션 동안 OS 절전/화면보호기 억제.
        wakelock.acquire()
        self._matches_a.clear()
        self._skipped_a.clear()
        self._reviewed_matches.clear()
        self._reviewed_unmatched.clear()
        self._reviewed_unmatched_keys = set()
        self._reviewed_all_matches = []
        self._run_log_written = False
        self._thumbs_handled = False        # 썸네일 완료 one-shot 가드 리셋(#C2)
        # 타일 픽스맵 캐시 비우기 — 폴더가 바뀌어도 stale 픽스맵이 남지 않게(#렉).
        try:
            from ..utils import image_io as _io
            _io.clear_tile_cache()
        except Exception as exc:
            _LOG.debug("타일 픽스맵 캐시 비우기 실패: %s", exc)
        self._session_id = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

        # ★ 오버레이를 **먼저** 띄운다.  아래 `_prepare_working_file` 은 결과 폴더로
        #   양식을 복사하는데(shutil.copyfile), 그 폴더가 NAS 면 수 초가 걸린다 —
        #   예전에는 그동안 아무 표시도 없어 [검증 시작] 이 먹지 않은 것처럼 보였다.
        self._loading.show_overlay(i18n.KO.LOAD_SCAN, cancelable=True,
                                   step=(1, 3),
                                   steps=i18n.KO.LOAD_JOURNEY_STEPS)
        QApplication.processEvents()

        # 양식 폴더의 양식.xlsx 를 결과 폴더로 복사 → 작업 파일 준비 ----
        self._prepare_working_file(inp)

        # 원본 mtime 메모이즈 초기화 — 이번 세션 동안 캐시 키용 stat() 을 경로당 1회로(#5).
        from ..utils import cache as _cache
        _cache.reset_mtime_cache()
        self._kla_folders: dict[str, str] = {}      # KLA slot명→폴더명(엑셀 회색 표기)

        # 폴더 스캔 — 워커에서 (U-05).  진행은 시그널로만 올라온다.
        self._stage = "scan"
        self._scan_token += 1
        worker = _FolderScan(self._scan_token, inp.ref_root, inp.val_root)
        worker.signals.progress.connect(self._on_scan_progress)
        worker.signals.done.connect(self._on_scan_done)
        worker.signals.failed.connect(self._on_scan_failed)
        _LIVE_SCANS.add(worker)
        worker.finished.connect(lambda w=worker: _LIVE_SCANS.discard(w))
        self._scan_worker = worker
        worker.start()

    def _on_scan_progress(self, token: int, done: int, total: int) -> None:
        if token != self._scan_token:
            return
        self._loading.set_progress(done, total, i18n.KO.LOAD_SCAN)

    def _on_scan_failed(self, token: int, message: str) -> None:
        """스캔이 실패했다 — 오버레이를 반드시 내리고 알린다.

        예전에는 예외가 나면 오버레이가 켜진 채 남아 앱이 잠긴 것처럼 보였다."""
        if token != self._scan_token:
            return
        self._scan_worker = None
        self._stage = ""
        self._loading.hide_overlay()
        _LOG.warning("폴더 스캔 실패: %s", message)
        sheets.warn(self, i18n.KO.APP_TITLE,
                    i18n.KO.WARN_SCAN_FAILED_FMT.format(detail=message))

    def _on_scan_done(self, token: int, sr: ScanResult) -> None:
        """스캔이 끝났다 — 여기서부터는 예전 `_on_start` 의 나머지 그대로다.

        (모달을 여는 `_ask_kla_side` 부터는 반드시 메인 스레드여야 한다.)"""
        if token != self._scan_token:
            return                          # 취소·재시작으로 밀려난 옛 스캔
        self._scan_worker = None
        self._stage = ""
        inp = self._input
        if inp is None:
            return

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
        """slot 확정 후 썸네일 캐시 사전 생성(백그라운드) → 다음 단계.

        ★ 오버레이는 **첫 슬롯 몫만** 기다린다.  예전에는 공통 슬롯 전부(기준+검증)의
        썸네일과 중간 이미지를 다 만든 **뒤에야** 화면을 내줬다 — 슬롯 25 · 사진 1만
        장이면 사진 1장당 2개(썸네일·중간)라 2만 번의 디코드·인코드를 다 볼 때까지
        아무것도 못 했고, 진행 수치도 1만 단위라 바가 멈춘 것처럼 보였다.  Stage 1 은
        슬롯을 하나씩 보여 주므로(`select_page._is_single_slot_mode`) 지금 필요한 것은
        첫 슬롯뿐이다.  나머지는 풀을 **멈추지 않고** 뒤에서 계속 데운다 — 큐를
        슬롯 순서(`common`, 정렬됨)로 넣고 Stage 1 도 같은 순서로 진행하므로 사전
        생성이 사용자보다 앞서 달린다.

        ⚠ 사전 생성을 아예 없애면 안 된다 — 타일은 GUI 스레드에서
        `cached_tile_pixmap` → `get_thumb_path` 로 **없으면 그 자리에서 만든다**.
        기다리는 몫을 줄이는 것이지 일을 없애는 게 아니다."""
        sr = self._scan
        if sr is None:
            return
        # 매핑/OCR 단계에서 오버레이가 숨겨졌을 수 있으므로 **반드시 다시 띄운다** —
        # 그렇지 않으면 썸네일 생성 동안 메인 창이 클릭 가능 상태로 남아 버그 유발.
        # (set_progress 는 숨겨진 오버레이를 다시 띄우지 않으므로 show_overlay 필수.)
        # 썸네일 단계는 가장 오래 걸리므로 [중지] 로 건너뛸 수 있게 한다(#C2).
        self._loading.show_overlay(
            i18n.KO.LOAD_THUMBNAIL, cancelable=True,
            step=(2, 3), steps=i18n.KO.LOAD_JOURNEY_STEPS)
        QApplication.processEvents()
        # 슬롯 순서대로 묶어 둔다 — 큐에 넣는 순서가 곧 사용자가 지나가는 순서다.
        by_slot: list[tuple[str, list[ImageItem]]] = []
        all_items: list[ImageItem] = []
        for name in common:
            slot = sr.slots[name]
            items = list(slot.ref_images) + list(slot.val_images)
            by_slot.append((name, items))
            all_items.extend(items)

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

        # 기다리는 몫 = 첫 슬롯.  수치도 이 몫으로 보여 준다 — 1만 분모에 한 칸씩
        # 차던 바가 '멈춘 것처럼' 보이던 원인이고, 남은 시간 추정도 여기서만 맞는다
        # (뒤 슬롯은 사용자가 화면을 쓰는 동안 데워지므로 대기 시간이 아니다).
        self._thumb_wait_slot = by_slot[0][0] if by_slot else ""
        self._thumb_wait_n = len(by_slot[0][1]) if by_slot else 0
        rest = max(0, len(common) - 1)
        self._thumb_wait_msg = (
            i18n.KO.LOAD_THUMBNAIL_SLOT_REST_FMT.format(
                slot=self._thumb_wait_slot, rest=rest)
            if rest else
            i18n.KO.LOAD_THUMBNAIL_SLOT_FMT.format(slot=self._thumb_wait_slot)
        )

        # 기본 티어보다 낮은 화질이 적용되면 한 번만 안내.
        if self._sizing_tier is not config.SIZING_TIERS[0]:
            self._loading.set_progress(
                0, self._thumb_wait_n,
                i18n.KO.SIZE_TIER_NOTICE_FMT.format(
                    thumb=self._sizing_tier.thumb_px,
                    q=self._sizing_tier.thumb_q,
                ),
            )

        self._loading.set_progress(0, self._thumb_wait_n, self._thumb_wait_msg)

        # 다중 스레드 + 우선순위 큐 풀 사용.  **슬롯 순서대로** 넣고 앞의 둘만
        # 우선순위를 올린다 — 첫 슬롯(지금 보여 줄 것) · 둘째 슬롯(look-ahead).
        # ※ 넣는 순서가 곧 Stage 1 의 진행 순서(둘 다 슬롯명 오름차순)라 이 뒤로는
        #   재정렬할 것이 없다.  풀의 `reprioritize_slot` 은 그래서 호출부가 없지만,
        #   '활성 슬롯이 넣은 순서를 벗어나는' 경우를 위한 수단이라 남겨 둔다.
        # (썸네일러는 모듈 최상위가 아니라 여기서 불러온다 — 위 import 주석 참조.)
        from ..workers.thumbnailer import (PRIORITY_ACTIVE_SLOT,
                                           PRIORITY_BACKGROUND,
                                           PRIORITY_NEXT_SLOT, ThumbnailPool)
        if self._thumb_pool is not None:
            self._thumb_pool.stop()
        self._thumb_pool = ThumbnailPool(
            tier=self._sizing_tier, also_mid=True, parent=self,
        )
        for idx, (_name, items) in enumerate(by_slot):
            self._thumb_pool.enqueue(items, priority=(
                PRIORITY_ACTIVE_SLOT if idx == 0
                else PRIORITY_NEXT_SLOT if idx == 1
                else PRIORITY_BACKGROUND))
        self._thumb_pool.signals.progress.connect(self._on_thumb_progress)
        self._thumb_pool.signals.finished.connect(self._on_thumbs_ready)
        # 빈 큐 (모든 슬롯의 양측이 0 장) 일 때 워커가 한 번도 progress 를
        # 보내지 않아 ``finished`` 가 emit 되지 않는 행 (Bug #5) 을 방지 — 풀을
        # 시작하지 않고 즉시 다음 단계로.
        if not all_items:
            QTimer.singleShot(0, self._on_thumbs_ready)
            return
        self._thumb_pool.start()

    def _on_thumb_progress(self, done: int, _total: int, _path: str) -> None:
        """사전 생성 진행 — **첫 슬롯 몫을 채우면 그 자리에서 다음 단계로 넘어간다.**

        넘어간 뒤에도 풀은 계속 돌아 진행 신호가 올라오는데, 그때는 오버레이가
        이미 내려갔으므로 **아무것도 하지 않는다**.  (`set_progress` 는 숨겨진
        오버레이를 되살리지 않지만, 라벨 갱신과 ETA 계산이 사용자가 화면을 쓰는
        동안 매 신호마다 도는 것 자체가 낭비다.)"""
        if self._thumbs_handled:
            return
        need = self._thumb_wait_n
        self._loading.set_progress(min(done, need), need, self._thumb_wait_msg)
        if done >= need:
            self._on_thumbs_ready()

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
        self._loading.set_stage((3, 3), i18n.KO.LOAD_JOURNEY_STEPS)
        self._loading.set_progress(0, 0, i18n.KO.LOAD_STAGE_PREP)
        QTimer.singleShot(0, self._continue_after_thumbs)

    def _on_loading_cancel(self) -> None:
        """로딩 오버레이의 [중지] — **단계마다 뜻이 다르다** (P-09).

        - 폴더 스캔 중: 세션을 접고 설정 화면으로 돌아간다.  스캔은 이후 모든
          단계의 입력이라 '건너뛰고 진행' 이 성립하지 않는다 — 부분 결과는 버린다.
        - 썸네일 사전생성 중: 썸네일은 비필수(이후 UI 가 필요 시 생성)이므로
          풀만 멈추고 곧바로 다음 단계로 진행한다.

        ``ThumbnailPool`` 은 QObject(워커만 QThread)라 ``isRunning()`` 이 없다.
        이미 다음 단계로 넘어갔는지는 one-shot 플래그로 판별하고, ``stop()`` 은
        이미 끝난 풀에 호출해도 무해하므로 그 조합으로 가드한다."""
        if self._stage == "scan":
            self._cancel_scan()
            return
        pool = self._thumb_pool
        if pool is not None and not self._thumbs_handled:
            pool.stop()                 # 이미 완료된 풀이어도 무해(플래그만 set)
            self._on_thumbs_ready()     # one-shot 가드로 1회만 진행

    def _cancel_scan(self) -> None:
        """스캔 취소 — 워커를 멈추고 세션 상태를 깨끗이 되돌린다.

        ``_scan_token`` 을 올려 두면 늦게 도착한 done/failed 가 옛 세대로 버려지므로
        스레드가 끝나기를 기다릴 필요가 없다(기다리면 그게 곧 멈춤이다)."""
        self._scan_token += 1
        w = self._scan_worker
        self._scan_worker = None
        self._stage = ""
        if w is not None:
            w.stop()
        # 부분 상태를 남기지 않는다 — 다음 [검증 시작] 이 옛 스캔 위에 얹히면 안 된다.
        self._scan = None
        wakelock.release()
        self._loading.hide_overlay()
        self._show_page(self._setup_page)

    def _continue_after_thumbs(self) -> None:
        """``_on_thumbs_ready`` 의 안전한 후속 — 모달/페이지 전환 OK.

        단계 3 표시는 `_on_thumbs_ready` 가 이미 세웠다(이 함수를 부르는 유일한
        곳이다).  여기서 또 부르면 `set_progress(0, 0, …)` 이 `_enter_busy` 를
        한 번 더 돌려 혜성 스윕과 ETA 시계를 이유 없이 재시작한다."""
        if self._input is None:
            return
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
        # ★ 결과 화면에서 검토로 되돌아올 때 되살리려면 '매치 없음' 표시를 키로 들고
        #   있어야 한다 — load_state 는 진입할 때마다 그 집합을 비운다.
        self._reviewed_unmatched_keys = set(
            self._match_review_page.unmatched_keys())
        # ★ 그리고 **표시된 행 자체**도 들고 있어야 한다.  `kept` 에는 '매치 없음' 행이
        #   빠져 있어서, 그걸 기반으로 되돌아가면 복원할 행이 없다 → 다음 [검토 완료]
        #   에서 그 사진들이 매치에도 미매칭에도 없는 상태가 돼 결과에서 사라진다.
        self._reviewed_all_matches = self._match_review_page.all_matches()
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
            # ★ 여기 있던 '후보 선별이 끝났습니다' 확인창을 없앴다 — 선택지가 없는
            #   안내인데 매 세션 클릭 1회를 강제했다.  전환 사실은 곧바로 뜨는 매칭
            #   오버레이의 첫 문구가 말한다(큐가 비어 자동으로 건너뛴 경우 포함).
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
        """Stage 2 종료 — 미탐 기록을 넘겨받고 검토 화면으로.

        ★ **두 번 불려도 결과가 같아야 한다(멱등).**  예전엔 `st.no_match` 를 중복
        검사 없이 `extend` 했다.  `finished` 가 두 번 나오는 경로가 실제로 있었고
        (검토 화면으로 전환하는 246ms 동안 Ctrl+Z 한 번 → `_undo_match` → `_advance`
        → 두 번째 `finished`), 그때 **엑셀 미탐 시트가 5행 → 10행**으로 늘었다 —
        같은 사진이 두 번 찍힌 것이다.  총 매치 수(`_matches_a`)는 정상이라 숫자로는
        이상을 알 수 없었다.
        `motion.transition_in` 이 그 전환 구간의 키를 막아 원인 하나는 닫혔지만,
        신호가 두 번 오는 경로는 또 생길 수 있으므로 **받는 쪽도 멱등하게** 둔다.
        """
        if self._phase == PHASE_A_MATCH:
            st = self._match_page.get_state()
            if st is not None:
                # 미탐으로 기록할 것은 ‘매칭 없음 확정’ 만. ‘잠시 보류’ 는 사용자
                # 결정 미정 → 미탐 시트에 넣지 않는다.
                for slot, items in st.no_match.items():
                    seen = {it.key for it in self._skipped_a[slot]}
                    self._skipped_a[slot].extend(
                        it for it in items if it.key not in seen
                    )
            self._proceed_to_review_or_finish()

    def _proceed_to_review_or_finish(self) -> None:
        """자동 매치 결과를 MatchReviewPage 로 넘겨 검토하게 한다."""
        # ★ 세션 입력이 없으면 **검토할 것도 끝낼 것도 없다.**  예전엔 여기서
        #   `_finish_session()` 을 불렀는데 그 함수 첫 줄이
        #   `assert self._scan is not None and self._input is not None` 이라
        #   자기모순이었다(도달하면 반드시 AssertionError).  도달 경로는
        #   [새 검증 시작] 이 세션 상태를 버린 뒤 Stage 2 의 `finished` 가 뒤늦게
        #   오는 경우다 — 그때는 이미 `_new_session` 이 첫 화면으로 보냈으므로
        #   조용히 물러나는 것이 맞다.
        if self._input is None or self._scan is None:
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
            review_scores=ctx.review_scores,
            candidates_by_ref=candidates_by_ref,
            coord_mode=ctx.coord_mode,
            tolerance=ctx.tolerance,
            coord_failed_count=ctx.coord_failed_count,
        )
        self._show_page(self._match_review_page)

    def _on_result_edited(self, matches: list, unmatched: list) -> None:
        """결과 화면에서 결과가 바뀌었다(실패 검토로 신규 매치 확정 등).

        ★ **왕복의 기반(`_reviewed_all_matches`)까지 함께 고쳐야 한다.**  `_reenter_review`
        는 그 목록을 먼저 보는데, 여기서 갱신하지 않으면 비어 있지 않은 **옛 목록**이
        항상 이겨 실패 검토로 확정한 매치가 검토 화면에 실리지 않는다.  그 상태로
        [검토 완료] 를 다시 누르면 그 쌍이 결과에서 사라지고 기준 사진이 '미매칭' 으로
        되돌아간다 — 사용자가 눈으로 확인해 확정한 것이 조용히 없어지는 것이다.
        """
        self._reviewed_matches = list(matches)
        self._reviewed_unmatched = list(unmatched)
        # 짝을 찾은 기준 사진은 더 이상 '매치 없음' 이 아니다 — 그 표시를 걷어낸다.
        matched_refs = {m.ref_path for m in matches}
        # ★ `_skipped_a`(자동 매칭이 짝을 못 찾은 기록)에서도 빼야 한다.  `_finish_session`
        #   은 미매칭을 매번 **여기서 다시 만들기** 때문에, 이걸 빼지 않으면 실패 검토로
        #   해결한 사진이 왕복 뒤 '미매칭' 으로 되살아나 매치와 양쪽에 남는다.
        for slot, its in list(self._skipped_a.items()):
            kept = [it for it in its if it.path not in matched_refs]
            if len(kept) != len(its):
                self._skipped_a[slot] = kept
        still_marked = [m for m in getattr(self, "_reviewed_all_matches", [])
                        if m.ref_path not in matched_refs
                        and m.key in self._reviewed_unmatched_keys]
        # 매치가 있는 행은 `matches` 가, 여전히 표시된 행은 옛 목록이 가져온다.
        self._reviewed_all_matches = list(matches) + still_marked
        self._reviewed_unmatched_keys = {m.key for m in still_marked}

    def _reenter_review(self) -> None:
        """결과 화면 → 매치 검토 화면 복귀(U-10).

        ★ `_proceed_to_review_or_finish` 를 그대로 쓰면 안 된다 — 그쪽은 원본 자동
        매치(`_merge_matches`)로 화면을 다시 만들어 사용자가 검토에서 한 스왑·매치
        없음 표시를 **전부 지워 버린다**.  검토 결과(`_reviewed_matches`)를 기반으로,
        '매치 없음' 표시까지 복원해서 들어간다."""
        if self._match_review_page is None or self._match_page is None:
            return
        # '매치 없음' 표시까지 되살리려면 그 행들이 있어야 하므로 **전체 목록**을
        #   기반으로 한다(`_reviewed_matches` 는 표시된 행이 빠진 kept 다).
        base = list(getattr(self, "_reviewed_all_matches", None)
                    or self._reviewed_matches or self._merge_matches())
        ctx = self._match_page.review_context()
        match_state = self._match_page.get_state()
        val_pool = match_state.val_pool if match_state is not None else None
        try:
            candidates_by_ref = self._match_page.build_candidates_by_ref(base)
        except Exception:
            candidates_by_ref = None
        self._match_review_page.load_state(
            base, score_cache=ctx.score_cache, val_pool=val_pool,
            review_scores=ctx.review_scores,
            candidates_by_ref=candidates_by_ref,
            coord_mode=ctx.coord_mode,
            tolerance=ctx.tolerance,
            coord_failed_count=ctx.coord_failed_count,
            unmatched_keys=getattr(self, "_reviewed_unmatched_keys", None),
        )
        self._notify_ready_for_review()
        self._show_page(self._match_review_page)

    def _notify_ready_for_review(self) -> None:
        """수 분짜리 자동 매칭이 끝난 줄 모르고 다른 창을 보고 있을 수 있다.

        ★ 창이 이미 활성이면 부르지 않는다 — 보고 있는데 작업표시줄이 깜빡이면 잡음이다."""
        if not self.isActiveWindow():
            QApplication.alert(self)

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
        # ★ 불변식: **짝을 찾은 기준 사진은 미매칭에 있을 수 없다.**  이 함수는 결과↔검토를
        #   오갈 때마다 다시 도는데, 미매칭은 `_skipped_a`(자동 매칭 실패 기록)에서 매번
        #   새로 만들고 매치는 편집된 목록에서 온다 — 두 출처가 어긋나면 같은 사진이
        #   '매칭' 과 '미매칭' 양쪽에 남아 엑셀에 두 줄로 찍힌다(실제로 그랬다).
        #   출처를 고치는 것과 별개로, 나가는 값에서 한 번 더 막는다.
        matched_refs = {m.ref_path for m in merged}
        seen: set = set()
        deduped: list[MissEntry] = []
        for u in unmatched_refs:
            key = (u.slot, Path(u.path))
            if Path(u.path) in matched_refs or key in seen:
                continue
            seen.add(key)
            deduped.append(u)
        unmatched_refs = deduped

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
            review_scores=ctx.review_scores,
            coord_classical_refs=ctx.coord_classical_refs,
        )
        self._show_page(self._result_page)
        self._phase = PHASE_NONE
        self._write_run_log()

    def _write_run_log(self) -> None:
        """검증 1회의 사용 통계를 컴퓨터별 폴더에 기록(캐시 빠른 매치는 제외)."""
        # ★ 결과 ↔ 검토를 오가면 `_finish_session` 이 여러 번 돈다.  사용 통계는
        #   세션당 한 번만 남긴다(오가는 횟수가 검증 건수처럼 쌓이면 안 된다).
        if getattr(self, "_run_log_written", False):
            return
        self._run_log_written = True
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
                    slot=slot, side="ref", path=it.path, note=i18n.KO.NOTE_UNMATCHED,
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
        self._reviewed_unmatched_keys = set()
        self._reviewed_all_matches = []
        self._run_log_written = False
        self._phase = PHASE_NONE
        # ★ **세션 입력·스캔 결과도 함께 버린다.**  `_autosave` 는 결정마다뿐 아니라
        #   30 초 타이머(`_autosave_timer`)로도 돌기 때문에, `_input` 을 남겨 두면
        #   맨 위에서 지운 세션 파일이 **옛 입력 그대로 다시 쓰여** 다음 실행의
        #   '이어하기' 가 이미 끝난 검증을 되살린다.  `_autosave` 가 읽는 것은
        #   `_input`(mode·ref_root·val_root·호기·threshold)·`_session_id`·`_phase`
        #   ·`_matches_a` 이고 앞의 둘만 남아 있었다 — 그래서 둘 다 지운다.
        #   `_scan`·`_working_xlsx` 는 자동 저장이 읽지는 않지만 같은 세션 상태다.
        #   남겨 두면 다음 [검증 시작] 이 옛 스캔/옛 결과 파일 위에 얹힌다
        #   (`_cancel_scan` 이 `_scan = None` 을 두는 것과 같은 처방).
        #   넷 다 `_on_start`/`_on_scan_done` 이 새로 채우므로 지워도 안전하다.
        self._input = None
        self._refresh_rail_criteria()      # 기준이 사라졌으니 레일도 비운다
        self._scan = None
        self._working_xlsx = None
        self._session_id = ""
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
        # Stage 1 [← 설정으로] — Stage 2 의 취소와 같은 자리로 돌아간다.
        self._select_page.cancelled.connect(self._on_select_cancelled)
        self._select_page.state_changed.connect(self._schedule_autosave)
        self._match_page.match_confirmed.connect(self._on_match_confirmed)
        self._match_page.match_undone.connect(self._on_match_undone)
        self._match_page.skipped_changed.connect(self._schedule_autosave)
        self._match_page.finished.connect(self._on_match_finished)
        self._match_page.cancelled.connect(self._on_match_cancelled)
        self._result_page.new_session_requested.connect(self._new_session)
        self._match_review_page.finished.connect(self._on_match_review_done)
        self._result_page.back_to_review_requested.connect(self._reenter_review)
        # 실패 검토에서 새로 확정한 매치가 결과 객체에만 남아 있으면, 검토로 돌아갔다
        # 재완료할 때 새 FinalResult 가 만들어지며 사라진다 — 여기로 되돌려 받는다.
        self._result_page.result_edited.connect(self._on_result_edited)

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
        # ★ 트리 **전체**를 한 프레임에 다시 폴리시하면 메인 스레드가 실측 ~125ms
        #   멈춘다(페이지 5장 × 위젯 수백 개).  그 정지가 곧 '버벅임' 이다.
        #   지금 **보이는 페이지와 창 밖 장식(레일·상태바·시트/오버레이)** 만 즉시
        #   처리하고, 숨은 페이지 4장은 이벤트 루프가 빈 틈에 한 장씩 나눠 맡긴다 —
        #   숨은 페이지는 다시 보일 때까지 색이 틀려도 사용자가 볼 수 없다.
        #   (구조개편 24안 — 정지 125 → ~30ms)
        self._repolish_visible_now()
        app_logo.refresh_all(self)
        self._apply_statusbar_theme()
        self._queue_hidden_page_repolish()

    def _repolish_visible_now(self) -> None:
        """보이는 페이지 + 스택 밖 창 장식만 즉시 다시 폴리시한다.

        ★ **창 자신(그리고 그 척추)** 을 먼저 처리한다.  화면 본문의 바탕색은
        페이지가 아니라 `QMainWindow { background-color: $bg }` 가 칠한다
        (style.qss 는 전역 QWidget 에 배경을 주지 않는다 — 스크롤이 죽는다).
        페이지만 다시 폴리시하면 **본문 바탕이 옛 색으로 남아** 하단바와 따로
        논다(회귀 가드 `test_recolor_covers_window`)."""
        style = self.style()

        def one(w):
            if w is None:
                return
            style.unpolish(w)
            style.polish(w)
            w.update()

        one(self)                      # 창 자신 — 본문 바탕을 칠하는 주체
        one(self.centralWidget())      # 척추: 중앙 위젯 → 스택
        one(self._stack)
        current = self._stack.currentWidget()
        if current is not None:
            self._repolish_tree(current)
        # 스택 밖(레일·상태바·시트·로딩 오버레이)은 항상 보이므로 함께 간다.
        for w in (getattr(self, "_rail", None), self._status_bar,
                  getattr(self, "_sheets", None), getattr(self, "_loading", None)):
            if w is not None:
                self._repolish_tree(w)

    def _queue_hidden_page_repolish(self) -> None:
        """숨은 페이지들을 **한 장씩** 뒤늦게 다시 폴리시한다.

        ★ 타이머는 창에 parent 를 둔다(정적 `QTimer.singleShot` 금지 — 창이 먼저
        닫히면 죽은 위젯을 건드린다).  간격 0 이면 이벤트 루프가 한 바퀴 도는
        사이마다 한 장씩 처리돼, 화면이 멈추는 구간이 페이지 하나 분량으로 쪼개진다.
        ★ 다시 보이는 순간에도 안전하다 — `_show_page` 는 색을 건드리지 않지만
          이 큐가 그 전에 끝나거나, 끝나지 않았다면 그 페이지 차례가 곧 온다."""
        current = self._stack.currentWidget()
        pending = [p for p in (self._setup_page, self._select_page,
                               self._match_page, self._match_review_page,
                               self._result_page)
                   if p is not None and p is not current]
        if not pending:
            return
        timer = getattr(self, "_repolish_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._repolish_next_hidden)
            self._repolish_timer = timer
        self._repolish_queue = pending
        timer.start(0)

    def _repolish_next_hidden(self) -> None:
        queue = getattr(self, "_repolish_queue", None)
        if not queue:
            return
        page = queue.pop(0)
        try:
            self._repolish_tree(page)
        except RuntimeError:
            pass                       # 페이지가 사라졌다 — 다음 장으로
        if queue:
            self._repolish_timer.start(0)

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

    # 각 화면에서 **실제로 존재하는** 복귀 경로.  {현재 단계: 갈 수 있는 단계들}
    # ★ '완료했으니 갈 수 있다' 가 아니다.  없는 경로를 눌리게 두면 죽은 클릭이
    #   되고, 있는 경로를 막으면 레일이 거짓말을 한다.  여기 적힌 것만 눌린다.
    _RAIL_ROUTES: dict[int, tuple[int, ...]] = {
        1: (0,),        # 후보 선별 → 설정   (select_page 가 결정 폐기를 확인한다)
        2: (0,),        # 매칭     → 설정   (match_page 가 계산 폐기를 확인한다)
        3: (0,),        # 매치 검토 → 설정   (여기서 묻는다 — 화면에 경로가 없었다)
        4: (3,),        # 결과     → 매치 검토 (기존 [← 검토 화면으로] 와 같은 길)
    }

    def _sync_rail(self, w: QWidget, *, animate: bool = False) -> None:
        """레일의 현재 단계와 '눌러서 갈 수 있는 단계' 를 화면에 맞춘다.

        ``animate`` 는 페이지가 실제로 슬라이드할 때만 True 다 — 그때 레일 눈금이
        먼저 채워지고, 창은 그 뒤에 화면을 밀어 넣는다(21안-A)."""
        idx = self._page_order(w)
        self._rail.set_current(idx, animate=animate)
        self._rail.set_navigable(self._RAIL_ROUTES.get(idx, ()))

    def _on_rail_step_clicked(self, target: int) -> None:
        """레일에서 지난 단계를 눌렀다 — 그 화면이 가진 복귀 흐름을 그대로 부른다.

        ★ 폐기 확인을 여기서 새로 쓰지 않는다.  선별/매칭은 이미 자기 규칙(무엇이
        사라지는지)을 알고 물어보므로 그쪽을 부르는 것이 단일 출처다."""
        cur = self._page_order(self._stack.currentWidget())
        if target not in self._RAIL_ROUTES.get(cur, ()):
            return
        if cur == 1 and self._select_page is not None:
            self._select_page.request_back_to_setup()
        elif cur == 2 and self._match_page is not None:
            self._match_page.request_back_to_setup()
        elif cur == 3:
            if sheets.ask(self, i18n.KO.JOURNEY_BACK_TO_SETUP_TITLE,
                          i18n.KO.JOURNEY_BACK_TO_SETUP_BODY,
                          QMessageBox.StandardButton.Yes
                          | QMessageBox.StandardButton.No,
                          QMessageBox.StandardButton.No
                          ) == QMessageBox.StandardButton.Yes:
                self._new_session()
        elif cur == 4:
            self._reenter_review()

    def _refresh_rail_criteria(self) -> None:
        """레일 오른쪽의 판정 기준 한 줄.

        ★ 문구를 여기서 조립하지 않는다 — `SetupPage.judgement_text()` 가 이미
        '판정 기준은 하나이므로 문장도 하나에서 나온다' 는 단일 출처다.  레일이
        따로 조립하면 엔진을 하나 더 만들 때 둘 중 하나가 낡는다."""
        page = getattr(self, "_setup_page", None)
        if page is None or self._input is None:
            self._rail.set_criteria("")     # 아직 시작 전 — 기준이 확정되지 않았다
            return
        try:
            name, value = page.judgement_text()
        except Exception:                   # 컨트롤이 아직 없을 수 있다
            self._rail.set_criteria("")
            return
        self._rail.set_criteria(f"{name} {value}")

    def _show_page(self, w: QWidget, *, animate: bool = True) -> None:
        """페이지 전환 — 들어오는 화면이 흐름 방향으로 슬라이드+페이드 진입(ease-out).

        나가는 화면은 아래에 두고 새 화면 스냅샷을 안착시킨 뒤 실제 전환(무플래시).
        offscreen/모션 줄이기·최초 표시·동일 페이지면 즉시 스왑."""
        from . import motion
        # ★ 레일은 스택 **밖**이라 전환 애니메이션(스냅샷)에 실리지 않는다 — 새 화면이
        #   미끄러져 들어오는 동안 레일만 먼저 새 단계를 가리키게 된다.  그게 옳다:
        #   레일은 '어디로 가는 중인가' 를 먼저 말해 주는 표지판이지 화면의 일부가
        #   아니다(사라졌다 나타나면 오히려 깜빡임으로 읽힌다).
        old = self._stack.currentWidget()
        sliding = not (old is w or old is None or not animate
                       or not motion.enabled())
        self._sync_rail(w, animate=sliding)
        if not sliding:
            self._stack.setCurrentWidget(w)
            self._notify_shown(w)
            return
        # ★ 21안-A '레일 선행 릴레이' — 레일 눈금이 먼저 차오르고(140ms), 그것이
        #   끝난 **뒤** 화면이 슬라이드-인(240ms)한다.  순차라 동시 애니는 늘 1개,
        #   총 380ms.  겹쳐 재생하면 같은 정보(어디로 가는가)를 두 모션이 동시에
        #   말해 서로를 흐린다 — 순서가 곧 인과(레일 → 화면)를 만든다.
        #   지연 중 새 전환이 들어오면 **마지막 것만** 산다(연타 보호).
        self._pending_page = w
        timer = getattr(self, "_page_lead_timer", None)
        if timer is None:
            # 정적 QTimer.singleShot 금지 — 창이 먼저 닫히면 죽은 위젯을 건드린다.
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._run_pending_page_transition)
            self._page_lead_timer = timer
        timer.start(motion.DUR_RAIL_LEAD)

    def _run_pending_page_transition(self) -> None:
        """레일이 다 찼다 — 이제 화면을 밀어 넣는다(21안-A 의 두 번째 박자)."""
        from . import motion
        w = getattr(self, "_pending_page", None)
        self._pending_page = None
        if w is None:
            return
        old = self._stack.currentWidget()
        if old is w or old is None or not motion.enabled():
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
            on_commit=lambda: self._commit_page(w))

    def _commit_page(self, w: QWidget) -> None:
        self._stack.setCurrentWidget(w)
        self._notify_shown(w)

    @staticmethod
    def _notify_shown(w: QWidget) -> None:
        """'페이지가 화면에 앉았다' 를 페이지에 알린다.

        ★ 진입 모션(검토 목록 스태거 등)은 이 순간을 알아야 한다.  페이지가 자기
        `showEvent` 로는 알 수 없다 — `_show_page` 는 스냅샷을 뜨려고 스택을 잠깐
        새 페이지로 바꿨다가 되돌리므로 showEvent 가 **보이지 않는 동안에도** 온다."""
        hook = getattr(w, "on_shown", None)
        if callable(hook):
            try:
                hook()
            except RuntimeError:
                pass

    # ==================================================================
    # Auto-save
    # ==================================================================
    def _schedule_autosave(self) -> None:
        # 결정이 있을 때마다 즉시 저장한다 (가벼움)
        self._autosave()
        # ★ Stage 1 진행 중에도 '직접 고른 기준 사진' 을 남긴다.  예전엔 선별을 **끝냈을
        #   때만** 저장해서, 도중에 앱이 꺼지면 수백 건의 결정이 되살릴 근거조차 없었다.
        #   이 기록이 있으면 재시작 때 '이전에 고른 n장을 재사용할까요?' 로 회수된다.
        #   ref_history 는 매 저장이 JSON 전체 재기록이라 디바운스한다(결정마다 왕복 금지).
        if self._phase == PHASE_A_SELECT and self._select_page is not None:
            self._ref_history_timer.start(1200)

    def _flush_ref_history(self) -> None:
        if self._select_page is None:
            return
        state = self._select_page.get_state()
        if state is not None:
            self._save_ref_selection(state.targets)

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
        # 폴더 스캔 워커 (U-05) — 토큰을 올려 늦은 결과를 무효화하고 멈추라고 알린다.
        # 기다리지는 않는다: `scan` 은 NAS 응답을 기다리는 중일 수 있어 종료가 그만큼
        # 늦어진다.  daemon 이 아니라 QThread 지만 `_LIVE_SCANS` 가 수명을 잡고 있고,
        # 남은 신호는 토큰 검사에서 버려진다.
        self._scan_token += 1
        if self._scan_worker is not None:
            self._scan_worker.stop()
            self._scan_worker = None
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
        # ★ KLA OCR 워커 — `_ocr_worker` 는 __init__ 에 선언이 없어(그 단계를 안 거친
        #   세션엔 속성 자체가 없다) getattr 로 방어한다.  정리하지 않으면 창을 닫을 때
        #   "QThread: Destroyed while thread is still running" 으로 강제 종료됐다.
        try:
            ocr = getattr(self, "_ocr_worker", None)
            if ocr is not None and ocr.isRunning():
                ocr.stop()
                ocr.wait(500)
        except Exception:
            pass
        # ★ 엑셀 저장 워커 — 기다리기만 하면 반쯤 쓰인 xlsx 가 남으므로 먼저 취소한다.
        try:
            exporter = getattr(self._result_page, "_exporter", None)
            if exporter is not None and exporter.isRunning():
                exporter.stop()
                exporter.wait(3000)
        except Exception:
            pass
        super().closeEvent(event)
