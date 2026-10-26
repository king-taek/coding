"""좌표 보정 화면 — KLA ↔ Camtek 매칭 직전, '같은 결함' 사진 쌍을 골라 어긋남을 뺀다.

왼쪽: 기준 사진(짝 후보가 있는 것만, 후보가 적은 순).  오른쪽: 고른 기준 사진을
**후보와 같은 크기로** 맨 앞에 두고, 눈금 너머에 가까운 die 의 검증 사진(그럴듯한 순).
같은 크기로 나란히 놓아야 '같은 결함인가' 를 눈으로 견줄 수 있다 — 예전엔 기준 사진을
크게 위에 두어 후보 줄이 아래로 밀려 파일명·거리가 접힌 채 보였다.  같은 결함을 누르면
쌍이 기록되고, 바닥에 쌍별 어긋남과 보정값(중앙값)·남은 오차가 보인다.
제목은 시트 제목줄이 그리므로 화면 안에 다시 쓰지 않는다.  계산은 전부
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
_TILE_CHROME_W = 14
_CAP_H = 20
_NAME_LINES = 2          # 파일명 줄 수 — 아래 `_elide_name` 참조
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


def _two_line_name(text: str, fm: QFontMetrics, width: int) -> str:
    """``_`` 토큰 경계에서 두 줄로 접은 파일명(순수 함수 — 테스트 `test_coord_calibration`)."""
    if fm.horizontalAdvance(text) <= width:
        return text
    tokens = text.split("_")
    first = ""
    for i in range(len(tokens) - 1, 0, -1):          # 첫 줄에 최대한 많은 토큰
        cand = "_".join(tokens[:i]) + "_"
        if fm.horizontalAdvance(cand) <= width:
            first, rest = cand, "_".join(tokens[i:])
            break
    else:
        rest = text
    if not first:                                    # 첫 토큰조차 안 들어가면 한 줄 줄임
        return fm.elidedText(text, Qt.TextElideMode.ElideMiddle, width)
    return first + "\n" + fm.elidedText(rest, Qt.TextElideMode.ElideMiddle, width)


class _PhotoTile(QFrame):
    """타일 전체가 클릭 영역인 사진 카드(UI 관습 — 작은 체크박스 금지)."""

    clicked = pyqtSignal(object)          # key

    def __init__(self, key, path, px: int, *, kind: str = "thumb",
                 clickable: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.key = key
        self._selected = False
        self._px = px
        self._name_text = path.name
        self.setObjectName("calTile")
        self.setProperty("role", "card-soft")
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(px + _TILE_CHROME_W, px + (_NAME_LINES + 1) * _CAP_H + 20)
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
        name.setFixedSize(px, _NAME_LINES * _CAP_H)
        name.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        name.setToolTip(path.name)
        lay.addWidget(name, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._name = name
        self._elide_name()
        self._badge = QLabel("", self)
        self._badge.setFixedHeight(_CAP_H)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._badge)

    def _elide_name(self) -> None:
        """파일명을 사진 폭 안에 **두 줄로** — 첫 줄은 ``_`` 토큰 단위로 들어가는 만큼,
        둘째 줄은 나머지(안 들어가면 그 줄만 가운데를 줄인다).

        한 줄 ElideMiddle 만 쓰면 ``312665…_1.jpg`` 처럼 WaferID 머리만 남고 **col·row 가
        든 꼬리가 사라진다** — 같은 웨이퍼 사진은 머리가 전부 같아 서로 구분이 안 된다.
        ★ 생성 시점과 ``showEvent`` 두 번 — QSS 폰트는 polish 뒤에야 라벨에 반영되므로
        생성 시점 metrics 로만 재면 실제보다 짧게 잡혀 양끝이 잘렸다(캡처로 확인).
        참조: ``thumb_grid._elide_caption``."""
        self._name.setText(_two_line_name(self._name_text, QFontMetrics(self._name.font()),
                                          max(24, self._px - 4)))

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._elide_name()

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

        # 제목은 시트 제목줄(`sheet_host` chrome)이 그린다 — 여기 또 쓰면 두 번 보인다.
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

        # ── 오른쪽: 안내 한 줄 + [고른 기준 사진 | 눈금 | 후보 그리드] — 전부 같은 크기.
        right = QVBoxLayout()
        right.setSpacing(8)
        self._cand_head = QLabel(i18n.KO.COORD_CAL_PICK_FIRST, self)
        self._cand_head.setProperty("role", "paneTitle")
        self._cand_head.setWordWrap(True)
        right.addWidget(self._cand_head)
        row = QHBoxLayout()
        row.setSpacing(12)
        self._anchor_host = QWidget(self)
        anchor_lay = QVBoxLayout(self._anchor_host)
        anchor_lay.setContentsMargins(0, 0, 0, 0)
        anchor_lay.setSpacing(4)
        anchor_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._anchor_host.setFixedWidth(_CAND_TILE_PX + _TILE_CHROME_W)
        self._anchor_tile: Optional[_PhotoTile] = None
        row.addWidget(self._anchor_host, alignment=Qt.AlignmentFlag.AlignTop)
        rule = QFrame(self)
        rule.setProperty("role", "vrule")
        row.addWidget(rule)
        self._cand_scroll = _scroll_area(self)
        self._cand_host = QWidget(self._cand_scroll)
        self._cand_grid = QGridLayout(self._cand_host)
        self._cand_grid.setContentsMargins(0, 0, 0, 0)
        self._cand_grid.setSpacing(_GRID_SPACING)
        self._cand_grid.setAlignment(Qt.AlignmentFlag.AlignTop
                                     | Qt.AlignmentFlag.AlignLeft)
        self._cand_scroll.setWidget(self._cand_host)
        row.addWidget(self._cand_scroll, stretch=1)
        right.addLayout(row, stretch=1)
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
        if self._anchor_tile is not None:
            self._anchor_tile.hide()                # _fill_candidates 와 같은 이유
            self._anchor_tile.deleteLater()
        self._anchor_tile = _PhotoTile(key, key.path, _CAND_TILE_PX, kind="mid",
                                       clickable=False, parent=self._anchor_host)
        self._anchor_tile.set_badge(i18n.KO.COORD_CAL_ANCHOR_FMT.format(dev=self._src_dev))
        self._anchor_host.layout().addWidget(self._anchor_tile)
        self._anchor_tile.show()
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
