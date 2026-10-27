"""GL 뷰어의 그래프 둘 — 히스토그램(임계값 ▼ 끌기)과 선 위치별 GL 프로파일.

데이터 가공은 ``gl.analysis`` 가 하고 여기는 그리기만 한다.  히스토그램은 GL 값(최대 65536
칸)을 화면 폭 칸으로 **누적합 차이**로 묶어 그리므로 16 bit 도 즉시 그려진다.
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF, QFontMetrics
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ... import i18n
from .. import theme

_LOW = QColor(47, 111, 224)
_HIGH = QColor(229, 50, 42)
_PAD_L, _PAD_R, _PAD_T, _PAD_B = 6, 6, 14, 18


def _mono(size: int = 9) -> QFont:
    return QFont(theme.FONT_MONO.split(",")[0].strip('" '), size)


def _nice_ticks(a: float, b: float, n: int = 5) -> list[int]:
    if b <= a:
        return [int(a)]
    return [int(round(a + (b - a) * i / (n - 1))) for i in range(n)]


class HistogramChart(QWidget):
    """GL 분포.  ▼ 표시를 끌어 임계값을 옮긴다(``thresholdDragged(which, 값)``)."""

    thresholdDragged = pyqtSignal(str, int)     # "lo" | "hi", GL
    hoverText = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._series: list[tuple[str, str, np.ndarray]] = []   # (이름, 색, 히스토그램)
        self._lo = 0
        self._hi = 1
        self._levels = 256
        self._log = False
        self._range_data = False
        self._normalize = False
        self._drag: Optional[str] = None
        self._plot = QRectF()
        self._domain = (0, 255)
        self.setMouseTracking(True)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName(i18n.KO.GL_HIST)

    # -- 설정 ------------------------------------------------------------
    def set_series(self, series: Sequence[tuple[str, str, np.ndarray]], levels: int,
                   *, normalize: bool = False) -> None:
        """``series`` — [(이름, 색, 히스토그램)].  둘 이상이면 겹쳐 그리고 ``normalize`` 로 % 로 맞춘다."""
        self._series = [(n, c, np.asarray(h)) for n, c, h in series]
        self._levels = int(levels)
        self._normalize = normalize
        self._update_domain()
        self.update()

    def set_thresholds(self, lo: int, hi: int) -> None:
        self._lo, self._hi = int(lo), int(hi)
        self.update()

    def set_log(self, on: bool) -> None:
        self._log = bool(on)
        self.update()

    def set_range_data(self, on: bool) -> None:
        self._range_data = bool(on)
        self._update_domain()
        self.update()

    def domain(self) -> tuple[int, int]:
        return self._domain

    def _update_domain(self) -> None:
        top = self._levels - 1
        if not self._range_data or not self._series:
            self._domain = (0, top)
            return
        nz = [np.flatnonzero(h) for _n, _c, h in self._series if h.size and h.any()]
        if not nz:
            self._domain = (0, top)
            return
        a = min(int(v[0]) for v in nz)
        b = max(int(v[-1]) for v in nz)
        m = max(1, int((b - a) * 0.05))
        self._domain = (max(0, a - m), min(top, b + m))

    # -- 좌표 ------------------------------------------------------------
    def _gl_x(self, gl: float) -> float:
        a, b = self._domain
        return self._plot.left() + (gl - a) / max(1, (b - a)) * self._plot.width()

    def _x_gl(self, x: float) -> int:
        a, b = self._domain
        v = a + (x - self._plot.left()) / max(1.0, self._plot.width()) * (b - a)
        return int(max(0, min(round(v), self._levels - 1)))

    def _layout(self) -> None:
        self._plot = QRectF(_PAD_L, _PAD_T, max(10, self.width() - _PAD_L - _PAD_R),
                            max(10, self.height() - _PAD_T - _PAD_B))

    # -- 그리기 ----------------------------------------------------------
    def paintEvent(self, e):  # noqa: N802
        self._layout()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.ELEV or "#FBFAF7"))
        pl = self._plot
        W = max(1, int(pl.width()))
        a, b = self._domain
        edges = np.linspace(a, b + 1, W + 1).astype(np.int64)
        cols = []
        for name, color, h in self._series:
            c = np.concatenate([[0], np.cumsum(h)])
            cnt = (c[np.minimum(edges[1:], len(h))] - c[np.minimum(edges[:-1], len(h))]).astype(np.float64)
            if self._normalize and h.sum() > 0:
                cnt = cnt / float(h.sum()) * 100.0
            cols.append((name, color, cnt))
        peak = max((float(c.max()) for _n, _c, c in cols if c.size), default=0.0)
        scale = (lambda v: np.log1p(v)) if self._log else (lambda v: v)
        top = float(scale(peak)) if peak > 0 else 1.0
        grid = QPen(QColor(theme.LINE2 or "#B7B2A6"), 1)
        p.setPen(grid)
        for i in range(1, 4):
            y = pl.top() + pl.height() * i / 4.0
            p.drawLine(QPointF(pl.left(), y), QPointF(pl.right(), y))
        multi = len(cols) > 1
        for ci, (name, color, cnt) in enumerate(cols):
            hts = scale(cnt) / top * pl.height()
            if multi:
                col = QColor(color)
                pen = QPen(col, 2)
                p.setPen(pen)
                pts = [QPointF(pl.left() + i + 0.5, pl.bottom() - float(hts[i])) for i in range(W)]
                for i in range(W - 1):
                    p.drawLine(pts[i], pts[i + 1])
                continue
            for i in range(W):
                if hts[i] <= 0:
                    continue
                gl_mid = (edges[i] + edges[i + 1] - 1) / 2.0
                if gl_mid <= self._lo:
                    col = _LOW
                elif gl_mid >= self._hi:
                    col = _HIGH
                else:
                    col = QColor(theme.MUTE or "#5A574E")
                p.fillRect(QRectF(pl.left() + i, pl.bottom() - hts[i], 1.0, hts[i]), col)
        # 임계값 ▼
        for which, gl, col in (("lo", self._lo, _LOW), ("hi", self._hi, _HIGH)):
            if not (a <= gl <= b):
                continue
            x = self._gl_x(gl)
            pen = QPen(col, 2)
            p.setPen(pen)
            p.drawLine(QPointF(x, pl.top()), QPointF(x, pl.bottom()))
            p.setBrush(col)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(QPolygonF([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x, 10)]))
        # 눈금
        p.setFont(_mono(8))
        p.setPen(QColor(theme.MUTE or "#5A574E"))
        fm = QFontMetrics(p.font())
        for t in _nice_ticks(a, b):
            x = self._gl_x(t)
            txt = f"{t:,}"
            tw = fm.horizontalAdvance(txt)
            p.drawText(QPointF(max(2.0, min(x - tw / 2, self.width() - tw - 2)), self.height() - 4), txt)
        p.end()

    # -- 마우스 ----------------------------------------------------------
    def _nearest(self, x: float) -> Optional[str]:
        near = []
        for which, gl in (("lo", self._lo), ("hi", self._hi)):
            d = abs(self._gl_x(gl) - x)
            if d <= 9:
                near.append((d, which))
        return min(near)[1] if near else None

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._layout()
        x = e.position().x()
        which = self._nearest(x)
        if which is None:                 # 그래프를 클릭하면 가까운 쪽 임계값이 거기로 온다
            which = "lo" if abs(x - self._gl_x(self._lo)) <= abs(x - self._gl_x(self._hi)) else "hi"
        self._drag = which
        self.thresholdDragged.emit(which, self._x_gl(x))

    def mouseMoveEvent(self, e):  # noqa: N802
        self._layout()
        x = e.position().x()
        if self._drag:
            self.thresholdDragged.emit(self._drag, self._x_gl(x))
            return
        self.setCursor(Qt.CursorShape.SizeHorCursor if self._nearest(x) else Qt.CursorShape.ArrowCursor)
        if self._plot.left() <= x <= self._plot.right() and self._series:
            gl = self._x_gl(x)
            parts = []
            for name, _c, h in self._series:
                n = int(h[gl]) if gl < len(h) else 0
                tot = int(h.sum()) or 1
                fmt = (i18n.KO.GL_HIST_HOVER_ITEM_FMT if len(self._series) > 1
                       else i18n.KO.GL_HIST_HOVER_ONE_FMT)
                parts.append(fmt.format(name=name, n=n, pct=n / tot * 100))
            self.hoverText.emit(i18n.KO.GL_HIST_HOVER_FMT.format(gl=gl, items=" · ".join(parts)))

    def mouseReleaseEvent(self, e):  # noqa: N802
        self._drag = None

    def leaveEvent(self, e):  # noqa: N802
        self.hoverText.emit("")
        super().leaveEvent(e)


class ProfileChart(QWidget):
    """선 위치별 GL.  선이 여럿이면 시작점 기준으로 겹쳐 그린다."""

    hoverText = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._lines: list[tuple[str, str, np.ndarray]] = []    # (이름, 색, 값)
        self._lo = 0
        self._hi = 1
        self._levels = 256
        self._hover: Optional[int] = None
        self.setMouseTracking(True)
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName(i18n.KO.GL_PROFILE)

    def set_lines(self, lines: Sequence[tuple[str, str, np.ndarray]], levels: int) -> None:
        self._lines = [(n, c, np.asarray(v)) for n, c, v in lines if len(v)]
        self._levels = int(levels)
        self.update()

    def set_thresholds(self, lo: int, hi: int) -> None:
        self._lo, self._hi = int(lo), int(hi)
        self.update()

    def _plot(self) -> QRectF:
        return QRectF(_PAD_L, 8, max(10, self.width() - _PAD_L - _PAD_R),
                      max(10, self.height() - 8 - _PAD_B))

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.ELEV or "#FBFAF7"))
        pl = self._plot()
        if not self._lines:
            p.setPen(QColor(theme.MUTE or "#5A574E"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, i18n.KO.GL_PROFILE_EMPTY)
            p.end()
            return
        top = self._levels - 1
        n_max = max(len(v) for _n, _c, v in self._lines)
        y_of = lambda gl: pl.bottom() - gl / max(1, top) * pl.height()  # noqa: E731
        # 판정 구간 띠
        lo_c, hi_c = QColor(_LOW), QColor(_HIGH)
        lo_c.setAlpha(34)
        hi_c.setAlpha(34)
        p.fillRect(QRectF(pl.left(), y_of(self._lo), pl.width(), pl.bottom() - y_of(self._lo)), lo_c)
        p.fillRect(QRectF(pl.left(), pl.top(), pl.width(), y_of(self._hi) - pl.top()), hi_c)
        W = max(1, int(pl.width()))
        for name, color, v in self._lines:
            pen = QPen(QColor(color), 1.6)
            p.setPen(pen)
            n = len(v)
            if n <= W * 2:
                pts = [QPointF(pl.left() + (i + 0.5) / n_max * pl.width(), y_of(float(v[i])))
                       for i in range(n)]
                for i in range(n - 1):
                    p.drawLine(pts[i], pts[i + 1])
                if n <= W // 3:
                    p.setBrush(QColor(color))
                    for q, val in zip(pts, v):
                        if val <= self._lo or val >= self._hi:
                            p.setBrush(_LOW if val <= self._lo else _HIGH)
                            p.drawEllipse(q, 3.0, 3.0)
            else:                          # 점이 너무 많으면 칸마다 최소~최대 막대로 줄인다
                edges = np.linspace(0, n, W + 1).astype(int)
                for i in range(W):
                    seg = v[edges[i]:max(edges[i + 1], edges[i] + 1)]
                    x = pl.left() + i + 0.5
                    p.drawLine(QPointF(x, y_of(float(seg.min()))), QPointF(x, y_of(float(seg.max()))))
        if self._hover is not None and self._hover < n_max:
            x = pl.left() + (self._hover + 0.5) / n_max * pl.width()
            p.setPen(QPen(QColor(theme.INK or "#1B1A17"), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x, pl.top()), QPointF(x, pl.bottom()))
        p.setFont(_mono(8))
        p.setPen(QColor(theme.MUTE or "#5A574E"))
        p.drawText(QPointF(4, self.height() - 4), "0")
        endt = f"{n_max - 1:,}"
        fm = QFontMetrics(p.font())
        p.drawText(QPointF(self.width() - fm.horizontalAdvance(endt) - 4, self.height() - 4), endt)
        p.end()

    def mouseMoveEvent(self, e):  # noqa: N802
        if not self._lines:
            return
        pl = self._plot()
        n_max = max(len(v) for _n, _c, v in self._lines)
        i = int((e.position().x() - pl.left()) / max(1.0, pl.width()) * n_max)
        i = max(0, min(i, n_max - 1))
        self._hover = i
        vals = [f"{n} {int(v[i]):,}" for n, _c, v in self._lines if i < len(v)]
        self.hoverText.emit(i18n.KO.GL_PROFILE_HOVER_FMT.format(i=i, vals=" · ".join(vals)))
        self.update()

    def leaveEvent(self, e):  # noqa: N802
        self._hover = None
        self.hoverText.emit("")
        self.update()
        super().leaveEvent(e)
