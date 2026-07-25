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
    _MIN_SECTION_W = 430           # 이 폭을 밑돌면 열을 줄인다(가로 스크롤 방지)

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
        self.ref_group, self.ref_path_edit, self.ref_machine_edit = \
            self._make_machine_group(i18n.KO.SETUP_REF_GROUP)
        self.val_group, self.val_path_edit, self.val_machine_edit = \
            self._make_machine_group(i18n.KO.SETUP_VAL_GROUP)
        self._sections = [
            self.ref_group,
            self.val_group,
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

    def _reflow_sections(self) -> None:
        avail = self.width() or 0
        if not avail:
            p = self.parentWidget()
            avail = p.width() if p is not None else 0
        reflow_into_grid(self._section_grid, self._sections, avail,
                         self._MIN_SECTION_W)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_section_grid"):
            self._reflow_sections()


class SetupPageC(SetupPage):
    """C안 — 현재 설정 요약 한 줄 + 상세는 접어 둔다."""

    LAYOUT_KEY = "c"

    def _build_body(self, root: QVBoxLayout) -> None:
        root.addWidget(self._build_top_bar())
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

        root.addStretch(1)
        root.addWidget(self._build_action_bar())
        root.addWidget(self._build_credit())
        self._refresh_summary()

        # 설정이 바뀌면 요약도 따라간다.
        self.auto_group.selection_changed.connect(lambda _k: self._refresh_summary())
        self.scope_group.selection_changed.connect(lambda _k: self._refresh_summary())
        self.legacy_switch.toggled.connect(lambda _o: self._refresh_summary())
        self.legacy_group.selection_changed.connect(lambda _k: self._refresh_summary())

    def _refresh_summary(self) -> None:
        engine = (i18n.KO.SUMMARY_ENGINE_LEGACY if self.legacy_switch.is_on()
                  else i18n.KO.SUMMARY_ENGINE_COORD)
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
