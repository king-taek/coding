"""Overkill 튜닝(옛 이름 Threshold 미리보기) — Zone 기준을 올리면 Defect 가 몇 개 빠지는지 본다.

현택님 요청(2026-11-16):
  * 폴더는 웨이퍼 폴더 또는 LOT 폴더(바로 아래 웨이퍼들), 여러 개를 [+ 폴더] · 끌어다 놓기로 더한다.
    **처음 고른 폴더의 레시피**(Zone 기준 · 픽셀 크기)와 같은 웨이퍼만 합친다(:func:`merge_wafers`) —
    더한 폴더 때문에 기준이 뒤바뀌어 조정한 값이 사라지지 않게(현택님 2026-11-16).
  * 폴더 칩(현택님 2026-11-16): 이름을 누르면 그 폴더 Defect 만 아래 사진에 보이고(값 계산은 전체 그대로),
    오른쪽 ✕ 로 뺀다(5초 동안 [되돌리기]).  칩마다 Defect 수와 [컬러 | 스캔] 을 둔다.  빼기는 메모리에서 다시
    합치기만 하고(읽지 않는다), 더하기는 화면을 막지 않고 칩 안에 진행을 보인다.  자리가 모자라면 '+n 더 보기'.
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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (QBuffer, QIODevice, QLocale, QObject, QPoint, QRectF, Qt, QThread, QTimer, QUrl,
                          pyqtSignal)
from PyQt6.QtGui import (QColor, QDesktopServices, QDoubleValidator, QImage, QPainter, QPen, QPixmap)
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QDialog, QFileDialog, QFrame, QGridLayout,
                             QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QVBoxLayout,
                             QWidget)

from ... import i18n
from ...config import CONFIG
from ...coords import abs_coord
from ...coords.geometry import GEOMETRY_MATCH_TOL
from ...coords import scan_image as SI
from ...coords import surface_flt as _flt
from ...coords.pixel_size import scan_pixel_size_xy
from ...models.slot import _list_images, list_slot_dirs
from ...threshold import preview as P
from ...threshold import report as RP
from ...threshold import rich as RS
from ...threshold import rules as R
from ...utils import image_io
from .. import theme
from ..drop_paths import install_paste, local_paths, split_paths
from ..folder_picker import FolderPicker
from . import sheet_host as sheets
from .loading_overlay import LoadingOverlay
from .neon_button import NeonButton
from .no_wheel_slider import NoWheelSlider

_LOG = logging.getLogger("aoi.threshold")
_KO = i18n.KO

LOAD_WORKERS = 6            # 여러 웨이퍼를 동시에 읽는 수(NAS 왕복 지연을 겹친다)
LIVE_TILES_MAX = 100        # 이 수 이하일 때만 슬라이더를 끄는 동안 사진을 바로 바꾼다(현택님 2026-11-14)
PAGE_TILES = 60             # 처음 보이는 사진 수 — 나머지는 [더 보기]
TILE_PX = 132
DETAIL_PX = 240
EXPORT_SAMPLES = 8          # HTML 내보내기 — Zone 마다 대표 사진 수
EXPORT_PHOTO_PX = 200
NOTICE_MS = 5000            # 뺐다 · 더했다 알림이 떠 있는 시간(되돌리기도 이 안에)
FLASH_MS = 1500             # 더한 칩 · 이미 있는 칩이 반짝이는 시간
MORE_CHIP_PX = 110          # '+n 더 보기' 칩이 차지할 자리
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
    skipped: tuple = ()               # 못 합친 웨이퍼 폴더 — 못 읽었거나 Zone 기준 · 픽셀 크기가 기준 웨이퍼와 다름
    has_scan: bool = False            # 폴더에 Scan 목록이 있다 — 칩의 [스캔] 을 켤 수 있다

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
    t0 = time.perf_counter()
    defects, goods, note = P.load_all(folder)
    t1 = time.perf_counter()
    w = WaferData(folder, defects, goods, note=note, has_scan=(folder / SI.SCAN_LIST_NAME).is_file())
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
    _LOG.info("Overkill 튜닝 읽기 %s: Defect %d개 %.2f초 · 사진 %d장 짝 %.2f초", folder.name, len(defects),
              t1 - t0, len(photos), time.perf_counter() - t1)
    if rich and (folder / RS.RICH_FILE).is_file():
        report(_KO.THRESH_PREVIEW_RICH)
        w.rich = RS.load_rich(folder, lambda done, total: report(_KO.THRESH_PREVIEW_RICH, done, total), stop)
    return w


def expand_root(p) -> list:
    """고른 폴더 하나 → ``[(웨이퍼 폴더, 실제 경로 키)]``.  Surface.flt 가 있으면 그 폴더, 없으면 바로 아래에서
    Surface.flt 가 든 폴더들(LOT 폴더).  실제 경로 키는 LOT 과 그 안의 웨이퍼를 함께 골라도 한 번만 세려는 것이다."""
    p = Path(p)
    found = [p] if _flt._find_flt(p) is not None else [
        d for _n, d in sorted(list_slot_dirs(p).items()) if _flt._find_flt(d) is not None]
    return [(d, os.path.normcase(str(d.resolve()))) for d in found]


def wafer_folders(paths) -> list:
    """고른 폴더들 → 웨이퍼 폴더 목록.  같은 폴더는 한 번만."""
    out: list = []
    seen: set = set()
    for p in paths:
        for d, real in expand_root(p):
            if real not in seen:
                seen.add(real)
                out.append(d)
    return out


def key_of(d: P.Defect) -> tuple:
    """Defect 의 키 — 여러 웨이퍼를 합치면 index 가 겹치므로 (웨이퍼, index)."""
    return d.wafer, d.index


def _folder_of(w: WaferData, d: P.Defect) -> Path:
    """그 Defect 가 나온 웨이퍼 폴더."""
    return w.folders[d.wafer] if w.folders else w.folder


def merge_wafers(ws: list) -> WaferData:
    """웨이퍼 여러 장을 한 집합으로 — **첫 웨이퍼(읽힌 것 중)의 레시피**(Zone 기준 · 픽셀 크기)와 같은 것만 합친다.

    기준이 다르면(레시피가 다르면) 같은 슬라이더 값으로 셀 수 없어 빼고 ``skipped`` 에 적는다.  기준을 '가장 많은 무리'
    로 잡으면 다른 레시피 폴더를 더하는 순간 기준이 뒤바뀌어 조정한 값이 사라져서, 처음 고른 쪽으로 고정한다.
    Defect 에는 몇 번째 웨이퍼인지(``wafer``)를 붙이고 사진 짝 키도 (웨이퍼, index) 로 바꾼다.
    RichSurface(내리기)는 한 장일 때만 쓴다."""
    good = [w for w in ws if not w.note]
    if not good:
        return WaferData(ws[0].folder, note=ws[0].note, skipped=tuple(w.folder for w in ws))
    base = good[0]
    same = [w for w in good if w.zones == base.zones and all(abs(a - b) < 1e-6 for a, b in zip(base.px, w.px))]
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
    """고른 폴더들을 읽는다 — LOT 안 웨이퍼 목록 · 웨이퍼 Defect 를 하나씩 읽어 시그널로 올린다(합치기는 화면이 한다).

    이미 읽은 것(``expand``·``cache`` 에 있는 것)은 다시 읽지 않는다.  끊겨도 다 읽은 웨이퍼는 올린다."""

    class _Signals(QObject):
        progress = pyqtSignal(int, int, int, str)     # token, done, total, msg
        expanded = pyqtSignal(int, object, object)    # token, 고른 폴더, [(웨이퍼 폴더, 실제 경로 키)]
        wafer = pyqtSignal(int, object, object)       # token, 웨이퍼 폴더, WaferData
        done = pyqtSignal(int, bool)                  # token, 성공

    def __init__(self, token: int, roots: list, expand: dict, known: set) -> None:
        super().__init__()
        self._token, self._roots = token, list(roots)
        self._expand = dict(expand)       # 고른 폴더 → 웨이퍼 목록 — LOT 폴더를 NAS 에서 다시 훑지 않는다
        self._known = set(known)          # 이미 읽은 웨이퍼 폴더
        self.signals = self._Signals()
        _LIVE.add(self)
        self.finished.connect(lambda: _LIVE.discard(self))

    def run(self) -> None:      # type: ignore[override]
        t = self._token
        stop = self.isInterruptionRequested
        try:
            order: list = []
            seen: set = set()
            for r in self._roots:
                if r not in self._expand:
                    self._expand[r] = expand_root(r)
                    self.signals.expanded.emit(t, r, self._expand[r])
                for d, real in self._expand[r]:
                    if real not in seen:
                        seen.add(real)
                        order.append(d)
            todo = [f for f in order if f not in self._known]
            if len(todo) == 1:
                got = load_wafer(todo[0], lambda msg, done=0, total=0:
                                 self.signals.progress.emit(t, done, total, msg), stop, rich=False)
                self.signals.wafer.emit(t, todo[0], got)
            elif todo:
                # 여러 장은 동시에 읽는다 — NAS 는 파일마다 왕복 지연이 대부분이라 한 장씩이면 그만큼 기다린다.
                self.signals.progress.emit(t, 0, len(todo), _KO.OVERKILL_LOADING_WAFERS.format(i=0, n=len(todo)))
                with ThreadPoolExecutor(max_workers=min(LOAD_WORKERS, len(todo))) as pool:
                    futs = {pool.submit(load_wafer, f, stop=stop, rich=False): f for f in todo}
                    cancelled = False
                    for i, fut in enumerate(as_completed(futs), start=1):
                        if stop() and not cancelled:
                            cancelled = True
                            for f in futs:
                                f.cancel()      # 아직 시작 안 한 것만 멈춘다 — 이미 다 읽은 웨이퍼는 아래에서 올린다
                        if fut.cancelled():
                            continue
                        self.signals.wafer.emit(t, futs[fut], fut.result())
                        self.signals.progress.emit(t, i, len(todo),
                                                   _KO.OVERKILL_LOADING_WAFERS.format(i=i, n=len(todo)))
            if stop():
                return
        except Exception:       # noqa: BLE001 — 실패를 삼키지 않고 화면에 알린다
            _LOG.exception("Overkill 튜닝 읽기 실패: %s", self._roots)
            self.signals.done.emit(t, False)
            return
        self.signals.done.emit(t, True)


class _RichLoad(QThread):
    """웨이퍼 한 장의 RichSurface(내리기용)를 화면을 띄운 뒤 뒤에서 읽는다."""

    class _Signals(QObject):
        progress = pyqtSignal(int, int, int)          # token, done, total
        done = pyqtSignal(int, object, object)        # token, 웨이퍼 폴더, 후보 값({} = 없음)

    def __init__(self, token: int, folder: Path) -> None:
        super().__init__()
        self._token, self._folder = token, folder
        self.signals = self._Signals()
        _LIVE.add(self)
        self.finished.connect(lambda: _LIVE.discard(self))

    def run(self) -> None:      # type: ignore[override]
        t0 = time.perf_counter()
        try:
            rich = RS.load_rich(self._folder, lambda done, total: self.signals.progress.emit(self._token, done, total),
                                self.isInterruptionRequested)
        except Exception:       # noqa: BLE001 — 내리기만 못 쓸 뿐
            _LOG.exception("Overkill 튜닝 RichSurface 읽기 실패: %s", self._folder)
            return
        if self.isInterruptionRequested():
            return
        _LOG.info("Overkill 튜닝 RichSurface %.1f초: %s", time.perf_counter() - t0, self._folder)
        self.signals.done.emit(self._token, self._folder, rich)


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

    def __init__(self, photo, caption: str, value: str, size: int, parent=None,
                 picked: bool = False, detail_btn: bool = False) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)   # 덜 찬 줄에서 가로로 늘어나지 않게
        self.picked = picked
        self.wait_path = None                          # 썸네일을 아직 기다리는 사진 경로(백그라운드가 채운다)
        self._open = False
        self._hover = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)             # 틀 없음 — 사진만, 캡션은 그 아래
        lay.setSpacing(4)
        pic = QLabel(self)
        pic.setObjectName("tilePic")
        pic.setFixedSize(size + 4, size + 4)           # 사진 둘레 2px 는 고른 상태·가리킴 표시 자리
        pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pic, self._size = pic, size
        self._style()
        lay.addWidget(pic)
        self.set_photo(photo)
        self.badge = QLabel("✓", pic)                  # 꼭 잡기 표시
        self.badge.setFixedSize(24, 24)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setStyleSheet(f"background: {theme.ACCENT}; color: {theme.ON_ACCENT}; border-radius: 12px;"
                                 " font-weight: 700;")
        self.badge.move(8, 8)
        self.badge.setVisible(picked)
        self.detail_btn = None
        if detail_btn:
            self.detail_btn = QLabel("⋯", pic)          # 누르면 값 펼치기 — 타일 본체 누름(고르기)과 따로
            self.detail_btn.setFixedSize(26, 22)
            self.detail_btn.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.detail_btn.setToolTip(_KO.OVERKILL_DETAIL_TIP)
            self.detail_btn.setStyleSheet(f"background: {theme.PANEL}; color: {theme.INK}; border-radius: 4px;"
                                          " font-weight: 700;")
            self.detail_btn.move(size - 30, 8)
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

    def set_photo(self, photo) -> None:
        """그림을 올린다.  ``None`` = Scan Crop 을 아직 자르는 중(자리표시)."""
        pic, size = self.pic, self._size
        if photo is None:
            pic.setText(_KO.OVERKILL_SCAN_WAIT)
            return
        if not isinstance(photo, QImage):               # Color 사진 — 캐시에 있으면 바로, 없으면 자리표시 뒤 백그라운드가 채운다
            pm = image_io._get_tile_cache().get(_tile_key(photo, size))
            if pm is not None:
                pic.setPixmap(pm)
            else:
                self.wait_path = photo
            return
        pic.setPixmap(_pixmap(photo, size))
        if isinstance(photo, QImage):                   # Color 사진이 없어 Scan 사진에서 잘라 온 것
            tag = QLabel(_KO.OVERKILL_SCAN_TAG, pic)
            tag.setStyleSheet(f"background: {theme.PANEL}; color: {theme.INK}; border-radius: 4px;"
                              " padding: 1px 5px; font-size: 11px; font-weight: 700;")
            tag.adjustSize()
            tag.move(8, size - tag.height() - 4)
            tag.show()

    def set_pixmap(self, pm: QPixmap) -> None:
        self.wait_path = None
        self.pic.setPixmap(pm)

    def _style(self) -> None:
        """틀 대신 사진 둘레 2px 만 쓴다 — 고름·펼침 = 강조색, 가리킴 = 옅은 선, 평소 = 투명."""
        c = theme.ACCENT if (self._open or self.picked) else theme.LINE_STRONG if self._hover else "transparent"
        self.pic.setStyleSheet(f"QLabel#tilePic {{ background: {theme.PANEL}; border: 2px solid {c}; }}")

    def set_open(self, on: bool) -> None:
        self._open = on
        self._style()

    def enterEvent(self, e):        # noqa: N802
        self._hover = True
        self._style()
        super().enterEvent(e)

    def leaveEvent(self, e):        # noqa: N802
        self._hover = False
        self._style()
        super().leaveEvent(e)

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


def _tile_key(photo, size: int) -> tuple:
    """``image_io.cached_tile_pixmap`` 과 같은 캐시 키 — 매치 검토와 같은 LRU 를 나눠 쓴다."""
    return (str(photo), int(size), False, False)


def _mid_key(photo, size: int) -> tuple:
    """``image_io.load_thumb_qpixmap(kind="mid")`` 와 같은 캐시 키."""
    return ("thumbq", str(photo), int(size), "mid")


class _ThumbBus(QObject):
    """백그라운드 스레드 → GUI 스레드 전달 통로.  QPixmap 은 GUI 스레드에서만 만든다."""

    ready = pyqtSignal(object, int, str, object)       # 경로 · 크기 · 'thumb'|'mid' · QImage


def _pixmap(photo, size: int, kind: str = "") -> QPixmap:
    """Color 사진 경로 또는 Scan 에서 잘라 온 ``QImage`` → ``size`` 안에 맞춘 그림."""
    if isinstance(photo, QImage):
        return QPixmap.fromImage(photo).scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation)
    return image_io.load_thumb_qpixmap(photo, size, kind=kind) if kind else image_io.cached_tile_pixmap(photo, size)


THUMB_WORKERS = 3           # 썸네일을 동시에 몇 장 만들까(NAS 에서 원본을 읽는 일이라 GIL 을 놓는다)
SCAN_WORKERS = 5            # Scan 원본을 동시에 몇 개 디코드할까 — NAS 지연이 커서 나눠 읽으면 그만큼 줄고, 디코드(PIL)는 GIL 을 놓는다
SCAN_BATCH = 16             # 잘라 낸 Crop 을 이만큼 모일 때마다 화면에 넘긴다


def group_by_source(found) -> dict:
    """``[(키, ScanMatch)]`` → ``{원본 경로: [(키, ScanMatch)]}`` — 같은 Scan 은 한 번만 디코드하려고 묶는다.
    들어온 순서(= 화면에서 보일 순서)를 지킨다: 묶음은 그 원본이 처음 나온 자리 순, 묶음 안도 들어온 순."""
    groups: dict = {}
    for k, m in found:
        groups.setdefault(str(m.best.path), []).append((k, m))
    return groups


def _to_qimage(im) -> QImage:
    im = im.convert("RGB")
    return QImage(im.tobytes(), im.width, im.height, 3 * im.width, QImage.Format.Format_RGB888).copy()


def scan_crops(items, report=lambda done, total: None, stop=lambda: False, on_batch=None) -> dict:
    """Color 사진이 없는 Defect 들 → ``{키: QImage}`` — 같은 폴더 Scan(``.t.``) 사진의 300 µm Crop.

    ``items`` = ``[(키, 웨이퍼 폴더, (x, y) µm)]``.  계산은 사진 정보 화면 · 엑셀과 같은
    :mod:`coords.scan_image` 가 한다(Defect ActualX/Y 는 Color 사진 좌표와 같은 틀 — FS UBM 14/14 일치, 관측).
    같은 Scan 원본을 **한 번만** 디코드해 그 원본의 Crop 을 전부 자르고, 원본들은 스레드 여럿이 나눠 읽는다.
    ``on_batch(dict)`` 를 주면 Crop 이 ``SCAN_BATCH`` 개 모일 때마다(스레드에서) 불러 준다 — 화면이 점진적으로 채워진다.
    못 자른 Defect 는 빠진다."""
    found = []
    for k, folder, xy in items:
        if stop():
            return {}
        m = SI.resolve_xy(folder, xy)
        if m.ok:
            found.append((k, m))
    groups = group_by_source(found)
    total = len(found)
    out: dict = {}
    lock = threading.Lock()
    state = {"done": 0}
    buf: dict = {}

    def flush(force: bool = False) -> None:
        if buf and (force or len(buf) >= SCAN_BATCH):
            chunk = dict(buf)
            buf.clear()
            if on_batch is not None:
                on_batch(chunk)

    def one(group: list) -> None:
        if stop():
            return
        crops = SI.crops_from(group[0][1].best.path, [m.best.box for _k, m in group])
        for i, (k, m) in enumerate(group):
            if stop():
                return
            if crops is not None:
                crop = crops[i]
            else:                                    # 최상위 후보를 못 읽음 — 다음 후보로(load_crop 규약)
                got = SI.load_crop(m)
                crop = got[0] if got is not None else None
            q = _to_qimage(crop) if crop is not None else None
            with lock:
                if q is not None:
                    out[k] = q
                    buf[k] = q
                state["done"] += 1
                flush()
                report(state["done"], total)

    with ThreadPoolExecutor(max_workers=max(1, min(SCAN_WORKERS, len(groups) or 1))) as pool:
        futs = [pool.submit(one, g) for g in groups.values()]
        for f in futs:
            try:
                f.result()
            except Exception:       # noqa: BLE001 — 한 원본 실패가 나머지를 막지 않게
                _LOG.exception("Overkill 튜닝 Scan 사진 자르기 실패")
        if stop():
            for f in futs:
                f.cancel()
    if stop():
        return {}
    with lock:
        flush(True)
        report(total, total)
    return out


class _ScanLoad(QThread):
    class _Signals(QObject):
        progress = pyqtSignal(int, int, int)           # token, done, total
        batch = pyqtSignal(int, object)                # token, {키: QImage} — 일부
        done = pyqtSignal(int, object)                 # token, 시도한 키 목록(못 자른 것 포함)

    def __init__(self, token: int, items: list) -> None:
        super().__init__()
        self._token, self._items = token, items
        self.signals = self._Signals()
        _LIVE.add(self)
        self.finished.connect(lambda: _LIVE.discard(self))

    def run(self) -> None:      # type: ignore[override]
        try:
            scan_crops(self._items, lambda d, t: self.signals.progress.emit(self._token, d, t),
                       self.isInterruptionRequested,
                       on_batch=lambda b: self.signals.batch.emit(self._token, b))
        except Exception:       # noqa: BLE001 — 실패를 삼키지 않고 로그에 남긴다(사진만 못 보일 뿐)
            _LOG.exception("Overkill 튜닝 Scan 사진 자르기 실패")
        self.signals.done.emit(self._token, [k for k, _f, _xy in self._items])


DEFECT_BOX_MULT = 3.0          # 상세 보기 빨간 박스 = Defect 크기의 이만큼(현택님 2026-11-16)
DEFECT_BOX_MIN_UM = 40.0       # 아주 작은 Defect 도 이보다는 넓게 — 가리지 않고 찾을 수 있게
DEFECT_BOX_RGB = "#E5322D"     # 회색조 Scan 위에서 보이는 고정 빨강(테마 DANGER 는 어두워서 사진 위에서 묻힌다)


def defect_box(crop_wh, px, width_px: float, length_px: float, area_px2: float = 0.0):
    """Scan Crop 안에서 Defect 를 감쌀 정사각 ``(x, y, w, h)``(Crop 픽셀) — 못 구하면 ``None``.

    Crop 은 Defect 좌표가 가운데 오게 자른다(:func:`coords.scan_image.crop_box` — 원본 밖은 안쪽으로 밀지 않고
    회색으로 채우므로 가장자리 Defect 도 가운데다, 반올림 오차 1px 이내).  한 변 = Defect 크기(폭 · 길이 중 큰 쪽,
    둘 다 없으면 √면적) × :data:`DEFECT_BOX_MULT`, 최소 :data:`DEFECT_BOX_MIN_UM` µm, 최대 Crop 한 변.
    ``px`` 는 Surface/Color 픽셀 크기 — Defect 크기(폭 · 길이 · 면적, Surface 픽셀) → µm 에만 쓴다.
    µm → Crop 픽셀은 **Scan** 픽셀 크기로 한다: Crop 은 ``CROP_SIDE_UM`` 을 ``crop.width()`` 픽셀에 담으므로
    Scan µm/px = ``CROP_SIDE_UM / 폭``(높이도 같은 식).  두 픽셀 크기는 다른 장비의 것이라 섞으면 박스가 틀린다."""
    (cw, ch), (px_x, px_y) = crop_wh, px
    if not (cw > 0 and ch > 0 and px_x > 0 and px_y > 0):
        return None
    size_um = max(width_px, length_px) * px_x
    if size_um <= 0:
        size_um = math.sqrt(max(area_px2, 0.0) * px_x * px_y)
    side_um = min(max(size_um * DEFECT_BOX_MULT, DEFECT_BOX_MIN_UM), SI.CROP_SIDE_UM)
    bw, bh = side_um * cw / SI.CROP_SIDE_UM, side_um * ch / SI.CROP_SIDE_UM
    return (cw / 2.0 - bw / 2.0, ch / 2.0 - bh / 2.0, bw, bh)


@dataclass
class _ChipInfo:
    """폴더 칩 하나를 그리는 데 필요한 것(화면 상태 → 그림)."""
    root: Path
    name: str
    state: str = "ok"             # ok | loading | excluded
    count: int = 0                # 합친 Defect 수
    wafers: int = 0               # 합친 웨이퍼 수
    lot: bool = False
    partial: int = 0              # LOT 안에서 못 합친 웨이퍼 수
    progress: str = ""            # loading — '읽는 중 i/n'
    reason: str = ""              # excluded — '제외: …'
    can_color: bool = False
    can_scan: bool = False
    view: str = "color"
    selected: bool = False
    flash: str = ""               # "" | added | dup
    tip: str = ""


class _FolderChip(QFrame):
    """폴더 칩 — 이름 영역을 누르면 ``select``, 오른쪽 ✕ 칸은 ``remove``, [컬러 | 스캔] 은 ``view``.

    칩 전체가 클릭 영역이고(CLAUDE.md UI 관습) 빼기는 따로 떨어진 ✕ 칸 하나뿐이라 고르다가 빠지지 않는다."""

    select = pyqtSignal()
    remove = pyqtSignal()
    view = pyqtSignal(str)

    NAME_MAX_PX = 190

    def __init__(self, info: _ChipInfo, parent=None) -> None:
        super().__init__(parent)
        self.info = info
        self.setObjectName("folderChip")
        self.setToolTip(info.tip)
        self.setFixedHeight(34)
        self._style()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 0, 0)
        lay.setSpacing(8)
        title = info.name + (f" · {_KO.OVERKILL_WAFERS.format(n=info.wafers)}" if info.lot and info.state == "ok" else "")
        name = QLabel(self.fontMetrics().elidedText(title, Qt.TextElideMode.ElideMiddle, self.NAME_MAX_PX), self)
        name.setStyleSheet("font-weight: 600; color: "
                           f"{theme.MUTE if info.state == 'excluded' else theme.ACCENT if info.selected else theme.INK};")
        lay.addWidget(name)
        side = {"ok": f"{info.count:,}" + (f" · {_KO.OVERKILL_CHIP_PARTIAL.format(n=info.partial)}" if info.partial else ""),
                "loading": info.progress, "excluded": info.reason}[info.state]
        self.side = QLabel(side, self)
        self.side.setStyleSheet(f"font-family: {theme.FONT_MONO}; color: "
                                f"{theme.WARN if info.state == 'excluded' or info.partial else theme.MUTE};")
        lay.addWidget(self.side)
        self.seg: dict = {}
        if info.state == "ok":
            group = QButtonGroup(self)
            for key, text, can in (("color", _KO.OVERKILL_VIEW_COLOR, info.can_color),
                                   ("scan", _KO.OVERKILL_VIEW_SCAN, info.can_scan)):
                b = NeonButton(text, role="segment")
                b.setCheckable(True)
                b.setChecked(info.view == key)
                b.setEnabled(can)
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                b.setMinimumHeight(22)
                b.setStyleSheet("QPushButton { padding: 0px 7px; min-height: 18px; font-size: 11px; }")
                if not can:
                    b.setToolTip(_KO.OVERKILL_VIEW_NO_COLOR if key == "color" else _KO.OVERKILL_VIEW_NO_SCAN)
                b.clicked.connect(lambda _c=False, k=key: self.view.emit(k))
                group.addButton(b)
                lay.addWidget(b)
                self.seg[key] = b
        self.x_btn = NeonButton("✕", role="ghost")           # 빼기 — 이름 영역과 떨어진 칸
        self.x_btn.setToolTip(_KO.OVERKILL_CHIP_REMOVE_TIP)
        self.x_btn.setFixedSize(30, 28)
        self.x_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.x_btn.setStyleSheet(
            f"QPushButton {{ border: none; border-left: 1px solid {theme.LINE}; border-radius: 0px; padding: 0px;"
            f" min-height: 0px; background: transparent; color: {theme.INK2}; }}"
            f"QPushButton:hover {{ background: {theme.DANGER_TINT}; color: {theme.DANGER}; }}")
        self.x_btn.clicked.connect(self.remove)
        lay.addWidget(self.x_btn)
        if info.state == "ok":
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _style(self) -> None:
        """평상시는 1px 선, 고름 · 반짝임은 2px 선 — 안쪽 여백(margin)으로 맞춰 칩 크기는 그대로다."""
        i = self.info
        if i.flash == "added":
            border, bg, line, bw = theme.PASS, theme.PASS_TINT, "solid", 2
        elif i.flash == "dup" or i.selected:
            border, bg, line, bw = theme.ACCENT, theme.ACCENT_TINT, "solid", 2
        elif i.state == "excluded":
            border, bg, line, bw = theme.LINE_STRONG, "transparent", "dashed", 1
        else:
            border, bg, line, bw = theme.LINE_STRONG, theme.PANEL, "solid", 1
        self.setStyleSheet(f"QFrame#folderChip {{ background: {bg}; border: {bw}px {line} {border};"
                           f" margin: {2 - bw}px; border-radius: 6px; }}"
                           "QFrame#folderChip QLabel { background: transparent; border: none; margin: 0px; }")

    def mousePressEvent(self, e):   # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.info.state == "ok":
            self.select.emit()
        super().mousePressEvent(e)


def _data_uri(photo) -> str:
    """사진 → 작은 JPEG ``data:`` 주소(HTML 안에 넣는다).  못 읽으면 빈 문자열."""
    pix = _pixmap(photo, EXPORT_PHOTO_PX, kind="mid")
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
        install_paste(self, self._add_dropped, self._on_paste_no_path)
        self._w: Optional[WaferData] = None
        self._roots: list = []            # 고른 폴더(웨이퍼 또는 LOT) — 칩 하나씩.  맨 앞이 합치기 기준 레시피를 정한다
        self._expand: dict = {}           # 고른 폴더 → [(웨이퍼 폴더, 실제 경로 키)] — LOT 을 NAS 에서 다시 훑지 않는다
        self._cache: dict = {}            # 웨이퍼 폴더 → WaferData — 빼기 · 더하기가 다시 읽지 않고 이걸로 합친다
        self._owner: dict = {}            # 웨이퍼 폴더 → 그걸 가진 칩(같은 웨이퍼를 두 칩이 가리켜도 앞 칩 하나만)
        self._order: list = []            # 합칠 웨이퍼 폴더(칩 순서)
        self._real_owner: dict = {}       # 실제 경로 키 → 칩
        self._failed: set = set()         # 읽다가 실패한 고른 폴더
        self._view: dict = {}             # 고른 폴더 → 'color' | 'scan' — 폴더를 빼고 다시 더해도 이 화면을 닫을 때까지 남는다
        self._sel: Optional[Path] = None  # 사진 칸을 이 칩의 Defect 로만 거른다(값 계산은 전체 그대로)
        self._added: list = []            # 방금 더해 아직 알리지 않은 칩
        self._flash: dict = {}            # 칩 → 'added' | 'dup' — 잠깐 반짝이는 칩
        self._undo_snap: Optional[dict] = None
        self._base_reset = False          # 마지막으로 합칠 때 기준 레시피가 바뀌어 값을 되돌렸다
        self._overlay_on = False
        self._mode = "manual"             # manual(직접 조정) | auto(자동 추천)
        self._picked: dict = {}           # 꼭 잡을 Defect — key_of(d) → Defect
        self._rec_zones: set = set()      # 마지막 추천이 바꾼 Zone — 고름이 바뀌면 이 Zone 들만 다시 계산
        self._scans: dict = {}            # (웨이퍼 폴더, Defect.index) → Scan 에서 잘라 온 QImage — 폴더를 더하고 빼도 남는다
        self._scan_thread: Optional[_ScanLoad] = None
        self._scan_map: Optional[dict] = None   # key_of → ((폴더, index), 폴더, (x, y)) — Scan 목록이 있는 폴더의 Defect
        self._scan_done: set = set()            # 자르기를 시도한 (폴더, index) — 성공은 _scans 에 있다 — 다시 시도하지 않는다
        self._scan_token = 0
        self._thumb_bus = _ThumbBus(self)      # 사진 썸네일 · 상세 중간 이미지는 백그라운드에서 만들고 GUI 가 받아 올린다
        self._thumb_bus.ready.connect(self._on_thumb)
        self._thumb_pool = ThreadPoolExecutor(max_workers=THUMB_WORKERS, thread_name_prefix="overkill-thumb")
        self._mid_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="overkill-mid")
        self._wanted: set = set()              # 지금 화면에 필요한 (경로, 크기, 종류) — 여기서 빠진 대기 작업은 건너뛴다(취소)
        self._pending: set = set()             # 이미 맡긴 작업
        self._detail_pic: Optional[tuple] = None
        self._drop_cache: dict = {}       # (Zone, 바꾼 값) → 빠지는 Defect — 같은 기준을 다시 세지 않는다
        self._ov: dict = {}               # {(zone_dir, zone, Classify, FeatureType): 새 LowValue(px)}
        self._rich_base: dict = {}        # (zone_dir, zone) → 레시피 값 RichSurface 통과 배열(캐시)
        self._zone: Optional[tuple] = None
        self._tab = "drop"
        self._open: Optional[int] = None  # 펼친 Defect.index
        self._shown = PAGE_TILES
        self._token = 0
        self._thread: Optional[_Load] = None
        self._rich_token = 0
        self._rich_thread: Optional[_RichLoad] = None
        self._rich_folder: Optional[Path] = None
        self._chips_w = -1
        self._hidden_chips: list = []
        self._more_pop: Optional[QFrame] = None
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
        chips = QWidget(self)                       # 고른 폴더 — 이름은 고르기, 오른쪽 ✕ 는 빼기
        chips.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._chips_host = chips
        self._chips = QHBoxLayout(chips)
        self._chips.setContentsMargins(0, 0, 0, 0)
        self._chips.setSpacing(6)
        top.addWidget(chips, 1)
        self.check_label = QLabel("", self)
        self.check_label.setProperty("role", "muted")
        top.addWidget(self.check_label)
        self.rich_label = QLabel("", self)           # 화면을 띄운 뒤 RichSurface(내리기)를 읽는 동안만
        self.rich_label.setProperty("role", "muted")
        top.addWidget(self.rich_label)
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

        self.notice = QFrame(self)                   # 뺐다 · 더했다 · 이미 있다 — 5초 동안 뜨는 알림(빼기는 [되돌리기] 가 붙는다)
        self.notice.setObjectName("notice")
        nl = QHBoxLayout(self.notice)
        nl.setContentsMargins(12, 4, 4, 4)
        nl.setSpacing(8)
        self.notice_label = QLabel("", self.notice)
        self.notice_label.setStyleSheet("font-weight: 600; background: transparent;")
        nl.addWidget(self.notice_label, 1)
        self.undo_btn = NeonButton(_KO.OVERKILL_UNDO, role="ghost")
        self.undo_btn.clicked.connect(self._undo)
        nl.addWidget(self.undo_btn)
        self.notice.setVisible(False)
        root.addWidget(self.notice)
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(self._hide_notice)
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._clear_flash)

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
        self.filter_label = QLabel("", self)             # 칩을 골라 그 폴더만 볼 때 — 탭 숫자는 전체 기준이라 따로 알린다
        self.filter_label.setStyleSheet(f"color: {theme.ACCENT}; font-weight: 600;")
        tabs.addWidget(self.filter_label)
        self.scan_status = QLabel("", self)              # Scan 사진을 불러오는 동안의 진행(화면을 막지 않는다)
        self.scan_status.setProperty("role", "muted")
        tabs.addWidget(self.scan_status)
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

    def _ready(self, root: Path) -> bool:
        """그 폴더의 웨이퍼 목록과 웨이퍼 데이터를 이미 메모리에 갖고 있다(읽을 게 없다)."""
        ents = self._expand.get(root)
        return root in self._failed or (ents is not None and all(d in self._cache for d, _r in ents))

    def _rebuild_owner(self) -> None:
        """웨이퍼 → 그걸 가진 칩.  같은 웨이퍼(LOT 과 그 안의 웨이퍼를 같이 고른 경우)는 앞 칩이 가진다."""
        owner, order, real_owner = {}, [], {}
        for r in self._roots:
            for d, real in self._expand.get(r, ()):
                if real not in real_owner:
                    real_owner[real] = r
                    owner[d] = r
                    order.append(d)
        self._owner, self._order, self._real_owner = owner, order, real_owner

    def add_folders(self, folders) -> None:
        """고른 폴더(웨이퍼 또는 LOT)를 더한다 — 이미 있는 것은 읽지 않고 그 칩을 반짝여 알린다.
        화면에 이미 뭔가 떠 있으면 막지 않고, 새 칩 안에서만 진행을 보인다."""
        new: list = []
        dups: list = []
        for f in map(Path, folders):
            owner = f if f in self._roots else self._owner.get(f)
            if owner is not None:
                dups.append((f, owner))
            elif f not in new:
                new.append(f)
        if dups:
            self._flash_chips([o for _f, o in dups], "dup")
            self._show_notice(_KO.OVERKILL_NOTICE_DUP.format(name=dups[0][0].name), "info")
        if new:
            self._roots += new
            self._added += new
            self._refresh_roots()

    def remove_folder(self, folder: Path) -> None:
        """칩을 뺀다 — 읽지 않고 메모리에 있는 웨이퍼로 다시 합친다.  5초 동안 [되돌리기]."""
        if folder not in self._roots:
            return
        w = self._w
        picks = {(_folder_of(w, d), d.index) for d in self._picked.values()} if w is not None else set()
        lost = sum(1 for f, _i in picks if self._owner.get(f) == folder)
        snap = {"root": folder, "index": self._roots.index(folder), "ov": dict(self._ov), "picks": picks,
                "lost": lost}
        self._roots.remove(folder)
        self._added = [r for r in self._added if r != folder]
        self._base_reset = False
        self._refresh_roots()
        snap["reset"] = self._base_reset
        text = _KO.OVERKILL_NOTICE_REMOVED.format(name=folder.name)
        if lost:
            text += _KO.OVERKILL_NOTICE_PICKS_LOST.format(n=lost)
        if self._base_reset:
            text += " · " + _KO.OVERKILL_NOTICE_BASE_RESET
        self._show_notice(text, "info", undo=snap)

    def _undo(self) -> None:
        """방금 뺀 칩을 되돌린다 — 그 칩과 (잃은) 꼭 잡기 · (기준이 바뀌어 지워진) 바꾼 값을 읽지 않고 다시 올린다."""
        snap = self._undo_snap
        self._hide_notice()
        if snap is None or snap["root"] in self._roots:
            return
        self._roots.insert(min(snap["index"], len(self._roots)), snap["root"])
        self._refresh_roots()
        w = self._w
        if w is None or w.note or not self._all_ready():
            return
        if snap["reset"] or (snap["lost"] and self._mode == "auto"):    # 자동 추천은 빠진 고름 때문에 다시 추천됐다 — 그 전 값으로
            self._ov = dict(snap["ov"])
        self._picked.update({key_of(d): d for d in w.defects if (_folder_of(w, d), d.index) in snap["picks"]})
        self._rec_zones = {(d.zone_dir, d.zone) for d in self._picked.values()}
        self._drop_cache = {}
        self._render()

    def set_folders(self, roots) -> None:
        """이 폴더들로 바꾼다(읽어 둔 웨이퍼는 다시 안 읽는다)."""
        self._roots = list(roots)
        self._added = []
        self._refresh_roots()

    def show_folder(self, folder: Optional[Path] = None) -> None:
        if folder is not None:                      # 옛 호출(폴더 하나) — 그 폴더로 바꾼다
            self.set_folders([folder])
        else:
            self._refresh_roots()

    def _all_ready(self) -> bool:
        return all(self._ready(r) for r in self._roots)

    def _refresh_roots(self) -> None:
        """칩 목록이 바뀐 뒤 — 읽을 게 없으면 그 자리에서 합치고(덮개 · 스레드 없음), 있을 때만 읽는다."""
        if self._sel not in self._roots:
            self._sel = None
        self._rebuild_owner()
        if not self._roots:
            self._render_chips()
            self._stop_load()
            self.set_wafer(None)
            self._maybe_rich()
        elif self._all_ready():
            self._stop_load()
            self._remerge()
        else:
            self._start_load()

    def _stop_load(self) -> None:
        self._token += 1                            # 옛 읽기의 늦은 마무리는 버린다(다 읽은 웨이퍼는 캐시에 이미 올라왔다)
        if self._thread is not None and self._thread.isRunning():
            self._thread.requestInterruption()
        if self._overlay_on:
            self._loading.hide_overlay()
            self._overlay_on = False

    def _start_load(self) -> None:
        self._stop_load()
        if self._w is None or self._w.note:         # 보여 줄 게 아직 없을 때만 화면을 덮는다
            self._loading.show_overlay(_KO.THRESH_PREVIEW_LOADING)
            self._overlay_on = True
            self._loading.repaint()
            QApplication.processEvents()
        th = _Load(self._token, self._roots, self._expand, set(self._cache))
        th.signals.progress.connect(self._on_progress)
        th.signals.expanded.connect(self._on_expanded)
        th.signals.wafer.connect(self._on_wafer)
        th.signals.done.connect(self._on_loaded)
        self._thread = th
        self._render_chips()
        th.start()

    def _on_expanded(self, _token: int, root, entries) -> None:
        self._expand[root] = list(entries)
        self._rebuild_owner()
        self._render_chips()

    def _on_wafer(self, _token: int, folder, data) -> None:
        """웨이퍼 하나를 다 읽었다 — 읽던 중에 끊겨도 이건 버리지 않는다."""
        self._cache[folder] = data
        self._render_chips()

    def _on_progress(self, token: int, done: int, total: int, msg: str) -> None:
        if token == self._token and self._overlay_on:
            self._loading.set_progress(done, total, msg)

    def _on_loaded(self, token: int, ok: bool) -> None:
        if token != self._token:
            return                      # 늦게 온 옛 결과
        if self._overlay_on:
            self._loading.hide_overlay()
            self._overlay_on = False
        if not ok:
            self._failed |= {r for r in self._roots if not self._ready(r)}
        self._remerge()

    def _included(self) -> set:
        return set(self._w.folders) if self._w is not None and not self._w.note else set()

    def _default_view(self, root: Path) -> str:
        """칩의 처음 보기 — 컬러 사진이 있으면 컬러, 스캔만 있으면 스캔."""
        ds = [self._cache[d] for d, _r in self._expand.get(root, ()) if d in self._cache and not self._cache[d].note]
        if any(c.photos for c in ds):
            return "color"
        return "scan" if any(c.has_scan for c in ds) else "color"

    def _remerge(self) -> None:
        """읽어 둔 웨이퍼로 합쳐 화면에 올린다(동기).  방금 더한 칩이 있으면 그 결과를 알린다."""
        self._rebuild_owner()
        added, self._added = self._added, []
        dups = [r for r in added if r in self._roots and self._expand.get(r)
                and all(self._real_owner.get(real) != r for _d, real in self._expand[r])]
        if dups:                                    # LOT 안 웨이퍼를 이미 다 갖고 있다 — 칩을 늘리지 않고 그 칩을 알린다
            owners = {self._real_owner[real] for r in dups for _d, real in self._expand[r]}
            for r in dups:
                self._roots.remove(r)
            added = [r for r in added if r not in dups]
            self._rebuild_owner()
            self._flash_chips(list(owners), "dup")
            self._show_notice(_KO.OVERKILL_NOTICE_DUP.format(name=dups[0].name), "info")
        for r in self._roots:
            if r not in self._view and self._ready(r):
                self._view[r] = self._default_view(r)
        ws = [self._cache[f] for f in self._order if f in self._cache]
        if not self._roots:
            w = None
        elif ws:
            w = merge_wafers(ws)
        else:
            w = WaferData(self._roots[0], note=_KO.THRESH_PREVIEW_FAILED if self._failed else _KO.OVERKILL_NO_WAFER)
        self.set_wafer(w)
        self._render_chips()
        self._announce(added)
        self._maybe_rich()

    def _announce(self, added: list) -> None:
        """더한 칩의 결과 — 칩을 초록으로 반짝이고 '몇 장 · Defect 몇 개' 를 알린다."""
        if not added:
            return
        inc = self._included()
        infos = [self._chip_info(r, inc) for r in added]
        ok = [i for i in infos if i.state == "ok"]
        if not ok:
            self._show_notice(_KO.OVERKILL_NOTICE_EXCLUDED.format(name=infos[0].name, why=infos[0].reason), "warn")
            return
        w, n = sum(i.wafers for i in ok), sum(i.count for i in ok)
        self._flash_chips([i.root for i in ok], "added")
        self._show_notice(_KO.OVERKILL_NOTICE_ADDED.format(name=ok[0].name, w=w, n=n) if len(infos) == 1
                          else _KO.OVERKILL_NOTICE_ADDED_MANY.format(k=len(ok), w=w, n=n), "ok")

    def _maybe_rich(self) -> None:
        """남은 웨이퍼가 정확히 한 장이고 RichSurface 를 아직 안 읽었으면 화면을 막지 않고 뒤에서 읽는다(내리기)."""
        w = self._w
        want = None
        if w is not None and not w.note and len(w.folders) == 1 and not w.rich:
            base = self._cache.get(w.folders[0])
            if base is not None and not base.rich and (w.folders[0] / RS.RICH_FILE).is_file():
                want = w.folders[0]
        if self._rich_thread is not None and self._rich_thread.isRunning() and self._rich_folder != want:
            self._rich_thread.requestInterruption()
            self._rich_token += 1
            self.rich_label.setText("")
        if want is None or (self._rich_thread is not None and self._rich_thread.isRunning()
                            and self._rich_folder == want):
            return
        self._rich_token += 1
        th = _RichLoad(self._rich_token, want)
        th.signals.progress.connect(self._on_rich_progress)
        th.signals.done.connect(self._on_rich)
        self._rich_thread, self._rich_folder = th, want
        th.start()

    def _on_rich_progress(self, token: int, done: int, total: int) -> None:
        if token == self._rich_token:
            self.rich_label.setText(_KO.OVERKILL_RICH_PROGRESS.format(p=int(100 * done / total) if total else 0))

    def _on_rich(self, token: int, folder, rich) -> None:
        """뒤에서 읽은 RichSurface 를 올린다 — 바꾼 값은 그대로, 기준 칸만 다시 그려 내리기를 연다."""
        if token != self._rich_token:
            return
        self.rich_label.setText("")
        base = self._cache.get(folder)
        if base is not None:
            base.rich = rich
        if not rich or self._w is None or self._w.note or self._w.folders != (folder,):
            return
        self._w.rich = rich
        self._rich_base = {}
        self._render_rules()
        self._render_summary()

    # ------------------------------------------------------------------ 칩
    def _chip_info(self, root: Path, included: set) -> _ChipInfo:
        info = _ChipInfo(root, root.name, selected=root == self._sel, flash=self._flash.get(root, ""),
                         tip=str(root))
        ents = self._expand.get(root)
        if root in self._failed:
            info.state, info.reason = "excluded", _KO.OVERKILL_CHIP_EX_UNREAD
            return info
        owned = [d for d, _r in ents or () if self._owner.get(d) == root]
        if ents is None or (ents and not owned):
            info.state, info.progress = "loading", _KO.OVERKILL_CHIP_READING_NOCOUNT
            return info
        info.lot = not (len(ents) == 1 and ents[0][0] == root)
        got = [d for d in owned if d in self._cache]
        if len(got) < len(owned):
            info.state = "loading"
            info.progress = _KO.OVERKILL_CHIP_READING.format(i=len(got), n=len(owned))
            return info
        data = self._cache
        ok = [d for d in owned if d in included]
        if info.lot:
            info.tip += "\n" + "\n".join(
                f"{d.name}   " + (f"{len(data[d].defects):,}" if d in included else
                                  _KO.OVERKILL_CHIP_EX_UNREAD if data[d].note else _KO.OVERKILL_CHIP_EX_RECIPE)
                for d in owned)
        if not ok:
            info.state = "excluded"
            info.reason = (_KO.OVERKILL_CHIP_EX_RECIPE if any(not data[d].note for d in owned)
                           else _KO.OVERKILL_CHIP_EX_UNREAD)
            return info
        info.count = sum(len(data[d].defects) for d in ok)
        info.wafers, info.partial = len(ok), len(owned) - len(ok)
        info.can_color = any(data[d].photos for d in ok)
        info.can_scan = any(data[d].has_scan for d in ok)
        info.view = self._view.get(root) or ("color" if info.can_color or not info.can_scan else "scan")
        return info

    def _make_chip(self, info: _ChipInfo, parent) -> _FolderChip:
        c = _FolderChip(info, parent)
        c.select.connect(lambda r=info.root: self.select_folder(r))
        c.remove.connect(lambda r=info.root: self.remove_folder(r))
        c.view.connect(lambda m, r=info.root: self.set_view(r, m))
        return c

    def _render_chips(self) -> None:
        """칩 줄을 다시 그린다.  자리가 모자라면 뒤쪽을 '+n 더 보기' 로 접는다(고른 칩 · 읽는 칩 · 반짝이는 칩은 먼저 남긴다)."""
        self._clear(self._chips)
        inc = self._included()
        infos = [self._chip_info(r, inc) for r in self._roots]
        chips = [self._make_chip(i, self._chips_host) for i in infos]
        gap = self._chips.spacing()
        avail = self._chips_host.width() if self._chips_host.isVisible() else 0      # 아직 안 그려졌으면 폭을 모른다
        self._chips_w = self._chips_host.width()
        show = set(range(len(chips)))
        if avail > 1 and chips and sum(c.sizeHint().width() + gap for c in chips) - gap > avail:
            budget = avail - MORE_CHIP_PX - gap
            prio = [i for i, inf in enumerate(infos) if inf.selected or inf.state == "loading" or inf.flash]
            show, used = set(), 0
            for i in prio + [i for i in range(len(chips)) if i not in prio]:
                need = chips[i].sizeHint().width() + gap
                if used + need <= budget:
                    show.add(i)
                    used += need
        self._hidden_chips = [infos[i] for i in range(len(chips)) if i not in show]
        for i, c in enumerate(chips):
            if i in show:
                self._chips.addWidget(c)
            else:
                c.hide()
                c.deleteLater()
        self.more_btn = None
        if self._hidden_chips:
            b = NeonButton(_KO.OVERKILL_MORE_FOLDERS.format(n=len(self._hidden_chips)), role="ghost")
            b.setFixedHeight(34)
            b.setToolTip(_KO.OVERKILL_MORE_FOLDERS_TIP)
            b.clicked.connect(self._open_more)
            self._chips.addWidget(b)
            self.more_btn = b
        self._chips.addStretch(1)
        if self._more_pop is not None and self._more_pop.isVisible():
            if self._hidden_chips:
                self._fill_more()
            else:
                self._more_pop.hide()

    def _open_more(self) -> None:
        """접힌 칩 전체 목록 — 수 · 컬러/스캔 · ✕ · 고르기가 칩 줄과 똑같이 된다."""
        if self._more_pop is None:
            pop = QFrame(self, Qt.WindowType.Popup)
            pop.setObjectName("morePop")
            pop.setStyleSheet(f"QFrame#morePop {{ background: {theme.PANEL}; border: 1px solid {theme.LINE_STRONG};"
                              " border-radius: 6px; }")
            lay = QVBoxLayout(pop)
            lay.setContentsMargins(8, 8, 8, 8)
            lay.setSpacing(6)
            self._more_pop = pop
        self._fill_more()
        pop = self._more_pop
        if self.more_btn is not None:
            pop.move(self.more_btn.mapToGlobal(QPoint(0, self.more_btn.height() + 4)))
        pop.show()

    def _fill_more(self) -> None:
        pop = self._more_pop
        self._clear(pop.layout())
        for info in self._hidden_chips:
            pop.layout().addWidget(self._make_chip(info, pop), 0, Qt.AlignmentFlag.AlignLeft)
        pop.adjustSize()

    def select_folder(self, root: Path) -> None:
        """칩 이름을 누르면 그 폴더 Defect 만 사진 칸에 보인다 — 다시 누르면 전체.  숫자 · 값 계산은 늘 전체 기준."""
        self._sel = None if self._sel == root else root
        self._open = None
        self._shown = PAGE_TILES
        self._render_chips()
        self._render_tiles()

    def set_view(self, root: Path, mode: str) -> None:
        """칩의 [컬러 | 스캔] — 그 폴더 Defect 가 컬러 사진으로 보일지 Scan Crop 으로 보일지."""
        if self._view.get(root) == mode:
            return
        self._view[root] = mode
        self._open = None
        self._render_chips()
        self._render_tiles()

    def _flash_chips(self, roots, kind: str) -> None:
        for r in roots:
            self._flash[r] = kind
        self._render_chips()
        self._flash_timer.start(FLASH_MS)

    def _clear_flash(self) -> None:
        self._flash.clear()
        self._render_chips()

    def _show_notice(self, text: str, kind: str = "info", undo: Optional[dict] = None) -> None:
        bg = {"info": theme.ACCENT_TINT, "ok": theme.PASS_TINT, "warn": theme.WARN_TINT}[kind]
        self.notice.setStyleSheet(f"QFrame#notice {{ background: {bg}; border-radius: 6px; }}")
        self.notice_label.setText(text)
        self._undo_snap = undo
        self.undo_btn.setVisible(undo is not None)
        self.notice.setVisible(True)
        self._notice_timer.start(NOTICE_MS)

    def _hide_notice(self) -> None:
        self.notice.setVisible(False)
        self._undo_snap = None

    def dragEnterEvent(self, e):    # noqa: N802
        if local_paths(e.mimeData()):           # 디스크는 drop 에서만 본다
            e.acceptProposedAction()

    def dropEvent(self, e):         # noqa: N802
        paths = local_paths(e.mimeData())
        if paths:
            e.acceptProposedAction()
            self._add_dropped(paths)

    def _add_dropped(self, paths) -> None:
        """끌어놓기·붙여넣기 경로 — 폴더는 그대로, 사진은 들어 있는 폴더를 더한다."""
        folders, images, _ = split_paths(
            paths, is_dir=lambda p: Path(p).is_dir(),
            is_image=lambda p: CONFIG.is_image(Path(p).name))
        dirs = [Path(f) for f in folders]
        for im in images:
            d = Path(im).parent
            if d not in dirs:
                dirs.append(d)
        if dirs:
            self.add_folders(dirs)
        else:
            self._show_notice(i18n.KO.DROP_WRONG_KIND_FOLDER, "warn")

    def _on_paste_no_path(self, has_image: bool) -> None:
        self._show_notice(i18n.KO.DROP_NO_PATH_IMAGE if has_image else i18n.KO.DROP_NOTHING_TO_PASTE, "warn")

    def set_wafer(self, w: Optional[WaferData]) -> None:
        """읽은 웨이퍼(여러 장이면 합친 것)를 화면에 올린다(테스트는 스레드 없이 이것만 부른다).

        폴더를 더하거나 빼도 남아 있는 웨이퍼의 꼭 잡기와, 합치기 기준(첫 폴더의 레시피)이 같으면 바꾼 값도 이어 간다.
        기준이 바뀌면(첫 폴더를 뺐을 때) 바꾼 값 · 꼭 잡기를 처음으로 되돌리고 알린다."""
        prev = self._w
        kept_picks = {(_folder_of(prev, d), d.index) for d in self._picked.values()} if prev else set()
        both = prev is not None and w is not None and not prev.note and not w.note
        same_base = both and prev.zones == w.zones and prev.px == w.px
        self._base_reset = bool(both and not same_base)
        self._w = w
        self._ov = dict(self._ov) if same_base else {}
        self._rich_base = {}
        self._drop_cache = {}
        self._picked = ({} if self._base_reset else
                        {key_of(d): d for d in (w.defects if w else ()) if (_folder_of(w, d), d.index) in kept_picks})
        prev_rec = self._rec_zones
        self._rec_zones = {(d.zone_dir, d.zone) for d in self._picked.values()}
        if (self._mode == "auto" and same_base and w is not None
                and {(_folder_of(w, d), d.index) for d in self._picked.values()} != kept_picks):
            # 폴더를 빼서 꼭 잡기가 줄었다 — 사라진 고름에서 나온 추천이 남지 않게 toggle_pick 과 같은 식으로 다시 추천한다
            # (고름이 없어진 Zone 은 레시피 값으로, 남은 Zone 은 남은 고름으로).  직접 조정은 그대로 둔다.
            self._rec_zones = prev_rec
            self._recommend()
        self._open = None
        self._shown = PAGE_TILES
        self._tab = "keep" if self._mode == "auto" else "drop"
        self._scan_map = None
        self.scan_status.setText("")
        self._scan_token += 1                       # 옛 목록의 Scan 진행·마무리는 버린다(잘라 둔 Crop 은 캐시에 남는다)
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self._scan_thread.requestInterruption()
        self._scan_thread = None
        if self._base_reset:
            self._show_notice(_KO.OVERKILL_NOTICE_BASE_RESET, "warn")
        if w is None:
            self._zone = None
            self._render()
            return
        zs = self.zone_keys()
        self._zone = next((k for k in zs if w.zones.get(k) and w.zones[k].bundles
                           and self._count(k)), zs[0] if zs else None)
        self._render()

    # ------------------------------------------------------------------ Scan 사진
    def _skey(self, d: P.Defect) -> tuple:
        """Scan Crop 캐시 키 — 합친 목록 안 위치가 아니라 (웨이퍼 폴더, Defect.index) 라 더하고 빼도 그대로다."""
        return _folder_of(self._w, d), d.index

    def _has_scan(self, folder: Path) -> bool:
        c = self._cache.get(folder)
        return c.has_scan if c is not None else (folder / SI.SCAN_LIST_NAME).is_file()

    def _scan_index(self) -> dict:
        """Scan 목록이 있는 웨이퍼의 Defect — ``{key_of: ((폴더, index), 폴더, (x, y))}``(목록이 바뀔 때마다 한 번)."""
        if self._scan_map is None:
            has: dict = {}
            out = {}
            w = self._w
            for d in (w.defects + w.goods) if w is not None and not w.note else ():
                folder = _folder_of(w, d)
                if folder not in has:
                    has[folder] = self._has_scan(folder)
                if has[folder]:
                    out[key_of(d)] = ((folder, d.index), folder, (d.x, d.y))
            self._scan_map = out
        return self._scan_map

    def _scan_items(self) -> list:
        """:meth:`_scan_index` 를 ``[(키, 폴더, (x, y))]`` 로 — 키는 ``(웨이퍼 폴더, Defect.index)``."""
        return list(self._scan_index().values())

    def _view_of(self, folder: Path) -> str:
        root = self._owner.get(folder)
        return self._view.get(root, "color") if root is not None else "color"

    def _wants_scan(self, d: P.Defect) -> bool:
        """이 Defect 는 Scan Crop 으로 보인다 — 칩이 [스캔] 이거나, [컬러] 인데 컬러 사진이 없을 때(대신 채운다)."""
        return key_of(d) not in self._w.photos or self._view_of(_folder_of(self._w, d)) == "scan"

    def _scan_pending(self, d: P.Defect) -> bool:
        """Scan Crop 을 쓰기로 했고 아직 안 잘랐다 — 사진이 '올 예정' 이라 자리표시 타일로 보인다."""
        idx = self._scan_index().get(key_of(d))
        return (idx is not None and self._wants_scan(d) and idx[0] not in self._scans
                and idx[0] not in self._scan_done)

    def _ensure_scans(self) -> None:
        """지금 화면(탭의 앞 ``_shown`` 장 + 한 쪽)에 필요한데 아직 안 자른 Crop 만 백그라운드로 자른다.

        한 번에 스레드 하나 — 끝나면(:meth:`_on_scans`) 다시 불러 그 사이 늘어난 필요(더 보기·탭 이동)를 이어서 한다.
        결과는 키로 ``_scans`` 에 쌓여 같은 Defect 를 두 번 자르지 않는다."""
        if self._w is None or self._zone is None:
            return
        if self._scan_thread is not None and self._scan_thread.isRunning():
            return
        want = []
        idx = self._scan_index()
        for d, _why in self._items():
            if self._scan_pending(d):
                want.append(idx[key_of(d)])
                if len(want) >= self._shown + PAGE_TILES:
                    break
        if not want:
            return
        th = _ScanLoad(self._scan_token, want)
        th.signals.progress.connect(self._on_scan_progress)
        th.signals.batch.connect(self._on_scan_batch)
        th.signals.done.connect(self._on_scans)
        self._scan_thread = th
        self.scan_status.setText(_KO.OVERKILL_SCAN_PROGRESS.format(done=0, total=len(want)))
        th.start()

    def _on_scan_progress(self, token: int, done: int, total: int) -> None:
        if token == self._scan_token:
            self.scan_status.setText(_KO.OVERKILL_SCAN_PROGRESS.format(done=done, total=total))

    def _on_scan_batch(self, _token: int, got: dict) -> None:
        """일부 Crop 이 도착 — 캐시에 쌓고(목록이 바뀐 뒤 늦게 와도 버리지 않는다), 이미 그려진 자리표시 타일의 그림만 바꾼다."""
        self._scans.update(got)
        if self._w is None:
            return
        for k, t in self.tiles.items():
            d = self._tile_defects.get(k)
            if d is None or self._open == k or self._skey(d) not in got:
                continue
            photo = self._pic(d)
            if photo is not None:
                t.set_photo(photo)

    def _on_scans(self, token: int, keys: list) -> None:
        if token != self._scan_token:
            return
        self._scan_thread = None
        self.scan_status.setText("")
        self._scan_done.update(keys)
        if self._open is not None or any(k not in self._scans for k in keys):
            self._render_tiles()                    # 못 자른 자리표시는 빠지고(펼친 값은 그림이 바뀌고), 늘어난 필요는 이어서 자른다
        else:
            self._ensure_scans()                    # 그 사이 늘어난 필요(더 보기·탭 이동)를 이어서

    def set_scans(self, got: dict) -> None:
        """잘라 온 Scan 사진을 올린다(테스트는 스레드 없이 이것만 부른다).  Scan 후보 전부를 시도한 것으로 본다."""
        self._scans.update(got)
        self._scan_done |= {v[0] for v in self._scan_index().values()}
        self._render_tiles()

    def _pic(self, d: P.Defect):
        """그 Defect 의 그림 — 칩이 [컬러] 면 컬러 사진(없으면 Scan Crop), [스캔] 이면 Scan Crop.  아직 없으면 None."""
        color = self._w.photos.get(key_of(d))
        idx = self._scan_index().get(key_of(d))
        if idx is None or (color is not None and not self._wants_scan(d)):
            return color
        return self._scans.get(idx[0])

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
        """지금 기준으로 빠지는 Defect — ``[(Defect, (Classify, FeatureType))]``.

        한 번 그릴 때 같은 계산을 여러 번 부른다(Zone 줄 · 숫자 · 사진) — 같은 기준이면 한 번만 센다."""
        key = key or self._zone
        sig = (key, frozenset(self._ov.items()))
        if sig not in self._drop_cache:
            if len(self._drop_cache) > 64:
                self._drop_cache.clear()
            self._drop_cache[sig] = [(d, why) for d in self._in_zone(key)
                                     if (why := P.why_dropped(d, self._w.zones, self._ov)) is not None]
        return self._drop_cache[sig]

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
        h.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        lab = QLabel(f"{name} ≥", cell)
        lab.setStyleSheet("font-weight: 600;")
        h.addWidget(lab)
        spin = QLineEdit(cell)                 # 위·아래 버튼 없이 숫자만 친다 — 조금씩은 슬라이더로
        val = QDoubleValidator(0.0, 1e9, dec, spin)   # 범위 밖 값도 받아서 apply 가 끝으로 맞춘다
        val.setNotation(QDoubleValidator.Notation.StandardNotation)
        val.setLocale(QLocale.c())
        spin.setValidator(val)
        spin.setObjectName("thrValue")
        spin.setFixedSize(60, 26)              # 라벨 한 줄 높이 — 값이 라벨 줄의 일부로 읽히게
        spin.setProperty("role", "mono")
        spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # 테두리 없는 칸 + 아래 실선만(포커스는 강조색 2px).  이 화면의 값 칸에만 적용한다.
        spin.setStyleSheet(
            f"QLineEdit#thrValue {{ background: transparent; border: none;"
            f" border-bottom: 1px solid {theme.LINE_STRONG}; border-radius: 0;"
            f" padding: 0px 2px 1px 2px; min-height: 0px; font-weight: 600; }}"
            f"QLineEdit#thrValue:hover {{ background: {theme.ACCENT_TINT_SOFT}; }}"
            f"QLineEdit#thrValue:focus {{ background: transparent; border: none;"
            f" border-bottom: 2px solid {theme.FOCUS}; padding: 0px 2px 0px 2px; }}")
        h.addWidget(spin)
        if unit:
            h.addSpacing(-2)                   # 단위를 값 바로 뒤에 붙인다
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
        """지금 탭의 ``[(Defect, 빠진 이유 | None)]`` — 칩을 골랐으면 그 폴더 것만(숫자 · 계산은 전체 기준 그대로)."""
        items = self._tab_items()
        if self._sel is None:
            return items
        w = self._w
        return [(d, why) for d, why in items if self._owner.get(_folder_of(w, d)) == self._sel]

    def _tab_items(self) -> list:
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

    def _want(self, photo, size: int, kind: str) -> None:
        """썸네일(`thumb`) · 중간 이미지(`mid`) 한 장을 백그라운드에 맡긴다 — 화면 순서대로 줄을 선다."""
        key = (str(photo), int(size), kind)
        self._wanted.add(key)
        if key in self._pending:
            return
        self._pending.add(key)
        (self._mid_pool if kind == "mid" else self._thumb_pool).submit(self._thumb_job, Path(photo), int(size), kind)

    def _thumb_job(self, path: Path, size: int, kind: str) -> None:
        """워커 스레드 — 화면에서 빠진 것은 건너뛰고, QImage 까지만 만든다(QPixmap 금지)."""
        key = (str(path), size, kind)
        sent = False
        try:
            if key not in self._wanted:
                return
            tp = image_io.get_mid_path(path) if kind == "mid" else image_io.get_thumb_path(path)
            img = QImage(str(tp))
            if img.isNull():
                return
            img = img.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self._thumb_bus.ready.emit(path, size, kind, img)
            sent = True
        except Exception:                       # 한 장 실패가 나머지를 막지 않는다 — 자리표시로 남는다
            _LOG.debug("썸네일 실패: %s", path, exc_info=True)
        finally:
            if not sent:
                self._pending.discard(key)

    def _on_thumb(self, path, size: int, kind: str, img) -> None:
        """GUI 스레드 — 받은 그림을 캐시에 넣고 기다리던 타일·상세에 올린다."""
        self._pending.discard((str(path), size, kind))
        pm = QPixmap.fromImage(img)
        cache = image_io._get_tile_cache()
        if kind == "mid":
            cache.put(_mid_key(path, size), pm)
            if self._detail_pic is not None and str(self._detail_pic[0]) == str(path):
                self._detail_pic[1].setPixmap(pm)
            return
        cache.put(_tile_key(path, size), pm)
        for t in self.tiles.values():
            if t.wait_path is not None and str(t.wait_path) == str(path):
                t.set_pixmap(pm)

    def _render_tiles(self) -> None:
        self._wanted = set()                    # 화면이 바뀌었다 — 줄 서 있던 것 중 이번 화면에 없는 건 건너뛴다
        self._detail_pic = None
        self._clear(self.grid)
        self.tiles = {}
        self._tile_defects = {}
        w = self._w
        if w is None or w.note or self._zone is None:
            self.empty_label.setText("" if w is None else w.note)
            self.no_photo_label.setText("")
            self.filter_label.setText("")
            return
        items = self._items()
        with_photo = [(d, why) for d, why in items if self._pic(d) is not None or self._scan_pending(d)]
        missing = len(items) - len(with_photo)
        self.no_photo_label.setText(_KO.THRESH_PREVIEW_NO_PHOTO.format(n=f"{missing:,}") if missing else "")
        self.filter_label.setText(_KO.OVERKILL_FILTER_ONLY.format(name=self._sel.name, n=len(items))
                                  if self._sel is not None else "")
        self.empty_label.setText("" if items else (_KO.THRESH_PREVIEW_NONE_DROPPED
                                                   if self._tab == "drop" else _KO.THRESH_PREVIEW_NONE))
        cols = self._cols()
        shown = with_photo[:self._shown]
        r = c = 0
        pending = None
        auto = self._mode == "auto"
        for j, (d, why) in enumerate(shown):
            k = key_of(d)
            tile = _Tile(self._pic(d), self._class_name(d.bin), self._why_text(d, why), TILE_PX,
                         self.grid_host, picked=k in self._picked, detail_btn=auto)
            if auto:                                 # 자동 모드: 타일 = 꼭 잡기 토글, ⋯ = 값 펼치기
                tile.clicked.connect(lambda i=k: self.toggle_pick(i))
                tile.detail.connect(lambda i=k: self._toggle(i))
            else:
                tile.clicked.connect(lambda i=k: self._toggle(i))
            tile.set_open(self._open == k)
            if tile.wait_path is not None:
                self._want(tile.wait_path, TILE_PX, "thumb")
            self.grid.addWidget(tile, r, c)
            self.tiles[k] = tile
            self._tile_defects[k] = d
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
        self._ensure_scans()

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
        photo = self._pic(d)
        pic.setStyleSheet(f"background: {theme.PANEL};")
        if isinstance(photo, QImage):                   # Scan Crop — Defect 자리를 빨간 박스로(상세 보기에서만)
            pix = _pixmap(photo, DETAIL_PX, kind="mid")
            self._mark_defect(pix, photo, d)
            pic.setPixmap(pix)
        elif photo is not None:                         # Color — 중간 이미지가 준비될 때까지 썸네일을 키워 보여준다
            cache = image_io._get_tile_cache()
            pix = cache.get(_mid_key(photo, DETAIL_PX))
            if pix is None:
                thumb = cache.get(_tile_key(photo, TILE_PX))
                if thumb is not None:
                    pix = thumb.scaled(DETAIL_PX, DETAIL_PX, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                self._want(photo, DETAIL_PX, "mid")
                self._detail_pic = (photo, pic)
            if pix is not None:
                pic.setPixmap(pix)
        else:
            pic.setText(_KO.OVERKILL_SCAN_WAIT)
        if photo is None or isinstance(photo, QImage):                   # Scan Crop — 크게 볼 원본 파일이 없다
            pic.setCursor(Qt.CursorShape.ArrowCursor)
        else:
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
        fn = QLabel(_KO.OVERKILL_SCAN_TAG if photo is None or isinstance(photo, QImage)
                    else str(photo) if len(w.folders) > 1 else photo.name, box)
        fn.setProperty("role", "muted")
        fn.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right.addWidget(fn)
        right.addStretch(1)
        lay.addLayout(right, 1)
        return box

    def _mark_defect(self, pix: QPixmap, crop: QImage, d: P.Defect) -> None:
        """Scan Crop 그림 위에 Defect 를 감싼 1px 빨간 박스를 그린다(그림 크기에 맞춰 환산)."""
        box = defect_box((crop.width(), crop.height()), self._w.px, d.width, d.length, d.area)
        if box is None or pix.isNull():
            return
        kx, ky = pix.width() / crop.width(), pix.height() / crop.height()
        x0, y0 = round(box[0] * kx), round(box[1] * ky)
        x1, y1 = round((box[0] + box[2]) * kx), round((box[1] + box[3]) * ky)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(QColor(DEFECT_BOX_RGB))
        pen.setWidth(1)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.drawRect(x0, y0, x1 - x0, y1 - y0)
        p.end()

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
            have = sorted(((d, why) for d, why in drops if self._pic(d) is not None),
                          key=lambda t: -self._below(t[0], t[1]))     # 기준 바로 아래부터 멀리까지
            samples = tuple(RP.Sample(_data_uri(self._pic(d)), self._class_name(d.bin), self._why_text(d, why))
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

    def showEvent(self, e):         # noqa: N802
        super().showEvent(e)
        if self._roots:
            self._render_chips()                    # 칩 줄 폭이 정해진 뒤에야 넘치는지 안다

    def resizeEvent(self, e):       # noqa: N802
        super().resizeEvent(e)
        if self._roots and self._chips_host.width() != self._chips_w:
            self._render_chips()
        if self._w is not None and self.grid.count():
            cols = self._cols()
            if cols != getattr(self, "_last_cols", cols):
                self._render_tiles()
            self._last_cols = cols

    def closeEvent(self, e):        # noqa: N802
        self._wanted = set()                       # 줄 서 있는 썸네일 작업은 건너뛴다
        if self.is_loading():
            self._thread.requestInterruption()
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self._scan_thread.requestInterruption()
        if self._rich_thread is not None and self._rich_thread.isRunning():
            self._rich_thread.requestInterruption()
        super().closeEvent(e)
