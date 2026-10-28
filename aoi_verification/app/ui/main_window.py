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
import os
import time
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (QElapsedTimer, QObject, Qt, QThread, QTimer,
                          pyqtSignal)
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QLabel, QMainWindow,
                              QMessageBox, QPushButton, QStackedWidget,
                              QStatusBar, QVBoxLayout, QWidget)

from .. import config, i18n
from . import deferred, theme
from ..coords import kla_info
from ..models import cross_check
from ..models import session as session_mod
from ..models.result import FinalResult, MatchResult, MissEntry, extract_result
from ..models.slot import (ImageItem, ScanResult, drop_empty_unmatched,
                           merge_unmatched_by_wafer_id,
                           push_one_sided_to_unmatched,
                           rename_slots_by_wafer_id, scan, scan_lots,
                           wafer_ids_from_broken_info)
from ..utils import paths, wafer_id, wakelock
from ..utils import prefs as _prefs
from ..utils.prefs import AutomationLevel, EngineMode
from ..workers import thumb_plan      # 순수 로직(Qt·numpy 없음) — 무거운 썸네일러와 별개
from .pages.setup_page import SetupInput, SetupPage
from .widgets.journey_rail import JourneyRail
from .widgets.loading_overlay import LoadingOverlay
from .widgets import sheet_host as sheets
from .widgets.sheet_host import SheetHost
from .widgets.window_controls import add_fullscreen_shortcut
from .multi_val_flow import MultiValFlow

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
# 교차검증의 두 번째 방향(검증 → 기준) — `models/cross_check.py`.
PHASE_B_SELECT = "B_select"
PHASE_B_MATCH = "B_match"


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

    def __init__(self, token: int, ref_root, val_root, only=None,
                 extract: bool = False, roots=None) -> None:
        """``only`` 는 '일부 슬롯만 진행' 의 슬롯명 집합 — 스캔이 **그 폴더들만** 연다.
        ``None`` 이면 전체.  (`models.slot.scan` 의 같은 인자로 그대로 넘어간다.)
        ``extract`` 면 한쪽만 훑는다 — ``roots``(여러 LOT, 없으면 ``ref_root`` 하나)
        를 `models.slot.scan_lots` 로."""
        super().__init__()                  # 부모 없음(위 주석)
        self._token = token
        self._ref_root = ref_root
        self._val_root = val_root
        # 추출은 LOT 별 목록(`scan_lots`), 매칭은 슬롯명 집합 하나.
        if extract:
            self._only = list(only) if only is not None else None
        else:
            self._only = set(only) if only is not None else None
        self._extract = bool(extract)
        self._roots = list(roots) if roots else [ref_root]
        self._stop = False
        self.signals = self._Signals()

    def stop(self) -> None:
        self._stop = True

    # 진행 보고 최소 간격(ms) — 이보다 촘촘한 신호는 큐만 채운다.
    _PROGRESS_MIN_MS = 50

    def run(self) -> None:      # type: ignore[override]
        # ★ **개수가 아니라 경과 시간으로 조인다.**  예전에는 `done % 25 == 0` 이었는데,
        #   `scan` 은 슬롯마다 done 을 1부터 올리므로 **슬롯이 25개인 자재에서는
        #   done=25(=total) 한 번만 참**이 된다 — 즉 스캔이 끝나는 순간에야 처음
        #   보고했고, 그때까지 오버레이는 busy(동심 물결) 그대로였다.  사용자가 본
        #   "폴더 스캔만 도는데 얼마나 걸리는지도, 몇 개 되었는지도 모르겠다" 가
        #   이것이다.  시간으로 조이면 슬롯이 몇 개든 첫 슬롯에서 결정형으로 승격하고,
        #   슬롯이 수천 개여도 초당 20회를 넘지 않아 원래 걱정(큐 폭주)도 그대로 막힌다.
        clock = QElapsedTimer()

        def _progress(done: int, total: int) -> None:
            if self._stop:
                raise _FolderScan._Stopped()
            # 첫 보고와 마지막 보고는 무조건 — 시작(busy 탈출)과 완료(100%)는
            # 놓치면 안 되는 두 프레임이다.
            if not clock.isValid():
                clock.start()
            elif done < total and clock.elapsed() < self._PROGRESS_MIN_MS:
                return
            else:
                clock.restart()
            self.signals.progress.emit(self._token, done, total)

        try:
            if self._extract:
                sr = scan_lots(self._roots, progress=_progress, only=self._only)
            else:
                sr = scan(self._ref_root, self._val_root, progress=_progress,
                          only=self._only)
        except _FolderScan._Stopped:
            return                          # 취소 — 아무것도 보고하지 않는다
        except Exception as exc:
            # ★ 실패를 알려야 한다.  예전엔 예외가 나면 오버레이가 **켜진 채** 남아
            #   앱이 잠긴 것처럼 보였다(폴더가 그 사이 사라진 경우 등).
            self.signals.failed.emit(self._token, str(exc))
            return
        if not self._stop:
            self.signals.done.emit(self._token, sr)



class _CalibPrepWorker(QThread):
    """좌표 보정 화면 데이터 준비 — 좌표 읽기(INI/.001 파싱)가 느려 백그라운드에서.

    ``groups`` 는 슬롯마다 ``(기준 사진들, 검증 사진들)``.  ``ready`` 로
    ``(entries, auto)`` 를 낸다(:mod:`coords.calibration`).  자동 계산은
    ``AUTO_TIME_LIMIT_S`` 에서 표 모으기를 멈추고 모인 표로 판단한다.  믿을 만하면
    수동 화면이 필요 없으니 ``entries`` 를 만들지 않는다(빈 목록).  실패하면
    ``([], None)`` — 보정을 건너뛰고 매칭은 그대로 진행한다."""

    progress = pyqtSignal(int, int)
    ready = pyqtSignal(object)

    # die-내부 좌표를 내는 소스만 짝지을 수 있다(절대좌표 'camtek_abs' 는 틀이 다르다).
    _DIE_LOCAL = frozenset({"kla", "camtek_ini", "camtek_live"})

    def __init__(self, groups, kla_side: str, parent=None, notch_dirs=()) -> None:
        super().__init__(parent)
        self._notch_dirs = tuple(notch_dirs or ())
        self._groups = groups
        self._src_is_kla = kla_side == "ref"

    def run(self) -> None:  # type: ignore[override]
        try:
            from ..coords import calibration as cal
            from ..coords import resolve_batch
            paths = [it.path for refs, vals in self._groups for it in (*refs, *vals)]
            coords = resolve_batch(paths, self.progress.emit)
            if self._notch_dirs:       # 보정 후보도 같은 방향으로 맞춘 좌표로 본다
                from ..coords import notch
                coords = notch.apply_notch(
                    coords, lambda p: notch.notch_for_path(self._notch_dirs, p))

            def c_of(it):
                c = coords.get(it.path)
                return c if c is not None and c.source in self._DIE_LOCAL else None

            groups = [([(it, c_of(it)) for it in refs], [(v, c_of(v)) for v in vals])
                      for refs, vals in self._groups]
            # 실행 전체 슬롯으로 한 번 — 웨이퍼 한 장씩이면 결함이 몰린 곳에서 틀린다.
            auto = cal.auto_offset(
                [([c for _k, c in s if c], [c for _k, c in o if c]) for s, o in groups],
                src_is_kla=self._src_is_kla,
                deadline=time.monotonic() + cal.AUTO_TIME_LIMIT_S)
            entries = ([] if cal.is_confident(auto)
                       else cal.build_entries(groups, src_is_kla=self._src_is_kla))
            self.ready.emit((entries, auto))
        except Exception:
            _LOG.exception("좌표 보정 준비 실패 — 보정 없이 진행")
            self.ready.emit(([], None))

