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


_PAINTERS = {"camera": _paint_camera, "globe": _paint_globe, "doc": _paint_doc,
             "bars": _paint_bars, "refresh": _paint_refresh}


def rail_icon(name: str) -> QIcon:
    """``name`` 아이콘(camera·globe·doc·bars·refresh) — 알 수 없는 이름은 빈 아이콘."""
    paint = _PAINTERS.get(name)
    if paint is None:
        return QIcon()
    scale = 2                                   # 2배로 그려 고해상도에서도 또렷하게
    pm = QPixmap(_SIZE * scale, _SIZE * scale)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(scale)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(theme.INK))
    pen.setWidthF(1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    paint(p)
    p.end()
    return QIcon(pm)
