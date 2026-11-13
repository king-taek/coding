"""Wafer map 뷰 — :mod:`coords.wafer_map` 의 평면 좌표를 원 안에 그린다.

그리기는 :func:`paint_map` 한 곳이다.  화면 위젯(:class:`WaferMapView`)과 엑셀용 PNG
(:func:`render_map_image`)가 **같은 함수**를 쓰므로 두 곳의 그림이 어긋나지 않는다.

성능: die 격자는 셀마다 사각형이 아니라 **경계선 묶음**(``drawLines``)이다.  die 8만
개(한 변 ~300)여도 선은 600개 남짓이라 매 paint 에 그려도 된다.  점은 수백~수천 개다.

보기 상태 둘은 **그리기만 바꾼다** — 평면 좌표(:mod:`coords.wafer_map`)는 그대로다.

* ``rotation`` — 노치 방향.  파일에 각도가 없어 평면은 노치를 아래로 두는데, 실물이
  다르면 90° 단위로 돌린다.  회전은 :class:`_Mapper` 안에서만 일어나므로 점·격자·
  히트 판정·확대 기준이 저절로 같이 돈다.
* ``fill_dies`` — 점 대신 **결함이 든 die 칸을 통째로** 칠한다(:func:`defect_cells`).
  색은 **그 칸 결함 점과 같은 색**이다 — 셋업 단계는 결함 색 하나, 결과 단계는
  매치됨/미매치(한 칸에 섞이면 기본은 미매치, ``unmatched_first`` 를 끄면 매치됨).  pitch 를 모르는 폴더(절대좌표)는 칸을 못
  정하므로 점으로 남는다.

결함 수(:func:`count_text`)는 **엑셀 PNG 에만** 그림 안 오른쪽 아래 빈 곳에 찍는다
(``show_count``) — 원래 크기 원과 겹치지 않게 잡고(:func:`count_label_rect`), 공간이
모자라면 글자를 줄이다 끝내 안 되면 생략한다.  화면 위젯은 그림 안에 쓰지 않는다 —
맵을 캡처해 쓰므로 거기 숫자가 박히면 안 된다(사용자 결정).  화면에서는 시트가 맵
**밖**에 따로 적는다(``wafer_map_dialog._MapPanel.count``).

상호작용: 휠 = 커서 기준 확대, 드래그 = 이동, 호버 = col/row·x/y 툴팁 + 썸네일,
점 더블클릭 = ``point_activated(Path)``(상세 정보).  빈 곳 더블클릭은 아무것도 하지 않는다
(사용자 결정 — 점을 빗맞히면 확대가 풀려 불편했다).
썸네일은 **저화질 전용 캐시**(120px·Q60)라 만들기도 띄우기도 가볍고, 시트가 미리
만들어 두면 툴팁이 바로 뜬다.  QPainter 순수 구현.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (QLineF, QPoint, QPointF, QRectF, Qt, QVariantAnimation,
                          pyqtSignal)
from PyQt6.QtGui import (QColor, QFontMetricsF, QImage, QPainter, QPainterPath,
                         QPen)
from PyQt6.QtWidgets import QToolTip, QWidget

from ... import i18n
from ...coords.wafer_map import (MapData, MapPoint, cell_bounds, cell_of,
                                 defect_cells, die_grid_segments)
from ...utils import image_io
from .. import motion
from .. import theme

DOT_R = 3.0            # 점 반지름(px) 기본값 — 확대해도 그대로
DOT_R_MIN = 1          # 점 크기 슬라이더 범위(px, 반지름)
DOT_R_MAX = 12
HIT_PX = 8.0           # 호버/클릭 판정 거리(px)
ZOOM_STEP = 1.25
ZOOM_MAX = 60.0
_TOOLTIP_THUMB = 120   # = config.Sizing.MAP_THUMB_PX — 저화질 캐시를 그대로(업스케일 없이)
_NOTCH_FRAC = 0.03     # 노치 표시 반지름 = 지름의 3% (실물 1 mm 는 보이지 않는다)
_COUNT_MIN_PX = 8      # 결함 수 글자 크기(px) 범위 — 맵 크기에 비례
_COUNT_MAX_PX = 22
ROTATIONS = 4          # 90° 단위 — rotation 은 0~3(시계 방향 회전 수), 0 = 노치 아래


def rotate_xy(x: float, y: float, rot: int) -> tuple[float, float]:
    """평면 (x, y) 를 화면 기준 **시계 방향**으로 ``rot``×90° 돌린 좌표.

    평면은 +Y 가 위라 시계 방향 한 번은 ``(x, y) → (y, −x)`` 다.  노치 ``(0, −r)`` 이
    rot 1 에서 왼쪽, 2 에서 위, 3 에서 오른쪽으로 간다 —
    ``i18n.KO.WAFER_MAP_NOTCH_DIRS`` 의 순서와 같다(테스트가 못 박는다).
    ``rot`` 이 음수여도 파이썬 ``%`` 가 0~3 으로 접어 역변환에 그대로 쓸 수 있다."""
    rot %= ROTATIONS
    if rot == 1:
        return y, -x
    if rot == 2:
        return -x, -y
    if rot == 3:
        return -y, x
    return x, y


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
        "text": QColor(c["ink"]),
    }


class _Mapper:
    """평면 µm ↔ 화면 px.  ``scale`` = px/µm, ``pan`` = 화면 중앙에 오는 좌표.

    ``rot`` 은 평면을 화면 기준 시계 방향으로 돌리는 90° 횟수다.  **회전은 여기서만**
    일어난다 — 점·격자·노치·히트 판정·확대 기준이 모두 :meth:`to_px`/:meth:`to_um` 을
    지나므로 한 곳만 돌리면 전부 같이 돈다.  ``pan``·:meth:`to_um` 은 **돌린 뒤**의
    좌표계라 드래그·휠 확대가 회전과 무관하게 화면 방향 그대로 동작한다."""

    def __init__(self, rect: QRectF, radius: float, zoom: float = 1.0,
                 pan: QPointF = QPointF(0, 0), rot: int = 0) -> None:
        fit = min(rect.width(), rect.height()) * 0.46 / max(radius, 1.0)
        self.scale = fit * zoom
        self.cx = rect.center().x()
        self.cy = rect.center().y()
        self.pan = pan
        self.rot = rot % ROTATIONS

    def to_px(self, x: float, y: float) -> QPointF:
        x, y = rotate_xy(x, y, self.rot)
        return QPointF(self.cx + (x - self.pan.x()) * self.scale,
                       self.cy - (y - self.pan.y()) * self.scale)

    def to_um(self, p: QPointF) -> QPointF:
        """화면 px → **돌린 뒤** 평면 좌표(pan 과 같은 계).  평면 원본이 아니다."""
        return QPointF((p.x() - self.cx) / self.scale + self.pan.x(),
                       -(p.y() - self.cy) / self.scale + self.pan.y())

    def to_plane(self, p: QPointF) -> QPointF:
        """화면 px → **평면 원본** 좌표 — 회전을 되돌린다(die 칸 조회용)."""
        v = self.to_um(p)
        return QPointF(*rotate_xy(v.x(), v.y(), -self.rot))


_STATE_KEY = {None: "neutral", False: "unmatched", True: "matched"}


def paint_map(painter: QPainter, rect: QRectF, data: Optional[MapData], *,
              zoom: float = 1.0, pan: QPointF = QPointF(0, 0), rot: int = 0,
              fill_dies: bool = False, unmatched_first: bool = True,
              colors: Optional[dict] = None, dot_r: float = DOT_R, show_count: bool = False) -> Optional[_Mapper]:
    """``rect`` 안에 맵을 그린다.  ``data``/프레임이 없으면 바탕만.  매퍼를 돌려준다."""
    col = colors or _colors()
    painter.fillRect(rect, col["bg"])
    if data is None or data.frame is None:
        return None
    frame = data.frame
    r = frame.radius
    m = _Mapper(rect, r, zoom, pan, rot)
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

    # 결함이 든 die 칸 칠하기(화면 토글).  격자·윤곽보다 **먼저** 칠해 선이 면 위에
    # 남는다.  pitch 를 모르면 cells 가 비고, 그때는 점 그리기로 돌아간다.
    painter.setClipPath(wafer, Qt.ClipOperation.IntersectClip)
    cells = defect_cells(data, unmatched_first) if fill_dies else {}
    if cells:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        by_state: dict[str, list[QRectF]] = {}
        for cell, state in cells.items():
            x0, y0, x1, y1 = cell_bounds(frame, cell)
            by_state.setdefault(_STATE_KEY[state], []).append(
                QRectF(m.to_px(x0, y0), m.to_px(x1, y1)).normalized())
        for key, rects in by_state.items():
            painter.setBrush(col[key])          # 그 칸 결함 점과 같은 색(사용자 결정)
            painter.drawRects(rects)

    # die 격자.  선 폭 1px 고정, 안티앨리어싱 없이(600개가 또렷하게).
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    # 너무 촘촘하면(경계 간격 < 3px) 격자는 생략 — 면이 회색으로 뭉개진다.
    if frame.pitch_x and frame.pitch_x * m.scale >= 3.0 \
            and frame.pitch_y and frame.pitch_y * m.scale >= 3.0:
        # 온전한 die 만 — 가장자리에서 잘리는 die 에는 선을 긋지 않는다.
        lines = [QLineF(m.to_px(x1, y1), m.to_px(x2, y2))
                 for x1, y1, x2, y2 in die_grid_segments(frame)]
        painter.setPen(QPen(col["grid"], 1))
        painter.drawLines(lines)
    painter.setClipRect(rect)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(col["outline"], 1.5))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(wafer)

    # 점 — 상태별로 묶어 한 번씩 그린다.  칸을 칠했으면 점은 생략한다(사용자 결정:
    # '결함 위치 대신 die 를 칠한다').
    if not cells:
        painter.setPen(Qt.PenStyle.NoPen)
        for key, want in (("neutral", None), ("unmatched", False), ("matched", True)):
            painter.setBrush(col[key])
            for p in data.points:
                if p.matched is want:
                    painter.drawEllipse(m.to_px(p.x, p.y), dot_r, dot_r)
    painter.restore()
    if show_count:
        _paint_count(painter, rect, count_text(data), col)
    return m


def count_label_rect(rect: QRectF, text: str, font) -> Optional[QRectF]:
    """결함 수 글상자 자리 — ``rect`` 오른쪽 아래, **원래 크기 원과 겹치지 않게**.

    원은 :class:`_Mapper` 의 맞춤(지름 = 짧은 변의 92%)이다.  글이 원에 닿으면 글자를
    한 px 씩 줄이고, 최소 크기에서도 닿으면 ``None``(그리지 않음).  ``font`` 의 픽셀
    크기를 바꾼다.  확대하면 맵이 어디든 덮으므로 판정은 원래 크기 기준이다."""
    s = min(rect.width(), rect.height())
    cx, cy, rad = rect.center().x(), rect.center().y(), s * 0.46
    pad = max(3.0, s * 0.01)
    for px in range(max(_COUNT_MIN_PX, min(_COUNT_MAX_PX, round(s * 0.035))),
                    _COUNT_MIN_PX - 1, -1):
        font.setPixelSize(px)
        fm = QFontMetricsF(font)
        w, h = fm.horizontalAdvance(text) + pad * 2, fm.height() + pad
        box = QRectF(rect.right() - pad - w, rect.bottom() - pad - h, w, h)
        # 원 중심에서 글상자까지의 최근접 거리 > 반지름 이면 겹치지 않는다.
        nx = min(max(cx, box.left()), box.right())
        ny = min(max(cy, box.top()), box.bottom())
        if (nx - cx) ** 2 + (ny - cy) ** 2 > rad * rad:
            return box
    return None


def count_text(data: MapData) -> str:
    """이 맵(슬롯 또는 LOT 합산)의 결함 수 — 좌표를 못 놓은 사진도 센다."""
    return i18n.KO.WAFER_MAP_DEFECT_TOTAL_FMT.format(
        n=len(data.points) + len(data.unplaced))


def _paint_count(painter: QPainter, rect: QRectF, text: str, col: dict) -> None:
    """:func:`count_text` 를 맵 오른쪽 아래(:func:`count_label_rect`)에 쓴다."""
    font = painter.font()
    box = count_label_rect(rect, text, font)
    if box is None:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setFont(font)
    # 확대하면 맵이 글 밑으로 들어온다 — 바탕을 깔아 읽히게 한다.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(col["bg"])
    painter.drawRoundedRect(box, 4, 4)
    painter.setPen(col["text"])
    painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
    painter.restore()


def render_map_image(data: Optional[MapData], size: int = 720,
                     palette: Optional[dict] = None) -> QImage:
    """엑셀 삽입용 PNG 원본 — 화면과 같은 :func:`paint_map`.  기본은 앱 팔레트."""
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    try:
        paint_map(painter, QRectF(0, 0, size, size), data,
                  colors=_colors(palette or theme.COLORS),
                  show_count=True,
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
        self._rot = 0
        self._fill_dies = False
        self._unmatched_first = True
        self._dot_r = DOT_R
        self._pan = QPointF(0, 0)
        self._drag_from: Optional[QPoint] = None
        self._hover: Optional[MapPoint] = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMinimumSize(240, 240)
        # P6A — 노치를 바꾸면 옛 그림이 실제 각도만큼 돈 뒤 새 그림으로 바뀐다.
        self._rot_pix = None
        self._rot_deg = 0.0
        self._rot_t = 0.0
        self._rot_anim = QVariantAnimation(self)
        self._rot_anim.setStartValue(0.0)
        self._rot_anim.setEndValue(1.0)
        self._rot_anim.setDuration(motion.DUR_MAP_ROTATE)
        self._rot_anim.setEasingCurve(motion.EASE_PRIMARY)
        self._rot_anim.valueChanged.connect(self._on_rot_tick)
        self._rot_anim.finished.connect(self._on_rot_done)
        # P18A — 휠 한 칸 확대·축소를 120ms 로.  커서 아래 점은 내내 제자리다.
        self._zoom_from = 1.0
        self._zoom_to = 1.0
        self._pan_from = QPointF(0, 0)
        self._zoom_anchor = QPointF(0, 0)
        self._zoom_anim = QVariantAnimation(self)
        self._zoom_anim.setStartValue(0.0)
        self._zoom_anim.setEndValue(1.0)
        self._zoom_anim.setDuration(motion.DUR_ZOOM_STEP)
        self._zoom_anim.setEasingCurve(motion.EASE_PRIMARY)
        self._zoom_anim.valueChanged.connect(self._on_zoom_tick)

    # ------------------------------------------------------------------
    def set_data(self, data: Optional[MapData]) -> None:
        self._data = data
        self._end_rotate()
        self.reset_view()

    def data(self) -> Optional[MapData]:
        return self._data

    def reset_view(self) -> None:
        self._zoom_anim.stop()
        self._zoom = 1.0
        self._pan = QPointF(0, 0)
        self.update()

    def zoom(self) -> float:
        return self._zoom

    # ------------------------------------------------------------------
    def set_rotation(self, rot: int) -> None:
        """노치 방향 — 시계 방향 90° 회전 수(0~3).  확대·이동은 유지한다."""
        rot %= ROTATIONS
        if rot == self._rot:
            return
        self._finish_zoom()
        self._end_rotate()
        # 가까운 쪽으로 돈다: +1 = 시계 90°, −1 = 반시계 90°, 2 = 180°.
        steps = (rot - self._rot + 1) % ROTATIONS - 1
        pix = (self.grab() if motion.enabled() and self.isVisible()
               and self._mapper() is not None else None)
        # pan 은 '돌린 뒤' 좌표계라, 회전한 만큼 함께 돌려야 보던 자리가 그대로 남는다.
        self._pan = QPointF(*rotate_xy(self._pan.x(), self._pan.y(),
                                       rot - self._rot))
        self._rot = rot
        self._hover = None
        if pix is not None:
            self._rot_pix = pix
            self._rot_deg = 90.0 * steps
            self._rot_t = 0.0
            self._rot_anim.start()
        self.update()

    def _on_rot_tick(self, v) -> None:
        self._rot_t = float(v)
        self.update()

    def _on_rot_done(self) -> None:
        self._rot_pix = None
        self.update()

    def _end_rotate(self) -> None:
        self._rot_anim.stop()
        self._rot_pix = None

    def rotation(self) -> int:
        return self._rot

    def set_fill_dies(self, on: bool) -> None:
        """결함 위치(점) 대신 결함이 든 die 칸을 통째로 칠한다."""
        on = bool(on)
        if on == self._fill_dies:
            return
        self._fill_dies = on
        self.update()

    def fill_dies(self) -> bool:
        return self._fill_dies

    def set_unmatched_first(self, on: bool) -> None:
        """die 색칠에서 한 칸에 매치·미매치가 섞이면 미매치 색(True) / 매치됨 색(False)."""
        on = bool(on)
        if on == self._unmatched_first:
            return
        self._unmatched_first = on
        self.update()

    def unmatched_first(self) -> bool:
        return self._unmatched_first

    def set_dot_radius(self, r: float) -> None:
        """점 반지름(px) — 화면 보기 전용.  좌표·칸 계산과 무관하다."""
        r = float(min(DOT_R_MAX, max(DOT_R_MIN, r)))
        if r == self._dot_r:
            return
        self._dot_r = r
        self.update()

    def dot_radius(self) -> float:
        return self._dot_r

    # ------------------------------------------------------------------
    def paintEvent(self, event):            # noqa: N802
        painter = QPainter(self)
        if self._rot_pix is not None:       # P6A — 찍어 둔 옛 그림을 돌려 보인다
            try:
                painter.fillRect(self.rect(), _colors()["bg"])
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                c = QRectF(self.rect()).center()
                painter.translate(c)
                painter.rotate(self._rot_deg * self._rot_t)     # 양수 = 시계 방향
                painter.translate(-c)
                painter.drawPixmap(0, 0, self._rot_pix)
            finally:
                painter.end()
            return
        try:
            m = paint_map(painter, QRectF(self.rect()), self._data,
                          zoom=self._zoom, pan=self._pan, rot=self._rot,
                          fill_dies=self._fill_dies,
                          unmatched_first=self._unmatched_first,
                          dot_r=self._dot_r)
            if m is not None and self._hover is not None:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(QPen(QColor(theme.INK), 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(m.to_px(self._hover.x, self._hover.y),
                                    self._dot_r + 3, self._dot_r + 3)
        finally:
            painter.end()

    def _mapper(self) -> Optional[_Mapper]:
        if self._data is None or self._data.frame is None:
            return None
        return _Mapper(QRectF(self.rect()), self._data.frame.radius,
                       self._zoom, self._pan, self._rot)

    def point_at(self, pos) -> Optional[MapPoint]:
        """화면 px 위치에서 가장 가까운 점(HIT_PX 이내).  없으면 None.

        **칠하기 모드**에서는 점이 보이지 않으므로 칸 아무 데나 집어도 그 칸의 결함을
        집은 것으로 본다 — 안 그러면 보이지도 않는 점 위 8px 에서만 호버·더블클릭이
        되어 기능이 통째로 죽는다."""
        m = self._mapper()
        if m is None:
            return None
        pos = QPointF(pos)

        def d2(p: MapPoint) -> float:
            q = m.to_px(p.x, p.y)
            return (q.x() - pos.x()) ** 2 + (q.y() - pos.y()) ** 2

        hit = max(HIT_PX, self._dot_r)      # 점을 키우면 점 전체가 집히게
        best, best_d = None, hit * hit
        for p in self._data.points:
            d = d2(p)
            if d <= best_d:
                best, best_d = p, d
        if best is not None or not self._fill_dies:
            return best
        um = m.to_plane(pos)
        cell = cell_of(self._data.frame, um.x(), um.y())
        if cell is None:        # pitch 를 모르는 폴더 — 칸이 없으니 점도 안 칠해졌다
            return None
        in_cell = [p for p in self._data.points
                   if cell_of(self._data.frame, p.x, p.y) == cell]
        return min(in_cell, key=d2) if in_cell else None

    # ------------------------------------------------------------------
    def wheelEvent(self, event):            # noqa: N802
        m = self._mapper()
        if m is None:
            return
        steps = event.angleDelta().y() / 120.0
        if steps == 0:
            return
        self._end_rotate()                  # 도는 중 그림과 좌표계를 맞춘다
        running = self._zoom_anim.state() == QVariantAnimation.State.Running
        base = self._zoom_to if running else self._zoom     # 연속 휠은 목표에 쌓는다
        factor = ZOOM_STEP ** steps
        new_zoom = min(ZOOM_MAX, max(1.0, base * factor))
        if new_zoom == base:
            return
        event.accept()
        # 커서 아래 평면 좌표가 확대 뒤에도 커서 아래 있도록 pan 을 보정한다.
        anchor = m.to_um(event.position())
        if not motion.enabled() or not self.isVisible() or self._drag_from is not None:
            # 끄는 중 휠 — 애니메이션이 끌기 이동을 덮어쓰지 않게 즉시 적용
            self._zoom_anim.stop()
            self._apply_zoom(self._zoom, QPointF(self._pan), anchor, new_zoom)
            self.update()
            return
        # 지금 **보이는** 배율·위치에서 새 목표로 이어 간다(연속 휠도 끊기지 않게).
        self._zoom_from, self._pan_from = self._zoom, QPointF(self._pan)
        self._zoom_anchor, self._zoom_to = anchor, new_zoom
        self._zoom_anim.stop()
        self._zoom_anim.start()

    def _apply_zoom(self, z0: float, pan0: QPointF, anchor: QPointF, z: float) -> None:
        ratio = z0 / z
        self._pan = QPointF(anchor.x() - (anchor.x() - pan0.x()) * ratio,
                            anchor.y() - (anchor.y() - pan0.y()) * ratio)
        self._zoom = z
        if self._zoom == 1.0:
            self._pan = QPointF(0, 0)

    def _on_zoom_tick(self, v) -> None:
        e = float(v)
        z = (self._zoom_to if e >= 1.0
             else self._zoom_from * (self._zoom_to / self._zoom_from) ** e)  # 배율은 지수로
        self._apply_zoom(self._zoom_from, self._pan_from, self._zoom_anchor, z)
        self.update()

    def _finish_zoom(self) -> None:
        """돌던 휠 확대를 목표값으로 확정한다 — 끌기·회전이 중간값 위에서 시작하지 않게."""
        if self._zoom_anim.state() == QVariantAnimation.State.Running:
            self._zoom_anim.stop()
            self._on_zoom_tick(1.0)

    def mousePressEvent(self, event):       # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._finish_zoom()
            if self._rot_pix is not None:
                self._end_rotate()
                self.update()
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

    # ------------------------------------------------------------------
    @staticmethod
    def tooltip_html(p: MapPoint) -> str:
        if p.col is not None and p.die_x is not None:
            pos = i18n.KO.WAFER_MAP_TIP_DIE_FMT.format(col=p.col, row=p.row,
                                                       x=p.die_x, y=p.die_y)
        elif p.col is not None:
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
