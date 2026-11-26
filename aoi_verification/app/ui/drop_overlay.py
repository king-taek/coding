"""끌어다 놓기 표시 — 놓일 자리에 테두리 + 위쪽 알약(무슨 일이 일어나는지)을 얹는다.

받을 수 있으면 강조색 테두리·``↓ 글자``, 못 받으면 위험색 테두리·``✕ 글자``다(바탕 칠 없음).
카드는 테두리를 카드 바깥에, 창 전체는 안쪽에 그린다.
창 위에 얹는 투명 위젯 하나(``DropOverlay``)를 화면마다 재사용한다(``overlay_of``).

★ 여기서도 디스크를 만지지 않는다 — ``dragEnter``/``dragMove`` 에서 부르는 도구라서다
  (``drop_paths`` 의 NAS 규칙).  받지 못할 때도 ``dragEnter`` 는 받아 둔다: 받지 않으면 Qt 가
  ``dragLeave`` 를 안 보내 표시를 못 끈다.  대신 ``dragMove`` 에서 ``ignore`` 해 '금지' 커서가 뜬다.
"""
from __future__ import annotations

from typing import Optional

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QPoint, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget

from . import theme

_INSET = 3          # 창 전체 표적: 가장자리에서 테두리까지(안쪽)
_OUTSET = 3         # 카드 표적: 카드 바깥으로 이만큼 띄운다(안의 글자·버튼을 안 가리게)
_WIDTH = 2          # 테두리 선 두께


class DropOverlay(QWidget):
    """``host`` 위에 얹히는 투명 위젯 — 마우스 이벤트는 그대로 통과한다."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._host = host
        self._target: Optional[QWidget] = None
        self.text = ""
        self.ok = True
        self.hide()

    def show_on(self, target: Optional[QWidget], text: str, ok: bool) -> None:
        """``target`` 위젯(없으면 창 전체)에 표시한다.  스크롤·크기 변화·뒤늦게 뜬 시트를
        따라가도록 부를 때마다 위치를 다시 잡고 맨 위로 올린다(값싸다)."""
        self._target, self.text, self.ok = target, text, ok
        self.setGeometry(self._host.rect())
        self.raise_()
        self.show()
        self.update()

    def clear(self) -> None:
        self._target = None
        self.hide()

    def border_rect(self) -> QRectF:
        """테두리 선의 중심선 사각형.  창 전체면 안쪽, 카드면 바깥(창 안으로 자른다)."""
        t = self._target
        half = _WIDTH / 2
        if t is None:
            return QRectF(self.rect()).adjusted(_INSET + half, _INSET + half,
                                                -_INSET - half, -_INSET - half)
        r = QRectF(t.rect().translated(t.mapTo(self._host, QPoint(0, 0))))
        r = r.adjusted(-_OUTSET, -_OUTSET, _OUTSET, _OUTSET)
        return r.intersected(QRectF(self.rect()).adjusted(half, half, -half, -half))

    def paintEvent(self, _event) -> None:       # noqa: N802
        base = QColor(theme.ACCENT if self.ok else theme.DANGER)
        rad = theme.PROFILE.radius_sm
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rr = self.border_rect()
        p.setPen(QPen(base, _WIDTH))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rr, rad, rad)
        # 알약 — 위쪽 테두리 선 위 가운데.  표적이 창 위쪽에 닿으면 잘리지 않게 안쪽으로 민다.
        f = QFont(QApplication.font())
        f.setPixelSize(13)
        f.setWeight(QFont.Weight.DemiBold)
        fm = QFontMetrics(f)
        text = ("↓ " if self.ok else "✕ ") + self.text
        w, h = fm.horizontalAdvance(text) + 20, fm.height() + 8
        y = max(rr.top() - h / 2, 3)
        pill = QRectF(rr.center().x() - w / 2, y, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(base)
        p.drawRoundedRect(pill, h / 2, h / 2)
        p.setFont(f)
        p.setPen(QColor(theme.ON_ACCENT))
        p.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)
        p.end()


def overlay_of(host: QWidget) -> DropOverlay:
    """``host`` 의 표시 위젯 — 처음 부를 때 만든다."""
    ov = getattr(host, "_drop_overlay", None)
    if ov is None:
        ov = host._drop_overlay = DropOverlay(host)
    return ov


def drop_feedback(host: QWidget, event, target: Optional[QWidget], text: str, ok: bool) -> None:
    """dragEnter/dragMove 에서 부른다 — 표시를 갱신하고 받을지(못 받을지)를 이벤트에 적는다."""
    overlay_of(host).show_on(target, text, ok)
    if ok or event.type() == QEvent.Type.DragEnter:
        event.acceptProposedAction()        # 못 받을 때도 enter 는 받아야 dragLeave 가 온다
    else:
        event.ignore()                      # move 에서 거절 → 금지 커서, 놓기는 오지 않는다


def drop_clear(host: QWidget) -> None:
    """dragLeave · drop 에서 부른다."""
    ov = getattr(host, "_drop_overlay", None)
    if ov is not None and not sip.isdeleted(ov):
        ov.clear()
