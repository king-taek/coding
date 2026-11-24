"""Overkill 튜닝 — 보고서 내보내기 설정(현택님 2026-11-24 안 8).

고른 사진(꼭 잡기 · Overkill)은 늘 넣는다.  견본 사진 · Scan 사진 · Defect Map · 사진 크기만 고른다.
처음 값은 현택님 답: 빠짐 · 남음 견본, Scan 사진, Defect Map 모두 넣고 400px.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtWidgets import QButtonGroup, QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ... import i18n
from .neon_button import NeonButton

_KO = i18n.KO
SIZES = (200, 400, 800)


@dataclass
class ReportOptions:
    drop_samples: bool = True
    keep_samples: bool = True
    scan: bool = True
    map: bool = True
    px: int = 400


class ReportOptionsDialog(QDialog):
    def __init__(self, opts: ReportOptions, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_KO.REPORT_OPTIONS_TITLE)
        self.opts = opts
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(10)
        title = QLabel(_KO.REPORT_OPTIONS_TITLE, self)
        title.setStyleSheet("font-weight: 700;")
        root.addWidget(title)
        self.checks = {}
        for key, text in (("drop_samples", _KO.REPORT_OPT_DROP), ("keep_samples", _KO.REPORT_OPT_KEEP),
                          ("scan", _KO.REPORT_OPT_SCAN), ("map", _KO.REPORT_OPT_MAP)):
            cb = QCheckBox(text, self)
            cb.setChecked(getattr(opts, key))
            root.addWidget(cb)
            self.checks[key] = cb
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(QLabel(_KO.REPORT_OPT_SIZE, self))
        row.addSpacing(8)
        self.size_btns = {}
        group = QButtonGroup(self)
        for n in SIZES:
            b = QPushButton(_KO.REPORT_OPT_SIZE_FMT.format(n=n), self)
            b.setProperty("role", "ovBoxToggle")
            b.setCheckable(True)
            b.setAutoDefault(False)
            b.setChecked(n == opts.px)
            group.addButton(b)
            row.addWidget(b)
            self.size_btns[n] = b
        row.addStretch(1)
        root.addLayout(row)
        foot = QHBoxLayout()
        foot.addStretch(1)
        cancel = NeonButton(_KO.OVERKILL_PICK_BTN_CANCEL, role="ghost")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        foot.addWidget(cancel)
        self.save_btn = NeonButton(_KO.REPORT_OPT_SAVE, role="primary")
        self.save_btn.setAutoDefault(False)
        self.save_btn.clicked.connect(self._ok)
        foot.addWidget(self.save_btn)
        root.addSpacing(6)
        root.addLayout(foot)

    def _ok(self) -> None:
        for key, cb in self.checks.items():
            setattr(self.opts, key, cb.isChecked())
        self.opts.px = next((n for n, b in self.size_btns.items() if b.isChecked()), self.opts.px)
        self.accept()
