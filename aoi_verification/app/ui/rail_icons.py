"""도구 레일 아이콘 — 선 아이콘을 코드로 그린다(이미지 자원을 늘리지 않는다).

색은 글자색(ink)이고, 고해상도 화면을 위해 devicePixelRatio 만큼 크게 그려 얹는다."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from . import theme

_SIZE = 20


def _paint_camera(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(2.5, 6, 15, 10.5), 2, 2)
    p.drawEllipse(QPointF(10, 11.2), 3.2, 3.2)
    p.drawLine(QPointF(7, 6), QPointF(8.2, 3.8))
    p.drawLine(QPointF(8.2, 3.8), QPointF(11.8, 3.8))
    p.drawLine(QPointF(11.8, 3.8), QPointF(13, 6))


def _paint_globe(p: QPainter) -> None:
    p.drawEllipse(QPointF(10, 10), 7.5, 7.5)
    p.drawEllipse(QPointF(10, 10), 3.2, 7.5)
    p.drawLine(QPointF(2.5, 10), QPointF(17.5, 10))


def _paint_doc(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(4.5, 2.5, 11, 15), 1.5, 1.5)
    for y in (7.5, 10.5, 13.5):
        p.drawLine(QPointF(7, y), QPointF(13, y))


def _paint_bars(p: QPainter) -> None:
    p.drawLine(QPointF(3, 17), QPointF(17, 17))
    for x, top in ((5, 11), (9, 5), (13, 8.5)):
        p.drawRect(QRectF(x - 1.2, top, 2.4, 17 - top))


def _paint_refresh(p: QPainter) -> None:
    p.drawArc(QRectF(3.5, 3.5, 13, 13), 40 * 16, 270 * 16)
    p.drawLine(QPointF(15.8, 3.2), QPointF(16.6, 7.2))
    p.drawLine(QPointF(16.6, 7.2), QPointF(12.6, 7.4))


# ── GL 뷰어 도구 레일 ───────────────────────────────────────────────────────
def _paint_move(p: QPainter) -> None:
    p.drawLine(QPointF(10, 3), QPointF(10, 17))
    p.drawLine(QPointF(3, 10), QPointF(17, 10))
    for a, b, c in ((QPointF(7.5, 5.5), QPointF(10, 3), QPointF(12.5, 5.5)),
                    (QPointF(7.5, 14.5), QPointF(10, 17), QPointF(12.5, 14.5)),
                    (QPointF(5.5, 7.5), QPointF(3, 10), QPointF(5.5, 12.5)),
                    (QPointF(14.5, 7.5), QPointF(17, 10), QPointF(14.5, 12.5))):
        p.drawLine(a, b)
        p.drawLine(b, c)


def _paint_hline(p: QPainter) -> None:
    p.drawLine(QPointF(2.5, 10), QPointF(17.5, 10))
    p.drawEllipse(QPointF(10, 10), 2.2, 2.2)


def _paint_vline(p: QPainter) -> None:
    p.drawLine(QPointF(10, 2.5), QPointF(10, 17.5))
    p.drawEllipse(QPointF(10, 10), 2.2, 2.2)


def _paint_rect(p: QPainter) -> None:
    p.drawRect(QRectF(2.5, 5, 15, 10))


def _paint_square(p: QPainter) -> None:
    p.drawRect(QRectF(4, 4, 12, 12))


def _paint_circle(p: QPainter) -> None:
    p.drawEllipse(QPointF(10, 10), 6.5, 6.5)


def _paint_fit(p: QPainter) -> None:
    for (x, y), (dx, dy) in (((3, 3), (1, 1)), ((17, 3), (-1, 1)),
                             ((3, 17), (1, -1)), ((17, 17), (-1, -1))):
        p.drawLine(QPointF(x, y), QPointF(x + 4.5 * dx, y))
        p.drawLine(QPointF(x, y), QPointF(x, y + 4.5 * dy))
    p.drawRect(QRectF(7, 7, 6, 6))


def _paint_undo(p: QPainter) -> None:
    p.drawArc(QRectF(4, 5.5, 12, 12), 20 * 16, 250 * 16)
    p.drawLine(QPointF(3.2, 10.6), QPointF(5.2, 6.6))
    p.drawLine(QPointF(5.2, 6.6), QPointF(9.2, 8.2))


def _paint_trash(p: QPainter) -> None:
    p.drawLine(QPointF(3.5, 5.5), QPointF(16.5, 5.5))
    p.drawLine(QPointF(8, 5.5), QPointF(8, 3.5))
    p.drawLine(QPointF(8, 3.5), QPointF(12, 3.5))
    p.drawLine(QPointF(12, 3.5), QPointF(12, 5.5))
    p.drawRoundedRect(QRectF(5, 5.5, 10, 12), 1.5, 1.5)
    p.drawLine(QPointF(8.3, 8.5), QPointF(8.3, 14.5))
    p.drawLine(QPointF(11.7, 8.5), QPointF(11.7, 14.5))


_PAINTERS = {"camera": _paint_camera, "globe": _paint_globe, "doc": _paint_doc,
             "bars": _paint_bars, "refresh": _paint_refresh,
             "move": _paint_move, "hline": _paint_hline, "vline": _paint_vline,
             "rect": _paint_rect, "square": _paint_square, "circle": _paint_circle,
             "fit": _paint_fit, "undo": _paint_undo, "trash": _paint_trash}


def rail_icon(name: str, color: str = "") -> QIcon:
    """``name`` 아이콘 — 알 수 없는 이름은 빈 아이콘.  ``color`` 를 비우면 글자색(ink)."""
    paint = _PAINTERS.get(name)
    if paint is None:
        return QIcon()
    scale = 2                                   # 2배로 그려 고해상도에서도 또렷하게
    pm = QPixmap(_SIZE * scale, _SIZE * scale)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(scale)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color or theme.INK))
    pen.setWidthF(1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    paint(p)
    p.end()
    return QIcon(pm)
