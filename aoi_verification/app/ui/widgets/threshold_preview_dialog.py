"""Overkill 튜닝(옛 이름 Threshold 미리보기) — Zone 기준을 올리면 Defect 가 몇 개 빠지는지 본다.

현택님 요청(2026-11-16):
  * 폴더는 웨이퍼 폴더 또는 LOT 폴더(바로 아래 웨이퍼들), 여러 개를 [+ 폴더] · 끌어다 놓기로 더한다.
    Zone 기준 · 픽셀 크기가 같은 웨이퍼 중 가장 많은 무리를 합친다(:func:`merge_wafers`).
  * [자동 추천]: 사진을 누르면 '꼭 잡기' — 고른 것을 전부 남기며 나머지를 많이 빼는 기준을
    :func:`app.threshold.preview.recommend` 로 계산해 바로 채운다.  고른 것이 있는 Zone 만 바꾼다.
    [직접 조정]으로 넘어가도 값은 그대로라 다듬을 수 있고, 고른 것이 빠지면 '놓침 n' 이 빨갛게 뜬다.

계산은 전부 :mod:`app.threshold` 코어가 한다(화면은 숫자를 다시 만들지 않는다).

화면(현택님 결정 2026-11-13 ~ 11-16):
  * 왼쪽 = **Zone 목록**(같은 이름이라도 레시피가 다르면 따로).  Defect class 로는 고르지 않는다.
  * Zone 안의 기준 묶음(같은 ``Classify``)마다 한 줄 — 묶음이 둘이면 '기준 1 · 기준 2'.
    Width · Area · Contrast 슬라이더, 시작값 = 레시피 값.  보통은 **올리기만**(최솟값 = 레시피 값).
    웨이퍼 폴더에 ``s_RichSurface.dat`` 가 있고 그 Zone 기준을 전부 그 열로 계산할 수 있으면 **내리기**도 된다
    — 하한 = 후보 값의 최솟값, '새로 생김' = 바꾼 값으로 새로 잡히는 후보 수(사진 없음, :mod:`app.threshold.rich`).
    Contrast 를 안 적는 Zone 은 Contrast 슬라이더를 두지 않는다.
  * 사진 탭: 새로 빠짐 · 남음 · EDC Good(첫 분류가 Good — 세지 않고 따로 보여 준다).
  * 사진을 누르면 그 줄 아래에 값이 펼쳐지고, 거기 사진을 누르면 크게 본다.
  * 슬라이더를 끄는 동안 사진은 빠지는 수가 :data:`LIVE_TILES_MAX` 장 이하일 때만 바로 바꾸고,
    넘으면 손을 뗄 때 바꾼다.  숫자는 늘 바로 바뀐다.
  * 앱 안 설명 문장은 두지 않는다 — 라벨 · 숫자 · 상태만.
  * HTML 내보내기(현택님 2026-11-15, ★ 저장 대신): 레시피 → 변경 값, 새로 빠지는 수, 분포, 대표 사진 — :mod:`app.threshold.report`.

값은 Zone ini 와 같은 픽셀 단위로 계산하고, 화면에만 µm 로 보인다(면적 = px_x·px_y, 폭 = px_x —
사진 정보 화면 :mod:`coords.geometry` 와 같은 환산).

읽기(Surface.flt · Zone ini · 사진 짝찾기)는 워커 스레드가 하고 진행은 시그널로
:class:`LoadingOverlay` 에 보낸다(CLAUDE.md 로딩 계약).
"""

from __future__ import annotations

import base64
import logging
import math
import os
from datetime import datetime
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QBuffer, QIODevice, QLocale, QObject, QRectF, Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QDoubleValidator, QPainter
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QDialog, QFileDialog, QFrame, QGridLayout,
                             QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QVBoxLayout,
                             QWidget)

from ... import i18n
from ...coords import abs_coord
from ...coords.geometry import GEOMETRY_MATCH_TOL
from ...coords import surface_flt as _flt
from ...coords.pixel_size import scan_pixel_size_xy
from ...models.slot import _list_images, list_slot_dirs
from ...threshold import preview as P
from ...threshold import report as RP
from ...threshold import rich as RS
from ...threshold import rules as R
from ...utils import image_io
from .. import theme
from ..folder_picker import FolderPicker
from . import sheet_host as sheets
from .loading_overlay import LoadingOverlay
from .neon_button import NeonButton
from .no_wheel_slider import NoWheelSlider

_LOG = logging.getLogger("aoi.threshold")
_KO = i18n.KO

LIVE_TILES_MAX = 100        # 이 수 이하일 때만 슬라이더를 끄는 동안 사진을 바로 바꾼다(현택님 2026-11-14)
PAGE_TILES = 60             # 처음 보이는 사진 수 — 나머지는 [더 보기]
TILE_PX = 132
DETAIL_PX = 240
EXPORT_SAMPLES = 8          # HTML 내보내기 — Zone 마다 대표 사진 수
EXPORT_PHOTO_PX = 200
_LIVE: set = set()          # 화면이 닫혀도 도는 읽기 스레드를 붙들어 둔다(wafer_map_dialog 와 같은 패턴)

# 화면에 보이는 순서와 단위.  Surface.flt 에 값이 있는 세 기준만 바꿀 수 있다(rules 모듈 docstring).
_FEATURES = ((R.FEATURE_WIDTH, "Width", "µm", 0.05, 2),
             (R.FEATURE_AREA, "Area", "µm²", 0.5, 1),
             (R.FEATURE_CONTRAST, "Contrast", "", 1.0, 1))


@dataclass
class WaferData:
    folder: Path
    defects: tuple = ()
    goods: tuple = ()                 # 첫 분류가 Good(EDC Good) — 세지 않는다
    zones: dict = field(default_factory=dict)
    classes: dict = field(default_factory=dict)
    px: tuple = (0.0, 0.0)            # (px_x, px_y) µm/px — 0 이면 못 읽음
    photos: dict = field(default_factory=dict)      # Defect.index → 사진
    note: str = ""
    misses: int = 0
    rich: dict = field(default_factory=dict)        # (zone_dir, zone) → RichSurface 후보 값 — 내리기용
    folders: tuple = ()               # 합친 웨이퍼 폴더들(하나면 folder 하나)
    skipped: tuple = ()               # 못 합친 웨이퍼 폴더 — 못 읽었거나 Zone 기준 · 픽셀 크기가 첫 웨이퍼와 다름

    def to_um(self, feature: int, v: float) -> float:
        if feature == R.FEATURE_AREA:
            return v * self.px[0] * self.px[1]
        if feature == R.FEATURE_WIDTH:
            return v * self.px[0]
        return v

    def to_px(self, feature: int, v: float) -> float:
        if feature == R.FEATURE_AREA:
            return v / (self.px[0] * self.px[1])
        if feature == R.FEATURE_WIDTH:
            return v / self.px[0]
        return v


