"""좌표 보정 화면 — KLA ↔ Camtek 매칭 직전, '같은 결함' 사진 쌍을 골라 어긋남을 뺀다.

왼쪽: 기준 사진(짝 후보가 있는 것만, 후보가 적은 순).  오른쪽: 고른 사진을 크게 +
가까운 die 의 검증 사진(그럴듯한 순).  같은 결함을 누르면 쌍이 기록되고, 바닥에
쌍별 어긋남과 보정값(중앙값)·남은 오차가 보인다.  계산은 전부
:mod:`~aoi_verification.app.coords.calibration` 이 한다 — 이 파일은 화면만.

결과는 :attr:`offset` — [보정 적용] 이면 ``(dx, dy)``(Camtek − KLA, µm), [보정
건너뛰기]·닫기면 ``None``.  보정은 매칭에만 쓰인다(엑셀·화면 좌표 불변, 사용자 지시).
"""

from __future__ import annotations

import math

from typing import Optional

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ... import i18n
from ...coords import calibration as cal
from ...utils import image_io
from .. import theme
from .neon_button import NeonButton
from .option_group import columns_for_width

_SRC_TILE_PX = 120
_CAND_TILE_PX = 200
_PREVIEW_PX = 260
_TILE_CHROME_W = 14
_CAP_H = 20
_GRID_SPACING = 8
# 왼쪽 목록 상한 — 후보가 적은(=짝이 분명한) 사진부터 이만큼만 보인다.  보정은 몇 쌍이면
# 되고, 수천 장을 타일로 만들면 여는 데만 수 초가 걸린다.
MAX_SRC_TILES = 120


def _scroll_area(parent) -> QScrollArea:
    sc = QScrollArea(parent)
    sc.setWidgetResizable(True)
    sc.setFrameShape(QScrollArea.Shape.NoFrame)
    sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sc.setStyleSheet(
        "QScrollArea { background: transparent; border: none; }"
        "QScrollArea > QWidget#qt_scrollarea_viewport { background: transparent; }")
    return sc


class _PhotoTile(QFrame):
    """타일 전체가 클릭 영역인 사진 카드(UI 관습 — 작은 체크박스 금지)."""

    clicked = pyqtSignal(object)          # key

    def __init__(self, key, path, px: int, *, kind: str = "thumb",
                 parent=None) -> None:
        super().__init__(parent)
        self.key = key
        self._selected = False
        self.setObjectName("calTile")
        self.setProperty("role", "card-soft")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(px + _TILE_CHROME_W, px + 2 * _CAP_H + 20)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(2)
        img = QLabel(self)
        img.setFixedSize(px, px)
        img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img.setPixmap(image_io.load_thumb_qpixmap(path, px, kind=kind))
        lay.addWidget(img, alignment=Qt.AlignmentFlag.AlignCenter)
        name = QLabel(self)
        name.setProperty("role", "muted")
        name.setFixedHeight(_CAP_H)
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name.setText(QFontMetrics(name.font()).elidedText(
            path.name, Qt.TextElideMode.ElideMiddle, px - 4))
        name.setToolTip(path.name)
        lay.addWidget(name)
        self._badge = QLabel("", self)
        self._badge.setFixedHeight(_CAP_H)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._badge)

    def set_badge(self, text: str) -> None:
        self._badge.setText(text)

    def set_selected(self, on: bool) -> None:
        if on == self._selected:
            return
        self._selected = bool(on)
        self.setStyleSheet(
            f"#calTile {{ border: 2px solid {theme.ACCENT}; border-radius: 8px;"
            f" background: {theme.ACCENT_TINT_SOFT}; }}" if on else "")

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)


