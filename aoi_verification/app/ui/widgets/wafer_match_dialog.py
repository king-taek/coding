"""OCR 후 수동 매치 다이얼로그.

자동 인식으로 짝짓지 못한 KLA 사진(헤더)들을 **한 번에 모두** 보여주고, 각 사진
오른쪽의 드롭다운에서 매칭할 wafer(반대쪽 폴더/slot)를 고르게 한다.

- 입력 ``rows`` : ``[(folder_name, header_pixmap|None), ...]`` — KLA(OCR 대상) 쪽 폴더.
- 입력 ``options`` : 반대쪽 폴더명 목록(드롭다운 후보 = 매칭할 wafer/slot).
- 결과 ``selections`` : ``{folder_name: 선택한_반대쪽_폴더명_또는_""}``.
"""

from __future__ import annotations

from typing import Iterable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel,
                             QScrollArea, QVBoxLayout, QWidget)

from ... import i18n
from .neon_button import NeonButton
from .window_controls import add_fullscreen_shortcut, enable_window_controls


class WaferMatchDialog(QDialog):
    def __init__(self, rows, options: Iterable[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.WAFER_MATCH_TITLE)
        self.resize(780, 620)
        self._rows = list(rows)
        self._options = list(options)
        self._combos: dict[str, QComboBox] = {}
        enable_window_controls(self)
        add_fullscreen_shortcut(self)
        self._build()

    @property
    def selections(self) -> dict[str, str]:
        return {name: (combo.currentData() or "")
                for name, combo in self._combos.items()}

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        hint = QLabel(i18n.KO.WAFER_MATCH_HINT, self)
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        host = QWidget()
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)

        for name, pm in self._rows:
            col.addWidget(self._make_row(name, pm))
        col.addStretch(1)
        scroll.setWidget(host)
        root.addWidget(scroll, stretch=1)

        bar = QHBoxLayout()
        bar.addStretch(1)
        cancel = NeonButton(i18n.KO.BTN_CANCEL, role="ghost")
        cancel.clicked.connect(self.reject)
        ok = NeonButton(i18n.KO.BTN_OK, role="primary")
        ok.clicked.connect(self.accept)
        bar.addWidget(cancel)
        bar.addWidget(ok)
        root.addLayout(bar)

    def _make_row(self, name: str, pm) -> QWidget:
        row = QHBoxLayout()

        img = QLabel(self)
        img.setMinimumHeight(60)
        img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img.setStyleSheet("background: #050810; border: 1px solid #1F2A3F;")
        if pm is not None and not pm.isNull():
            img.setPixmap(pm.scaledToWidth(
                min(360, pm.width()),
                Qt.TransformationMode.SmoothTransformation))
        else:
            img.setText(name)
        row.addWidget(img, stretch=3)

        row.addWidget(QLabel("→", self))

        combo = QComboBox(self)
        combo.addItem(i18n.KO.WAFER_MATCH_NONE, "")
        for opt in self._options:
            combo.addItem(opt, opt)
        row.addWidget(combo, stretch=2)
        self._combos[name] = combo

        wrap = QWidget()
        wrap.setLayout(row)
        return wrap