def load_wafer(folder: Path, report=lambda msg, done=0, total=0: None, stop=lambda: False,
               rich: bool = True) -> WaferData:
    """웨이퍼 폴더 하나를 읽는다(워커 스레드에서).  못 읽은 것은 ``note`` 에 적고 raise 하지 않는다.

    ``rich`` 가 거짓이면 RichSurface 를 읽지 않는다 — 여러 장을 합칠 때는 내리기를 하지 않는다
    (웨이퍼당 후보 약 100만 개라 여러 장이면 슬라이더마다 수천만 개를 다시 센다)."""
    report(_KO.THRESH_PREVIEW_LOADING)
    defects, goods, note = P.load_all(folder)
    w = WaferData(folder, defects, goods, note=note)
    if note:
        return w
    w.zones = R.read_zones(folder)
    w.classes = R.read_classes(folder)
    px = scan_pixel_size_xy(folder)
    if not px:
        w.note = _KO.THRESH_PREVIEW_NO_PIXEL          # µm 로 못 바꾼다 — 추측값(0.77)으로 보이지 않는다
        return w
    w.px = (float(px[0]), float(px[1]))
    w.misses = len(P.recipe_misses(defects, w.zones))
    report(_KO.THRESH_PREVIEW_PHOTOS)
    photos = _list_images(folder)
    total = len(photos)

    seen = 0

    def xy_of(path):
        nonlocal seen
        seen += 1
        if seen % 200 == 0 or seen == total:
            report(_KO.THRESH_PREVIEW_PHOTOS, seen, total)
        return abs_coord.absolute_xy(path)

    w.photos = {(0, i): ph for i, ph in P.match_photos(defects + goods, photos, xy_of, GEOMETRY_MATCH_TOL).items()}
    if rich and (folder / RS.RICH_FILE).is_file():
        report(_KO.THRESH_PREVIEW_RICH)
        w.rich = RS.load_rich(folder, lambda done, total: report(_KO.THRESH_PREVIEW_RICH, done, total), stop)
    return w


def wafer_folders(paths) -> list:
    """고른 폴더들 → 웨이퍼 폴더 목록.  Surface.flt 가 있으면 그 폴더, 없으면 바로 아래에서 Surface.flt 가 든
    폴더들(LOT 폴더).  같은 폴더는 한 번만."""
    out: list = []
    seen: set = set()
    for p in map(Path, paths):
        found = [p] if _flt._find_flt(p) is not None else [
            d for _n, d in sorted(list_slot_dirs(p).items()) if _flt._find_flt(d) is not None]
        for d in found:
            real = os.path.normcase(str(d.resolve()))   # LOT 과 그 안의 웨이퍼를 함께 골라도 한 번만
            if real not in seen:
                seen.add(real)
                out.append(d)
    return out


def key_of(d: P.Defect) -> tuple:
    """Defect 의 키 — 여러 웨이퍼를 합치면 index 가 겹치므로 (웨이퍼, index)."""
    return d.wafer, d.index


def merge_wafers(ws: list) -> WaferData:
    """웨이퍼 여러 장을 한 집합으로 — Zone 기준 · 픽셀 크기가 같은 웨이퍼 중 가장 많은 무리만 합친다.

    기준이 다르면(레시피가 다르면) 같은 슬라이더 값으로 셀 수 없어 빼고 ``skipped`` 에 적는다.
    Defect 에는 몇 번째 웨이퍼인지(``wafer``)를 붙이고 사진 짝 키도 (웨이퍼, index) 로 바꾼다.
    RichSurface(내리기)는 한 장일 때만 쓴다."""
    good = [w for w in ws if not w.note]
    if not good:
        return WaferData(ws[0].folder, note=ws[0].note, skipped=tuple(w.folder for w in ws))
    groups: list = []
    for w in good:
        g = next((g for g in groups if g[0].zones == w.zones
                  and all(abs(a - b) < 1e-6 for a, b in zip(g[0].px, w.px))), None)
        if g is None:
            groups.append([w])
        else:
            g.append(w)
    same = max(groups, key=len)
    base = same[0]
    out = WaferData(base.folder, zones=base.zones, classes=base.classes, px=base.px,
                    folders=tuple(w.folder for w in same),
                    skipped=tuple(w.folder for w in ws if not any(w is x for x in same)),
                    rich=base.rich if len(same) == 1 else {})
    defects, goods = [], []
    for i, w in enumerate(same):
        ds = {d.index: replace(d, wafer=i) for d in w.defects + w.goods}
        defects += [ds[d.index] for d in w.defects]
        goods += [ds[d.index] for d in w.goods]
        out.photos.update({(i, k[1]): ph for k, ph in w.photos.items()})
        out.misses += w.misses
    out.defects, out.goods = tuple(defects), tuple(goods)
    return out


class _Load(QThread):
    class _Signals(QObject):
        progress = pyqtSignal(int, int, int, str)     # token, done, total, msg
        done = pyqtSignal(int, object)                # token, WaferData | None(실패)

    def __init__(self, token: int, roots: list, cache: dict) -> None:
        super().__init__()
        self._token, self._roots = token, list(roots)
        self.cache = dict(cache)          # 폴더 → WaferData — '폴더 추가' 때 앞에서 읽은 것은 다시 안 읽는다
        self.signals = self._Signals()
        _LIVE.add(self)
        self.finished.connect(lambda: _LIVE.discard(self))

    def run(self) -> None:      # type: ignore[override]
        t = self._token
        try:
            folders = wafer_folders(self._roots)
            ws = []
            for i, folder in enumerate(folders, start=1):
                if self.isInterruptionRequested():
                    return
                head = _KO.OVERKILL_WAFER_N.format(i=i, n=len(folders)) if len(folders) > 1 else ""
                key = (folder, len(folders) == 1)          # 한 장일 때만 RichSurface 를 읽는다
                if key not in self.cache:
                    self.cache[key] = load_wafer(folder, lambda msg, done=0, total=0, h=head:
                                                 self.signals.progress.emit(t, done, total, h + msg),
                                                 self.isInterruptionRequested, rich=key[1])
                ws.append(self.cache[key])
            w = merge_wafers(ws) if ws else WaferData(Path(self._roots[0]), note=_KO.OVERKILL_NO_WAFER)
        except Exception:       # noqa: BLE001 — 실패를 삼키지 않고 화면에 알린다
            _LOG.exception("Overkill 튜닝 읽기 실패: %s", self._roots)
            w = None
        self.signals.done.emit(t, w)


def zone_label(zone_dir: str, zone: R.Zone | None, zone_id: int) -> tuple[str, str]:
    """``(Zone 이름, 레시피 표시)`` — ``Zones`` 는 레시피 표시 없음, ``Recipe2-Zones`` 는 '레시피 2'."""
    name = zone.name if zone is not None and zone.name else f"Zone {zone_id}"
    rec = "" if zone_dir == "Zones" else _KO.THRESH_PREVIEW_RECIPE.format(
        n=zone_dir.removeprefix("Recipe").removesuffix("-Zones"))
    return name, rec


