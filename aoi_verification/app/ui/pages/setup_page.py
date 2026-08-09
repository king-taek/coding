"""초기 입력 화면 (Setup) — 모드/폴더/호기/임계치 입력."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QBoxLayout, QDoubleSpinBox, QFileDialog,
                              QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                              QMessageBox, QScrollArea, QSizePolicy,
                              QToolButton, QVBoxLayout, QWidget)

from ... import i18n
from .. import theme
from ...utils import prefs as _prefs
from ... import config
from ...utils.prefs import AutomationLevel, EngineMode
from ..widgets.app_logo import build_logo_label
from ..widgets.collapsible_section import CollapsibleSection
from ..widgets.neon_button import NeonButton
from ..widgets.neon_card import NeonCard
from ..widgets.no_wheel_slider import NoWheelDoubleSpinBox, NoWheelSlider
from ..widgets.option_group import OptionGroup, reflow_into_grid
from ..widgets.switch_row import SwitchRow
from ..widgets import sheet_host as sheets


@dataclass
class SetupInput:
    mode: str        # 항상 "single" (양쪽 교차검증 제거).
    ref_root: Path
    val_root: Path
    ref_machine: str
    val_machine: str
    threshold: float
    automation_level: str = AutomationLevel.USER_SELECT
    # 유사도 엔진 + 기타 설정 (계산 전용).
    engine_mode: str = EngineMode.COORDINATE   # EngineMode.{BASIC,EFFICIENCY,COORDINATE}
    persist_scores: bool = True      # 유사도 점수 디스크 캐시 — 항상 기본 적용
    accel_concurrency: int = 32      # 고효율 모드 동시 추론 수(in-flight)
    use_cpu: bool = True             # 고효율 장치 토글(테스트용)
    use_gpu: bool = True
    embed_batch: int = 1             # 정적 배치 B (1=끔)
    # 좌표 기반 매칭(v2) 허용 오차 — µm 단위.
    coord_tolerance: float = config.DEFAULT_COORD_TOLERANCE
    # 진행할 슬롯 부분집합 (None = 전체 진행). '일부 슬롯만 진행' 옵션으로 설정.
    selected_slots: Optional[set] = None


# '실행 옵션'·'매칭 설정' 두 카드를 가로로 나란히 세우려면 이만큼은 있어야 한다.
# 이보다 좁으면 세로로 쌓는다 — 나란히 두면 카드가 최소 폭을 못 얻어 가로 스크롤이
# 생긴다(800px 실측 69px 넘침).  후보 배치안 5종을 비교한 끝에 '나란히' 를 채택했고,
# 좁은 창 폴백만 덧붙였다.
_SIDE_BY_SIDE_MIN_W = 900


# 살아 있는 die 기하 스캔을 여기에 붙잡아 둔다 — **페이지의 자식으로 두지 않는다.**
#
# ★ 이유: 실행 중인 QThread 가 파괴되면 Qt 는 "QThread: Destroyed while thread is
#   still running" 으로 프로세스를 죽인다.  다크 모드 전환은 이 페이지를 다시 만들 수
#   있어(`main_window._build_setup_page`), 스캔이 끝나기 전에 페이지가 사라지면 정확히
#   그 일이 벌어진다.  부모를 떼고 여기서 참조를 쥐고 있다가 `finished` 에서 놓아 주면
#   페이지가 언제 죽든 스레드는 자기 수명을 다 살고 조용히 사라진다
#   (`widgets/zoom_window.py` 의 `_LIVE_LOADERS` 와 같은 패턴).
_LIVE_DIE_SCANS: set = set()


class _DieGeometryScan(QThread):
    """기준 폴더의 die 기하를 **워커 스레드에서** 읽는다(UI 가 멈추지 않게).

    이 스캔은 슬롯을 하나씩 열어 INI 를 파싱하므로 자재에 따라 초 단위다 — 실측으로
    슬롯 25개 × 결함 3,000개에서 1.4초, NAS 에서는 파일 왕복이 더해진다.  UI 스레드에서
    돌리면 그동안 창이 통째로 멈춘다(그게 이 변경의 이유다).

    ``token`` 은 **결과가 늦게 도착했을 때 버리기 위한 세대 번호**다.  사용자가 폴더를
    연달아 바꾸면 스캔이 겹치는데, 늦게 끝난 옛 스캔이 새 폴더의 안내를 덮어쓰면 안 된다.
    ``signals`` 는 메인 스레드에서 만들어지므로 emit 은 큐 연결이 된다."""

    class _Signals(QObject):
        done = pyqtSignal(int, object, str, bool)    # token, pitch|None, src, broken

    def __init__(self, token: int, ref_text: str) -> None:
        super().__init__()                  # 부모 없음(위 주석)
        self._token = token
        self._ref_text = ref_text
        self.signals = self._Signals()

    def run(self) -> None:      # type: ignore[override]
        # `_detect_die_geometry` 는 전 구간 fail-safe 지만, 워커에서 예외가 새면
        # 안내가 영원히 '확인 중' 으로 남는다 — 여기서도 한 번 더 막는다.
        try:
            pitch, src, broken = SetupPage._detect_die_geometry(self._ref_text)
        except Exception:
            pitch, src, broken = (None, "", False)
        self.signals.done.emit(self._token, pitch, src, broken)


_LIVE_DIR_PROBES: set = set()


class _DirProbe(QThread):
    """두 폴더가 실제로 있는지 **워커 스레드에서** 확인한다 (P-15).

    ``Path.is_dir()`` 는 값싸 보이지만 네트워크 폴더(NAS)에서는 응답이 초 단위로
    늦거나, 연결이 끊긴 경로에서는 OS 타임아웃까지 통째로 멈춘다.  이 확인은
    입력란을 **한 글자 칠 때마다**(250ms 디바운스) 돌기 때문에, UI 스레드에서
    하면 경로를 타이핑하는 내내 창이 끊긴다.

    ``token`` 은 늦게 도착한 옛 확인을 버리기 위한 세대 번호다 — 사용자가 계속
    타이핑하면 확인이 겹치는데, 옛 결과가 새 경로의 판정을 덮으면 안 된다."""

    class _Signals(QObject):
        done = pyqtSignal(int, str, str, str, str)   # token, ref_text, ref_state, val_text, val_state

    def __init__(self, token: int, ref_text: str, val_text: str) -> None:
        super().__init__()                  # 부모 없음(`_DieGeometryScan` 과 같은 이유)
        self._token = token
        self._ref_text = ref_text
        self._val_text = val_text
        self.signals = self._Signals()

    def run(self) -> None:      # type: ignore[override]
        try:
            ref_state = SetupPage._dir_state(self._ref_text)
            val_state = SetupPage._dir_state(self._val_text)
        except Exception:
            # 여기서 예외가 새면 안내가 영원히 '확인 중' 으로 남는다.
            ref_state = val_state = "missing"
        self.signals.done.emit(self._token, self._ref_text, ref_state,
                               self._val_text, val_state)


class SetupPage(QWidget):
    """검증 시작 화면."""

    start_requested = pyqtSignal(object)             # SetupInput
    update_check_requested = pyqtSignal()            # '업데이트 확인' 버튼
    # 색 모드 변경 → 페이지 재생성 요청.  ★ 새 색 모드를 **인자로 싣는다** — 받는 쪽이
    # prefs 를 다시 읽지 않아도 되게(전환 경로에서 디스크 왕복을 한 번 줄인다).
    appearance_changed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # 손잡이 이동이 끝난 뒤 색을 갈아 끼우기 위한 지연 타이머(연타 합치기 겸용).
        # ★ 부모 있는 타이머여야 페이지가 파괴될 때 함께 죽는다(_on_dark_mode_toggled 주석).
        self._pending_color_mode: Optional[str] = None
        self._appearance_timer = QTimer(self)
        self._appearance_timer.setSingleShot(True)
        self._appearance_timer.timeout.connect(self._emit_appearance_changed)
        # 무거운 구성 요소(영상 처리·가속)가 준비됐는가 — 준비 전엔 [검증 시작] 을
        # 잠근다.  ★ 기본은 True 다: 이 페이지를 단독으로 띄우는 곳(테스트·미리보기)
        # 에서 버튼이 영원히 잠기면 안 된다.  메인 창이 곧바로 False 로 뒤집는다.
        self._backend_ready = True
        # die 기하 스캔(백그라운드) 상태.  `_die_scanned_for` 는 **이미 스캔이 끝난
        # 경로** 다 — 같은 경로로 `_validate` 가 다시 와도(허용 오차를 건드리면 온다)
        # 다시 훑지 않고 캐시한 결과로 문구만 다시 그린다.
        self._die_token = 0
        self._die_scanned_for: Optional[str] = None
        self._die_scanning_for: Optional[str] = None
        self._die_result: tuple = (None, "", False)
        self._die_scan: Optional[_DieGeometryScan] = None
        # 폴더 존재 확인(P-15) — {경로 문자열: '' | 'missing'}.  네트워크 폴더에서
        # `is_dir()` 이 초 단위로 걸리기 때문에 워커가 채우고 UI 는 읽기만 한다.
        self._probe_token = 0
        self._probed: dict[str, str] = {}
        # 헤드리스에서는 동기로 확인한다 — 테스트가 `_validate()` 의 반환값을
        # 호출 즉시 단언한다(`widgets/zoom_window.py` 와 같은 게이트).
        import os
        self._sync_probe = (
            os.environ.get("QT_QPA_PLATFORM", "") == "offscreen")
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        # 좁은/짧은 창에서도 모든 컨트롤에 접근 가능하도록 스크롤 영역으로 감싼다.
        # 기존 디자인을 유지하려고 별도 마진·배경·푸터 chrome 은 추가하지 않는다.
        # 스크롤바는 ‘필요할 때만’ 자동으로 나타난다.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # QScrollArea 자체의 배경/보더가 페이지 배경 위에 겹쳐 보이지 않게.
        # ★ 뷰포트 배경은 '맨 선언(bare)' 스타일시트로 주면 안 된다 — 자식으로 캐스케이드돼
        #   선택된 세그먼트/타일의 :checked 배경(채움)을 덮어써 '빈 박스'로 렌더된다.
        #   뷰포트를 objectName 으로 스코프해 자식에 새지 않게 한다.
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget#qt_scrollarea_viewport { background: transparent; }"
        )
        outer.addWidget(scroll)
        # 색 모드 전환은 이제 이 페이지를 **버리지 않는다**(`main_window._recolor_in_place`).
        # 스크롤 위치·입력값이 저절로 남으므로 옮겨 심는 코드가 필요 없다.
        self._scroll = scroll

        host = QWidget()
        host.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.MinimumExpanding)
        scroll.setWidget(host)

        # 원본과 동일한 외곽 마진/스페이싱 유지.
        root = QVBoxLayout(host)
        # ★ 하드코딩 40/20 이 `PROFILE.page_margin`·`section_gap` 을 죽은 토큰으로
        #   만들고 있었다 — 밀도를 한 곳에서 조절할 수 있게 토큰을 쓴다.
        m = theme.PROFILE.page_margin
        root.setContentsMargins(m, m, m, m)
        root.setSpacing(theme.PROFILE.section_gap)

        # 상단 로고 — 스크롤 **안**에 둔다.  아래를 볼 때 위로 밀려 올라가며
        # 화면을 넓게 쓰게 한다(사용자 요청: 고정 칸이 아니게).
        root.addWidget(build_logo_label(host))

        self._build_body(root)

        # 액션바는 스크롤 **밖**에 고정한다(주요 액션이 항상 손에 닿게).
        if self._pinned_action_bar():
            bar = self._build_action_bar()
            # 스크롤 안 본문과 같은 좌우 마진 + 상단 눈금으로 '고정된 바닥'임을 말한다.
            bar.setContentsMargins(theme.PROFILE.page_margin, 12,
                                   theme.PROFILE.page_margin, 16)
            rule = QWidget(self)
            rule.setFixedHeight(1)
            rule.setProperty("role", "hrule")
            outer.addWidget(rule)
            outer.addWidget(bar)

        # ★ 첫 포커스를 첫 입력란에 둔다.  이전엔 탭 체인 첫 정지가 보기 옵션이라,
        #   키보드 사용자가 폴더를 입력하려면 화면을 다시 만드는 컨트롤(다크 모드)을
        #   먼저 지나야 했다.  QTimer 로 미루는 이유는 show() 이후에야 포커스가 실제로
        #   들어가기 때문.
        QTimer.singleShot(0, self._focus_first_field)


    def _focus_first_field(self) -> None:
        """탭 체인의 출발점을 첫 입력란으로 — 파괴적 컨트롤을 먼저 지나지 않게."""
        edit = getattr(self, "ref_path_edit", None)
        if edit is not None and edit.isVisible():
            edit.setFocus(Qt.FocusReason.OtherFocusReason)

    # ==================================================================
    # 본문 빌더 — 각 조각은 위젯 하나를 만들어 돌려준다.  순서·열 구성은
    # ``_build_body``, 컨트롤 디자인은 개별 빌더에서 바꾼다.
    # ==================================================================
    def _pinned_action_bar(self) -> bool:
        """액션바를 스크롤 밖에 고정할지.

        ★ 항상 True 다.  한때 액션바를 스크롤 **안**에 둔 배치안이 있었는데, 800×600
        에서 내용이 뷰포트를 넘겨 [검증 시작]이 화면 밖으로 밀려났다(실측).  '주요
        액션을 찾으려면 스크롤해야 한다'는 어떤 배치에서도 결함이다."""
        return True

    def _build_body(self, root: QVBoxLayout) -> None:
        """「진행 순서형」 — 한 열, 위에서 아래로(폴더 → 옵션 → 시작).

        폴더가 먼저다: 매번 바뀌는 값이고, 자동화 수준·진행 범위는 대체로 그대로 쓴다.
        그 둘은 전체폭 카드 두 장을 차지하다가 **'실행 옵션' 카드 하나**로 합쳐졌다
        (아래 `_build_run_options_card` 주석 참조).

        두 설정 카드는 ``_place_setting_cards`` 가 창 폭에 따라 나란히/위아래로 놓는다."""
        root.addWidget(self._build_top_bar())
        root.addWidget(self._build_device_row())
        self._place_setting_cards(root)
        root.addWidget(self._build_howto())
        root.addStretch(1)
        if not self._pinned_action_bar():
            root.addWidget(self._build_action_bar())
        # ★ 크레딧을 여기에 두지 않는다 — `main_window` 의 **상태바**가 모든 화면에
        #   공통으로 띄운다(`main_window._credit_label`).  둘 다 두면 한 화면에
        #   'Developed by …' 가 두 번 보인다(실측: 가운데 + 좌하단).

    # ------------------------------------------------------------------
    # 설정 카드 배치 — 두 카드를 나란히(넓을 때) / 위아래로(좁을 때)
    # ------------------------------------------------------------------
    def _place_setting_cards(self, root: QVBoxLayout) -> None:
        """'실행 옵션'·'매칭 설정' 을 가로로 나란히 둔다.

        전에는 두 카드가 각각 전체 폭을 쓰면서 오른쪽이 크게 비었다(1512px 에서 빈 폭
        75%).  나란히 세우면 그 폭을 둘이 나눠 갖는다.

        ★ 두 가지를 지킨다.
        1. **좁은 창에서는 세로로 쌓는다.**  800×600 에서 나란히 두면 카드가 최소 폭을
           확보하지 못해 가로 스크롤이 생긴다(실측 69px 넘침).  이 앱은 가로 넘침을
           금지하므로 임계 폭 미만에서는 원래대로 위아래로 놓는다.
        2. **두 카드를 `AlignTop` 으로 넣는다.**  정렬을 주지 않으면 QHBoxLayout 이
           두 아이템에 *행 높이 전체*(둘 중 큰 sizeHint)를 배정하고, NeonCard 는
           sizePolicy 가 Preferred(=Grow) 라 sizeHint 보다 늘어난다.  그래서 구형
           모드를 켜 '매칭 설정' 이 커지면 '실행 옵션' 카드까지 같이 부풀었다.
        """
        run_card = self._build_run_options_card()
        eng_card = self._build_engine_card()
        self._setting_cards = (run_card, eng_card)
        self._cards_row = QWidget(self)
        self._cards_row.setProperty("role", "rowHost")
        # ★ 레이아웃은 **하나만** 만들고 방향만 뒤집는다(QBoxLayout.setDirection).
        #   전환할 때마다 새 QLayout 을 붙이면 Qt 가 "이미 레이아웃이 있다"며 거부해
        #   카드가 배치되지 않은 채 남는다(실측: 카드가 100×30 으로 찌그러짐).
        self._cards_lay = QBoxLayout(QBoxLayout.Direction.LeftToRight,
                                     self._cards_row)
        self._cards_lay.setContentsMargins(0, 0, 0, 0)
        self._cards_lay.setSpacing(theme.PROFILE.section_gap)
        self._cards_lay.addWidget(run_card)
        self._cards_lay.addWidget(eng_card)
        self._cards_side_by_side: Optional[bool] = None
        self._apply_card_orientation(self._side_by_side_fits())
        root.addWidget(self._cards_row)

    def _side_by_side_fits(self) -> bool:
        """지금 폭에서 두 카드를 나란히 세워도 가로가 넘치지 않는가."""
        width = self.width() or self.sizeHint().width()
        return int(width) >= _SIDE_BY_SIDE_MIN_W

    def _apply_card_orientation(self, side_by_side: bool) -> None:
        """가로↔세로 배치 전환 — 카드는 그대로 두고 레이아웃 방향만 바꾼다."""
        if side_by_side == self._cards_side_by_side:
            return
        self._cards_side_by_side = side_by_side
        lay = self._cards_lay
        lay.setDirection(QBoxLayout.Direction.LeftToRight if side_by_side
                         else QBoxLayout.Direction.TopToBottom)
        for idx, card in enumerate(self._setting_cards):
            # 나란히일 때만 폭을 반씩 나눠 갖는다.
            lay.setStretch(idx, 1 if side_by_side else 0)
            # 위 정렬 — 한 카드가 커져도 다른 카드는 자기 높이를 지킨다.
            lay.setAlignment(card, Qt.AlignmentFlag.AlignTop if side_by_side
                             else Qt.AlignmentFlag(0))

    def _build_top_bar(self) -> QWidget:
        """제목 + 모드 배지 + 보기 옵션 — **한 줄**.

        ★ 예전엔 보기 옵션이 제목 **위**의 별도 줄이었다.  '모션 줄이기' 까지 있던
        시절엔 한 줄에 몰면 800×600 에서 가로가 넘쳤지만, 지금 보기 옵션은 다크 모드
        스위치 하나뿐이라 같은 줄에 들어간다.  줄을 하나로 합치면 제목 위의 빈 띠
        (툴바 높이 + 줄 간격) 가 통째로 사라져 제목이 화면 맨 위로 올라온다
        (사용자 요청: '상단 여백이 너무 많다').  좁은 창 안전은
        `test_setup_top_spacing` 이 800px 가로 넘침 없음으로 지킨다."""
        host = QWidget(self)
        # 제목 왼쪽, 모드 배지 오른쪽.  배지가 제목과 같은 줄에 있어야 '무슨 모드야'가
        # 첫 시선에 들어온다.  보기 옵션은 그 오른쪽 끝.
        tr = QHBoxLayout(host)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.setSpacing(16)
        tr.addWidget(self._build_title())
        tr.addStretch(1)
        tr.addWidget(self._build_mode_badge(),
                     alignment=Qt.AlignmentFlag.AlignVCenter)
        tr.addWidget(self._build_view_options(),
                     alignment=Qt.AlignmentFlag.AlignVCenter)
        return host

    def _build_mode_badge(self) -> QWidget:
        """상단 모드 배지 — 지금 무슨 엔진·무슨 판정 수치로 도는지.

        판정 기준 문장이 엔진 카드 안에만 있어서, A안은 화면 밖·C안은 접힘 아래로
        밀려 '모르고 켜둔 채 실행'을 막지 못했다(채점자 5인 공통 지적).  모드 이름만
        담으면 부족하다 — **판정 수치까지** 담아야 오조작이 눈에 걸린다."""
        card = NeonCard(role="card", parent=self)
        card.setToolTip(i18n.KO.MODE_BADGE_TOOLTIP)
        cap = QLabel(i18n.KO.MODE_BADGE_CAPTION, card)
        cap.setProperty("role", "badgeCaption")
        card.body().addWidget(cap)
        # 이름(한국어, 본문 서체) + 수치(모노) — 한 라벨에 모노를 걸면 한글 글리프가
        # 없어 문장 안에서 서체가 갈린다.
        badge_row = QWidget(card)
        badge_row.setProperty("role", "rowHost")
        brow = QHBoxLayout(badge_row)
        brow.setContentsMargins(0, 0, 0, 0)
        brow.setSpacing(8)
        self._mode_badge = QLabel("", badge_row)
        self._mode_badge.setProperty("role", "modeBadge")
        self._mode_badge_value = QLabel("", badge_row)
        self._mode_badge_value.setProperty("role", "modeBadgeValue")
        brow.addWidget(self._mode_badge)
        brow.addWidget(self._mode_badge_value)
        brow.addStretch(1)
        card.body().addWidget(badge_row)
        self._mode_badge_card = card
        return card

    def judgement_text(self) -> tuple[str, str]:
        """판정 기준을 (이름, 수치) 로 — **단일 출처**.

        ★ 배지와 C안 요약이 각자 문장을 만들어 같은 사실을 서로 다른 말로 했다
        (배지 '구형 고효율 · 유사도 임계치 55 %' vs 요약 '구형 유사도 엔진 (임계치 55 %)').
        판정 기준은 하나이므로 문장도 하나에서 나와야 한다."""
        if self.legacy_switch.is_on():
            sub = (i18n.KO.ENGINE_MODE_EFFICIENCY_SHORT
                   if self.legacy_group.current_key() == EngineMode.EFFICIENCY
                   else i18n.KO.ENGINE_MODE_BASIC_SHORT)
            return (i18n.KO.JUDGE_NAME_LEGACY_FMT.format(sub=sub),
                    i18n.KO.JUDGE_VALUE_LEGACY_FMT.format(
                        th=float(self.slider.value())))
        return (i18n.KO.JUDGE_NAME_COORD,
                i18n.KO.JUDGE_VALUE_COORD_FMT.format(
                    tol=float(self.coord_tol_spin.value())))

    def _refresh_mode_badge(self) -> None:
        badge = getattr(self, "_mode_badge", None)
        if badge is None or not hasattr(self, "legacy_switch"):
            return
        name, value = self.judgement_text()
        badge.setText(name)
        self._mode_badge_value.setText(value)
        # 구형은 예외 경로 — 배지가 색으로도 말하게 한다(모르고 켜둔 채 실행 방지).
        card = getattr(self, "_mode_badge_card", None)
        if card is not None:
            state = "legacy" if self.legacy_switch.is_on() else ""
            card.setProperty("badgeState", state)
            card.style().unpolish(card)
            card.style().polish(card)

    def _build_title(self) -> QWidget:
        # 화면 크기 컨트롤은 별도 버튼 없이 OS 의 표준 창 조작
        # (드래그, 최대화/복원, 모서리 리사이즈) 으로만 처리.
        title = QLabel(i18n.KO.SETUP_TITLE, self)
        title.setProperty("role", "title")
        return title

    def _build_howto(self) -> QWidget:
        """사용 방법 안내 — 접을 수 있는 섹션 (기본 접힘)."""
        _prefs_now = _prefs.load()
        self._howto_section = CollapsibleSection(
            open_label=i18n.KO.HOWTO_TOGGLE_OPEN,
            close_label=i18n.KO.HOWTO_TOGGLE_CLOSE,
            expanded=bool(_prefs_now.howto_expanded),
            parent=self,
        )
        howto_card = NeonCard(role="card-soft", parent=self._howto_section)
        howto_title = QLabel(i18n.KO.SETUP_HOW_TO_USE_TITLE, howto_card)
        howto_title.setProperty("role", "cardTitle")
        howto_card.body().addWidget(howto_title)
        howto_body = QLabel(i18n.KO.SETUP_HOW_TO_USE_BODY, howto_card)
        howto_body.setWordWrap(True)
        howto_body.setProperty("role", "bodyText")
        howto_card.body().addWidget(howto_body)
        self._howto_section.add_content_widget(howto_card)
        self._howto_section.toggled.connect(
            lambda expanded: _prefs.patch(howto_expanded=bool(expanded))
        )
        return self._howto_section

    # ★ 나란히 둘지는 **카드가 요구하는 폭**으로 판단한다 — '900px' 같은 매직 넘버는
    #   맞출 수 없다.  실제로 900 으로 두었더니 1000px 창에서 두 카드가 나란히 서고
    #   가로 스크롤 58px 이 났다(경로 입력란 하한 240 + 라벨 + [폴더 선택…] × 2).
    #   `minimumSizeHint()` 는 카드 패딩·라벨·버튼을 모두 포함한 진짜 하한이다.
    # 101단계 슬라이더를 1200px 에 펼치면 단계당 12px — 넓을수록 정밀해지지 않는다.
    _SLIDER_MAX_W = 320
    _SLIDER_MIN_W = 200      # 101단계를 74px 에 펼치면 조절이 불가능하다(실측)
    # 폴더 이름이 읽히는 하한.  ★ 이건 **가독성 하한**이지 배치 상수가 아니다 —
    #   칸이 넓으면 입력란이 알아서 늘어난다(grid 의 열 stretch).  그런데 이 값이
    #   기준/검증 카드의 `minimumSizeHint` 를 통해 페이지 전체의 최소 폭을 결정해서,
    #   240 으로 두면 **1024px 창에서 6px 가 넘쳤다**(동봉 폰트 NanumSquare 로 바꾼 뒤
    #   라벨·[폴더 선택…] 버튼이 조금씩 넓어진 결과).  하한을 낮춰 여유를 준다 —
    #   실측: 224 일 때 1024px 에서 26px 여유(회귀 가드
    #   `test_setting_cards_layout.py::test_no_horizontal_scroll_at_any_width`).
    _PATH_MIN_W = 224

    def _build_device_row(self) -> QWidget:
        """기준/검증 장비 폴더·호기 — 넓으면 2열, 좁으면 세로로 쌓는다."""
        host = QWidget(self)
        self._device_grid = QGridLayout(host)
        self._device_grid.setContentsMargins(0, 0, 0, 0)
        self._device_grid.setSpacing(20)
        self.ref_group, self.ref_path_edit, self.ref_machine_edit = \
            self._make_machine_group(i18n.KO.SETUP_REF_GROUP)
        self.val_group, self.val_path_edit, self.val_machine_edit = \
            self._make_machine_group(i18n.KO.SETUP_VAL_GROUP)
        self._device_cards = [self.ref_group, self.val_group]
        self._device_host = host
        # ★ 자기 폭이 정해진 뒤 다시 흘려야 한다 — B안에서는 이 행이 좁은 그리드 칸
        #   안에 들어가는데, 페이지 폭(1512)을 보고 나란히 두면 입력란이 104px 로
        #   짜부라진다(실측).  호스트의 resize 를 직접 듣는다.
        host.installEventFilter(self)
        self._reflow_device_row()
        return host

    def eventFilter(self, obj, event):  # noqa: N802
        from PyQt6.QtCore import QEvent
        if (event.type() == QEvent.Type.Resize
                and obj is getattr(self, "_device_host", None)):
            self._reflow_device_row()
        return super().eventFilter(obj, event)

    def _reflow_device_row(self) -> None:
        grid = getattr(self, "_device_grid", None)
        if grid is None:
            return
        # ★ **자기 호스트의 폭**을 쓴다 — 페이지 폭을 쓰면 좁은 그리드 칸(B안) 안에서도
        #   1512px 인 줄 알고 두 카드를 나란히 둬 입력란이 104px 이 된다.
        host = getattr(self, "_device_host", None)
        avail = (host.width() if host is not None else 0) or 0
        if avail <= 1:
            avail = self.width() or 0
        if avail <= 1:
            p = self.parentWidget()
            avail = p.width() if p is not None else 0
        # 한 열로 접을지 두 열로 둘지 — 카드 자신의 하한을 넘긴다(reflow 가 열 수 계산).
        need = max((c.minimumSizeHint().width() for c in self._device_cards),
                   default=self._PATH_MIN_W)
        reflow_into_grid(grid, self._device_cards, avail, need)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._reflow_device_row()
        # 설정 카드 배치도 폭을 따라간다 — 임계치를 넘나들 때만 실제로 바뀐다
        # (`_apply_card_orientation` 이 같은 방향이면 즉시 반환).
        if getattr(self, "_cards_row", None) is not None:
            self._apply_card_orientation(self._side_by_side_fits())

    def _build_run_options_card(self) -> QWidget:
        """실행 옵션 — '자동화 수준' 과 '진행 범위' 를 **한 카드**에 작은 세그먼트로.

        ★ 전에는 각각 전체폭 카드 한 장 + 694×58 타일 두 장이었다.  둘 다 값이 **둘뿐인**
        설정이고 매번 바꾸는 값도 아닌데, 화면의 두 덩어리를 차지해 '폴더를 고르고 시작
        한다'는 이 화면의 본론을 아래로 밀어냈다.  타일 크기는 고르는 값의 수가 아니라
        **그 선택의 무게**를 따라야 한다 — 그래서 34px 세그먼트로 강등하고 카드를 합쳤다
        (카드 2장 → 1장, 세로 ~200px 절약).

        각 줄은 `라벨 — 세그먼트 — (남는 폭)` 이다.  라벨을 왼쪽에 고정해 두 줄의 세그먼트
        왼쪽 변이 한 축에 정렬된다.
        """
        _prefs_now = _prefs.load()
        self._selected_slots: Optional[set] = None
        card = NeonCard(role="card", parent=self)
        col = card.body()

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title = QLabel(i18n.KO.RUN_OPTIONS_TITLE, card)
        title.setProperty("role", "cardTitle")
        # 도움말은 카드 하나에 하나로 — 두 카드에 각자 '?' 를 두면 같은 어포던스가 둘이 된다.
        self._auto_help_btn = QToolButton(card)
        self._auto_help_btn.setText("?")
        self._auto_help_btn.setObjectName("helpToggle")
        self._auto_help_btn.setCheckable(True)
        self._auto_help_btn.setToolTip(i18n.KO.HELP_TOGGLE_TOOLTIP)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self._auto_help_btn)
        col.addLayout(title_row)

        # ── 자동화 수준 — 키가 곧 AutomationLevel 값이라 분기 없이 읽는다.
        _last_auto = getattr(_prefs_now, "automation_level",
                             AutomationLevel.USER_SELECT)
        self.auto_group = OptionGroup(
            [(AutomationLevel.USER_SELECT, i18n.KO.AUTOMATION_USER_SELECT_SHORT),
             (AutomationLevel.AUTO_ALL, i18n.KO.AUTOMATION_AUTO_ALL_SHORT)],
            current=_last_auto, role="segment",
            min_tile_w=self._SEGMENT_MIN_W, fixed_cols=2,
            # 부수효과 없음(prefs 저장뿐) → 방향키로 바로 고를 수 있다(라디오 감각).
            activate_on_arrow=True, parent=card,
        )
        self.auto_group.set_option_tooltip(AutomationLevel.USER_SELECT,
                                           i18n.KO.AUTOMATION_USER_SELECT)
        self.auto_group.set_option_tooltip(AutomationLevel.AUTO_ALL,
                                           i18n.KO.AUTOMATION_AUTO_ALL)
        self.auto_group.selection_changed.connect(
            lambda key: _prefs.patch(automation_level=key))
        col.addWidget(self._labeled_segment_row(card, i18n.KO.AUTOMATION_TITLE,
                                                self.auto_group))

        # ── 진행 범위 — 상태가 **세그먼트 자신**에 산다(옆 라벨에 두지 않는다).
        #    ★ activate_on_arrow 를 켜지 않는다(기본 False) — 'subset' 선택은 슬롯 선택
        #      **모달**을 띄운다.  방향키로 훑는 것만으로 창이 떠선 안 된다.
        self.scope_group = OptionGroup(
            [("all", i18n.KO.SCOPE_ALL), ("subset", i18n.KO.SCOPE_SUBSET)],
            current="all", role="segment",
            min_tile_w=self._SEGMENT_MIN_W, fixed_cols=2, parent=card,
        )
        self.scope_group.set_option_tooltip("subset",
                                            i18n.KO.SLOT_SELECT_BTN_TOOLTIP)
        self.scope_group.selection_changed.connect(self._on_scope_changed)
        col.addWidget(self._labeled_segment_row(card, i18n.KO.SCOPE_TITLE,
                                                self.scope_group))

        # 도움말 본문 — 두 설정을 한 번에 설명한다(기본 접힘).
        self._auto_hint = QLabel(i18n.KO.RUN_OPTIONS_HINT, card)
        self._auto_hint.setProperty("role", "muted")
        self._auto_hint.setWordWrap(True)
        self._auto_hint.setStyleSheet("padding-top: 4px;")
        self._auto_hint.setVisible(False)
        col.addWidget(self._auto_hint)
        self._auto_help_btn.toggled.connect(self._auto_hint.setVisible)
        return card

    # 세그먼트 하나의 **하한** 폭.  실제 폭은 그룹 안 가장 넓은 라벨에 맞춰 균등해진다
    # (OptionGroup._equalize_segments).  하한을 두는 이유는 '모든 슬롯'처럼 짧은 라벨이
    # 손가락에 너무 좁아지지 않게 하는 것.  둘이 나란히 서도 800px 창에서 넘치지 않는다
    # (110×2 + 간격 4 + 라벨 92 + 카드 패딩 40 ≈ 356 < 800 - 마진 80).
    _SEGMENT_MIN_W = 110
    _SEGMENT_LABEL_W = 92        # 두 줄의 세그먼트 왼쪽 변을 한 축에 맞춘다

    def _labeled_segment_row(self, parent: QWidget, label: str,
                             group: OptionGroup) -> QWidget:
        """`라벨 — 세그먼트 — 남는 폭` 한 줄.  두 설정이 같은 격자에 앉게 한다."""
        row = QWidget(parent)
        # 맨 QWidget 은 전역 `QWidget { background: $bg }` 를 물려받아 카드 면 위에 색이
        # 다른 띠로 보인다 — 투명으로 못 박는다(로딩 패널·행 호스트와 같은 함정).
        row.setProperty("role", "rowHost")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lbl = QLabel(label, row)
        lbl.setFixedWidth(self._SEGMENT_LABEL_W)
        lay.addWidget(lbl)
        lay.addWidget(group)
        # ★ 남는 폭을 흡수하는 stretch — 없으면 세그먼트가 카드 폭만큼 늘어나 '작게'라는
        #   의도가 무너진다(그리드가 열마다 stretch 1 을 주기 때문).
        lay.addStretch(1)
        return row

    def _on_scope_changed(self, key: str) -> None:
        if key == "subset":
            self._open_slot_select()          # 취소/전체선택이면 내부에서 'all' 로 복귀
        else:
            self._reset_slot_selection()

    def _build_engine_card(self) -> QWidget:
        """매칭 설정 — 허용 오차(좌표) + 구형(유사도) 모드."""
        _prefs_now = _prefs.load()
        engine_card = NeonCard(role="card-soft", parent=self)
        eng_title = QLabel(i18n.KO.ENGINE_CARD_TITLE, engine_card)
        eng_title.setProperty("role", "cardTitle")
        engine_card.body().addWidget(eng_title)

        # 좌표 매칭 허용 오차 — **항상 이 자리에 있다.**  구형 모드일 땐 비활성으로만
        # 보인다(숨기면 아래 구형 스위치가 위로 튄다 — `_sync_engine_controls` 참조).
        self._tol_row = QWidget(engine_card)
        # 맨 QWidget 은 전역 `QWidget { background: $bg }` 를 물려받아 카드 면($panel)
        # 위에 색이 다른 띠로 보인다 — 투명으로 못 박는다(로딩 패널과 같은 함정).
        self._tol_row.setProperty("role", "rowHost")
        _tol_layout = QHBoxLayout(self._tol_row)
        _tol_layout.setContentsMargins(0, 0, 0, 0)
        _tol_layout.setSpacing(6)
        _tol_label = QLabel(i18n.KO.COORD_TOLERANCE_LABEL, self._tol_row)
        _tol_label.setToolTip(i18n.KO.COORD_TOLERANCE_TOOLTIP)
        self.coord_tol_spin = NoWheelDoubleSpinBox(self._tol_row)
        self.coord_tol_spin.setRange(10.0, 5000.0)
        self.coord_tol_spin.setSingleStep(50.0)
        # 배지와 표기를 일치시킨다 — 같은 값을 200 / 200.0 두 가지로 쓰지 않는다.
        self.coord_tol_spin.setDecimals(0)
        self.coord_tol_spin.setSuffix(" µm")
        self.coord_tol_spin.setValue(getattr(_prefs_now, "coord_tolerance",
                                     config.DEFAULT_COORD_TOLERANCE))
        self.coord_tol_spin.setToolTip(i18n.KO.COORD_TOLERANCE_TOOLTIP)
        # 수치는 모노 — '도면' 컨셉의 핵심인데 이 화면엔 모노가 한 글자도 없었다.
        self.coord_tol_spin.valueChanged.connect(
            lambda _v: (self._refresh_mode_badge(), self._schedule_validate()))
        self.coord_tol_spin.setProperty("role", "mono")
        self.coord_tol_spin.setAlignment(Qt.AlignmentFlag.AlignRight
                                        | Qt.AlignmentFlag.AlignVCenter)
        # ★ Qt 기본 up/down 버튼을 쓰지 않는다.  스타일시트로는 삼각형 화살표를 그릴 수
        #   없어(이미지 리소스 필요) 납작한 막대로 잘려 렌더되고, 폭도 ~10px 이라
        #   WCAG 2.5.8(24px)에 한참 못 미쳤다(4가지 방식 실측 비교 후 결론).
        #   대신 진짜 QPushButton −/+ 를 옆에 둔다 — 경계·포커스 링·타깃 규칙을 전부
        #   공유하고 새 리소스가 필요 없다.
        self.coord_tol_spin.setButtonSymbols(
            QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.coord_tol_spin.setMinimumWidth(120)
        self.tol_minus_btn = self._stepper_button("−", -1)
        self.tol_plus_btn = self._stepper_button("+", +1)
        _tol_layout.addWidget(_tol_label)
        _tol_layout.addWidget(self.coord_tol_spin)
        _tol_layout.addWidget(self.tol_minus_btn)
        _tol_layout.addWidget(self.tol_plus_btn)
        _tol_layout.addStretch()
        engine_card.body().addWidget(self._tol_row)

        # 기준 폴더에서 읽어낸 die 크기 — 허용 오차를 자재에 맞게 정하도록 돕는다.
        # ★ 값을 대신 바꾸지 않는다.  조용히 값을 바꾸면 기존 결과가 달라진다 — 알리기만 한다.
        # ★ `_start_hint`·`_errLabel` 과 같은 관습: 자리를 예약해 두고 **문자열만** 바꾼다
        #   (setVisible 을 쓰면 아래 위젯이 위아래로 튄다).
        self._die_hint = QLabel("", engine_card)
        self._die_hint.setProperty("role", "muted")
        self._die_hint.setWordWrap(True)
        engine_card.body().addWidget(self._die_hint)

        # ── 구형(유사도) 모드 — 명시적 스위치 ─────────────────────────────
        # ★ 접이식 섹션을 쓰지 않는다.  예전에는 '섹션이 펼쳐졌는가'가 곧 엔진 모드였고,
        #   설명을 읽으려고 펼치기만 해도 좌표 → 유사도로 조용히 바뀌었다.  읽을 대상
        #   자체를 없애 같은 실수가 재발할 수 없게 한다.
        _legacy_on, _legacy_sub = self._resolved_legacy_state(_prefs_now)

        self.legacy_switch = SwitchRow(
            i18n.KO.LEGACY_SWITCH_TITLE,
            description=i18n.KO.LEGACY_SWITCH_DESC,
            checked=_legacy_on,
            parent=engine_card,
        )
        # 언제 이 모드를 쓰는지는 툴팁으로 — 화면을 조용하게 유지한다.
        self.legacy_switch.setToolTip(i18n.KO.LEGACY_MODE_HINT)
        self.legacy_switch.toggled.connect(self._on_legacy_toggled)
        engine_card.body().addWidget(self.legacy_switch)

        # 구형 하위 선택 — 스위치가 켜졌을 때만 활성.
        self.legacy_group = OptionGroup(
            [(EngineMode.BASIC, i18n.KO.ENGINE_MODE_BASIC),
             (EngineMode.EFFICIENCY, i18n.KO.ENGINE_MODE_EFFICIENCY)],
            current=_legacy_sub,
            # 부수효과 없음 → 방향키 즉시 커밋 허용.
            activate_on_arrow=True, parent=engine_card,
        )
        self.legacy_group.selection_changed.connect(self._on_legacy_sub_changed)
        engine_card.body().addWidget(self.legacy_group)

        # 임계치 슬라이더 (구형 모드 전용 파라미터)
        self._threshold_row = QWidget(engine_card)
        self._threshold_row.setProperty("role", "rowHost")
        sl_row = QHBoxLayout(self._threshold_row)
        sl_row.setContentsMargins(0, 0, 0, 0)
        sl_row.addWidget(QLabel(i18n.KO.SETUP_THRESHOLD_LABEL, self._threshold_row))
        self.slider = NoWheelSlider(Qt.Orientation.Horizontal, self._threshold_row)
        self.slider.setRange(0, 100)
        self.slider.setValue(int(round(_prefs_now.threshold * 100)))
        # 101단계를 1219px 에 펼치면 단계당 12px 이라 정밀 조절이 오히려 어렵다.
        self.slider.setMaximumWidth(self._SLIDER_MAX_W)
        self.slider.setMinimumWidth(self._SLIDER_MIN_W)
        self.threshold_label = QLabel(f"{self.slider.value()} %", self._threshold_row)
        # 값은 모노 + 우측 정렬 — 자릿수가 바뀌어도 좌우로 춤추지 않는다.
        self.threshold_label.setProperty("role", "mono")
        self.threshold_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
        self.threshold_label.setFixedWidth(56)
        self.slider.valueChanged.connect(self._on_threshold_changed)
        sl_row.addWidget(self.slider, stretch=1)
        sl_row.addWidget(self.threshold_label)
        sl_row.addStretch(1)          # 상한 폭을 넘는 여백은 오른쪽으로
        engine_card.body().addWidget(self._threshold_row)

        # 지금 어느 파라미터가 유효한지 문장으로 — 비활성 컨트롤의 이유를 말해준다.
        self._engine_inert_hint = QLabel("", engine_card)
        self._engine_inert_hint.setProperty("role", "muted")
        self._engine_inert_hint.setWordWrap(True)
        engine_card.body().addWidget(self._engine_inert_hint)

        self._sync_engine_controls()
        return engine_card

    def _stepper_button(self, glyph: str, step: int) -> NeonButton:
        """허용 오차 ±  — 정사각 버튼.  Qt 스핀 버튼 대신 쓴다(위 주석 참조)."""
        btn = NeonButton(glyph, role="stepper")
        side = theme.PROFILE.input_h
        btn.setFixedWidth(side)     # 높이는 QSS 가 input_h 로 고정한다(중심 정렬)
        btn.setAccessibleName(i18n.KO.TOL_STEP_UP if step > 0
                              else i18n.KO.TOL_STEP_DOWN)
        btn.setToolTip(btn.accessibleName())
        btn.clicked.connect(lambda: self.coord_tol_spin.stepBy(step))
        return btn

    # ------------------------------------------------------------------
    @staticmethod
    def _resolved_legacy_state(p) -> tuple[bool, str]:
        """prefs → (구형 on?, 하위 엔진).  실제 prefs 로 생성되므로 방어적으로."""
        try:
            return _prefs.resolve_legacy_state(p)
        except Exception:
            return (False, EngineMode.BASIC)

    def _current_engine_mode(self) -> str:
        """엔진 모드는 **명시 컨트롤 상태**에서만 유도한다.

        ★ 접이식 펼침 상태(is_expanded 등)를 절대 읽지 않는다 — 설명을 읽는 행위가
        엔진을 바꾸던 버그의 재발 방지."""
        if not self.legacy_switch.is_on():
            return EngineMode.COORDINATE
        sub = self.legacy_group.current_key()
        return sub if sub in (EngineMode.BASIC, EngineMode.EFFICIENCY) \
            else EngineMode.BASIC

    def _sync_engine_controls(self) -> None:
        """무효한 파라미터를 정리한다 — **다만 토글은 절대 움직이지 않게.**

        | 구형 스위치 | 허용 오차(µm) | 구형 하위 선택 · 유사도 임계치 |
        |---|---|---|
        | OFF (좌표 매칭) | 보임 · 사용 가능 | **숨김** |
        | ON (구형)       | 보임 · **비활성(회색)** | 보임 |

        ★ 숨기기와 비활성을 **위치로 나눈다.**  이 카드의 위젯 순서는
        `제목 → 허용 오차 → [구형 스위치] → 구형 하위선택 → 임계치` 다.

        - 스위치 **아래**에 있는 것(하위 선택·임계치)은 숨겨도 스위치가 안 움직인다
          → 안 쓸 때 숨긴다(사용자 결정 유지).
        - 스위치 **위**에 있는 허용 오차를 숨기면 스위치가 그 높이+간격만큼 위로
          튄다 — 사용자가 "구형 모드 켜면 박스 안 객체들이 움직이면서 토글 위치가
          변한다" 고 신고한 그 현상이다.  그래서 **자리는 지키고 비활성**으로만 보인다.

        비활성 회색은 이미 있는 QSS 가 칠한다(`QDoubleSpinBox:disabled` ·
        `QLabel:disabled` · `QPushButton[role="stepper"]:disabled`) — 새 스타일 불필요.
        `setEnabled(False)` 는 자식에 전파되므로 행 전체가 한 번에 회색이 된다.

        지금 어느 엔진이 도는지는 `_engine_inert_hint` 한 줄과 상단 모드 배지가 말한다."""
        legacy_on = self.legacy_switch.is_on()
        self.legacy_group.setVisible(legacy_on)
        self._threshold_row.setVisible(legacy_on)
        self._tol_row.setVisible(True)
        self._tol_row.setEnabled(not legacy_on)
        if legacy_on:
            short = (i18n.KO.ENGINE_MODE_EFFICIENCY_SHORT
                     if self.legacy_group.current_key() == EngineMode.EFFICIENCY
                     else i18n.KO.ENGINE_MODE_BASIC_SHORT)
            text = i18n.KO.ENGINE_ACTIVE_LEGACY_FMT.format(sub=short)
        else:
            text = i18n.KO.ENGINE_ACTIVE_COORD
        self._engine_inert_hint.setText(text)
        self._refresh_mode_badge()

    def _on_legacy_toggled(self, on: bool) -> None:
        self._sync_engine_controls()
        _prefs.patch(legacy_enabled=bool(on),
                     engine_mode=self._current_engine_mode())

    def _on_legacy_sub_changed(self, key: str) -> None:
        self._sync_engine_controls()
        _prefs.patch(legacy_engine=key, engine_mode=self._current_engine_mode())

    def _build_action_bar(self) -> QWidget:
        """시작 / 업데이트 확인 (+ 개발자 모드 버튼)."""
        host = QWidget(self)
        bar = QHBoxLayout(host)
        bar.setContentsMargins(0, 0, 0, 0)
        # 업데이트 확인은 좌측(보조), 검증 시작은 우측(주). 좌상단 도움말 메뉴 대체.
        self.update_btn = NeonButton(i18n.KO.MENU_CHECK_UPDATE, role="ghost")
        self.update_btn.setMinimumHeight(46)
        self.update_btn.clicked.connect(self.update_check_requested.emit)
        bar.addWidget(self.update_btn)
        # 사진 한 장의 결함 정보를 바로 보는 도구.
        self.image_info_btn = NeonButton(i18n.KO.IMAGE_INFO_BUTTON, role="ghost")
        self.image_info_btn.setMinimumHeight(46)
        self.image_info_btn.clicked.connect(self._open_image_info)
        bar.addWidget(self.image_info_btn)
        # ★ 자리 계약: 왼쪽 보조 버튼들 → stretch → 힌트 → 주 액션(start_btn).
        #   새 위젯은 반드시 stretch **뒤**나 그 앞의 보조 묶음에 붙인다 — 잘못 넣으면
        #   주 액션이 가운데로 밀린다(test_action_bar_index_contract).
        self._action_bar = bar
        bar.addStretch(1)
        # 왜 시작할 수 없는지 — 툴팁이 아니라 버튼 옆에 보이게.
        self._start_hint = QLabel("", host)
        self._start_hint.setProperty("role", "muted")
        bar.addWidget(self._start_hint)
        self.start_btn = NeonButton(i18n.KO.BTN_START, role="primary")
        self.start_btn.setMinimumWidth(220)
        self.start_btn.setMinimumHeight(46)   # 유일한 primary — 가장 크게
        self.start_btn.clicked.connect(self._on_start)
        bar.addWidget(self.start_btn)
        self._validate()                      # 초기 상태(폴더 미지정) 반영
        return host

    # ------------------------------------------------------------------
    def _make_machine_group(self, title: str) -> tuple[QWidget, QLineEdit, QLineEdit]:
        """기준/검증 장비 입력 카드.

        QGroupBox(라디우스 8px + margin-top) 대신 카드 + 그리드로 — 시트의 형태 언어와
        일치하고, 두 카드의 필드가 같은 열에 정렬된다."""
        card = NeonCard(role="card", parent=self)
        head = QLabel(title, card)
        head.setProperty("role", "cardTitle")
        card.body().addWidget(head)

        grid = QGridLayout()
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)          # 입력란이 늘어난다

        path_edit = QLineEdit(card)
        # 폴더 경로는 읽혀야 의미가 있다 — 컨테이너 계산이 어긋나도 이 하한은 남는다
        # (B안 그리드 칸에서 104px 까지 짜부라진 적이 있다).
        path_edit.setMinimumWidth(self._PATH_MIN_W)
        path_edit.setPlaceholderText(i18n.KO.SETUP_FOLDER_PLACEHOLDER)
        path_edit.setReadOnly(False)
        browse = NeonButton(i18n.KO.BTN_BROWSE, role="ghost")
        browse.clicked.connect(lambda: self._browse(path_edit))
        grid.addWidget(QLabel(i18n.KO.SETUP_FOLDER_LABEL, card), 0, 0)
        grid.addWidget(path_edit, 0, 1)
        grid.addWidget(browse, 0, 2)

        # 이유를 말하는 인라인 오류 줄.
        # ★ show/hide 하면 카드 높이가 30px, 옆 카드 정렬이 23px 흔들린다(실측 지적) —
        #   **자리를 예약**하고 문자열만 비운다(항상 표시, 높이 고정).
        err = QLabel("", card)
        err.setProperty("role", "error")
        err.setMinimumHeight(16)
        err.setWordWrap(True)
        grid.addWidget(err, 1, 1, 1, 2)

        machine_edit = QLineEdit(card)
        machine_edit.setPlaceholderText(i18n.KO.SETUP_MACHINE_PLACEHOLDER)
        grid.addWidget(QLabel(i18n.KO.SETUP_MACHINE_LABEL, card), 2, 0)
        grid.addWidget(machine_edit, 2, 1, 1, 2)

        card.body().addLayout(grid)
        # 유효성 표시를 위해 오류 라벨을 입력란에 붙여 둔다.
        path_edit.setProperty("_errLabel", err)
        path_edit.textChanged.connect(self._schedule_validate)
        return card, path_edit, machine_edit

    # ── 입력 유효성 — 비활성 버튼이 이유를 말하게 ─────────────────────────────
    def _schedule_validate(self) -> None:
        """타이핑마다 stat 하지 않도록 디바운스(죽은 네트워크 드라이브 대비)."""
        timer = getattr(self, "_validate_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._validate)
            self._validate_timer = timer
        timer.start(250)

    @staticmethod
    def _dir_state(text: str) -> str:
        """'' | 'empty' | 'missing' — 경로 문자열의 유효성."""
        t = (text or "").strip()
        if not t:
            return "empty"
        try:
            return "" if Path(t).is_dir() else "missing"
        except OSError:
            return "missing"                 # 접근 불가한 경로도 '없음' 취급

    def _set_field_state(self, edit: QLineEdit, state: str) -> None:
        """동적 프로퍼티 + repolish(안 하면 QSS 가 다시 그려지지 않는다)."""
        edit.setProperty("state", "invalid" if state else "")
        edit.style().unpolish(edit)
        edit.style().polish(edit)
        err = edit.property("_errLabel")
        if err is not None:
            # ★ 'empty' 분기는 두지 않는다.  `_validate()` 가 빈 필드에는 state 를 주지
            #   않기 때문에(빈 상태에서 빨간 테두리로 겁주지 않으려는 의도) 도달할 수
            #   없고, 같은 말은 이미 버튼 옆 `_start_hint`(START_BLOCKED_HINT)가 한다.
            #   도달 불가 분기는 '처리되고 있다'는 착각만 남긴다.
            if state == "missing":
                err.setText(i18n.KO.SETUP_INVALID_FOLDER)
            else:
                err.setText("")
            # ★ setVisible 을 쓰지 않는다 — 자리를 예약해 뒀으므로 문자열만 바꾼다.

    def set_backend_ready(self, ready: bool) -> None:
        """무거운 구성 요소가 준비됐는지 알린다 — 창을 먼저 띄우는 시작 흐름용.

        준비 전에는 [검증 시작] 을 잠그고 버튼에 '준비 중' 을 표기한다.  활성 여부는
        여전히 :meth:`_validate` 하나가 정한다 — 여기서 직접 ``setEnabled`` 를 부르면
        다음 폴더 입력의 디바운스가 그것을 곧바로 덮어쓴다."""
        self._backend_ready = bool(ready)
        self.start_btn.setText(
            i18n.KO.BTN_START if self._backend_ready
            else i18n.KO.BTN_START_PREPARING)
        self._validate()

    def _probe_state(self, text: str) -> Optional[str]:
        """파일시스템에 **손대지 않고** 알 수 있는 것만 — 모르면 ``None``(확인 필요).

        빈 칸은 디스크를 볼 필요가 없으므로 즉시 답한다.  나머지는 워커가 답을
        채워 둔 것만 쓴다 (P-15)."""
        t = (text or "").strip()
        if not t:
            return "empty"
        return self._probed.get(t)

    def _start_dir_probe(self, ref_text: str, val_text: str) -> None:
        """폴더 존재 확인을 워커에 맡긴다 — 캐시가 있어도 **매번 다시 확인**한다.

        캐시는 '멈추지 않기' 위한 것이지 '다시 안 보기' 위한 것이 아니다.  그래서
        사용자가 그 사이에 폴더를 만들었다면 다음 확인에서 저절로 풀린다."""
        self._probe_token += 1
        probe = _DirProbe(self._probe_token, ref_text, val_text)
        probe.signals.done.connect(self._on_dir_probe_done)
        _LIVE_DIR_PROBES.add(probe)
        probe.finished.connect(lambda p=probe: _LIVE_DIR_PROBES.discard(p))
        probe.start()

    def _on_dir_probe_done(self, token: int, ref_text: str, ref_state: str,
                           val_text: str, val_state: str) -> None:
        """확인이 끝났다 — **최신 세대만** 반영하고 판정을 다시 그린다."""
        if token != self._probe_token:
            return
        changed = (self._probed.get(ref_text) != ref_state
                   or self._probed.get(val_text) != val_state)
        self._probed[ref_text] = ref_state
        self._probed[val_text] = val_state
        if changed:
            self._validate()          # 새 답으로 화면을 다시 그린다(재귀는 1회).

    def _validate(self) -> bool:
        """두 폴더가 모두 유효하고 **구성 요소가 준비됐을 때만** [검증 시작] 활성화.

        폴더 확인 자체는 워커가 한다 (P-15) — 아직 답이 없으면 '확인 중' 으로 두고
        시작을 잠근다.  헤드리스에서는 동기로 확인해 호출 즉시 판정이 나오게 한다
        (테스트가 `_validate()` 의 반환값을 그 자리에서 단언한다)."""
        ref_text = self.ref_path_edit.text()
        val_text = self.val_path_edit.text()
        if self._sync_probe:
            ref_state: Optional[str] = self._dir_state(ref_text)
            val_state: Optional[str] = self._dir_state(val_text)
        else:
            self._start_dir_probe(ref_text, val_text)
            ref_state = self._probe_state(ref_text)
            val_state = self._probe_state(val_text)
        checking = ref_state is None or val_state is None
        # 비어 있는 초기 상태에서 빨간 테두리로 겁주지 않는다 — 문구만 조용히.
        # 확인 중(None)도 마찬가지다 — 아직 모르는 것을 틀렸다고 칠할 수는 없다.
        self._set_field_state(self.ref_path_edit,
                              "missing" if ref_state == "missing" else "")
        self._set_field_state(self.val_path_edit,
                              "missing" if val_state == "missing" else "")
        dirs_ok = ref_state == "" and val_state == ""
        ok = dirs_ok and self._backend_ready
        self.start_btn.setEnabled(ok)
        if hasattr(self, "_start_hint"):
            # 폴더 문제가 먼저다 — 그건 사용자가 지금 고칠 수 있는 것이고,
            # '준비 중' 은 가만히 있으면 저절로 풀린다.
            if checking:
                self._start_hint.setText(i18n.KO.START_CHECKING_HINT)
            elif not dirs_ok:
                self._start_hint.setText(i18n.KO.START_BLOCKED_HINT)
            elif not self._backend_ready:
                self._start_hint.setText(i18n.KO.START_PREPARING_HINT)
            else:
                self._start_hint.setText("")
        # die 안내는 **폴더가 확실히 있을 때만** 시작한다 — 확인 중(None)에 넘기면
        # 있는지도 모르는 경로를 훑는다.
        self._refresh_die_hint(ref_text if ref_state == "" else None)
        return ok

    def _refresh_die_hint(self, ref_text: Optional[str]) -> None:
        """기준 폴더의 die 크기 안내를 갱신한다 — **스캔은 백그라운드에서** 한다.

        ★ 여기서 직접 훑지 않는다.  `_detect_die_geometry` 는 슬롯을 하나씩 열어
        INI 를 파싱하므로 자재에 따라 초 단위고(실측: 슬롯 25 × 결함 3,000 → 1.4초),
        UI 스레드에서 돌리면 **폴더를 고르는 순간 창이 그만큼 멈춘다**.  이게 실제
        사용자 신고였다.  계산량은 줄이지 않는다 — pitch 검산은 정확도 가드라
        완화하면 격자가 다른 자재에 상수가 채택된다(CLAUDE.md).  자리를 옮길 뿐이다.

        같은 경로로 다시 불리면 스캔하지 않고 캐시한 결과로 문구만 다시 그린다 —
        허용 오차를 건드릴 때마다(`coord_tol_spin.valueChanged` → `_schedule_validate`)
        폴더를 다시 훑던 낭비가 사라진다.  전 구간 fail-safe."""
        hint = getattr(self, "_die_hint", None)
        if hint is None:
            return
        if not ref_text:
            # 경로가 비었/잘못됐다 — 진행 중인 스캔 결과를 버리고 조용히 비운다.
            self._die_token += 1
            self._die_scanned_for = None
            self._die_scanning_for = None
            self._die_result = (None, "", False)
            self._apply_die_hint(None, "", False)
            return
        if ref_text == self._die_scanned_for:
            self._apply_die_hint(*self._die_result)     # 캐시 — 스캔하지 않는다
            return
        if ref_text == self._die_scanning_for:
            # ★ 같은 경로를 이미 훑는 중이다 — 결과를 기다린다.  기준 폴더를 고른
            #   직후 검증 폴더를 고르면 `_validate` 가 다시 오는데(두 입력란이 같은
            #   디바운스를 공유한다), 그때마다 워커를 새로 띄우면 같은 폴더를 두 번
            #   훑는다.  느린 NAS 에서 부하가 그대로 두 배가 된다.
            return
        self._die_token += 1
        self._die_scanning_for = ref_text
        self._set_die_hint_text(i18n.KO.DIE_SIZE_CHECKING, "muted")
        scan = _DieGeometryScan(self._die_token, ref_text)
        scan.signals.done.connect(self._on_die_scan_done)
        _LIVE_DIE_SCANS.add(scan)
        scan.finished.connect(lambda s=scan: _LIVE_DIE_SCANS.discard(s))
        self._die_scan = scan
        scan.start()

    def _on_die_scan_done(self, token: int, pitch, src: str, broken: bool) -> None:
        """워커가 끝났다 — **최신 세대의 결과만** 반영한다.

        폴더를 연달아 바꾸면 스캔이 겹치고, 늦게 끝난 옛 스캔이 새 폴더의 안내를
        덮어쓸 수 있다.  세대 번호가 다르면 그 결과는 버린다."""
        if token != self._die_token:
            return
        self._die_scanned_for = self._die_scanning_for
        self._die_scanning_for = None
        self._die_result = (pitch, src, broken)
        self._apply_die_hint(pitch, src, broken)

    def _set_die_hint_text(self, text: str, role: str) -> None:
        """문구 + 동적 프로퍼티 repolish(안 하면 QSS 가 다시 그려지지 않는다)."""
        hint = self._die_hint
        hint.setText(text)
        hint.setProperty("role", role)
        hint.style().unpolish(hint)
        hint.style().polish(hint)

    def _apply_die_hint(self, pitch, src: str, broken: bool) -> None:
        """스캔 결과 → 화면 문구.  순수 렌더링(파일을 읽지 않는다)."""
        if pitch is None:
            # ★ die 크기를 모른다고 곧장 경고하지 않는다.  KLA 슬롯·LIVE 파일명 슬롯은
            #   die 크기 없이도 좌표가 나온다 — 진짜 못 쓰는 경우(`broken`)만 경고한다.
            self._set_die_hint_text(i18n.KO.DIE_SIZE_NOT_FOUND if broken else "",
                                    "warn" if broken else "muted")
            return
        text = i18n.KO.DIE_SIZE_DETECTED_FMT.format(x=pitch[0], y=pitch[1], src=src)
        self._set_die_hint_text(text, "muted")

    @staticmethod
    def _detect_die_geometry(ref_text: str):
        """기준 폴더의 슬롯들을 훑어 ``((pitch_x, pitch_y) | None, 출처, 못쓰는가)``.

        · Camtek INI 슬롯 → `Params_WaferInfo.ini` 등에서 die pitch
        · KLA 슬롯        → `.001` 의 `DiePitch`
        · LIVE 파일명 슬롯 → die pitch 는 없지만 **좌표는 파일명에서 나온다**(경고 대상 아님)

        '못쓰는가' 는 **Camtek INI 항목이 있는데 pitch 를 확정 못 한** 경우만 True 다.
        전 구간 fail-safe — 안내가 실패해도 설정 화면이 막히면 안 된다."""
        from ...coords import kla_info
        from ...coords.wafer_geometry import (camtek_geometry, has_camtek_entries,
                                              kla_geometry)
        from ...models.slot import list_slot_dirs

        try:
            root = Path(ref_text.strip())
            if not root.is_dir():
                return (None, "", False)
            broken = False
            for _name, folder in sorted(list_slot_dirs(root).items()):
                geom = camtek_geometry(folder)
                if geom is not None:
                    return ((geom.pitch_x, geom.pitch_y), geom.source, False)
                if kla_info.load_folder(folder):          # KLA 슬롯 — DiePitch 가 있다
                    kg = kla_geometry(folder)
                    return ((kg.pitch_x, kg.pitch_y), i18n.KO.DIE_SIZE_SRC_KLA, False)
                if has_camtek_entries(folder):
                    broken = True     # 변환할 항목이 있는데 pitch 를 못 정했다 — 진짜 문제
            return (None, "", broken)
        except Exception:
            return (None, "", False)

    def _browse(self, target: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, i18n.KO.SETUP_FOLDER_LABEL)
        if path:
            target.setText(path)
            # 기준 폴더가 바뀌면 이전 슬롯 선택은 더 이상 유효하지 않다.
            if target is self.ref_path_edit:
                self._reset_slot_selection()

    # ------------------------------------------------------------------
    def _reset_slot_selection(self) -> None:
        """전체 진행으로 되돌린다 — 타일 라벨·선택까지 원복."""
        self._selected_slots = None
        self.scope_group.set_option_label("subset", i18n.KO.SCOPE_SUBSET)
        self.scope_group.set_current_key("all")        # emit 없음(재귀 방지)

    def _open_slot_select(self) -> None:
        """'일부 슬롯만 진행' — 기준 폴더의 슬롯을 스캔해 부분 선택."""
        from ...models.slot import list_slot_dirs
        from ..widgets.slot_select_dialog import SlotSelectDialog

        ref_text = self.ref_path_edit.text().strip()
        ref_root = Path(ref_text) if ref_text else None
        if ref_root is None or not ref_root.is_dir():
            sheets.warn(
                self, i18n.KO.APP_TITLE, i18n.KO.SLOT_SELECT_NEED_REF,
            )
            self._reset_slot_selection()       # 고를 수 없으면 전체 진행으로 복귀
            return
        slot_names = sorted(list_slot_dirs(ref_root).keys())
        if not slot_names:
            sheets.info(
                self, i18n.KO.APP_TITLE, i18n.KO.SLOT_SELECT_EMPTY,
            )
            self._reset_slot_selection()
            return
        dlg = SlotSelectDialog(
            slot_names, preselected=self._selected_slots, parent=self,
        )
        if sheets.run(dlg) and dlg.accepted_ok:
            chosen = dlg.selected
            # 전체 선택과 동일하면 '전체 진행'(None)으로 정규화.
            if not chosen or chosen == set(slot_names):
                self._reset_slot_selection()
            else:
                self._selected_slots = set(chosen)
                # 상태를 타일 라벨에 — 옆 라벨을 보러 갈 필요가 없다.
                self.scope_group.set_option_label(
                    "subset", i18n.KO.SCOPE_SUBSET_COUNT_FMT.format(
                        n=len(chosen), total=len(slot_names)))
                self.scope_group.set_current_key("subset")
        else:
            self._reset_slot_selection()        # 취소 → 전체 진행 유지

    def _open_image_info(self) -> None:
        """단일 사진 정보 다이얼로그 — 사진 1장의 결함 좌표/measurement 확인."""
        from ..widgets.image_info_dialog import ImageInfoDialog
        dlg = ImageInfoDialog(self)
        sheets.run(dlg, full_bleed=True)

    def _on_threshold_changed(self, v: int) -> None:
        self.threshold_label.setText(f"{v} %")
        self._refresh_mode_badge()
        _prefs.patch(threshold=v / 100.0)

    # ------------------------------------------------------------------
    def _build_view_options(self) -> QWidget:
        """상단 보기 옵션 줄 — '다크 모드' 하나.

        ※ 옆에 '모션 줄이기' 스위치가 있었으나 사용자 결정으로 제거했다(모션은 항상
        켜진다).  화면에 남는 보기 옵션은 색 모드 하나뿐이다."""
        host = QWidget(self)
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        row.addStretch(1)

        self._dark_switch = SwitchRow(
            i18n.KO.DARK_MODE_LABEL, parent=host,
            checked=theme.is_dark_mode(),
        )
        self._dark_switch.setToolTip(i18n.KO.DARK_MODE_TOOLTIP)
        self._dark_switch.toggled.connect(self._on_dark_mode_toggled)
        row.addWidget(self._dark_switch)
        return host

    def _on_dark_mode_toggled(self, on: bool) -> None:
        """다크 모드 전환 요청 — 실제 적용(페이지 재생성)은 main_window 가 한다.

        ★ **지연 0** — 누르는 즉시 색 전환이 시작된다(사용자 지정).
        전에는 손잡이 이동(DUR_SWITCH=160ms)이 끝난 **뒤에** 무거운 일을 시작했다.
        메인 스레드가 그 일 동안 멈추므로(실측 223ms: apply_to_app 99 + 페이지
        재생성 124) 손잡이 애니메이션이 얼어붙는 것을 피하려던 것이었는데, 결과적으로
        **누르고 색이 움직이기까지 160+223 = 383ms** 가 걸렸다 — 그게 '선딜레이' 다.
        이제 160ms 를 없애 ~223ms 로 줄인다.  남은 것은 계산 시간이라 타이머로는
        더 줄일 수 없다(줄이려면 위젯이 색을 f-string 으로 굽는 구조를 바꿔야 한다).

        ★ 타이머 자체는 **남긴다.**  두 가지를 계속 해 준다:
        (a) 연타를 **한 번으로 합친다**(prefs 는 마지막 값, emit 은 한 번),
        (b) 클릭 핸들러가 **먼저 반환**되게 해 눌린 상태가 화면에 반영된다.
        ★ 정적 `QTimer.singleShot` 금지 — 부모 있는 타이머여야 페이지가 파괴될 때 함께
        죽는다(죽은 위젯으로 콜백이 들어가면 세그폴트다, 전례 있음).
        """
        key = "dark" if on else "light"
        self._pending_color_mode = key
        _prefs.patch(color_mode=key)       # 재생성된 페이지가 prefs 에서 상태를 복원한다
        self._appearance_timer.start(0)    # 지연 없음 — 이벤트 루프 한 바퀴만

    def _emit_appearance_changed(self) -> None:
        """손잡이 이동이 끝났다 — 이제 색을 갈아 끼운다(연타는 여기서 한 번으로 합쳐진다)."""
        key = self._pending_color_mode or theme.COLOR_MODE
        self._pending_color_mode = None
        if key == theme.COLOR_MODE:
            return                         # 연타로 제자리 → 재생성할 이유가 없다
        # ★ 색 모드를 **인자로 실어 보낸다** — 받는 쪽이 prefs 를 다시 읽지 않게(디스크
        #   왕복 2회 → 1회.  회사 환경에서 prefs 가 네트워크 홈에 있으면 이 한 번이 크다).
        self.appearance_changed.emit(key)

    # ------------------------------------------------------------------
    def _on_start(self) -> None:
        inp = self._collect_input()
        if inp is None:
            return
        self.start_requested.emit(inp)

    def _collect_input(self):
        ref_root = Path(self.ref_path_edit.text().strip())
        val_root = Path(self.val_path_edit.text().strip())
        ref_machine = self.ref_machine_edit.text().strip()
        val_machine = self.val_machine_edit.text().strip()

        if not ref_root.exists() or not ref_root.is_dir():
            sheets.warn(self, i18n.KO.APP_TITLE,
                                i18n.KO.WARN_PATH_NOT_EXIST.format(path=ref_root))
            return
        if not val_root.exists() or not val_root.is_dir():
            sheets.warn(self, i18n.KO.APP_TITLE,
                                i18n.KO.WARN_PATH_NOT_EXIST.format(path=val_root))
            return
        if not ref_machine:
            ref_machine = i18n.KO.DEFAULT_REF_MACHINE
        if not val_machine:
            val_machine = i18n.KO.DEFAULT_VAL_MACHINE

        if ref_root.resolve() == val_root.resolve():
            r = sheets.ask(
                self, i18n.KO.WARN_SAME_PATH_TITLE, i18n.KO.WARN_SAME_PATH_BODY,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if r != QMessageBox.StandardButton.Yes:
                return

        mode = "single"             # 양쪽 교차검증 제거 — 항상 한쪽만 검증.
        threshold = self.slider.value() / 100.0
        # 타일 키가 곧 AutomationLevel 값 — 분기 없이 읽는다.
        automation = self.auto_group.current_key() or AutomationLevel.USER_SELECT
        # 엔진 모드는 명시 스위치에서만 유도한다(펼침 상태를 읽지 않는다).
        engine_mode = self._current_engine_mode()
        coord_tolerance = float(self.coord_tol_spin.value())
        persist_scores = True   # 디스크 점수 캐시 항상 기본 적용(토글 제거).
        accel_concurrency = 32      # 자동 산정 상한(슬라이더 제거) — 워크로드 기반 유동.
        # 효율 모드 = CPU+GPU fusion-zscore 고정.
        use_cpu = True
        use_gpu = True
        embed_batch = 1

        # 마지막 입력 값을 영속화 (#14)
        _prefs.patch(
            threshold=threshold,
            last_ref_root=str(ref_root),
            last_val_root=str(val_root),
            last_ref_machine=ref_machine,
            last_val_machine=val_machine,
            last_mode=mode,
            automation_level=automation,
            engine_mode=engine_mode,
            persist_scores=persist_scores,
            accel_concurrency=accel_concurrency,
            use_cpu=use_cpu,
            use_gpu=use_gpu,
            embed_batch=embed_batch,
            coord_tolerance=coord_tolerance,
        )
        return SetupInput(
            mode=mode,
            ref_root=ref_root,
            val_root=val_root,
            ref_machine=ref_machine,
            val_machine=val_machine,
            threshold=threshold,
            automation_level=automation,
            engine_mode=engine_mode,
            persist_scores=persist_scores,
            accel_concurrency=accel_concurrency,
            use_cpu=use_cpu,
            use_gpu=use_gpu,
            embed_batch=embed_batch,
            coord_tolerance=coord_tolerance,
            selected_slots=(set(self._selected_slots)
                            if self._selected_slots is not None else None),
        )

    # ── 작성 중 입력 이관 (색 모드/배치 전환 시) ─────────────────────────────
    # 색 모드·배치를 바꾸면 페이지를 파괴하고 다시 만든다(구운 색 교체).  '세션 시작
    # 전'은 **아무것도 입력하지 않았다는 뜻이 아니다** — 폴더·호기·진행 범위·손으로 고른
    # 슬롯·허용 오차는 [검증 시작] 전까지 prefs 에 없다.  그대로 파괴하면 조용히 사라지고,
    # 특히 '일부 슬롯 12/40' 이 '모든 슬롯'으로 되돌아가면 40슬롯을 통째로 돌리게 된다.
    # 그래서 재생성 전에 여기서 걷어 두고, 새 페이지에 다시 심는다.
    # ------------------------------------------------------------------
    def apply_state(self, ref_root: str, val_root: str,
                    ref_machine: str, val_machine: str,
                    threshold: float) -> None:
        self.ref_path_edit.setText(ref_root)
        self.val_path_edit.setText(val_root)
        self.ref_machine_edit.setText(ref_machine)
        self.val_machine_edit.setText(val_machine)
        self.slider.setValue(int(threshold * 100))
