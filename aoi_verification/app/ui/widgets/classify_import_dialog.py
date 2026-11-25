"""Overkill 튜닝 — [리뷰 결과 불러오기] 팝업(현택님 2026-11-24 안 A).

사람이 검토로 바꾼 분류마다 한 줄: 이름 · 개수 · [Defect] [Overkill] [안 씀].  처음 값은 바꾼 분류의 종류로
정한다(Good → Overkill, 그 밖 → Defect).  [고르기] 하면 ``applied(꼭 잡기, Overkill)`` 를 낸다.
순수 로직은 :mod:`...threshold.classify` 에 있다.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QButtonGroup, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from ... import i18n
from ...threshold import classify as C
from .neon_button import NeonButton

_KO = i18n.KO
_CHOICES = ((C.DEFECT, "OVERKILL_MARK_DEFECT"), (C.OVERKILL, "OVERKILL_MARK_OVERKILL"),
            (C.SKIP, "OVERKILL_CLASSIFY_SKIP"))


class ClassifyImportDialog(QDialog):
    applied = pyqtSignal(dict, dict)

    def __init__(self, rows, key_of, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_KO.OVERKILL_CLASSIFY_TITLE)
        self._rows = list(rows)
        self._key_of = key_of
        self.choice = {r.cls: r.default for r in self._rows}
        self.groups: dict = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        title = QLabel(_KO.OVERKILL_CLASSIFY_TITLE, self)
        title.setStyleSheet("font-weight: 700;")
        root.addWidget(title)
        if not self._rows:
            none = QLabel(_KO.OVERKILL_CLASSIFY_NONE, self)
            none.setProperty("role", "muted")
            root.addWidget(none)
        else:
            host = QWidget(self)
            grid = QGridLayout(host)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(8)
            for i, r in enumerate(self._rows):
                grid.addWidget(QLabel(r.name, host), i, 0)
                n = QLabel(f"{len(r.defects):,}", host)
                n.setProperty("role", "mono")
                n.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                grid.addWidget(n, i, 1)
                seg = QHBoxLayout()
                seg.setSpacing(4)
                group = QButtonGroup(self)
                group.setExclusive(True)
                for kind, key in _CHOICES:
                    b = QPushButton(getattr(_KO, key), host)
                    b.setProperty("role", "ovBoxToggle")
                    b.setCheckable(True)
                    b.setAutoDefault(False)
                    b.setCursor(Qt.CursorShape.PointingHandCursor)
                    b.setChecked(kind == r.default)
                    b.clicked.connect(lambda _c=False, c=r.cls, k=kind: self.set_choice(c, k))
                    group.addButton(b)
                    seg.addWidget(b)
                    self.groups[(r.cls, kind)] = b
                grid.addLayout(seg, i, 2)
            grid.setColumnStretch(0, 1)
            area = QScrollArea(self)
            area.setWidgetResizable(True)
            area.setFrameShape(QScrollArea.Shape.NoFrame)
            area.setWidget(host)
            root.addWidget(area, 1)
        foot = QHBoxLayout()
        foot.addStretch(1)
        cancel = NeonButton(_KO.OVERKILL_PICK_BTN_CANCEL, role="ghost")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        foot.addWidget(cancel)
        self.apply_btn = NeonButton("", role="primary")
        self.apply_btn.setAutoDefault(False)
        self.apply_btn.clicked.connect(self._apply)
        foot.addWidget(self.apply_btn)
        root.addLayout(foot)
        self._sync()

    def set_choice(self, cls: int, kind: str) -> None:
        self.choice[cls] = kind
        b = self.groups.get((cls, kind))
        if b is not None and not b.isChecked():
            b.setChecked(True)
        self._sync()

    def result_sets(self) -> tuple:
        return C.split(self._rows, self.choice, self._key_of)

    def _sync(self) -> None:
        picked, over = self.result_sets()
        self.apply_btn.setText(_KO.OVERKILL_CLASSIFY_APPLY_FMT.format(d=f"{len(picked):,}", o=f"{len(over):,}"))
        self.apply_btn.setEnabled(bool(picked or over))

    def _apply(self) -> None:
        picked, over = self.result_sets()
        self.applied.emit(picked, over)
        self.accept()
