"""카드 컨테이너 — role 속성으로 QSS 가 면/보더를 칠한다."""

from __future__ import annotations

from PyQt6.QtWidgets import QFrame, QVBoxLayout


class NeonCard(QFrame):
    """역할(role) 별로 다른 외곽선을 갖는 카드.

    '도면' 은 무광 제도 시트라 그림자/글로우를 쓰지 않는다 — 면과 눈금(QSS)만."""

    def __init__(self, *, role: str = "card", parent=None) -> None:
        super().__init__(parent)
        self.setProperty("role", role)
        self.setFrameShape(QFrame.Shape.NoFrame)

        from .. import theme
        pad = theme.PROFILE.card_pad
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(pad, pad, pad, pad)
        self._layout.setSpacing(8)

    def body(self) -> QVBoxLayout:
        return self._layout
