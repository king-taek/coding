"""셋업 화면 **배치안 3종** — 상단 스위처로 눌러 보며 비교하기 위한 임시 모듈.

세 안 모두 컨트롤·유효성·엔진 스위치·색 모드·로딩 모션을 **똑같이** 쓴다.  다른 것은
**배치(정보 구조)** 뿐이라, 비교가 배치 자체에 대한 판단이 된다.

- **A안 「진행 순서형」** : 한 열, 위→아래(폴더 → 옵션 → 시작).  익숙함 최대.
  (기본 구현 = ``SetupPage._build_body``)
- **B안 「카드 그리드 + 고정 액션바」** : 섹션을 가용 폭에 맞춰 2열로 흘리고, 액션바를
  스크롤 밖에 고정해 [검증 시작]이 항상 손에 닿는다.
- **C안 「요약 헤더 + 점진적 공개」** : 상단에 현재 설정을 한 줄로 요약하고, 상세는 필요할
  때 펼친다.  '지난 설정 그대로 시작'이 가장 빠르다.

※ 안이 확정되면 이 파일과 상단 스위처를 함께 제거한다(미선택 안은 데드 코드).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QVBoxLayout, QWidget

from ... import i18n
from .. import theme
from ...utils import prefs as _prefs
from ..widgets.collapsible_section import CollapsibleSection
from ..widgets.option_group import reflow_into_grid
from .setup_page import SetupPage

# 스위처 표시 순서 = 이 dict 순서.
LAYOUT_LABELS: dict[str, str] = {
    "a": i18n.KO.LAYOUT_A_LABEL,
    "b": i18n.KO.LAYOUT_B_LABEL,
    "c": i18n.KO.LAYOUT_C_LABEL,
}
DEFAULT_LAYOUT = "a"


def layout_keys() -> tuple[str, ...]:
    return tuple(LAYOUT_LABELS.keys())


def current_layout_key() -> str:
    """저장된 배치안 키(미지 값은 기본)."""
    try:
        key = getattr(_prefs.load(), "setup_layout", DEFAULT_LAYOUT)
    except Exception:
        key = DEFAULT_LAYOUT
    return key if key in LAYOUT_LABELS else DEFAULT_LAYOUT


class SetupPageA(SetupPage):
    """A안 — 기본 배치(한 열, 진행 순서)."""

    LAYOUT_KEY = "a"


class SetupPageB(SetupPage):
    """B안 — 섹션을 2열로 흘리고 액션바를 하단에 고정."""

    LAYOUT_KEY = "b"
    # ★ 이 값은 **내용의 실제 하한**이다 — 임의로 낮추면 안 된다.
    #   한 칸에는 장비 카드(라벨 + 경로 입력란 ≥240 + [폴더 선택…])가 들어가므로
    #   440px 밑으로 내려가면 입력란이 짜부라지거나 가로 스크롤이 생긴다(360 으로
    #   낮췄더니 1100px 에서 실제로 hscroll 2px 이 났다).
    #   결과: B안이 2열이 되려면 약 950px 이상이 필요하고, 800×600 에서는 1열이다 —
    #   이건 버그가 아니라 **내용이 정하는 성질**이다(억지로 2열로 만들면 읽을 수 없는
    #   입력란이 나온다).  좁은 창에서 세로 스크롤이 길어지는 것은 B안의 트레이드오프다.
    _MIN_SECTION_W = 440
    # ★ 이름이 'B 2열' 인데 실측 4/4/3/3/3열 이었다 — 2열이 되는 폭이 아예 없었다.
    #   상한을 2로 못 박아 이름과 화면을 일치시킨다(넓으면 열이 넓어질 뿐).
    _MAX_COLS = 2

    def _build_body(self, root: QVBoxLayout) -> None:
        root.addWidget(self._build_top_bar())
        root.addWidget(self._build_subtitle())
        root.addWidget(self._build_howto())

        # 섹션들을 그리드에 흘린다 — 열 수는 가용 폭에서 계산(OptionGroup 과 같은 헬퍼).
        # 기준/검증 카드는 **각각 하나의 섹션**이다 — 한 칸에 둘을 넣으면 입력란이 짜부라진다.
        self._grid_host = QWidget(self)
        self._section_grid = QGridLayout(self._grid_host)
        self._section_grid.setContentsMargins(0, 0, 0, 0)
        self._section_grid.setSpacing(16)
        self._section_grid.setAlignment(Qt.AlignmentFlag.AlignTop)  # 카드 높이 자연스럽게
        self._selected_slots = None
        # ★ 장비 카드를 **직접 만들지 않는다** — `_build_device_row()` 를 쓴다.
        #   B안만 자기 손으로 만들어서 공유 레이어의 수정(폭 기준 세로 스택, 입력란
        #   최소 폭)이 B안에는 하나도 적용되지 않았다: 경로 입력란이 106px 로
        #   짜부라져 폴더 이름이 잘렸다(실측).  '배치만 다르다'는 약속을 지킨다.
        self._sections = [
            self._build_device_row(),
            self._build_automation_card(),
            self._build_engine_card(),
            self._build_scope_row(),
        ]
        self._reflow_sections()
        root.addWidget(self._grid_host)
        # ★ 큰 addStretch 를 두지 않는다 — 넓은 창에서 화면 아래 절반이 비고 크레딧이
        #   그 빈 공간에 홀로 떠 있었다.  그리드 바로 아래에 붙여 '문서 끝'으로 읽히게.
        root.addWidget(self._build_credit())
        root.addStretch(1)

    def _viewport(self):
        from PyQt6.QtWidgets import QScrollArea
        scroll = self.findChild(QScrollArea)
        return scroll.viewport() if scroll is not None else None

    def _reflow_sections(self) -> None:
        # ★ 페이지 폭이 아니라 **뷰포트 폭**을 써야 한다 — 세로 스크롤바·마진을 빼지
        #   않으면 그리드가 페이지를 넘겨 열이 과하게 늘어난다(1512 에서 4열).
        vp = self._viewport()
        avail = vp.width() if vp is not None else 0
        if avail <= 1:
            avail = self.width() or 0
        avail = max(0, avail - 2 * theme.PROFILE.page_margin)
        # ★ 최소 폭을 **숫자로 추측하지 않는다** — 하한을 가진 섹션에게 직접 묻는다.
        #   440 이라 적어 뒀을 때 1100px 에서 hscroll 2px 이 났다: 실제 하한은 카드
        #   패딩·라벨·[폴더 선택…]까지 합친 값이라 손으로 맞출 수 있는 종류가 아니다.
        #   단 **장비 행만** 본다 — 나머지 카드는 글이 감기므로 minimumSizeHint 가
        #   과대하게 나와(설명 2줄) 전부 물어보면 어느 폭에서도 1열이 된다.
        dev = self._sections[0]
        need = max(dev.minimumSizeHint().width(), self._MIN_SECTION_W)
        sp = self._section_grid.spacing()
        n = self._MAX_COLS
        # 상한 n 열: 그 열 수에 딱 맞는 폭으로 올려 더 쪼개지지 않게 한다.
        # 단 내용 하한(need)을 못 맞추면 열을 줄인다(reflow 가 알아서 1열로 떨어진다).
        fit = (avail - (n - 1) * sp) // n if avail > 1 else need
        reflow_into_grid(self._section_grid, self._sections, avail, max(need, fit))

    # ★ 페이지의 resizeEvent 만으로는 부족하다 — 그때 뷰포트는 아직 100px 이고, 그 뒤
    #   뷰포트가 실제 폭을 갖는 순간에는 페이지 resize 가 다시 오지 않는다(실측: 열이
    #   영구히 1로 남았다).  **뷰포트의 resize** 를 직접 듣는다.
    def eventFilter(self, obj, event):  # noqa: N802
        from PyQt6.QtCore import QEvent
        if (event.type() == QEvent.Type.Resize
                and obj is self._viewport()
                and hasattr(self, "_section_grid")):
            self._reflow_sections()
        return super().eventFilter(obj, event)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        vp = self._viewport()
        if vp is not None and not getattr(self, "_vp_hooked", False):
            vp.installEventFilter(self)
            self._vp_hooked = True
        if hasattr(self, "_section_grid"):
            self._reflow_sections()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_section_grid"):
            self._reflow_sections()


class SetupPageC(SetupPage):
    """C안 — 현재 설정 요약 한 줄 + 상세는 접어 둔다."""

    LAYOUT_KEY = "c"

    def _build_body(self, root: QVBoxLayout) -> None:
        root.addWidget(self._build_top_bar())
        # ★ 부제를 빠뜨리고 있었다 — 첫 방문자가 '기준/검증이 뭔지' 알 방법이 없었다.
        root.addWidget(self._build_subtitle())
        # 폴더는 늘 보인다(매번 바꾸는 값).
        root.addWidget(self._build_device_row())
        # 요약 — 지금 무엇으로 돌아갈지 한 줄로.
        # ★ 이 줄은 C안의 **안전 장치**다: 상세를 접어 둔 채 시작하는 배치이므로, 어떤
        #   엔진·범위로 돌아가는지를 여기서만 알 수 있다.  그런데 이전엔 화면에서 가장
        #   작고(12px) 가장 옅은(muted) 글자였다 — 가장 중요한 정보가 가장 안 보였다.
        #   카드에 담고 본문 등급 이상으로 올린다.
        from PyQt6.QtWidgets import QLabel
        from ..widgets.neon_card import NeonCard
        self._summary = NeonCard(role="card", parent=self)
        cap = QLabel(i18n.KO.SUMMARY_CAPTION, self._summary)
        cap.setProperty("role", "colHead")
        self._summary.body().addWidget(cap)
        self._summary_label = QLabel("", self._summary)
        self._summary_label.setProperty("role", "summaryValue")
        self._summary_label.setWordWrap(True)
        self._summary.body().addWidget(self._summary_label)
        root.addWidget(self._summary)

        # 상세 — 필요할 때만.
        self._detail_section = CollapsibleSection(
            open_label=i18n.KO.SETUP_DETAIL_OPEN,
            close_label=i18n.KO.SETUP_DETAIL_CLOSE,
            expanded=False, parent=self,
        )
        detail = QWidget(self._detail_section)
        dlay = QVBoxLayout(detail)
        dlay.setContentsMargins(0, 0, 0, 0)
        dlay.setSpacing(16)
        dlay.addWidget(self._build_automation_card())
        dlay.addWidget(self._build_scope_row())
        dlay.addWidget(self._build_engine_card())
        dlay.addWidget(self._build_howto())
        self._detail_section.add_content_widget(detail)
        root.addWidget(self._detail_section)

        # ★ 액션바를 여기서 다시 만들면 안 된다 — `_build()` 가 이미 스크롤 밖에 고정한다.
        #   두 번 만들면 `self.start_btn` 이 두 번째 것으로 덮여, 스크롤 안의 첫 번째는
        #   유효성 갱신을 못 받는 유령 버튼으로 남는다(실제로 그렇게 렌더됐다).
        if not self._pinned_action_bar():
            root.addWidget(self._build_action_bar())
        # 크레딧은 내용 바로 아래 — 남는 공간은 그 **뒤**로 밀어 문서 끝처럼 읽히게.
        root.addWidget(self._build_credit())
        root.addStretch(1)
        self._refresh_summary()

        # 설정이 바뀌면 요약도 따라간다.
        self.auto_group.selection_changed.connect(lambda _k: self._refresh_summary())
        self.scope_group.selection_changed.connect(lambda _k: self._refresh_summary())
        self.legacy_switch.toggled.connect(lambda _o: self._refresh_summary())
        self.legacy_group.selection_changed.connect(lambda _k: self._refresh_summary())
        self.coord_tol_spin.valueChanged.connect(lambda _v: self._refresh_summary())
        self.slider.valueChanged.connect(lambda _v: self._refresh_summary())

    def _refresh_summary(self) -> None:
        # ★ 요약은 이 배치의 **안전 장치**다 — 엔진 이름만으론 부족하고 판정에 쓰이는
        #   **수치**(허용 오차 µm / 임계치 %)까지 있어야 오조작이 눈에 걸린다.
        if self.legacy_switch.is_on():
            engine = i18n.KO.SUMMARY_ENGINE_LEGACY_FMT.format(
                th=float(self.slider.value()))
        else:
            engine = i18n.KO.SUMMARY_ENGINE_COORD_FMT.format(
                tol=float(self.coord_tol_spin.value()))
        scope = (self.scope_group.button(self.scope_group.current_key()).text()
                 if self.scope_group.button(self.scope_group.current_key())
                 else "")
        auto = (self.auto_group.button(self.auto_group.current_key()).text()
                if self.auto_group.button(self.auto_group.current_key()) else "")
        self._summary_label.setText(
            i18n.KO.SUMMARY_FMT.format(engine=engine, scope=scope, auto=auto))

    # 엔진 컨트롤 상태가 바뀔 때 요약도 갱신(부모가 호출).
    def _sync_engine_controls(self) -> None:
        super()._sync_engine_controls()
        if hasattr(self, "_summary_label"):
            self._refresh_summary()


LAYOUTS: dict[str, type[SetupPage]] = {
    "a": SetupPageA,
    "b": SetupPageB,
    "c": SetupPageC,
}


def make_setup_page() -> SetupPage:
    """저장된 배치안으로 셋업 페이지를 만든다."""
    return LAYOUTS[current_layout_key()]()
