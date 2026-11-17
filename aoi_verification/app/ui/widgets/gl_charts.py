"""GL 뷰어의 그래프 — 히스토그램 하나가 임계값 조작축까지 겸한다(아래 띠의 손잡이 · 값 칩).

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
_X_PAD = 8          # 좌우 여백 — 손잡이(반폭 6)가 끝 값에서도 잘리지 않게
ALL_ROW = -1        # 줄 모드에서 '사진 전체' 줄의 번호(ROI 번호는 1 부터)

_NAME_H, _RATIO_H = 16, 18
_ROW_HEAD = _NAME_H + _RATIO_H          # 줄마다 이름 줄 + 비율 줄(비교 줄은 이름 줄이 하나 더)
_ROW_GAP = 8
_CMP_PAD = 6                             # 비교 줄 위 여백
_CMP_HEAD = _CMP_PAD + _NAME_H + 2 + _NAME_H + _RATIO_H     # 여백 + 줄 이름 + 구역 이름 + 비율
_BAND_Y, _BAND_H = 10, 8                # 바닥선 아래 밝기 띠(손잡이가 그 위에 앉는다)
_HANDLE_W, _HANDLE_H = 12, 24
_CHIP_H = 20
_CHIP_Y = _BAND_Y + _BAND_H // 2 + _HANDLE_H // 2 + 4      # 값 칩 · 눈금 글자 줄
_BOTTOM = _CHIP_Y + _CHIP_H + 2


def _mono(size: int = 9) -> QFont:
    return QFont(theme.FONT_MONO.split(",")[0].strip('" '), size)


def _px_font(px: int, *, mono: bool = False, bold: bool = False) -> QFont:
    f = _mono() if mono else QFont()
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def axis_ticks(a: float, b: float) -> list[int]:
    """가로축 눈금 — 처음 · 가운데 · 끝 셋뿐이다(8 bit 면 0 / 128 / 255)."""
    if b <= a:
        return [int(a)]
    return [int(a), int((a + b + 1) // 2), int(b)]


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
    """GL 분포 — 구역 색으로 칠한 계단 면 + 임계선 + 구역 비율, 그리고 아래 가로축이 **임계값 조작축**이다.

    축은 밝기 띠 위에 손잡이 둘(하한 파랑 · 상한 빨강 테두리)이 앉고 임계선이 거기까지 내려온다.
    값 칩(``≤ 70`` · ``≥ 200``)은 축 아래 한 번만 나오고 누르면 ``editRequested`` 로 직접 입력을 청한다.
    손잡이·그래프를 끌면 ``thresholdDragged(which, 값)``, 방향키 ←→ 는 마지막에 잡은 손잡이를 1(Shift 10) 옮긴다.
    ``set_rows`` 는 같은 자리에서 줄(전체 · ROI 별), ``set_compare`` 는 A · B 두 줄을 쌓는다 — 축은 하나를 같이 쓴다."""

    thresholdDragged = pyqtSignal(str, int)     # "lo" | "hi", GL
    editRequested = pyqtSignal(str, QRectF)     # 어느 쪽, 값 칩이 그려진 자리
    rowClicked = pyqtSignal(int)                # ALL_ROW 또는 ROI 번호
    hoverText = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._series: list[tuple[str, str, np.ndarray]] = []   # (이름, 색, 히스토그램)
        self._rows: list[tuple[int, str, str, np.ndarray]] = []   # (번호, 이름, 색, 히스토그램)
        self._sel_row: Optional[int] = None
        self._compare = False                   # 줄이 ROI 별이 아니라 A · B 비교다
        self._lo = 0
        self._hi = 1
        self._levels = 256
        self._range_data = False
        self._normalize = False
        self._drag: Optional[str] = None
        self._active = "lo"                     # 방향키가 옮길 손잡이
        self._hover_gl: Optional[int] = None    # 사진 위 마우스가 가리키는 GL — 얇은 세로선
        self._plot = QRectF()
        self._chips: dict[str, QRectF] = {}
        self._row_rects: list[QRectF] = []       # 줄 전체(이름·비율 포함)
        self._row_plots: list[QRectF] = []       # 줄의 그래프 칸
        self._domain = (0, 255)
        self.setMouseTracking(True)
        self.setMinimumHeight(190)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName(i18n.KO.GL_HIST)
        self.setToolTip(i18n.KO.GL_THR_EDIT_TIP)

    # -- 설정 ------------------------------------------------------------
    def set_series(self, series: Sequence[tuple[str, str, np.ndarray]], levels: int,
                   *, normalize: bool = False) -> None:
        """``series`` — [(이름, 색, 히스토그램)].  둘 이상이면 겹쳐 그리고 ``normalize`` 로 % 로 맞춘다."""
        self._series = [(n, c, np.asarray(h)) for n, c, h in series]
        self._rows = []
        self._compare = False
        self._levels = int(levels)
        self._normalize = normalize
        self._update_domain()
        self.update()

    def set_rows(self, rows: Sequence[tuple[int, str, str, np.ndarray]], levels: int,
                 selected: Optional[int]) -> None:
        """줄 쌓기 — [(번호, 이름, 색, 히스토그램)].  ``selected`` 는 밝게·크게 그린다."""
        self._rows = [(int(i), n, c, np.asarray(h)) for i, n, c, h in rows]
        self._sel_row = selected
        self._compare = False
        self._series = []
        self._levels = int(levels)
        self._update_domain()
        self.update()

    def set_compare(self, rows: Sequence[tuple[int, str, str, np.ndarray]], levels: int) -> None:
        """A · B 비교 — 줄마다 이름 견본 · 자기 구역 비율 · 자기 분포 선, 축과 임계선은 하나."""
        self._rows = [(int(i), n, c, np.asarray(h)) for i, n, c, h in rows]
        self._sel_row = None
        self._compare = True
        self._series = []
        self._levels = int(levels)
        self._update_domain()
        self.update()

    def stack_mode(self) -> bool:
        return bool(self._rows)

    def compare_mode(self) -> bool:
        return bool(self._rows) and self._compare

    def rows(self) -> list[tuple[int, str, str, np.ndarray]]:
        return list(self._rows)

    def selected_row(self) -> Optional[int]:
        return self._sel_row

    def row_rects(self) -> list[QRectF]:
        self._layout()
        return list(self._row_rects)

    def chip_rects(self) -> dict[str, QRectF]:
        """값 칩이 놓인 자리(위젯 좌표) — 아무것도 안 그릴 때는 비어 있다."""
        self._layout()
        return dict(self._chips)

    def active(self) -> str:
        return self._active

    def set_thresholds(self, lo: int, hi: int) -> None:
        self._lo, self._hi = int(lo), int(hi)
        self.update()

    def set_hover_gl(self, gl: Optional[int]) -> None:
        """사진 위 마우스 자리의 GL — 그 값에 얇은 세로선을 긋는다(None 이면 지운다)."""
        gl = None if gl is None else int(gl)
        if gl != self._hover_gl:
            self._hover_gl = gl
            self.update()

    def hover_gl(self) -> Optional[int]:
        return self._hover_gl

    def set_range_data(self, on: bool) -> None:
        self._range_data = bool(on)
        self._update_domain()
        self.update()

    def domain(self) -> tuple[int, int]:
        return self._domain

    def _empty(self) -> bool:
        return not self._rows and not self._series      # 사진 전 — 값이 없다

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

    def _row_head(self) -> float:
        return _CMP_HEAD if self._compare else _ROW_HEAD

    def _layout(self) -> None:
        w, h = float(self.width()), float(self.height())
        left, width = float(_X_PAD), max(10.0, w - 2 * _X_PAD)
        bottom = h - _BOTTOM
        self._row_rects, self._row_plots = [], []
        if self._rows:
            n = len(self._rows)
            head = self._row_head()
            top = 2.0 if self._compare else _NAME_H + 4.0         # ROI 줄은 맨 위에 구역 이름 한 번
            sel = next((i for i, r in enumerate(self._rows) if r[0] == self._sel_row), None)
            avail = bottom - top - n * (head + _ROW_GAP) + _ROW_GAP
            hs = stack_heights(n, sel, avail, 14.0)
            y = top
            for ph in hs:
                self._row_rects.append(QRectF(0, y, w, head + ph))
                self._row_plots.append(QRectF(left, y + head, width, ph))
                y += head + ph + _ROW_GAP
            self._plot = QRectF(left, self._row_plots[0].top(), width,
                                self._row_plots[-1].bottom() - self._row_plots[0].top())
        else:
            top = _NAME_H + (0 if len(self._series) > 1 else _RATIO_H) + 4
            self._plot = QRectF(left, top, width, max(10.0, bottom - top))
            self._row_plots = [self._plot]
        self._layout_chips()

    def _chip_font(self) -> QFont:
        return _px_font(12, mono=True, bold=True)

    def _layout_chips(self) -> None:
        """값 칩 자리 — 손잡이 아래에 가운데 맞추고, 둘이 겹치면 벌린다."""
        self._chips = {}
        if self._empty():
            return
        fm = QFontMetrics(self._chip_font())
        items = (("lo", f"≤ {self._lo:,}", self._line_x(self._lo)),
                 ("hi", f"≥ {self._hi:,}", self._line_x(self._hi)))
        ws = [fm.horizontalAdvance(t) + 14 for _w, t, _x in items]
        xs = spread_labels([x for _w, _t, x in items], ws, 0.0, float(self.width()), 4)
        y = self._plot.bottom() + _CHIP_Y
        for (which, _t, _x), x0, w in zip(items, xs, ws):
            self._chips[which] = QRectF(x0, y, w, _CHIP_H)

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

        empty = self._empty()      # 사진 전이면 값이 없다 — 바닥선 · 띠 · 눈금만

        # 1) 구역 이름 · 비율의 자리(가운데)
        zones = [(pl.left(), xl), (xl, xh), (xh, pl.right())]
        names = (i18n.KO.GL_ZONE_LOW, i18n.KO.GL_ZONE_MID, i18n.KO.GL_ZONE_HIGH)
        f_name, f_ratio = _px_font(12), _px_font(13, mono=True, bold=True)
        fm_n, fm_r = QFontMetrics(f_name), QFontMetrics(f_ratio)

        def put_ratios(y: float, h: np.ndarray, strong: bool, with_names: bool, even: bool = False) -> None:
            r = analysis.zone_ratios(h, self._lo, self._hi) if h is not None else None
            texts = [pct_text(v) for v in r] if r else ["" for _ in zones]
            ws = [max(fm_n.horizontalAdvance(n) if with_names else 0,
                      fm_r.horizontalAdvance(t)) + 2 for n, t in zip(names, texts)]
            if even:                      # 세 칸 균등 — 그래프 폭을 3 등분해 가운데 맞춤(임계값과 무관)
                third = pl.width() / 3.0
                ws = [third - 4.0] * 3
                xs = [pl.left() + third * i + 2.0 for i in range(3)]
            else:
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
                if self._compare:
                    self._row_line(p, h, rp, QColor(color))
                else:
                    self._bars(p, h, rp)
                p.setPen(hair)
                p.drawLine(QPointF(rp.left(), rp.bottom() + 0.5), QPointF(rp.right(), rp.bottom() + 0.5))
                if self._compare:                  # 짧은 선 견본(분포 선과 같은 색)
                    ly = rr.top() + _CMP_PAD
                    p.setPen(QPen(QColor(color), 2))
                    p.drawLine(QPointF(pl.left(), ly + _NAME_H / 2.0), QPointF(pl.left() + 14, ly + _NAME_H / 2.0))
                    lab_x, lab_w = pl.left() + 20, pl.width() - 20
                else:
                    sw = QRectF(pl.left(), rr.top() + 3, 10, 10)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(color))
                    p.drawRoundedRect(sw, 2, 2)
                    lab_x, lab_w = pl.left() + 16, pl.width() - 16
                p.setFont(f_name)
                p.setPen(ink if (sel or self._compare) else mute)
                label = fm_n.elidedText(name, Qt.TextElideMode.ElideRight, int(lab_w))
                p.drawText(QRectF(lab_x, rr.top() + (_CMP_PAD if self._compare else 0), lab_w, _NAME_H),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
                if self._compare:
                    put_ratios(rr.top() + _CMP_PAD + _NAME_H + 2, h, True, True, even=True)
                else:
                    put_ratios(rr.top() + _NAME_H, h, sel, False)
            if not self._compare:
                put_ratios(0, None, False, True)                # 구역 이름은 맨 위 한 번
            line_top, base = (2.0 if self._compare else float(_NAME_H)), ymax
        else:
            one = self._series[0][2] if len(self._series) == 1 else None
            if not empty:
                if multi:                           # 둘 이상(ROI 겹침)은 비율이 없어 구역 이름 대신 범례
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
            line_top, base = pl.top() - 2, pl.bottom()

        # 3) 마우스가 사진 위에서 가리키는 GL — 얇은 중립 세로선
        if not empty and self._hover_gl is not None and a <= self._hover_gl <= b:
            hx = self._gl_x(self._hover_gl)
            hc = QColor(ink)
            hc.setAlpha(150)
            p.setPen(QPen(hc, 1))
            p.drawLine(QPointF(hx, line_top), QPointF(hx, base))

        # 4) 밝기 띠 + 손잡이 + 임계선(손잡이까지)
        top_v = float(max(1, self._levels - 1))
        g = QLinearGradient(pl.left(), 0, pl.right(), 0)
        for pos, gl in ((0.0, a), (1.0, b)):
            v = int(round(gl / top_v * 255))
            g.setColorAt(pos, QColor(v, v, v))
        band = QRectF(pl.left(), base + _BAND_Y, pl.width(), _BAND_H)
        p.fillRect(band, g)
        p.setPen(hair)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(band.adjusted(0.5, 0.5, -0.5, -0.5))
        handle_top = band.center().y() - _HANDLE_H / 2.0
        if not empty:
            spans = [(line_top, handle_top)]
            if self._compare:           # 줄 머리(이름 · 비율 글자)는 비워 둔다 — 임계선은 그래프 칸과 줄 사이만 지나간다
                spans, y0 = [], self._row_plots[0].top()
                for rr, rp in zip(self._row_rects, self._row_plots):
                    if rr.top() > y0:
                        spans.append((y0, rr.top()))
                    y0 = rp.top()
                spans.append((y0, handle_top))
            for which, col, x in (("lo", _LOW, xl), ("hi", _HIGH, xh)):
                p.setPen(QPen(col, 1.5))
                for y_a, y_b in spans:
                    p.drawLine(QPointF(x, y_a), QPointF(x, y_b))
            for which, col, x in (("lo", _LOW, xl), ("hi", _HIGH, xh)):
                hr = QRectF(x - _HANDLE_W / 2.0, handle_top, _HANDLE_W, _HANDLE_H)
                p.setPen(QPen(col, 2))
                p.setBrush(QColor("#FFFFFF"))
                p.drawRoundedRect(hr, 3, 3)
                if self.hasFocus() and which == self._active:
                    p.setPen(QPen(QColor(theme.FOCUS or "#2B4C6F"), 1))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRoundedRect(hr.adjusted(-3, -3, 3, 3), 5, 5)

        # 5) 값 칩(하한 · 상한 한 번씩) + 눈금 0 / 가운데 / 끝 — 칩과 겹치는 눈금 글자는 뺀다
        f_chip = self._chip_font()
        p.setFont(f_chip)
        for which, col, txt in (("lo", _LOW, f"≤ {self._lo:,}"), ("hi", _HIGH, f"≥ {self._hi:,}")):
            box = self._chips.get(which)
            if box is None:
                continue
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(box, 4, 4)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, txt)
        p.setFont(_px_font(11, mono=True))
        p.setPen(mute)
        fm = QFontMetrics(p.font())
        ty = base + _CHIP_Y
        for t in axis_ticks(a, b):
            txt = f"{t:,}"
            tw = fm.horizontalAdvance(txt)
            x0 = max(2.0, min(self._gl_x(t) - tw / 2, self.width() - tw - 2))
            r = QRectF(x0, ty, tw, _CHIP_H)
            if any(r.adjusted(-4, 0, 4, 0).intersects(c) for c in self._chips.values()):
                continue
            p.drawText(r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, txt)
        p.end()

    def _legend(self, p: QPainter, font: QFont, ink: QColor, pl: QRectF) -> None:
        """ROI 겹침의 분포 선 색 견본(■) + 이름 — 제목 줄에 왼쪽부터, 자리가 모자라면 거기서 멈춘다."""
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

    def _curve(self, h: np.ndarray, pl: QRectF) -> tuple[list[float], np.ndarray]:
        """분포 선의 x 들과 칸별 개수 — GL 칸이 화면 칸보다 적으면 GL 한 칸이 한 점이다."""
        a, b = self._domain
        if (b - a + 1) <= max(1, int(pl.width())):
            return [self._gl_x(g) for g in range(a, b + 1)], h[a:b + 1].astype(np.float64)
        cnt, _e = self._binned_counts(h, pl)
        return [pl.left() + i + 0.5 for i in range(len(cnt))], cnt

    def _row_line(self, p: QPainter, h: np.ndarray, pl: QRectF, color: QColor) -> None:
        """비교 줄 하나 — 자기 최댓값에 맞춘 2px 분포 선과 그 아래 옅은 면."""
        xs, cnt = self._curve(h, pl)
        peak = float(cnt.max()) if cnt.size else 0.0
        if peak <= 0 or len(xs) < 2:
            return
        pts = [QPointF(x, pl.bottom() - float(v) / peak * pl.height()) for x, v in zip(xs, cnt)]
        fill = QColor(color)
        fill.setAlpha(40)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawPolygon(QPolygonF([QPointF(xs[0], pl.bottom())] + pts + [QPointF(xs[-1], pl.bottom())]))
        p.setPen(QPen(color, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(len(pts) - 1):
            p.drawLine(pts[i], pts[i + 1])

    def _lines(self, p: QPainter, pl: QRectF) -> None:
        """ROI 겹쳐 보기 — 분포마다 2px 선(합이 1 이 되게 %로 맞춘다)."""
        series = []
        for name, color, h in self._series:
            xs, cnt = self._curve(h, pl)
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
        if e.button() != Qt.MouseButton.LeftButton or self._empty():
            return
        self._layout()
        pos = e.position()
        for which, box in self._chips.items():      # 값 칩을 누르면 그 자리에서 직접 입력
            if box.adjusted(-2, -2, 2, 2).contains(pos):
                self._active = which
                self.update()
                self.editRequested.emit(which, box)
                return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        x = pos.x()
        which = self._nearest(x)
        if self.stack_mode() and not self._compare and which is None:   # 줄을 누르면 그 ROI 를 고른다
            rid = self._row_at(pos.y())
            if rid is not None:
                self.rowClicked.emit(rid)
            return
        if which is None:                 # 그래프를 클릭하면 가까운 쪽 임계값이 거기로 온다
            which = "lo" if abs(x - self._gl_x(self._lo)) <= abs(x - self._gl_x(self._hi)) else "hi"
        self._drag = self._active = which
        self.update()
        self.thresholdDragged.emit(which, self._x_gl(x))

    def mouseMoveEvent(self, e):  # noqa: N802
        self._layout()
        x = e.position().x()
        if self._drag:
            self.thresholdDragged.emit(self._drag, self._x_gl(x))
            return
        if self._empty():
            return
        if any(b.contains(e.position()) for b in self._chips.values()):
            self.setCursor(Qt.CursorShape.IBeamCursor)
        elif self._nearest(x):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif self.stack_mode() and not self._compare and self._row_at(e.position().y()) is not None:
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

    # -- 키보드 ----------------------------------------------------------
    def keyPressEvent(self, e):  # noqa: N802
        if self._empty():
            super().keyPressEvent(e)
            return
        step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        k = e.key()
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            cur = self._lo if self._active == "lo" else self._hi
            self.thresholdDragged.emit(self._active, cur + (step if k == Qt.Key.Key_Right else -step))
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._active = "hi" if self._active == "lo" else "lo"
            self.update()
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._layout()
            self.editRequested.emit(self._active, self._chips.get(self._active, QRectF()))
        else:
            super().keyPressEvent(e)

    def focusInEvent(self, e):  # noqa: N802
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e):  # noqa: N802
        self.update()
        super().focusOutEvent(e)
