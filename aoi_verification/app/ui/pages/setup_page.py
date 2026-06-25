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
                              QSizePolicy, QVBoxLayout, QWidget)

from ... import config, i18n
from ...utils import prefs as _prefs
from ...utils.prefs import AutomationLevel
from ..widgets.collapsible_section import CollapsibleSection
from ..widgets.neon_button import NeonButton
from ..widgets.neon_card import NeonCard
from ..widgets.no_wheel_slider import NoWheelSlider


@dataclass
class SetupInput:
    mode: str        # 항상 "single" (양쪽 교차검증 제거).
    ref_root: Path
    val_root: Path
    ref_machine: str
    val_machine: str
    threshold: float
    automation_level: str = AutomationLevel.USER_SELECT
    # 유사도 엔진 + 중앙 전처리 (계산 전용).
    engine_mode: str = "basic"       # EngineMode.{BASIC,EFFICIENCY,COORDINATE}
    center_crop: bool = False        # 사진 중앙 30% 만 사용 (기준·검증)
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
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        scroll.viewport().setStyleSheet("background: transparent;")
        outer.addWidget(scroll)

        host = QWidget()
        host.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.MinimumExpanding)
        scroll.setWidget(host)

        # 원본과 동일한 외곽 마진/스페이싱 유지.
        root = QVBoxLayout(host)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(20)

        # 제목 — 화면 크기 컨트롤은 별도 버튼 없이 OS 의 표준 창 조작
        # (드래그, 최대화/복원, 모서리 리사이즈) 으로만 처리.
        title = QLabel(i18n.KO.SETUP_TITLE, self)
        title.setProperty("role", "title")
        root.addWidget(title)

        subtitle = QLabel(i18n.KO.SETUP_HINT, self)
        subtitle.setProperty("role", "subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # 사용 방법 안내 — 접을 수 있는 섹션 (기본 접힘) ----------------
        _prefs_now = _prefs.load()
        self._howto_section = CollapsibleSection(
            open_label=i18n.KO.HOWTO_TOGGLE_OPEN,
            close_label=i18n.KO.HOWTO_TOGGLE_CLOSE,
            expanded=bool(_prefs_now.howto_expanded),
            parent=self,
        )
        howto_card = NeonCard(role="card-soft", parent=self._howto_section)
        howto_title = QLabel(i18n.KO.SETUP_HOW_TO_USE_TITLE, howto_card)
        howto_title.setStyleSheet(
            "color: #39FF14; font-weight: 700; letter-spacing: 1px;"
        )
        howto_card.body().addWidget(howto_title)
        howto_body = QLabel(i18n.KO.SETUP_HOW_TO_USE_BODY, howto_card)
        howto_body.setWordWrap(True)
        howto_body.setStyleSheet(
            "color: #E5F4FF; line-height: 160%; padding-top: 4px;"
        )
        howto_card.body().addWidget(howto_body)
        self._howto_section.add_content_widget(howto_card)
        self._howto_section.toggled.connect(
            lambda expanded: _prefs.patch(howto_expanded=bool(expanded))
        )
        root.addWidget(self._howto_section)

        # 자동화 수준 — 올인원 모드 (#3) ---------------------------------
        auto_card = NeonCard(role="card-soft", parent=self)
        auto_title = QLabel(i18n.KO.AUTOMATION_TITLE, auto_card)
        auto_title.setStyleSheet(
            "color: #39FF14; font-weight: 700; letter-spacing: 1px;"
        )
        auto_card.body().addWidget(auto_title)

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
        auto_hint = QLabel(i18n.KO.AUTOMATION_HINT, auto_card)
        auto_hint.setProperty("role", "muted")
        auto_hint.setWordWrap(True)
        auto_hint.setStyleSheet("color: #7FB3D5; padding-top: 4px;")
        auto_card.body().addWidget(auto_hint)
        root.addWidget(auto_card)

        # 폴더/호기 2칸 ---------------------------------------------------
        row = QHBoxLayout()
        row.setSpacing(20)
        self.ref_group, self.ref_path_edit, self.ref_machine_edit = \
            self._make_machine_group(i18n.KO.SETUP_REF_GROUP)
        self.val_group, self.val_path_edit, self.val_machine_edit = \
            self._make_machine_group(i18n.KO.SETUP_VAL_GROUP)
        row.addWidget(self.ref_group)
        row.addWidget(self.val_group)
        root.addLayout(row)

        # 일부 슬롯만 진행 옵션 ------------------------------------------
        # None = 전체 진행.  버튼으로 기준 폴더의 슬롯을 스캔해 부분 선택.
        self._selected_slots: Optional[set] = None
        slot_row = QHBoxLayout()
        self.btn_select_slots = NeonButton(
            i18n.KO.SLOT_SELECT_BTN, role="ghost",
        )
        self.btn_select_slots.setToolTip(i18n.KO.SLOT_SELECT_BTN_TOOLTIP)
        self.btn_select_slots.clicked.connect(self._open_slot_select)
        self.slot_select_label = QLabel(i18n.KO.SLOT_SELECT_ALL_HINT, self)
        self.slot_select_label.setProperty("role", "muted")
        slot_row.addWidget(self.btn_select_slots)
        slot_row.addWidget(self.slot_select_label, stretch=1)
        root.addLayout(slot_row)

        # 매칭 설정 카드 ------------------------------------------------
        engine_card = NeonCard(role="card-soft", parent=self)
        eng_title = QLabel(i18n.KO.ENGINE_CARD_TITLE, engine_card)
        eng_title.setStyleSheet(
            "color: #39FF14; font-weight: 700; letter-spacing: 1px;"
        )
        engine_card.body().addWidget(eng_title)

        # 좌표 매칭 허용 오차 스핀박스 (항상 표시 — 기본 모드)
        _tol_row = QWidget(engine_card)
        _tol_layout = QHBoxLayout(_tol_row)
        _tol_layout.setContentsMargins(0, 0, 0, 0)
        _tol_layout.setSpacing(6)
        _tol_label = QLabel(i18n.KO.COORD_TOLERANCE_LABEL, _tol_row)
        _tol_label.setToolTip(i18n.KO.COORD_TOLERANCE_TOOLTIP)
        self.coord_tol_spin = QDoubleSpinBox(_tol_row)
        self.coord_tol_spin.setRange(10.0, 5000.0)
        self.coord_tol_spin.setSingleStep(50.0)
        self.coord_tol_spin.setDecimals(1)
        self.coord_tol_spin.setSuffix(" µm")
        self.coord_tol_spin.setValue(getattr(_prefs_now, "coord_tolerance", 500.0))
        self.coord_tol_spin.setToolTip(i18n.KO.COORD_TOLERANCE_TOOLTIP)
        _tol_layout.addWidget(_tol_label)
        _tol_layout.addWidget(self.coord_tol_spin)
        _tol_layout.addStretch()
        engine_card.body().addWidget(_tol_row)

        # 사진 중앙 30% 만 사용 — 기준·검증 공통 단일 토글
        self.check_center_crop = QCheckBox(i18n.KO.CENTER_CROP_LABEL, engine_card)
        self.check_center_crop.setToolTip(i18n.KO.CENTER_CROP_TOOLTIP)
        self.check_center_crop.setChecked(bool(getattr(_prefs_now, "center_crop", False)))
        engine_card.body().addWidget(self.check_center_crop)

        # 구형 모드 (유사도 엔진) — 접힌 상태로 기본 비활성 -----------------
        _last_engine = getattr(_prefs_now, "engine_mode", "coordinate")
        _legacy_expanded = _last_engine in ("basic", "efficiency")
        self._legacy_section = CollapsibleSection(
            open_label=i18n.KO.LEGACY_MODE_OPEN,
            close_label=i18n.KO.LEGACY_MODE_CLOSE,
            expanded=_legacy_expanded,
            parent=engine_card,
        )
        legacy_inner = QWidget(self._legacy_section)
        legacy_lay = QVBoxLayout(legacy_inner)
        legacy_lay.setContentsMargins(8, 6, 8, 6)
        legacy_lay.setSpacing(6)

        _legacy_hint = QLabel(i18n.KO.LEGACY_MODE_HINT, legacy_inner)
        _legacy_hint.setWordWrap(True)
        _legacy_hint.setStyleSheet("color: #7FB3D5; font-size: 12px;")
        legacy_lay.addWidget(_legacy_hint)

        self.radio_engine_basic = QRadioButton(i18n.KO.ENGINE_MODE_BASIC, legacy_inner)
        self.radio_engine_efficiency = QRadioButton(
            i18n.KO.ENGINE_MODE_EFFICIENCY, legacy_inner)
        if _last_engine == "efficiency":
            self.radio_engine_efficiency.setChecked(True)
        else:
            self.radio_engine_basic.setChecked(True)
        self._engine_group = QButtonGroup(legacy_inner)
        self._engine_group.setExclusive(True)
        self._engine_group.addButton(self.radio_engine_basic)
        self._engine_group.addButton(self.radio_engine_efficiency)
        legacy_lay.addWidget(self.radio_engine_basic)
        legacy_lay.addWidget(self.radio_engine_efficiency)

        # 임계치 슬라이더 (구형 모드 전용)
        sl_row = QHBoxLayout()
        sl_row.addWidget(QLabel(i18n.KO.SETUP_THRESHOLD_LABEL, legacy_inner))
        self.slider = NoWheelSlider(Qt.Orientation.Horizontal, legacy_inner)
        self.slider.setRange(0, 100)
        _last_prefs = _prefs.load()
        self.slider.setValue(int(round(_last_prefs.threshold * 100)))
        self.threshold_label = QLabel(f"{self.slider.value()} %", legacy_inner)
        self.threshold_label.setStyleSheet("color: #39FF14; font-weight: 700;")
        self.threshold_label.setFixedWidth(60)
        self.slider.valueChanged.connect(self._on_threshold_changed)
        sl_row.addWidget(self.slider, stretch=1)
        sl_row.addWidget(self.threshold_label)
        legacy_lay.addLayout(sl_row)

        self._legacy_section.add_content_widget(legacy_inner)
        engine_card.body().addWidget(self._legacy_section)
        root.addWidget(engine_card)

        root.addStretch(1)

        # 시작 / 업데이트 확인 버튼 -------------------------------------
        bar = QHBoxLayout()
        # 업데이트 확인은 좌측(보조), 검증 시작은 우측(주). 좌상단 도움말 메뉴 대체.
        self.update_btn = NeonButton(i18n.KO.MENU_CHECK_UPDATE, role="ghost")
        self.update_btn.setMinimumHeight(46)
        self.update_btn.clicked.connect(self.update_check_requested.emit)
        bar.addWidget(self.update_btn)
        # 개발자 모드(환경변수 AOI_DEV_MODE 또는 prefs.dev_mode)에서만 보이는
        # ‘개발자 벤치마크 / 정답 라벨’ 버튼 — 일반 사용자 화면에는 나타나지 않는다.
        # 앱 안에서 Ctrl+Shift+D 로 켜고 끌 수 있으며, 토글 시 버튼이 즉시
        # 나타나거나 사라진다(아래 _refresh_dev_buttons).
        self._action_bar = bar
        self.dev_bench_btn: NeonButton | None = None
        self.dev_label_btn: NeonButton | None = None
        bar.addStretch(1)
        self.start_btn = NeonButton(i18n.KO.BTN_START, role="primary")
        self.start_btn.setMinimumWidth(220)
        self.start_btn.setMinimumHeight(46)
        self.start_btn.clicked.connect(self._on_start)
        bar.addWidget(self.start_btn)
        root.addLayout(bar)
        self._refresh_dev_buttons()

        # 개발자 모드 토글 단축키 — 일반 사용자에게는 보이지 않는 진입점.
        self._dev_shortcut = QShortcut(QKeySequence("Ctrl+Shift+D"), self)
        self._dev_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._dev_shortcut.activated.connect(self._toggle_dev_mode)

        # 개발자 크레딧 (메인 화면) -------------------------------------
        credit = QLabel(i18n.KO.CREDIT, self)
        credit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credit.setStyleSheet("color: #7FB3D5; padding-top: 10px;")
        root.addWidget(credit)

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
        # 구형 섹션이 펼쳐진 상태에서만 유사도 엔진 모드 사용
        if self._legacy_section.is_expanded():
            if self.radio_engine_efficiency.isChecked():
                engine_mode = "efficiency"
            else:
                engine_mode = "basic"
        else:
            engine_mode = "coordinate"
        coord_tolerance = float(self.coord_tol_spin.value())
        center_crop = bool(self.check_center_crop.isChecked())
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
            center_crop=center_crop,
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
            center_crop=center_crop,
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
