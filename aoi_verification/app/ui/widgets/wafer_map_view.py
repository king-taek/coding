"""Wafer map 뷰 — :mod:`coords.wafer_map` 의 평면 좌표를 원 안에 그린다.

그리기는 :func:`paint_map` 한 곳이다.  화면 위젯(:class:`WaferMapView`)과 엑셀용 PNG
(:func:`render_map_image`)가 **같은 함수**를 쓰므로 두 곳의 그림이 어긋나지 않는다.

성능: die 격자는 셀마다 사각형이 아니라 **경계선 묶음**(``drawLines``)이다.  die 8만
개(한 변 ~300)여도 선은 600개 남짓이라 매 paint 에 그려도 된다.  점은 수백~수천 개다.

상호작용: 휠 = 커서 기준 확대, 드래그 = 이동, 호버 = col/row·x/y 툴팁 + 썸네일,
점 더블클릭 = ``point_activated(Path)``(상세 정보), 빈 곳 더블클릭 = 원래 크기.
썸네일은 **저화질 전용 캐시**(120px·Q60)라 만들기도 띄우기도 가볍고, 시트가 미리
만들어 두면 툴팁이 바로 뜬다.  QPainter 순수 구현.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QLineF, QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (QColor, QImage, QPainter, QPainterPath, QPen)
from PyQt6.QtWidgets import QToolTip, QWidget

from ... import i18n
from ...coords.wafer_map import MapData, MapPoint, grid_lines
from ...utils import image_io
from .. import theme

DOT_R = 3.0            # 점 반지름(px) — 확대해도 그대로
HIT_PX = 8.0           # 호버/클릭 판정 거리(px)
ZOOM_STEP = 1.25
ZOOM_MAX = 60.0
_TOOLTIP_THUMB = 120   # = config.Sizing.MAP_THUMB_PX — 저화질 캐시를 그대로(업스케일 없이)
_NOTCH_FRAC = 0.03     # 노치 표시 반지름 = 지름의 3% (실물 1 mm 는 보이지 않는다)


def _colors(palette: Optional[dict] = None) -> dict[str, QColor]:
    """그리기 색.  ``palette`` 를 주면(엑셀 PNG) 그 팔레트로, 아니면 현재 테마로."""
    c = palette or theme.COLORS
    return {
        "bg": QColor(c["panel"]),
        "wafer": QColor(c["elev"]),
        "outline": QColor(c["line"]),
        "grid": QColor(c["line2"]),
        "matched": QColor(c["pass"]),
        "unmatched": QColor(c["danger"]),
        "neutral": QColor(c["accent"]),
    }


class _Mapper:
    """평면 µm ↔ 화면 px.  ``scale`` = px/µm, ``pan`` = 화면 중앙에 오는 평면 좌표."""

    def __init__(self, rect: QRectF, radius: float, zoom: float = 1.0,
                 pan: QPointF = QPointF(0, 0)) -> None:
        fit = min(rect.width(), rect.height()) * 0.46 / max(radius, 1.0)
        self.scale = fit * zoom
        self.cx = rect.center().x()
        self.cy = rect.center().y()
        self.pan = pan

    def to_px(self, x: float, y: float) -> QPointF:
        return QPointF(self.cx + (x - self.pan.x()) * self.scale,
                       self.cy - (y - self.pan.y()) * self.scale)

    def to_um(self, p: QPointF) -> QPointF:
        return QPointF((p.x() - self.cx) / self.scale + self.pan.x(),
                       -(p.y() - self.cy) / self.scale + self.pan.y())


def paint_map(painter: QPainter, rect: QRectF, data: Optional[MapData], *,
              zoom: float = 1.0, pan: QPointF = QPointF(0, 0),
              colors: Optional[dict] = None, dot_r: float = DOT_R) -> Optional[_Mapper]:
    """``rect`` 안에 맵을 그린다.  ``data``/프레임이 없으면 바탕만.  매퍼를 돌려준다."""
    col = colors or _colors()
    painter.fillRect(rect, col["bg"])
    if data is None or data.frame is None:
        return None
    frame = data.frame
    r = frame.radius
    m = _Mapper(rect, r, zoom, pan)
    painter.save()
    painter.setClipRect(rect)

    # 웨이퍼 원 + 노치(아래).
    center = m.to_px(0.0, 0.0)
    rp = r * m.scale
    wafer = QPainterPath()
    wafer.addEllipse(center, rp, rp)
    notch = QPainterPath()
    nr = r * _NOTCH_FRAC * m.scale
    notch.addEllipse(m.to_px(0.0, -r), nr, nr)
    wafer = wafer.subtracted(notch)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.fillPath(wafer, col["wafer"])

    # die 격자 — 원 안 현(弦)만.  선 폭 1px 고정, 안티앨리어싱 없이(600개가 또렷하게).
    painter.setClipPath(wafer, Qt.ClipOperation.IntersectClip)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    xs, ys = grid_lines(frame)
    # 너무 촘촘하면(경계 간격 < 3px) 격자는 생략 — 면이 회색으로 뭉개진다.
    if frame.pitch_x and frame.pitch_x * m.scale >= 3.0 \
            and frame.pitch_y and frame.pitch_y * m.scale >= 3.0:
        lines = []
        for x in xs:
            h = (r * r - x * x) ** 0.5
            lines.append(QLineF(m.to_px(x, -h), m.to_px(x, h)))
        for y in ys:
            w = (r * r - y * y) ** 0.5
            lines.append(QLineF(m.to_px(-w, y), m.to_px(w, y)))
        painter.setPen(QPen(col["grid"], 1))
        painter.drawLines(lines)
    painter.setClipRect(rect)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(col["outline"], 1.5))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(wafer)

    # 점 — 상태별로 묶어 한 번씩 그린다.
    painter.setPen(Qt.PenStyle.NoPen)
    for key, want in (("neutral", None), ("unmatched", False), ("matched", True)):
        painter.setBrush(col[key])
        for p in data.points:
            if p.matched is want:
                painter.drawEllipse(m.to_px(p.x, p.y), dot_r, dot_r)
    painter.restore()
    return m


def render_map_image(data: Optional[MapData], size: int = 720,
                     palette: Optional[dict] = None) -> QImage:
    """엑셀 삽입용 PNG 원본 — 화면과 같은 :func:`paint_map`.  기본은 밝은 팔레트."""
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    try:
        paint_map(painter, QRectF(0, 0, size, size), data,
                  colors=_colors(palette or theme.PALETTES["light"]),
                  dot_r=max(2.0, size / 240))
    finally:
        painter.end()
    return img


def render_map_png(data: Optional[MapData], size: int) -> bytes:
    """:func:`render_map_image` 를 PNG 바이트로 — 엑셀 저장 워커에 **인자로** 넘긴다
    (workers 계층은 ui 를 import 하지 않는다 — ``test_layering``)."""
    from PyQt6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    render_map_image(data, size=size).save(buf, "PNG")
    return bytes(buf.data())


class WaferMapView(QWidget):
    """휠 확대 · 드래그 이동 · 호버 툴팁 · 클릭 신호를 가진 맵 위젯."""

    point_activated = pyqtSignal(object)    # Path — 점 더블클릭

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._data: Optional[MapData] = None
        self._zoom = 1.0
        self._pan = QPointF(0, 0)
        self._drag_from: Optional[QPoint] = None
        self._hover: Optional[MapPoint] = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMinimumSize(240, 240)

    # ------------------------------------------------------------------
    def set_data(self, data: Optional[MapData]) -> None:
        self._data = data
        self.reset_view()

    def data(self) -> Optional[MapData]:
        return self._data

    def reset_view(self) -> None:
        self._zoom = 1.0
        self._pan = QPointF(0, 0)
        self.update()

    def zoom(self) -> float:
        return self._zoom

    # ------------------------------------------------------------------
    def paintEvent(self, event):            # noqa: N802
        painter = QPainter(self)
        try:
            m = paint_map(painter, QRectF(self.rect()), self._data,
                          zoom=self._zoom, pan=self._pan)
            if m is not None and self._hover is not None:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(QPen(QColor(theme.INK), 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(m.to_px(self._hover.x, self._hover.y),
                                    DOT_R + 3, DOT_R + 3)
        finally:
            painter.end()

    def _mapper(self) -> Optional[_Mapper]:
        if self._data is None or self._data.frame is None:
            return None
        return _Mapper(QRectF(self.rect()), self._data.frame.radius,
                       self._zoom, self._pan)

    def point_at(self, pos) -> Optional[MapPoint]:
        """화면 px 위치에서 가장 가까운 점(HIT_PX 이내).  없으면 None."""
        m = self._mapper()
        if m is None:
            return None
        pos = QPointF(pos)
        best, best_d = None, HIT_PX * HIT_PX
        for p in self._data.points:
            q = m.to_px(p.x, p.y)
            d = (q.x() - pos.x()) ** 2 + (q.y() - pos.y()) ** 2
            if d <= best_d:
                best, best_d = p, d
        return best

    # ------------------------------------------------------------------
    def wheelEvent(self, event):            # noqa: N802
        m = self._mapper()
        if m is None:
            return
        steps = event.angleDelta().y() / 120.0
        if steps == 0:
            return
        factor = ZOOM_STEP ** steps
        new_zoom = min(ZOOM_MAX, max(1.0, self._zoom * factor))
        if new_zoom == self._zoom:
            return
        # 커서 아래 평면 좌표가 확대 뒤에도 커서 아래 있도록 pan 을 보정한다.
        anchor = m.to_um(event.position())
        ratio = self._zoom / new_zoom
        self._pan = QPointF(anchor.x() - (anchor.x() - self._pan.x()) * ratio,
                            anchor.y() - (anchor.y() - self._pan.y()) * ratio)
        self._zoom = new_zoom
        if self._zoom == 1.0:
            self._pan = QPointF(0, 0)
        self.update()
        event.accept()

    def mousePressEvent(self, event):       # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = event.position().toPoint()

    def mouseMoveEvent(self, event):        # noqa: N802
        m = self._mapper()
        if self._drag_from is not None and m is not None:
            delta = event.position().toPoint() - self._drag_from
            self._pan = QPointF(self._pan.x() - delta.x() / m.scale,
                                self._pan.y() + delta.y() / m.scale)
            self._drag_from = event.position().toPoint()
            self.update()
            return
        hit = self.point_at(event.position())
        if hit is not self._hover:
            self._hover = hit
            self.update()
            if hit is None:
                QToolTip.hideText()
            else:
                QToolTip.showText(event.globalPosition().toPoint(),
                                  self.tooltip_html(hit), self)

    def mouseReleaseEvent(self, event):     # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = None

    def leaveEvent(self, event):            # noqa: N802
        self._hover = None
        self.update()

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        self._drag_from = None
        hit = self.point_at(event.position())
        if hit is not None:
            self.point_activated.emit(hit.path)
        else:
            self.reset_view()

    # ------------------------------------------------------------------
    @staticmethod
    def tooltip_html(p: MapPoint) -> str:
        if p.col is not None:
            pos = i18n.KO.WAFER_MAP_TIP_COLROW_FMT.format(col=p.col, row=p.row)
        else:
            pos = i18n.KO.WAFER_MAP_TIP_NO_DIE
        xy = i18n.KO.WAFER_MAP_TIP_XY_FMT.format(x=p.x, y=p.y)
        img = ""
        try:
            tp = image_io.get_map_thumb_path(p.path)     # 저화질 — 즉시성 우선
            img = (f"<br><img src='{Path(tp).as_uri()}' "
                   f"width='{_TOOLTIP_THUMB}'>")
        except Exception:
            pass
        return (f"<b>{p.path.name}</b><br>{pos}<br>{xy}{img}")
