"""KLA 장비 확인 — 그림 카드 넷(기준 / 검증 / 둘 다 / KLA 아님), 한 번 눌러 확정.

현택님 결정(2026-11-24, K1 A안): 글만 많고 취소가 없던 팝업을 그림 카드로 바꾼다.
카드마다 작은 [기준][검증] 상자 그림 + 짧은 이름.  KLA 인 쪽 상자가 채워지고, 'KLA 아님' 은
빈 두 상자에 빗금이다.

- 카드를 누르면 바로 확정된다(고르고 [확인] 을 또 누르게 하지 않는다).
- 왼쪽 아래 [취소] 와 Esc 는 **검증 시작을 멈춘다** — 'KLA 아님'(그냥 진행)과 다르다.
  결과 :attr:`answer` 는 ``"ref"``·``"val"``·``"both"``·``None``(KLA 아님)·:data:`CANCEL`.
- 넷은 대등한 선택지라 어느 하나도 강조·기본값이 없고, Enter 는 아무것도 누르지 않는다.
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                             QSizePolicy, QVBoxLayout)

from ... import i18n
from .. import theme
from .neon_button import NeonButton

__all__ = ["CANCEL", "KlaSideDialog"]

CANCEL = "cancel"        # [취소]·Esc — 시작을 멈춘다(KLA 아님 = None 과 다르다)

_CARD_H = 150


def draw_pair(p: QPainter, r: QRectF, ref_on: bool, val_on: bool, none: bool) -> None:
    """``r`` 안에 [기준][검증] 두 상자 — KLA 인 쪽은 채우고, KLA 아님은 빗금."""
    gap = r.width() * 0.08
    w = (r.width() - gap) / 2
    sides = ((i18n.KO.KLA_SIDE_REF, ref_on), (i18n.KO.KLA_SIDE_VAL, val_on))
    for i, (label, on) in enumerate(sides):
        box = QRectF(r.left() + i * (w + gap), r.top(), w, r.height())
        p.setPen(QPen(QColor(theme.ACCENT if on else theme.LINE_STRONG), 1.5))
        p.setBrush(QColor(theme.ACCENT) if on else Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box, 5, 5)
        f = QFont(p.font())
        f.setPixelSize(max(10, int(r.height() * 0.30)))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.ON_ACCENT if on else theme.MUTE))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, label)
    if none:
        p.setPen(QPen(QColor(theme.DANGER), 2))
        p.drawLine(int(r.left() + 2), int(r.bottom() - 2),
                   int(r.right() - 2), int(r.top() + 2))


class _Card(QPushButton):
    """카드 전체가 클릭 영역(집안 관습).  면·테두리·포커스 링은 QSS(role=option)가,
    그림·이름은 직접 그린다."""

    def __init__(self, key, label: str, ref_on: bool, val_on: bool, none: bool,
                 parent=None) -> None:
        super().__init__("", parent)
        self.key = key
        self._label, self._ref, self._val, self._none = label, ref_on, val_on, none
        self.setProperty("role", "option")
        self.setAccessibleName(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoDefault(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(_CARD_H)

    def label(self) -> str:
        return self._label

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(132, _CARD_H)

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        draw_pair(p, QRectF(14, 22, w - 28, 54), self._ref, self._val, self._none)
        f = QFont(self.font())
        f.setPixelSize(15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.INK))
        p.drawText(QRectF(0, h - 52, w, 30), Qt.AlignmentFlag.AlignCenter, self._label)
        p.end()


class KlaSideDialog(QDialog):
    """KLA 장비가 어느 쪽인지 묻는다.  결과는 :attr:`answer`."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        K = i18n.KO
        self.setProperty("role", "sheet")
        self.answer = CANCEL
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(16)
        head = QLabel(K.KLA_ASK_TITLE, self)
        head.setProperty("role", "sheetTitle")
        v.addWidget(head)

        # ★ 네 경우(기준/검증/둘 다/KLA 아님)를 모두 유지한다(CLAUDE.md 규칙).
        row = QHBoxLayout()
        row.setSpacing(10)
        self.cards: dict = {}
        for key, label, ref_on, val_on, none in (
                ("ref", K.KLA_SIDE_REF, True, False, False),
                ("val", K.KLA_SIDE_VAL, False, True, False),
                ("both", K.KLA_SIDE_BOTH, True, True, False),
                (None, K.KLA_SIDE_NONE, False, False, True)):
            card = _Card(key, label, ref_on, val_on, none, self)
            card.clicked.connect(lambda _c=False, k=key: self._pick(k))
            row.addWidget(card)
            self.cards[key] = card
        v.addLayout(row)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        self.cancel_btn = NeonButton(K.BTN_CANCEL, role="ghost", parent=self)
        self.cancel_btn.setAutoDefault(False)
        self.cancel_btn.setFixedHeight(theme.PROFILE.control_h_lg)
        self.cancel_btn.clicked.connect(self.reject)
        foot.addWidget(self.cancel_btn)
        foot.addStretch(1)
        v.addLayout(foot)
        self.setMinimumWidth(620)

    def _pick(self, key) -> None:
        self.answer = key
        self.accept()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()                   # Enter 는 아무것도 누르지 않는다
            return
        if event.key() == Qt.Key.Key_Escape:
            self.reject()                    # answer 는 CANCEL 그대로
            return
        super().keyPressEvent(event)
