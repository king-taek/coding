"""Defect 추출 저장 방식 — 방식마다 그림 한 줄(현택님 2026-11-24, K2 B안).

설명문(어느 방식이 무엇인지 풀어 쓴 두 줄)을 없애고, 줄마다 작은 그림이 뜻을 말한다:
한 시트(시트 한 장) · Recipe별 시트(겹친 시트) · Recipe별 열(한 시트 안 나란한 열).
줄은 모두 같은 등급 — 파랑 강조·기본값이 없고, 누르면 바로 그 방식으로 간다.
왼쪽 아래 [돌아가기]·Esc 는 저장하지 않는다.  결과는 :attr:`choice`(``"back"`` = 돌아가기).
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout)

from ... import i18n
from ...workers import exporter as ex
from .. import theme
from .neon_button import NeonButton

__all__ = ["BACK", "ExtractSaveDialog"]

BACK = "back"

_ROW_H = 54


class _Row(QPushButton):
    """줄 전체가 클릭 영역.  면·테두리는 QSS(role=option), 그림·이름은 직접 그린다."""

    def __init__(self, key: str, label: str, parent=None) -> None:
        super().__init__("", parent)
        self.key, self._label = key, label
        self.setProperty("role", "option")
        self.setAccessibleName(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoDefault(False)
        self.setFixedHeight(_ROW_H)

    def label(self) -> str:
        return self._label

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(theme.ACCENT), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        x, y = 16.0, 13.0
        if self.key == ex.RECIPE_LAYOUT_SHEETS:          # 시트 여러 장(겹침)
            for i in range(3):
                p.drawRoundedRect(QRectF(x + i * 6, y + 8 - i * 4, 24, 20), 3, 3)
        elif self.key == ex.RECIPE_LAYOUT_COLUMNS:       # 한 시트 안 열 나란히
            p.drawRoundedRect(QRectF(x, y + 2, 40, 26), 3, 3)
            for i in (1, 2):
                cx = int(x + i * 13.3)
                p.drawLine(cx, int(y + 2), cx, int(y + 28))
        else:                                            # 시트 한 장
            p.drawRoundedRect(QRectF(x + 6, y, 28, 28), 3, 3)
            p.drawLine(int(x + 12), int(y + 10), int(x + 28), int(y + 10))
            p.drawLine(int(x + 12), int(y + 18), int(x + 28), int(y + 18))
        f = QFont(self.font())
        f.setPixelSize(15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.INK))
        p.drawText(QRectF(76, 0, self.width() - 92, self.height()),
                   Qt.AlignmentFlag.AlignVCenter, self._label)
        p.end()


class ExtractSaveDialog(QDialog):
    """``layouts`` = ``[(key, 이름), …]`` — 위에서부터 한 줄씩."""

    def __init__(self, layouts, wafers: int, n: int, parent=None) -> None:
        super().__init__(parent)
        K = i18n.KO
        self.setProperty("role", "sheet")
        self.choice = BACK
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(8)
        head = QLabel(K.EXTRACT_SUMMARY_TITLE, self)
        head.setProperty("role", "sheetTitle")
        v.addWidget(head)
        fact = QLabel(K.EXTRACT_SUMMARY_FMT.format(wafers=wafers, n=n), self)
        fact.setProperty("role", "sheetBody")
        v.addWidget(fact)
        v.addSpacing(6)
        self.rows: dict = {}
        for key, label in layouts:
            row = _Row(key, label, self)
            row.clicked.connect(lambda _c=False, k=key: self._pick(k))
            v.addWidget(row)
            self.rows[key] = row
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 10, 0, 0)
        self.back_btn = NeonButton(K.EXTRACT_SUMMARY_BACK, role="ghost", parent=self)
        self.back_btn.setAutoDefault(False)
        self.back_btn.setFixedHeight(theme.PROFILE.control_h_lg)
        self.back_btn.clicked.connect(self.reject)
        foot.addWidget(self.back_btn)
        foot.addStretch(1)
        v.addLayout(foot)
        self.setMinimumWidth(420)

    def _pick(self, key) -> None:
        self.choice = key
        self.accept()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()                   # Enter 는 아무것도 누르지 않는다
            return
        if event.key() == Qt.Key.Key_Escape:
            self.reject()                    # choice 는 BACK 그대로
            return
        super().keyPressEvent(event)
