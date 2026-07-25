"""초기 입력 화면 (Setup) — 모드/폴더/호기/임계치 입력."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QDoubleSpinBox,
                              QFileDialog, QFormLayout,
                              QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                              QMessageBox, QRadioButton, QScrollArea,
                              QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from ... import config, i18n
from .. import theme
from ...utils import prefs as _prefs
from ...utils.prefs import AutomationLevel, EngineMode
from ..widgets.collapsible_section import CollapsibleSection
from ..widgets.neon_button import NeonButton
from ..widgets.neon_card import NeonCard
from ..widgets.no_wheel_slider import NoWheelSlider
from ..widgets.option_group import OptionGroup
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
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(20)

        # 본문 구성 — 배치안(서브클래스)이 이 메서드만 오버라이드하면 배치가 바뀐다.
        self._build_body(root)

        # 개발자 모드 토글 단축키 — 일반 사용자에게는 보이지 않는 진입점.
        self._dev_shortcut = QShortcut(QKeySequence("Ctrl+Shift+D"), self)
        self._dev_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._dev_shortcut.activated.connect(self._toggle_dev_mode)

    # ==================================================================
    # 본문 빌더 — 각 조각은 위젯 하나를 만들어 돌려준다.  배치안은 ``_build_body``
    # (순서·열 구성) 또는 개별 빌더(컨트롤 디자인)만 갈아끼우면 된다.
    # ==================================================================
    def _build_body(self, root: QVBoxLayout) -> None:
        """기본 배치 — 한 열, 위에서 아래로(현행 순서 그대로)."""
        root.addWidget(self._build_title())
        root.addWidget(self._build_view_options())
        root.addWidget(self._build_subtitle())
        root.addWidget(self._build_howto())
        root.addWidget(self._build_automation_card())
        root.addWidget(self._build_device_row())
        root.addWidget(self._build_scope_row())
        root.addWidget(self._build_engine_card())
        root.addStretch(1)
        root.addWidget(self._build_action_bar())
        root.addWidget(self._build_credit())

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

        self.radio_auto_user = QRadioButton(i18n.KO.AUTOMATION_USER_SELECT, auto_card)
        self.radio_auto_all = QRadioButton(i18n.KO.AUTOMATION_AUTO_ALL, auto_card)
        # 마지막 선택 복원 (기본: 사진 직접 선택).
        _last_auto = getattr(_prefs_now, "automation_level", AutomationLevel.USER_SELECT)
        if _last_auto == AutomationLevel.AUTO_ALL:
            self.radio_auto_all.setChecked(True)
        else:
            self.radio_auto_user.setChecked(True)
        for rb in (self.radio_auto_user, self.radio_auto_all):
            auto_card.body().addWidget(rb)
        self._auto_hint = QLabel(i18n.KO.AUTOMATION_HINT, auto_card)
        self._auto_hint.setProperty("role", "muted")
        self._auto_hint.setWordWrap(True)
        self._auto_hint.setStyleSheet("padding-top: 4px;")
        self._auto_hint.setVisible(False)
        auto_card.body().addWidget(self._auto_hint)
        self._auto_help_btn.toggled.connect(self._auto_hint.setVisible)
        return auto_card

    def _build_device_row(self) -> QWidget:
        """기준/검증 장비 폴더·호기 2칸."""
        host = QWidget(self)
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(20)
        self.ref_group, self.ref_path_edit, self.ref_machine_edit = \
            self._make_machine_group(i18n.KO.SETUP_REF_GROUP)
        self.val_group, self.val_path_edit, self.val_machine_edit = \
            self._make_machine_group(i18n.KO.SETUP_VAL_GROUP)
        row.addWidget(self.ref_group)
        row.addWidget(self.val_group)
        return host

    def _build_scope_row(self) -> QWidget:
        """일부 슬롯만 진행 옵션.  None = 전체 진행."""
        self._selected_slots: Optional[set] = None
        host = QWidget(self)
        slot_row = QHBoxLayout(host)
        slot_row.setContentsMargins(0, 0, 0, 0)
        self.btn_select_slots = NeonButton(
            i18n.KO.SLOT_SELECT_BTN, role="ghost",
        )
        self.btn_select_slots.setToolTip(i18n.KO.SLOT_SELECT_BTN_TOOLTIP)
        self.btn_select_slots.clicked.connect(self._open_slot_select)
        self.slot_select_label = QLabel(i18n.KO.SLOT_SELECT_ALL_HINT, self)
        self.slot_select_label.setProperty("role", "muted")
        slot_row.addWidget(self.btn_select_slots)
        slot_row.addWidget(self.slot_select_label, stretch=1)
        return host

    def _build_engine_card(self) -> QWidget:
        """매칭 설정 — 허용 오차(좌표) + 구형(유사도) 모드."""
        _prefs_now = _prefs.load()
        engine_card = NeonCard(role="card-soft", parent=self)
        eng_title = QLabel(i18n.KO.ENGINE_CARD_TITLE, engine_card)
        eng_title.setProperty("role", "cardTitle")
        engine_card.body().addWidget(eng_title)

        # 좌표 매칭 허용 오차 스핀박스 (항상 표시 — 기본 모드)
        self._tol_row = QWidget(engine_card)
        _tol_layout = QHBoxLayout(self._tol_row)
        _tol_layout.setContentsMargins(0, 0, 0, 0)
        _tol_layout.setSpacing(6)
        _tol_label = QLabel(i18n.KO.COORD_TOLERANCE_LABEL, self._tol_row)
        _tol_label.setToolTip(i18n.KO.COORD_TOLERANCE_TOOLTIP)
        self.coord_tol_spin = QDoubleSpinBox(self._tol_row)
        self.coord_tol_spin.setRange(10.0, 5000.0)
        self.coord_tol_spin.setSingleStep(50.0)
        self.coord_tol_spin.setDecimals(1)
        self.coord_tol_spin.setSuffix(" µm")
        self.coord_tol_spin.setValue(getattr(_prefs_now, "coord_tolerance", 500.0))
        self.coord_tol_spin.setToolTip(i18n.KO.COORD_TOLERANCE_TOOLTIP)
        _tol_layout.addWidget(_tol_label)
        _tol_layout.addWidget(self.coord_tol_spin)
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
            current=_legacy_sub, parent=engine_card,
        )
        self.legacy_group.selection_changed.connect(self._on_legacy_sub_changed)
        engine_card.body().addWidget(self.legacy_group)

        # 임계치 슬라이더 (구형 모드 전용 파라미터)
        self._threshold_row = QWidget(engine_card)
        sl_row = QHBoxLayout(self._threshold_row)
        sl_row.setContentsMargins(0, 0, 0, 0)
        sl_row.addWidget(QLabel(i18n.KO.SETUP_THRESHOLD_LABEL, self._threshold_row))
        self.slider = NoWheelSlider(Qt.Orientation.Horizontal, self._threshold_row)
        self.slider.setRange(0, 100)
        self.slider.setValue(int(round(_prefs_now.threshold * 100)))
        self.threshold_label = QLabel(f"{self.slider.value()} %", self._threshold_row)
        self.threshold_label.setStyleSheet(f"color: {theme.INK}; font-weight: 700;")
        self.threshold_label.setFixedWidth(60)
        self.slider.valueChanged.connect(self._on_threshold_changed)
        sl_row.addWidget(self.slider, stretch=1)
        sl_row.addWidget(self.threshold_label)
        engine_card.body().addWidget(self._threshold_row)

        # 지금 어느 파라미터가 유효한지 문장으로 — 비활성 컨트롤의 이유를 말해준다.
        self._engine_inert_hint = QLabel("", engine_card)
        self._engine_inert_hint.setProperty("role", "muted")
        self._engine_inert_hint.setWordWrap(True)
        engine_card.body().addWidget(self._engine_inert_hint)

        self._sync_engine_controls()
        return engine_card

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
        self.start_btn = NeonButton(i18n.KO.BTN_START, role="primary")
        self.start_btn.setMinimumWidth(220)
        self.start_btn.setMinimumHeight(46)
        self.start_btn.clicked.connect(self._on_start)
        bar.addWidget(self.start_btn)
        self._refresh_dev_buttons()
        return host

    def _build_credit(self) -> QWidget:
        """개발자 크레딧 (메인 화면)."""
        credit = QLabel(i18n.KO.CREDIT, self)
        credit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credit.setStyleSheet(f"color: {theme.MUTE}; padding-top: 10px;")
        return credit

    # ------------------------------------------------------------------
    def _make_machine_group(self, title: str) -> tuple[QGroupBox, QLineEdit, QLineEdit]:
        box = QGroupBox(title, self)
        form = QFormLayout(box)
        form.setContentsMargins(14, 18, 14, 14)
        form.setSpacing(10)

        # 경로 + 버튼
        row = QHBoxLayout()
        path_edit = QLineEdit(box)
        path_edit.setPlaceholderText(i18n.KO.SETUP_FOLDER_PLACEHOLDER)
        path_edit.setReadOnly(False)
        browse = NeonButton(i18n.KO.BTN_BROWSE, role="ghost")
        browse.clicked.connect(lambda: self._browse(path_edit))
        row.addWidget(path_edit, stretch=1)
        row.addWidget(browse)
        form.addRow(QLabel(i18n.KO.SETUP_FOLDER_LABEL, box), self._wrap(row))

        machine_edit = QLineEdit(box)
        machine_edit.setPlaceholderText(i18n.KO.SETUP_MACHINE_PLACEHOLDER)
        form.addRow(QLabel(i18n.KO.SETUP_MACHINE_LABEL, box), machine_edit)

        return box, path_edit, machine_edit

    @staticmethod
    def _wrap(lay):
        host = QWidget()
        host.setLayout(lay)
        return host

    def _browse(self, target: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, i18n.KO.SETUP_FOLDER_LABEL)
        if path:
            target.setText(path)
            # 기준 폴더가 바뀌면 이전 슬롯 선택은 더 이상 유효하지 않다.
            if target is self.ref_path_edit:
                self._reset_slot_selection()

    # ------------------------------------------------------------------
    def _reset_slot_selection(self) -> None:
        self._selected_slots = None
        self.slot_select_label.setText(i18n.KO.SLOT_SELECT_ALL_HINT)

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
            return
        slot_names = sorted(list_slot_dirs(ref_root).keys())
        if not slot_names:
            QMessageBox.information(
                self, i18n.KO.APP_TITLE, i18n.KO.SLOT_SELECT_EMPTY,
            )
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
                self.slot_select_label.setText(
                    i18n.KO.SLOT_SELECT_COUNT_FMT.format(
                        n=len(chosen), total=len(slot_names),
                    )
                )

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
        _prefs.patch(threshold=v / 100.0)

    # ------------------------------------------------------------------
    def _build_view_options(self) -> QWidget:
        """상단 보기 옵션 줄 — '모션 줄이기' 접근성 토글."""
        host = QWidget(self)
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addStretch(1)
        self._reduce_chk = QCheckBox(i18n.KO.REDUCE_MOTION_LABEL, host)
        self._reduce_chk.setToolTip(i18n.KO.REDUCE_MOTION_TOOLTIP)
        try:
            self._reduce_chk.setChecked(bool(_prefs.load().reduce_motion))
        except Exception:
            pass
        self._reduce_chk.toggled.connect(self._on_reduce_motion)
        row.addWidget(self._reduce_chk)
        return host

    def _on_reduce_motion(self, on: bool) -> None:
        from .. import motion
        _prefs.patch(reduce_motion=bool(on))
        motion.set_reduce_motion(bool(on))

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
        if self.radio_auto_all.isChecked():
            automation = AutomationLevel.AUTO_ALL
        else:
            automation = AutomationLevel.USER_SELECT
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

    # ------------------------------------------------------------------
    def apply_state(self, ref_root: str, val_root: str,
                    ref_machine: str, val_machine: str,
                    mode: str, threshold: float) -> None:
        self.ref_path_edit.setText(ref_root)
        self.val_path_edit.setText(val_root)
        self.ref_machine_edit.setText(ref_machine)
        self.val_machine_edit.setText(val_machine)
        self.slider.setValue(int(threshold * 100))
