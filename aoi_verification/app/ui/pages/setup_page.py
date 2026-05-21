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
from ...learning import evaluator as _evaluator
from ...learning import registry as _registry
from ...learning import triplet_model as _triplet
from ...learning.dataset import TrainingDataStore
from ...learning.trainer import TrainHeadWorker
from ...utils import prefs as _prefs
from ...utils.prefs import AutomationLevel
from ..widgets.collapsible_section import CollapsibleSection
from ..widgets.loading_overlay import LoadingOverlay
from ..widgets.neon_button import NeonButton
from ..widgets.neon_card import NeonCard
from ..widgets.no_wheel_slider import NoWheelSlider


@dataclass
class SetupInput:
    mode: str        # "single" | "cross"
    ref_root: Path
    val_root: Path
    ref_machine: str
    val_machine: str
    threshold: float
    automation_level: str = AutomationLevel.MANUAL
    # 유사도 엔진 + 강화/KLA 전처리 (계산 전용).
    engine_mode: str = "basic"       # EngineMode.{BASIC,FAST}
    center20_ref: bool = False       # 기준 사진 중앙 20% 만 사용
    center20_val: bool = False       # 검증 사진 중앙 20% 만 사용
    pre_grayscale: bool = False
    pre_contrast: bool = False
    kla_crop: bool = False
    persist_scores: bool = False     # 유사도 점수 디스크 캐시 (basic 엔진)


