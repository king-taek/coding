"""초기 입력 화면 (Setup) — 모드/폴더/호기/임계치 입력."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                              QLabel, QLineEdit, QMessageBox, QRadioButton,
                              QSlider, QVBoxLayout, QWidget)

from ... import config, i18n
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
