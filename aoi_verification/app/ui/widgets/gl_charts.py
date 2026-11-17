"""GL 뷰어의 그래프 — 히스토그램(임계값 ▼ 끌기)과 임계값 막대.

데이터 가공은 ``gl.analysis`` 가 하고 여기는 그리기만 한다.  히스토그램은 GL 값(최대 65536
칸)을 화면 폭 칸으로 **누적합 차이**로 묶어 그리므로 16 bit 도 즉시 그려진다.
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen,
                         QPolygonF)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ... import i18n
from ...gl import analysis
from .. import theme

_LOW = QColor(47, 111, 224)
_HIGH = QColor(229, 50, 42)
_X_PAD = 8          # 좌우 여백 — 위 임계값 막대(ThresholdRange._PAD)와 같아야 손잡이와 임계선이 세로로 맞는다
ALL_ROW = -1        # 줄 모드에서 '사진 전체' 줄의 번호(ROI 번호는 1 부터)

_TAG_H, _NAME_H, _RATIO_H = 18, 16, 18
_ROW_HEAD = _NAME_H + _RATIO_H          # 줄마다 이름 줄 + 비율 줄
_ROW_GAP = 8
_BAND_H = 6
_TICK_H = 14
_BOTTOM = _BAND_H + 6 + _TICK_H + 2     # 바닥선 아래: 띠 + 눈금


def _mono(size: int = 9) -> QFont:
    return QFont(theme.FONT_MONO.split(",")[0].strip('" '), size)


def _px_font(px: int, *, mono: bool = False, bold: bool = False) -> QFont:
    f = _mono() if mono else QFont()
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def _nice_ticks(a: float, b: float, n: int = 5) -> list[int]:
    if b <= a:
        return [int(a)]
    return [int(round(a + (b - a) * i / (n - 1))) for i in range(n)]


def pct_text(v: float) -> str:
    """구역 비율 글자 — 소수 1자리, 0 은 '0%'."""
    return "0%" if v <= 0 else f"{v:.1f}%"


def spread_labels(centers: Sequence[float], widths: Sequence[float], left: float, right: float,
                  gap: float = 4.0) -> list[float]:
    """가운데 맞춤 글자들의 왼쪽 x — 서로 ``gap`` 이상 떨어지고 [left, right] 안에 든다.

    구역이 좁아 겹치면 왼쪽에서 오른쪽으로 밀고, 오른쪽 끝을 넘으면 다시 왼쪽으로 민다."""
    xs = [c - w / 2.0 for c, w in zip(centers, widths)]
    n = len(xs)
    for i in range(n):
        xs[i] = max(xs[i], left)
        if i:
            xs[i] = max(xs[i], xs[i - 1] + widths[i - 1] + gap)
    for i in range(n - 1, -1, -1):
        lim = right if i == n - 1 else xs[i + 1] - gap
        xs[i] = min(xs[i], lim - widths[i])
    for i in range(n):                   # 전부 못 들어가는 극단 — 왼쪽은 지킨다
        xs[i] = max(xs[i], left)
    return xs


def stack_heights(n: int, selected: Optional[int], avail: float, min_h: float) -> list[float]:
    """줄 쌓기의 그래프 높이 — 선택한 줄이 두 배 가중, 합이 ``avail``(모자라도 줄마다 ``min_h`` 는 지킨다)."""
    w = [2.0 if i == selected else 1.0 for i in range(n)]
    tot = sum(w) or 1.0
    return [max(float(min_h), avail * x / tot) for x in w]


class HistogramChart(QWidget):
    """GL 분포 — 구역 색으로 칠한 계단 면 + 임계선(꼬리표) + 구역 비율 + 밝기 띠.

    임계선을 끌어 임계값을 옮긴다(``thresholdDragged(which, 값)``).  ``set_rows`` 를 주면 같은 자리에서
    줄(전체 · ROI 별)을 쌓고, 줄을 누르면 ``rowClicked(번호)`` 를 낸다."""

    thresholdDragged = pyqtSignal(str, int)     # "lo" | "hi", GL
    rowClicked = pyqtSignal(int)                # ALL_ROW 또는 ROI 번호
    hoverText = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._series: list[tuple[str, str, np.ndarray]] = []   # (이름, 색, 히스토그램)
        self._rows: list[tuple[int, str, str, np.ndarray]] = []   # (번호, 이름, 색, 히스토그램)
        self._sel_row: Optional[int] = None
        self._lo = 0
        self._hi = 1
        self._levels = 256
        self._range_data = False
        self._normalize = False
        self._drag: Optional[str] = None
        self._plot = QRectF()
        self._row_rects: list[QRectF] = []       # 줄 전체(이름·비율 포함)
        self._row_plots: list[QRectF] = []       # 줄의 그래프 칸
        self._domain = (0, 255)
        self.setMouseTracking(True)
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName(i18n.KO.GL_HIST)

    # -- 설정 ------------------------------------------------------------
    def set_series(self, series: Sequence[tuple[str, str, np.ndarray]], levels: int,
                   *, normalize: bool = False) -> None:
        """``series`` — [(이름, 색, 히스토그램)].  둘 이상이면 겹쳐 그리고 ``normalize`` 로 % 로 맞춘다."""
        self._series = [(n, c, np.asarray(h)) for n, c, h in series]
        self._rows = []
        self._levels = int(levels)
        self._normalize = normalize
        self._update_domain()
        self.update()

    def set_rows(self, rows: Sequence[tuple[int, str, str, np.ndarray]], levels: int,
                 selected: Optional[int]) -> None:
        """줄 쌓기 — [(번호, 이름, 색, 히스토그램)].  ``selected`` 는 밝게·크게 그린다."""
        self._rows = [(int(i), n, c, np.asarray(h)) for i, n, c, h in rows]
        self._sel_row = selected
        self._series = []
        self._levels = int(levels)
        self._update_domain()
        self.update()

    def stack_mode(self) -> bool:
        return bool(self._rows)

    def rows(self) -> list[tuple[int, str, str, np.ndarray]]:
        return list(self._rows)

    def selected_row(self) -> Optional[int]:
        return self._sel_row

    def row_rects(self) -> list[QRectF]:
        self._layout()
        return list(self._row_rects)

    def set_thresholds(self, lo: int, hi: int) -> None:
        self._lo, self._hi = int(lo), int(hi)
        self.update()

    def set_range_data(self, on: bool) -> None:
        self._range_data = bool(on)
        self._update_domain()
        self.update()

    def domain(self) -> tuple[int, int]:
        return self._domain

    def _hists(self) -> list[np.ndarray]:
        return [r[3] for r in self._rows] if self._rows else [h for _n, _c, h in self._series]

    def _update_domain(self) -> None:
        top = self._levels - 1
        hs = self._hists()
        if not self._range_data or not hs:
            self._domain = (0, top)
            return
        nz = [np.flatnonzero(h) for h in hs if h.size and h.any()]
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

    def _line_x(self, gl: float) -> float:
        return min(self._plot.right(), max(self._plot.left(), self._gl_x(gl)))

    def _layout(self) -> None:
        w, h = float(self.width()), float(self.height())
        left, width = float(_X_PAD), max(10.0, w - 2 * _X_PAD)
        bottom = h - _BOTTOM
        self._row_rects, self._row_plots = [], []
        if self._rows:
            n = len(self._rows)
            top = _TAG_H + 4 + _NAME_H + 4
            sel = next((i for i, r in enumerate(self._rows) if r[0] == self._sel_row), None)
            avail = bottom - top - n * (_ROW_HEAD + _ROW_GAP) + _ROW_GAP
            hs = stack_heights(n, sel, avail, 14.0)
            y = top
            for ph in hs:
                self._row_rects.append(QRectF(0, y, w, _ROW_HEAD + ph))
                self._row_plots.append(QRectF(left, y + _ROW_HEAD, width, ph))
                y += _ROW_HEAD + ph + _ROW_GAP
            self._plot = QRectF(left, self._row_plots[0].top(), width,
                                self._row_plots[-1].bottom() - self._row_plots[0].top())
            return
        top = _NAME_H + (0 if len(self._series) > 1 else _RATIO_H) + 4 + _TAG_H + 2
        self._plot = QRectF(left, top, width, max(10.0, bottom - top))
        self._row_plots = [self._plot]

    # -- 그리기 ----------------------------------------------------------
    def _bars(self, p: QPainter, h: np.ndarray, pl: QRectF) -> None:
        """한 분포를 구역 색의 채운 계단 면으로 — 화면 칸이 GL 칸보다 적으면 묶고, 많으면 GL 한 칸이 여러 칸 폭이다."""
        a, b = self._domain
        W = max(1, int(pl.width()))
        span = b - a
        nlv = len(h)
        if span + 1 > W:
            ef = np.linspace(a - 0.5, b + 0.5, W + 1)
            edges = np.clip(np.ceil(ef), 0, nlv).astype(np.int64)
            xs = pl.left() + np.arange(W + 1, dtype=np.float64)
        else:
            edges = np.arange(a, b + 2, dtype=np.int64)
            edges = np.clip(edges, 0, nlv)
            xs = np.round(pl.left() + (np.arange(a, b + 2) - 0.5 - a) / max(1, span) * pl.width())
            xs = np.clip(xs, pl.left(), pl.right())
        c = np.concatenate([[0], np.cumsum(h)])
        cnt = (c[edges[1:]] - c[edges[:-1]]).astype(np.float64)
        peak = float(cnt.max()) if cnt.size else 0.0
        if peak <= 0:                      # 줄마다 자기 최댓값에 맞춘다(줄 사이는 모양, 양은 비율 글자)
            return
        hts = cnt / peak * pl.height()
        binned = span + 1 > W
        mid = (edges[:-1] + edges[1:] - 1) / 2.0 if binned else edges[:-1].astype(np.float64)
        mid_col = QColor(theme.INK2 or "#3D3B35")
        for i in range(len(cnt)):
            if hts[i] <= 0:
                continue
            col = _LOW if mid[i] <= self._lo else _HIGH if mid[i] >= self._hi else mid_col
            p.fillRect(QRectF(xs[i], pl.bottom() - hts[i], max(1.0, xs[i + 1] - xs[i]), hts[i]),
                       col)

    def _binned_counts(self, h: np.ndarray, pl: QRectF) -> tuple[np.ndarray, np.ndarray]:
        a, b = self._domain
        W = max(1, int(pl.width()))
        ef = np.linspace(a - 0.5, b + 0.5, W + 1)
        edges = np.clip(np.ceil(ef), 0, len(h)).astype(np.int64)
        c = np.concatenate([[0], np.cumsum(h)])
        return (c[edges[1:]] - c[edges[:-1]]).astype(np.float64), edges

    def _zone_backdrop(self, p: QPainter, pl: QRectF) -> None:
        xl, xh = self._line_x(self._lo), self._line_x(self._hi)
        lo_c, hi_c = QColor(_LOW), QColor(_HIGH)
        lo_c.setAlpha(26)
        hi_c.setAlpha(26)
        p.fillRect(QRectF(pl.left(), pl.top(), max(0.0, xl - pl.left()), pl.height()), lo_c)
        p.fillRect(QRectF(xh, pl.top(), max(0.0, pl.right() - xh), pl.height()), hi_c)

    def paintEvent(self, e):  # noqa: N802
        self._layout()
        p = QPainter(self)
        panel = QColor(theme.PANEL or "#F4F2EC")
        mute = QColor(theme.MUTE or "#5A574E")
        ink = QColor(theme.INK or "#1B1A17")
        p.fillRect(self.rect(), panel)
        pl = self._plot
        a, b = self._domain
        xl, xh = self._line_x(self._lo), self._line_x(self._hi)
        stack = self.stack_mode()
        multi = len(self._series) > 1
        hair = QPen(QColor(theme.LINE_STRONG or "#B7B2A6"), 1)

        empty = not stack and not self._series      # 사진 전이면 값이 없다 — 바닥선 · 띠 · 눈금만

        # 1) 구역 이름 · 비율의 자리(가운데)
        zones = [(pl.left(), xl), (xl, xh), (xh, pl.right())]
        names = (i18n.KO.GL_ZONE_LOW, i18n.KO.GL_ZONE_MID, i18n.KO.GL_ZONE_HIGH)
        f_name, f_ratio = _px_font(12), _px_font(13, mono=True, bold=True)
        fm_n, fm_r = QFontMetrics(f_name), QFontMetrics(f_ratio)

        def put_ratios(y: float, h: np.ndarray, strong: bool, with_names: bool) -> None:
            r = analysis.zone_ratios(h, self._lo, self._hi) if h is not None else None
            texts = [pct_text(v) for v in r] if r else ["" for _ in zones]
            ws = [max(fm_n.horizontalAdvance(n) if with_names else 0,
                      fm_r.horizontalAdvance(t)) + 2 for n, t in zip(names, texts)]
            xs = spread_labels([(z0 + z1) / 2 for z0, z1 in zones], ws, pl.left(), pl.right(), 6)
            for i, (n, t) in enumerate(zip(names, texts)):
                if with_names:
                    p.setFont(f_name)
                    p.setPen(mute)
                    p.drawText(QRectF(xs[i], y, ws[i], _NAME_H), Qt.AlignmentFlag.AlignCenter, n)
                if t:
                    p.setFont(f_ratio)
                    p.setPen(ink if strong else QColor(theme.INK2 or "#3D3B35"))
                    p.drawText(QRectF(xs[i], y + (_NAME_H if with_names else 0), ws[i], _RATIO_H),
                               Qt.AlignmentFlag.AlignCenter, t)

        # 2) 본체
        if stack:
            ymax = self._row_plots[-1].bottom()
            for k, (rid, name, color, h) in enumerate(self._rows):
                rr, rp = self._row_rects[k], self._row_plots[k]
                sel = rid == self._sel_row
                if sel:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(theme.ELEV or "#FBFAF7"))
                    p.drawRoundedRect(rr.adjusted(0, -4, 0, 4), 6, 6)
                self._zone_backdrop(p, rp)
                self._bars(p, h, rp)
                p.setPen(hair)
                p.drawLine(QPointF(rp.left(), rp.bottom() + 0.5), QPointF(rp.right(), rp.bottom() + 0.5))
                sw = QRectF(pl.left(), rr.top() + 3, 10, 10)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(color))
                p.drawRoundedRect(sw, 2, 2)
                p.setFont(f_name)
                p.setPen(ink if sel else mute)
                label = fm_n.elidedText(name, Qt.TextElideMode.ElideRight, int(pl.width() - 16))
                p.drawText(QRectF(pl.left() + 16, rr.top(), pl.width() - 16, _NAME_H),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
                put_ratios(rr.top() + _NAME_H, h, sel, False)
            put_ratios(_TAG_H + 4, None, False, True)           # 구역 이름은 맨 위 한 번
            line_top, line_bot, base = float(_TAG_H), ymax, ymax
        else:
            one = self._series[0][2] if len(self._series) == 1 else None
            if not empty:
                if multi:                           # 둘 이상(겹침·비교)은 비율이 없어 구역 이름 대신 범례
                    self._legend(p, f_name, mute, pl)
                else:
                    put_ratios(0, one, True, True)
                self._zone_backdrop(p, pl)
            if multi:
                self._lines(p, pl)
            elif self._series:
                self._bars(p, self._series[0][2], pl)
            p.setPen(hair)
            p.drawLine(QPointF(pl.left(), pl.bottom() + 0.5), QPointF(pl.right(), pl.bottom() + 0.5))
            line_top, line_bot, base = pl.top() - 2, pl.bottom(), pl.bottom()

        # 3) 임계선 1.5px + 꼬리표
        tags = []
        for which, gl, col, x, txt in (("lo", self._lo, _LOW, xl, f"≤ {self._lo:,}"),
                                       ("hi", self._hi, _HIGH, xh, f"≥ {self._hi:,}")):
            if empty:
                continue
            p.setPen(QPen(col, 1.5))
            p.drawLine(QPointF(x, line_top), QPointF(x, line_bot))
            tags.append((col, x, txt))
        f_tag = _px_font(12, mono=True, bold=True)
        fm_t = QFontMetrics(f_tag)
        ws = [fm_t.horizontalAdvance(t) + 12 for _c, _x, t in tags]
        txs = spread_labels([t[1] for t in tags], ws, 0.0, float(self.width()), 4) if tags else []
        ty = (0.0 if stack else float(pl.top() - 2 - _TAG_H))
        p.setFont(f_tag)
        for (col, _x, txt), tx, w in zip(tags, txs, ws):
            box = QRectF(tx, ty, w, _TAG_H)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(box, 4, 4)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, txt)

        # 4) 밝기 띠 + 눈금
        top_v = float(max(1, self._levels - 1))
        g = QLinearGradient(pl.left(), 0, pl.right(), 0)
        for pos, gl in ((0.0, a), (1.0, b)):
            v = int(round(gl / top_v * 255))
            g.setColorAt(pos, QColor(v, v, v))
        band = QRectF(pl.left(), base + 6, pl.width(), _BAND_H)
        p.fillRect(band, g)
        p.setPen(hair)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(band.adjusted(0.5, 0.5, -0.5, -0.5))
        p.setFont(_px_font(11, mono=True))
        p.setPen(mute)
        fm = QFontMetrics(p.font())
        for t in _nice_ticks(a, b):
            x = self._gl_x(t)
            txt = f"{t:,}"
            tw = fm.horizontalAdvance(txt)
            p.drawText(QPointF(max(2.0, min(x - tw / 2, self.width() - tw - 2)),
                               band.bottom() + 3 + fm.ascent()), txt)
        p.end()

    def _legend(self, p: QPainter, font: QFont, ink: QColor, pl: QRectF) -> None:
        """분포 선 색 견본(■) + 이름 — 제목 줄에 왼쪽부터, 자리가 모자라면 거기서 멈춘다."""
        fm = QFontMetrics(font)
        p.setFont(font)
        x = pl.left()
        for name, color, _h in self._series:
            w = 12 + fm.horizontalAdvance(name)
            if x + w > pl.right():
                break
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(x, (_NAME_H - 8) / 2.0, 8, 8), 1.5, 1.5)
            p.setPen(ink)
            p.drawText(QRectF(x + 12, 0, w - 12 + 2, _NAME_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)
            x += w + 14

    def _lines(self, p: QPainter, pl: QRectF) -> None:
        """겹쳐 보기·A/B 비교 — 분포마다 2px 선(합이 1 이 되게 %로 맞춘다)."""
        a, b = self._domain
        per_gl = (b - a + 1) <= max(1, int(pl.width()))      # GL 칸이 화면 칸보다 적으면 GL 한 칸이 한 점
        series = []
        for name, color, h in self._series:
            if per_gl:
                cnt = h[a:b + 1].astype(np.float64)
                xs = [self._gl_x(g) for g in range(a, b + 1)]
            else:
                cnt, _e = self._binned_counts(h, pl)
                xs = [pl.left() + i + 0.5 for i in range(len(cnt))]
            if self._normalize and h.sum() > 0:
                cnt = cnt / float(h.sum()) * 100.0
            series.append((color, xs, cnt))
        peak = max((float(c.max()) for _c, _x, c in series if c.size), default=0.0)
        top = peak if peak > 0 else 1.0
        for color, xs, cnt in series:
            hts = cnt / top * pl.height()
            p.setPen(QPen(QColor(color), 2))
            pts = [QPointF(x, pl.bottom() - float(v)) for x, v in zip(xs, hts)]
            for i in range(len(pts) - 1):
                p.drawLine(pts[i], pts[i + 1])

    # -- 마우스 ----------------------------------------------------------
    def _nearest(self, x: float) -> Optional[str]:
        near = []
        for which, gl in (("lo", self._lo), ("hi", self._hi)):
            d = abs(self._line_x(gl) - x)
            if d <= 9:
                near.append((d, which))
        return min(near)[1] if near else None

    def _row_at(self, y: float) -> Optional[int]:
        for rr, row in zip(self._row_rects, self._rows):
            if rr.top() <= y <= rr.bottom():
                return row[0]
        return None

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._layout()
        x = e.position().x()
        which = self._nearest(x)
        if self.stack_mode() and which is None:     # 줄을 누르면 그 ROI 를 고른다
            rid = self._row_at(e.position().y())
            if rid is not None:
                self.rowClicked.emit(rid)
            return
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
        if self._nearest(x):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif self.stack_mode() and self._row_at(e.position().y()) is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        if self.stack_mode():
            rid = self._row_at(e.position().y())
            hover = [(n, c, h) for i, n, c, h in self._rows if i == rid]
        else:
            hover = self._series
        if self._plot.left() <= x <= self._plot.right() and hover:
            gl = self._x_gl(x)
            parts = []
            for name, _c, h in hover:
                n = int(h[gl]) if gl < len(h) else 0
                tot = int(h.sum()) or 1
                fmt = (i18n.KO.GL_HIST_HOVER_ITEM_FMT if len(hover) > 1 or self.stack_mode()
                       else i18n.KO.GL_HIST_HOVER_ONE_FMT)
                parts.append(fmt.format(name=name, n=n, pct=n / tot * 100))
            self.hoverText.emit(i18n.KO.GL_HIST_HOVER_FMT.format(gl=gl, items=" · ".join(parts)))

    def mouseReleaseEvent(self, e):  # noqa: N802
        self._drag = None

    def leaveEvent(self, e):  # noqa: N802
        self.hoverText.emit("")
        super().leaveEvent(e)


class ThresholdRange(QWidget):
    """임계값 막대 — 0~최댓값 위의 손잡이 둘(하한 파랑 · 상한 빨강)을 끌어 옮긴다.

    막대 가운데는 그 구간의 밝기(어두움→밝음)를 그대로 칠해 손잡이가 어디쯤인지 보여 준다.
    값 검증(하한 < 상한)은 받는 쪽이 한다 — 여기서는 ``moved(which, 값)`` 만 낸다.
    방향키 ←→ 는 마지막에 잡은 손잡이를 1(Shift 10) 옮기고, 숫자를 두 번 누르면
    ``editRequested(which, 숫자 자리)`` 로 직접 입력을 청한다.
    """

    moved = pyqtSignal(str, int)                # "lo" | "hi", GL
    editRequested = pyqtSignal(str, QRectF)     # 어느 쪽, 숫자가 그려진 자리

    _PAD = _X_PAD                               # 손잡이 반폭 — 끝 값에서도 잘리지 않게
    _TRACK_Y, _TRACK_H = 26, 12

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._lo, self._hi, self._levels = 0, 1, 256
        self._active = "lo"
        self._drag: Optional[str] = None
        self._labels: dict[str, QRectF] = {}
        self.setFixedHeight(62)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(i18n.KO.GL_THRESHOLD)
        self.setToolTip(i18n.KO.GL_THR_EDIT_TIP)

    def set_levels(self, levels: int) -> None:
        self._levels = max(2, int(levels))
        self.update()

    def set_thresholds(self, lo: int, hi: int) -> None:
        self._lo, self._hi = int(lo), int(hi)
        self.update()

    def active(self) -> str:
        return self._active

    # -- 좌표 ------------------------------------------------------------
    def _span(self) -> tuple[float, float]:
        return float(self._PAD), float(max(self._PAD + 10, self.width() - self._PAD))

    def gl_x(self, gl: float) -> float:
        a, b = self._span()
        return a + gl / float(self._levels - 1) * (b - a)

    def x_gl(self, x: float) -> int:
        a, b = self._span()
        v = (x - a) / (b - a) * (self._levels - 1)
        return int(max(0, min(round(v), self._levels - 1)))

    def _nearest(self, x: float) -> str:
        return "lo" if abs(x - self.gl_x(self._lo)) <= abs(x - self.gl_x(self._hi)) else "hi"

    # -- 그리기 ----------------------------------------------------------
    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a, b = self._span()
        xl, xh = self.gl_x(self._lo), self.gl_x(self._hi)
        y, h = float(self._TRACK_Y), float(self._TRACK_H)
        top = float(self._levels - 1)
        p.setPen(Qt.PenStyle.NoPen)
        if not self.isEnabled():               # 사진이 없으면 값도 없다 — 빈 막대만
            p.fillRect(QRectF(a, y, b - a, h), QColor(theme.LINE2 or "#B7B2A6"))
            p.end()
            return
        p.fillRect(QRectF(a, y, max(0.0, xl - a), h), _LOW)
        p.fillRect(QRectF(xh, y, max(0.0, b - xh), h), _HIGH)
        if xh > xl:
            g = QLinearGradient(xl, 0, xh, 0)
            for pos, gl in ((0.0, self._lo), (1.0, self._hi)):
                v = int(round(gl / top * 255))
                g.setColorAt(pos, QColor(v, v, v))
            p.fillRect(QRectF(xl, y, xh - xl, h), g)
        f = _mono(9)
        f.setBold(True)
        fm = QFontMetrics(f)
        rects = {}
        for which, x, gl in (("lo", xl, self._lo), ("hi", xh, self._hi)):
            txt = f"{gl:,}"
            w = fm.horizontalAdvance(txt) + 4
            rects[which] = [max(0.0, min(x - w / 2, self.width() - w)), w, txt]
        # 두 숫자가 겹치면 하한은 왼쪽, 상한은 오른쪽으로 벌린다
        gap = rects["lo"][0] + rects["lo"][1] + 4 - rects["hi"][0]
        if gap > 0:
            rects["lo"][0] = max(0.0, rects["lo"][0] - gap / 2)
            rects["hi"][0] = min(self.width() - rects["hi"][1], rects["lo"][0] + rects["lo"][1] + 4)
        p.setFont(f)
        for which, x, col in (("lo", xl, _LOW), ("hi", xh, _HIGH)):
            lx, w, txt = rects[which]
            box = QRectF(lx, 2, w, fm.height())
            self._labels[which] = box
            p.setPen(col)
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, txt)
            pen = QPen(col, 2)
            p.setPen(pen)
            p.setBrush(QColor(theme.ELEV or "#FBFAF7"))
            p.drawRoundedRect(QRectF(x - 5, y - 5, 10, h + 10), 3, 3)
            if self.hasFocus() and which == self._active:
                p.setPen(QPen(QColor(theme.FOCUS or "#2B4C6F"), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(QRectF(x - 8, y - 8, 16, h + 16), 5, 5)
        p.setFont(_mono(8))
        p.setPen(QColor(theme.MUTE or "#5A574E"))
        base = self.height() - 4
        p.drawText(QPointF(a, base), "0")                   # 막대 양 끝에 맞춘다(왼쪽 숫자는 왼끝, 오른쪽은 오른끝)
        end = f"{self._levels - 1:,}"
        p.drawText(QPointF(b - QFontMetrics(p.font()).horizontalAdvance(end), base), end)
        p.end()

    # -- 조작 ------------------------------------------------------------
    def mousePressEvent(self, e):  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton or not self.isEnabled():
            return
        x = e.position().x()
        self._drag = self._active = self._nearest(x)
        self.moved.emit(self._drag, self.x_gl(x))
        self.update()

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._drag:
            self.moved.emit(self._drag, self.x_gl(e.position().x()))
        else:
            self.setCursor(Qt.CursorShape.SizeHorCursor)

    def mouseReleaseEvent(self, e):  # noqa: N802
        self._drag = None

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        self._drag = None
        for which, box in self._labels.items():
            if box.adjusted(-6, -2, 6, 6).contains(e.position()):
                self._active = which
                self.editRequested.emit(which, box)
                return

    def keyPressEvent(self, e):  # noqa: N802
        step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        k = e.key()
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            cur = self._lo if self._active == "lo" else self._hi
            self.moved.emit(self._active, cur + (step if k == Qt.Key.Key_Right else -step))
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._active = "hi" if self._active == "lo" else "lo"
            self.update()
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.editRequested.emit(self._active, self._labels.get(self._active, QRectF()))
        else:
            super().keyPressEvent(e)

    def focusInEvent(self, e):  # noqa: N802
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e):  # noqa: N802
        self.update()
        super().focusOutEvent(e)