class _Hist(QWidget):
    """기준값 오른쪽에 있는 Defect 분포 — 지금 기준 아래(빠지는 쪽) 막대는 빨갛다."""

    BINS = 28

    def __init__(self, values, lo: float, hi: float, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._lo, self._hi, self._cut = lo, hi, lo
        span = (hi - lo) or 1.0
        self._counts = [0] * self.BINS
        for v in values:
            if lo <= v <= hi:
                self._counts[min(self.BINS - 1, int((v - lo) / span * self.BINS))] += 1

    def set_cut(self, v: float) -> None:
        self._cut = v
        self.update()

    def paintEvent(self, _e):       # noqa: N802
        p = QPainter(self)
        n, w, h = self.BINS, self.width(), self.height()
        top = max(1, max(self._counts))
        bw = w / n
        span = (self._hi - self._lo) or 1.0
        for i, c in enumerate(self._counts):
            mid = self._lo + (i + 0.5) * span / n
            col = QColor(theme.DANGER if mid < self._cut else theme.LINE)
            bh = max(1.0, h * c / top) if c else 1.0
            p.fillRect(QRectF(i * bw + 0.5, h - bh, bw - 1, bh), col)
        p.end()


class _Tile(QFrame):
    """사진 한 장 + 캡션.  누르면 ``clicked``.  자동 모드에서는 오른쪽 위 작은 버튼이 값 펼치기(``detail``)다."""

    clicked = pyqtSignal()
    detail = pyqtSignal()

    def __init__(self, photo: Path, caption: str, value: str, size: int, parent=None,
                 picked: bool = False, detail_btn: bool = False) -> None:
        super().__init__(parent)
        self.setProperty("role", "card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.picked = picked
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(3)
        pic = QLabel(self)
        pic.setFixedSize(size, size)
        pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pic.setPixmap(image_io.cached_tile_pixmap(photo, size))
        lay.addWidget(pic)
        self.badge = QLabel("✓", pic)                  # 꼭 잡기 표시
        self.badge.setFixedSize(24, 24)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setStyleSheet(f"background: {theme.ACCENT}; color: {theme.ON_ACCENT}; border-radius: 12px;"
                                 " font-weight: 700;")
        self.badge.move(6, 6)
        self.badge.setVisible(picked)
        self.detail_btn = None
        if detail_btn:
            self.detail_btn = QLabel("⋯", pic)          # 누르면 값 펼치기 — 타일 본체 누름(고르기)과 따로
            self.detail_btn.setFixedSize(26, 22)
            self.detail_btn.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.detail_btn.setToolTip(_KO.OVERKILL_DETAIL_TIP)
            self.detail_btn.setStyleSheet(f"background: {theme.PANEL}; color: {theme.INK}; border-radius: 4px;"
                                          " font-weight: 700;")
            self.detail_btn.move(size - 32, 6)
        row = QHBoxLayout()
        row.setSpacing(4)
        cap = QLabel(caption, self)
        cap.setProperty("role", "muted")
        cap.setMaximumWidth(size - 50 if value else size)
        row.addWidget(cap, 1)
        if value:
            v = QLabel(value, self)
            v.setStyleSheet(f"color: {theme.DANGER}; font-family: {theme.FONT_MONO};")
            row.addWidget(v)
        lay.addLayout(row)

    def set_open(self, on: bool) -> None:
        if self.picked:
            self.setStyleSheet(f"QFrame[role='card'] {{ border: 2px solid {theme.ACCENT};"
                               f" background: {theme.ACCENT_TINT}; }}")
        else:
            self.setStyleSheet(f"QFrame[role='card'] {{ border: 2px solid {theme.ACCENT}; }}" if on else "")

    def mousePressEvent(self, e):   # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            hit = self.detail_btn is not None and self.detail_btn.geometry().contains(
                self.detail_btn.parentWidget().mapFrom(self, e.position().toPoint()))
            (self.detail if hit else self.clicked).emit()
        super().mousePressEvent(e)


class _BigPhoto(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, e):   # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


def _data_uri(photo: Path) -> str:
    """사진 → 작은 JPEG ``data:`` 주소(HTML 안에 넣는다).  못 읽으면 빈 문자열."""
    pix = image_io.load_thumb_qpixmap(photo, EXPORT_PHOTO_PX, kind="mid")
    if pix.isNull():
        return ""
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not pix.save(buf, "JPG", 82):
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(bytes(buf.data())).decode("ascii")


class ThresholdPreviewDialog(QDialog):
    """왼쪽 메뉴 'Overkill 튜닝' 화면."""

    def __init__(self, parent=None, folder: Optional[Path] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_KO.THRESH_PREVIEW_TITLE)
        self.setAcceptDrops(True)
        self._w: Optional[WaferData] = None
        self._roots: list = []            # 고른 폴더(웨이퍼 또는 LOT) — 칩 하나씩
        self._cache: dict = {}            # (웨이퍼 폴더, RichSurface 읽음) → WaferData
        self._mode = "manual"             # manual(직접 조정) | auto(자동 추천)
        self._picked: dict = {}           # 꼭 잡을 Defect — key_of(d) → Defect
        self._rec_zones: set = set()      # 마지막 추천이 바꾼 Zone — 고름이 바뀌면 이 Zone 들만 다시 계산
        self._ov: dict = {}               # {(zone_dir, zone, Classify, FeatureType): 새 LowValue(px)}
        self._rich_base: dict = {}        # (zone_dir, zone) → 레시피 값 RichSurface 통과 배열(캐시)
        self._zone: Optional[tuple] = None
        self._tab = "drop"
        self._open: Optional[int] = None  # 펼친 Defect.index
        self._shown = PAGE_TILES
        self._token = 0
        self._thread: Optional[_Load] = None
        self._picker = FolderPicker(self)
        self._picker.picked.connect(lambda p: p and self.add_folders([Path(p)]))
        self._build()
        self._loading = LoadingOverlay(self)
        if folder:
            self.set_folders([Path(folder)])
        else:
            self._render()

    # ------------------------------------------------------------------ 틀
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.pick_btn = NeonButton(_KO.OVERKILL_ADD_FOLDER, role="primary")
        self.pick_btn.setToolTip(_KO.OVERKILL_ADD_FOLDER_TIP)
        self.pick_btn.clicked.connect(self._on_pick)
        top.addWidget(self.pick_btn)
        chips = QWidget(self)                       # 고른 폴더 — 누르면 뺀다
        chips.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._chips = QHBoxLayout(chips)
        self._chips.setContentsMargins(0, 0, 0, 0)
        self._chips.setSpacing(6)
        top.addWidget(chips, 1)
        self.wafer_label = QLabel("", self)
        self.wafer_label.setStyleSheet(f"font-family: {theme.FONT_MONO};")
        top.addWidget(self.wafer_label)
        self.check_label = QLabel("", self)
        self.check_label.setProperty("role", "muted")
        top.addWidget(self.check_label)
        top.addSpacing(12)
        self._mode_group = QButtonGroup(self)
        self.mode_btns = {}
        for key, text in (("manual", _KO.OVERKILL_MODE_MANUAL), ("auto", _KO.OVERKILL_MODE_AUTO)):
            b = NeonButton(text, role="segment")
            b.setCheckable(True)
            b.setChecked(key == self._mode)
            b.clicked.connect(lambda _c=False, k=key: self.set_mode(k))
            self._mode_group.addButton(b)
            top.addWidget(b)
            self.mode_btns[key] = b
        root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(16)
        self._zone_col = QVBoxLayout()
        self._zone_col.setSpacing(6)
        zhost = QWidget(self)
        zhost.setFixedWidth(220)
        zhost.setLayout(self._zone_col)
        body.addWidget(zhost, 0, Qt.AlignmentFlag.AlignTop)
        self._zone_group = QButtonGroup(self)
        self._zone_group.setExclusive(True)

        main = QVBoxLayout()
        main.setSpacing(12)
        summ = QHBoxLayout()
        summ.setSpacing(24)
        box = QVBoxLayout()
        box.setSpacing(0)
        cap = QLabel(_KO.THRESH_PREVIEW_DROPPED, self)
        cap.setProperty("role", "statCaption")
        box.addWidget(cap)
        self.drop_value = QLabel("0", self)
        self.drop_value.setProperty("role", "statValue")
        self.drop_value.setStyleSheet("font-size: 40px;")
        box.addWidget(self.drop_value)
        summ.addLayout(box)
        self.new_box = QWidget(self)
        nb = QVBoxLayout(self.new_box)
        nb.setContentsMargins(0, 0, 0, 0)
        nb.setSpacing(0)
        cap = QLabel(_KO.THRESH_PREVIEW_APPEARED, self.new_box)
        cap.setProperty("role", "statCaption")
        nb.addWidget(cap)
        self.new_value = QLabel("0", self.new_box)
        self.new_value.setProperty("role", "statValue")
        self.new_value.setStyleSheet("font-size: 40px;")
        nb.addWidget(self.new_value)
        self.new_box.setVisible(False)
        summ.addWidget(self.new_box)
        self.keep_label = QLabel("", self)
        self.all_label = QLabel("", self)
        for lab in (self.keep_label, self.all_label):
            lab.setStyleSheet(f"font-family: {theme.FONT_MONO}; font-size: 15px;")
            summ.addWidget(lab, 0, Qt.AlignmentFlag.AlignBottom)
        self.pick_label = QLabel("", self)           # 꼭 잡기 k — 하나라도 빠지면 빨간 '놓침 m'
        self.pick_label.setStyleSheet(f"font-family: {theme.FONT_MONO}; font-size: 15px; color: {theme.ACCENT};"
                                      " font-weight: 700;")
        summ.addWidget(self.pick_label, 0, Qt.AlignmentFlag.AlignBottom)
        self.miss_label = QLabel("", self)
        self.miss_label.setStyleSheet(f"font-family: {theme.FONT_MONO}; font-size: 15px; color: {theme.DANGER};"
                                      " font-weight: 700;")
        summ.addWidget(self.miss_label, 0, Qt.AlignmentFlag.AlignBottom)
        summ.addStretch(1)
        self.reset_btn = NeonButton(_KO.THRESH_PREVIEW_RESET, role="ghost")
        self.reset_btn.clicked.connect(self.reset)
        summ.addWidget(self.reset_btn, 0, Qt.AlignmentFlag.AlignBottom)
        self.export_btn = NeonButton(_KO.THRESH_PREVIEW_EXPORT, role="ghost")
        self.export_btn.clicked.connect(self._on_export)
        summ.addWidget(self.export_btn, 0, Qt.AlignmentFlag.AlignBottom)
        main.addLayout(summ)

        self.rules = QFrame(self)
        self.rules.setProperty("role", "card")
        self._rules_lay = QVBoxLayout(self.rules)
        self._rules_lay.setContentsMargins(16, 12, 16, 12)
        self._rules_lay.setSpacing(12)
        main.addWidget(self.rules)

        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self._tabs_lay = tabs
        self._tab_group = QButtonGroup(self)
        self.tab_btns = {}
        for key in ("drop", "keep", "good", "pick"):
            b = NeonButton("", role="segment")
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, k=key: self._set_tab(k))
            self._tab_group.addButton(b)
            tabs.addWidget(b)
            self.tab_btns[key] = b
        self.no_photo_label = QLabel("", self)
        self.no_photo_label.setProperty("role", "muted")
        tabs.addSpacing(8)
        tabs.addWidget(self.no_photo_label)
        tabs.addStretch(1)
        main.addLayout(tabs)

        self.grid_host = QWidget(self)
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        main.addWidget(self.grid_host)
        self.empty_label = QLabel("", self)
        self.empty_label.setProperty("role", "muted")
        main.addWidget(self.empty_label)
        main.addStretch(1)
        body.addLayout(main, 1)
        root.addLayout(body, 1)

    # ------------------------------------------------------------------ 폴더
    def _on_pick(self) -> None:
        self._picker.open(_KO.THRESH_PREVIEW_PICK_TITLE, str(self._roots[-1]) if self._roots else "")

    def add_folders(self, folders) -> None:
        """고른 폴더(웨이퍼 또는 LOT)를 더한다 — 이미 있는 것은 그대로."""
        self.set_folders(self._roots + [Path(f) for f in folders if Path(f) not in self._roots])

    def remove_folder(self, folder: Path) -> None:
        self.set_folders([r for r in self._roots if r != folder])

    def set_folders(self, roots) -> None:
        """이 폴더들을 다시 읽어 합친다(앞에서 읽은 웨이퍼는 다시 안 읽는다)."""
        self._roots = list(roots)
        self._render_chips()
        if not self._roots:
            self._token += 1
            self.set_wafer(None)
            return
        self.show_folder()

    def show_folder(self, folder: Optional[Path] = None) -> None:
        if folder is not None:                      # 옛 호출(폴더 하나) — 그 폴더로 바꾼다
            self.set_folders([folder])
            return
        self._token += 1
        token = self._token
        if self._thread is not None and self._thread.isRunning():
            self._thread.requestInterruption()
        self._loading.show_overlay(_KO.THRESH_PREVIEW_LOADING)
        self._loading.repaint()
        QApplication.processEvents()
        th = _Load(token, self._roots, self._cache)
        th.signals.progress.connect(self._on_progress)
        th.signals.done.connect(self._on_loaded)
        th.finished.connect(lambda t=th: self._cache.update(t.cache))
        self._thread = th
        th.start()

    def _render_chips(self) -> None:
        self._clear(self._chips)
        for r in self._roots:
            b = NeonButton(f"{r.name}  ✕", role="ghost")
            b.setToolTip(str(r))
            b.clicked.connect(lambda _c=False, f=r: self.remove_folder(f))
            self._chips.addWidget(b)
        self._chips.addStretch(1)

    def dragEnterEvent(self, e):    # noqa: N802
        if any(Path(u.toLocalFile()).is_dir() for u in e.mimeData().urls() if u.isLocalFile()):
            e.acceptProposedAction()

    def dropEvent(self, e):         # noqa: N802
        dirs = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        dirs = [d for d in dirs if d.is_dir()]
        if dirs:
            self.add_folders(dirs)
            e.acceptProposedAction()

    def _on_progress(self, token: int, done: int, total: int, msg: str) -> None:
        if token == self._token:
            self._loading.set_progress(done, total, msg)

    def _on_loaded(self, token: int, w) -> None:
        if token != self._token:
            return                      # 늦게 온 옛 결과
        self._loading.hide_overlay()
        self.set_wafer(w if w is not None else WaferData(Path(self._roots[0]) if self._roots else Path(),
                                                         note=_KO.THRESH_PREVIEW_FAILED))

    def set_wafer(self, w: Optional[WaferData]) -> None:
        """읽은 웨이퍼(여러 장이면 합친 것)를 화면에 올린다(테스트는 스레드 없이 이것만 부른다)."""
        self._w = w
        self._ov = {}
        self._rich_base = {}
        self._picked = {}
        self._rec_zones = set()
        self._open = None
        self._shown = PAGE_TILES
        self._tab = "keep" if self._mode == "auto" else "drop"
        if w is None:
            self._zone = None
            self._render()
            return
        zs = self.zone_keys()
        self._zone = next((k for k in zs if w.zones.get(k) and w.zones[k].bundles
                           and self._count(k)), zs[0] if zs else None)
        self._render()

    def is_loading(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    # ------------------------------------------------------------------ 계산
    def zone_keys(self) -> list:
        """Defect 가 있거나 기준이 있는 Zone — Defect 많은 순."""
        w = self._w
        if w is None:
            return []
        keys = {(d.zone_dir, d.zone) for d in w.defects + w.goods}
        keys |= {k for k, z in w.zones.items() if z.bundles}
        return sorted(keys, key=lambda k: (-self._count(k), k))

    def _count(self, key) -> int:
        return sum(1 for d in self._w.defects if (d.zone_dir, d.zone) == key)

    def _in_zone(self, key=None) -> list:
        key = key or self._zone
        return [d for d in self._w.defects if (d.zone_dir, d.zone) == key]

    def _rich_cand(self, key=None):
        """이 Zone 의 RichSurface 후보 값 — 기준을 전부 그 열로 계산할 수 있을 때만, 아니면 None."""
        key = key or self._zone
        cand = self._w.rich.get(key) if self._w else None
        return cand if cand is not None and RS.supports(self._w.zones.get(key)) else None

    def appeared(self, key=None) -> int:
        """내린 값으로 새로 잡히는 후보 수(RichSurface 가 있는 Zone 만, 없으면 0)."""
        key = key or self._zone
        cand = self._rich_cand(key)
        if cand is None or not any(k[:2] == key for k in self._ov):
            return 0
        zone = self._w.zones[key]
        if key not in self._rich_base:
            self._rich_base[key] = RS.passes(cand, zone, key, {})
        return RS.count_new(cand, zone, key, self._ov, self._rich_base[key])

    def dropped(self, key=None) -> list:
        """지금 기준으로 빠지는 Defect — ``[(Defect, (Classify, FeatureType))]``."""
        out = []
        for d in self._in_zone(key):
            why = P.why_dropped(d, self._w.zones, self._ov)
            if why is not None:
                out.append((d, why))
        return out

    # ------------------------------------------------------------------ 그리기
    def _render(self) -> None:
        w = self._w
        self.reset_btn.setEnabled(bool(self._ov))
        self.export_btn.setEnabled(bool(self._ov))
        if w is None or w.note:
            self.check_label.setText(w.note if w else "")
        else:
            n = len(w.defects)
            self.check_label.setText((_KO.THRESH_PREVIEW_CHECK_OK if not w.misses else
                                      _KO.THRESH_PREVIEW_CHECK_BAD).format(ok=n - w.misses, n=n))
        many = w is not None and (len(w.folders) > 1 or w.skipped)
        text = _KO.OVERKILL_WAFERS.format(n=len(w.folders)) if many else ""
        if many and w.skipped:
            text += "   " + _KO.OVERKILL_SKIPPED.format(n=len(w.skipped))
        self.wafer_label.setText(text)
        self.wafer_label.setToolTip("\n".join(
            [str(f) for f in w.folders] + ([f"— {_KO.OVERKILL_SKIPPED_HEAD}"] + [str(f) for f in w.skipped]
                                            if w.skipped else [])) if many else "")
        self.wafer_label.setStyleSheet(f"font-family: {theme.FONT_MONO};"
                                       + (f" color: {theme.WARN};" if many and w.skipped else ""))
        self._render_zones()
        self._render_rules()
        self._render_summary()
        self._render_tiles()

    @staticmethod
    def _clear(lay) -> None:
        while lay.count():
            it = lay.takeAt(0)
            wid = it.widget()
            if wid is not None:
                wid.hide()              # deleteLater 는 다음 이벤트 때라 그 사이 새 칸과 겹쳐 보인다
                wid.deleteLater()
            elif it.layout() is not None:
                ThresholdPreviewDialog._clear(it.layout())

    def _render_zones(self) -> None:
        for b in self._zone_group.buttons():
            self._zone_group.removeButton(b)
        self._clear(self._zone_col)
        head = QLabel(_KO.THRESH_PREVIEW_ZONE, self)
        head.setProperty("role", "colHead")
        self._zone_col.addWidget(head)
        self.zone_btns = {}
        if self._w is None or self._w.note:
            return
        for key in self.zone_keys():
            b = NeonButton("", role="option")
            b.setCheckable(True)
            b.setChecked(key == self._zone)
            b.setMinimumHeight(54)
            b.clicked.connect(lambda _c=False, k=key: self._set_zone(k))
            self._zone_group.addButton(b)
            self._zone_col.addWidget(b)
            self.zone_btns[key] = b
            self._zone_text(key)
        self._zone_col.addStretch(1)

    def _zone_text(self, key) -> None:
        name, rec = zone_label(key[0], self._w.zones.get(key), key[1])
        drop = len(self.dropped(key))
        new = self.appeared(key)
        line1 = f"{name}" + (f"  · {rec}" if rec else "")
        line2 = _KO.THRESH_PREVIEW_ZONE_COUNT.format(n=self._count(key))
        picks = sum(1 for d in self._picked.values() if (d.zone_dir, d.zone) == key)
        moved = "   ".join(t for t in (f"−{drop:,}" if drop else "", f"+{new:,}" if new else "",
                                       f"✓{picks:,}" if picks else "") if t)
        self.zone_btns[key].setText(f"{line1}\n{line2}" + (f"\n{moved}" if moved else ""))

    def _render_rules(self) -> None:
        self._clear(self._rules_lay)
        self.sliders = {}
        w = self._w
        if w is None or w.note or self._zone is None:
            self.rules.setVisible(False)
            return
        self.rules.setVisible(True)
        z = w.zones.get(self._zone)
        if z is None or not z.bundles:
            lab = QLabel(_KO.THRESH_PREVIEW_NO_FILTER, self.rules)
            lab.setProperty("role", "muted")
            self._rules_lay.addWidget(lab)
            return
        known = P.contrast_known(w.defects, *self._zone)
        many = len(z.bundles) > 1
        for i, (cls, filters) in enumerate(z.bundles.items(), start=1):
            if i > 1:
                rule = QFrame(self.rules)
                rule.setFrameShape(QFrame.Shape.HLine)
                rule.setStyleSheet(f"color: {theme.LINE2};")
                self._rules_lay.addWidget(rule)
            row = QHBoxLayout()
            row.setSpacing(20)
            head = QVBoxLayout()
            head.setSpacing(2)
            t = QLabel(_KO.THRESH_PREVIEW_RULE_N.format(n=i) if many else _KO.THRESH_PREVIEW_RULE, self.rules)
            t.setStyleSheet("font-weight: 700;")
            head.addWidget(t)
            sub = QLabel(f"→ {self._class_name(cls)}", self.rules)
            sub.setProperty("role", "muted")
            head.addWidget(sub)
            head.addStretch(1)
            hw = QWidget(self.rules)
            hw.setLayout(head)
            hw.setFixedWidth(150)
            row.addWidget(hw)
            for feat, name, unit, step, dec in _FEATURES:
                f = next((x for x in filters if x.feature == feat), None)
                if f is None or (feat == R.FEATURE_CONTRAST and not known):
                    continue                       # Contrast 를 안 적는 Zone — 슬라이더 없음(현택님 2026-11-16)
                row.addWidget(self._feature_cell(cls, f, name, unit, step, dec), 1)
            row.addStretch(0)
            self._rules_lay.addLayout(row)

    def _feature_cell(self, cls: int, f: R.Filter, name: str, unit: str, step: float, dec: int) -> QWidget:
        w = self._w
        key = (*self._zone, cls, f.feature)
        rec = w.to_um(f.feature, f.low)
        zone_vals = [w.to_um(f.feature, P._value(d, f.feature)) for d in self._in_zone()]
        vals = [w.to_um(f.feature, P._value(d, f.feature)) for d in self._in_zone() if d.bin == cls]
        if f.feature == R.FEATURE_CONTRAST:
            vals = [v for v in vals if v != 0]
        # 끝 = 이 Zone 에서 가장 큰 값(그 위로 올리면 전부 빠져 더 볼 게 없다) — 레시피의 2배보다 작으면 2배.
        hi = max(rec * 2 if rec > 0 else rec + 10 * step, max(zone_vals, default=rec))
        steps = max(1, math.ceil((hi - rec) / step))
        hi = rec + steps * step
        # 내리기: RichSurface 후보가 있는 Zone 만.  하한 = 후보 값의 최솟값(그 아래는 파일이 모른다).
        down = 0
        cand = self._rich_cand()
        if cand is not None:
            floor = w.to_um(f.feature, float(cand[f.feature].min())) if len(cand[f.feature]) else rec
            down = max(0, min(math.ceil((rec - max(floor, 0.0)) / step), math.floor(rec / step)))
        lo = rec - down * step
        cell = QWidget(self.rules)
        lay = QVBoxLayout(cell)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        h = QHBoxLayout()
        h.setSpacing(6)
        lab = QLabel(f"{name} ≥", cell)
        lab.setStyleSheet("font-weight: 600;")
        h.addWidget(lab)
        spin = QLineEdit(cell)                 # 위·아래 버튼 없이 숫자만 친다 — 조금씩은 슬라이더로
        val = QDoubleValidator(0.0, 1e9, dec, spin)   # 범위 밖 값도 받아서 apply 가 끝으로 맞춘다
        val.setNotation(QDoubleValidator.Notation.StandardNotation)
        val.setLocale(QLocale.c())
        spin.setValidator(val)
        spin.setFixedWidth(90)
        spin.setProperty("role", "mono")
        spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(spin)
        if unit:
            u = QLabel(unit, cell)
            u.setProperty("role", "muted")
            h.addWidget(u)
        delta = QLabel("", cell)
        delta.setStyleSheet(f"color: {theme.DANGER}; font-family: {theme.FONT_MONO};")
        h.addStretch(1)
        h.addWidget(delta)
        lay.addLayout(h)
        hist = _Hist(vals, lo, hi, cell)
        lay.addWidget(hist)
        sl = NoWheelSlider(Qt.Orientation.Horizontal, cell)
        sl.setRange(0, down + steps)
        lay.addWidget(sl)
        foot = QLabel(_KO.THRESH_PREVIEW_RECIPE_VALUE.format(v=f"{rec:.{dec}f}")
                      + (f"   {_KO.THRESH_PREVIEW_LOWEST.format(v=f'{lo:.{dec}f}')}" if down else ""), cell)
        foot.setProperty("role", "muted")
        lay.addWidget(foot)

        cur = w.to_um(f.feature, self._ov[key]) if key in self._ov else rec

        def show(v: float) -> None:
            spin.setText(f"{v:.{dec}f}")
            sl.blockSignals(True)
            sl.setValue(down + round((v - rec) / step))
            sl.blockSignals(False)
            hist.set_cut(v)
            d = v - rec
            delta.setText(f"+{d:.{dec}f}" if d > 1e-9 else f"−{-d:.{dec}f}" if d < -1e-9 else "")
            delta.setStyleSheet(f"color: {theme.DANGER if d > 0 else theme.PASS}; font-family: {theme.FONT_MONO};")

        def apply(v: float, final: bool) -> None:
            v = min(max(v, lo), hi)
            if abs(v - rec) <= 1e-9:
                self._ov.pop(key, None)
            else:
                self._ov[key] = w.to_px(f.feature, v)
            show(v)
            self._changed(final)

        sl.valueChanged.connect(lambda i: apply(rec + (i - down) * step, False))
        sl.sliderReleased.connect(lambda: apply(rec + (sl.value() - down) * step, True))
        def typed() -> None:
            try:
                apply(float(spin.text().replace(",", "")), True)
            except ValueError:
                show(rec + (sl.value() - down) * step)  # 못 읽으면 지금 값으로 되돌린다

        spin.editingFinished.connect(typed)
        show(cur)
        self.sliders[(cls, f.feature)] = (sl, spin)
        return cell

    def _changed(self, final: bool) -> None:
        self.reset_btn.setEnabled(bool(self._ov))
        self.export_btn.setEnabled(bool(self._ov))
        self._zone_text(self._zone)
        drop = self._render_summary()
        if final or drop <= LIVE_TILES_MAX:
            self._open = None if self._tab != "good" else self._open
            self._render_tiles()

    def _render_summary(self) -> int:
        w = self._w
        if w is None or w.note or self._zone is None:
            self.drop_value.setText("0")
            self.new_box.setVisible(False)
            self.keep_label.setText("")
            self.all_label.setText("")
            for b in self.tab_btns.values():
                b.setVisible(False)
            self.pick_label.setText("")
            self.miss_label.setText("")
            return 0
        n = len(self._in_zone())
        drop = len(self.dropped())
        self.drop_value.setText(f"{drop:,}")
        self.drop_value.setProperty("tone", "over" if drop else "none")
        self.drop_value.style().unpolish(self.drop_value)
        self.drop_value.style().polish(self.drop_value)
        self.new_box.setVisible(self._rich_cand() is not None)
        new = self.appeared()
        self.new_value.setText(f"{new:,}")
        self.new_value.setProperty("tone", "ok" if new else "none")
        self.new_value.style().unpolish(self.new_value)
        self.new_value.style().polish(self.new_value)
        self.keep_label.setText(_KO.THRESH_PREVIEW_KEPT.format(n=f"{n - drop:,}"))
        self.all_label.setText(_KO.THRESH_PREVIEW_ALL.format(n=f"{n:,}"))
        goods = sum(1 for d in w.goods if (d.zone_dir, d.zone) == self._zone)
        picks = sum(1 for d in self._picked.values() if (d.zone_dir, d.zone) == self._zone)
        counts = {"drop": drop, "keep": n - drop, "good": goods, "pick": picks}
        names = {"drop": _KO.THRESH_PREVIEW_TAB_DROP, "keep": _KO.THRESH_PREVIEW_TAB_KEEP,
                 "good": _KO.THRESH_PREVIEW_TAB_GOOD, "pick": _KO.OVERKILL_TAB_PICK}
        auto = self._mode == "auto"
        order = ("keep", "drop", "pick") if auto else ("drop", "keep", "good")
        shown = {k for k in order if k not in ("good", "pick") or counts[k] > 0 or (k == "pick" and auto)}
        if self._tab not in shown:
            self._tab = order[0]
        for i, k in enumerate(order):                # 자동 모드는 '남음' 이 앞 — 처음엔 전부 남아 있다
            self._tabs_lay.insertWidget(i, self.tab_btns[k])
        for k, b in self.tab_btns.items():
            b.setText(f"{names[k]}  {counts[k]:,}")
            b.setVisible(k in shown)
            b.setChecked(k == self._tab)
        total_picks = len(self._picked)
        missed = self.missed()
        self.pick_label.setText(_KO.OVERKILL_PICKED.format(n=f"{total_picks:,}") if total_picks else "")
        self.miss_label.setText(_KO.OVERKILL_MISSED.format(n=f"{len(missed):,}") if missed else "")
        return drop

    def _items(self) -> list:
        """지금 탭의 ``[(Defect, 빠진 이유 | None)]``."""
        w = self._w
        if self._tab == "good":
            return [(d, None) for d in w.goods if (d.zone_dir, d.zone) == self._zone]
        drop = {key_of(d): why for d, why in self.dropped()}
        if self._tab == "pick":
            return [(d, drop.get(key_of(d))) for d in self._in_zone() if key_of(d) in self._picked]
        if self._tab == "drop":                     # 기준 바로 아래부터 — 잡아야 할 것을 먼저 훑게
            return sorted(((d, drop[key_of(d)]) for d in self._in_zone() if key_of(d) in drop),
                          key=lambda t: -self._below(*t))
        return [(d, None) for d in self._in_zone() if key_of(d) not in drop]

    def _cols(self) -> int:
        avail = max(TILE_PX, self.grid_host.width() or self.width() - 300)
        return max(1, (avail + self.grid.spacing()) // (TILE_PX + 8 + self.grid.spacing()))

    def _render_tiles(self) -> None:
        self._clear(self.grid)
        self.tiles = {}
        w = self._w
        if w is None or w.note or self._zone is None:
            self.empty_label.setText("" if w is None else w.note)
            self.no_photo_label.setText("")
            return
        items = self._items()
        with_photo = [(d, why) for d, why in items if key_of(d) in w.photos]
        missing = len(items) - len(with_photo)
        self.no_photo_label.setText(_KO.THRESH_PREVIEW_NO_PHOTO.format(n=f"{missing:,}") if missing else "")
        self.empty_label.setText("" if items else (_KO.THRESH_PREVIEW_NONE_DROPPED
                                                   if self._tab == "drop" else _KO.THRESH_PREVIEW_NONE))
        cols = self._cols()
        shown = with_photo[:self._shown]
        r = c = 0
        pending = None
        auto = self._mode == "auto"
        for j, (d, why) in enumerate(shown):
            k = key_of(d)
            tile = _Tile(w.photos[k], self._class_name(d.bin), self._why_text(d, why), TILE_PX,
                         self.grid_host, picked=k in self._picked, detail_btn=auto)
            if auto:                                 # 자동 모드: 타일 = 꼭 잡기 토글, ⋯ = 값 펼치기
                tile.clicked.connect(lambda i=k: self.toggle_pick(i))
                tile.detail.connect(lambda i=k: self._toggle(i))
            else:
                tile.clicked.connect(lambda i=k: self._toggle(i))
            tile.set_open(self._open == k)
            self.grid.addWidget(tile, r, c)
            self.tiles[k] = tile
            if self._open == k:
                pending = self._detail(d, why)
            c += 1
            if c == cols or j == len(shown) - 1:
                r += 1
                c = 0
                if pending is not None:          # 펼친 값은 그 줄 바로 아래
                    self.grid.addWidget(pending, r, 0, 1, cols)
                    r += 1
                    pending = None
        if len(with_photo) > self._shown:
            more = NeonButton(_KO.THRESH_PREVIEW_MORE.format(n=f"{len(with_photo) - self._shown:,}"), role="ghost")
            more.clicked.connect(self._more)
            self.grid.addWidget(more, r, 0, 1, min(cols, 2))

    def _detail(self, d: P.Defect, why) -> QWidget:
        w = self._w
        box = QFrame(self.grid_host)
        box.setProperty("role", "card")
        box.setStyleSheet(f"QFrame[role='card'] {{ border: 2px solid {theme.ACCENT}; }}")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(16)
        pic = _BigPhoto(box)
        pic.setFixedSize(DETAIL_PX, DETAIL_PX)
        pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pic.setCursor(Qt.CursorShape.PointingHandCursor)
        photo = w.photos[key_of(d)]
        pic.setPixmap(image_io.load_thumb_qpixmap(photo, DETAIL_PX, kind="mid"))
        pic.clicked.connect(lambda: self._enlarge(photo))
        lay.addWidget(pic)
        right = QVBoxLayout()
        right.setSpacing(8)
        name, rec = zone_label(d.zone_dir, w.zones.get((d.zone_dir, d.zone)), d.zone)
        t = QLabel(f"{self._class_name(d.bin)}   ·  Zone {name}" + (f" · {rec}" if rec else "")
                   + (f"   ·  {w.folders[d.wafer].name}" if len(w.folders) > 1 else ""), box)
        t.setStyleSheet("font-weight: 700;")
        right.addWidget(t)
        g = QGridLayout()
        g.setHorizontalSpacing(24)
        g.setVerticalSpacing(6)
        for c, txt in enumerate(("", _KO.THRESH_PREVIEW_COL_THIS, _KO.THRESH_PREVIEW_COL_RECIPE,
                                 _KO.THRESH_PREVIEW_COL_NOW)):
            h = QLabel(txt, box)
            h.setProperty("role", "colHead")
            g.addWidget(h, 0, c)
        z = w.zones.get((d.zone_dir, d.zone))
        bundle = z.bundles.get(d.bin, ()) if z is not None else ()
        mono = f"font-family: {theme.FONT_MONO};"
        for r, (feat, fname, unit, _step, dec) in enumerate(_FEATURES, start=1):
            v = P._value(d, feat)
            f = next((x for x in bundle if x.feature == feat), None)
            bad = why is not None and why[1] == feat
            this = "—" if (feat == R.FEATURE_CONTRAST and v == 0) else f"{w.to_um(feat, v):.{dec}f} {unit}".strip()
            recv = "—" if f is None else f"{w.to_um(feat, f.low):.{dec}f}"
            now = "—" if f is None else f"{w.to_um(feat, self._ov.get((d.zone_dir, d.zone, d.bin, feat), f.low)):.{dec}f}"
            g.addWidget(QLabel(fname, box), r, 0)
            for c, txt in ((1, this), (2, recv), (3, now)):
                lab = QLabel(txt, box)
                style = mono + (f" color: {theme.DANGER}; font-weight: 700;" if bad and c != 2 else "")
                lab.setStyleSheet(style)
                g.addWidget(lab, r, c)
        right.addLayout(g)
        fn = QLabel(str(photo) if len(w.folders) > 1 else photo.name, box)
        fn.setProperty("role", "muted")
        fn.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right.addWidget(fn)
        right.addStretch(1)
        lay.addLayout(right, 1)
        return box

    # ------------------------------------------------------------------ 동작
    def _set_zone(self, key) -> None:
        self._zone = key
        if key in getattr(self, "zone_btns", {}):
            self.zone_btns[key].setChecked(True)
        self._open = None
        self._shown = PAGE_TILES
        self._render_rules()
        self._render_summary()
        self._render_tiles()

    def _set_tab(self, key: str) -> None:
        self._tab = key
        self._open = None
        self._shown = PAGE_TILES
        self._render_summary()
        self._render_tiles()

    def _toggle(self, index: int) -> None:
        self._open = None if self._open == index else index
        self._render_tiles()

    def _more(self) -> None:
        self._shown += PAGE_TILES * 2
        self._render_tiles()

    def _enlarge(self, photo: Path) -> None:
        from .zoom_window import FullscreenViewer
        sheets.run(FullscreenViewer(photo, self), full_bleed=True)

    def reset(self) -> None:
        """레시피 값으로 되돌리기 — 모든 Zone 의 바꾼 값과 꼭 잡기를 지운다."""
        self._ov = {}
        self._picked = {}
        self._rec_zones = set()
        self._open = None
        self._render()

    # ------------------------------------------------------------------ 자동 추천
    def set_mode(self, mode: str) -> None:
        """직접 조정 ↔ 자동 추천.  모드만 바꾼다 — 기준값은 그대로(추천값을 직접 조정으로 다듬을 수 있다)."""
        self._mode = mode
        self.mode_btns[mode].setChecked(True)
        self._tab = "keep" if mode == "auto" else "drop"
        self._open = None
        self._shown = PAGE_TILES
        self._render_summary()
        self._render_tiles()

    def toggle_pick(self, k: tuple) -> None:
        """꼭 잡을 Defect 를 더하거나 빼고, 고른 것을 전부 남기는 기준을 다시 추천한다."""
        if k in self._picked:
            del self._picked[k]
        else:
            d = next((x for x in self._w.defects if key_of(x) == k), None)
            if d is None:
                return
            self._picked[k] = d
        self._recommend()
        self._render_zones()
        self._render_rules()
        self._changed(True)

    def _recommend(self) -> None:
        """고른 것이 있는(또는 있던) Zone 만 다시 계산한다 — 다른 Zone 의 직접 조정은 그대로 둔다."""
        picked = list(self._picked.values())
        zones_now = {(d.zone_dir, d.zone) for d in picked}
        for k in [k for k in self._ov if k[:2] in zones_now | self._rec_zones]:
            del self._ov[k]
        rec, _missed = P.recommend(picked, self._w.zones, self._snap)
        self._ov.update(rec)
        self._rec_zones = zones_now

    def _snap(self, feature: int, v: float, strict: bool) -> float:
        """px 값 → 화면 소수 자리로 **내린** µm 를 다시 px 로.  그 값 이하(엄격이면 미만)를 보장한다."""
        dec = next((x[4] for x in _FEATURES if x[0] == feature), 2)
        q = 10 ** dec
        um = self._w.to_um(feature, v)
        cut = math.floor(um * q) / q
        if strict and cut >= um:
            cut -= 1 / q
        top = math.nextafter(v, -math.inf) if strict else v
        return min(self._w.to_px(feature, cut), top)

    def missed(self) -> list:
        """고른 Defect 중 지금 기준으로 빠지는 것 — 직접 조정에서 너무 올리면 생긴다."""
        if self._w is None:
            return []
        return [d for d in self._picked.values() if not P.is_kept(d, self._w.zones, self._ov)]

    # ------------------------------------------------------------------ 내보내기
    def _on_export(self) -> None:
        """저장 창은 ``open()`` 으로 — ``exec()`` 는 Windows 에서 UI 스레드를 세운다(:mod:`..folder_picker`)."""
        if not self._ov or self._w is None:
            return
        home = Path.home()
        start = home / "Desktop" if (home / "Desktop").is_dir() else home
        base = self._roots[0].name if len(self._roots) == 1 else self._w.folder.name
        name = f"Overkill_{base}_{datetime.now():%Y%m%d_%H%M}.html"
        dlg = QFileDialog(self, _KO.THRESH_EXPORT_SAVE_TITLE, str(start / name), _KO.THRESH_EXPORT_FILTER)
        dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dlg.setDefaultSuffix("html")
        dlg.fileSelected.connect(self._export_to)
        dlg.finished.connect(dlg.deleteLater)
        self._save_dlg = dlg
        dlg.open()

    def _export_to(self, path: str) -> None:
        if not path:
            return
        try:
            self.export_html(Path(path))
        except OSError as exc:
            _LOG.warning("Threshold HTML 저장 실패: %s", exc)
            self.check_label.setText(_KO.THRESH_EXPORT_FAILED.format(e=exc))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def export_html(self, path: Path) -> None:
        """바꾼 값 · 새로 빠지는 수 · 분포 · Zone 마다 대표 사진 :data:`EXPORT_SAMPLES` 장을 HTML 한 장에."""
        w = self._w
        changes = []
        for (zdir, zid, cls, feat), px in sorted(self._ov.items()):
            z = w.zones.get((zdir, zid))
            if z is None or cls not in z.bundles:
                continue
            f = next((x for x in z.bundles[cls] if x.feature == feat), None)
            meta = next((x for x in _FEATURES if x[0] == feat), None)
            if f is None or meta is None:
                continue
            _feat, item, unit, _step, dec = meta
            name, rec = zone_label(zdir, z, zid)
            n = list(z.bundles).index(cls) + 1
            rule = _KO.THRESH_PREVIEW_RULE_N.format(n=n) if len(z.bundles) > 1 else _KO.THRESH_PREVIEW_RULE
            vals = [w.to_um(feat, P._value(d, feat)) for d in w.defects
                    if (d.zone_dir, d.zone, d.bin) == (zdir, zid, cls)
                    and not (feat == R.FEATURE_CONTRAST and P._value(d, feat) == 0)]
            changes.append(RP.Change(name + (f" · {rec}" if rec else ""), f"{rule} → {self._class_name(cls)}",
                                     item, unit, w.to_um(feat, f.low), w.to_um(feat, px), dec, tuple(vals)))
        zones = []
        total_drop = 0
        total_new = 0
        for key in self.zone_keys():
            drops = self.dropped(key)
            new = self.appeared(key)
            if not drops and not new:
                continue
            total_drop += len(drops)
            total_new += new
            name, rec = zone_label(key[0], w.zones.get(key), key[1])
            have = sorted(((d, why) for d, why in drops if key_of(d) in w.photos),
                          key=lambda t: -self._below(t[0], t[1]))     # 기준 바로 아래부터 멀리까지
            samples = tuple(RP.Sample(_data_uri(w.photos[key_of(d)]), self._class_name(d.bin), self._why_text(d, why))
                            for d, why in RP.pick_samples(have, EXPORT_SAMPLES))
            zones.append(RP.ZoneResult(name + (f" · {rec}" if rec else ""), self._count(key), len(drops),
                                       samples, len(drops) - len(have), new))
        where = (str(w.folder) if len(w.folders) <= 1 else
                 f"{w.folder.parent}  ·  " + ", ".join(f.name for f in w.folders))
        text = RP.build_html(folder=where, when=f"{datetime.now():%Y-%m-%d %H:%M}",
                             total=len(w.defects), dropped=total_drop, changes=changes, zones=zones,
                             appeared=total_new)
        Path(path).write_text(text, encoding="utf-8")

    def _below(self, d: P.Defect, why) -> float:
        """걸린 값 ÷ 지금 기준 — 항목(Width · Contrast)이 달라도 '기준에서 얼마나 아래' 로 비교한다."""
        cls, feat = why
        z = self._w.zones.get((d.zone_dir, d.zone))
        f = next((x for x in z.bundles.get(cls, ()) if x.feature == feat), None) if z else None
        cut = self._ov.get((d.zone_dir, d.zone, cls, feat), f.low if f else 0.0)
        return P._value(d, feat) / cut if cut > 0 else 0.0

    def set_value(self, cls: int, feature: int, um: float) -> None:
        """테스트용 — 지금 Zone 의 슬라이더 칸에 µm 값을 넣는다(사용자가 숫자를 친 것과 같다)."""
        edit = self.sliders[(cls, feature)][1]
        edit.setText(str(um))
        edit.editingFinished.emit()

    def _class_name(self, n: int) -> str:
        c = self._w.classes.get(n) if self._w else None
        return c.name if c else f"class {n}"

    def _why_text(self, d: P.Defect, why) -> str:
        if why is None:
            return ""
        feat = why[1]
        dec = next(x[4] for x in _FEATURES if x[0] == feat)
        return f"{next(x[1] for x in _FEATURES if x[0] == feat)[0]} {self._w.to_um(feat, P._value(d, feat)):.{dec}f}"

    def resizeEvent(self, e):       # noqa: N802
        super().resizeEvent(e)
        if self._w is not None and self.grid.count():
            cols = self._cols()
            if cols != getattr(self, "_last_cols", cols):
                self._render_tiles()
            self._last_cols = cols

    def closeEvent(self, e):        # noqa: N802
        if self.is_loading():
            self._thread.requestInterruption()
        super().closeEvent(e)
