"""초기 입력 화면 (Setup) — 모드/폴더/호기/임계치 입력."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFileDialog,
                              QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                              QMessageBox, QScrollArea, QSizePolicy,
                              QToolButton, QVBoxLayout, QWidget)

from ... import config, i18n
from .. import theme
from ...utils import prefs as _prefs
from ...utils.prefs import AutomationLevel, EngineMode
from ..widgets.collapsible_section import CollapsibleSection
from ..widgets.neon_button import NeonButton
from ..widgets.neon_card import NeonCard
from ..widgets.no_wheel_slider import NoWheelDoubleSpinBox, NoWheelSlider
from ..widgets.option_group import OptionGroup, reflow_into_grid
from ..widgets.switch_row import SwitchRow


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
    use_npu: bool = False            # 효율 모드 = CPU+GPU. NPU 비활성(코드만 보존).
    embed_batch: int = 1             # 정적 배치 B (1=끔)
    # 좌표 기반 매칭(v2) 허용 오차 — µm 단위.
    coord_tolerance: float = 500.0
    # 진행할 슬롯 부분집합 (None = 전체 진행). '일부 슬롯만 진행' 옵션으로 설정.
    selected_slots: Optional[set] = None


class SetupPage(QWidget):
    """검증 시작 화면."""

    start_requested = pyqtSignal(object)             # SetupInput
    update_check_requested = pyqtSignal()            # '업데이트 확인' 버튼
    appearance_changed = pyqtSignal()                # 색 모드/배치 변경 → 페이지 재생성 요청

    # 이 페이지가 어떤 배치안인지 — 스위처가 prefs 대신 **자기 자신**을 보고 표시한다
    # (prefs 와 실제 화면이 어긋나는 일이 없게).  배치안 서브클래스가 덮어쓴다.
    LAYOUT_KEY = "a"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
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
        #   내부 스위처 칩의 :checked 배경(채움)을 덮어써 '빈 박스'로 렌더된다. 뷰포트를
        #   objectName 으로 스코프해 자식에 새지 않게 한다(스위처 채움 버그 방지).
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget#qt_scrollarea_viewport { background: transparent; }"
        )
        outer.addWidget(scroll)

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

        # 본문 구성 — 배치안(서브클래스)이 이 메서드만 오버라이드하면 배치가 바뀐다.
        self._build_body(root)

        # 액션바는 스크롤 **밖**에 고정한다(주요 액션이 항상 손에 닿게).
        if self._pinned_action_bar():
            bar = self._build_action_bar()
            # 스크롤 안 본문과 같은 좌우 마진 + 상단 눈금으로 '고정된 바닥'임을 말한다.
            bar.setContentsMargins(theme.PROFILE.page_margin, 12,
                                   theme.PROFILE.page_margin, 16)
            rule = QWidget(self)
            rule.setFixedHeight(1)
            rule.setStyleSheet(f"background: {theme.LINE};")
            outer.addWidget(rule)
            outer.addWidget(bar)

        # ★ 첫 포커스를 첫 입력란에 둔다.  이전엔 탭 체인 첫 정지가 보기 옵션(모션
        #   줄이기)이라, 키보드 사용자가 폴더를 입력하려면 파괴적 컨트롤(색 모드·배치
        #   스위처)을 먼저 지나야 했다.  QTimer 로 미루는 이유는 show() 이후에야
        #   포커스가 실제로 들어가기 때문.
        QTimer.singleShot(0, self._focus_first_field)

        # 개발자 모드 토글 단축키 — 일반 사용자에게는 보이지 않는 진입점.
        self._dev_shortcut = QShortcut(QKeySequence("Ctrl+Shift+D"), self)
        self._dev_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._dev_shortcut.activated.connect(self._toggle_dev_mode)

    def _focus_first_field(self) -> None:
        """탭 체인의 출발점을 첫 입력란으로 — 파괴적 컨트롤을 먼저 지나지 않게."""
        edit = getattr(self, "ref_path_edit", None)
        if edit is not None and edit.isVisible():
            edit.setFocus(Qt.FocusReason.OtherFocusReason)

    # ==================================================================
    # 본문 빌더 — 각 조각은 위젯 하나를 만들어 돌려준다.  배치안은 ``_build_body``
    # (순서·열 구성) 또는 개별 빌더(컨트롤 디자인)만 갈아끼우면 된다.
    # ==================================================================
    def _pinned_action_bar(self) -> bool:
        """액션바를 스크롤 밖에 고정할지.

        ★ 기본이 True 다.  이전엔 A/C 안이 액션바를 스크롤 **안**에 뒀는데, 800×600 에서
        내용이 뷰포트를 넘겨 [검증 시작]이 화면 밖으로 밀려났다(실측).  '주요 액션을
        찾으려면 스크롤해야 한다'는 어느 배치에서도 결함이므로 배치안의 차이로 두지
        않는다 — B 안의 차별점은 그리드 흐름이다."""
        return True

    def _build_body(self, root: QVBoxLayout) -> None:
        """A안 「진행 순서형」 — 한 열, 위에서 아래로(폴더 → 옵션 → 시작).

        ★ 순서가 이 docstring 과 **반대**였다: 자동화 수준 카드가 첫 카드고 폴더가
        두 번째였다.  폴더는 매번 바뀌는 값이고 자동화 수준은 거의 그대로 쓰는 값이라,
        선언한 '진행 순서'대로 폴더를 먼저 둔다."""
        root.addWidget(self._build_top_bar())
        root.addWidget(self._build_subtitle())
        root.addWidget(self._build_device_row())
        root.addWidget(self._build_automation_card())
        root.addWidget(self._build_scope_row())
        root.addWidget(self._build_engine_card())
        root.addWidget(self._build_howto())
        root.addStretch(1)
        if not self._pinned_action_bar():
            root.addWidget(self._build_action_bar())
        root.addWidget(self._build_credit())

    def _build_top_bar(self) -> QWidget:
        """상단 툴바(배치 스위처 + 보기 옵션) + 제목 — 배치안 공통 상단.

        한 줄에 몰아넣지 않는다: 800×600 에서 제목까지 같은 줄에 두면 폭이 넘쳐
        가로 스크롤이 생긴다(실측 확인).  컨트롤 줄과 제목 줄을 분리해 좁은 창에서도
        안전하게 한다."""
        host = QWidget(self)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)

        tools = QWidget(host)
        trow = QHBoxLayout(tools)
        trow.setContentsMargins(0, 0, 0, 0)
        trow.setSpacing(12)
        trow.addStretch(1)
        trow.addWidget(self._build_view_options())
        col.addWidget(tools)

        # 제목 줄 — 제목 왼쪽, 모드 배지 오른쪽.  배지가 제목과 같은 줄에 있어야
        # '무슨 모드야'가 첫 시선에 들어온다.
        title_row = QWidget(host)
        tr = QHBoxLayout(title_row)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.setSpacing(16)
        tr.addWidget(self._build_title())
        tr.addStretch(1)
        tr.addWidget(self._build_mode_badge(),
                     alignment=Qt.AlignmentFlag.AlignVCenter)
        col.addWidget(title_row)

        # ★ 배치 스위처는 **제목보다 아래·작게**.  이전엔 58px 칩이 33px 표제 위에
        #   있어 '삭제 예정 임시 컨트롤'이 화면에서 가장 큰 요소였다(채점자 3인 지적).
        col.addWidget(self._build_layout_switcher())
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

    def _build_layout_switcher(self) -> QWidget:
        """상단 배치 전환 버튼 — 3개 안을 눌러 보며 비교한다(비교용 임시 컨트롤).

        안이 확정되면 이 스위처와 미선택 배치안을 함께 제거한다."""
        from .setup_layouts import LAYOUT_LABELS, layout_keys
        host = QWidget(self)
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        lbl = QLabel(i18n.KO.LAYOUT_SWITCH_LABEL, host)
        lbl.setProperty("role", "muted")
        row.addWidget(lbl)
        keys = layout_keys()
        self.layout_group = OptionGroup(
            [(k, LAYOUT_LABELS[k]) for k in keys],
            current=self.LAYOUT_KEY, min_tile_w=84,
            fixed_cols=len(keys), role="chip",
            # ★ 배치 전환은 페이지 **재생성**이다 — 방향키로 훑기만 해도 화면이 날아가고
            #   포커스를 잃으면 안 된다.  포커스만 옮기고 Space/Enter 로 확정한다.
            activate_on_arrow=False, parent=host,
        )
        for k in keys:
            self.layout_group.set_option_tooltip(k, i18n.KO.LAYOUT_SWITCH_TOOLTIP)
        self.layout_group.selection_changed.connect(self._on_layout_chosen)
        row.addWidget(self.layout_group)
        # ★ 남는 폭을 흡수하는 stretch — 없으면 칩이 화면 폭만큼 늘어나 '작게'라는
        #   의도가 무너진다(그리드가 열마다 stretch 1 을 주기 때문).
        row.addStretch(1)
        return host

    def _on_layout_chosen(self, key: str) -> None:
        if key == self.LAYOUT_KEY:
            return
        _prefs.patch(setup_layout=key)
        self.appearance_changed.emit()      # main_window 가 페이지를 다시 만든다

    def _build_title(self) -> QWidget:
        # 화면 크기 컨트롤은 별도 버튼 없이 OS 의 표준 창 조작
        # (드래그, 최대화/복원, 모서리 리사이즈) 으로만 처리.
        title = QLabel(i18n.KO.SETUP_TITLE, self)
        title.setProperty("role", "title")
        return title

    def _build_subtitle(self) -> QWidget:
        subtitle = QLabel(i18n.KO.SETUP_HINT, self)
        subtitle.setProperty("role", "subtitle")
        subtitle.setWordWrap(True)
        return subtitle

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
        howto_body.setStyleSheet(
            f"color: {theme.INK}; line-height: 160%; padding-top: 4px;"
        )
        howto_card.body().addWidget(howto_body)
        self._howto_section.add_content_widget(howto_card)
        self._howto_section.toggled.connect(
            lambda expanded: _prefs.patch(howto_expanded=bool(expanded))
        )
        return self._howto_section

    def _build_automation_card(self) -> QWidget:
        """자동화 수준 — 올인원 모드 (#3)."""
        _prefs_now = _prefs.load()
        auto_card = NeonCard(role="card-soft", parent=self)

        auto_title_row = QHBoxLayout()
        auto_title_row.setContentsMargins(0, 0, 0, 0)
        auto_title = QLabel(i18n.KO.AUTOMATION_TITLE, auto_card)
        auto_title.setProperty("role", "cardTitle")
        self._auto_help_btn = QToolButton(auto_card)
        self._auto_help_btn.setText("?")
        self._auto_help_btn.setObjectName("helpToggle")
        self._auto_help_btn.setCheckable(True)
        self._auto_help_btn.setToolTip(i18n.KO.HELP_TOGGLE_TOOLTIP)
        auto_title_row.addWidget(auto_title)
        auto_title_row.addStretch()
        auto_title_row.addWidget(self._auto_help_btn)
        auto_card.body().addLayout(auto_title_row)

        # 작은 라디오 대신 타일 — 키가 곧 AutomationLevel 값이라 분기 없이 읽는다.
        _last_auto = getattr(_prefs_now, "automation_level", AutomationLevel.USER_SELECT)
        self.auto_group = OptionGroup(
            [(AutomationLevel.USER_SELECT, i18n.KO.AUTOMATION_USER_SELECT),
             (AutomationLevel.AUTO_ALL, i18n.KO.AUTOMATION_AUTO_ALL)],
            current=_last_auto,
            # 부수효과 없음(prefs 저장뿐) → 방향키로 바로 고를 수 있다(라디오 감각).
            activate_on_arrow=True, parent=auto_card,
        )
        self.auto_group.selection_changed.connect(
            lambda key: _prefs.patch(automation_level=key))
        auto_card.body().addWidget(self.auto_group)
        self._auto_hint = QLabel(i18n.KO.AUTOMATION_HINT, auto_card)
        self._auto_hint.setProperty("role", "muted")
        self._auto_hint.setWordWrap(True)
        self._auto_hint.setStyleSheet("padding-top: 4px;")
        self._auto_hint.setVisible(False)
        auto_card.body().addWidget(self._auto_hint)
        self._auto_help_btn.toggled.connect(self._auto_hint.setVisible)
        return auto_card

    # ★ 나란히 둘지는 **카드가 요구하는 폭**으로 판단한다 — '900px' 같은 매직 넘버는
    #   맞출 수 없다.  실제로 900 으로 두었더니 1000px 창에서 두 카드가 나란히 서고
    #   가로 스크롤 58px 이 났다(경로 입력란 하한 240 + 라벨 + [폴더 선택…] × 2).
    #   `minimumSizeHint()` 는 카드 패딩·라벨·버튼을 모두 포함한 진짜 하한이다.
    # 101단계 슬라이더를 1200px 에 펼치면 단계당 12px — 넓을수록 정밀해지지 않는다.
    _SLIDER_MAX_W = 320
    _SLIDER_MIN_W = 200      # 101단계를 74px 에 펼치면 조절이 불가능하다(실측)
    _PATH_MIN_W = 240        # 폴더 이름이 읽히는 하한

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

    def _build_scope_row(self) -> QWidget:
        """진행 범위 — 상태가 **타일 자신**에 산다(옆 라벨에 두지 않는다).

        None = 전체 진행."""
        # ★ 이전엔 이것만 카드 없는 '맨몸 제목' 이라 (a) 제목 축이 다른 카드보다 21px
        #   왼쪽으로 어긋났고 (b) 선택 타일이 카드 면이 아니라 페이지 바탕 위에 앉아
        #   라벨 대비가 4.87 로 게이트를 깼다.  카드로 감싸 둘을 함께 해결한다.
        self._selected_slots: Optional[set] = None
        host = NeonCard(role="card", parent=self)
        col = host.body()
        title = QLabel(i18n.KO.SCOPE_TITLE, host)
        title.setProperty("role", "cardTitle")
        col.addWidget(title)
        # ★ activate_on_arrow 를 켜지 않는다(기본 False) — 'subset' 선택은 슬롯 선택
        #   **모달**을 띄운다.  방향키 한 번에 경고창이 떠 하네스가 블로킹된 적이 있다.
        self.scope_group = OptionGroup(
            [("all", i18n.KO.SCOPE_ALL), ("subset", i18n.KO.SCOPE_SUBSET)],
            current="all", parent=host,
        )
        self.scope_group.set_option_tooltip("subset", i18n.KO.SLOT_SELECT_BTN_TOOLTIP)
        self.scope_group.selection_changed.connect(self._on_scope_changed)
        col.addWidget(self.scope_group)
        # 남는 세로 공간은 아래로 — 그리드 배치(B안)에서 제목과 타일이 벌어지지 않게.
        col.addStretch(1)
        return host

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

        # 좌표 매칭 허용 오차 스핀박스 (항상 표시 — 기본 모드)
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
        # 배지와 표기를 일치시킨다 — 같은 값을 500 / 500.0 두 가지로 쓰지 않는다.
        self.coord_tol_spin.setDecimals(0)
        self.coord_tol_spin.setSuffix(" µm")
        self.coord_tol_spin.setValue(getattr(_prefs_now, "coord_tolerance", 500.0))
        self.coord_tol_spin.setToolTip(i18n.KO.COORD_TOLERANCE_TOOLTIP)
        # 수치는 모노 — '도면' 컨셉의 핵심인데 이 화면엔 모노가 한 글자도 없었다.
        self.coord_tol_spin.valueChanged.connect(
            lambda _v: self._refresh_mode_badge())
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
        """모드에 따라 무효한 파라미터를 **비활성**(숨기지 않음).

        숨기지 않는 이유: 스크롤 안에서 show/hide 는 내용이 튀고, 무엇보다 '허용 오차는
        좌표 매칭에, 임계치는 구형 모드에 속한다'는 사실을 눈으로 가르쳐 준다."""
        legacy_on = self.legacy_switch.is_on()
        self.legacy_group.setEnabled(legacy_on)
        self._threshold_row.setEnabled(legacy_on)
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
        # 개발자 모드(환경변수 AOI_DEV_MODE 또는 prefs.dev_mode)에서만 보이는
        # ‘개발자 벤치마크 / 정답 라벨’ 버튼 — 일반 사용자 화면에는 나타나지 않는다.
        # 앱 안에서 Ctrl+Shift+D 로 켜고 끌 수 있으며, 토글 시 버튼이 즉시
        # 나타나거나 사라진다(아래 _refresh_dev_buttons).
        # ★ 인덱스 계약: [0]=update_btn, [1..2]=개발자 버튼, 그 뒤 stretch.
        #   신규 위젯은 반드시 stretch **뒤**에 붙인다(_refresh_dev_buttons 가정 보존).
        self._action_bar = bar
        self.dev_bench_btn: NeonButton | None = None
        self.dev_label_btn: NeonButton | None = None
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
        self._refresh_dev_buttons()
        self._validate()                      # 초기 상태(폴더 미지정) 반영
        return host

    def _build_credit(self) -> QWidget:
        """개발자 크레딧 (메인 화면)."""
        credit = QLabel(i18n.KO.CREDIT, self)
        credit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credit.setStyleSheet(f"color: {theme.MUTE}; padding-top: 10px;")
        return credit

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
            if state == "empty":
                err.setText(i18n.KO.SETUP_NEED_FOLDER)
            elif state == "missing":
                err.setText(i18n.KO.SETUP_INVALID_FOLDER)
            else:
                err.setText("")
            # ★ setVisible 을 쓰지 않는다 — 자리를 예약해 뒀으므로 문자열만 바꾼다.

    def _validate(self) -> bool:
        """두 폴더가 모두 유효할 때만 [검증 시작] 을 활성화하고, 아니면 이유를 표시."""
        ref_state = self._dir_state(self.ref_path_edit.text())
        val_state = self._dir_state(self.val_path_edit.text())
        # 비어 있는 초기 상태에서 빨간 테두리로 겁주지 않는다 — 문구만 조용히.
        self._set_field_state(self.ref_path_edit,
                              ref_state if ref_state == "missing" else "")
        self._set_field_state(self.val_path_edit,
                              val_state if val_state == "missing" else "")
        ok = not ref_state and not val_state
        self.start_btn.setEnabled(ok)
        if hasattr(self, "_start_hint"):
            self._start_hint.setText("" if ok else i18n.KO.START_BLOCKED_HINT)
        return ok

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
            QMessageBox.warning(
                self, i18n.KO.APP_TITLE, i18n.KO.SLOT_SELECT_NEED_REF,
            )
            self._reset_slot_selection()       # 고를 수 없으면 전체 진행으로 복귀
            return
        slot_names = sorted(list_slot_dirs(ref_root).keys())
        if not slot_names:
            QMessageBox.information(
                self, i18n.KO.APP_TITLE, i18n.KO.SLOT_SELECT_EMPTY,
            )
            self._reset_slot_selection()
            return
        dlg = SlotSelectDialog(
            slot_names, preselected=self._selected_slots, parent=self,
        )
        if dlg.exec() and dlg.accepted_ok:
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

    # ------------------------------------------------------------------
    # 개발자 모드 — 앱 내 토글 + 버튼 갱신
    # ------------------------------------------------------------------
    def _dev_mode_enabled(self) -> bool:
        try:
            from ..widgets.dev_benchmark_dialog import dev_mode_enabled
            return bool(dev_mode_enabled())
        except Exception:
            return False

    def _refresh_dev_buttons(self) -> None:
        """개발자 모드 상태에 맞춰 ‘개발자 벤치마크 / 정답 라벨’ 버튼을 추가·제거."""
        bar = getattr(self, "_action_bar", None)
        if bar is None:
            return
        enabled = self._dev_mode_enabled()
        # 켜짐 → 없으면 생성해 update_btn 다음(index 1)에 삽입.
        if enabled:
            if self.dev_bench_btn is None:
                self.dev_bench_btn = NeonButton(i18n.KO.DEV_BENCH_BUTTON, role="ghost")
                self.dev_bench_btn.setMinimumHeight(46)
                self.dev_bench_btn.clicked.connect(self._open_dev_benchmark)
                bar.insertWidget(1, self.dev_bench_btn)
            if self.dev_label_btn is None:
                self.dev_label_btn = NeonButton(i18n.KO.DEV_LABEL_BUTTON, role="ghost")
                self.dev_label_btn.setMinimumHeight(46)
                self.dev_label_btn.clicked.connect(self._open_label_maker)
                bar.insertWidget(2, self.dev_label_btn)
        else:
            for attr in ("dev_bench_btn", "dev_label_btn"):
                btn = getattr(self, attr, None)
                if btn is not None:
                    bar.removeWidget(btn)
                    btn.deleteLater()
                    setattr(self, attr, None)

    def _toggle_dev_mode(self) -> None:
        """Ctrl+Shift+D — 개발자 모드 on/off (prefs 영속) + 버튼 즉시 갱신."""
        # 환경변수로 강제된 경우엔 그 상태가 우선하지만, prefs 플래그는 토글한다.
        cur = bool(getattr(_prefs.load(), "dev_mode", False))
        new = not cur
        _prefs.patch(dev_mode=new)
        self._refresh_dev_buttons()
        if self._dev_mode_enabled():
            QMessageBox.information(
                self, i18n.KO.DEV_MODE_TOGGLE_TITLE,
                i18n.KO.DEV_MODE_ON_FMT.format(button=i18n.KO.DEV_BENCH_BUTTON))
        else:
            QMessageBox.information(
                self, i18n.KO.DEV_MODE_TOGGLE_TITLE, i18n.KO.DEV_MODE_OFF)

    def _default_dev_roots(self) -> tuple[str, str]:
        """개발자 도구의 기본 기준/검증 폴더 — 현재 입력 → 마지막 입력 → 예시 ‘기준’."""
        from ...utils import paths as _paths
        ref = self.ref_path_edit.text().strip()
        if not ref:
            ref = getattr(_prefs.load(), "last_ref_root", "") or ""
            if not ref:
                cand = _paths.resource_path("기준")
                if cand.is_dir():
                    ref = str(cand)
        val = self.val_path_edit.text().strip()
        if not val:
            val = getattr(_prefs.load(), "last_val_root", "") or ""
        return ref, val

    def _open_dev_benchmark(self) -> None:
        """개발자 벤치마크 다이얼로그 — 매칭 가속 조합 실험(개발자 모드 전용)."""
        from ..widgets.dev_benchmark_dialog import DevBenchmarkDialog
        default_ref, default_val = self._default_dev_roots()
        dlg = DevBenchmarkDialog(self, default_ref=default_ref,
                                 default_val=default_val)
        dlg.showMaximized()
        dlg.exec()

    def _open_label_maker(self) -> None:
        """정답 라벨 만들기 다이얼로그 — 기준 사진별 정답 검증 사진 지정(개발자 모드 전용)."""
        from ..widgets.label_maker_dialog import LabelMakerDialog
        default_ref, default_val = self._default_dev_roots()
        dlg = LabelMakerDialog(self, default_ref=default_ref,
                               default_val=default_val)
        dlg.showMaximized()
        dlg.exec()

    def _on_threshold_changed(self, v: int) -> None:
        self.threshold_label.setText(f"{v} %")
        self._refresh_mode_badge()
        _prefs.patch(threshold=v / 100.0)

    # ------------------------------------------------------------------
    def _build_view_options(self) -> QWidget:
        """상단 보기 옵션 줄 — 색 모드 3택 · '모션 줄이기'.

        ★ 색 모드가 on/off 스위치가 아니라 **3택**이다(벨럼 · 청사진 · 흑연).  어두운
        모드가 둘이라 boolean 으로 표현할 수 없고, 애초에 '어두운 화면 켜기'보다
        '어느 시트를 쓸지'가 정확한 모형이다.

        ★ 두 컨트롤의 **어휘를 통일**한다: 이전엔 나란한 두 설정이 48×28 스위치와
        18px 체크박스로 서로 달랐고, 하필 시각적으로 약한 쪽이 모션 설정이었다."""
        host = QWidget(self)
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        row.addStretch(1)

        mode_lbl = QLabel(i18n.KO.COLOR_MODE_LABEL, host)
        mode_lbl.setProperty("role", "muted")
        row.addWidget(mode_lbl)
        keys = theme.color_mode_keys()
        self.color_mode_group = OptionGroup(
            [(k, theme.COLOR_MODE_LABELS[k]) for k in keys],
            current=theme.COLOR_MODE, min_tile_w=64,
            fixed_cols=len(keys), role="chip",
            # 색 모드 전환도 페이지 **재생성**이다 — 방향키 즉시 커밋 금지.
            activate_on_arrow=False, parent=host,
        )
        for k in keys:
            self.color_mode_group.set_option_tooltip(
                k, i18n.KO.COLOR_MODE_TOOLTIP_FMT.format(
                    name=theme.COLOR_MODE_LABELS[k]))
        self.color_mode_group.selection_changed.connect(self._on_color_mode_chosen)
        row.addWidget(self.color_mode_group)

        self._reduce_switch = SwitchRow(
            i18n.KO.REDUCE_MOTION_LABEL, parent=host,
            checked=self._saved_reduce_motion(),
        )
        self._reduce_switch.setToolTip(i18n.KO.REDUCE_MOTION_TOOLTIP)
        self._reduce_switch.toggled.connect(self._on_reduce_motion)
        row.addWidget(self._reduce_switch)
        return host

    @staticmethod
    def _saved_reduce_motion() -> bool:
        try:
            return bool(_prefs.load().reduce_motion)
        except Exception:
            return False

    def _on_reduce_motion(self, on: bool) -> None:
        from .. import motion
        _prefs.patch(reduce_motion=bool(on))
        motion.set_reduce_motion(bool(on))

    def _on_color_mode_chosen(self, key: str) -> None:
        """색 모드 전환 요청 — 실제 적용(페이지 재생성)은 main_window 가 한다."""
        if key == theme.COLOR_MODE:
            return
        _prefs.patch(color_mode=key)
        self.appearance_changed.emit()

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
            QMessageBox.warning(self, i18n.KO.APP_TITLE,
                                i18n.KO.WARN_PATH_NOT_EXIST.format(path=ref_root))
            return
        if not val_root.exists() or not val_root.is_dir():
            QMessageBox.warning(self, i18n.KO.APP_TITLE,
                                i18n.KO.WARN_PATH_NOT_EXIST.format(path=val_root))
            return
        if not ref_machine:
            ref_machine = "기준호기"
        if not val_machine:
            val_machine = "검증호기"

        if ref_root.resolve() == val_root.resolve():
            r = QMessageBox.question(
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
        # 효율 모드 = CPU+GPU fusion-zscore 고정.  NPU 는 비활성(코드만 보존).
        use_cpu = True
        use_gpu = True
        use_npu = False
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
            use_npu=use_npu,
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
            use_npu=use_npu,
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
    def capture_draft(self) -> dict:
        """재생성을 넘어 살려야 하는 입력값."""
        scope = self.scope_group.current_key()
        subset_btn = self.scope_group.button("subset")
        return {
            "ref_root": self.ref_path_edit.text(),
            "val_root": self.val_path_edit.text(),
            "ref_machine": self.ref_machine_edit.text(),
            "val_machine": self.val_machine_edit.text(),
            "coord_tolerance": float(self.coord_tol_spin.value()),
            "scope": scope,
            "subset_label": subset_btn.text() if subset_btn is not None else "",
            "selected_slots": (set(self._selected_slots)
                              if self._selected_slots is not None else None),
        }

    def restore_draft(self, draft: dict) -> None:
        """``capture_draft`` 로 걷은 값을 되돌린다(시그널 없이 — 저장 루프 방지)."""
        if not draft:
            return
        self.ref_path_edit.setText(draft.get("ref_root", "") or "")
        self.val_path_edit.setText(draft.get("val_root", "") or "")
        self.ref_machine_edit.setText(draft.get("ref_machine", "") or "")
        self.val_machine_edit.setText(draft.get("val_machine", "") or "")
        tol = draft.get("coord_tolerance")
        if tol:
            self.coord_tol_spin.setValue(float(tol))
        slots = draft.get("selected_slots")
        self._selected_slots = set(slots) if slots else None
        label = draft.get("subset_label") or ""
        if label:
            self.scope_group.set_option_label("subset", label)
        scope = draft.get("scope") or "all"
        if scope in self.scope_group.keys():
            # ★ emit=False — 'subset' 을 emit 하면 슬롯 선택 다이얼로그가 다시 뜬다.
            self.scope_group.set_current_key(scope)
        self._sync_engine_controls()
        self._validate()

    # ------------------------------------------------------------------
    def apply_state(self, ref_root: str, val_root: str,
                    ref_machine: str, val_machine: str,
                    mode: str, threshold: float) -> None:
        self.ref_path_edit.setText(ref_root)
        self.val_path_edit.setText(val_root)
        self.ref_machine_edit.setText(ref_machine)
        self.val_machine_edit.setText(val_machine)
        self.slider.setValue(int(threshold * 100))
