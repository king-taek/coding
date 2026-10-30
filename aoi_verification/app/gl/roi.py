"""ROI 모델 — 전체 폭·높이 선(수평/수직)과 도형(사각형·정사각형·원).

좌표는 모두 **이미지 픽셀**이다.  화면 배율·이동과 무관하게 저장하므로 확대·축소해도 선과
도형이 자동으로 맞는다.  Qt 를 쓰지 않는다(헤드리스 테스트).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

HLINE, VLINE = "hline", "vline"
RECT, SQUARE, CIRCLE = "rect", "square", "circle"
LINE_KINDS = (HLINE, VLINE)
SHAPE_KINDS = (RECT, SQUARE, CIRCLE)

MIN_SIZE = 2          # 도형 한 변(지름)의 하한(px) — 더 작은 드래그는 만들지 않는다
UNDO_LIMIT = 50

# ROI 색 — 어두운 바탕 위에서 파랑/빨강(판정색)과 겹치지 않게 고른다.
PALETTE = ("#7BE08A", "#C79BFF", "#FFFFFF", "#5FE3E3", "#C8E85A", "#F0A8D8")


@dataclass
class Roi:
    id: int
    kind: str
    name: str
    color: str
    pos: int = 0          # 선: 행(수평) 또는 열(수직)
    x: int = 0            # 도형: 왼쪽 위와 크기
    y: int = 0
    w: int = 0
    h: int = 0

    @property
    def is_line(self) -> bool:
        return self.kind in LINE_KINDS

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2.0, self.y + self.h / 2.0


def clamp_shape(roi: Roi, img_w: int, img_h: int) -> Roi:
    """도형을 이미지 안에 가둔다(크기가 이미지보다 크면 줄인다)."""
    w = max(MIN_SIZE, min(roi.w, img_w))
    h = max(MIN_SIZE, min(roi.h, img_h))
    if roi.kind in (SQUARE, CIRCLE):
        s = min(w, h)
        w = h = s
    x = max(0, min(roi.x, img_w - w))
    y = max(0, min(roi.y, img_h - h))
    return replace(roi, x=x, y=y, w=w, h=h)


def move(roi: Roi, dx: int, dy: int, img_w: int, img_h: int) -> Roi:
    """선은 자기 축 방향 성분만 움직인다(수평선은 dy, 수직선은 dx)."""
    if roi.kind == HLINE:
        return replace(roi, pos=max(0, min(roi.pos + dy, img_h - 1)))
    if roi.kind == VLINE:
        return replace(roi, pos=max(0, min(roi.pos + dx, img_w - 1)))
    return clamp_shape(replace(roi, x=roi.x + dx, y=roi.y + dy), img_w, img_h)


def resize(roi: Roi, dw: int, dh: int, img_w: int, img_h: int) -> Roi:
    """중심을 지킨 채 크기를 바꾼다.  정사각형·원은 ``dw`` 만 쓰고(지름 변화) 정비율을 지킨다.

    원은 반지름을 1 px 키우려면 지름이 2 늘어야 한다 — 호출부가 ``dw=2`` 를 넘긴다."""
    if roi.is_line:
        return roi
    if roi.kind in (SQUARE, CIRCLE):
        dh = dw
    cx, cy = roi.center
    w, h = max(MIN_SIZE, roi.w + dw), max(MIN_SIZE, roi.h + dh)
    if roi.kind in (SQUARE, CIRCLE):
        s = min(w, h, img_w, img_h)
        w = h = s
    new = replace(roi, w=w, h=h, x=int(round(cx - w / 2.0)), y=int(round(cy - h / 2.0)))
    return clamp_shape(new, img_w, img_h)


def from_drag(kind: str, p0: tuple[int, int], p1: tuple[int, int],
              img_w: int, img_h: int) -> Optional[Roi]:
    """드래그 시작·끝 점(이미지 픽셀) → 도형.  너무 작으면 ``None``.

    정사각형·원은 짧은 변에 맞춰 정비율로, 드래그 방향(어느 사분면이든)을 따른다."""
    x0, y0 = p0
    x1, y1 = p1
    w, h = abs(x1 - x0), abs(y1 - y0)
    if kind in (SQUARE, CIRCLE):
        s = max(w, h)
        w = h = s
        x = x0 if x1 >= x0 else x0 - s
        y = y0 if y1 >= y0 else y0 - s
    else:
        x, y = min(x0, x1), min(y0, y1)
    if w < MIN_SIZE or h < MIN_SIZE:
        return None
    r = Roi(0, kind, "", "", x=x, y=y, w=w, h=h)
    return clamp_shape(r, img_w, img_h)


class RoiSet:
    """ROI 목록 + 선택 + 되돌리기(최대 50단계).

    ``snap()`` 은 **바꾸기 직전**에 부른다.  선 보기/숨기기·선택은 되돌리기 대상이 아니다
    (보기 설정일 뿐 분석 값을 바꾸지 않는다)."""

    def __init__(self) -> None:
        self._rois: list[Roi] = []
        self._next = 1
        self._undo: list[list[Roi]] = []
        self.selected: Optional[int] = None
        self.show_h = True
        self.show_v = True

    # -- 읽기 ------------------------------------------------------------
    @property
    def rois(self) -> list[Roi]:
        return list(self._rois)

    def get(self, rid: Optional[int]) -> Optional[Roi]:
        for r in self._rois:
            if r.id == rid:
                return r
        return None

    def active(self) -> Optional[Roi]:
        return self.get(self.selected)

    def visible(self, roi: Roi) -> bool:
        return not ((roi.kind == HLINE and not self.show_h)
                    or (roi.kind == VLINE and not self.show_v))

    def can_undo(self) -> bool:
        return bool(self._undo)

    # -- 쓰기 ------------------------------------------------------------
    def snap(self) -> None:
        self._undo.append([replace(r) for r in self._rois])
        del self._undo[:-UNDO_LIMIT]

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._rois = self._undo.pop()
        if self.get(self.selected) is None:
            self.selected = None
        return True

    def _name(self, kind: str) -> str:
        label = {HLINE: "수평선", VLINE: "수직선", RECT: "사각형",
                 SQUARE: "정사각형", CIRCLE: "원"}[kind]
        n = 1 + sum(1 for r in self._rois if r.kind == kind)
        used = {r.name for r in self._rois}
        while f"{label} {n}" in used:
            n += 1
        return f"{label} {n}"

    def _color(self) -> str:
        used = [r.color for r in self._rois]
        for c in PALETTE:
            if c not in used:
                return c
        return PALETTE[len(self._rois) % len(PALETTE)]

    def add(self, roi: Roi) -> Roi:
        """새 ROI 를 넣고 선택한다.  id·이름·색은 여기서 정한다."""
        self.snap()
        roi = replace(roi, id=self._next, name=self._name(roi.kind), color=self._color())
        self._next += 1
        self._rois.append(roi)
        self.selected = roi.id
        return roi

    def add_line(self, kind: str, pos: int) -> Roi:
        return self.add(Roi(0, kind, "", "", pos=pos))

    def replace_roi(self, roi: Roi) -> None:
        """같은 id 의 ROI 를 바꾼다(되돌리기 기록은 호출부가 ``snap`` 으로 남긴다)."""
        self._rois = [roi if r.id == roi.id else r for r in self._rois]

    def delete(self, rid: Optional[int] = None) -> bool:
        rid = self.selected if rid is None else rid
        if self.get(rid) is None:
            return False
        self.snap()
        self._rois = [r for r in self._rois if r.id != rid]
        if self.selected == rid:
            self.selected = None
        return True

    def clear(self) -> bool:
        if not self._rois:
            return False
        self.snap()
        self._rois = []
        self.selected = None
        return True

    def reset(self) -> None:
        """사진을 바꿀 때 — 목록·기록을 모두 비운다(보기 설정은 둔다)."""
        self._rois = []
        self._undo = []
        self.selected = None


HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")


def resize_handle(roi: Roi, handle: str, px: int, py: int,
                  img_w: int, img_h: int) -> Roi:
    """손잡이를 ``(px, py)``(이미지 픽셀)로 끌었을 때의 도형.

    반대쪽 변·모서리는 고정이다.  정사각형·원은 정비율을 지킨다 — 끈 방향 중 더 긴 쪽에
    맞추고, 손잡이가 변 가운데(n/s/e/w)면 다른 축은 가운데에 둔다."""
    if roi.is_line:
        return roi
    x0, y0, x1, y1 = roi.x, roi.y, roi.x + roi.w, roi.y + roi.h
    px = max(0, min(int(px), img_w))
    py = max(0, min(int(py), img_h))
    if "w" in handle:
        x0 = px
    if "e" in handle:
        x1 = px
    if "n" in handle:
        y0 = py
    if "s" in handle:
        y1 = py
    # 반대편을 넘겨 끌면 뒤집힌다(왼쪽 위 ↔ 오른쪽 아래).
    nx0, nx1 = min(x0, x1), max(x0, x1)
    ny0, ny1 = min(y0, y1), max(y0, y1)
    w, h = max(MIN_SIZE, nx1 - nx0), max(MIN_SIZE, ny1 - ny0)
    if roi.kind in (SQUARE, CIRCLE):
        s = max(w, h)
        w = h = s
        # 고정 쪽을 기준으로 s 만큼 뻗는다.
        if "w" in handle:
            ax = x1 if x1 >= x0 else x0
            nx0 = ax - s
        elif "e" in handle:
            nx0 = (x0 if x1 >= x0 else x1)
        else:
            nx0 = int(round((roi.x + roi.w / 2.0) - s / 2.0))
        if "n" in handle:
            ay = y1 if y1 >= y0 else y0
            ny0 = ay - s
        elif "s" in handle:
            ny0 = (y0 if y1 >= y0 else y1)
        else:
            ny0 = int(round((roi.y + roi.h / 2.0) - s / 2.0))
    return clamp_shape(replace(roi, x=nx0, y=ny0, w=w, h=h), img_w, img_h)
