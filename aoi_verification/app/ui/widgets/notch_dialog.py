"""Notch 방향 고르기 — 좌표 매칭 직전, 기준·검증 폴더의 notch 가 어느 쪽인지.

두 줄(기준 / 검증)에 위·왼쪽·오른쪽·아래 네 칸.  처음 값은 폴더에서 읽은 값(KLA)이거나
표준(아래)이라 대개 [확인] 한 번이면 된다.  방향이 다르면 매칭이 그 폴더의 좌표를 돌려서
(`coords.notch`) 계산한다 — 엑셀·화면 좌표는 그대로다.  결과는 :attr:`dirs` (기준, 검증).
"""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout

from ... import i18n
from ...coords import notch
from .. import theme
from .neon_button import NeonButton
from .option_group import OptionGroup

__all__ = ["NotchDialog"]


class NotchDialog(QDialog):
    def __init__(self, ref_label: str, val_label: str, ref_dir: str, val_dir: str,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.NOTCH_TITLE)
        root = QVBoxLayout(self)
        m = theme.PROFILE.page_margin // 2
        root.setContentsMargins(m, m, m, m)
        root.setSpacing(12)

        head = QLabel(i18n.KO.NOTCH_TITLE, self)
        head.setProperty("role", "title")
        root.addWidget(head)

        opts = [(d, i18n.KO.NOTCH_DIRS[d]) for d in notch.DIRECTIONS]
        self.ref_group = self._row(root, i18n.KO.NOTCH_REF, ref_label, opts, ref_dir)
        self.val_group = self._row(root, i18n.KO.NOTCH_VAL, val_label, opts, val_dir)

        bar = QHBoxLayout()
        bar.addStretch(1)
        ok = NeonButton(i18n.KO.NOTCH_OK, role="primary", parent=self)
        ok.clicked.connect(self.accept)
        bar.addWidget(ok)
        root.addLayout(bar)
        self.setMinimumWidth(460)

    def _row(self, root, side: str, machine: str, opts, current: str) -> OptionGroup:
        lbl = QLabel(i18n.KO.NOTCH_SIDE_FMT.format(side=side, machine=machine), self)
        lbl.setProperty("role", "muted")
        root.addWidget(lbl)
        grp = OptionGroup(opts, current=notch.normalize(current), fixed_cols=4,
                          role="option", parent=self)
        root.addWidget(grp)
        return grp

    @property
    def dirs(self) -> tuple:
        return (self.ref_group.current_key() or notch.DEFAULT,
                self.val_group.current_key() or notch.DEFAULT)