class SetupPage(QWidget):
    """검증 시작 화면."""

    start_requested = pyqtSignal(object)             # SetupInput

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

        # 학습 모델 카드 ------------------------------------------------
        self._model_card = NeonCard(role="card-soft", parent=self)
        self._model_card.setToolTip(i18n.KO.MODEL_TOOLTIP)
        m_title = QLabel(i18n.KO.MODEL_CARD_TITLE, self._model_card)
        m_title.setStyleSheet(
            "color: #00D4FF; font-weight: 700; letter-spacing: 1px;"
        )
        self._model_card.body().addWidget(m_title)

        # 라디오 그룹 + 데이터 카운트 + 버튼은 _refresh_model_card 에서 갱신.
        # 모델이 많으면 (>5) 카드 자체가 너무 길어지므로 ScrollArea 로 감싸서
        # 최대 높이를 두고, 그 이상은 세로 스크롤로 선택하게 한다.
        self._model_scroll = QScrollArea(self._model_card)
        self._model_scroll.setWidgetResizable(True)
        self._model_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._model_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        self._model_radios_host = QWidget()
        self._model_radios_layout = QVBoxLayout(self._model_radios_host)
        self._model_radios_layout.setContentsMargins(0, 4, 0, 4)
        self._model_radios_layout.setSpacing(4)
        self._model_scroll.setWidget(self._model_radios_host)
        self._model_card.body().addWidget(self._model_scroll)

        self._model_group = QButtonGroup(self._model_card)
        self._model_group.setExclusive(True)
        self._model_group.buttonClicked.connect(self._on_model_radio_clicked)

        self._model_data_label = QLabel("", self._model_card)
        self._model_data_label.setProperty("role", "muted")
        self._model_card.body().addWidget(self._model_data_label)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_retrain = NeonButton(i18n.KO.BTN_RETRAIN, role="primary")
        self.btn_refresh_acc = NeonButton(i18n.KO.BTN_REFRESH_ACC, role="ghost")
        self.btn_delete_model = NeonButton(i18n.KO.BTN_DELETE_MODEL, role="danger")
        self.btn_export_model = NeonButton(i18n.KO.BTN_EXPORT_MODEL, role="ghost")
        self.btn_import_model = NeonButton(i18n.KO.BTN_IMPORT_MODEL, role="ghost")
        self.btn_retrain.clicked.connect(self._on_retrain)
        self.btn_refresh_acc.clicked.connect(self._on_refresh_accuracy)
        self.btn_delete_model.clicked.connect(self._on_delete_model)
        self.btn_export_model.clicked.connect(self._on_export_model)
        self.btn_import_model.clicked.connect(self._on_import_model)
        bar.addWidget(self.btn_retrain)
        bar.addWidget(self.btn_refresh_acc)
        bar.addWidget(self.btn_delete_model)
        bar.addWidget(self.btn_export_model)
        bar.addWidget(self.btn_import_model)
        bar.addStretch(1)
        self._model_card.body().addLayout(bar)
        root.addWidget(self._model_card)

        # #4 학습 모델 기능 숨김 — 모델 선택/버튼은 감추고 누적 데이터 개수만
        # 표시한다.  위젯은 살려두어 _refresh_model_card 가 안전하게 동작.
        self._model_scroll.hide()
        for _b in (self.btn_retrain, self.btn_refresh_acc, self.btn_delete_model,
                   self.btn_export_model, self.btn_import_model):
            _b.hide()

        # 학습 워커 / 로딩 상태
        self._loading = LoadingOverlay(self)
        self._train_worker: TrainHeadWorker | None = None

        # 모드 선택 ------------------------------------------------------
        mode_card = NeonCard(role="card-soft", parent=self)
        h = QHBoxLayout()
        h.setSpacing(20)
        h.addWidget(QLabel(i18n.KO.SETUP_MODE_LABEL, mode_card))
        self.radio_single = QRadioButton(i18n.KO.SETUP_MODE_SINGLE, mode_card)
        self.radio_cross = QRadioButton(i18n.KO.SETUP_MODE_CROSS, mode_card)
        self.radio_single.setChecked(True)
        h.addWidget(self.radio_single)
        h.addWidget(self.radio_cross)
        h.addStretch(1)
        mode_card.body().addLayout(h)
        root.addWidget(mode_card)

        # 자동화 수준 — 올인원 모드 (#3) ---------------------------------
        auto_card = NeonCard(role="card-soft", parent=self)
        auto_title = QLabel(i18n.KO.AUTOMATION_TITLE, auto_card)
        auto_title.setStyleSheet(
            "color: #00D4FF; font-weight: 700; letter-spacing: 1px;"
        )
        auto_card.body().addWidget(auto_title)

        self.radio_auto_manual = QRadioButton(i18n.KO.AUTOMATION_MANUAL, auto_card)
        self.radio_auto_user = QRadioButton(i18n.KO.AUTOMATION_USER_SELECT, auto_card)
        self.radio_auto_all = QRadioButton(i18n.KO.AUTOMATION_AUTO_ALL, auto_card)
        # 마지막 선택 복원.
        _last_auto = getattr(_prefs_now, "automation_level", AutomationLevel.MANUAL)
        if _last_auto == AutomationLevel.USER_SELECT:
            self.radio_auto_user.setChecked(True)
        elif _last_auto == AutomationLevel.AUTO_ALL:
            self.radio_auto_all.setChecked(True)
        else:
            self.radio_auto_manual.setChecked(True)
        for rb in (self.radio_auto_manual, self.radio_auto_user, self.radio_auto_all):
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
        self.radio_engine_fast = QRadioButton(i18n.KO.ENGINE_MODE_FAST, engine_card)
        _last_engine = getattr(_prefs_now, "engine_mode", "basic")
        if _last_engine == "fast":
            self.radio_engine_fast.setChecked(True)
        else:
            self.radio_engine_basic.setChecked(True)
        self._engine_group = QButtonGroup(engine_card)
        self._engine_group.setExclusive(True)
        self._engine_group.addButton(self.radio_engine_basic)
        self._engine_group.addButton(self.radio_engine_fast)
        engine_card.body().addWidget(self.radio_engine_basic)
        engine_card.body().addWidget(self.radio_engine_fast)

        pre_title = QLabel(i18n.KO.PRE_GROUP_TITLE, engine_card)
        pre_title.setToolTip(i18n.KO.PRE_GROUP_TOOLTIP)
        pre_title.setStyleSheet("color: #7FB3D5; padding-top: 6px;")
        engine_card.body().addWidget(pre_title)
        # 중앙 20% 만 사용 — 기준/검증 독립 토글 (#2/#5).
        self.check_center20_ref = QCheckBox(i18n.KO.CENTER20_REF_LABEL, engine_card)
        self.check_center20_val = QCheckBox(i18n.KO.CENTER20_VAL_LABEL, engine_card)
        self.check_center20_ref.setToolTip(i18n.KO.CENTER20_TOOLTIP)
        self.check_center20_val.setToolTip(i18n.KO.CENTER20_TOOLTIP)
        self.check_center20_ref.setChecked(bool(getattr(_prefs_now, "center20_ref", False)))
        self.check_center20_val.setChecked(bool(getattr(_prefs_now, "center20_val", False)))
        self.check_pre_grayscale = QCheckBox(i18n.KO.PRE_GRAYSCALE_LABEL, engine_card)
        self.check_pre_contrast = QCheckBox(i18n.KO.PRE_CONTRAST_LABEL, engine_card)
        self.check_kla_crop = QCheckBox(i18n.KO.KLA_CROP_LABEL, engine_card)
        self.check_pre_grayscale.setChecked(bool(getattr(_prefs_now, "pre_grayscale", False)))
        self.check_pre_contrast.setChecked(bool(getattr(_prefs_now, "pre_contrast", False)))
        self.check_kla_crop.setChecked(bool(getattr(_prefs_now, "kla_crop", False)))
        # 유사도 점수 디스크 캐시 (#5B) — basic 엔진에서 재실행 시 재계산 생략.
        self.check_persist_scores = QCheckBox(
            i18n.KO.PERSIST_SCORES_LABEL, engine_card)
        self.check_persist_scores.setToolTip(i18n.KO.PERSIST_SCORES_TOOLTIP)
        self.check_persist_scores.setChecked(
            bool(getattr(_prefs_now, "persist_scores", False)))
        for _c in (self.check_center20_ref, self.check_center20_val,
                   self.check_pre_grayscale, self.check_pre_contrast,
                   self.check_kla_crop, self.check_persist_scores):
            engine_card.body().addWidget(_c)
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

        # 빠른 모드 (썸네일 화질 낮춤) ----------------------------------
        speed_row = QHBoxLayout()
        self.check_speed_mode = QCheckBox(i18n.KO.SPEED_MODE_LABEL, slider_card)
        self.check_speed_mode.setToolTip(i18n.KO.SPEED_MODE_TOOLTIP)
        self.check_speed_mode.setChecked(bool(_last_prefs.speed_mode))
        self.check_speed_mode.toggled.connect(
            lambda on: _prefs.patch(speed_mode=bool(on))
        )
        speed_row.addWidget(self.check_speed_mode)
        speed_row.addStretch(1)
        slider_card.body().addLayout(speed_row)
        root.addWidget(slider_card)

        root.addStretch(1)

        # 시작 버튼 -----------------------------------------------------
        bar = QHBoxLayout()
        bar.addStretch(1)
        self.start_btn = NeonButton(i18n.KO.BTN_START, role="primary")
        self.start_btn.setMinimumWidth(220)
        self.start_btn.setMinimumHeight(46)
        self.start_btn.clicked.connect(self._on_start)
        bar.addWidget(self.start_btn)
        root.addLayout(bar)

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

    def _on_threshold_changed(self, v: int) -> None:
        self.threshold_label.setText(f"{v} %")
        _prefs.patch(threshold=v / 100.0)

    # ------------------------------------------------------------------
    def _on_start(self) -> None:
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

        mode = "cross" if self.radio_cross.isChecked() else "single"
        threshold = self.slider.value() / 100.0
        if self.radio_auto_all.isChecked():
            automation = AutomationLevel.AUTO_ALL
        elif self.radio_auto_user.isChecked():
            automation = AutomationLevel.USER_SELECT
        else:
            automation = AutomationLevel.MANUAL
        engine_mode = "fast" if self.radio_engine_fast.isChecked() else "basic"
        center20_ref = bool(self.check_center20_ref.isChecked())
        center20_val = bool(self.check_center20_val.isChecked())
        pre_grayscale = bool(self.check_pre_grayscale.isChecked())
        pre_contrast = bool(self.check_pre_contrast.isChecked())
        kla_crop = bool(self.check_kla_crop.isChecked())
        persist_scores = bool(self.check_persist_scores.isChecked())

        # 고속 모드를 골랐는데 의존성(hnswlib 등)이 없으면 조용히 기본 모드로
        # 폴백돼 "속도 차이가 없다"는 혼란을 준다.  설치를 안내하고, 설치 전에는
        # 세션 시작을 보류한다 (사용자가 '기본 모드로 진행' 을 고르면 그대로 진행).
        if engine_mode == "fast" and not self._ensure_fast_ready():
            return
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
            center20_ref=center20_ref,
            center20_val=center20_val,
            pre_grayscale=pre_grayscale,
            pre_contrast=pre_contrast,
            kla_crop=kla_crop,
            persist_scores=persist_scores,
        )
        self.start_requested.emit(SetupInput(
            mode=mode,
            ref_root=ref_root,
            val_root=val_root,
            ref_machine=ref_machine,
            val_machine=val_machine,
            threshold=threshold,
            automation_level=automation,
            engine_mode=engine_mode,
            center20_ref=center20_ref,
            center20_val=center20_val,
            pre_grayscale=pre_grayscale,
            pre_contrast=pre_contrast,
            kla_crop=kla_crop,
            persist_scores=persist_scores,
        ))

    # ------------------------------------------------------------------
    def _ensure_fast_ready(self) -> bool:
        """고속 모드 의존성 확인.  준비됐으면 True(세션 시작 진행).

        미설치면 설치 안내 모달을 띄우고, 사용자가 '기본 모드로 진행' 을 고를
        때만 True 를 반환한다 (이 경우 엔진은 기본으로 폴백).  '지금 설치' 를
        고르면 백그라운드 설치를 시작하고 False 를 반환(세션 보류) — 설치가
        끝나면 다시 [검증 시작] 을 누르도록 안내한다.
        """
        from ...learning import fast_deps_installer as _fdi
        if _fdi.fast_ready():
            return True
        pkgs = _fdi.missing_packages()
        if not pkgs:
            return True
        body = i18n.KO.FAST_DEPS_BODY_FMT.format(pkgs=", ".join(pkgs))
        if "openvino" in pkgs:
            body += i18n.KO.FAST_DEPS_NOTE_OPENVINO
        box = QMessageBox(self)
        box.setWindowTitle(i18n.KO.FAST_DEPS_TITLE)
        box.setText(body)
        btn_install = box.addButton(i18n.KO.FAST_DEPS_BTN_INSTALL,
                                    QMessageBox.ButtonRole.AcceptRole)
        btn_basic = box.addButton(i18n.KO.FAST_DEPS_BTN_BASIC,
                                  QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_basic:
            return True                       # 기본 모드로 폴백 진행 (사용자 선택)
        if clicked is btn_install:
            self._start_fast_install(pkgs)
            return False                      # 설치 후 다시 시작하도록 보류
        return False                          # 취소

    def _start_fast_install(self, packages: list) -> None:
        from ...learning.fast_deps_installer import FastDepsInstallWorker
        self._loading.show_overlay(
            i18n.KO.FAST_DEPS_INSTALLING.format(line="")
        )
        self._fast_install_worker = FastDepsInstallWorker(packages, parent=self)
        self._fast_install_worker.signals.progress.connect(
            lambda line: self._loading.show_overlay(
                i18n.KO.FAST_DEPS_INSTALLING.format(line=line[-80:])
            )
        )
        self._fast_install_worker.signals.finished.connect(
            self._on_fast_install_finished
        )
        self._fast_install_worker.start()

    def _on_fast_install_finished(self, ok: bool, message: str) -> None:
        import importlib
        from ...learning import fast_deps_installer as _fdi
        # 방금 설치된 패키지를 현재 프로세스가 import 할 수 있도록 finder 캐시 갱신.
        importlib.invalidate_caches()
        self._loading.hide_overlay()
        if not ok:
            QMessageBox.warning(
                self, i18n.KO.FAST_DEPS_TITLE,
                i18n.KO.FAST_DEPS_FAILED_FMT.format(error=message),
            )
            return
        # 설치 성공 — 현재 프로세스에서 즉시 import 되면 재시작 불필요.
        msg = (i18n.KO.FAST_DEPS_DONE if _fdi.fast_ready()
               else i18n.KO.FAST_DEPS_DONE_RESTART)
        QMessageBox.information(self, i18n.KO.FAST_DEPS_TITLE, msg)

    # ------------------------------------------------------------------
    def apply_state(self, ref_root: str, val_root: str,
                    ref_machine: str, val_machine: str,
                    mode: str, threshold: float) -> None:
        self.ref_path_edit.setText(ref_root)
        self.val_path_edit.setText(val_root)
        self.ref_machine_edit.setText(ref_machine)
        self.val_machine_edit.setText(val_machine)
        if mode == "cross":
            self.radio_cross.setChecked(True)
        else:
            self.radio_single.setChecked(True)
        self.slider.setValue(int(threshold * 100))

    # ==================================================================
    # 학습 모델 카드 — 외부에서 호출 가능
    # ==================================================================
    def refresh_models(self) -> None:
        """모델 목록과 데이터 카운트, 라디오 선택을 다시 그린다."""
        # 라디오 초기화
        for b in self._model_group.buttons():
            self._model_group.removeButton(b)
            b.setParent(None)
            b.deleteLater()
        while self._model_radios_layout.count():
            it = self._model_radios_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

        torch_available = _triplet.is_available()
        active = _registry.get_active()

        # 기본 모드 라디오 — 별도로 basic.jsonl 을 집계해 같은 형식으로 표기
        basic_metrics = _evaluator.aggregate(_registry.BASIC)
        if basic_metrics.has_enough and basic_metrics.picks > 0:
            basic_text = i18n.KO.MODEL_OPTION_BASELINE_FMT.format(
                hit5=int(round(basic_metrics.hit_at_5 * 100)),
                lo=int(round(basic_metrics.hit_at_5_lo * 100)),
                hi=int(round(basic_metrics.hit_at_5_hi * 100)),
                evals=basic_metrics.num_evaluations,
            )
        else:
            basic_text = i18n.KO.MODEL_OPTION_BASIC

        rb_basic = QRadioButton(basic_text, self._model_card)
        rb_basic.setProperty("model_name", _registry.BASIC)
        self._model_group.addButton(rb_basic)
        self._model_radios_layout.addWidget(rb_basic)

        baseline_hit5 = (basic_metrics.hit_at_5
                         if basic_metrics.has_enough else None)

        # 학습 모델 목록
        for info in _registry.list_models():
            num_evals = info.num_evaluations
            num_pairs = info.num_train_pairs
            metrics_dict = info.meta
            hit5 = None
            if num_evals >= _evaluator.MIN_EVALS_FOR_LABEL and metrics_dict.get("hit_at_5") is not None:
                hit5 = int(round(float(metrics_dict["hit_at_5"]) * 100))
            elif info.accuracy_pct is not None:
                hit5 = info.accuracy_pct

            if hit5 is None:
                text = i18n.KO.MODEL_OPTION_NO_ACC_FMT.format(
                    name=info.name, pairs=num_pairs,
                )
            else:
                lo_val = metrics_dict.get("hit_at_5_lo")
                hi_val = metrics_dict.get("hit_at_5_hi")
                # 메타에 CI 가 없는 (옛 모델/import 한 모델) 경우 즉시 계산
                if not lo_val and not hi_val:
                    picks = int(metrics_dict.get("picks", 0))
                    successes = int(round(float(metrics_dict.get("hit_at_5", 0.0)) * picks))
                    lo_f, hi_f = _evaluator.wilson_interval(successes, picks)
                else:
                    lo_f, hi_f = float(lo_val or 0.0), float(hi_val or 0.0)
                lo = int(round(lo_f * 100))
                hi = int(round(hi_f * 100))
                text = i18n.KO.MODEL_OPTION_FMT.format(
                    name=info.name, pairs=num_pairs,
                    hit5=hit5, lo=lo, hi=hi, evals=num_evals,
                )
                # 기본 모드 대비 델타 (#6)
                if baseline_hit5 is not None:
                    delta = round((hit5 / 100.0 - baseline_hit5) * 100)
                    sign = "+" if delta >= 0 else ""
                    text += i18n.KO.MODEL_DELTA_FMT.format(
                        sign=sign, delta=int(delta),
                    )

                # 최약 슬롯 (#7)
                per_slot = metrics_dict.get("per_slot") or {}
                weakest = None
                for s, v in per_slot.items():
                    p = int(v.get("picks", 0)) if isinstance(v, dict) else 0
                    h = float(v.get("hit_at_5", 0.0)) if isinstance(v, dict) else 0.0
                    if p >= 3 and (weakest is None or h < weakest[1]):
                        weakest = (s, h, p)
                if weakest is not None:
                    text += "\n      " + i18n.KO.MODEL_WEAKEST_SLOT_FMT.format(
                        slot=weakest[0],
                        hit5=int(round(weakest[1] * 100)),
                        picks=weakest[2],
                    )

            rb = QRadioButton(text, self._model_card)
            rb.setProperty("model_name", info.name)
            self._model_group.addButton(rb)
            self._model_radios_layout.addWidget(rb)

        # 모델 라디오 ScrollArea 의 높이 정책 (#4):
        # - 최소 3 줄은 항상 보이도록 minimumHeight 보장.
        # - 5 개를 넘으면 최대 5 줄까지만 보이고 그 이상은 세로 스크롤.
        # - 1~5 개면 자연 높이 (minimum 만 보장).
        radio_count = len(self._model_group.buttons())
        if radio_count > 0:
            sample = self._model_group.buttons()[0]
            row_h = max(sample.sizeHint().height(),
                        sample.minimumSizeHint().height(), 28)
            min_h = row_h * 3 + 16
            self._model_scroll.setMinimumHeight(min_h)
            if radio_count > 5:
                max_h = row_h * 5 + 24
                self._model_scroll.setMaximumHeight(max_h)
            else:
                self._model_scroll.setMaximumHeight(16777215)

        # 활성 모델 표시
        for b in self._model_group.buttons():
            if b.property("model_name") == active:
                b.setChecked(True)
                break
        else:
            # active 모델이 사라진 경우 basic 으로 fallback
            rb_basic.setChecked(True)
            _registry.set_active(_registry.BASIC)

        # 데이터 카운트
        try:
            n = TrainingDataStore().count()
        except Exception:
            n = 0
        self._model_data_label.setText(
            i18n.KO.MODEL_DATA_COUNT_FMT.format(n=n)
        )

        # torch 미설치 시 학습/삭제 비활성, 안내
        if not torch_available:
            self.btn_retrain.setEnabled(False)
            self.btn_refresh_acc.setEnabled(False)
            self.btn_delete_model.setEnabled(False)
            self._model_data_label.setText(i18n.KO.MODEL_NO_TORCH)
        else:
            self.btn_retrain.setEnabled(n >= 5)
            has_models = bool(_registry.list_models())
            self.btn_refresh_acc.setEnabled(has_models)
            self.btn_delete_model.setEnabled(
                has_models and active != _registry.BASIC
            )

    # ------------------------------------------------------------------
    def _on_model_radio_clicked(self, button) -> None:
        name = button.property("model_name") or _registry.BASIC
        _registry.set_active(str(name))
        # 삭제 가능 여부 즉시 갱신
        self.btn_delete_model.setEnabled(
            bool(_registry.list_models()) and str(name) != _registry.BASIC
        )

    # ------------------------------------------------------------------
    def _on_retrain(self) -> None:
        if not _triplet.is_available():
            QMessageBox.information(
                self, i18n.KO.APP_TITLE, i18n.KO.MODEL_NO_TORCH,
            )
            return
        store = TrainingDataStore()
        n = store.count()
        if n < 5:
            QMessageBox.information(
                self, i18n.KO.APP_TITLE, i18n.KO.TRAIN_NEED_MORE_DATA,
            )
            return
        r = QMessageBox.question(
            self, i18n.KO.TRAIN_CONFIRM_TITLE,
            i18n.KO.TRAIN_CONFIRM_BODY_FMT.format(n=n),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if r != QMessageBox.StandardButton.Yes:
            return

        self._loading.show_overlay(
            i18n.KO.LOAD_BACKBONE_FMT.format(done=0, total=n)
        )
        self._train_worker = TrainHeadWorker(store, parent=self)
        self._train_worker.signals.backbone_progress.connect(
            lambda d, t: self._loading.set_progress(
                d, t, i18n.KO.LOAD_BACKBONE_FMT.format(done=d, total=t),
            )
        )
        self._train_worker.signals.epoch_progress.connect(
            lambda e, t, loss: (self._loading.set_progress(
                e, t, i18n.KO.LOAD_TRAIN_FMT.format(
                    epoch=e, total=t, loss=float(loss),
                ),
            ), self._loading.push_sparkline(float(loss)))
        )
        self._train_worker.signals.finished.connect(self._on_train_finished)
        self._train_worker.signals.failed.connect(self._on_train_failed)
        self._train_worker.start()

    def _on_train_finished(self, result) -> None:
        self._loading.hide_overlay()
        # result 는 TrainResult dataclass (name, activated, hit_at_5_*).
        try:
            name = getattr(result, "name", "") or str(result)
            activated = bool(getattr(result, "activated", True))
        except Exception:
            name, activated = str(result), True
        if activated:
            msg = i18n.KO.TRAIN_DONE_FMT.format(name=name)
        else:
            new_h = int(round(getattr(result, "hit_at_5_new", 0.0) * 100))
            base_h = int(round(getattr(result, "hit_at_5_basic", 0.0) * 100))
            msg = i18n.KO.TRAIN_KEPT_BASIC_FMT.format(
                name=name, new=new_h, basic=base_h,
            )
        QMessageBox.information(self, i18n.KO.APP_TITLE, msg)
        self.refresh_models()

    def _on_train_failed(self, msg: str) -> None:
        self._loading.hide_overlay()
        QMessageBox.warning(
            self, i18n.KO.APP_TITLE,
            i18n.KO.TRAIN_FAIL_FMT.format(error=msg),
        )
        self.refresh_models()

    # ------------------------------------------------------------------
    def _on_refresh_accuracy(self) -> None:
        outcomes = _evaluator.refresh_accuracy()
        renamed = sum(1 for o in outcomes if o.renamed_from)
        if not outcomes:
            QMessageBox.information(
                self, i18n.KO.APP_TITLE, i18n.KO.ACC_REFRESH_NO_CHANGE,
            )
        else:
            QMessageBox.information(
                self, i18n.KO.APP_TITLE,
                i18n.KO.ACC_REFRESH_DONE_FMT.format(renamed=renamed),
            )
        self.refresh_models()

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def stop_training(self) -> None:
        """학습 워커 안전 종료 (창 닫힘/세션 종료 시 호출)."""
        if self._train_worker is not None and self._train_worker.isRunning():
            self._train_worker.stop()
            self._train_worker.wait(2000)

    # ------------------------------------------------------------------
    def _on_export_model(self) -> None:
        active = _registry.get_active()
        if active == _registry.BASIC:
            return
        dst, _ = QFileDialog.getSaveFileName(
            self, i18n.KO.EXPORT_DIALOG_TITLE, f"{active}.zip",
            "AOI Model (*.zip)",
        )
        if not dst:
            return
        try:
            out = _registry.export_model(active, Path(dst))
        except Exception as exc:
            QMessageBox.warning(
                self, i18n.KO.APP_TITLE,
                i18n.KO.EXPORT_FAIL_FMT.format(error=str(exc)),
            )
            return
        QMessageBox.information(
            self, i18n.KO.APP_TITLE,
            i18n.KO.EXPORT_DONE_FMT.format(path=str(out)),
        )

    def _on_import_model(self) -> None:
        src, _ = QFileDialog.getOpenFileName(
            self, i18n.KO.IMPORT_DIALOG_TITLE, "",
            "AOI Model (*.zip)",
        )
        if not src:
            return
        try:
            name = _registry.import_model(Path(src))
        except Exception as exc:
            QMessageBox.warning(
                self, i18n.KO.APP_TITLE,
                i18n.KO.IMPORT_FAIL_FMT.format(error=str(exc)),
            )
            return
        try:
            from ...learning import embedder as _emb
            _emb.invalidate_caches()
        except Exception:
            pass
        QMessageBox.information(
            self, i18n.KO.APP_TITLE,
            i18n.KO.IMPORT_DONE_FMT.format(name=name),
        )
        self.refresh_models()

    # ------------------------------------------------------------------
    def _on_delete_model(self) -> None:
        active = _registry.get_active()
        if active == _registry.BASIC:
            return
        r = QMessageBox.question(
            self, i18n.KO.DELETE_CONFIRM_TITLE,
            i18n.KO.DELETE_CONFIRM_BODY_FMT.format(name=active),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        _registry.delete_model(active)
        # 임베더 캐시 무효화
        try:
            from ...learning import embedder as _emb
            _emb.invalidate_caches()
        except Exception:
            pass
        self.refresh_models()
