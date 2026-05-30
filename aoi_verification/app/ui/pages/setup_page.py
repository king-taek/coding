"""초기 입력 화면 (Setup) — 모드/폴더/호기/임계치 입력."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QFileDialog, QFormLayout,
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
    engine_mode: str = "basic"       # EngineMode.{BASIC,EFFICIENCY}
    center_crop: bool = False        # 사진 중앙 30% 만 사용 (기준·검증)
    persist_scores: bool = True      # 유사도 점수 디스크 캐시 — 항상 기본 적용
    accel_concurrency: int = 32      # 고효율 모드 동시 추론 수(in-flight)
    use_cpu: bool = True             # 고효율 장치 토글(테스트용)
    use_gpu: bool = True
    use_npu: bool = False            # 효율 모드 = CPU+GPU. NPU 비활성(코드만 보존).
    embed_batch: int = 1             # 정적 배치 B (1=끔)


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
            "color: #00D4FF; font-weight: 700; letter-spacing: 1px;"
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
            "color: #00D4FF; font-weight: 700; letter-spacing: 1px;"
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

        # 유사도 엔진 모드 + 강화 전처리 (#1/#10) -----------------------
        engine_card = NeonCard(role="card-soft", parent=self)
        engine_card.setToolTip(i18n.KO.ENGINE_MODE_TOOLTIP)
        eng_title = QLabel(i18n.KO.ENGINE_CARD_TITLE, engine_card)
        eng_title.setStyleSheet(
            "color: #00D4FF; font-weight: 700; letter-spacing: 1px;"
        )
        engine_card.body().addWidget(eng_title)

        self.radio_engine_basic = QRadioButton(i18n.KO.ENGINE_MODE_BASIC, engine_card)
        self.radio_engine_efficiency = QRadioButton(
            i18n.KO.ENGINE_MODE_EFFICIENCY, engine_card)
        _last_engine = getattr(_prefs_now, "engine_mode", "basic")
        if _last_engine == "efficiency":
            self.radio_engine_efficiency.setChecked(True)
        else:
            self.radio_engine_basic.setChecked(True)
        self._engine_group = QButtonGroup(engine_card)
        self._engine_group.setExclusive(True)
        self._engine_group.addButton(self.radio_engine_basic)
        self._engine_group.addButton(self.radio_engine_efficiency)
        engine_card.body().addWidget(self.radio_engine_basic)
        engine_card.body().addWidget(self.radio_engine_efficiency)

        # 동시 추론 수(in-flight)는 워크로드에 맞춰 자동 산정 — 사용자 설정 없음.

        pre_title = QLabel(i18n.KO.PRE_GROUP_TITLE, engine_card)
        pre_title.setToolTip(i18n.KO.PRE_GROUP_TOOLTIP)
        pre_title.setStyleSheet("color: #7FB3D5; padding-top: 6px;")
        engine_card.body().addWidget(pre_title)
        # 사진 중앙 30% 만 사용 — 기준·검증 공통 단일 토글 (#2/#5).
        self.check_center_crop = QCheckBox(i18n.KO.CENTER_CROP_LABEL, engine_card)
        self.check_center_crop.setToolTip(i18n.KO.CENTER_CROP_TOOLTIP)
        self.check_center_crop.setChecked(bool(getattr(_prefs_now, "center_crop", False)))
        # 유사도 점수 디스크 캐시(#5B)는 항상 기본 적용 — 사용자 토글 제거.
        engine_card.body().addWidget(self.check_center_crop)
        root.addWidget(engine_card)

        # 임계치 슬라이더 ------------------------------------------------
        slider_card = NeonCard(role="card-soft", parent=self)
        sl = QHBoxLayout()
        sl.addWidget(QLabel(i18n.KO.SETUP_THRESHOLD_LABEL, slider_card))
        self.slider = NoWheelSlider(Qt.Orientation.Horizontal, slider_card)
        self.slider.setRange(0, 100)
        # 마지막 사용 값(#14) 우선, 없으면 config 기본값
        _last_prefs = _prefs.load()
        self.slider.setValue(int(round(_last_prefs.threshold * 100)))
        self.threshold_label = QLabel(f"{self.slider.value()} %", slider_card)
        self.threshold_label.setStyleSheet("color: #00D4FF; font-weight: 700;")
        self.threshold_label.setFixedWidth(60)
        self.slider.valueChanged.connect(self._on_threshold_changed)
        sl.addWidget(self.slider, stretch=1)
        sl.addWidget(self.threshold_label)
        slider_card.body().addLayout(sl)
        root.addWidget(slider_card)

        root.addStretch(1)

        # 시작 / 업데이트 확인 버튼 -------------------------------------
        bar = QHBoxLayout()
        # 업데이트 확인은 좌측(보조), 검증 시작은 우측(주). 좌상단 도움말 메뉴 대체.
        self.update_btn = NeonButton(i18n.KO.MENU_CHECK_UPDATE, role="ghost")
        self.update_btn.setMinimumHeight(46)
        self.update_btn.clicked.connect(self.update_check_requested.emit)
        bar.addWidget(self.update_btn)
        # 개발자 모드(환경변수 AOI_DEV_MODE 또는 prefs.dev_mode)에서만 보이는
        # ‘개발자 벤치마크’ 진입 버튼 — 일반 사용자 화면에는 나타나지 않는다.
        try:
            from ..widgets.dev_benchmark_dialog import dev_mode_enabled
            if dev_mode_enabled():
                self.dev_bench_btn = NeonButton(i18n.KO.DEV_BENCH_BUTTON, role="ghost")
                self.dev_bench_btn.setMinimumHeight(46)
                self.dev_bench_btn.clicked.connect(self._open_dev_benchmark)
                bar.addWidget(self.dev_bench_btn)
        except Exception:
            pass
        bar.addStretch(1)
        self.start_btn = NeonButton(i18n.KO.BTN_START, role="primary")
        self.start_btn.setMinimumWidth(220)
        self.start_btn.setMinimumHeight(46)
        self.start_btn.clicked.connect(self._on_start)
        bar.addWidget(self.start_btn)
        root.addLayout(bar)

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

    def _open_dev_benchmark(self) -> None:
        """개발자 벤치마크 다이얼로그 — 매칭 가속 조합 실험(개발자 모드 전용)."""
        from ..widgets.dev_benchmark_dialog import DevBenchmarkDialog
        default_ref = self.ref_path_edit.text().strip()
        if not default_ref:
            # 마지막 입력 → 저장소의 ‘기준’ 예시 폴더 순으로 기본값을 채운다.
            from ...utils import paths as _paths
            default_ref = getattr(_prefs.load(), "last_ref_root", "") or ""
            if not default_ref:
                cand = _paths.resource_path("기준")
                if cand.is_dir():
                    default_ref = str(cand)
        default_val = self.val_path_edit.text().strip()
        dlg = DevBenchmarkDialog(self, default_ref=default_ref,
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
        if self.radio_engine_efficiency.isChecked():
            engine_mode = "efficiency"
        else:
            engine_mode = "basic"
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
