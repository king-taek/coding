"""초기 입력 화면 (Setup) — 모드/폴더/호기/임계치 입력."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QButtonGroup, QFileDialog, QFormLayout, QGroupBox,
                              QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                              QRadioButton, QSlider, QVBoxLayout, QWidget)

from ... import config, i18n
from ...learning import evaluator as _evaluator
from ...learning import registry as _registry
from ...learning import triplet_model as _triplet
from ...learning.dataset import TrainingDataStore
from ...learning.trainer import TrainHeadWorker
from ..widgets.loading_overlay import LoadingOverlay
from ..widgets.neon_button import NeonButton
from ..widgets.neon_card import NeonCard


@dataclass
class SetupInput:
    mode: str        # "single" | "cross"
    ref_root: Path
    val_root: Path
    ref_machine: str
    val_machine: str
    threshold: float


class SetupPage(QWidget):
    """검증 시작 화면."""

    start_requested = pyqtSignal(object)     # SetupInput

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(20)

        title = QLabel(i18n.KO.SETUP_TITLE, self)
        title.setProperty("role", "title")
        root.addWidget(title)

        subtitle = QLabel(i18n.KO.SETUP_HINT, self)
        subtitle.setProperty("role", "subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # 사용 방법 안내 카드 -------------------------------------------
        howto_card = NeonCard(role="card-soft", parent=self)
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
        root.addWidget(howto_card)

        # 학습 모델 카드 ------------------------------------------------
        self._model_card = NeonCard(role="card-soft", parent=self)
        self._model_card.setToolTip(i18n.KO.MODEL_TOOLTIP)
        m_title = QLabel(i18n.KO.MODEL_CARD_TITLE, self._model_card)
        m_title.setStyleSheet(
            "color: #00D4FF; font-weight: 700; letter-spacing: 1px;"
        )
        self._model_card.body().addWidget(m_title)

        # 라디오 그룹 + 데이터 카운트 + 버튼은 _refresh_model_card 에서 갱신
        self._model_radios_host = QWidget(self._model_card)
        self._model_radios_layout = QVBoxLayout(self._model_radios_host)
        self._model_radios_layout.setContentsMargins(0, 4, 0, 4)
        self._model_radios_layout.setSpacing(4)
        self._model_card.body().addWidget(self._model_radios_host)

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
        self.btn_retrain.clicked.connect(self._on_retrain)
        self.btn_refresh_acc.clicked.connect(self._on_refresh_accuracy)
        self.btn_delete_model.clicked.connect(self._on_delete_model)
        bar.addWidget(self.btn_retrain)
        bar.addWidget(self.btn_refresh_acc)
        bar.addWidget(self.btn_delete_model)
        bar.addStretch(1)
        self._model_card.body().addLayout(bar)
        root.addWidget(self._model_card)

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

        # 임계치 슬라이더 ------------------------------------------------
        slider_card = NeonCard(role="card-soft", parent=self)
        sl = QHBoxLayout()
        sl.addWidget(QLabel(i18n.KO.SETUP_THRESHOLD_LABEL, slider_card))
        self.slider = QSlider(Qt.Orientation.Horizontal, slider_card)
        self.slider.setRange(0, 100)
        self.slider.setValue(int(config.CONFIG.default_threshold * 100))
        self.threshold_label = QLabel(f"{self.slider.value()} %", slider_card)
        self.threshold_label.setStyleSheet("color: #00D4FF; font-weight: 700;")
        self.threshold_label.setFixedWidth(60)
        self.slider.valueChanged.connect(
            lambda v: self.threshold_label.setText(f"{v} %")
        )
        sl.addWidget(self.slider, stretch=1)
        sl.addWidget(self.threshold_label)
        slider_card.body().addLayout(sl)
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
        self.start_requested.emit(SetupInput(
            mode=mode,
            ref_root=ref_root,
            val_root=val_root,
            ref_machine=ref_machine,
            val_machine=val_machine,
            threshold=threshold,
        ))

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

        # 기본 모드 라디오는 항상 존재
        rb_basic = QRadioButton(i18n.KO.MODEL_OPTION_BASIC, self._model_card)
        rb_basic.setProperty("model_name", _registry.BASIC)
        self._model_group.addButton(rb_basic)
        self._model_radios_layout.addWidget(rb_basic)

        # 학습 모델 목록
        for info in _registry.list_models():
            num_evals = info.num_evaluations
            num_pairs = info.num_train_pairs
            if info.accuracy_pct is not None:
                hit5 = info.accuracy_pct
            elif info.meta.get("hit_at_5") is not None and num_evals >= _evaluator.MIN_EVALS_FOR_LABEL:
                hit5 = int(round(float(info.meta["hit_at_5"]) * 100))
            else:
                hit5 = None

            if hit5 is None:
                text = i18n.KO.MODEL_OPTION_NO_ACC_FMT.format(
                    name=info.name, pairs=num_pairs,
                )
            else:
                text = i18n.KO.MODEL_OPTION_FMT.format(
                    name=info.name, pairs=num_pairs,
                    hit5=hit5, evals=num_evals,
                )
            rb = QRadioButton(text, self._model_card)
            rb.setProperty("model_name", info.name)
            self._model_group.addButton(rb)
            self._model_radios_layout.addWidget(rb)

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
            lambda e, t, loss: self._loading.set_progress(
                e, t, i18n.KO.LOAD_TRAIN_FMT.format(
                    epoch=e, total=t, loss=float(loss),
                ),
            )
        )
        self._train_worker.signals.finished.connect(self._on_train_finished)
        self._train_worker.signals.failed.connect(self._on_train_failed)
        self._train_worker.start()

    def _on_train_finished(self, new_name: str) -> None:
        self._loading.hide_overlay()
        QMessageBox.information(
            self, i18n.KO.APP_TITLE,
            i18n.KO.TRAIN_DONE_FMT.format(name=new_name),
        )
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
