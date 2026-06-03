"""'일부 슬롯만 진행' 선택 다이얼로그.

기준 폴더에서 발견된 슬롯(하위 폴더) 목록을 체크박스로 보여주고, 이번 검증에서
진행할 슬롯만 고르게 한다.  결과는 ``selected`` 속성(슬롯명 집합)으로 가져간다.
미선택(취소) 시 호출자가 전체 진행으로 처리한다.
"""

from __future__ import annotations

from typing import Iterable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QVBoxLayout)

from ... import i18n
from .neon_button import NeonButton
from .window_controls import add_fullscreen_shortcut, enable_window_controls


class SlotSelectDialog(QDialog):
    """발견된 슬롯 목록에서 진행할 슬롯만 체크로 선택."""

    def __init__(self,
                 slot_names: Iterable[str],
                 *,
                 preselected: Optional[Iterable[str]] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.SLOT_SELECT_TITLE)
        self.resize(520, 560)
        self._slot_names = sorted(set(slot_names))
        self._preselected = (set(preselected) if preselected is not None
                             else set(self._slot_names))
        self._accepted = False
        enable_window_controls(self)
        add_fullscreen_shortcut(self)
        self._build()

    @property
    def selected(self) -> set[str]:
        """체크된 슬롯명 집합."""
        out: set[str] = set()
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.add(it.text())
        return out

    @property
    def accepted_ok(self) -> bool:
        return self._accepted

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        hint = QLabel(i18n.KO.SLOT_SELECT_HINT, self)
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._list = QListWidget(self)
        self._list.setMinimumHeight(360)
        for name in self._slot_names:
            item = QListWidgetItem(name, self._list)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if name in self._preselected
                else Qt.CheckState.Unchecked
            )
        root.addWidget(self._list, stretch=1)

        # 전체 선택 / 해제 보조 버튼
        aux = QHBoxLayout()
        btn_all = NeonButton(i18n.KO.SLOT_SELECT_ALL, role="ghost")
        btn_all.clicked.connect(lambda: self._set_all(True))
        btn_none = NeonButton(i18n.KO.SLOT_SELECT_NONE, role="ghost")
        btn_none.clicked.connect(lambda: self._set_all(False))
        aux.addWidget(btn_all)
        aux.addWidget(btn_none)
        aux.addStretch(1)
        root.addLayout(aux)

        bar = QHBoxLayout()
        bar.addStretch(1)
        cancel = NeonButton(i18n.KO.BTN_CANCEL, role="ghost")
        cancel.clicked.connect(self.reject)
        ok = NeonButton(i18n.KO.BTN_OK, role="primary")
        ok.clicked.connect(self._on_ok)
        bar.addWidget(cancel)
        bar.addWidget(ok)
        root.addLayout(bar)

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(state)

    def _on_ok(self) -> None:
        self._accepted = True
        self.accept()