class MainWindow(MultiValFlow, QMainWindow):

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

    def __init__(self, progress=None, startup_checks=None) -> None:
        """``progress(done, total, message)`` 를 주면 페이지 생성 진행을 보고한다
        (시작 스플래시의 로딩 표시).  창 생성은 메인 스레드를 막으므로, 진행을
        보고하지 않으면 로딩 표시가 그 구간 내내 멈춰 보인다.

        ``startup_checks`` 는 로딩창에서 미리 끝낸 업데이트 확인(``main._StartupChecks``:
        ``done`` Event·``info``).  끝났으면 그 결과를 쓰고, 아니면 예전처럼 직접 확인한다."""
        super().__init__()
        self._startup_checks = startup_checks
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
        self._apply_statusbar_theme()
        self._status_bar.addWidget(self._credit_label)
        self._mem_label = QLabel("", self._status_bar)
        self._mem_label.setProperty("role", "muted")
        self._status_bar.addPermanentWidget(self._mem_label)
        # 개발자 모드(Ctrl+Shift+D) — 켜져 있을 때만 상태바에 설정 버튼이 보인다.
        self._dev_btn = QPushButton(i18n.KO.DEV_MODE_BUTTON, self._status_bar)
        self._dev_btn.setFlat(True)
        self._dev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dev_btn.clicked.connect(self._open_dev_mode)
        self._status_bar.addPermanentWidget(self._dev_btn)
        self._dev_btn.setVisible(_prefs.load().dev_mode)
        QShortcut(QKeySequence("Ctrl+Shift+D"), self,
                  activated=self._toggle_dev_mode)
        self._mem_timer = QTimer(self)
        self._mem_timer.setInterval(2000)
        self._mem_timer.timeout.connect(self._update_memory_label)
        self._mem_pressure_shown = False
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
        # 폴더 스캔 워커 (U-05) + [중지] 가 무엇을 멈춰야 하는지 알려 주는 현재 단계 (P-09).
        self._scan_token = 0
        # 좌표 보정(KLA ↔ Camtek, 매칭 전용) — `_calibrate_then` 참고.
        self._coord_offset = None
        self._notch_dirs = ()
        self._calib_next = None
        self._calib_worker = None
        self._calib_side = None
        # KLA 정보파일이 비어 있던 폴더 — `_after_slot_resolved` 가 한 번 경고한다.
        self._kla_broken: list = []
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
        # ★ 검토를 거쳤는지는 목록이 비었는지로 판단하지 않는다 — 사용자가 매치를 **전부**
        #   '매치 없음' 으로 표시하면 목록이 비는데, 그걸 '검토 안 함' 으로 읽으면 지운
        #   자동 매치가 되살아난다(전체 점검에서 발견).
        self._a_reviewed = False
        self._reviewed_unmatched: list[MissEntry] = []
        self._reviewed_unmatched_keys: set = set()
        # 검토 대상 전체('매치 없음' 표시분 포함) — 결과↔검토 왕복의 기반.
        self._reviewed_all_matches: list[MatchResult] = []
        self._reset_cross_state()
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
        QTimer.singleShot(400, self._begin_update_check)

    def _begin_update_check(self) -> None:
        """로딩창에서 이미 확인했으면 그 결과로, 아니면(시간 초과·없음) 직접 확인한다.

        ★ **창이 보일 때만** 시작한다.  `main._wait_backend` 가 창을 띄우기 전에 이벤트를
          돌리므로 400ms 타이머가 숨은 창에서 발화할 수 있다 — 그때 업데이트 안내·이어하기
          시트를 열면 보이지 않는 팝업이 중첩 루프를 쥐어 시작이 영영 끝나지 않는다."""
        if not self.isVisible():
            deferred.call_later(self, 200, self._begin_update_check)
            return
        checks = self._startup_checks
        if checks is None or not checks.done.is_set():
            self._check_for_update_async()
        elif checks.info:
            self._update_found.emit(checks.info)
        else:
            self._startup_proceed.emit()

    def _run_startup_popups(self) -> None:
        """업데이트 흐름이 끝난 뒤(없음/거절/실패) 나머지 시작 팝업을 순서대로 1회만."""
        if self._startup_done:
            return
        self._startup_done = True
        self._maybe_show_whats_new()         # 업데이트 후 첫 실행이면 바뀐 점 안내.
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

    # ==================================================================
    # 개발자 모드 (Ctrl+Shift+D) — 업데이트 저장소·브랜치 고르기
    # ==================================================================
    def _toggle_dev_mode(self) -> None:
        """켜면 설정 창을 바로 연다.  끄면 자동 업데이트가 기본 브랜치로 돌아간다."""
        on = not _prefs.load().dev_mode
        _prefs.patch(dev_mode=on)
        self._dev_btn.setVisible(on)
        self._status_bar.showMessage(
            i18n.KO.DEV_MODE_ON if on else i18n.KO.DEV_MODE_OFF, 4000)
        if on:
            self._open_dev_mode()

    def _open_dev_mode(self) -> None:
        from .widgets.dev_mode_dialog import DevModeDialog
        dlg = DevModeDialog(parent=self)
        sheets.run(dlg)
        info = dlg.install_info
        if not info:
            return
        ans = sheets.ask(
            self, i18n.KO.DEV_MODE_TITLE,
            i18n.KO.DEV_MODE_INSTALL_ASK_FMT.format(
                repo=info["repo"], branch=info["branch"], sha=str(info["sha"])[:7]),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
            labels={QMessageBox.StandardButton.Yes: i18n.KO.UPDATE_BTN_NOW,
                    QMessageBox.StandardButton.No: i18n.KO.UPDATE_BTN_LATER},
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._start_update_download(info, dev=True)

    def _on_update_found(self, info: dict) -> None:
        """'업데이트 있음' 안내 → 동의하면 백그라운드로 다운로드/교체."""
        # 사용자에겐 개발자용 커밋 메시지/SSL 멘트 대신 간단한 안내만.
        body = (i18n.KO.UPDATE_UNKNOWN_CURRENT if (info or {}).get("current_unknown")
                else i18n.KO.UPDATE_AVAILABLE_BODY)
        ans = sheets.ask(
            self, i18n.KO.UPDATE_AVAILABLE_TITLE, body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
            labels={QMessageBox.StandardButton.Yes: i18n.KO.UPDATE_BTN_NOW,
                    QMessageBox.StandardButton.No: i18n.KO.UPDATE_BTN_LATER},
        )
        if ans != QMessageBox.StandardButton.Yes:
            self._run_startup_popups()       # 거절 → 나머지 시작 팝업 진행
            return
        from ..utils import updater
        self._start_update_download(info, dev=updater.dev_target() is not None)

    def _start_update_download(self, info: dict, dev: bool = False) -> None:
        """``info`` 의 repo/branch/sha 를 받아 적용한다(백그라운드 + 로딩바).

        ``dev`` 는 개발자 모드에서 고른 대상이라는 표식(VERSION 에 남는다)."""
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
        self._update_step = 1
        self._loading.show_overlay(i18n.KO.UPDATE_DOWNLOADING, step=(1, 2),
                                   steps=i18n.KO.UPDATE_JOURNEY_STEPS)
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
                    info["repo"], info["branch"], info["sha"], progress=_report,
                    dev=dev)
            except Exception:
                ok = False
            self._update_applied.emit(bool(ok), info or {})

        threading.Thread(target=_work, name="update-apply", daemon=True).start()

    def _on_update_progress(self, done: int, total: int, phase: str) -> None:
        """업데이트 진행(다운로드/압축해제/적용) → 로딩바 갱신(메인 스레드).

        화면에는 크게 두 단계만 보인다: 다운로드(1) → 설치(2, 다운로드 이후 전부)."""
        step = 1 if phase == i18n.KO.UPDATE_PHASE_DOWNLOAD else 2
        if step != getattr(self, "_update_step", 1):
            self._update_step = step
            self._loading.set_stage((step, 2), i18n.KO.UPDATE_JOURNEY_STEPS)
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
            # ★ 상태만 정한다 — 띄우는 건 `main` 의 show() 다.  여기서 showMaximized()
            #   를 부르면 로딩창 대기 중에 창이 먼저 떠서, 그 사이 업데이트 안내 시트가
            #   대기 루프 안에서 열리고 로딩창이 끝나지 않았다(사용자 신고).
            self.setWindowState(Qt.WindowState.WindowMaximized)
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
        """#8 매치 페이지에서 중지 — 진행 중 작업을 멈추고 셋업 화면으로 복귀.

        ★ `_on_select_cancelled` 처럼 세션 상태도 버린다.  세션 파일과 `_phase` 가 남으면
          자동 저장이 접은 실행을 계속 다시 쓰고, 다음 실행의 '이어하기' 가 그걸 되살린다."""
        self._phase = PHASE_NONE
        self._matches_a.clear()
        self._skipped_a.clear()
        self._reset_cross_state()
        session_mod.clear()
        wakelock.release()
        self._show_page(self._setup_page)

    # ------------------------------------------------------------------
    def _maybe_show_whats_new(self) -> None:
        """업데이트 후 첫 실행이면 '바뀐 점' 을 한 번 보여 준다(사용자 결정: 하이라이트형).

        본 것으로 기록하는 시점은 **보여 준 뒤**다 — 띄우다 실패하면 다음 실행에 다시 뜬다."""
        try:
            from ..i18n import whats_new as _notes
            from ..utils import whats_new as _wn
            todo = _wn.pending(_prefs.load().whats_new_seen, _notes.ENTRIES)
            if not todo:
                return
            from .widgets.whats_new_dialog import WhatsNewDialog
            sheets.run(WhatsNewDialog(_wn.merge(todo), self))
            _prefs.patch(whats_new_seen=_notes.ENTRIES[0]["id"])
        except Exception:
            _LOG.exception("업데이트 안내를 띄우지 못함")

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
        #   (색을 f-string 으로 굽지 않는다).
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
        # ★ on_done 은 한 번만 — `finalize()` 가 이미 on_done 을 부른 뒤에 예외가 나면
        #   폴백이 다음 단계를 **두 번** 돌린다.  예외는 삼키지 않고 기록한다.
        ran = [False]

        def once() -> None:
            if ran[0]:
                return
            ran[0] = True
            on_done()

        try:
            self._kla_resolve_impl(sr, kla_side, once)
        except Exception:
            _LOG.exception("KLA 정보파일/OCR 해석 실패 — 해석 없이 다음 단계로 진행")
            once()

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
                                   steps=self._journey_steps())
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
        # 정보파일이 비어 헤더를 못 읽은 KLA 폴더 — 파일 이름의 WaferID 와 카세트 슬롯
        # 번호가 **둘 다** 상대 폴더와 맞을 때만 짝짓는다(사용자 결정 2026-10-25).
        # 둘 다 KLA 면 상대에 `ActiveSlot` 이 없어 이 경로를 쓰지 않는다.
        self._kla_broken = []
        if kla_side in ("ref", "val"):
            kla_is_ref = kla_side == "ref"
            known = info_ref if kla_is_ref else info_val
            ids, self._kla_broken = wafer_ids_from_broken_info(
                {n: dir_of(n, kla_is_ref)
                 for n in (sr.ref_only if kla_is_ref else sr.val_only)},
                {n: dir_of(n, not kla_is_ref)
                 for n in (sr.val_only if kla_is_ref else sr.ref_only)},
                known)
            known.update(ids)
            for n, w, ok in self._kla_broken:
                _LOG.warning("KLA 정보파일이 비어 있음: %s (이름의 WaferID %s, 슬롯 번호로 짝 %s)",
                             n, w, "지음" if ok else "못 지음")
        merge_unmatched_by_wafer_id(sr, info_ref, info_val)

        # 메타 작성 + 다음 단계 — OCR 결과(있으면)를 반영해 최종 메타를 만든다.
        # 판독에 성공한 폴더는 **사진이 없어도**(image=None) slot명을 갖는다 → 수동
        # 매핑에서 미리보기만 없고 선택은 가능하다.
        def build_meta(names, is_kla, info, ocr, img0, is_ref) -> dict:
            meta: dict[str, dict] = {}
            for n in names:
                img = img0.get(n)
                if is_kla and n in info:
                    d = dir_of(n, is_ref)
                    meta[n] = {"slot": info[n], "method": "info", "image": img,
                               "slot_no": kla_info.read_slot_no(d) if d else None}
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
                                             ocr_ref, img0_ref, True)
            self._slot_meta_val = build_meta(list(sr.val_only), do_val, info_val,
                                             ocr_val, img0_val, False)
            self._ocr_worker = None
            on_done()

        def finalize_in_slot(ocr_ref=None, ocr_val=None) -> None:
            """Qt 슬롯에서 쓰는 finalize — 예외가 슬롯 밖으로 나가지 않게 한다.
            finalize 가 on_done 전에 죽었으면 다음 단계가 영영 안 이어지므로 on_done 도
            이어 준다(바깥 `_resolve_and_merge_kla` 의 once 가 중복 호출을 막는다)."""
            try:
                finalize(ocr_ref, ocr_val)
            except Exception:
                _LOG.exception("KLA 메타 작성 실패 — 메타 없이 다음 단계로 진행")
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
                steps=self._journey_steps())
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
                except Exception:
                    # Qt 슬롯 밖으로 예외가 나가면 PyQt6 가 프로세스를 끝낸다.
                    _LOG.exception("OCR 결과 병합 실패 — 병합 없이 계속")
                finally:
                    finalize_in_slot(ocr_ref, ocr_val)

            worker.signals.progress.connect(_on_progress)
            worker.signals.done.connect(_on_ocr_done)
            worker.signals.failed.connect(lambda _msg: finalize_in_slot())
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
            # 검증 폴더 경로도 옮긴다 — 빠뜨리면 그 슬롯의 검증 쪽 정보(슬롯 번호·
            # 정보파일)를 나중에 찾을 수 없다(`merge_unmatched_by_wafer_id` 와 같은 규칙).
            ref_slot.val_dir = val_slot.val_dir
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
            # 좌표 보정(매칭 전용) — 보정 화면에서 [보정 적용] 했을 때만 0 이 아니다.
            coord_offset=tuple(getattr(self, "_coord_offset", None) or (0.0, 0.0)),
            # Notch 방향 보정(매칭 전용) — 표준(아래)이 아닌 폴더가 있을 때만 비어 있지 않다.
            notch_dirs=tuple(getattr(self, "_notch_dirs", None) or ()),
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
        self._a_reviewed = False
        self._reviewed_unmatched.clear()
        self._reviewed_unmatched_keys = set()
        self._reviewed_all_matches = []
        self._reset_cross_state()
        if self._is_cross():
            self._cross_stage = "A"
        # 기준 1 ↔ 검증 여러 장비 — 장비마다 구간을 만든다(`ui/multi_val_flow.py`).
        self._init_legs(inp)
        self._run_log_written = False
        self._thumbs_handled = False        # 썸네일 완료 one-shot 가드 리셋(#C2)
        self._stop_lazy_thumb_pools()
        self._lazy_val_thumbs = False
        # 타일 픽스맵 캐시 비우기 — 폴더가 바뀌어도 stale 픽스맵이 남지 않게(#렉).
        try:
            from ..utils import image_io as _io
            _io.clear_tile_cache()
        except Exception as exc:
            _LOG.debug("타일 픽스맵 캐시 비우기 실패: %s", exc)
        self._session_id = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

        # ★ 오버레이를 **먼저** 띄운다.  아래 `_prepare_working_file` 은 결과 폴더를
        #   만들고 검증 폴더의 INI 한 장을 읽는데, 그 폴더가 NAS 면 왕복이 초 단위일 수
        #   있다 — 예전에는 그동안 아무 표시도 없어 [검증 시작] 이 먹지 않은 것처럼
        #   보였다.
        self._show_scan_overlay()

        # 결과 파일의 **이름·자리만** 정한다 — 파일 자체는 [엑셀로 저장] 때 만든다.
        self._prepare_working_file(inp)

        # 원본 mtime 메모이즈 초기화 — 이번 세션 동안 캐시 키용 stat() 을 경로당 1회로(#5).
        from ..utils import cache as _cache
        _cache.reset_mtime_cache()
        # 좌표 파서 캐시도 실행마다 새로 — 앱을 켠 채 장비가 파일을 다시 쓰면 옛 좌표가 남았다.
        from .. import coords as _coords
        _coords.clear_caches()
        self._kla_folders: dict[str, str] = {}      # KLA slot명→폴더명(엑셀 회색 표기)
        # KLA 폴더별 판독 메타 — `_kla_resolve_impl` 이 채운다.  안 지우면 KLA 가 아닌 다음
        # 실행에서도 옛 메타가 엑셀·실행 로그에 새어 나간다.
        self._slot_meta_ref = None
        self._slot_meta_val = None
        self._launch_scan(inp)

    def _show_scan_overlay(self) -> None:
        self._loading.show_overlay(self._leg_message(i18n.KO.LOAD_SCAN),
                                   cancelable=True, step=(1, 3),
                                   steps=self._journey_steps())
        QApplication.processEvents()

    def _launch_scan(self, inp: SetupInput) -> None:
        """폴더 스캔 워커를 띄운다 — 실행 시작과 다음 검증 장비(구간)가 같이 쓴다."""
        if self._multi() and self._leg_idx > 0:
            self._show_scan_overlay()

        # 폴더 스캔 — 워커에서 (U-05).  진행은 시그널로만 올라온다.
        # ★ '일부 슬롯만 진행' 이면 **그 슬롯 폴더만** 연다.  예전에는 전부 훑은 뒤
        #   `_on_scan_done` 에서 결과를 줄여서, 25슬롯 중 3개만 고른 사용자도 25개의
        #   사진 열거(NAS 왕복)를 기다렸다.  줄이는 자리는 스캔 하나뿐이다 —
        #   결과가 이미 선택 슬롯만 담으므로 다운스트림은 손댈 것이 없다.
        self._stage = "scan"
        self._stage_clock()                 # 단계별 소요 시간(app.log) — 여기서 시작
        self._scan_token += 1
        worker = _FolderScan(self._scan_token, inp.ref_root, inp.val_root,
                             only=(getattr(inp, "extract_slots", None)
                                   if self._is_extract()
                                   else getattr(inp, "selected_slots", None)),
                             extract=self._is_extract(),
                             roots=getattr(inp, "extract_roots", None))
        worker.signals.progress.connect(self._on_scan_progress)
        worker.signals.done.connect(self._on_scan_done)
        worker.signals.failed.connect(self._on_scan_failed)
        _LIVE_SCANS.add(worker)
        worker.finished.connect(lambda w=worker: _LIVE_SCANS.discard(w))
        self._scan_worker = worker
        worker.start()

    def _stage_clock(self, label: str = "", **info) -> None:
        """단계별 소요 시간을 app.log 에 남긴다 — 느린 단계를 실측으로 가리기 위해.

        ``label`` 없이 부르면 시계만 다시 건다(세션 시작).  있으면 직전 호출부터
        지금까지를 그 단계의 시간으로 적고 시계를 다시 건다.  화면에는 나가지 않는다."""
        now = time.perf_counter()
        start = getattr(self, "_stage_t0", None)
        self._stage_t0 = now
        if label and start is not None:
            extra = " ".join(f"{k}={v}" for k, v in info.items())
            _LOG.info("단계 소요 [%s] %.2f초 %s", label, now - start, extra)

    def _on_scan_progress(self, token: int, done: int, total: int) -> None:
        if token != self._scan_token:
            return
        self._loading.set_progress(done, total, self._leg_message(i18n.KO.LOAD_SCAN))

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
                    self._leg_message(
                        i18n.KO.WARN_SCAN_FAILED_FMT.format(detail=message)))
        if self._multi() and self._leg_idx > 0:
            # 앞 장비들의 매칭은 끝났다 — 이 장비만 빼고 이어 간다.
            self._multi_after_match_empty()

    def _on_scan_done(self, token: int, sr: ScanResult) -> None:
        """스캔이 끝났다 — 여기서부터는 예전 `_on_start` 의 나머지 그대로다.

        (모달을 여는 `_ask_kla_side` 부터는 반드시 메인 스레드여야 한다.)"""
        if token != self._scan_token:
            return                          # 취소·재시작으로 밀려난 옛 스캔
        self._scan_worker = None
        self._stage = ""
        self._stage_clock("scan", slots=len(sr.slots),
                          photos=sum(len(s.ref_images) + len(s.val_images)
                                     for s in sr.slots.values()))
        inp = self._input
        if inp is None:
            return

        # ※ '일부 슬롯만 진행' 은 여기서 거르지 않는다 — 스캔 워커가 `only=` 로
        #   그 폴더들만 열었으므로 `sr` 이 이미 선택 슬롯만 담고 있다(`_on_start`).
        # ※ `.t.` 사진은 열거 단계에서 항상 빠진다(`models.slot.is_ignored_name`) —
        #   한때 여기서 예시 사진과 함께 물었지만 사용자 결정으로 '항상 뺀다' 로
        #   돌아갔다.
        self._scan = sr
        if self._is_extract():
            self._extract_after_scan(sr)
            return
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
        broken, self._kla_broken = self._kla_broken, []
        if broken:
            # 깨진 .001 에는 결함 좌표도 없다 — 짝을 지었어도 좌표 매칭이 안 되므로 알린다.
            self._loading.hide_overlay()
            sheets.warn(self, i18n.KO.APP_TITLE, i18n.KO.KLA_INFO_BROKEN_FMT.format(
                n=len(broken), k=sum(1 for _n, _w, ok in broken if ok),
                names=", ".join(f"{n}({w})" for n, w, _ok in broken)))
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
            sheets.warn(self, i18n.KO.APP_TITLE,
                        self._leg_message(i18n.KO.WARN_NO_SLOTS))
            if self._multi() and self._leg_idx > 0:
                self._multi_after_match_empty()      # 이 장비만 빼고 이어 간다
            return
        self._continue_start_after_scan(common)

    def _continue_start_after_scan(self, common: list[str]) -> None:
        """slot 확정 후 썸네일 캐시 사전 생성(백그라운드) → 다음 단계."""
        sr = self._scan
        if sr is None:
            return
        # KLA 해석·수동 매핑(사용자 대답을 기다린 시간 포함)이 여기서 끝난다.
        self._stage_clock("slots(KLA/mapping/dialog wait)", slots=len(common))
        # 매핑/OCR 단계에서 오버레이가 숨겨졌을 수 있으므로 **반드시 다시 띄운다** —
        # 그렇지 않으면 썸네일 생성 동안 메인 창이 클릭 가능 상태로 남아 버그 유발.
        # (set_progress 는 숨겨진 오버레이를 다시 띄우지 않으므로 show_overlay 필수.)
        # 썸네일 단계는 가장 오래 걸리므로 [중지] 로 건너뛸 수 있게 한다(#C2).
        self._loading.show_overlay(
            i18n.KO.LOAD_THUMBNAIL, cancelable=True,
            step=(2, 3), steps=self._journey_steps())
        QApplication.processEvents()
        # 좌표 매칭은 썸네일을 보지 않는다 — 검증 사진은 매칭 후보로 나온 것만 뒤에서
        # 만들고, 여기서는 기준 사진만 기다린다(사용자 결정 2026-10-26).
        self._lazy_val_thumbs = (
            EngineMode.is_coordinate(
                getattr(self._input, "engine_mode", EngineMode.COORDINATE))
            and not self._is_extract())
        all_items = thumb_plan.startup_items(sr.slots, common,
                                             lazy_val=self._lazy_val_thumbs)

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
            i18n.KO.LOAD_THUMBNAIL,
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
                d, t, i18n.KO.LOAD_THUMBNAIL,
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

    # ------------------------------------------------------------------
    # 좌표 매칭 — 검증 사진 썸네일을 필요한 것만 뒤에서 (`workers/thumb_plan`)
    # ------------------------------------------------------------------
    # 좌표 매칭이면 검증 사진 썸네일을 시작 때 기다리지 않는다(`workers/thumb_plan`).
    # 그 대신 뒤에서 도는 풀들 — 후보 사진용·교차검증 2차 대비(저부하)·2차 진입 마무리.
    # 클래스 기본값이라 `_on_start` 전에 읽혀도 안전하다(`_cross_stage` 와 같은 방식).
    _lazy_val_thumbs = False
    _cand_pool = None
    _cross_b_pool = None
    _b_wait_pool = None
    _b_wait_queue: Optional[list] = None

    def _new_thumb_pool(self, **kw):
        from ..workers.thumbnailer import ThumbnailPool
        return ThumbnailPool(tier=self._sizing_tier, also_mid=True, parent=self, **kw)

    def _on_coord_candidates(self, _slot: str, items: list) -> None:
        """좌표 매칭이 슬롯 하나를 끝냈다 — 그 슬롯의 후보 사진 썸네일을 뒤에서 만든다.

        화면은 썸네일이 없으면 그 자리에서 만들므로, 이 풀은 '미리' 일 뿐 정답에는
        영향이 없다.  화면을 쓰는 동안이라 절반 코어·최저 우선순위로 돌린다."""
        if not self._lazy_val_thumbs or not items:
            return
        if self._cand_pool is None:
            self._cand_pool = self._new_thumb_pool(
                num_workers=max(1, (os.cpu_count() or 2) // 2), low_priority=True)
            self._cand_seen: set = set()
        new = [it for it in items if it.path not in self._cand_seen]
        self._cand_seen.update(it.path for it in new)
        if new:
            self._cand_pool.enqueue(new)
            self._cand_pool.start()

    def _start_cross_b_prefetch(self) -> None:
        """교차검증 1차 매칭 끝 — 2차 큐에 들어갈 검증 사진 썸네일을 **천천히** 만든다.

        사용자 결정: 컴퓨터 부하가 심하지 않게 — 일꾼 1명·OS 우선순위 최저.
        1차 검토에서 짝을 풀면 그 사진도 2차 큐에 들어가지만, 그 사진은 이미 후보
        썸네일로 만들어져 있다(짝이 됐다 = 후보였다)."""
        if (not self._lazy_val_thumbs or self._cross_stage != "A"
                or self._cross_b_pool is not None or self._scan is None):
            return
        sr = self._scan
        val_by_slot = {n: list(sr.slots[n].val_images) for n in sr.common_slot_names}
        items = thumb_plan.cross_b_prefetch_items(val_by_slot, self._matches_a)
        if not items:
            return
        self._cross_b_pool = self._new_thumb_pool(num_workers=1, low_priority=True)
        self._cross_b_pool.enqueue(items)
        self._cross_b_pool.start()

    def _wait_cross_b_thumbs(self, queue: list) -> None:
        """2차 선별 진입 — 아직 안 만든 썸네일을 **전속력으로** 마저 만든 뒤 들어간다
        (사용자 결정 '가').  이미 만든 사진은 캐시 적중이라 금방 지나간다."""
        if self._cross_b_pool is not None:
            self._cross_b_pool.stop()        # 저부하 풀은 할 일을 넘기고 물러난다
        pool = self._new_thumb_pool()
        self._b_wait_pool = pool
        self._b_wait_queue = list(queue)
        self._loading.show_overlay(i18n.KO.LOAD_THUMBNAIL_CROSS_B, cancelable=True)
        self._loading.set_progress(0, len(queue), i18n.KO.LOAD_THUMBNAIL_CROSS_B)
        pool.enqueue(queue)
        pool.signals.progress.connect(self._on_b_wait_progress)
        pool.signals.finished.connect(self._on_b_wait_finished)
        pool.start()

    def _on_b_wait_progress(self, done: int, total: int, _path: str) -> None:
        if self._b_wait_queue is not None:
            self._loading.set_progress(done, total, i18n.KO.LOAD_THUMBNAIL_CROSS_B)

    def _on_b_wait_finished(self) -> None:
        # finished 콜백 안에서 페이지를 바꾸지 않는다(`_on_thumbs_ready` 와 같은 이유).
        QTimer.singleShot(0, self._show_cross_b_select)

    def _show_cross_b_select(self) -> None:
        """2차 선별 화면(올인원이면 곧장 2차 매칭) — 썸네일 마무리(또는 [중지]) 뒤
        한 번만 들어간다."""
        queue, self._b_wait_queue = self._b_wait_queue, None
        if queue is None:
            return
        if self._b_wait_pool is not None:       # 썸네일 마무리 오버레이를 내린다
            self._loading.hide_overlay()
        if self._cross_stage != "B":
            return
        if self._input.automation_level == AutomationLevel.AUTO_ALL:
            self._start_cross_b_match(queue)
            return
        self._select_page.set_extract_mode(False)
        self._select_page.set_reverse(True)
        self._select_page.load_state(queue=queue, targets={}, excluded={},
                                     history=[])
        self._phase = PHASE_B_SELECT
        self._show_page(self._select_page)

    def _stop_lazy_thumb_pools(self, wait_ms: int = 0) -> None:
        """뒤에서 도는 썸네일 풀을 멈춘다 — 새 실행·새 세션·종료 때."""
        for name in ("_cand_pool", "_cross_b_pool", "_b_wait_pool"):
            pool = getattr(self, name, None)
            if pool is not None:
                pool.stop()
                if wait_ms:
                    pool.wait(wait_ms)
            setattr(self, name, None)
        self._b_wait_queue = None

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
        self._stage_clock("thumbnails")
        self._loading.set_stage((3, 3), self._journey_steps())
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
        if self._b_wait_queue is not None:
            # 교차검증 2차 진입의 썸네일 마무리 — 멈추고 들어간다(남은 것은 화면이 만든다).
            if self._b_wait_pool is not None:
                self._b_wait_pool.stop()
            self._show_cross_b_select()
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
        한 번 더 돌려 busy 물결과 ETA 시계를 이유 없이 재시작한다."""
        if self._input is None:
            return
        if self._input.automation_level == AutomationLevel.AUTO_ALL:
            self._loading.hide_overlay()
            if self._is_extract():          # 선별을 건너뛴다 = 전부 추출
                self._finish_extract({
                    n: list(s.ref_images) for n, s in self._scan.slots.items()})
                return
            self._enter_stage2_auto_all()
            return
        self._loading.hide_overlay()
        if self._multi_skip_select():        # 둘째 검증 장비부터 — 선별은 한 번만
            return
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
        self._calibrate_then(queue, pool, auto_mode=True)

    def _on_match_review_done(self,
                              kept: list,
                              unmatched_refs: list) -> None:
        """MatchReviewPage 의 [검토 완료] 시그널 → 결과 페이지 진입.

        교차검증이면 첫 방향(A)의 검토가 끝난 뒤 결과 대신 두 번째 방향(B)으로 간다."""
        if self._cross_stage == "B":
            self._store_b_review(kept, unmatched_refs)
            self._finish_session()
            return
        if self._multi():
            self._multi_review_done(kept, unmatched_refs)
            return
        self._reviewed_matches = list(kept)
        self._a_reviewed = True
        self._reviewed_unmatched = list(unmatched_refs)
        # ★ 결과 화면에서 검토로 되돌아올 때 되살리려면 '매치 없음' 표시를 키로 들고
        #   있어야 한다 — load_state 는 진입할 때마다 그 집합을 비운다.
        self._reviewed_unmatched_keys = set(
            self._match_review_page.unmatched_keys())
        # ★ 그리고 **표시된 행 자체**도 들고 있어야 한다.  `kept` 에는 '매치 없음' 행이
        #   빠져 있어서, 그걸 기반으로 되돌아가면 복원할 행이 없다 → 다음 [검토 완료]
        #   에서 그 사진들이 매치에도 미매칭에도 없는 상태가 돼 결과에서 사라진다.
        self._reviewed_all_matches = self._match_review_page.all_matches()
        if self._cross_stage == "A":
            self._enter_cross_b()
            return
        self._finish_session()

    # ==================================================================
    # 교차검증 — 두 번째 방향(검증 → 기준).  순수 로직은 `models/cross_check.py`.
    # ==================================================================
    # 클래스 기본값 — `_reset_cross_state` 가 인스턴스 값을 채운다(그 전에 읽혀도 안전하게).
    _cross_stage = ""
    _ctx_a = None
    _a_reviewed = False

    def _is_cross(self) -> bool:
        """시작한 실행이 교차검증인가 — `_on_start` 에서만 본다.  그 뒤로는
        `_cross_stage`(비어 있지 않음 = 교차검증 실행)가 같은 사실을 들고 다닌다."""
        return (getattr(self._input, "mode", "") == cross_check.CROSS_MODE
                and not self._is_extract())

    def _reset_cross_state(self) -> None:
        """교차검증 상태를 비운다 — 새 실행·새 세션마다."""
        # "" = 교차검증 아님, "A" = 첫 방향 진행 중, "B" = 두 번째 방향 진입 후.
        self._cross_stage = ""
        self._ctx_a = None                   # A 의 검토 맥락 사본(B 가 MatchPage 를 덮는다)
        self._matches_b: list[MatchResult] = []      # B 자동 매치 — **큐 방향**
        self._skipped_b: dict[str, list[ImageItem]] = defaultdict(list)
        self._b_reviewed = False
        self._b_kept: list[MatchResult] = []         # B 검토 결과 — **정방향**
        self._b_misses: list[MissEntry] = []         # B 검토에서 '매치 없음' (side="val")
        self._b_all: list[MatchResult] = []          # B 검토 왕복의 기반 — 큐 방향
        self._b_unmatched_keys: set = set()

    def _a_final_matches(self) -> list[MatchResult]:
        """첫 방향의 확정 매치 — 검토(또는 결과 편집)를 거쳤으면 그 목록이 전부다.

        빈 목록도 '전부 버림' 이라는 확정 결과다(`_a_reviewed` 주석)."""
        if self._a_reviewed:
            return list(self._reviewed_matches)
        return self._merge_matches()

    def _enter_cross_b(self) -> None:
        """A 검토 완료 → B 선별(또는 올인원이면 곧장 B 매칭).

        큐 = 검증 사진 − A 에서 짝지은 검증 사진(사용자 결정: 다시 나타나지 않는다).
        후보 풀은 기준 사진 전부다(`cross_check` 모듈 docstring)."""
        assert self._scan is not None and self._input is not None
        self._cross_stage = "B"
        # ★ B 를 MatchPage 에 싣는 순간 A 의 선계산 결과(fast_results)가 지워진다.
        #   결과 화면의 실패 검토가 A 쪽 미매칭에도 그 값을 쓰므로 사본을 떠 둔다.
        ctx = self._review_ctx()
        self._ctx_a = (dict(ctx.fast_results), frozenset(ctx.coord_classical_refs))
        sr = self._scan
        val_by_slot = {n: list(sr.slots[n].val_images) for n in sr.common_slot_names}
        by_slot = cross_check.reverse_queue(val_by_slot, self._a_final_matches())
        queue = [it for n in sorted(by_slot) for it in by_slot[n]]
        if not queue:                       # 검증 사진이 전부 A 에서 짝지어졌다
            self._finish_session()
            return
        self._b_wait_queue = list(queue)
        if self._lazy_val_thumbs:
            # 올인원도 기다린다 — 2차 검토 화면이 이 사진들을 곧바로 그린다.
            self._wait_cross_b_thumbs(queue)
            return
        self._show_cross_b_select()

    def _start_cross_b_match(self, queue: list[ImageItem]) -> None:
        """B 매칭 — 큐는 검증 사진, 후보 풀은 같은 슬롯의 기준 사진 전부.

        좌표 보정은 다시 묻지 않는다 — A 에서 정한 값(`_coord_offset`)을 그대로 쓴다.
        보정은 KLA 좌표(`source == "kla"`)에만 더해지므로 방향과 무관하다."""
        assert self._scan is not None and self._input is not None
        sr = self._scan
        pool = {n: list(sr.slots[n].ref_images) for n in sr.common_slot_names}
        self._matches_b.clear()
        self._skipped_b.clear()
        auto_mode = AutomationLevel.is_auto(self._input.automation_level)
        self._calib_next = (queue, pool, auto_mode)
        self._start_stage2()

    def _store_b_review(self, kept: list, unmatched: list) -> None:
        """B 검토 완료 — 큐 방향을 정방향으로 돌려 보관한다."""
        self._b_reviewed = True
        self._b_kept = [cross_check.swap(m) for m in kept]
        self._b_misses = cross_check.as_val_misses(unmatched)
        self._b_unmatched_keys = set(self._match_review_page.unmatched_keys())
        self._b_all = self._match_review_page.all_matches()

    def _cross_b_parts(self) -> tuple[list[MatchResult], list[MissEntry]]:
        """B 의 (정방향 매치, 검증 쪽 미매칭)."""
        if self._b_reviewed:
            matches = list(self._b_kept)
        else:
            matches = [cross_check.swap(m) for m in self._matches_b]
        misses = list(self._b_misses) + [
            MissEntry(slot=slot, side="val", path=it.path,
                      note=i18n.KO.NOTE_UNMATCHED)
            for slot, items in self._skipped_b.items() for it in items]
        return matches, misses

    # ==================================================================
    # Stage 1
    # ==================================================================
    def _enter_stage1_phase_a(self) -> None:
        assert self._scan is not None and self._input is not None
        # 추출은 짝이 없다 — 한쪽 스캔의 슬롯 전부가 후보다.
        one_side = self._is_extract()
        names = (sorted(self._scan.slots) if one_side
                 else self._scan.common_slot_names)
        slots = [self._scan.slots[n] for n in names]
        # queue: 기준(ref) 사진 전부 (Slot 명 / 파일명 오름차순)
        queue: list[ImageItem] = []
        for slot in sorted(slots, key=lambda s: s.name):
            queue.extend(slot.ref_images)

        # 이전에 이 기준 폴더로 고른 기준 사진이 있으면 재사용할지 물어본다 (#6).
        # ★ 추출은 묻지도 기록하지도 않는다 — 그 기록은 '매칭에 쓸 기준 사진' 이다.
        restored = ({} if one_side
                    else self._maybe_restore_ref_selection(queue))
        self._select_page.set_extract_mode(one_side)     # '검증' ↔ '추출' 문구
        self._select_page.set_reverse(False)
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
        if self._phase == PHASE_B_SELECT:
            targets = self._collect_panel(self._select_page.get_state().targets)
            queue = [it for slot in sorted(targets) for it in targets[slot]]
            if not queue:                   # 검증 쪽에 더 볼 사진이 없다 — 결과로
                self._finish_session()
                return
            self._start_cross_b_match(queue)
            return
        if self._phase == PHASE_A_SELECT and self._is_extract():
            self._on_extract_select_finished()
            return
        if self._phase == PHASE_A_SELECT:
            self._stage1_a_snapshot = {
                "targets": self._collect_panel(self._select_page.get_state().targets),
                "excluded": self._collect_panel(self._select_page.get_state().excluded),
            }
            # ★ 한 장도 안 골랐으면 **되돌릴 기회를 준다**(사용자 요청).  막지는
            #   않는다 — '전부 제외' 도 유효한 결정일 수 있다.  다만 그대로 넘어가면
            #   매칭할 사진이 0장이라 다음 화면이 통째로 비므로, 그 사실을 말하고
            #   묻는다.  기록(`_save_ref_selection`)보다 **먼저** 물어야 빈 선택이
            #   '다음에 재사용할 기준' 으로 남지 않는다.
            if not any(self._stage1_a_snapshot["targets"].values()):
                r = sheets.ask(
                    self, i18n.KO.SELECT_NONE_CHOSEN_TITLE,
                    i18n.KO.SELECT_NONE_CHOSEN_BODY,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if r == QMessageBox.StandardButton.Yes:
                    self._on_select_cancelled()     # 선별 상태를 버리고 설정으로
                    return
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

        auto_mode = AutomationLevel.is_auto(self._input.automation_level)
        self._calibrate_then(queue, pool, auto_mode=auto_mode)

    # ==================================================================
    # 좌표 보정 (KLA ↔ Camtek) — Stage 2 매칭 직전
    # ==================================================================
    def _kla_camtek_side(self) -> Optional[str]:
        """이 실행이 KLA ↔ Camtek 이면 KLA 쪽(``"ref"``/``"val"``), 아니면 None.

        폴더의 KLA 정보파일 헤더(WaferID)만 읽는다 — 좌표 파싱보다 훨씬 싸다.
        KLA ↔ KLA·Camtek ↔ Camtek 은 같은 장비 규칙이라 보정할 어긋남이 없다."""
        sr = self._scan
        if sr is None:
            return None

        def is_kla(images, folder) -> bool:
            d = images[0].path.parent if images else folder
            return d is not None and kla_info.read_wafer_id(Path(d)) is not None

        for name in sr.common_slot_names:
            slot = sr.slots[name]
            rk = is_kla(slot.ref_images, slot.ref_dir)
            vk = is_kla(slot.val_images, slot.val_dir)
            if rk != vk:
                return "ref" if rk else "val"
        return None

    def _calibrate_then(self, queue: list, pool: dict, *, auto_mode: bool) -> None:
        """좌표 매칭이고 KLA ↔ Camtek 이면 **좌표 보정 화면**을 거쳐 Stage 2 로.

        보정값은 `_make_sim_cfg` → `CoordScheduler` 로만 간다 — 매칭 계산에만 쓰이고
        엑셀·화면 좌표는 바뀌지 않는다(사용자 지시).  사용자 결정: 평행이동만·실행
        전체 1회·저장 안 함.  좌표 읽기는 백그라운드에서 하고 진행을 오버레이로 보인다
        (CLAUDE.md 로딩 규칙)."""
        self._coord_offset = None
        self._calib_next = (queue, pool, auto_mode)
        engine = getattr(self._input, "engine_mode", EngineMode.COORDINATE)
        self._ask_notch(EngineMode.is_coordinate(engine))
        side = (self._kla_camtek_side()
                if EngineMode.is_coordinate(engine) else None)
        if side is None:
            self._start_stage2()
            return
        self._calib_side = side
        groups = []
        for slot_name in sorted(pool):
            refs = [it for it in queue if it.slot == slot_name]
            if refs:
                groups.append((refs, pool[slot_name]))
        self._loading.show_overlay(i18n.KO.COORD_CAL_LOADING)
        self._loading.set_progress(0, 0, i18n.KO.COORD_CAL_LOADING)
        worker = _CalibPrepWorker(groups, side, self, self._notch_dirs)
        worker.progress.connect(self._on_calib_progress)
        worker.ready.connect(self._on_calib_ready)
        self._calib_worker = worker
        worker.start()

    def _ask_notch(self, coordinate: bool) -> None:
        """좌표 매칭이면 기준·검증 notch 방향을 묻는다 — 결과는 `_notch_dirs`(매칭 전용).

        처음 값은 폴더에서 읽은 값(KLA)이거나 표준(아래).  둘 다 표준이면 보정 없음."""
        self._notch_dirs = ()
        if not coordinate or self._scan is None or self._input is None:
            return
        from ..coords import notch
        from .widgets.notch_dialog import NotchDialog
        sr = self._scan

        def guess(attr: str) -> str:
            found = [notch.detect(Path(d)) for n in sr.common_slot_names[:5]
                     for d in [getattr(sr.slots[n], attr)] if d is not None]
            return max(set(found), key=found.count) if found else notch.DEFAULT

        dlg = NotchDialog(self._input.ref_machine, self._input.val_machine,
                          guess("ref_dir"), guess("val_dir"), parent=self)
        sheets.run(dlg)
        dirs = ((str(self._input.ref_root), dlg.dirs[0]),
                (str(self._input.val_root), dlg.dirs[1]))
        if any(not notch.is_none(d) for _r, d in dirs):
            self._notch_dirs = dirs
            _LOG.info(i18n.KO.NOTCH_LOG_FMT.format(dirs=", ".join(f"{r}={d}" for r, d in dirs)))

    def _on_calib_progress(self, done: int, total: int) -> None:
        self._loading.set_progress(done, total, i18n.KO.COORD_CAL_LOADING)

    def _on_calib_ready(self, result) -> None:
        if self.sender() is not self._calib_worker:
            return                          # 새 세션으로 밀려난 옛 준비
        self._calib_worker = None
        self._loading.hide_overlay()
        if self._input is None or self._scan is None:
            return
        entries, auto = result
        from ..coords import calibration as cal
        if auto is not None:
            _LOG.info("좌표 보정 자동 계산: dx=%.1f dy=%.1f µm (근거 %d표 · 다른 무리 %d표 · "
                      "전체 %d표 · 일부만 봄 %s · 신뢰 %s)", *auto.offset, auto.support,
                      auto.runner_up, auto.votes, auto.partial, cal.is_confident(auto))
        hint = auto.offset if auto is not None else None
        if cal.is_confident(auto):
            # 믿을 만하면 화면 없이 바로 적용한다(사용자 결정 2026-10-25) — 값은
            # 매칭 화면 표제에 보인다(`match_page._stage2_title`).
            self._coord_offset = auto.offset
            _LOG.info("좌표 보정 자동 적용(KLA→Camtek, 매칭 전용): dx=%.1f dy=%.1f µm",
                      *auto.offset)
        elif entries:
            from PyQt6.QtWidgets import QDialog
            from .widgets.coord_calibration_dialog import CoordCalibrationDialog
            dlg = CoordCalibrationDialog(
                entries, kla_side=self._calib_side, hint=hint, auto=auto,
                tolerance=float(getattr(self._input, "coord_tolerance",
                                        config.DEFAULT_COORD_TOLERANCE)),
                parent=self)
            if (sheets.run(dlg, full_bleed=True) == QDialog.DialogCode.Accepted
                    and dlg.offset is not None):
                self._coord_offset = dlg.offset
                _LOG.info("좌표 보정 적용(KLA→Camtek, 매칭 전용): dx=%.1f dy=%.1f µm",
                          *dlg.offset)
        else:
            _LOG.info("좌표 보정: 가까운 짝 후보가 있는 사진이 없어 건너뜀")
        self._start_stage2()

    def _start_stage2(self) -> None:
        nxt, self._calib_next = self._calib_next, None
        if nxt is None or self._input is None or self._scan is None:
            return
        queue, pool, auto_mode = nxt
        reverse = self._cross_stage == "B"
        self._match_page.set_reverse(reverse)
        self._match_page.load_state(
            queue=queue,
            val_pool_by_slot=pool,
            threshold=self._input.threshold,
            session_id=self._session_id,
            auto_mode=auto_mode,
            engine_cfg=self._make_sim_cfg(),
        )
        self._show_page(self._match_page)
        self._phase = PHASE_B_MATCH if reverse else PHASE_A_MATCH
        self._autosave()

    def _on_match_confirmed(self, match: MatchResult) -> None:
        if self._phase == PHASE_A_MATCH:
            self._matches_a.append(match)
        elif self._phase == PHASE_B_MATCH:
            self._matches_b.append(match)
        self._schedule_autosave()

    def _on_match_undone(self, match: MatchResult) -> None:
        """Stage 2 되돌리기(#C1) — 집계된 매칭에서도 해당 항목을 제거.

        같은 ``key``(slot/ref명/val명) 의 마지막 항목을 제거한다.
        """
        bucket = {PHASE_A_MATCH: self._matches_a,
                  PHASE_B_MATCH: self._matches_b}.get(self._phase)
        if bucket is not None:
            for i in range(len(bucket) - 1, -1, -1):
                if bucket[i].key == match.key:
                    del bucket[i]
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
        if self._phase in (PHASE_A_MATCH, PHASE_B_MATCH):
            skipped = (self._skipped_b if self._phase == PHASE_B_MATCH
                       else self._skipped_a)
            if self._phase == PHASE_A_MATCH:
                self._start_cross_b_prefetch()
            st = self._match_page.get_state()
            if st is not None:
                # 미탐으로 기록할 것은 ‘매칭 없음 확정’ 만. ‘잠시 보류’ 는 사용자
                # 결정 미정 → 미탐 시트에 넣지 않는다.
                for slot, items in st.no_match.items():
                    seen = {it.key for it in skipped[slot]}
                    skipped[slot].extend(
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
        reverse = self._cross_stage == "B"
        if self._multi():
            if not reverse:                  # 다음 검증 장비로, 다 했으면 합친 검토
                self._multi_after_match()
                return
            self._snapshot_leg_ctx()         # 2차 — 결과 화면이 이 장비 것을 쓰게
        merged = list(self._matches_b) if reverse else self._merge_matches()
        self._match_review_page.set_reverse(reverse)
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

        교차검증이면 결과 목록에 두 방향이 섞여 있다 — 새 매치를 A·B 몫으로 갈라
        각자의 보관처를 같은 규칙으로 고친다(`cross_check.split_new_matches`).
        """
        if self._cross_stage:
            self._on_result_edited_cross(matches, unmatched)
            return
        self._apply_a_edit(matches, unmatched)

    def _apply_a_edit(self, matches: list, unmatched: list) -> None:
        """첫 방향(A) 보관처를 결과 화면의 편집에 맞춘다(`_on_result_edited`)."""
        self._reviewed_matches = list(matches)
        self._a_reviewed = True
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

    def _on_result_edited_cross(self, matches: list, unmatched: list) -> None:
        b_matches, b_misses = self._cross_b_parts()
        a_matches = self._a_final_matches()
        new_a, new_b = cross_check.split_new_matches(
            matches, a_matches + b_matches, [u.path for u in b_misses])
        self._apply_a_edit(a_matches + new_a,
                           [u for u in unmatched if u.side == "ref"])
        # B — A 와 같은 규칙을 큐 방향으로.  B 보관처는 이제 '검토를 거친 것' 이다.
        b_kept = b_matches + new_b
        matched_vals = {Path(m.val_path) for m in matches}
        self._b_kept = b_kept
        self._b_misses = [u for u in unmatched if u.side == "val"]
        for slot, its in list(self._skipped_b.items()):
            self._skipped_b[slot] = [it for it in its
                                     if Path(it.path) not in matched_vals]
        still_marked = [m for m in self._b_all
                        if Path(m.ref_path) not in matched_vals
                        and m.key in self._b_unmatched_keys]
        self._b_all = [cross_check.swap(m) for m in b_kept] + still_marked
        self._b_unmatched_keys = {m.key for m in still_marked}
        self._b_reviewed = True

    def _reenter_review(self) -> None:
        """결과 화면 → 매치 검토 화면 복귀(U-10).

        ★ `_proceed_to_review_or_finish` 를 그대로 쓰면 안 된다 — 그쪽은 원본 자동
        매치(`_merge_matches`)로 화면을 다시 만들어 사용자가 검토에서 한 스왑·매치
        없음 표시를 **전부 지워 버린다**.  검토 결과(`_reviewed_matches`)를 기반으로,
        '매치 없음' 표시까지 복원해서 들어간다."""
        if self._match_review_page is None or self._match_page is None:
            return
        if self._multi_reenter_review():
            return
        # ★ 교차검증은 **두 번째 방향(B)의 검토**로 돌아간다.  MatchPage 가 지금 들고 있는
        #   후보·점수가 B 의 것이라 A 검토는 다시 만들 수 없다(A 의 확정분은 그대로 둔다).
        reverse = self._cross_stage == "B"
        self._match_review_page.set_reverse(reverse)
        # '매치 없음' 표시까지 되살리려면 그 행들이 있어야 하므로 **전체 목록**을
        #   기반으로 한다(`_reviewed_matches` 는 표시된 행이 빠진 kept 다).
        if reverse:
            # ★ 검토를 거친 B 는 빈 목록도 확정이다 — `or` 로 이으면 사용자가 전부 버린
            #   뒤에 B 자동 매치(`_matches_b`)가 되살아난다(A 의 `_a_reviewed` 와 같은 이치).
            if self._b_reviewed:
                base = list(self._b_all or [cross_check.swap(m) for m in self._b_kept])
            else:
                base = list(self._matches_b)
            marked = self._b_unmatched_keys
        else:
            base = list(getattr(self, "_reviewed_all_matches", None)
                        or self._a_final_matches())
            marked = getattr(self, "_reviewed_unmatched_keys", None)
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
            unmatched_keys=marked,
        )
        self._notify_ready_for_review()
        self._show_page(self._match_review_page)

    def _notify_ready_for_review(self) -> None:
        """수 분짜리 자동 매칭이 끝난 줄 모르고 다른 창을 보고 있을 수 있다.

        ★ 창이 이미 활성이면 부르지 않는다 — 보고 있는데 작업표시줄이 깜빡이면 잡음이다."""
        if not self.isActiveWindow():
            QApplication.alert(self)

    # ==================================================================
    # Defect 추출 — 매칭 없이 한쪽 폴더에서 고른 사진을 엑셀로
    # ==================================================================
    # 흐름: 설정(추출 모드) → 스캔 → (KLA면 slot명 해석) → 썸네일 → 후보 선별(매칭과
    # 같은 화면) → 요약 → 이름 확인 → 저장 → 설정 화면.  매칭·검토·결과 화면은 거치지
    # 않고, 세션 자동 저장·기준 사진 기록도 하지 않는다(`_autosave` 주석).
    def _is_extract(self) -> bool:
        return bool(getattr(self._input, "extract", False))

    def _journey_steps(self) -> tuple:
        """로딩 오버레이의 단계 이름 — 추출에는 매칭이 없어 마지막 단계 이름만 다르다."""
        return (i18n.KO.EXTRACT_LOAD_JOURNEY_STEPS if self._is_extract()
                else i18n.KO.LOAD_JOURNEY_STEPS)

    def _extract_after_scan(self, sr: ScanResult) -> None:
        """스캔 직후 — 사진 없는 슬롯을 빼고, KLA 면 slot명을 WaferID 로 바꾼 뒤 썸네일로."""
        for name in [n for n, s in sr.slots.items() if not s.ref_images]:
            sr.slots.pop(name)
        if not sr.slots:
            self._loading.hide_overlay()
            wakelock.release()
            sheets.warn(self, i18n.KO.APP_TITLE, i18n.KO.EXTRACT_NONE_FOUND)
            return
        # KLA 여부는 **묻지 않는다**(사용자 지적: "KLA인지 확인하는거 왜뜨냐").  매칭은
        # 짝이 안 맞을 때만 묻지만 예전 추출은 Camtek 폴더에도 매번 물었다.  호기가 K-n
        # 이거나, 폴더에 KLA 정보파일(`WaferID "…";`)이 하나라도 있으면 KLA 다 —
        # 좌표 보정의 장비 판별(`_kla_camtek_side`)과 같은 기준이다.
        is_kla = (self._kla_machine_side(self._input) is not None
                  or self._has_kla_info(sr))
        # 이름은 **해석이 끝난 뒤** 센다 — KLA 면 그 사이 slot명이 WaferID 로 바뀐다.
        proceed = lambda: self._continue_start_after_scan(sorted(sr.slots))  # noqa: E731
        if is_kla:
            self._resolve_kla_extract(sr, on_done=proceed)
        else:
            proceed()

    @staticmethod
    def _has_kla_info(sr: ScanResult) -> bool:
        """추출 대상 폴더 중 하나라도 KLA 정보파일 헤더(WaferID)를 가졌는가.

        헤더 앞부분만 읽고 캐시된다(`kla_info.read_header_ids`) — 이어지는 slot명
        해석이 같은 값을 다시 읽지 않는다."""
        return any(kla_info.read_wafer_id(s.ref_dir) is not None
                   for s in sr.slots.values() if s.ref_dir)

    def _resolve_kla_extract(self, sr: ScanResult, on_done) -> None:
        """KLA 폴더의 slot명 = WaferID — **정보파일 우선, OCR 폴백**(매칭과 같은 순서).

        OCR 은 정보파일에서 못 읽은 폴더에만, 백그라운드 워커에서 돈다."""
        self._loading.show_overlay(i18n.KO.LOAD_KLA_INFO, step=(1, 3),
                                   steps=self._journey_steps())
        QApplication.processEvents()
        info: dict[str, str] = {}
        for name, slot in sr.slots.items():
            wid = kla_info.read_wafer_id(slot.ref_dir) if slot.ref_dir else None
            if wid:
                info[name] = wid

        def finalize(ocr=None) -> None:
            try:
                self._kla_folders = rename_slots_by_wafer_id(sr, {**info, **(ocr or {})})
            finally:
                self._ocr_worker = None
                on_done()

        jobs = [("ref", n, [it.path for it in s.ref_images])
                for n, s in sorted(sr.slots.items()) if n not in info]
        if jobs and wafer_id.ocr_available():
            from ..workers.wafer_id_ocr import WaferIdOcrWorker
            self._loading.show_overlay(i18n.KO.LOAD_KLA_OCR, step=(1, 3),
                                       steps=self._journey_steps())
            worker = WaferIdOcrWorker(jobs, parent=self)
            self._ocr_worker = worker          # GC 방지 참조 보관
            worker.signals.progress.connect(
                lambda d, t: self._loading.set_progress(d, t, i18n.KO.LOAD_KLA_OCR))
            worker.signals.done.connect(lambda ocr_ref, _val: finalize(ocr_ref))
            worker.signals.failed.connect(lambda _msg: finalize())
            worker.start()
            return
        finalize()

    def _on_extract_select_finished(self) -> None:
        """후보 선별이 끝났다 — 오른쪽(고른 사진)이 곧 추출 대상이다."""
        st = self._select_page.get_state()
        picked = {k: list(v) for k, v in (st.targets if st else {}).items() if v}
        if not picked:
            r = sheets.ask(
                self, i18n.KO.EXTRACT_NONE_CHOSEN_TITLE,
                i18n.KO.EXTRACT_NONE_CHOSEN_BODY,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if r == QMessageBox.StandardButton.Yes:
                self._on_select_cancelled()
            return
        self._finish_extract(picked)

    def _finish_extract(self, picked: dict[str, list[ImageItem]]) -> None:
        """간단한 요약 → 이름 확인 → 저장(사용자 결정: 결과 화면 없이 바로 저장)."""
        assert self._input is not None
        result = extract_result(
            self._input.ref_machine,
            {slot: [it.path for it in items] for slot, items in picked.items()},
            kla_folders=getattr(self, "_kla_folders", {}),
            slot_numbers=self._slot_numbers())
        from ..workers import exporter as ex

        from_select = self._stack.currentWidget() is self._select_page
        # 저장 방식(Recipe 나누기)을 여기서 고른다(사용자 결정) — 마지막 선택이 기본.
        layouts = [(ex.RECIPE_LAYOUT_SINGLE, i18n.KO.EXTRACT_SAVE_SINGLE),
                   (ex.RECIPE_LAYOUT_SHEETS, i18n.KO.EXTRACT_SAVE_SHEETS),
                   (ex.RECIPE_LAYOUT_COLUMNS, i18n.KO.EXTRACT_SAVE_COLUMNS)]
        last = getattr(_prefs.load(), "extract_recipe_layout", ex.RECIPE_LAYOUT_SINGLE)
        if last not in dict(layouts):
            last = ex.RECIPE_LAYOUT_SINGLE
        choice = sheets.choose(
            self, i18n.KO.EXTRACT_SUMMARY_TITLE,
            i18n.KO.EXTRACT_SUMMARY_FMT.format(wafers=len(result.slot_images),
                                               n=len(result.unmatched_refs))
            + "\n\n" + i18n.KO.EXTRACT_SUMMARY_LAYOUT_HINT,
            [(key, label, "primary" if key == last else "ghost")
             for key, label in layouts]
            + [("back", i18n.KO.EXTRACT_SUMMARY_BACK if from_select
                else i18n.KO.EXTRACT_SUMMARY_CANCEL, "ghost")],
            default=last)
        dst = None
        if choice in dict(layouts) and self._working_xlsx is not None:
            _prefs.patch(extract_recipe_layout=choice)
            from .widgets.save_name_dialog import SaveNameDialog
            dlg = SaveNameDialog(self._working_xlsx.name, self._working_xlsx.parent,
                                 parent=self, body=i18n.KO.EXTRACT_SAVE_NAME_BODY)
            if sheets.run(dlg) and dlg.chosen is not None:
                dst = self._working_xlsx.with_name(dlg.chosen)
        if dst is None:
            # 선별에서 왔으면 그 화면에 남는다(Z 로 되돌려 다시 고를 수 있다).
            # 선별을 건너뛴 경우(모든 사진 자동)에는 돌아갈 화면이 설정뿐이다.
            if not from_select:
                self._new_session()
            return
        self._start_extract_export(result, dst, choice)

    def _start_extract_export(self, result: FinalResult, dst: Path,
                              layout: str) -> None:
        from ..workers.exporter import ExcelExporter
        from .widgets.wafer_map_view import render_map_png

        self._extract_result, self._extract_dst = result, dst
        self._extract_layout = layout
        self._rail.set_current(2)            # 추출 레일의 마지막 단계 '저장'
        self._rail.set_navigable(())
        self._loading.show_overlay(i18n.KO.LOAD_EXPORT)
        # Wafer Map 시트는 설정 화면 스위치로 끌 수 있다(사용자 결정) — 렌더러가 없으면
        # exporter 가 그 시트를 만들지 않는다.
        with_map = bool(getattr(self._input, "extract_wafer_map", True))
        exporter = ExcelExporter(result, dst, template_path=self._template_used,
                                 map_renderer=render_map_png if with_map else None,
                                 recipe_layout=layout)
        self._extract_exporter = exporter      # GC 방지 + closeEvent 에서 취소
        exporter.signals.progress.connect(
            lambda d, t, msg: self._loading.set_progress(
                d, t, msg or i18n.KO.LOAD_EXPORT))
        exporter.signals.done.connect(self._on_extract_export_done)
        exporter.signals.failed.connect(self._on_extract_export_failed)
        exporter.start()

    def _on_extract_export_done(self, path: str) -> None:
        self._loading.hide_overlay()
        self._extract_saved = Path(path)
        # 워커 시그널 슬롯에서 곧바로 모달을 열지 않는다(결과 화면과 같은 이유).
        QTimer.singleShot(0, self._offer_open_extract)

    def _offer_open_extract(self) -> None:
        """저장 완료 — 파일/폴더 열기를 권하고 설정 화면(추출 모드 그대로)으로."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        path = self._extract_saved
        choice = sheets.choose(
            self, i18n.KO.APP_TITLE, i18n.KO.SAVE_SUCCESS_FMT.format(path=path),
            [("open_file", i18n.KO.BTN_OPEN_SAVED_FILE, "primary"),
             ("open_dir", i18n.KO.BTN_OPEN_SAVED_FOLDER, "ghost"),
             ("close", i18n.KO.MSG_BTN_CLOSE, "ghost")],
            default="open_file")
        if choice in ("open_file", "open_dir"):
            target = path if choice == "open_file" else path.parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        self._new_session()

    def _on_extract_export_failed(self, msg: str) -> None:
        """실패 — 결과 화면과 같은 안내(열려 있는 파일이 가장 흔한 원인) + 다시 시도."""
        self._loading.hide_overlay()
        self._sync_rail(self._stack.currentWidget())      # '저장' → 지금 화면
        locked = "permission" in msg.lower() or "errno 13" in msg.lower()
        body = (i18n.KO.SAVE_FAIL_LOCKED_FMT.format(error=msg) if locked
                else i18n.KO.SAVE_FAIL_FMT.format(error=msg))
        choice = sheets.choose(
            self, i18n.KO.APP_TITLE, body,
            [("retry", i18n.KO.BTN_RETRY_SAVE, "primary"),
             ("close", i18n.KO.MSG_BTN_CLOSE, "ghost")],
            default="retry")
        if choice == "retry":
            self._start_extract_export(self._extract_result, self._extract_dst,
                                       self._extract_layout)
        elif self._stack.currentWidget() is not self._select_page:
            self._new_session()

    # ==================================================================
    # Result
    # ==================================================================
    def _finish_session(self) -> None:
        assert self._scan is not None and self._input is not None
        if self._multi_leg_finished():       # 여러 검증 장비 — 다음 2차 또는 결과로
            return
        result = self._build_final_result()
        self._show_final_result(result)

    def _build_final_result(self) -> FinalResult:
        """지금 실행(구간)의 최종 결과 — 화면 상태를 바꾸지 않는다."""
        # 자동 모드 + 매치 검토를 거친 경우 reviewed_matches 가 우선(비어 있어도).
        merged = self._a_final_matches()
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
        if self._cross_stage:
            # 두 방향을 합친다 — 한쪽에서 놓친 사진이 다른 방향에서 짝을 찾았으면
            # 미매칭에서 빠진다(`cross_check.merge` 불변식).
            b_matches, b_misses = self._cross_b_parts()
            merged, unmatched_refs = cross_check.merge(
                merged, unmatched_refs, b_matches, b_misses)

        result = FinalResult(
            mode=self._input.mode,
            ref_machine=self._input.ref_machine,
            val_machine=self._input.val_machine,
            matches=merged,
            slot_only_ref=list(self._scan.ref_only),
            slot_only_val=list(self._scan.val_only),
            unmatched_refs=unmatched_refs,
            kla_folders=dict(getattr(self, "_kla_folders", {})),
            slot_numbers=self._slot_numbers(),
            # Wafer map — 슬롯마다 양쪽 사진 전부(매치 여부는 matches 로 가른다).
            slot_images={
                name: ([it.path for it in s.ref_images],
                       [it.path for it in s.val_images])
                for name, s in self._scan.slots.items()
                if s.ref_images or s.val_images
            },
        )
        return result

    def _show_final_result(self, result: FinalResult) -> None:
        # 결과 페이지에는 ‘결과 파일이 놓일 경로’ 와 ‘템플릿 원본’ 둘 다 전달
        # (파일은 아직 없다 — [엑셀로 저장] 때 그 경로에 만들어진다).
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
        ctx = self._review_ctx()
        fast_results, classical = ctx.fast_results, ctx.coord_classical_refs
        if self._ctx_a is not None:
            # 교차검증 — A 의 선계산(키 = 기준 사진)과 B 의 것(키 = 검증 사진)을 합친다.
            #   키가 겹치지 않는다: 같은 경로가 양쪽 폴더에 있을 수 없다.
            #   점수 캐시는 MatchPage 가 같은 객체를 계속 쓰고 키가 방향을 가진다
            #   ((기준, 검증) ↔ (검증, 기준)) — 따로 합칠 것이 없다.
            a_fast, a_classical = self._ctx_a
            fast_results = {**a_fast, **fast_results}
            classical = frozenset(a_classical) | frozenset(classical)
        self._result_page.show_result(
            result,
            template_path=self._template_used,
            target_path=self._working_xlsx,
            auto_mode=auto_mode,
            val_pool=review_pool,
            score_cache=ctx.score_cache,
            fast_results=fast_results,
            coord_mode=ctx.coord_mode,
            tolerance=ctx.tolerance,
            review_scores=ctx.review_scores,
            coord_classical_refs=classical,
        )
        # 단일 실행이면 장비 버튼을 감춘다(여러 장비면 `_multi_show_result` 가 다시 건다).
        self._result_page.set_equipments([], 0, None)
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
                           for m in {**(getattr(self, "_slot_meta_ref", None) or {}),
                                     **(getattr(self, "_slot_meta_val", None) or {})}.values())
            rec = run_log.build_record(
                options=options, ref_root=inp.ref_root, val_root=inp.val_root,
                slot_count=len(common), ref_photos=ref_photos, val_photos=val_photos,
                elapsed_s=elapsed, kla_used=kla_used, ocr_used=ocr_used)
            run_log.record(rec, elapsed_s=elapsed, uploader=None)
        except Exception as exc:
            _LOG.debug("실행 로그 기록 실패: %s", exc)
        session_mod.clear()

    # ------------------------------------------------------------------
    # 결과 파일 — 이름과 자리만 정한다
    # ------------------------------------------------------------------
    def _prepare_working_file(self, inp: SetupInput) -> None:
        """결과 엑셀의 **경로만** 정한다 — 파일은 여기서 만들지 않는다.

        ★ 예전에는 [검증 시작] 때 `양식.xlsx` 를 결과 폴더로 복사해 '작업 파일' 을
        만들어 두었다.  그런데 저장까지 가지 않은 세션(도중에 접거나, 스캔 결과가 없거나,
        그냥 닫은 경우)마다 **양식 그대로인 빈 xlsx 가 결과 폴더에 하나씩 남았다**
        (사용자 신고: "빈 파일이 너무 많아짐").  저장(`workers/exporter`)은 어차피
        양식을 직접 열어 채운 뒤 목적지에 쓰므로 미리 복사할 이유가 없었다 — 파일은
        [엑셀로 저장] 을 눌러 이름을 확정한 순간에만 생긴다.

        결과 파일 이름은 :meth:`_suggest_result_name` 규칙을 따르고, 결과 폴더에 같은
        이름이 이미 있으면 타임스탬프를 붙여 충돌을 피한다(지난 세션의 결과를 덮지 않게).
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

        # 파일 이름 — "장비Layer_자재검증(장비기준)" (사용자 요청).
        if self._multi():                    # 검증 장비 여러 대 — 이름을 모두 적는다
            inp = replace(inp, val_machine="+".join(self._leg_labels()))
        dst_name = self._suggest_result_name(inp)
        dst = paths.results_dir() / dst_name
        if dst.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dst = dst.with_name(dst.stem + f"_{ts}" + dst.suffix)
        self._working_xlsx = dst

    def _slot_numbers(self) -> dict[str, str]:
        """{slot명 → 카세트 슬롯 번호} — 엑셀 B열의 `(#6)` 표기용.

        번호는 슬롯 폴더의 ``WaferInfo.ini`` 의 ``ActiveSlot`` 이다.  기준·검증 폴더를
        모두 읽고 **값이 다르면 병기**한다(KLA 폴더명 표기와 같은 관습 — 한쪽만 남기면
        어느 장비의 번호인지 알 수 없다).  같은 웨이퍼라도 두 장비에서 카세트 위치가
        다를 수 있어 한쪽 값으로 단정하지 않는다.

        KLA 폴더에는 ``WaferInfo.ini`` 가 없다 — 정보파일 헤더의 ``Slot N;`` 이 같은
        번호다(``kla_info.read_slot_no``).  예전에는 이걸 보지 않아 KLA 쪽 웨이퍼는
        엑셀에 번호가 빠졌다.

        못 읽은 슬롯은 아예 넣지 않는다 → 엑셀은 slot명만 찍는다.  파일 접근은 슬롯당
        최대 두 번(INI 1개씩)이고 :func:`read_active_slot` 이 폴더별로 캐시하므로,
        결과↔검토를 오가며 이 함수가 다시 돌아도 왕복이 늘지 않는다.
        """
        from ..models.lot_info import read_active_slot

        out: dict[str, str] = {}
        if self._scan is None:
            return out
        # KLA 로 해석된 슬롯만 정보파일을 본다 — 다른 폴더까지 보면 사진 수천 장 폴더를
        # 슬롯마다 한 번 더 훑는다(정보파일 후보를 찾는 열거).
        kla_slots = set(getattr(self, "_kla_folders", {}) or {})
        for name, slot in self._scan.slots.items():
            nums: list[str] = []
            for d in (slot.ref_dir, slot.val_dir):
                n = read_active_slot(Path(d)) if d else None
                if n is None and d and name in kla_slots:
                    n = kla_info.read_slot_no(Path(d))
                if n and n not in nums:
                    nums.append(n)
            if nums:
                out[name] = " / ".join(nums)
        return out

    @staticmethod
    def _suggest_result_name(inp: SetupInput) -> str:
        """결과 엑셀의 추천 제목 — `{검증장비} {Layer}_{자재} 검증({기준장비} 기준).xlsx`.

        Layer·자재는 **검증 대상 폴더**의 `WaferInfo.ini` `InputLot=자재-Layer` 에서
        읽는다(사용자 결정).  못 읽으면 예전 이름으로 폴백한다 — 그때도 저장은 되어야
        하고, 어차피 마지막 저장 직전에 사용자가 고칠 수 있다.

        ⚠ 파일시스템 접근은 **슬롯 하나의 INI 한 번**이다(`read_lot_info`).  느린
        NAS 라도 왕복 두어 번이라 [검증 시작] 의 오버레이 뒤에서 티가 나지 않는다 —
        여기서 폴더를 훑기 시작하면 설정 화면 die 안내가 겪은 그 정지가 재현된다."""
        from ..models.lot_info import read_lot_info

        if getattr(inp, "extract", False):
            # 추출 — 같은 규칙(자재·Layer 는 WaferInfo.ini)에 '추출' 을 붙인다(사용자 결정).
            lot = read_lot_info(inp.ref_root)
            if lot is None:
                return i18n.KO.EXTRACT_FILE_TITLE_FALLBACK_FMT.format(
                    machine=inp.ref_machine)
            return i18n.KO.EXTRACT_FILE_TITLE_FMT.format(
                machine=inp.ref_machine, layer=lot.layer, material=lot.material)
        lot = read_lot_info(inp.val_root)
        if lot is None:
            _LOG.info("WaferInfo.ini 에서 자재·Layer 를 못 읽어 기본 제목을 씁니다: %s",
                      inp.val_root)
            return i18n.KO.RESULT_FILE_TITLE_FALLBACK_FMT.format(
                val=inp.val_machine, ref=inp.ref_machine)
        return i18n.KO.RESULT_FILE_TITLE_FMT.format(
            val=inp.val_machine, ref=inp.ref_machine,
            layer=lot.layer, material=lot.material)

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
        # 준비 중인 좌표 보정은 버린다(뒤늦은 ready 는 `_on_calib_ready` 가 무시한다).
        self._calib_worker = None
        self._calib_next = None
        self._coord_offset = None
        self._notch_dirs = ()
        # #14 세션 종료 — 절전 억제 해제.
        wakelock.release()
        self._stop_lazy_thumb_pools()
        self._matches_a.clear()
        self._skipped_a.clear()
        self._stage1_a_snapshot = None
        self._reviewed_matches.clear()
        self._a_reviewed = False
        self._reviewed_unmatched.clear()
        self._reviewed_unmatched_keys = set()
        self._reviewed_all_matches = []
        self._reset_cross_state()
        self._legs = []
        self._leg_idx = 0
        self._leg_ctx = None
        self._run_log_written = False
        self._phase = PHASE_NONE
        self._slot_meta_ref = None
        self._slot_meta_val = None
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
        오므로, 다 불러온 뒤(``_on_backend_loaded``)에 만든다.  이미 준비가 끝난
        뒤의 재구축이면 그 자리에서 다섯을 모두 만든다.

        ``progress(done, total, message)`` 를 주면 진행을 보고한다(시작 스플래시)."""
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
        self._setup_page.extract_mode_changed.connect(self._on_extract_mode_changed)
        self._setup_page.cross_mode_changed.connect(self._on_extract_mode_changed)
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
        self._match_page.coord_candidates_ready.connect(self._on_coord_candidates)
        self._result_page.new_session_requested.connect(self._new_session)
        self._match_review_page.finished.connect(self._on_match_review_done)
        self._result_page.back_to_review_requested.connect(self._reenter_review)
        # 실패 검토에서 새로 확정한 매치가 결과 객체에만 남아 있으면, 검토로 돌아갔다
        # 재완료할 때 새 FinalResult 가 만들어지며 사라진다 — 여기로 되돌려 받는다.
        self._result_page.result_edited.connect(self._on_result_edited)
        self._result_page.equip_requested.connect(self._on_equip_requested)

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

    def backend_settled(self) -> bool:
        """백그라운드 import 와 나머지 화면 생성이 끝났는가(성공·실패 무관).

        `main._wait_backend` 가 이것을 보고 **창을 띄우기 전에** 기다린다.  창이 뜬 뒤에
        끝나면 그 순간 화면이 몇 초 멈춘다 — 사용자는 막 [폴더 선택…] 을 누른 참이라
        폴더 창을 닫자마자 앱이 굳는 것으로 보였다(앱을 켜고 처음 한 번만 — 사용자 확인).
        import 하는 C 확장(cv2·OpenVINO)은 DLL 을 올리는 동안 GIL 을 놓지 않아, 다른
        스레드에서 돌려도 화면 스레드를 굶긴다."""
        return getattr(self, "_backend_settled", False)

    def _show_backend_error(self) -> None:
        """구성 요소 로딩 실패 안내 — **창이 보일 때** 띄운다.

        `main._wait_backend` 가 창을 띄우기 전에 이벤트를 돌리므로 실패가 창이 숨은 채로
        들어올 수 있다.  숨은 창에 시트를 열면 보이지 않는 팝업이 중첩 루프를 쥐고
        시작이 그 자리에서 멈춘다 — 보일 때까지 미룬다."""
        if not self.isVisible():
            deferred.call_later(self, 200, self._show_backend_error)
            return
        sheets.error(self, i18n.KO.APP_TITLE,
                     i18n.KO.BACKEND_LOAD_FAILED_FMT.format(err=self._backend_error))

    def _on_backend_loaded(self) -> None:
        """무거운 모듈이 다 올라왔다 — 나머지 페이지를 만들고 시작 버튼을 연다."""
        if self._select_page is None:
            try:
                # 화면 스레드에서 위젯을 만드는 구간이라 그동안 UI 가 멈춘다 — 폴더 창을
                # 닫은 직후의 정지(`setup_page._log_stall`)와 시각을 맞춰 보려고 남긴다.
                t0 = time.perf_counter()
                self._build_remaining_pages()
                _LOG.info("나머지 화면 생성: %.2f초(화면 스레드)",
                          time.perf_counter() - t0)
            except Exception as exc:          # import 실패가 여기서 표면화된다
                self._backend_settled = True
                self._backend_error = exc
                self._show_backend_error()
                return                        # 시작 버튼은 '준비 중' 그대로 둔다
        self._backend_settled = True
        self._backend_ready = True
        try:
            self._setup_page.set_backend_ready(True)
        except RuntimeError:
            return                            # 창이 이미 닫혔다
        # GPU 임베딩 모델을 미리 컴파일/워밍업 — 첫 슬롯의 커널 JIT 지연 제거(#3).
        # ★ 무거운 import 뒤에 이어 붙인다(같은 모듈이 필요하고, 시작 경로를 비운다).
        self._warmup_accel_async()

    def _apply_statusbar_theme(self) -> None:
        """상태바 크레딧 라벨 색을 테마 토큰으로 적용(페이지 밖 위젯)."""
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
    # 교차검증 레일(8단계)의 단계 → 위 표의 단계.  선별·매칭·검토가 1차(1~3)·2차(4~6)
    # 두 번 나오고 결과가 7 이다.  복귀 경로는 같은 화면이 가진 그대로다 — 결과에서는
    # 2차 검토로만 돌아간다(`_reenter_review`).
    _CROSS_RAIL_STEP = {0: 0, 1: 1, 2: 2, 3: 3, 4: 1, 5: 2, 6: 3, 7: 4}

    def _rail_index(self, w: QWidget) -> int:
        """레일에 표시할 단계 번호 — 교차검증의 2차(검증 → 기준)는 3칸 뒤다."""
        idx = self._page_order(w)
        if not self._cross_stage or idx == 0:
            return idx
        if idx == 4:
            return 7
        return idx + 3 if self._cross_stage == "B" else idx

    def _rail_routes(self, rail_idx: int) -> tuple[int, ...]:
        if not self._cross_stage:
            return self._RAIL_ROUTES.get(rail_idx, ())
        if rail_idx == 7:
            return (6,)
        return (0,) if rail_idx else ()

    def _sync_rail(self, w: QWidget, *, animate: bool = False) -> None:
        """레일의 현재 단계와 '눌러서 갈 수 있는 단계' 를 화면에 맞춘다.

        ``animate`` 는 페이지가 실제로 슬라이드할 때만 True 다 — 그때 레일 눈금이
        먼저 채워지고, 창은 그 뒤에 화면을 밀어 넣는다(21안-A)."""
        idx = self._rail_index(w)
        self._rail.set_labels(self._rail_labels(w))
        self._rail.set_current(idx, animate=animate)
        self._rail.set_navigable(self._rail_routes(idx))

    def _rail_labels(self, w: QWidget) -> tuple:
        """레일 단계 이름 — Defect 추출은 매칭·검토·결과를 거치지 않아 3단계다(사용자 결정).
        교차검증은 선별·매칭·검토를 두 번 돌아 8단계다(사용자 요청 2026-10-24).

        설정 화면에서는 입력란의 모드가, 그 뒤로는 시작한 실행의 모드가 기준이다."""
        if w is self._setup_page and self._setup_page is not None:
            extract = self._setup_page.is_extract_mode()
            cross = self._setup_page.is_cross_mode()
        else:
            extract = self._is_extract()
            cross = bool(self._cross_stage)
        if extract:
            return i18n.KO.EXTRACT_JOURNEY_RAIL_STEPS
        return (i18n.KO.CROSS_JOURNEY_RAIL_STEPS if cross
                else i18n.KO.JOURNEY_RAIL_STEPS)

    def _on_extract_mode_changed(self, _on: bool) -> None:
        self._sync_rail(self._stack.currentWidget())

    def _on_rail_step_clicked(self, target: int) -> None:
        """레일에서 지난 단계를 눌렀다 — 그 화면이 가진 복귀 흐름을 그대로 부른다.

        ★ 폐기 확인을 여기서 새로 쓰지 않는다.  선별/매칭은 이미 자기 규칙(무엇이
        사라지는지)을 알고 물어보므로 그쪽을 부르는 것이 단일 출처다."""
        rail_cur = self._rail_index(self._stack.currentWidget())
        if target not in self._rail_routes(rail_cur):
            return
        cur = (self._CROSS_RAIL_STEP[rail_cur] if self._cross_stage
               else rail_cur)
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
        if page is None or self._input is None or self._is_extract():
            # 시작 전이면 기준이 확정되지 않았고, 추출에는 판정 기준이 없다.
            self._rail.set_criteria("")
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
        # ★ 레일 선행 릴레이 — 레일 눈금이 먼저 차오르기 시작하고(140ms), 그 절반
        #   (``DUR_RAIL_HANDOFF`` 70ms)에서 화면 전환이 이어받는다(03안).  예전(21안-A)엔
        #   다 찬 **뒤** 시작해서 누른 뒤 화면이 반응하기까지 기다리는 느낌이 있었다 —
        #   사용자가 실제 화면 녹화로 비교하고 반만 겹치는 쪽을 골랐다(2026-10).
        #   레일이 먼저 출발하므로 '레일 → 화면' 순서는 그대로 읽힌다.
        #   지연 중 새 전환이 들어오면 **마지막 것만** 산다(연타 보호).
        self._pending_page = w
        timer = getattr(self, "_page_lead_timer", None)
        if timer is None:
            # 정적 QTimer.singleShot 금지 — 창이 먼저 닫히면 죽은 위젯을 건드린다.
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._run_pending_page_transition)
            self._page_lead_timer = timer
        timer.start(motion.DUR_RAIL_HANDOFF)

    def _run_pending_page_transition(self) -> None:
        """레일이 반쯤 찼다 — 이제 화면을 밀어 넣는다(릴레이의 두 번째 박자)."""
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
        #   자세한 근거는 `motion.snapshot`.
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
        if self._is_extract():
            return          # 추출은 이어하기·기준 사진 기록 대상이 아니다(`_autosave`)
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
        # ★ 추출 세션은 저장하지 않는다 — '이어하기' 는 매칭 세션을 되살리는 기능이라
        #   추출 입력이 남으면 다음 실행에서 매칭으로 복원된다.
        if self._input is None or self._is_extract():
            return
        # ★ 단계가 없으면(끝났거나 접은 세션) 저장하지 않는다 — 결과까지 간 세션은 실행
        #   로그가 파일을 지웠는데, 30 초 타이머가 stage "none" 으로 다시 써 두었다.
        if self._phase == PHASE_NONE:
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
        # ★ 열린 시트의 중첩 루프를 끝낸다.  시트는 창이 숨을 때 저절로 닫히지 않는다
        #   (최소화로 팝업이 사라지지 않게 — `SheetHost.eventFilter`).
        self._sheets.close_all()
        # #14 절전 억제 해제 (남아 있을 경우).
        wakelock.release()
        if self._thumb_pool is not None:
            self._thumb_pool.stop()
            self._thumb_pool.wait(1000)
        self._stop_lazy_thumb_pools(wait_ms=500)
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
            _LOG.warning("종료 중 점수 사전 계산 워커 정리 실패 — 계속 종료합니다", exc_info=True)
        # OpenVINO 설치 워커 정리.
        try:
            if (self._openvino_worker is not None
                    and self._openvino_worker.isRunning()):
                self._openvino_worker.stop()
                self._openvino_worker.wait(500)
        except Exception:
            _LOG.warning("종료 중 OpenVINO 설치 워커 정리 실패 — 계속 종료합니다", exc_info=True)
        # ★ KLA OCR 워커 — `_ocr_worker` 는 __init__ 에 선언이 없어(그 단계를 안 거친
        #   세션엔 속성 자체가 없다) getattr 로 방어한다.  정리하지 않으면 창을 닫을 때
        #   "QThread: Destroyed while thread is still running" 으로 강제 종료됐다.
        try:
            ocr = getattr(self, "_ocr_worker", None)
            if ocr is not None and ocr.isRunning():
                ocr.stop()
                ocr.wait(500)
        except Exception:
            _LOG.warning("종료 중 KLA OCR 워커 정리 실패 — 계속 종료합니다", exc_info=True)
        # ★ 엑셀 저장 워커 — 기다리기만 하면 반쯤 쓰인 xlsx 가 남으므로 먼저 취소한다.
        #   Defect 추출의 저장 워커도 같다.
        for exporter in (getattr(self._result_page, "_exporter", None),
                         getattr(self, "_extract_exporter", None)):
            try:
                if exporter is not None and exporter.isRunning():
                    exporter.stop()
                    exporter.wait(3000)
            except Exception:
                _LOG.warning("종료 중 엑셀/추출 저장 워커 정리 실패 — 계속 종료합니다", exc_info=True)
        super().closeEvent(event)
