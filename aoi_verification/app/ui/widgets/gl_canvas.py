"""GL 뷰어의 사진 화면 — 확대·이동, 도형(사각형·원).

좌표는 모두 이미지 픽셀로 저장하고 그릴 때만 화면 좌표로 바꾼다 — 그래서 확대·축소·이동을
해도 도형이 자동으로 맞는다.

조작
- 선택한 ROI: 방향키로 1 px(Shift 10 px) 이동.
- 도형: 몸통을 끌어 옮기고 손잡이로 크기를 바꾼다.
- 휠 확대(커서 기준), 가운데·오른쪽 버튼 또는 Space+끌기로 이동, ``F`` 맞춤.
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QImage, QPainter, QPen, QCursor,
                         QFontMetrics)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ... import i18n
from ...gl import roi as R
from ...gl.roi import Roi, RoiSet
from .. import motion, theme

TOOL_MOVE = "move"
TOOLS = (TOOL_MOVE, R.RECT, R.CIRCLE)

_MIN_SCALE, _MAX_SCALE = 0.02, 64.0
_GRID_FROM = 12.0               # 이 배율부터 픽셀 격자를 그린다
_HANDLE_PX = 9                  # 손잡이 크기(화면 px)
_DRAG_START_PX = 4              # 이만큼 움직여야 '끌기' 로 본다
_KEY_COALESCE_S = 0.8           # 방향키 연타는 되돌리기 한 번으로 묶는다


class GlCanvas(QWidget):
    roisChanged = pyqtSignal()                 # ROI 가 바뀜(추가·이동·크기·삭제·되돌리기)
    selectionChanged = pyqtSignal()
    hoverInfo = pyqtSignal(int, int, int)      # x, y, GL (사진 밖이면 x = -1)
    viewChanged = pyqtSignal(float)            # 배율
    viewStateChanged = pyqtSignal()            # 배율·이동이 바뀜(두 사진 비교의 화면 동기화)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rois = RoiSet()
        self.tool = TOOL_MOVE
        self._data: Optional[np.ndarray] = None
        self._rgb: Optional[np.ndarray] = None
        self._qimg: Optional[QImage] = None
        self._scale = 1.0
        self._off = QPointF(0, 0)
        self._fitted = True
        self._lo = self._hi = None
        self._drag: Optional[dict] = None
        self._space = False
        self._cursor_img: Optional[tuple] = None
        self._last_key: tuple = (None, "", 0.0)
        # 확대·맞춤 애니메이션 — _scale/_off 는 늘 '지금 보이는' 값이라 좌표 계산은 그대로 맞다.
        self._zoom_anim = QVariantAnimation(self)
        self._zoom_anim.setStartValue(0.0)
        self._zoom_anim.setEndValue(1.0)
        self._zoom_anim.valueChanged.connect(self._on_zoom_tick)
        self._zoom_plan: Optional[dict] = None
        self._zoom_goal: Optional[tuple] = None     # 목표 (배율, 오프셋) — 연속 휠이 여기에 쌓인다
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName(i18n.KO.GL_CANVAS_NAME)

    # ------------------------------------------------------------------
    # 데이터
    # ------------------------------------------------------------------
    def set_image(self, data: np.ndarray, rgb: np.ndarray, *, fit: bool = True) -> None:
        """분석 배열(원본)과 표시용 RGB 를 함께 받는다.  ``fit`` 이면 화면에 맞춘다."""
        self._data = data
        self.set_display(rgb)
        if fit:
            self.fit()

    def set_display(self, rgb: np.ndarray) -> None:
        """표시용 RGB 만 바꾼다(임계값·범위가 바뀔 때) — 배율·이동은 그대로."""
        self._rgb = np.ascontiguousarray(rgb)
        h, w = self._rgb.shape[:2]
        self._qimg = QImage(self._rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888)
        self.update()

    def set_thresholds(self, lo, hi) -> None:
        self._lo, self._hi = lo, hi

    def has_image(self) -> bool:
        return self._data is not None

    def image_size(self) -> tuple[int, int]:
        """(폭, 높이).  사진이 없으면 (0, 0)."""
        if self._data is None:
            return 0, 0
        return self._data.shape[1], self._data.shape[0]

    def scale(self) -> float:
        return self._scale

    def view_state(self) -> tuple[float, float, float]:
        return self._scale, self._off.x(), self._off.y()

    def set_view_state(self, state: tuple[float, float, float]) -> None:
        """다른 화면(비교 상대)의 배율·이동을 그대로 따라간다 — 신호를 다시 내지 않는다."""
        scale, ox, oy = state
        self._stop_zoom()
        if (scale, ox, oy) == self.view_state():
            return
        self._scale, self._off, self._fitted = scale, QPointF(ox, oy), False
        self.update()

    # ------------------------------------------------------------------
    # 좌표 변환
    # ------------------------------------------------------------------
    def to_screen(self, x: float, y: float) -> QPointF:
        return QPointF(self._off.x() + x * self._scale, self._off.y() + y * self._scale)

    def to_image(self, sx: float, sy: float) -> tuple[float, float]:
        return (sx - self._off.x()) / self._scale, (sy - self._off.y()) / self._scale

    def _pixel_at(self, sx: float, sy: float) -> tuple[int, int]:
        x, y = self.to_image(sx, sy)
        w, h = self.image_size()
        return (max(0, min(int(np.floor(x)), w - 1)),
                max(0, min(int(np.floor(y)), h - 1)))

    # ------------------------------------------------------------------
    # 보기
    # ------------------------------------------------------------------
    def fit(self, animate: bool = False) -> None:
        w, h = self.image_size()
        if w <= 0 or self.width() <= 0 or self.height() <= 0:
            return
        s = min(self.width() / w, self.height() / h)
        scale = max(_MIN_SCALE, min(s, _MAX_SCALE))
        self._go_to(scale, QPointF((self.width() - w * scale) / 2.0,
                                   (self.height() - h * scale) / 2.0),
                    pivot=None, animate=animate)
        self._fitted = True

    # ------------------------------------------------------------------
    # 확대·맞춤 애니메이션 — 휠 120ms, 상자 확대·맞춤 300ms
    # ------------------------------------------------------------------
    def _center_img(self, scale: float, off: QPointF, pivot: QPointF) -> QPointF:
        return QPointF((pivot.x() - off.x()) / scale, (pivot.y() - off.y()) / scale)

    def _go_to(self, scale: float, off: QPointF, *, pivot: Optional[QPointF],
               animate: bool, wheel: bool = False) -> None:
        """목표 (배율, 오프셋)로 간다.  pivot 은 화면에서 제자리인 점(휠은 커서, 그 밖은 중앙).
        끄면 한 프레임에 간다.  배율은 지수, pivot 아래 이미지 점은 직선으로 보간한다."""
        self._stop_zoom()
        self._zoom_goal = (scale, QPointF(off))
        if not (animate and motion.enabled() and self.isVisible()):
            self._apply_view(scale, off)
            return
        pv = pivot if pivot is not None else QPointF(self.width() / 2.0, self.height() / 2.0)
        self._zoom_plan = {
            "pivot": pv, "s0": self._scale, "s1": scale,
            "i0": self._center_img(self._scale, self._off, pv),
            "i1": self._center_img(scale, off, pv),
        }
        self._zoom_anim.setDuration(motion.DUR_ZOOM_STEP if wheel else motion.DUR_MORPH)
        self._zoom_anim.setEasingCurve(motion.EASE_PRIMARY if wheel else motion.EASE_MORPH)
        self._zoom_anim.start()

    def _apply_view(self, scale: float, off: QPointF) -> None:
        self._scale, self._off = scale, QPointF(off)
        self.viewChanged.emit(self._scale)
        self.viewStateChanged.emit()
        self.update()

    def _on_zoom_tick(self, v) -> None:
        plan = self._zoom_plan
        if plan is None:
            return
        e = float(v)
        if e >= 1.0:
            scale, off = self._zoom_goal
            self._apply_view(scale, off)
            return
        s = plan["s0"] * (plan["s1"] / plan["s0"]) ** e
        i0, i1, pv = plan["i0"], plan["i1"], plan["pivot"]
        ix, iy = i0.x() + (i1.x() - i0.x()) * e, i0.y() + (i1.y() - i0.y()) * e
        self._apply_view(s, QPointF(pv.x() - ix * s, pv.y() - iy * s))

    def _stop_zoom(self) -> None:
        """도는 확대를 지금 보이는 자리에서 멈춘다(끌기·그리기·새 목표가 중간값 위에서 시작)."""
        if self._zoom_anim.state() == QVariantAnimation.State.Running:
            self._zoom_anim.stop()
        self._zoom_plan = None
        self._zoom_goal = None

    def zoom_to_box(self, box: tuple, margin: float = 1.15) -> None:
        """이미지 px 상자(left, top, w, h)가 화면에 꽉 차게 확대한다."""
        l, t, bw, bh = box
        if bw <= 0 or bh <= 0 or self.width() <= 0 or self.height() <= 0:
            return
        s = min(self.width() / (bw * margin), self.height() / (bh * margin))
        scale = max(_MIN_SCALE, min(s, _MAX_SCALE))
        cx, cy = l + bw / 2.0, t + bh / 2.0
        self._fitted = False
        self._go_to(scale, QPointF(self.width() / 2.0 - cx * scale,
                                   self.height() / 2.0 - cy * scale),
                    pivot=None, animate=True)

    def set_scale(self, scale: float, anchor: Optional[QPointF] = None) -> None:
        if self._data is None:
            return
        scale = max(_MIN_SCALE, min(scale, _MAX_SCALE))
        a = anchor if anchor is not None else QPointF(self.width() / 2.0, self.height() / 2.0)
        ix, iy = self.to_image(a.x(), a.y())       # 지금 보이는 점 — 커서 아래가 제자리
        self._fitted = False
        self._go_to(scale, QPointF(a.x() - ix * scale, a.y() - iy * scale),
                    pivot=a, animate=True, wheel=True)

    def zoom_by(self, factor: float, anchor: Optional[QPointF] = None) -> None:
        # 연속 휠은 도는 중이면 목표 배율에 쌓는다(보이는 배율에 쌓으면 휠이 먹힌다).
        base = (self._zoom_goal[0] if self._zoom_goal is not None
                and self._zoom_anim.state() == QVariantAnimation.State.Running
                else self._scale)
        self.set_scale(base * factor, anchor)

    def resizeEvent(self, e):  # noqa: N802
        if self._fitted:
            self.fit()
        super().resizeEvent(e)

    # ------------------------------------------------------------------
    # 그리기
    # ------------------------------------------------------------------
    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.VIEWER_BG or "#000000"))
        if self._qimg is None:
            self._paint_empty(p)
            p.end()
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self._scale < 1.0)
        p.translate(self._off)
        p.scale(self._scale, self._scale)
        p.drawImage(0, 0, self._qimg)
        p.restore()
        if self._scale >= _GRID_FROM:
            self._paint_grid(p)
        sel = self.rois.selected
        for r in self.rois.rois:
            if r.id != sel:
                self._paint_roi(p, r, False)
        a = self.rois.active()
        if a is not None:
            self._paint_roi(p, a, True)
        if self._drag and self._drag.get("mode") == "draw":
            self._paint_draft(p)
        self._paint_loupe(p)
        p.end()

    def _paint_empty(self, p: QPainter) -> None:
        p.setPen(QColor(theme._VIEWER_INK))
        p.setFont(QFont(theme.FONT_BODY.split(",")[0].strip('" '), 12))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, i18n.KO.GL_EMPTY_HINT)

    def _paint_grid(self, p: QPainter) -> None:
        w, h = self.image_size()
        x0 = max(0, int(np.floor(self.to_image(0, 0)[0])))
        y0 = max(0, int(np.floor(self.to_image(0, 0)[1])))
        x1 = min(w, int(np.ceil(self.to_image(self.width(), 0)[0])))
        y1 = min(h, int(np.ceil(self.to_image(0, self.height())[1])))
        dark, light = QPen(QColor(0, 0, 0, 150)), QPen(QColor(255, 255, 255, 70))
        for pen in (dark, light):
            pen.setCosmetic(True)
        for x in range(x0, x1 + 1):
            sx = self.to_screen(x, 0).x()
            p.setPen(dark)
            p.drawLine(QPointF(sx, 0), QPointF(sx, self.height()))
            p.setPen(light)
            p.drawLine(QPointF(sx + 1, 0), QPointF(sx + 1, self.height()))
        for y in range(y0, y1 + 1):
            sy = self.to_screen(0, y).y()
            p.setPen(dark)
            p.drawLine(QPointF(0, sy), QPointF(self.width(), sy))
            p.setPen(light)
            p.drawLine(QPointF(0, sy + 1), QPointF(self.width(), sy + 1))

    def _halo_pen(self, color: str, width: float, dash=False) -> tuple[QPen, QPen]:
        halo = QPen(QColor(0, 0, 0, 200), width + 2)
        pen = QPen(QColor(color), width)
        if dash:
            pen.setStyle(Qt.PenStyle.DashLine)
        for q in (halo, pen):
            q.setCosmetic(True)
        return halo, pen

    def _paint_roi(self, p: QPainter, r: Roi, active: bool) -> None:
        halo, pen = self._halo_pen(r.color, 3 if active else 2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        a, b = self.to_screen(r.x, r.y), self.to_screen(r.x + r.w, r.y + r.h)
        rect = QRectF(a, b)
        for q in (halo, pen):
            p.setPen(q)
            if r.kind == R.CIRCLE:
                p.drawEllipse(rect)
            else:
                p.drawRect(rect)
        label = r.name if not active else f"{r.name} · {r.w}×{r.h}"
        self._paint_pill(p, label, QPointF(rect.left(), rect.top() - 4), r.color, above=True)
        if active:
            for _name, c in self._handle_points(r):
                hs = _HANDLE_PX
                p.setPen(QPen(QColor(0, 0, 0), 1))
                p.setBrush(QColor(r.color))
                p.drawRect(QRectF(c.x() - hs / 2, c.y() - hs / 2, hs, hs))

    def _paint_pill(self, p: QPainter, text: str, at: QPointF, color: str, *, above: bool) -> None:
        f = QFont(theme.FONT_MONO.split(",")[0].strip('" '), 9)
        p.setFont(f)
        fm = QFontMetrics(f)
        w, h = fm.horizontalAdvance(text) + 10, fm.height() + 4
        x = max(2.0, min(at.x(), self.width() - w - 2))
        y = at.y() - h if above else at.y()
        y = max(2.0, min(y, self.height() - h - 2))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 205))
        p.drawRect(QRectF(x, y, w, h))
        p.setPen(QColor(color))
        p.drawText(QRectF(x, y, w, h), Qt.AlignmentFlag.AlignCenter, text)

    def _paint_draft(self, p: QPainter) -> None:
        d = self._drag
        r = R.from_drag(d["kind"], d["p0"], d["p1"], *self.image_size())
        if r is None:
            return
        halo, pen = self._halo_pen("#FFD60A", 2, dash=True)
        a, b = self.to_screen(r.x, r.y), self.to_screen(r.x + r.w, r.y + r.h)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for q in (halo, pen):
            p.setPen(q)
            if r.kind == R.CIRCLE:
                p.drawEllipse(QRectF(a, b))
            else:
                p.drawRect(QRectF(a, b))
        self._paint_pill(p, f"{r.w}×{r.h}", QPointF(a.x(), a.y() - 4), "#FFD60A", above=True)

    def _paint_loupe(self, p: QPainter) -> None:
        if self._cursor_img is None or self._data is None:
            return
        x, y, gl, sx, sy = self._cursor_img
        txt = i18n.KO.GL_LOUPE_FMT.format(x=x, y=y, gl=gl)
        if self._lo is not None and gl <= self._lo:
            txt += i18n.KO.GL_LOUPE_LOW
        elif self._hi is not None and gl >= self._hi:
            txt += i18n.KO.GL_LOUPE_HIGH
        self._paint_pill(p, txt, QPointF(sx + 14, sy + 16), "#E8E8E8", above=False)

    # ------------------------------------------------------------------
    # 손잡이·적중 판정
    # ------------------------------------------------------------------
    def _handle_points(self, r: Roi):
        a, b = self.to_screen(r.x, r.y), self.to_screen(r.x + r.w, r.y + r.h)
        cx, cy = (a.x() + b.x()) / 2.0, (a.y() + b.y()) / 2.0
        pts = {"nw": (a.x(), a.y()), "n": (cx, a.y()), "ne": (b.x(), a.y()),
               "e": (b.x(), cy), "se": (b.x(), b.y()), "s": (cx, b.y()),
               "sw": (a.x(), b.y()), "w": (a.x(), cy)}
        return [(n, QPointF(*pts[n])) for n in R.HANDLES]

    def _hit_handle(self, pos: QPointF) -> Optional[str]:
        a = self.rois.active()
        if a is None:
            return None
        tol = _HANDLE_PX / 2.0 + 3
        for name, c in self._handle_points(a):
            if abs(c.x() - pos.x()) <= tol and abs(c.y() - pos.y()) <= tol:
                return name
        return None

    def _hit_shape(self, pos: QPointF) -> Optional[Roi]:
        ix, iy = self.to_image(pos.x(), pos.y())
        for r in reversed(self.rois.rois):
            if r.kind == R.CIRCLE:
                cx, cy = r.center
                if ((ix - cx) / (r.w / 2.0)) ** 2 + ((iy - cy) / (r.h / 2.0)) ** 2 <= 1.0:
                    return r
            elif r.x <= ix <= r.x + r.w and r.y <= iy <= r.y + r.h:
                return r
        return None

    # ------------------------------------------------------------------
    # 도구
    # ------------------------------------------------------------------
    def set_tool(self, tool: str) -> None:
        if tool in TOOLS and tool != self.tool:
            self.tool = tool
            self._update_cursor()
            self.update()

    def _update_cursor(self) -> None:
        if self._drag and self._drag.get("mode") == "pan":
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self.tool == TOOL_MOVE or self._space:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def _select(self, rid: Optional[int]) -> None:
        if self.rois.selected != rid:
            self.rois.selected = rid
            self.selectionChanged.emit()
            self.update()

    # ------------------------------------------------------------------
    # 마우스
    # ------------------------------------------------------------------
    def mousePressEvent(self, e):  # noqa: N802
        self.setFocus()
        if self._data is None:
            return
        self._stop_zoom()
        pos = e.position()
        btn = e.button()
        if btn in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton) or (
                btn == Qt.MouseButton.LeftButton and self._space):
            self._drag = {"mode": "pan", "last": pos}
            self._update_cursor()
            return
        if btn != Qt.MouseButton.LeftButton:
            return
        h = self._hit_handle(pos)
        if h:
            self._drag = {"mode": "resize", "handle": h, "snapped": False}
            return
        shape = self._hit_shape(pos)
        draws = self.tool in (R.RECT, R.CIRCLE)
        if shape is not None and (not draws or shape.id == self.rois.selected):
            self._select(shape.id)
            self._drag = {"mode": "moveshape", "rid": shape.id, "last": self.to_image(pos.x(), pos.y()),
                          "snapped": False, "frac": (0.0, 0.0)}
            return
        if draws:
            p0 = self._pixel_at(pos.x(), pos.y())
            self._drag = {"mode": "draw", "kind": self.tool, "p0": p0, "p1": p0,
                          "press": pos, "started": False, "hit": shape}
            return
        # 이동 도구: 빈 곳 — 선택 해제하고 사진을 끈다.
        self._select(shape.id if shape is not None else None)
        self._drag = {"mode": "pan", "last": pos}
        self._update_cursor()

    def mouseMoveEvent(self, e):  # noqa: N802
        pos = e.position()
        self._track_cursor(pos)
        d = self._drag
        if d is None:
            self._hover_cursor(pos)
            return
        w, h = self.image_size()
        mode = d["mode"]
        if mode == "pan":
            last = d["last"]
            self._off = QPointF(self._off.x() + pos.x() - last.x(), self._off.y() + pos.y() - last.y())
            d["last"] = pos
            self._fitted = False
            self.viewStateChanged.emit()
            self.update()
        elif mode == "moveshape":
            r = self.rois.get(d["rid"])
            if r is None:
                return
            ix, iy = self.to_image(pos.x(), pos.y())
            fx, fy = d["frac"]
            dx, dy = ix - d["last"][0] + fx, iy - d["last"][1] + fy
            mx, my = int(dx), int(dy)           # 소수 부분은 다음 이동으로 넘긴다
            d["frac"] = (dx - mx, dy - my)
            d["last"] = (ix, iy)
            if mx or my:
                new = R.move(r, mx, my, w, h)
                if (new.x, new.y) != (r.x, r.y):
                    self._snap_once(d)
                    self.rois.replace_roi(new)
                    self.roisChanged.emit()
                    self.update()
        elif mode == "resize":
            r = self.rois.active()
            if r is None:
                return
            ix, iy = self.to_image(pos.x(), pos.y())
            new = R.resize_handle(r, d["handle"], int(round(ix)), int(round(iy)), w, h)
            if (new.x, new.y, new.w, new.h) != (r.x, r.y, r.w, r.h):
                self._snap_once(d)
                self.rois.replace_roi(new)
                self.roisChanged.emit()
                self.update()
        elif mode == "draw":
            if not d["started"]:
                if (pos - d["press"]).manhattanLength() < _DRAG_START_PX:
                    return
                d["started"] = True
            d["p1"] = self._pixel_at(pos.x(), pos.y())
            self.update()

    def mouseReleaseEvent(self, e):  # noqa: N802
        d, self._drag = self._drag, None
        if d is None:
            return
        if d["mode"] == "draw":
            if d["started"]:
                r = R.from_drag(d["kind"], d["p0"], d["p1"], *self.image_size())
                if r is not None:
                    self.rois.add(r)
                    self.selectionChanged.emit()
                    self.roisChanged.emit()
                else:
                    self._notify_too_small()
            elif d.get("hit") is not None:
                self._select(d["hit"].id)       # 그냥 클릭 — 도형 선택
            else:
                self._select(None)
        self._update_cursor()
        self.update()

    def _notify_too_small(self) -> None:
        self.setToolTip(i18n.KO.GL_TOO_SMALL)

    def _snap_once(self, d: dict) -> None:
        if not d.get("snapped"):
            self.rois.snap()
            d["snapped"] = True

    def _hover_cursor(self, pos: QPointF) -> None:
        if self._data is None or self._space:
            return
        if self._hit_handle(pos):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self._update_cursor()

    def _track_cursor(self, pos: QPointF) -> None:
        if self._data is None:
            return
        ix, iy = self.to_image(pos.x(), pos.y())
        w, h = self.image_size()
        if 0 <= ix < w and 0 <= iy < h:
            x, y = int(ix), int(iy)
            gl = int(self._data[y, x])
            self._cursor_img = (x, y, gl, pos.x(), pos.y())
            self.hoverInfo.emit(x, y, gl)
        else:
            self._cursor_img = None
            self.hoverInfo.emit(-1, -1, 0)
        self.update()

    def leaveEvent(self, e):  # noqa: N802
        self._cursor_img = None
        self.hoverInfo.emit(-1, -1, 0)
        self.update()
        super().leaveEvent(e)

    def wheelEvent(self, e):  # noqa: N802
        if self._data is None:
            return
        step = e.angleDelta().y()
        if step:
            self.zoom_by(1.18 ** (step / 120.0), e.position())
        e.accept()

    # ------------------------------------------------------------------
    # 키보드
    # ------------------------------------------------------------------
    def _snap_for_key(self, rid, kind: str) -> None:
        now = time.monotonic()
        last_rid, last_kind, last_t = self._last_key
        if not (last_rid == rid and last_kind == kind and now - last_t < _KEY_COALESCE_S):
            self.rois.snap()
        self._last_key = (rid, kind, now)

    def nudge(self, dx: int, dy: int) -> bool:
        """선택한 ROI 를 방향키만큼 옮긴다.  옮겼으면 True."""
        r = self.rois.active()
        w, h = self.image_size()
        if r is None or w <= 0:
            return False
        new = R.move(r, dx, dy, w, h)
        if new == r:
            return False
        self._snap_for_key(r.id, "move")
        self.rois.replace_roi(new)
        self.roisChanged.emit()
        self.update()
        return True

    def delete_selected(self) -> bool:
        if self.rois.delete():
            self.selectionChanged.emit()
            self.roisChanged.emit()
            self.update()
            return True
        return False

    def undo(self) -> bool:
        if self.rois.undo():
            self.selectionChanged.emit()
            self.roisChanged.emit()
            self.update()
            return True
        return False

    def keyPressEvent(self, e):  # noqa: N802
        k = e.key()
        mods = e.modifiers()
        big = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        n = 10 if big else 1
        arrows = {Qt.Key.Key_Left: (-n, 0), Qt.Key.Key_Right: (n, 0),
                  Qt.Key.Key_Up: (0, -n), Qt.Key.Key_Down: (0, n)}
        if k in arrows:
            self.nudge(*arrows[k])
            e.accept()
        elif k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
            e.accept()
        elif k == Qt.Key.Key_Z and mods & Qt.KeyboardModifier.ControlModifier:
            self.undo()
            e.accept()
        elif k == Qt.Key.Key_Escape:
            if self._drag and self._drag.get("mode") == "draw":
                self._drag = None
                self.update()
                e.accept()
            else:
                super().keyPressEvent(e)       # 대화상자 닫기 등은 위로 넘긴다
        elif k == Qt.Key.Key_F:
            self.fit(animate=True)
            e.accept()
        elif k == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = True
            self._update_cursor()
            e.accept()
        else:
            super().keyPressEvent(e)

    def keyReleaseEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = False
            self._update_cursor()
        super().keyReleaseEvent(e)