class CoordCalibrationDialog(QDialog):
    """``entries`` = :func:`coords.calibration.build_entries` 결과(키는 ``ImageItem``).

    ``kla_side`` 는 ``"ref"``/``"val"`` — 어느 쪽이 KLA 인지(부호 규약용).
    ``hint`` 는 :func:`coords.calibration.guess_offset` — 쌍을 고르기 전 후보 정렬용.
    ``auto`` 는 :func:`coords.calibration.auto_offset` — 믿을 만하면(:func:`is_confident`)
    쌍을 고르지 않아도 그 값으로 '보정 적용' 할 수 있다.  고른 쌍이 있으면 쌍이 우선이다."""

    def __init__(self, entries, *, kla_side: str, hint=None, auto=None,
                 tolerance: float = 200.0, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(i18n.KO.COORD_CAL_TITLE)
        self._src_is_kla = kla_side == "ref"
        self._hint0 = hint
        self._auto = auto if cal.is_confident(auto) else None
        self._auto_weak = auto if self._auto is None else None
        self._tol = float(tolerance) if tolerance and tolerance > 0 else 200.0
        self._omitted = max(0, len(entries) - MAX_SRC_TILES)
        self._entries = list(entries)[:MAX_SRC_TILES]
        self._by_key = {e[0]: e for e in self._entries}
        # 쌍 — 기준 키 → (검증 키, (dx, dy)).  고른 순서를 지킨다(번호 표기).
        self._pairs: dict = {}
        self._current = None
        self._src_tiles: dict = {}
        self._cand_tiles: list[_PhotoTile] = []
        self.offset: Optional[cal.Offset] = None
        src_dev, dst_dev = ("KLA", "Camtek") if self._src_is_kla else ("Camtek", "KLA")
        self._src_dev, self._dst_dev = src_dev, dst_dev
        self._build()
        self._refresh_pairs()
        if self._entries:
            QTimer.singleShot(0, self._select_first)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        m = theme.PROFILE.page_margin // 2
        root.setContentsMargins(m, m, m, m)
        root.setSpacing(10)

        head = QLabel(i18n.KO.COORD_CAL_TITLE, self)
        head.setProperty("role", "title")
        root.addWidget(head)
        hint = QLabel(i18n.KO.COORD_CAL_HINT_FMT.format(
            src=self._src_dev, dst=self._dst_dev), self)
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(16)

        # ── 왼쪽: 기준 사진 목록(2열 고정, 세로 스크롤).
        left = QVBoxLayout()
        left.setSpacing(6)
        side_fmt = i18n.KO.COORD_CAL_SIDE_REF_FMT
        src_head = QLabel(i18n.KO.COORD_CAL_SRC_HEAD_FMT.format(
            side=side_fmt.format(dev=self._src_dev), n=len(self._entries)), self)
        src_head.setProperty("role", "paneTitle")
        left.addWidget(src_head)
        if self._omitted:
            more = QLabel(i18n.KO.COORD_CAL_SRC_MORE_FMT.format(n=self._omitted), self)
            more.setProperty("role", "muted")
            more.setWordWrap(True)
            left.addWidget(more)
        self._src_scroll = _scroll_area(self)
        host = QWidget(self._src_scroll)
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(_GRID_SPACING)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        for i, (item, _c, _found) in enumerate(self._entries):
            t = _PhotoTile(item, item.path, _SRC_TILE_PX, parent=host)
            t.clicked.connect(self._on_src_clicked)
            grid.addWidget(t, i // 2, i % 2)
            self._src_tiles[item] = t
        self._src_scroll.setWidget(host)
        left_w = 2 * (_SRC_TILE_PX + _TILE_CHROME_W) + _GRID_SPACING + 24
        self._src_scroll.setFixedWidth(left_w)
        left.addWidget(self._src_scroll, stretch=1)
        body.addLayout(left)

        # ── 오른쪽: 고른 사진 크게 + 후보 그리드.
        right = QVBoxLayout()
        right.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(12)
        self._preview = QLabel(self)
        self._preview.setFixedSize(_PREVIEW_PX, _PREVIEW_PX)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setProperty("role", "imagePlate")
        top.addWidget(self._preview)
        info = QVBoxLayout()
        self._preview_name = QLabel(i18n.KO.COORD_CAL_PICK_FIRST, self)
        self._preview_name.setProperty("role", "paneTitle")
        self._preview_name.setWordWrap(True)
        info.addWidget(self._preview_name)
        self._cand_head = QLabel("", self)
        self._cand_head.setProperty("role", "muted")
        self._cand_head.setWordWrap(True)
        info.addWidget(self._cand_head)
        info.addStretch(1)
        top.addLayout(info, stretch=1)
        right.addLayout(top)

        self._cand_scroll = _scroll_area(self)
        self._cand_host = QWidget(self._cand_scroll)
        self._cand_grid = QGridLayout(self._cand_host)
        self._cand_grid.setContentsMargins(0, 0, 0, 0)
        self._cand_grid.setSpacing(_GRID_SPACING)
        self._cand_grid.setAlignment(Qt.AlignmentFlag.AlignTop
                                     | Qt.AlignmentFlag.AlignLeft)
        self._cand_scroll.setWidget(self._cand_host)
        right.addWidget(self._cand_scroll, stretch=1)
        body.addLayout(right, stretch=1)
        root.addLayout(body, stretch=1)

        # ── 바닥: 고른 쌍 + 보정값 + 버튼.
        pairs_box = QFrame(self)
        pairs_box.setProperty("role", "card-soft")
        pl = QVBoxLayout(pairs_box)
        pl.setContentsMargins(12, 10, 12, 10)
        pl.setSpacing(4)
        self._pairs_host = QVBoxLayout()
        self._pairs_host.setSpacing(2)
        pl.addLayout(self._pairs_host)
        self._result = QLabel("", self)
        self._result.setProperty("role", "paneTitle")
        pl.addWidget(self._result)
        self._warn = QLabel("", self)
        self._warn.setProperty("role", "warn")
        self._warn.setWordWrap(True)
        pl.addWidget(self._warn)
        root.addWidget(pairs_box)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addStretch(1)
        skip = NeonButton(i18n.KO.COORD_CAL_SKIP, role="ghost")
        skip.setMinimumHeight(theme.PROFILE.control_h_lg)
        skip.clicked.connect(self.reject)
        skip.setAutoDefault(False)
        self._apply = NeonButton(i18n.KO.COORD_CAL_APPLY, role="primary")
        self._apply.setMinimumHeight(theme.PROFILE.control_h_lg)
        self._apply.clicked.connect(self._on_apply)
        self._apply.setAutoDefault(False)
        bar.addWidget(skip)
        bar.addWidget(self._apply)
        root.addLayout(bar)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(1200, 860)

    # ------------------------------------------------------------------
    def current_offset(self) -> Optional[cal.Offset]:
        if not self._pairs:
            return self._auto.offset if self._auto is not None else None
        return cal.estimate_offset([d for _v, d in self._pairs.values()])

    def _hint(self):
        return self.current_offset() or self._hint0

    def _select_first(self) -> None:
        if self._entries:
            self._on_src_clicked(self._entries[0][0])

    def _on_src_clicked(self, key) -> None:
        if key not in self._by_key:
            return
        if self._current in self._src_tiles:
            self._src_tiles[self._current].set_selected(False)
        self._current = key
        self._src_tiles[key].set_selected(True)
        self._preview.setPixmap(
            image_io.load_thumb_qpixmap(key.path, _PREVIEW_PX, kind="mid"))
        self._preview_name.setText(key.path.name)
        self._cand_head.setText(i18n.KO.COORD_CAL_CANDS_HEAD_FMT.format(
            dst=self._dst_dev))
        self._fill_candidates()

    def _cols(self) -> int:
        w = self._cand_scroll.viewport().width()
        return max(1, min(6, columns_for_width(
            w, _CAND_TILE_PX + _TILE_CHROME_W, _GRID_SPACING)))

    def _fill_candidates(self) -> None:
        for t in self._cand_tiles:
            # ★ hide 먼저 — deleteLater 는 중첩 루프(시트)가 돌 때까지 미뤄져 옛 타일이
            #   새 타일 밑에 겹쳐 그려진다(캡처에서 글자가 겹쳤다).
            t.hide()
            self._cand_grid.removeWidget(t)
            t.deleteLater()
        self._cand_tiles = []
        if self._current is None:
            return
        _item, coord, found = self._by_key[self._current]
        ordered = cal.order_candidates(coord, found, src_is_kla=self._src_is_kla,
                                       hint=self._hint())
        chosen = self._pairs.get(self._current, (None, None))[0]
        cols = self._cols()
        for i, (vitem, _vc, dist) in enumerate(ordered):
            t = _PhotoTile(vitem, vitem.path, _CAND_TILE_PX, kind="mid",
                           parent=self._cand_host)
            t.set_badge(i18n.KO.COORD_CAL_DIST_FMT.format(d=dist))
            t.set_selected(vitem == chosen)
            t.clicked.connect(self._on_cand_clicked)
            self._cand_grid.addWidget(t, i // cols, i % cols)
            self._cand_tiles.append(t)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if self._cand_tiles:
            cols = self._cols()
            for i, t in enumerate(self._cand_tiles):
                self._cand_grid.addWidget(t, i // cols, i % cols)

    def _on_cand_clicked(self, vkey) -> None:
        if self._current is None:
            return
        _item, coord, found = self._by_key[self._current]
        vcoord = next((c for k, c, _d in found if k == vkey), None)
        if vcoord is None:
            return
        if self._pairs.get(self._current, (None,))[0] == vkey:
            del self._pairs[self._current]         # 같은 사진을 다시 누르면 취소
        else:
            if self._src_is_kla:
                d = cal.pair_offset(coord, vcoord)
            else:
                d = cal.pair_offset(vcoord, coord)
            self._pairs.pop(self._current, None)   # 순서를 맨 뒤로(다시 고른 쌍)
            self._pairs[self._current] = (vkey, d)
        for t in self._cand_tiles:
            t.set_selected(self._pairs.get(self._current, (None,))[0] == t.key)
        self._refresh_pairs()

    def _remove_pair(self, key) -> None:
        self._pairs.pop(key, None)
        self._refresh_pairs()
        if key == self._current:
            for t in self._cand_tiles:
                t.set_selected(False)

    def _refresh_pairs(self) -> None:
        while self._pairs_host.count():
            it = self._pairs_host.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()                    # 겹쳐 그려지지 않게(_fill_candidates 참고)
                w.deleteLater()
            elif it.layout() is not None:
                lay = it.layout()
                while lay.count():
                    sub = lay.takeAt(0).widget()
                    if sub is not None:
                        sub.hide()
                        sub.deleteLater()
        for t in self._src_tiles.values():
            t.set_badge("")
        if not self._pairs:
            lbl = QLabel(i18n.KO.COORD_CAL_PAIRS_NONE, self)
            lbl.setProperty("role", "muted")
            self._pairs_host.addWidget(lbl)
        for i, (skey, (vkey, (dx, dy))) in enumerate(self._pairs.items(), start=1):
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(i18n.KO.COORD_CAL_PAIR_ROW_FMT.format(
                i=i, a=skey.path.name, b=vkey.path.name, dx=dx, dy=dy), self)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            rm = NeonButton(i18n.KO.COORD_CAL_REMOVE, role="ghost")
            rm.setAutoDefault(False)
            rm.clicked.connect(lambda _=False, k=skey: self._remove_pair(k))
            row.addWidget(lbl, stretch=1)
            row.addWidget(rm)
            self._pairs_host.addLayout(row)
            if skey in self._src_tiles:
                self._src_tiles[skey].set_badge(i18n.KO.COORD_CAL_PAIRED_FMT.format(i=i))
        off = self.current_offset()
        if not self._pairs:
            if self._auto is not None:
                self._result.setText(i18n.KO.COORD_CAL_AUTO_FMT.format(
                    dx=off[0], dy=off[1], n=self._auto.support))
            else:
                self._result.setText("")
            self._warn.setText("" if self._auto_weak is None else
                               i18n.KO.COORD_CAL_AUTO_WEAK_FMT.format(
                                   n=self._auto_weak.support, m=self._auto_weak.runner_up))
        else:
            res = cal.residuals([d for _v, d in self._pairs.values()], off)
            r = max(res) if res else 0.0
            self._result.setText(i18n.KO.COORD_CAL_RESULT_FMT.format(
                dx=off[0], dy=off[1], n=len(self._pairs), r=r))
            warn = (i18n.KO.COORD_CAL_RESIDUAL_WARN_FMT.format(r=r)
                    if r > self._tol * 0.5 else "")
            # 쌍 하나는 남은 오차가 늘 0 이라 위 경고가 안 뜬다 — 믿을 만한 자동값과
            # 크게 다르면 잘못 고른 쌍일 수 있다고 알린다(적용은 막지 않는다).
            if not warn and self._auto is not None:
                ax, ay = self._auto.offset
                d = math.hypot(off[0] - ax, off[1] - ay)
                if d > self._tol * 0.5:
                    warn = i18n.KO.COORD_CAL_AUTO_DIFF_FMT.format(ax=ax, ay=ay, d=d)
            self._warn.setText(warn)
        self._apply.setEnabled(off is not None)

    def _on_apply(self) -> None:
        self.offset = self.current_offset()
        if self.offset is not None:
            self.accept()
