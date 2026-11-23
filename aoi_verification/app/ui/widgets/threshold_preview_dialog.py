"""Overkill 튜닝(옛 이름 Threshold 미리보기) — Zone 기준을 올리면 Defect 가 몇 개 빠지는지 본다.

현택님 요청(2026-11-16):
  * 폴더는 웨이퍼 폴더 또는 LOT 폴더(바로 아래 웨이퍼들), 여러 개를 [+ 폴더] · 끌어다 놓기로 더한다.
    **처음 고른 폴더의 레시피**(Zone 기준 · 픽셀 크기 · 파일에 적힌 Job/레시피)와 같은 웨이퍼만 합친다(:func:`merge_wafers`) —
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
import html
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

from PyQt6.QtCore import (QBuffer, QCoreApplication, QEvent, QIODevice, QLocale, QObject, QPoint, QPointF, QPropertyAnimation, QRectF, QSize, Qt,
                          QThread, QTimer, QUrl, pyqtProperty, pyqtSignal)
from PyQt6.QtGui import (QColor, QDesktopServices, QDoubleValidator, QFont, QFontMetrics, QImage, QPainter, QPainterPath,
                         QPen, QPixmap, QPolygonF)
from PyQt6.QtWidgets import (QAbstractButton, QAbstractSlider, QApplication, QButtonGroup, QDialog, QFileDialog, QFrame, QHBoxLayout,
                             QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ... import i18n
from ...config import CONFIG, Sizing
from ...coords import abs_coord
from ...coords.geometry import GEOMETRY_MATCH_TOL
from ...coords import scan_crop_cache as SCC
from ...coords import scan_image as SI
from ...coords import surface_flt as _flt
from ...coords.pixel_size import scan_pixel_size_xy
from ...models.lot_info import read_active_slot
from ...models.slot import _list_images, list_slot_dirs
from ...threshold import preview as P
from ...threshold import report as RP
from ...threshold import rich as RS
from ...threshold import rules as R
from ...threshold import saved_picks as SP
from ...utils import image_io
from .. import theme
from .. import motion
from ..deferred import call_later
from ..drop_overlay import drop_clear, drop_feedback
from ..drop_paths import guess_kinds, install_paste, local_paths, split_paths
from ..folder_picker import FolderPicker
from . import sheet_host as sheets
from .loading_overlay import LoadingOverlay
from .neon_button import NeonButton
from .overkill_pick_dialog import OverkillPickDialog
from .no_wheel_slider import NoWheelSlider

_LOG = logging.getLogger("aoi.threshold")
_KO = i18n.KO

LOAD_WORKERS = 6            # 여러 웨이퍼를 동시에 읽는 수(NAS 왕복 지연을 겹친다)
LIVE_TILES_MAX = 100        # 이 수 이하일 때만 슬라이더를 끄는 동안 사진을 바로 바꾼다(현택님 2026-11-14)
PAGE_TILES = 60             # 처음 보이는 사진 수 — 나머지는 [더 보기]
TILE_PX = 132
TILE_GAP = 2                # 밀착 인화 — 타일 사이 간격
DETAIL_PX = 200
SIDE_W = 244                # 왼쪽 열(폴더 · Zone) 폭
RULE_HEAD_W = 136           # 기준 줄 머리(기준 · 검출 단계) 열 폭 — 모든 줄이 같은 폭이라 슬라이더 칸이 세로로 맞는다
RULE_GAP = 20               # 기준 줄 안 칸 사이
EXPORT_SAMPLES = 8          # HTML 내보내기 — Zone 마다 대표 사진 수
EXPORT_PHOTO_PX = 200
NOTICE_MS = 5000            # 뺐다 · 더했다 알림이 떠 있는 시간(되돌리기도 이 안에)
PICK_REFRESH_MS = 80        # [Defect 고르기] 팝업에 도착한 사진을 모아 올리는 간격
WAFER_MERGE_MS = 150        # LOT 안 웨이퍼를 넣고 뺄 때 — 연달아 누르면 모아서 한 번만 합친다
FLASH_MS = 1500             # 더한 칩 · 이미 있는 칩이 반짝이는 시간
_LIVE: set = set()          # 화면이 닫혀도 도는 읽기 스레드를 붙들어 둔다(wafer_map_dialog 와 같은 패턴)

# 화면에 보이는 순서와 단위.  Surface.flt 에 값이 있는 세 기준만 바꿀 수 있다(rules 모듈 docstring).
_FEATURES = ((R.FEATURE_WIDTH, "Width", "µm", 0.05, 2),
             (R.FEATURE_AREA, "Area", "µm²", 0.5, 1),
             (R.FEATURE_CONTRAST, "Contrast", "", 1.0, 1))


# 검출 단계 하한(Zone ini [Surface]) — (극성, 종류) → (ini 이름, 화면 이름, Feature 번호).  단위 · 간격은 _FEATURES 와 같다.
# Length 는 길이라 폭(Width)과 같은 단위 · 간격을 쓴다.  끈 조건(9999999) · 하한 없음(0) 칸은 띄우지 않는다.
_DETECT_ROWS = (("dark", "area", "DarkArea", "Dark Area", R.FEATURE_AREA),
                ("dark", "width", "DarkDiameter", "Dark Diameter", R.FEATURE_WIDTH),
                ("dark", "length", "DarkLength", "Dark Length", R.FEATURE_WIDTH),
                ("bright", "area", "BrightArea", "Bright Area", R.FEATURE_AREA),
                ("bright", "width", "BrightDiameter", "Bright Diameter", R.FEATURE_WIDTH),
                ("bright", "length", "BrightLength", "Bright Length", R.FEATURE_WIDTH))


def _shown_limit(z, stage: str, kind: str) -> bool:
    """화면에 슬라이더로 띄울 검출 하한인가 — 끈 조건(inf) · 하한 없음(0) · 없는 키는 바꿀 게 없어 안 띄운다."""
    return 0 < z.detect.get((stage, kind), 0) < math.inf


def detect_label(stage: str, kind: str) -> str:
    return next(r[3] for r in _DETECT_ROWS if r[:2] == (stage, kind))


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
    skipped: tuple = ()               # 못 합친 웨이퍼 폴더 — 못 읽었거나 레시피(:func:`same_base`)가 기준 웨이퍼와 다름
    has_scan: bool = False            # 폴더에 Scan 목록이 있다 — 칩의 [스캔] 을 켤 수 있다
    slot: str = ""                    # 카세트 슬롯 번호(WaferInfo.ini ActiveSlot) — 모르면 빈 글자
    recipe_id: tuple = ("", ())       # 어느 레시피로 스캔했나(SetupPath · 레시피 이름들) — :func:`rules.recipe_identity`
    _multi: object = field(default=None, repr=False, compare=False)   # 레시피(Zone 폴더)가 둘 이상인가(캐시)

    def label(self, key) -> tuple:
        """Zone 키 → ``(이름, 레시피 표시)`` — 레시피가 여럿이면 어느 레시피 Zone 인지 늘 적는다."""
        if self._multi is None:
            # Defect 가 레시피 1 에만 있어도 다른 레시피 Zone 줄이 같은 이름으로 서므로, 폴더의 Zone 폴더 수로도 본다.
            self._multi = len({d.zone_dir for d in self.defects + self.goods} | {k[0] for k in self.zones}) > 1
        return zone_label(key[0], self.zones.get(key), key[1], R.read_recipe_names(self.folder), self._multi)

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
    w = WaferData(folder, defects, goods, note=note, has_scan=(folder / SI.SCAN_LIST_NAME).is_file(),
                  slot=read_active_slot(folder) or "", recipe_id=R.recipe_identity(folder))
    if note:
        return w
    w.zones = R.zones_in_flt_px(folder, R.read_zones(folder))     # flt 와 같은 픽셀로 — rules.zones_in_flt_px
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


def _wafer_label(folder: Path, data: WaferData) -> str:
    """LOT 안 웨이퍼 줄 이름 — 슬롯 번호(ActiveSlot)를 알면 ``#슬롯 · 폴더``, 모르면 폴더 이름만."""
    return _KO.OVERKILL_LOT_WAFER_NAME.format(slot=data.slot, name=folder.name) if data.slot else folder.name


def same_base(a: WaferData, b: WaferData) -> bool:
    """두 웨이퍼를 같은 슬라이더로 셀 수 있나 — Zone 기준 · 픽셀 크기가 같고 **같은 레시피로 스캔한 것**.

    레시피가 다르면 스캔을 새로 한 것이라 Zone 이름 · 값이 같아도 따로 튜닝한다(현택님 2026-11-23) — 레시피는
    파일에서 읽는다(:func:`rules.recipe_identity`, 모르는 칸은 비교하지 않는다)."""
    return (a.zones == b.zones and all(abs(x - y) < 1e-6 for x, y in zip(a.px, b.px))
            and R.same_recipe(a.recipe_id, b.recipe_id))


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
    same = [w for w in good if same_base(base, w)]
    out = WaferData(base.folder, zones=base.zones, classes=base.classes, px=base.px, recipe_id=base.recipe_id,
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


def zone_label(zone_dir: str, zone: R.Zone | None, zone_id: int, names: dict | None = None,
               multi: bool = False) -> tuple[str, str]:
    """``(Zone 이름, 레시피 표시)`` — 레시피 하나뿐인 ``Zones`` 는 표시 없음, ``Recipe2-Zones`` 는 '레시피 2'.

    ``multi``(폴더에 레시피 Zone 폴더가 둘 이상)이면 ``Zones`` 도 레시피 1 로 적는다 — 레시피마다 같은 이름의 Zone
    (Recipe3 ScanArea · Recipe2 Scan Area)이 있어 구분이 안 된다.  ``names`` = ``{N: RecipesInfo.ini 이름}``
    이면 '레시피 N' 대신 그 이름(x20 등), 없으면 '레시피 N'."""
    name = zone.name if zone is not None and zone.name else f"Zone {zone_id}"
    if zone_dir == "Zones" and not multi:
        return name, ""
    n = 1 if zone_dir == "Zones" else int(zone_dir.removeprefix("Recipe").removesuffix("-Zones"))
    return name, (names or {}).get(n) or _KO.THRESH_PREVIEW_RECIPE.format(n=n)


def recipe_no(zone_dir: str) -> int:
    """Zone 폴더 이름 → 레시피 번호(``Zones`` = 1, ``RecipeN-Zones`` = N) — Zone 목록을 레시피 순으로 놓는 데 쓴다."""
    return 1 if zone_dir == "Zones" else int(zone_dir.removeprefix("Recipe").removesuffix("-Zones"))


def recipe_head(zone_dir: str, rec: str) -> str:
    """Zone 목록의 레시피 머리줄 — 이름이 있으면 '레시피 N · 이름', 없으면 '레시피 N'."""
    plain = _KO.THRESH_PREVIEW_RECIPE.format(n=recipe_no(zone_dir))
    return plain if rec == plain else _KO.THRESH_PREVIEW_RECIPE_HEAD.format(n=recipe_no(zone_dir), name=rec)


LOG_P90_FRAC = 0.10         # 값의 90% 가 선형 축의 앞 10% 안에 몰리면 로그 축(현택님 2026-11-17 — 한두 개가 멀리 튀는 Area 등)
LOG_STEPS = 200             # 로그 축 슬라이더 칸 수 — 슬라이더는 정수 위치라 칸마다 값을 기하급수로 놓는다


def use_log_axis(lo: float, hi: float, values) -> bool:
    """값 축을 로그로 그릴까 — ``lo > 0`` 이고, 축 안(``lo ~ hi``) 값의 90번째 백분위가 선형 축의 앞 10% 안에 있을 때.

    예: 거의 다 10~20 인데 하나가 4571 이면 선형 축에서는 분포가 왼쪽 끝 한 칸에 눌린다.  값이 없으면 선형."""
    if not (lo > 0 and hi > lo):
        return False
    xs = sorted(v for v in values if lo <= v <= hi)
    if not xs:
        return False
    p90 = xs[min(len(xs) - 1, math.ceil(0.9 * len(xs)) - 1)]
    return (p90 - lo) < LOG_P90_FRAC * (hi - lo)


def log_decades(lo: float, hi: float) -> list:
    """``lo ~ hi`` 안의 10의 거듭제곱(10, 100, 1000 …) — 로그 축 눈금."""
    if not (lo > 0 and hi > lo):
        return []
    k = math.ceil(math.log10(lo) - 1e-12)
    out = []
    while 10 ** k <= hi * (1 + 1e-12):
        out.append(float(10 ** k))
        k += 1
    return out


class _Axis:
    """기준값 축 — 분포 · 자름선 · 레시피 눈금 · 슬라이더가 **같은** 대응을 쓴다(선형 또는 로그).

    ``frac(v)`` 값 → 0~1, ``value(f)`` 0~1 → 값(이어진 값), ``value_at(i)`` / ``index_of(v)`` 슬라이더 정수 위치 ↔ 값.
    선형은 예전 그대로(레시피 ± ``step`` 의 배수).  로그는 ``LOG_STEPS`` 칸을 기하급수로 놓고 화면 소수 자리로 맞추되
    맨 끝(최소 · 최대)과 **레시피 값 자리는 정확한 값**이다 — 손잡이가 레시피 값에 늘 닿는다."""

    def __init__(self, lo: float, hi: float, rec: float, step: float, dec: int, down: int, steps: int,
                 log: bool = False) -> None:
        self.lo, self.hi, self.rec, self.step, self.dec = lo, hi, rec, step, dec
        self.down, self.log = down, bool(log and lo > 0 and hi > lo)
        self.n = LOG_STEPS if self.log else down + steps
        self._i_rec = round(self.frac(rec) * self.n) if self.log else down

    def frac(self, v: float) -> float:
        span = (self.hi - self.lo) or 1.0
        v = min(max(v, self.lo), self.hi)
        if self.log:
            return math.log(v / self.lo) / math.log(self.hi / self.lo)
        return (v - self.lo) / span

    def value(self, f: float) -> float:
        f = min(max(f, 0.0), 1.0)
        if self.log:
            return self.lo * (self.hi / self.lo) ** f
        return self.lo + f * (self.hi - self.lo)

    def value_at(self, i: int) -> float:
        if not self.log:
            return self.rec + (i - self.down) * self.step
        i = min(max(int(i), 0), self.n)
        if i == 0:
            return self.lo
        if i == self.n:
            return self.hi
        if i == self._i_rec:
            return self.rec
        return min(max(round(self.value(i / self.n), self.dec), self.lo), self.hi)

    def index_of(self, v: float) -> int:
        if not self.log:
            return self.down + round((v - self.rec) / self.step)
        return min(max(round(self.frac(v) * self.n), 0), self.n)


class _Hist(QWidget):
    """기준값 축 위의 Defect 분포 — 슬라이더와 **같은 x · 폭**(손잡이 중심이 다니는 구간 = 양쪽 ``PAD`` 안쪽).
    지금 기준보다 작은(빠지는) 막대는 빨강, 나머지는 잉크.  기준 위치에서 막대 위까지 자름선이 선다.
    막대 칸과 자름선은 ``axis``(선형 또는 로그)의 대응을 따른다."""

    BINS = 28
    PAD = 6               # 슬라이더 손잡이(12px)의 반지름 — 분포 · 자름선 · 손잡이 중심이 같은 x 를 쓴다

    def __init__(self, values, lo: float, hi: float, parent=None, axis: Optional[_Axis] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._lo, self._hi, self._cut = lo, hi, lo
        self._axis = axis or _Axis(lo, hi, lo, 1.0, 2, 0, 1)
        self._counts = [0] * self.BINS
        for v in values:
            if lo <= v <= hi:
                self._counts[min(self.BINS - 1, int(self._axis.frac(v) * self.BINS))] += 1

    def set_cut(self, v: float) -> None:
        self._cut = v
        self.update()

    def cut_x(self) -> float:
        return self.PAD + self._axis.frac(self._cut) * (self.width() - 2 * self.PAD)

    def paintEvent(self, _e):       # noqa: N802
        p = QPainter(self)
        n, h = self.BINS, self.height()
        x0, w = self.PAD, self.width() - 2 * self.PAD
        top = max(1, max(self._counts))
        bw = w / n
        ink = QColor(theme.INK2)
        ink.setAlpha(190)
        for i, c in enumerate(self._counts):
            mid = self._axis.value((i + 0.5) / n)
            col = QColor(theme.DANGER) if mid < self._cut else ink
            bh = max(1.0, (h - 2) * c / top) if c else 0.0
            if bh:
                p.fillRect(QRectF(x0 + i * bw + 0.5, h - 1 - bh, bw - 1, bh), col)
        p.fillRect(QRectF(0, h - 1, self.width(), 1), QColor(theme.LINE2))          # 축
        p.fillRect(QRectF(self.cut_x() - 0.75, 0, 1.5, h), QColor(theme.INK))     # 자름선
        p.end()


class _AxisFoot(QWidget):
    """슬라이더 아래 한 줄 — 레시피 값 자리의 작은 눈금 + '레시피 값', 오른쪽 끝에 축 최댓값.
    로그 축이면 그 밑에 10의 거듭제곱 눈금 글자(10 · 100 · 1000)가 한 줄 더 붙어 로그임이 읽힌다."""

    DECADE_H = 14

    def __init__(self, rec_text: str, max_text: str, axis: _Axis, parent=None) -> None:
        super().__init__(parent)
        self._axis = axis
        self._decades = log_decades(axis.lo, axis.hi) if axis.log else []
        self.setFixedHeight(22 + (self.DECADE_H if self._decades else 0))
        mono = theme.FONT_MONO.split(",")[0].strip('" ')
        self._mono = mono
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, self.DECADE_H if self._decades else 0)
        lay.setSpacing(0)
        left = QLabel(rec_text, self)
        left.setProperty("role", "axisNote")
        lay.addWidget(left)
        lay.addStretch(1)
        right = QLabel(max_text, self)
        right.setProperty("role", "axisNote")
        right.setStyleSheet(f"font-family: {mono};")
        lay.addWidget(right)

    @staticmethod
    def tick_text(v: float) -> str:
        return f"{int(v)}" if v >= 1 else f"{v:g}"

    def paintEvent(self, _e):       # noqa: N802
        p = QPainter(self)
        span = self.width() - 2 * _Hist.PAD
        x = _Hist.PAD + self._axis.frac(self._axis.rec) * span
        p.fillRect(QRectF(x - 0.5, 0, 1, 6), QColor(theme.MUTE))
        if self._decades:
            f = QFont(self._mono)
            f.setPixelSize(10)
            f.setStyleHint(QFont.StyleHint.Monospace)
            p.setFont(f)
            fm = QFontMetrics(f)
            for v in self._decades:
                tx = _Hist.PAD + self._axis.frac(v) * span
                p.fillRect(QRectF(tx - 0.5, 0, 1, 4), QColor(theme.LINE_STRONG))
                t = self.tick_text(v)
                tw = fm.horizontalAdvance(t)
                p.setPen(QColor(theme.MUTE))
                p.drawText(QRectF(tx - tw / 2, self.height() - self.DECADE_H, tw, self.DECADE_H),
                           int(Qt.AlignmentFlag.AlignCenter), t)
        p.end()


class _Tile(QFrame):
    """사진 한 장 — 테두리 · 캡션 없이 사진만.  누르면 ``clicked``.

    표시는 사진 위 작은 것만: 꼭 잡기 = 오른쪽 위 작은 체크, 펼친 타일 = 3px 강조 테두리, 가리킨 타일 = 옅은 외곽선.
    우클릭 = [크게보기](``enlarge``)."""

    clicked = pyqtSignal()
    enlarge = pyqtSignal()

    def __init__(self, photo, size: int, parent=None, picked: bool = False,
                 tip: str = "") -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)   # 덜 찬 줄에서 가로로 늘어나지 않게
        self.setFixedSize(size, size)
        self.setToolTip(tip)
        self.picked = picked
        self.wait_path = None                          # 썸네일을 아직 기다리는 사진 경로(백그라운드가 채운다)
        pic = QLabel(self)
        pic.setProperty("role", "tilePic")
        pic.setGeometry(0, 0, size, size)
        pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pic, self._size = pic, size
        self.ring = QFrame(self)                       # 펼친 타일 표시 — 사진 위 2px 선
        self.ring.setProperty("role", "tileRing")
        self.ring.setGeometry(0, 0, size, size)
        self.ring.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.ring.setVisible(False)
        self.hover = QFrame(self)                      # 가리킨 타일 — 사진 위 옅은 외곽선(펼친 타일은 ring 이 대신한다)
        self.hover.setProperty("role", "tileHover")
        self.hover.setGeometry(0, 0, size, size)
        self.hover.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hover.setVisible(False)
        self.is_open = False
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.set_photo(photo)
        self.badge = QLabel("✓", self)                 # 꼭 잡기 표시
        self.badge.setProperty("role", "tileBadge")
        self.badge.setFixedSize(16, 16)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.move(size - 22, 6)
        self.badge.setVisible(picked)

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
        pic.setPixmap(_pixmap(photo, size))             # Color 사진이 없어 Scan 사진에서 잘라 온 것

    def set_pixmap(self, pm: QPixmap) -> None:
        self.wait_path = None
        self.pic.setPixmap(pm)

    def set_open(self, on: bool) -> None:
        self.is_open = bool(on)
        self.ring.setVisible(on)
        if on:
            self.hover.setVisible(False)

    def _on_context_menu(self, pos) -> None:
        if _ask_enlarge(self, self.mapToGlobal(pos)):
            self.enlarge.emit()

    def enterEvent(self, e):        # noqa: N802
        self.hover.setVisible(not self.is_open)
        super().enterEvent(e)

    def leaveEvent(self, e):        # noqa: N802
        self.hover.setVisible(False)
        super().leaveEvent(e)

    def mousePressEvent(self, e):   # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


def _ask_enlarge(owner: QWidget, global_pos) -> bool:
    """우클릭 메뉴 [크게보기] — 매치 검토 타일(``zoom_window._MidTile``)과 같은 메뉴.  고르면 True."""
    menu = QMenu(owner)
    act = menu.addAction(_KO.CTX_VIEW_LARGER)
    return menu.exec(global_pos) is act


class _BigPhoto(QLabel):
    clicked = pyqtSignal()
    enlarge = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def _on_context_menu(self, pos) -> None:
        if _ask_enlarge(self, self.mapToGlobal(pos)):
            self.enlarge.emit()

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


class _PickBus(QObject):
    """[적용] 의 추천값 계산(백그라운드) → GUI 스레드 전달 통로."""

    done = pyqtSignal(int, object)       # 토큰 · P.recommend 의 값(실패면 None)


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
SCAN_CACHE_WORKERS = 16     # 캐시된 Crop 의 원본 확인(stat)을 동시에 몇 개 — NAS 왕복이 대부분이다
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

    sigs = {f: SCC.manifest_sig(f) for f in {f for _k, f, _xy in items}}
    pxs = {f: scan_pixel_size_xy(f) for f in sigs}

    def cached(item):
        _k, folder, xy = item
        return SCC.get(folder, sigs[folder], xy, pxs[folder])

    with ThreadPoolExecutor(max_workers=max(1, min(SCAN_CACHE_WORKERS, len(items) or 1))) as pool:
        hits = list(pool.map(cached, items))             # 지난번에 잘라 둔 Crop — 원본(NAS)을 다시 읽지 않는다
    by_folder: dict = {}                             # 폴더마다 목록 stat 을 한 번만(NAS 왕복) — 순서는 items 그대로
    for pos, (k, folder, xy) in enumerate(items):
        if stop():
            return {}
        if hits[pos] is not None:
            crop, src = hits[pos]
            q = _to_qimage(crop)
            q.setText("src", src)
            out[k] = buf[k] = q
            state["done"] += 1
            flush()
            report(state["done"], len(items))
        else:
            by_folder.setdefault(folder, []).append((pos, k, xy))
    slots: list = [None] * len(items)
    for folder, rows in by_folder.items():
        if stop():
            return {}
        for (pos, k, _xy), m in zip(rows, SI.resolve_xy_many(folder, [r[2] for r in rows])):
            slots[pos] = (k, m) if m.ok else None
    found = [x for x in slots if x is not None]
    groups = group_by_source(found)
    total = state["done"] + len(found)

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
            if q is not None and crops is not None:
                q.setText("src", str(group[0][1].best.path))          # 상세 보기에 파일 이름을 보이려고
                SCC.put(m.color_path, sigs.get(m.color_path), m.defect_xy, m.pixel_xy, crop, group[0][1].best.path)
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


@dataclass(frozen=True)
class Criterion:
    """상세의 기준 하나 — 이 Defect 의 값 · 지금 기준(슬라이더) · 레시피 기준(모두 µm 단위) · 연산자 · 지금 기준에 걸리는지."""
    name: str
    value: float
    cut: float
    recipe: float
    dec: int = 1
    op: str = "≥"
    ok: bool = True
    unit: str = ""

    @property
    def adjusted(self) -> bool:
        """슬라이더로 레시피 값에서 옮겼다."""
        return abs(self.cut - self.recipe) > 10 ** -(self.dec + 1)


def filter_op(ftype: int) -> str:
    """FilterType → 기준 앞 연산자.  4 = 초과, 1(범위)·5 = 이상.  Zone 기준은 하한만 다루므로 나머지도 이상으로 본다."""
    return ">" if ftype == 4 else "≥"


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
    rows: tuple = ()              # LOT — ((웨이퍼 폴더, 이름, Defect 수, 넣었나), …) 보이는 순서
    parts: str = ""               # LOT 에서 일부만 넣었을 때 '3/8장 · 425'
    open: bool = False            # LOT 의 웨이퍼 목록이 펴져 있다
    by_count: bool = False        # 목록을 Defect 많은 순으로


class _ElideLabel(QLabel):
    """폭이 모자라면 가운데를 줄이는 한 줄 라벨 — 레이아웃이 주는 폭에 맞춘다."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def _fit(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(0, self.width()))
        if shown != self.text():
            self.setText(shown)

    def resizeEvent(self, e):       # noqa: N802
        super().resizeEvent(e)
        self._fit()

    def showEvent(self, e):         # noqa: N802
        super().showEvent(e)
        self._fit()


class _FolderRow(QFrame):
    """왼쪽 열의 폴더 한 줄 — 이름 + Defect 수 + 작은 ✕.  줄 전체가 클릭 영역이라 누르면 ``select``.

    고른 줄에만 둘째 줄로 [컬러 | 스캔] 이 나온다(``view``).  ✕(``remove``)는 떨어진 작은 칸 하나라 고르다 빠지지 않는다.
    읽는 중 · 제외 폴더는 둘째 줄에 상태 글자가 붙고 ✕ 만 된다."""

    select = pyqtSignal()
    remove = pyqtSignal()
    view = pyqtSignal(str)
    toggle_open = pyqtSignal()

    ROW_H = 32

    def __init__(self, info: _ChipInfo, parent=None) -> None:
        super().__init__(parent)
        self.info = info
        self.setProperty("role", "ovRow")
        self.setProperty("selected", info.selected and info.state == "ok")
        self.setProperty("flash", "on" if info.flash else "")
        self.setProperty("excluded", info.state == "excluded")
        self.setToolTip(info.tip)
        col = QVBoxLayout(self)
        fold = info.state == "ok" and len(info.rows) >= 2           # 웨이퍼를 고를 수 있는 LOT 만 ▾/▸
        col.setContentsMargins(4 if fold else 12, 0, 2, 0)
        col.setSpacing(0)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(4)
        if fold:
            self.disc = QPushButton("▾" if info.open else "▸", self)
            self.disc.setProperty("role", "ovRowDisc")
            self.disc.setFixedSize(18, 28)
            self.disc.setCursor(Qt.CursorShape.PointingHandCursor)
            self.disc.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.disc.setToolTip(_KO.OVERKILL_LOT_TOGGLE_TIP)
            self.disc.clicked.connect(self.toggle_open)
            line.addWidget(self.disc)
        title = info.name + (f" · {_KO.OVERKILL_WAFERS.format(n=info.wafers)}"
                             if info.lot and info.state == "ok" and not info.parts else "")
        self.name = _ElideLabel(title, self)
        self.name.setProperty("role", "ovRowName")
        self.name.setFixedHeight(self.ROW_H)
        line.addWidget(self.name, 1)
        self.seg: dict = {}
        self.side = QLabel("", self)
        if info.state == "ok":
            self.side.setText(info.parts or f"{info.count:,}" + (
                f" · {_KO.OVERKILL_CHIP_PARTIAL.format(n=info.partial)}" if info.partial else ""))
            self.side.setProperty("role", "ovRowCount")
            line.addWidget(self.side)
        self.x_btn = QPushButton("✕", self)             # 빼기 — 이름 영역과 떨어진 작은 칸
        self.x_btn.setProperty("role", "ovRowX")
        self.x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.x_btn.setToolTip(_KO.OVERKILL_CHIP_REMOVE_TIP)
        self.x_btn.setFixedSize(28, 28)
        self.x_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.x_btn.clicked.connect(self.remove)
        line.addWidget(self.x_btn)
        col.addLayout(line)
        if info.state != "ok":                          # 읽는 중 · 제외 — 상태 한 줄
            self.side.setProperty("role", "ovRowNote")
            self.side.setProperty("warn", info.state == "excluded")
            self.side.setText(info.progress if info.state == "loading" else info.reason)
            self.side.setContentsMargins(0, 0, 0, 6)
            col.addWidget(self.side)
        else:
            seg = QHBoxLayout()
            seg.setContentsMargins(0, 0, 0, 6)
            seg.setSpacing(0)
            group = QButtonGroup(self)
            for key, text, can in (("color", _KO.OVERKILL_VIEW_COLOR, info.can_color),
                                   ("scan", _KO.OVERKILL_VIEW_SCAN, info.can_scan)):
                b = QPushButton(text, self)
                b.setProperty("role", "ovView")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setCheckable(True)
                b.setChecked(info.view == key)
                b.setEnabled(can)
                b.setFixedHeight(24)
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                if not can:
                    b.setToolTip(_KO.OVERKILL_VIEW_NO_COLOR if key == "color" else _KO.OVERKILL_VIEW_NO_SCAN)
                b.clicked.connect(lambda _c=False, k=key: self.view.emit(k))
                group.addButton(b)
                seg.addWidget(b)
                self.seg[key] = b
            seg.addStretch(1)
            self.seg_host = QWidget(self)
            self.seg_host.setLayout(seg)
            self.seg_host.setVisible(info.selected)     # 고른 폴더에만 — 평소엔 이름 · 수만
            col.addWidget(self.seg_host)
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, e):   # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.info.state == "ok":
            self.select.emit()
        super().mousePressEvent(e)


class _OnMark(QWidget):
    """웨이퍼 줄 앞 표시 — 넣었으면 채운 원 + 체크, 뺐으면 빈 고리."""

    def __init__(self, on: bool, parent=None) -> None:
        super().__init__(parent)
        self.on = on
        self.setFixedSize(14, 14)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _e):       # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.ACCENT))
            p.drawEllipse(r)
            pen = QPen(QColor(theme.ON_ACCENT), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            x, y, w, h = r.x(), r.y(), r.width(), r.height()
            p.drawPolyline([QPointF(x + w * .26, y + h * .52), QPointF(x + w * .44, y + h * .70),
                            QPointF(x + w * .76, y + h * .32)])
        else:
            p.setPen(QPen(QColor(theme.LINE_STRONG), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r)
        p.end()


class _CountBar(QWidget):
    """웨이퍼 줄 아래 가는 막대 — 그 LOT 에서 가장 많은 웨이퍼 대비 Defect 수."""

    def __init__(self, ratio: float, on: bool, parent=None) -> None:
        super().__init__(parent)
        self.ratio, self.on = ratio, on
        self.setFixedHeight(3)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _e):       # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        rad = r.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.LINE2))
        p.drawRoundedRect(r, rad, rad)
        if self.ratio > 0:
            p.setBrush(QColor(theme.ACCENT if self.on else theme.LINE_STRONG))
            p.drawRoundedRect(QRectF(0, 0, max(r.height(), r.width() * self.ratio), r.height()), rad, rad)
        p.end()


class _WaferRow(QFrame):
    """LOT 안 웨이퍼 한 줄 — 표시 · ``#슬롯 · WaferID`` · Defect 수 · 가는 막대.  줄 전체가 누르는 자리(넣기 ↔ 빼기)."""

    toggled = pyqtSignal()

    def __init__(self, label: str, count: int, top: int, on: bool, tip: str, parent=None) -> None:
        super().__init__(parent)
        self.on = on
        self.setProperty("role", "ovRow")
        self.setProperty("wafer", True)
        self.setProperty("excluded", not on)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tip)
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 0, 6, 0)
        col.setSpacing(0)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        line.addWidget(_OnMark(on, self))
        self.name = _ElideLabel(label, self)
        self.name.setProperty("role", "ovRowName")
        self.name.setFixedHeight(28)
        line.addWidget(self.name, 1)
        self.count = QLabel(f"{count:,}", self)
        self.count.setProperty("role", "ovRowCount")
        line.addWidget(self.count)
        col.addLayout(line)
        bar = QHBoxLayout()
        bar.setContentsMargins(22, 0, 0, 7)
        bar.addWidget(_CountBar(count / top if top else 0.0, on, self))
        col.addLayout(bar)

    def mousePressEvent(self, e):   # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.toggled.emit()
        super().mousePressEvent(e)


class _WaferList(QWidget):
    """펼친 LOT 아래 — [전부 · 없음 · 많은 순] 한 줄과 웨이퍼 줄들."""

    toggled = pyqtSignal(object)        # 웨이퍼 폴더
    all_on = pyqtSignal()
    all_off = pyqtSignal()
    sort_flip = pyqtSignal()

    def __init__(self, info: _ChipInfo, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(0)
        ctl = QHBoxLayout()
        ctl.setContentsMargins(18, 2, 0, 2)
        ctl.setSpacing(0)
        self.ctl: dict = {}
        for key, text, sig in (("all", _KO.OVERKILL_LOT_ALL, self.all_on), ("none", _KO.OVERKILL_LOT_NONE, self.all_off),
                               ("top", _KO.OVERKILL_LOT_BY_COUNT, self.sort_flip)):
            b = QPushButton(text, self)
            b.setProperty("role", "ovView")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setFixedHeight(24)
            if key == "top":
                b.setCheckable(True)
                b.setChecked(info.by_count)
                b.setToolTip(_KO.OVERKILL_LOT_BY_COUNT_TIP)
            b.clicked.connect(lambda _c=False, sg=sig: sg.emit())
            ctl.addWidget(b)
            self.ctl[key] = b
        ctl.addStretch(1)
        lay.addLayout(ctl)
        top = max((c for _d, _n, c, _o in info.rows), default=0)
        self.rows: list = []
        for d, name, count, on in info.rows:
            r = _WaferRow(name, count, top, on, _KO.OVERKILL_LOT_WAFER_TIP.format(name=d.name, n=count), self)
            r.toggled.connect(lambda d=d: self.toggled.emit(d))
            lay.addWidget(r)
            self.rows.append(r)


def paint_ratio(p: QPainter, x: float, y: float, w: float, h: float, drop: int, total: int, ticks=(),
                tick_over: float = 3.0) -> None:
    """가로 비율 막대 — 빠지는 몫은 빨강, 남는 몫은 옅은 잉크.  ``ticks`` = 꼭 잡기 위치(0~1)가 강조색 세로 눈금."""
    if w <= 0 or h <= 0:
        return
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(x, y, w, h), 2, 2)
    p.save()
    p.setClipPath(clip)
    rest = QColor(theme.INK2)
    rest.setAlpha(71)                                   # 옅은 잉크 — 남는 몫(빠짐과 구분되는 중립 면)
    p.fillRect(QRectF(x, y, w, h), rest)
    if total > 0 and drop > 0:
        p.fillRect(QRectF(x, y, w * min(1.0, drop / total), h), QColor(theme.DANGER))
    p.restore()
    for t in ticks:
        p.fillRect(QRectF(x + min(w - 2, max(0.0, w * t - 1)), y - tick_over, 2, h + 2 * tick_over), QColor(theme.ACCENT))


class _RatioBar(QWidget):
    """요약 줄 아래 가로 비율 막대 하나(빠짐 : 남음, 꼭 잡기 눈금)."""

    BAR_H = 12
    MAX_W = 640

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self.BAR_H + 8)
        self.setMaximumWidth(self.MAX_W)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.drop, self.total, self.ticks = 0, 0, ()

    def set_values(self, drop: int, total: int, ticks=()) -> None:
        self.drop, self.total, self.ticks = drop, total, tuple(ticks)
        self.update()

    def paintEvent(self, _e):       # noqa: N802
        p = QPainter(self)
        paint_ratio(p, 0, 4, self.width(), self.BAR_H, self.drop, self.total, self.ticks, tick_over=4.0)
        p.end()


class _ZoneRow(QAbstractButton):
    """왼쪽 열의 Zone 한 줄 — 이름 + 작은 비율 막대 + 빠짐 수.  고른 줄만 면 색(상자 · 테두리 없음).

    글자는 직접 그린다(막대를 한 줄에 넣으려고).  ``text()`` 에는 같은 내용을 줄바꿈으로 남겨 접근성 · 테스트가 읽는다."""

    H = 56

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(self.H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.H)
        self._label, self._count, self._drop, self._total = "", 0, 0, 0
        self._new, self._picks, self._ticks = 0, 0, ()

    def set_data(self, label: str, count: int, drop: int, new: int, picks: int, ticks=()) -> None:
        self._label, self._count, self._drop, self._total = label, count, drop, count
        self._new, self._picks, self._ticks = new, picks, tuple(ticks)
        moved = "   ".join(t for t in (f"−{drop:,}" if drop else "", f"+{new:,}" if new else "",
                                       f"✓{picks:,}" if picks else "") if t)
        self.setText(f"{label}\n{_KO.THRESH_PREVIEW_ZONE_COUNT.format(n=count)}" + (f"\n{moved}" if moved else ""))
        self.update()

    def sizeHint(self):             # noqa: N802
        return QSize(200, self.H)

    def paintEvent(self, _e):       # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, on = self.width(), self.isChecked()
        if on:
            tint = QColor(theme.ACCENT)
            tint.setAlpha(26)
            p.setBrush(tint)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(0, 0, w, self.H), 6, 6)
        elif self.underMouse():
            p.setBrush(QColor(theme.ELEV))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(0, 0, w, self.H), 6, 6)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(theme.FOCUS), 2))
            p.drawRoundedRect(QRectF(1, 1, w - 2, self.H - 2), 6, 6)
        pad = 12
        f1 = QFont(self.font())
        f1.setBold(on)
        p.setFont(f1)
        p.setPen(QColor(theme.ACCENT if on else theme.INK))
        p.drawText(QRectF(pad, 8, w - 2 * pad, 22), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                   QFontMetrics(f1).elidedText(self._label, Qt.TextElideMode.ElideRight, w - 2 * pad))
        mono = QFont(theme.FONT_MONO.split(",")[0].strip('" '))
        mono.setPixelSize(13)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        p.setFont(mono)
        fm = QFontMetrics(mono)
        y2, h2 = 34, 16
        right = w - pad
        parts = [(t, c) for t, c in ((f"−{self._drop:,}" if self._drop else "0", theme.DANGER if self._drop else theme.MUTE),
                                     (f"+{self._new:,}" if self._new else "", theme.PASS),
                                     (f"✓{self._picks:,}" if self._picks else "", theme.ACCENT)) if t]
        for text, color in reversed(parts):             # 오른쪽 끝부터 − · + · ✓ 순으로 놓는다
            tw = fm.horizontalAdvance(text)
            p.setPen(QColor(color))
            p.drawText(QRectF(right - tw, y2, tw, h2), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), text)
            right -= tw + 8
        p.setPen(QColor(theme.MUTE))
        cnt = f"{self._count:,}"
        cw = max(fm.horizontalAdvance(cnt), 22)
        p.drawText(QRectF(pad, y2, cw, h2), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), cnt)
        bx = pad + cw + 8
        paint_ratio(p, bx, y2 + 5, max(0, right - bx), 6, self._drop, self._total, self._ticks, tick_over=3.0)
        p.end()


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


class _FullScanLoad(QThread):
    """Scan Crop 의 [크게보기] — 같은 폴더의 Scan 원본 전체와 Crop 범위를 뒤에서 읽는다(UI 를 막지 않는다).
    사진 정보 화면(:mod:`image_info_dialog`)과 같은 :mod:`coords.scan_image` 가 계산한다.  못 읽으면 원본 · 범위 없이 알린다."""

    class _Signals(QObject):
        done = pyqtSignal(object, object, object, str)      # Crop QImage · 원본 QImage | None · 범위 | None · 원본 경로

    def __init__(self, crop: QImage, folder: Path, xy: tuple, src: str) -> None:
        super().__init__()
        self._crop, self._folder, self._xy, self._src = crop, folder, xy, src
        self.signals = self._Signals()
        _LIVE.add(self)
        self.finished.connect(lambda: _LIVE.discard(self))

    def run(self) -> None:      # type: ignore[override]
        full = box = None
        path = ""
        try:
            m = SI.resolve_xy(self._folder, self._xy)
            if m.ok:
                cand = next((c for c in m.candidates if str(c.path) == self._src), m.best)
                im = SI.load_full(cand)
                if im is not None:
                    full, box, path = _to_qimage(im), tuple(cand.box), str(cand.path)
        except Exception:       # noqa: BLE001 — 못 읽으면 Crop 을 크게 보여 준다
            _LOG.exception("Overkill 튜닝 Scan 원본 읽기 실패")
        self.signals.done.emit(self._crop, full, box, path)


class _ScanProbe(QThread):
    """크게보기에서 이 Defect 의 Scan Crop 이 있는지 · 그 그림을 뒤에서 찾는다 — 타일과 같은 :func:`scan_crops`(Crop 캐시 포함).
    ``done`` 은 Crop ``QImage`` 또는 ``None``(없음 · 못 읽음)."""

    class _Signals(QObject):
        done = pyqtSignal(object)

    def __init__(self, item: tuple) -> None:
        super().__init__()
        self._item = item
        self.signals = self._Signals()
        _LIVE.add(self)
        self.finished.connect(lambda: _LIVE.discard(self))

    def run(self) -> None:      # type: ignore[override]
        got = None
        try:
            got = scan_crops([self._item]).get(self._item[0])
        except Exception:       # noqa: BLE001 — 못 찾으면 '없음' 으로 알린다
            _LOG.exception("Overkill 튜닝 크게보기 Scan 확인 실패")
        self.signals.done.emit(got)


def _settle_layout(_h: int = 0) -> None:
    """띠 높이를 바꾼 틱에서 밀린 재배치(호스트 크기 갱신)를 바로 끝낸다 — 한 프레임 늦으면 그 사이 위쪽 줄이 눌려 출렁인다."""
    for _ in range(3):                       # 재배치가 다음 재배치를 부른다(띠 → 호스트 → 스크롤 영역 안 위젯)
        QCoreApplication.sendPostedEvents(None, int(QEvent.Type.LayoutRequest))


class _DetailBand(QWidget):
    """타일 줄 아래 펼쳐지는 값 띠 — 맨 위에 고른 타일 가운데를 가리키는 작은 삼각형(caret)이 있다.

    같은 줄의 다른 타일로 옮기면 띠는 그대로 두고 삼각형만 미끄러진다(:meth:`move_caret`), 내용은 :meth:`swap_content`."""

    CARET_H = 8
    CARET_HALF_W = 8

    def __init__(self, content: QWidget, caret_x: float, parent=None) -> None:
        super().__init__(parent)
        self._caret = float(caret_x)
        self._caret_anim: Optional[QPropertyAnimation] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, self.CARET_H, 0, 0)
        lay.setSpacing(0)
        self.content = content
        lay.addWidget(content)

    def _get_caret(self) -> float:
        return self._caret

    def _set_caret(self, v: float) -> None:
        self._caret = float(v)
        self.update()

    caret = pyqtProperty(float, _get_caret, _set_caret)

    def move_caret(self, x: float) -> None:
        if not motion.enabled() or not self.isVisible():
            self._set_caret(x)
            return
        if self._caret_anim is None:
            self._caret_anim = QPropertyAnimation(self, b"caret", self)
            self._caret_anim.setEasingCurve(motion.EASE_PRIMARY)
        self._caret_anim.stop()
        self._caret_anim.setDuration(motion.dur(motion.DUR_ROW_RESIZE))
        self._caret_anim.setStartValue(self._caret)
        self._caret_anim.setEndValue(float(x))
        self._caret_anim.start()

    def swap_content(self, new: QWidget) -> None:
        """내용을 새것으로 — 옛 모습을 얹었다가 흐려 서서히 바뀌어 보인다.  높이가 다르면 그만큼만 부드럽게 맞춘다."""
        old = self.content
        ghost = motion.grab_in_place(old) if motion.enabled() and self.isVisible() else None
        h0 = self.height()
        lay = self.layout()
        lay.removeWidget(old)
        old.hide()
        old.deleteLater()
        self.content = new
        lay.addWidget(new)
        new.show()
        if ghost is not None:
            lay.activate()
            motion.crossfade_over(new, ghost, duration=motion.DUR_TILE_TOGGLE)
            h1 = self.sizeHint().height()
            if h1 != h0:
                motion.animate_height(self, h0, h1, duration=motion.DUR_ROW_RESIZE)

    def dispose(self) -> None:
        """띠를 레이아웃에서 빼고 지운다(접히는 모션이 끝났을 때 · 바로 치울 때)."""
        parent = self.parentWidget()
        lay = parent.layout() if parent is not None else None
        if lay is not None:
            lay.removeWidget(self)
        self.hide()
        self.deleteLater()

    def paintEvent(self, _e):       # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        h, w = float(self.CARET_H), float(self.CARET_HALF_W)
        p.drawPolygon(QPolygonF([QPointF(self._caret - w, h), QPointF(self._caret, 0.0), QPointF(self._caret + w, h)]))
        p.end()


class ThresholdPreviewDialog(QDialog):
    """왼쪽 메뉴 'Overkill 튜닝' 화면."""

    def __init__(self, parent=None, folder: Optional[Path] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_KO.THRESH_PREVIEW_TITLE)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        install_paste(self, self._add_dropped, self._on_paste_no_path)
        self._w: Optional[WaferData] = None
        self._show_box = True             # 상세의 Scan 그림에 Defect 빨간 상자를 그릴까 — 창이 열려 있는 동안 유지
        self._roots: list = []            # 고른 폴더(웨이퍼 또는 LOT) — 칩 하나씩.  맨 앞이 합치기 기준 레시피를 정한다
        self._expand: dict = {}           # 고른 폴더 → [(웨이퍼 폴더, 실제 경로 키)] — LOT 을 NAS 에서 다시 훑지 않는다
        self._cache: dict = {}            # 웨이퍼 폴더 → WaferData — 빼기 · 더하기가 다시 읽지 않고 이걸로 합친다
        self._owner: dict = {}            # 웨이퍼 폴더 → 그걸 가진 칩(같은 웨이퍼를 두 칩이 가리켜도 앞 칩 하나만)
        self._order: list = []            # 합칠 웨이퍼 폴더(칩 순서)
        self._real_owner: dict = {}       # 실제 경로 키 → 칩
        self._off: set = set()            # LOT 에서 사용자가 뺀 웨이퍼 폴더 — 합치지 않는다(화면을 닫을 때까지만, 저장 안 함)
        self._open_lots: set = set()      # 웨이퍼 목록을 펼친 LOT
        self._by_count: set = set()       # 목록을 Defect 많은 순으로 보는 LOT
        self._merge_timer = QTimer(self)  # 웨이퍼를 연달아 넣고 뺄 때 한 번만 합친다
        self._merge_timer.setSingleShot(True)
        self._merge_timer.setInterval(WAFER_MERGE_MS)
        self._merge_timer.timeout.connect(self._merge_wafer_choice)
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
        self._overkill: dict = {}         # Overkill 로 가른 Defect — key_of(d) → Defect (팝업에서 고른다 · 추천에는 안 쓰고 '빠짐 m/n' 만 센다)
        self._pick_bus = _PickBus(self)
        self._pick_bus.done.connect(self._on_pick_computed)
        self._pick_token = 0              # [적용] 계산의 세대 — 늦게 온 옛 결과는 버린다
        self._pick_touched: set = set()   # 마지막 [적용] 에서 고른 것이 바뀐 Zone — 그 Zone 저장분만 고친다
        self._pick_dlg: Optional[QDialog] = None   # 열려 있는 [Defect 고르기] 팝업
        self._pick_refresh = QTimer(self)          # 사진이 한 장씩 올 때마다 팝업 전체를 훑지 않게 모아서 한 번
        self._pick_refresh.setSingleShot(True)
        self._pick_refresh.setInterval(PICK_REFRESH_MS)
        self._pick_refresh.timeout.connect(self._refresh_pick_now)
        self._pick_wanted: set = set()    # 팝업이 요청한 (경로, 크기, 종류) — 타일을 다시 그려도 취소되지 않게
        self._pick_extra: dict = {}       # 팝업이 요청한 Scan Crop 대기 Defect — key_of → Defect
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
        self._band: Optional[_DetailBand] = None    # 지금 펼쳐진 값 띠
        self._band_row: Optional[QWidget] = None    # 그 띠가 붙은 타일 줄
        self._row_of: dict = {}           # 타일 키 → 그 타일이 든 줄 위젯
        self._col_of: dict = {}           # 타일 키 → 줄 안 몇 번째(삼각형 x 계산)
        self._tile_why: dict = {}         # 타일 키 → 빠진 이유(상세를 새로 만들 때)
        self._full_scan: Optional[_FullScanLoad] = None
        self._shown = PAGE_TILES
        self._token = 0
        self._thread: Optional[_Load] = None
        self._rich_token = 0
        self._rich_thread: Optional[_RichLoad] = None
        self._rich_folder: Optional[Path] = None
        self._picker = FolderPicker(self)
        self._picker.picked.connect(lambda p: p and self.add_folders([Path(p)]))
        self._build()
        for b in self.findChildren(QPushButton):     # 열릴 때 첫 버튼이 포커스 링을 달고 나오지 않게
            b.setAutoDefault(False)
        self._loading = LoadingOverlay(self)
        self._loading.cancel_requested.connect(self._cancel_load)
        if folder:
            self.set_folders([Path(folder)])
        else:
            self._render()

    # ------------------------------------------------------------------ 틀
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 윗줄 — 검산 · 모드만(폴더는 왼쪽 열, 되돌리기 · 내보내기는 요약 줄)
        head = QFrame(self)
        head.setProperty("role", "ovHeader")
        head.setFixedHeight(56)
        top = QHBoxLayout(head)
        top.setContentsMargins(24, 0, 16, 0)
        top.setSpacing(16)
        self.check_label = QLabel("", self)          # 제목은 창 맨 위 단계 줄이 이미 보인다 — 여기엔 두지 않는다
        self.check_label.setProperty("role", "muted")
        top.addWidget(self.check_label)
        self.rich_label = QLabel("", self)           # 화면을 띄운 뒤 RichSurface(내리기)를 읽는 동안만
        self.rich_label.setProperty("role", "muted")
        top.addWidget(self.rich_label)
        top.addStretch(1)
        self._mode_group = QButtonGroup(self)
        self.mode_btns = {}
        seg = QHBoxLayout()
        seg.setSpacing(4)
        for key, text in (("manual", _KO.OVERKILL_MODE_MANUAL), ("auto", _KO.OVERKILL_MODE_AUTO)):
            b = NeonButton(text, role="segment")
            b.setProperty("grade", "panel")
            b.setCheckable(True)
            b.setChecked(key == self._mode)
            b.clicked.connect(lambda _c=False, k=key: self._on_mode_clicked(k))
            self._mode_group.addButton(b)
            seg.addWidget(b)
            self.mode_btns[key] = b
        top.addLayout(seg)
        root.addWidget(head)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # 왼쪽 열 — 위 = 폴더, 아래 = Zone(묶음 사이 24px)
        side = QFrame(self)
        side.setProperty("role", "ovSide")
        side.setFixedWidth(SIDE_W)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(side)
        scroll.setProperty("role", "ovSideScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setContentsMargins(12, 16, 12, 16)
        il.setSpacing(0)
        fhead = self._side_folder_head = QLabel(_KO.OVERKILL_FOLDERS, inner)
        fhead.setProperty("role", "ovGroupHead")
        fhead.setContentsMargins(12, 0, 0, 4)
        il.addWidget(fhead)
        self._folder_lay = QVBoxLayout()               # 폴더 줄 — 비어 있으면 아래 [+ 폴더] 만 남는다
        self._folder_lay.setSpacing(2)
        il.addLayout(self._folder_lay)
        self.pick_btn = NeonButton(_KO.OVERKILL_ADD_FOLDER, role="textAccent")
        self.pick_btn.setToolTip(_KO.OVERKILL_ADD_FOLDER_TIP)
        self.pick_btn.clicked.connect(self._on_pick)
        il.addWidget(self.pick_btn, 0, Qt.AlignmentFlag.AlignLeft)
        il.addSpacing(12)
        rule = self._side_rule = QFrame(inner)
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {theme.LINE2}; border: none;")
        il.addWidget(rule)
        il.addSpacing(12)
        zhead = self._zone_head = QLabel(_KO.THRESH_PREVIEW_ZONE, inner)
        zhead.setProperty("role", "ovGroupHead")
        zhead.setContentsMargins(12, 0, 0, 4)
        il.addWidget(zhead)
        self._zone_col = QVBoxLayout()
        self._zone_col.setSpacing(4)
        il.addLayout(self._zone_col)
        il.addStretch(1)
        scroll.setWidget(inner)
        sl.addWidget(scroll)
        body.addWidget(side)
        self._zone_group = QButtonGroup(self)
        self._zone_group.setExclusive(True)
        self.zone_btns = {}
        self.recipe_heads: dict = {}          # 레시피가 여럿일 때 Zone 목록의 레시피 머리줄(Zone 폴더 → QLabel)

        main = self._main = QVBoxLayout()
        main.setContentsMargins(12, 16, 12, 12)       # 왼쪽 열 244 + 좌우 12 + 기준 패널 최소 772 = 창 최소 폭 1040
        main.setSpacing(12)
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
        main.addWidget(self.notice)
        # 폴더가 없을 때의 본문 — 한 줄 안내 + [+ 폴더].  폴더를 더하면 사라진다.
        self.empty_state = QWidget(self)
        el = QVBoxLayout(self.empty_state)
        el.setSpacing(12)
        el.addStretch(1)
        self.empty_hint = QLabel(_KO.OVERKILL_EMPTY, self.empty_state)
        self.empty_hint.setProperty("role", "muted")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el.addWidget(self.empty_hint)
        self.empty_pick_btn = NeonButton(_KO.OVERKILL_ADD_FOLDER, role="ghost")
        self.empty_pick_btn.setProperty("grade", "panel")
        self.empty_pick_btn.setToolTip(_KO.OVERKILL_ADD_FOLDER_TIP)
        self.empty_pick_btn.clicked.connect(self._on_pick)
        el.addWidget(self.empty_pick_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        el.addStretch(2)
        main.addWidget(self.empty_state, 1)
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(self._hide_notice)
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._clear_flash)

        # 요약 — 한 줄 수치 + 비율 막대 하나(큰 숫자 블록 없음)
        self.summary = QWidget(self)
        sm = QVBoxLayout(self.summary)
        sm.setContentsMargins(0, 0, 0, 0)
        sm.setSpacing(8)
        srow = QHBoxLayout()
        srow.setSpacing(20)

        def stat(caption: str, tone: str = "none"):
            box = QWidget(self.summary)
            bl = QHBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(6)
            cap = QLabel(caption, box)
            cap.setProperty("role", "sumCap")
            val = QLabel("", box)
            val.setProperty("role", "sumNum")
            val.setProperty("tone", tone)
            bl.addWidget(cap)
            bl.addWidget(val)
            srow.addWidget(box)
            return box, val

        _b, self.drop_value = stat(_KO.OVERKILL_STAT_DROP)
        self.drop_value.setText("0")
        self.new_box, self.new_value = stat(_KO.THRESH_PREVIEW_APPEARED)
        self.new_value.setText("0")
        self.new_box.setVisible(False)
        _b, self.keep_label = stat(_KO.OVERKILL_STAT_KEPT)
        _b, self.all_label = stat(_KO.OVERKILL_STAT_ALL)
        self.pick_box, self.pick_label = stat(_KO.OVERKILL_STAT_PICKED, "pick")   # 꼭 잡기 k
        self.miss_box, self.miss_label = stat(_KO.OVERKILL_STAT_MISSED, "over")   # 하나라도 빠지면 빨간 '놓침 m'
        self.ov_box, self.ov_label = stat(_KO.OVERKILL_STAT_OVERKILL_GONE)       # Overkill 빠짐 m/n
        self.pick_box.setVisible(False)
        self.miss_box.setVisible(False)
        self.ov_box.setVisible(False)
        srow.addStretch(1)
        self.pick_open_btn = NeonButton(_KO.OVERKILL_PICK_OPEN, role="primary")   # 자동 추천 모드에서만
        self.pick_open_btn.clicked.connect(self.open_pick_dialog)
        self.pick_open_btn.setVisible(self._mode == "auto")
        srow.addWidget(self.pick_open_btn)
        self.saved_btn = NeonButton(_KO.OVERKILL_SAVED_LOAD, role="textAction")   # 자동 추천 모드에서만 — 저장해 둔 선택
        self.saved_btn.setToolTip(_KO.OVERKILL_SAVED_LOAD_TIP)
        self.saved_btn.clicked.connect(self.load_saved_picks)
        self.saved_btn.setVisible(self._mode == "auto")
        self.saved_btn.setEnabled(False)
        srow.addWidget(self.saved_btn)
        self.reset_btn = NeonButton(_KO.THRESH_PREVIEW_RESET, role="textAction")
        self.reset_btn.setToolTip(_KO.THRESH_PREVIEW_RESET_TIP)
        self.reset_btn.clicked.connect(self.reset)
        srow.addWidget(self.reset_btn)
        self.export_btn = NeonButton(_KO.THRESH_PREVIEW_EXPORT, role="textAction")
        self.export_btn.clicked.connect(self._on_export)
        srow.addWidget(self.export_btn)
        sm.addLayout(srow)
        self.ratio = _RatioBar(self.summary)
        sm.addWidget(self.ratio)
        main.addWidget(self.summary)

        self.rules = QFrame(self)                    # 상자 없음 — 열 머리 · 분포 · 슬라이더가 같은 면 위에 선다
        self._rules_lay = QVBoxLayout(self.rules)
        self._rules_lay.setContentsMargins(0, 4, 0, 4)
        self._rules_lay.setSpacing(16)
        main.addWidget(self.rules)

        self._tabs_host = tabs_host = QFrame(self)                     # 밑줄 탭 — 고른 탭은 굵게 + 강조색 밑줄
        tabs_host.setProperty("role", "ovTabs")
        tabs = QHBoxLayout(tabs_host)
        tabs.setContentsMargins(0, 0, 0, 0)
        tabs.setSpacing(24)
        self._tabs_lay = tabs
        self._tab_group = QButtonGroup(self)
        self.tab_btns = {}
        for key in ("drop", "keep", "good", "pick"):
            b = NeonButton("", role="underTab")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            b.clicked.connect(lambda _c=False, k=key: self._set_tab(k))
            self._tab_group.addButton(b)
            tabs.addWidget(b)
            self.tab_btns[key] = b
        tabs.addStretch(1)
        self.no_photo_label = QLabel("", self)
        self.no_photo_label.setProperty("role", "muted")
        tabs.addWidget(self.no_photo_label)
        self.filter_label = QLabel("", self)             # 폴더를 골라 그 폴더만 볼 때 — 탭 숫자는 전체 기준이라 따로 알린다
        self.filter_label.setStyleSheet(f"color: {theme.ACCENT}; font-weight: 600;")
        tabs.addWidget(self.filter_label)
        self.scan_status = QLabel("", self)              # Scan 사진을 불러오는 동안의 진행(화면을 막지 않는다)
        self.scan_status.setProperty("role", "muted")
        tabs.addWidget(self.scan_status)
        main.addWidget(tabs_host)

        self.grid_host = QWidget(self)
        self.grid = QVBoxLayout(self.grid_host)         # 분류 제목 · 타일 한 줄(줄 위젯) · 펼친 값 띠가 위에서 아래로 — 띠를 줄 사이에 끼우고 뺀다
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(TILE_GAP)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        main.addWidget(self.grid_host)
        self.empty_label = QLabel("", self)
        self.empty_label.setProperty("role", "muted")
        main.addWidget(self.empty_label)
        main.addStretch(1)
        self._sync_empty()
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
                    if d not in self._off:          # 사용자가 뺀 웨이퍼는 칩에는 남고 합치지 않는다
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
        self._open_lots &= set(self._roots)         # 빠진 LOT 의 고름 · 펼침은 버린다(다시 더하면 전부 넣은 채로)
        self._by_count &= set(self._roots)
        self._off &= {d for r in self._roots for d, _r in self._expand.get(r, ())}
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

    def _cancel_load(self) -> None:
        """[중지] — 아직 읽는 폴더를 목록에서 빼고(이미 읽은 폴더는 남긴다) 읽기 전 화면으로 돌아간다."""
        if not self._overlay_on:
            return
        self._roots = [r for r in self._roots if self._ready(r)]
        self._added = [r for r in self._added if r in self._roots]
        self._refresh_roots()

    def _start_load(self) -> None:
        self._stop_load()
        if self._w is None or self._w.note:         # 보여 줄 게 아직 없을 때만 화면을 덮는다
            self._loading.show_overlay(_KO.THRESH_PREVIEW_LOADING, cancelable=True)
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
        elif self._off & set(self._owner):          # 웨이퍼를 전부 뺐다 — 보통 빈 화면
            w = WaferData(self._roots[0], note=_KO.OVERKILL_LOT_NONE_PICKED)
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
        active = [d for d in owned if d not in self._off]            # 사용자가 뺀 웨이퍼는 '제외' 가 아니다
        ok = [d for d in active if d in included]
        pending = self._merge_timer.isActive()                      # 넣고 뺀 직후 — 합치기 전이라 included 가 옛것
        if pending:
            ok = [d for d in active if not data[d].note]
        if info.lot:
            info.tip += "\n" + "\n".join(
                f"{d.name}   " + (f"{len(data[d].defects):,}" if d in included or (d in self._off and not data[d].note)
                                  else _KO.OVERKILL_CHIP_EX_UNREAD if data[d].note else _KO.OVERKILL_CHIP_EX_RECIPE)
                for d in owned)
            lst = [d for d in owned if not data[d].note]
            if len(lst) >= 2:
                info.open, info.by_count = root in self._open_lots, root in self._by_count
                rows = [(d, _wafer_label(d, data[d]), len(data[d].defects), d not in self._off) for d in lst]
                info.rows = tuple(sorted(rows, key=lambda t: -t[2]) if info.by_count else rows)
                kept = [t for t in rows if t[3]]
                if len(kept) < len(rows):
                    info.parts = _KO.OVERKILL_LOT_PARTS.format(k=len(kept), n=len(rows), c=sum(t[2] for t in kept))
        if not ok and active:
            info.state = "excluded"
            info.reason = (_KO.OVERKILL_CHIP_EX_RECIPE if any(not data[d].note for d in owned)
                           else _KO.OVERKILL_CHIP_EX_UNREAD)
            return info
        info.count = sum(len(data[d].defects) for d in ok)
        info.wafers, info.partial = len(ok), 0 if pending else len(active) - len(ok)
        info.can_color = any(data[d].photos for d in ok)
        info.can_scan = any(data[d].has_scan for d in ok)
        info.view = self._view.get(root) or ("color" if info.can_color or not info.can_scan else "scan")
        return info

    def _make_chip(self, info: _ChipInfo, parent) -> _FolderRow:
        c = _FolderRow(info, parent)
        c.select.connect(lambda r=info.root: self.select_folder(r))
        c.remove.connect(lambda r=info.root: self.remove_folder(r))
        c.view.connect(lambda m, r=info.root: self.set_view(r, m))
        if info.rows:
            c.toggle_open.connect(lambda r=info.root: self.toggle_lot_open(r))
        return c

    def _make_wafer_list(self, info: _ChipInfo) -> _WaferList:
        lst = _WaferList(info, self)
        lst.toggled.connect(self.toggle_wafer)
        lst.all_on.connect(lambda r=info.root: self.set_lot_wafers(r, True))
        lst.all_off.connect(lambda r=info.root: self.set_lot_wafers(r, False))
        lst.sort_flip.connect(lambda r=info.root: self.toggle_lot_sort(r))
        return lst

    def _render_chips(self) -> None:
        """왼쪽 열의 폴더 목록을 다시 그린다 — 폴더가 많으면 열이 스크롤되고 접지 않는다."""
        self._clear(self._folder_lay)
        inc = self._included()
        for r in self._roots:
            info = self._chip_info(r, inc)
            self._folder_lay.addWidget(self._make_chip(info, self))
            if info.open and len(info.rows) >= 2:
                self._folder_lay.addWidget(self._make_wafer_list(info))

    # ------------------------------------------------------------------ LOT 안 웨이퍼 고르기
    def toggle_lot_open(self, root: Path) -> None:
        """LOT 줄의 ▾/▸ — 웨이퍼 목록을 펴고 접는다(합친 결과는 그대로)."""
        self._open_lots ^= {root}
        self._render_chips()

    def toggle_lot_sort(self, root: Path) -> None:
        """[많은 순] — 목록을 Defect 많은 순 ↔ 폴더 순서."""
        self._by_count ^= {root}
        self._render_chips()

    def _lot_wafers(self, root: Path) -> list:
        """그 LOT 칩이 가진 웨이퍼 중 읽힌 것(고를 수 있는 것)."""
        return [d for d, _r in self._expand.get(root, ())
                if self._owner.get(d) == root and d in self._cache and not self._cache[d].note]

    def toggle_wafer(self, folder: Path) -> None:
        """웨이퍼 한 장을 넣거나 뺀다 — 줄 표시는 바로, 합치기는 연달아 누르면 모아서 한 번."""
        self._off ^= {folder}
        self._wafers_changed()

    def set_lot_wafers(self, root: Path, on: bool) -> None:
        """[전부] · [없음] — 그 LOT 의 읽힌 웨이퍼를 전부 넣거나 뺀다."""
        ds = set(self._lot_wafers(root))
        self._off = self._off - ds if on else self._off | ds
        self._wafers_changed()

    def _wafers_changed(self) -> None:
        self._rebuild_owner()
        self._render_chips()
        self._merge_timer.start()

    def flush_wafer_choice(self) -> None:
        """기다리는 합치기가 있으면 지금 한다(테스트 · 닫기 전)."""
        if self._merge_timer.isActive():
            self._merge_timer.stop()
            self._merge_wafer_choice()

    def _merge_wafer_choice(self) -> None:
        """고른 웨이퍼로 다시 합친다.  뺀 웨이퍼의 꼭 잡기 · Overkill 은 set_wafer 가 함께 버리고(화면에 없는 Defect 로
        추천하지 않게) 자동 추천은 남은 고름으로 다시 계산한다."""
        self._merge_timer.stop()
        if not self._roots or not self._all_ready():
            return
        before = len(self._picked)
        self._remerge()
        lost = before - len(self._picked)
        if lost > 0 and not self._base_reset:
            self._show_notice(_KO.OVERKILL_NOTICE_WAFER_PICKS_LOST.format(n=lost), "info")

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

    def hideEvent(self, event):         # noqa: N802
        drop_clear(self)                # 끌던 중에 숨겨져도 표시가 남지 않게
        super().hideEvent(event)

    def dragEnterEvent(self, e):    # noqa: N802
        paths = local_paths(e.mimeData())       # 디스크는 drop 에서만 본다
        if not paths:
            drop_clear(self)
            e.ignore()
            return
        ok = bool(guess_kinds(paths) & {"folder", "image"})
        drop_feedback(self, e, None, i18n.KO.DROP_PILL_ADD_FOLDER if ok
                      else i18n.KO.DROP_PILL_ONLY_FOLDER_PHOTO, ok)

    def dragMoveEvent(self, e):     # noqa: N802
        self.dragEnterEvent(e)

    def dragLeaveEvent(self, e):    # noqa: N802
        drop_clear(self)
        e.accept()

    def dropEvent(self, e):         # noqa: N802
        drop_clear(self)
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
        same = both and same_base(prev, w)
        self._base_reset = bool(both and not same)
        self._w = w
        self._ov = dict(self._ov) if same else {}
        self._rich_base = {}
        self._drop_cache = {}
        self._picked = ({} if self._base_reset else
                        {key_of(d): d for d in (w.defects if w else ()) if (_folder_of(w, d), d.index) in kept_picks})
        prev_rec = self._rec_zones
        kept_ov = {(_folder_of(prev, d), d.index) for d in self._overkill.values()} if prev else set()
        self._overkill = {key_of(d): d for d in (w.defects if w else ()) if (_folder_of(w, d), d.index) in kept_ov}
        self._rec_zones = {(d.zone_dir, d.zone) for d in self._picked.values()}
        if (self._mode == "auto" and same and w is not None
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

    def _ensure_scans(self, extra=()) -> None:
        """지금 화면(탭의 앞 ``_shown`` 장 + 한 쪽)에 필요한데 아직 안 자른 Crop 만 백그라운드로 자른다.

        한 번에 스레드 하나 — 끝나면(:meth:`_on_scans`) 다시 불러 그 사이 늘어난 필요(더 보기·탭 이동)를 이어서 한다.
        결과는 키로 ``_scans`` 에 쌓여 같은 Defect 를 두 번 자르지 않는다."""
        if self._w is None or self._zone is None:
            return
        if self._scan_thread is not None and self._scan_thread.isRunning():
            return
        want = []
        idx = self._scan_index()
        first = list(extra) or (list(self._pick_extra.values()) if self._pick_dlg is not None else [])
        for d in first:                              # [Defect 고르기] 팝업이 부탁한 것을 먼저
            if self._scan_pending(d) and idx[key_of(d)] not in want:
                want.append(idx[key_of(d)])
        window = 0                                   # 화면에 설 사진(이미 있는 것 포함) 수 — 보이는 장 + 한 쪽까지만 센다
        for d, _why in self._items():
            if self._scan_pending(d):
                if idx[key_of(d)] not in want:
                    want.append(idx[key_of(d)])
            elif self._pic(d) is None:
                continue                             # 사진이 없어 타일이 서지 않는 Defect — 화면 순서에 안 센다
            window += 1
            if window >= self._shown + PAGE_TILES:
                break                                # (예전엔 '못 자른 것' 만 세어 Defect 3000개를 끝까지 이어서 잘랐다 — 측정 3.9 GB)
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
        self._refresh_pick()
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
        self._refresh_pick()
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
        """Defect 가 있거나 기준이 있는 Zone — **레시피 순으로 먼저 나누고**, 그 안에서 Defect 많은 순(현택님 2026-11-23)."""
        w = self._w
        if w is None:
            return []
        keys = {(d.zone_dir, d.zone) for d in w.defects + w.goods}
        keys |= {k for k, z in w.zones.items() if z.bundles}
        return sorted(keys, key=lambda k: (recipe_no(k[0]), -self._count(k), k))

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
    def _sync_empty(self) -> None:
        """폴더가 하나도 없으면 본문 가운데 안내 + [+ 폴더], 폴더 없이는 못 하는 것은 끈다."""
        empty = not self._roots and self._w is None
        for wid in (self.summary, self._tabs_host, self.grid_host, self.empty_label, self.rules,
                    self._side_folder_head, self.pick_btn, self._side_rule):
            wid.setVisible(not empty)
        self.empty_state.setVisible(empty)
        self._main.setStretch(self._main.count() - 1, 0 if empty else 1)
        for b in self.mode_btns.values():
            b.setEnabled(not empty)
        self._zone_head.setVisible(not empty and bool(self.zone_btns))

    def _render(self) -> None:
        w = self._w
        self.reset_btn.setEnabled(self._zone_dirty())
        self.export_btn.setEnabled(bool(self._ov))
        if w is None or w.note:
            self.check_label.setText(w.note if w else "")
        else:
            n = len(w.defects)
            self.check_label.setText((_KO.THRESH_PREVIEW_CHECK_OK if not w.misses else
                                      _KO.THRESH_PREVIEW_CHECK_BAD).format(ok=n - w.misses, n=n))
        self._render_zones()
        self._sync_empty()
        self._render_rules()
        self._render_summary()
        self._render_tiles()
        self._refresh_saved_btn()

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
        self.zone_btns = {}
        self.recipe_heads = {}
        if self._w is None or self._w.note:
            return
        for key in self.zone_keys():
            rec = self._w.label(key)[1]
            if rec and key[0] not in self.recipe_heads:       # 레시피가 여럿이면 레시피 머리줄 아래에 그 레시피 Zone 들
                head = QLabel(recipe_head(key[0], rec), self)
                head.setProperty("role", "ovGroupName")
                head.setContentsMargins(12, 0 if not self.recipe_heads else 12, 0, 2)
                self._zone_col.addWidget(head)
                self.recipe_heads[key[0]] = head
            b = _ZoneRow(self)
            b.setChecked(key == self._zone)
            b.clicked.connect(lambda _c=False, k=key: self._set_zone(k))
            self._zone_group.addButton(b)
            self._zone_col.addWidget(b)
            self.zone_btns[key] = b
            self._zone_text(key)

    def _zone_ticks(self, key) -> list:
        """꼭 잡기 위치(0~1) — 빠지는 Defect 를 앞, 남는 것을 뒤에 놓은 줄에서의 자리(놓친 고름은 빨간 쪽에 놓인다)."""
        zone = self._in_zone(key)
        if not zone or not self._picked:
            return []
        gone = {key_of(d) for d, _why in self.dropped(key)}
        order = [key_of(d) for d in zone if key_of(d) in gone] + [key_of(d) for d in zone if key_of(d) not in gone]
        pos = {k: i for i, k in enumerate(order)}
        return sorted((pos[k] + 0.5) / len(order) for k in self._picked if k in pos)

    def _zone_text(self, key) -> None:
        name, _rec = self._w.label(key)          # 레시피는 머리줄에 있다(:meth:`_render_zones`) — 줄에는 Zone 이름만
        picks = sum(1 for d in self._picked.values() if (d.zone_dir, d.zone) == key)
        self.zone_btns[key].set_data(name, self._count(key),
                                     len(self.dropped(key)), self.appeared(key), picks, self._zone_ticks(key))

    def _render_rules(self) -> None:
        self._clear(self._rules_lay)
        self.sliders = {}
        w = self._w
        if w is None or w.note or self._zone is None:
            self.rules.setVisible(False)
            return
        self.rules.setVisible(True)
        z = w.zones.get(self._zone)
        rows: list = []                                # [(머리 제목, 머리 아래 줄, [칸…])] — 모든 줄을 같은 칸 격자에 맞춘다
        if z is None or not z.bundles:
            lab = QLabel(_KO.THRESH_PREVIEW_NO_FILTER, self.rules)
            lab.setProperty("role", "muted")
            self._rules_lay.addWidget(lab)
        else:
            known = P.contrast_known(w.defects, *self._zone)
            many = len(z.bundles) > 1
            for i, (cls, filters) in enumerate(z.bundles.items(), start=1):
                cells = []
                for feat, name, unit, step, dec in _FEATURES:
                    f = next((x for x in filters if x.feature == feat), None)
                    if f is None or (feat == R.FEATURE_CONTRAST and not known):
                        continue                   # Contrast 를 안 적는 Zone — 슬라이더 없음(현택님 2026-11-16)
                    cells.append(self._feature_cell(cls, f, name, unit, step, dec))
                rows.append((_KO.THRESH_PREVIEW_RULE_N.format(n=i) if many else _KO.THRESH_PREVIEW_RULE,
                             f"→ {self._class_name(cls)}", cells))
        detect = self._detect_cells(z)
        if detect:
            cells, stages = detect
            rows.append((_KO.THRESH_PREVIEW_DETECT_STAGE_HEAD, "→ " + " · ".join(stages), cells))
        ncol = max((len(r[2]) for r in rows), default=0)     # 줄마다 칸 수가 달라도 세로 줄이 맞게 빈 칸으로 채운다
        for i, (title, sub, cells) in enumerate(rows):
            if i:
                rule = QFrame(self.rules)
                rule.setFrameShape(QFrame.Shape.HLine)
                rule.setStyleSheet(f"color: {theme.LINE2};")
                self._rules_lay.addWidget(rule)
            row = QHBoxLayout()
            row.setSpacing(RULE_GAP)
            row.addWidget(self._rule_head(title, sub), 0, Qt.AlignmentFlag.AlignTop)
            for c in cells:
                row.addWidget(c, 1)
            for _ in range(ncol - len(cells)):
                row.addWidget(QWidget(self.rules), 1)
            self._rules_lay.addLayout(row)

    def _rule_head(self, title: str, sub: str) -> QWidget:
        """줄 머리(왼쪽 고정 폭 열) — 제목과 그 아래 한 줄.  길면 줄을 바꿔 잘리지 않게 한다."""
        hw = QWidget(self.rules)
        hw.setFixedWidth(RULE_HEAD_W)
        head = QVBoxLayout(hw)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(2)
        t = QLabel(title, hw)
        t.setStyleSheet("font-weight: 700;")
        t.setWordWrap(True)
        head.addWidget(t)
        if sub:
            s = QLabel(sub, hw)
            s.setProperty("role", "muted")
            s.setWordWrap(True)
            head.addWidget(s)
        return hw

    def _detect_cells(self, z):
        """검출 단계(Dark/Bright) 하한 칸들 — 이 Zone 에 검출 단계 Defect 가 있는 극성만.
        ``(칸들, 극성 이름들)``.  없으면 None.  앱에서 못 바꾸는 검출 값은 화면에 띄우지 않는다(현택님 2026-11-21)."""
        if z is None or not z.detect:
            return None
        mine = [d for d in self._in_zone() if not d.by_filter and d.stage is not None]
        rows = [r for r in _DETECT_ROWS if _shown_limit(z, r[0], r[1]) and any(d.stage == r[0] for d in mine)]
        if not rows:
            return None
        w = self._w
        cells = []
        for stage, kind, ini, name, feat in rows:
            _f, _n, unit, step, dec = next(x for x in _FEATURES if x[0] == feat)
            vals = [w.to_um(feat, P._detect_value(d, kind)) for d in mine if d.stage == stage]
            cells.append(self._limit_cell((*self._zone, stage, kind), (stage, kind), feat, z.detect[(stage, kind)],
                                          name, unit, step, dec, vals, vals, False, ("Surface", ini, "detect")))
        stages = [st.capitalize() for st in ("dark", "bright") if any(r[0] == st for r in rows)]
        return cells, stages

    def _feature_cell(self, cls: int, f: R.Filter, name: str, unit: str, step: float, dec: int) -> QWidget:
        w = self._w
        zone_vals = [w.to_um(f.feature, P._value(d, f.feature)) for d in self._in_zone()]
        vals = [w.to_um(f.feature, P._value(d, f.feature)) for d in self._in_zone() if d.bin == cls]
        if f.feature == R.FEATURE_CONTRAST:
            vals = [v for v in vals if v != 0]
        sec = "Surface Feature Filter"
        ini = (sec, f.name[len(sec):].strip() or name, f"Classify {cls}") if f.name else None
        return self._limit_cell((*self._zone, cls, f.feature), (cls, f.feature), f.feature, f.low, name, unit, step,
                                dec, zone_vals, vals, True, ini)

    def _limit_cell(self, key: tuple, slider_key: tuple, feature: int, low_px: float, name: str, unit: str,
                    step: float, dec: int, zone_vals: list, vals: list, can_down: bool, ini: tuple | None = None) -> QWidget:
        """슬라이더 칸 하나 — Feature Filter 기준과 검출 단계 하한이 함께 쓴다(``key`` 만 다르다)."""
        w = self._w
        rec = w.to_um(feature, low_px)
        # 끝 = 이 Zone 에서 가장 큰 값(그 위로 올리면 전부 빠져 더 볼 게 없다) — 레시피의 2배보다 작으면 2배.
        hi = max(rec * 2 if rec > 0 else rec + 10 * step, max(zone_vals, default=rec))
        steps = max(1, math.ceil((hi - rec) / step))
        hi = rec + steps * step
        # 내리기: RichSurface 후보가 있는 Zone 만.  하한 = 후보 값의 최솟값(그 아래는 파일이 모른다).
        down = 0
        cand = self._rich_cand() if can_down else None
        if cand is not None:
            floor = w.to_um(feature, float(cand[feature].min())) if len(cand[feature]) else rec
            down = max(0, min(math.ceil((rec - max(floor, 0.0)) / step), math.floor(rec / step)))
        lo = rec - down * step
        axis = _Axis(lo, hi, rec, step, dec, down, steps,
                     log=use_log_axis(lo, hi, zone_vals))        # 값이 한쪽에 눌리면 로그 축(분포 · 자름선 · 슬라이더가 같은 대응)
        cell = QWidget(self.rules)
        lay = QVBoxLayout(cell)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        h = QHBoxLayout()                      # 열 머리 한 줄 — 이름 + 값 칸 + 단위 + 변화량
        h.setSpacing(8)
        mono = theme.FONT_MONO
        px_unit = {R.FEATURE_AREA: " px²", R.FEATURE_WIDTH: " px"}.get(feature, "")
        # ini 에 적힌 숫자 = 레시피 자기 픽셀(화면 값은 flt 픽셀로 바꾼 것이라 되돌린다, rules.zones_in_flt_px)
        lin, area = R.recipe_scale(w.folder, self._zone[0])
        ini_px = low_px / {R.FEATURE_AREA: area, R.FEATURE_WIDTH: lin}.get(feature, 1.0)
        px_txt = f"{ini_px:.4f}".rstrip("0").rstrip(".") + px_unit
        ini_lab = None
        if ini is None:
            lab = QLabel(f"{name} ≥", cell)
            lab.setProperty("role", "sumCap")
            h.addWidget(lab)
        else:
            sec, ikey, sub = ini
            # 키 크게, 구간 작게, 단계마다 가는 색 띠(파랑 Feature Filter · 주황 검출 단계 [Surface])
            top = QHBoxLayout()
            top.setSpacing(8)
            bar = QFrame(cell)
            bar.setFixedWidth(3)
            bar.setStyleSheet(f"background: {theme.WARN if sub == 'detect' else theme.ACCENT}; border: none;")
            top.addWidget(bar)
            col = QVBoxLayout()
            col.setSpacing(0)
            # 섹션 이름을 위에, 키를 아래 크게(현택님 2026-11-21).  소분류(Classify N)는 줄 머리 '→ 분류 이름' 이 이미 말한다.
            sl2 = QLabel(f"[{sec}]", cell)
            sl2.setProperty("role", "muted")
            sl2.setStyleSheet(f"font-family: {mono}; font-size: 10px;")
            col.addWidget(sl2)
            lab = QLabel(f"{ikey} ≥", cell)
            lab.setStyleSheet(f"font-family: {mono}; font-size: 15px; font-weight: 700;")
            col.addWidget(lab)
            top.addLayout(col, 1)
            lay.addLayout(top)
            ini_lab = QLabel(f"ini {px_txt}", cell)
            ini_lab.setProperty("role", "muted")
            ini_lab.setStyleSheet(f"font-family: {mono}; font-size: 10px;")
        spin = QLineEdit(cell)                 # 위·아래 버튼 없이 숫자만 친다 — 조금씩은 슬라이더로
        val = QDoubleValidator(0.0, 1e9, dec, spin)   # 범위 밖 값도 받아서 apply 가 끝으로 맞춘다
        val.setNotation(QDoubleValidator.Notation.StandardNotation)
        val.setLocale(QLocale.c())
        spin.setValidator(val)
        spin.setProperty("role", "axisField")
        spin.setFixedSize(64, 32)
        spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(spin)
        if unit:
            u = QLabel(unit, cell)
            u.setProperty("role", "muted")
            h.addWidget(u)
        if ini_lab is not None:
            h.addWidget(ini_lab)
        delta = QLabel("", cell)
        delta.setProperty("role", "axisDelta")
        h.addSpacing(4)
        h.addWidget(delta)
        h.addStretch(1)
        lay.addLayout(h)
        hist = _Hist(vals, lo, hi, cell, axis)
        lay.addWidget(hist)
        sl = NoWheelSlider(Qt.Orientation.Horizontal, cell)
        sl.setProperty("role", "axis")
        sl.setFixedHeight(16)
        sl.setRange(0, axis.n)
        lay.addWidget(sl)
        mono = theme.FONT_MONO.split(",")[0].strip('" ')
        rec_text = (_KO.THRESH_PREVIEW_RECIPE_VALUE.format(v=f'<span style="font-family: {mono};">{rec:.{dec}f}</span>')
                    + (f"   {_KO.THRESH_PREVIEW_LOWEST.format(v=f'{lo:.{dec}f}')}" if down else ""))
        foot = _AxisFoot(rec_text, f"{hi:.{dec}f}", axis, cell)
        lay.addWidget(foot)
        lay.addStretch(1)                      # 로그 축 칸만 눈금 글자 줄이 더 길다 — 위로 붙여 옆 칸과 머리·분포 높이를 맞춘다

        cur = w.to_um(feature, self._ov[key]) if key in self._ov else rec

        def show(v: float) -> None:
            spin.setText(f"{v:.{dec}f}")
            if abs(axis.value_at(sl.value()) - v) > 1e-9:       # 끄는 중 손잡이가 한 칸 되튀지 않게 — 이미 그 값이면 건드리지 않는다
                sl.blockSignals(True)
                sl.setValue(axis.index_of(v))
                sl.blockSignals(False)
            hist.set_cut(v)
            d = v - rec
            delta.setText(f"+{d:.{dec}f}" if d > 1e-9 else f"−{-d:.{dec}f}" if d < -1e-9 else "")
            delta.setProperty("tone", "over" if d > 0 else "ok")
            delta.style().unpolish(delta)
            delta.style().polish(delta)

        def apply(v: float, final: bool) -> None:
            v = min(max(v, lo), hi)
            if abs(v - rec) <= 1e-9:
                self._ov.pop(key, None)
            else:
                self._ov[key] = w.to_px(feature, v)
            show(v)
            self._changed(final)

        sl.valueChanged.connect(lambda i: apply(axis.value_at(i), False))
        sl.sliderReleased.connect(lambda: apply(axis.value_at(sl.value()), True))
        def typed() -> None:
            # 커서만 넣었다 빼도 editingFinished 가 온다 — 화면에 적힌 글자 그대로면 바꾼 게 아니다.
            # (적힌 글자는 화면 자리로 반올림돼 레시피 값 37.96 이 38.0 으로 박혀 그 사이 Defect 가 빠졌다.)
            now = w.to_um(feature, self._ov[key]) if key in self._ov else rec
            if spin.text().strip() == f"{now:.{dec}f}":
                return
            try:
                apply(float(spin.text().replace(",", "")), True)
            except ValueError:
                show(axis.value_at(sl.value()))  # 못 읽으면 지금 값으로 되돌린다

        spin.editingFinished.connect(typed)
        show(cur)
        self.sliders[slider_key] = (sl, spin)
        return cell

    def _changed(self, final: bool) -> None:
        self.reset_btn.setEnabled(self._zone_dirty())
        self.export_btn.setEnabled(bool(self._ov))
        self._zone_text(self._zone)
        drop = self._render_summary()
        # 끄는 동안은 다시 그릴 사진이 적을 때만 바로 바꾼다 — [남음] 탭에 사진 1,110장이면 한 칸마다 0.7 초씩 멈춰
        # 손잡이 · 화면이 깨져 보였다(실측 2026-11-21).  많으면 손을 뗄 때 한 번만 그린다.
        if final or (drop <= LIVE_TILES_MAX and min(len(self._items()), self._shown) <= LIVE_TILES_MAX):
            self._open = None if self._tab != "good" else self._open
            self._render_tiles()

    @staticmethod
    def _tone(label: QLabel, tone: str) -> None:
        label.setProperty("tone", tone)
        label.style().unpolish(label)
        label.style().polish(label)

    def _render_summary(self) -> int:
        w = self._w
        if w is None or w.note or self._zone is None:
            self.summary.setVisible(False)
            self._tabs_host.setVisible(False)
            self.drop_value.setText("0")
            self.new_box.setVisible(False)
            self.keep_label.setText("")
            self.all_label.setText("")
            for b in self.tab_btns.values():
                b.setVisible(False)
            self.pick_label.setText("")
            self.miss_label.setText("")
            self.pick_box.setVisible(False)
            self.miss_box.setVisible(False)
            self.ov_box.setVisible(False)
            self.pick_open_btn.setEnabled(False)
            return 0
        self.summary.setVisible(True)
        self._tabs_host.setVisible(True)
        self.reset_btn.setEnabled(self._zone_dirty())
        n = len(self._in_zone())
        drop = len(self.dropped())
        self.drop_value.setText(f"{drop:,}")
        self._tone(self.drop_value, "over" if drop else "none")
        self.new_box.setVisible(self._rich_cand() is not None)
        new = self.appeared()
        self.new_value.setText(f"{new:,}")
        self._tone(self.new_value, "ok" if new else "none")
        self.keep_label.setText(f"{n - drop:,}")
        self.all_label.setText(f"{n:,}")
        self.ratio.set_values(drop, n, self._zone_ticks(self._zone))
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
        self.pick_label.setText(f"{total_picks:,}" if total_picks else "")
        self.pick_box.setVisible(bool(total_picks))
        self.miss_label.setText(f"{len(missed):,}" if missed else "")
        self.miss_box.setVisible(bool(missed))
        self.pick_open_btn.setVisible(auto)
        self.pick_open_btn.setEnabled(True)
        self.saved_btn.setVisible(auto)
        marked = len(self._overkill)
        self.ov_label.setText(f"{self._overkill_gone(self._ov)}/{marked}" if marked else "")
        self.ov_box.setVisible(auto and bool(marked))
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
        avail = max(TILE_PX, self.grid_host.width() or self.width() - SIDE_W - 24)
        return max(1, (avail + self.grid.spacing()) // (TILE_PX + self.grid.spacing()))

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
            self._refresh_pick()
            return
        cache.put(_tile_key(path, size), pm)
        for t in self.tiles.values():
            if t.wait_path is not None and str(t.wait_path) == str(path):
                t.set_pixmap(pm)
        self._refresh_pick()

    def _render_tiles(self) -> None:
        """사진 격자를 다시 그린다 — 다 만들 때까지 칠하지 않아 지우고 채우는 중간 모습이 비치지 않게 한다."""
        top = self.window()
        frozen = top.updatesEnabled()
        top.setUpdatesEnabled(False)
        try:
            self._render_tiles_now()
        finally:
            top.setUpdatesEnabled(frozen)

    def _render_tiles_now(self) -> None:
        self._wanted = set(self._pick_wanted)   # 화면이 바뀌었다 — 줄 서 있던 것 중 이번 화면에 없는 건 건너뛴다
        self._detail_pic = None
        self._clear(self.grid)
        self.tiles = {}
        self._tile_defects = {}
        self._row_of, self._col_of, self._tile_why = {}, {}, {}
        self._tile_rows = []                           # 화면에 보이는 줄 순서대로 [키, …] — 방향키 이동용
        self._band = self._band_row = None
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
        total: dict = {}
        for d, _why in with_photo:
            total[self._class_name(d.bin)] = total.get(self._class_name(d.bin), 0) + 1
        groups: dict = {}                              # 분류 이름 → 타일들 — 이름은 격자 위에 한 번만
        for d, why in shown:
            groups.setdefault(self._class_name(d.bin), []).append((d, why))
        mono = theme.FONT_MONO.split(",")[0].strip('" ')      # 리치텍스트 style 속성에는 목록 대신 첫 이름만
        for gi, (cname, rows) in enumerate(groups.items()):
            head = QLabel(f'{html.escape(cname)}<span style="font-family: {mono}; font-weight: 400;'
                          f' color: {theme.MUTE};">  {total[cname]:,}</span>', self.grid_host)
            head.setProperty("role", "ovGroupName")
            head.setTextFormat(Qt.TextFormat.RichText)
            head.setContentsMargins(0, 0 if gi == 0 else 24, 0, 8)
            self.grid.addWidget(head)
            for start in range(0, len(rows), cols):
                row = QWidget(self.grid_host)           # 타일 한 줄 — 펼친 값 띠는 이 줄 바로 아래 끼운다
                row.setFixedHeight(TILE_PX)
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 0, 0, 0)
                rl.setSpacing(TILE_GAP)
                opened = None
                self._tile_rows.append([])
                for c, (d, why) in enumerate(rows[start:start + cols]):
                    k = key_of(d)
                    tile = _Tile(self._pic(d), TILE_PX, row, picked=k in self._picked,
                                 tip=f"{cname}  {self._why_text(d, why)}".strip())
                    tile.clicked.connect(lambda i=k: self._toggle(i))     # 두 모드 모두 값 펼치기 — 꼭 잡기는 [Defect 고르기] 팝업에서
                    tile.enlarge.connect(lambda i=k: self._enlarge_tile(i))
                    tile.set_open(self._open == k)
                    if tile.wait_path is not None:
                        self._want(tile.wait_path, TILE_PX, "thumb")
                    rl.addWidget(tile)
                    self.tiles[k] = tile
                    self._tile_defects[k] = d
                    self._tile_why[k] = why
                    self._row_of[k], self._col_of[k] = row, c
                    self._tile_rows[-1].append(k)
                    if self._open == k:
                        opened = k
                rl.addStretch(1)
                self.grid.addWidget(row)
                if opened is not None:                   # 펼친 값은 그 줄 바로 아래(다시 그릴 때는 모션 없이 바로)
                    self._band = self._new_band(opened)
                    self._band_row = row
                    self.grid.addWidget(self._band)
        if len(with_photo) > self._shown:
            more = NeonButton(_KO.THRESH_PREVIEW_MORE.format(n=f"{len(with_photo) - self._shown:,}"), role="ghost")
            more.clicked.connect(self._more)
            self.grid.addWidget(more, 0, Qt.AlignmentFlag.AlignLeft)
        # ★ 끝에 늘어나는 빈 칸을 둔다 — 없으면 띠가 접히고 펼쳐지는 동안(레이아웃 합 < 아직 안 줄어든 호스트 높이)의 남는 높이가
        #   위쪽 줄 · 제목에 나뉘어 눌린 타일이 아래위로 출렁였다(실측: 눌린 줄이 한 프레임에 최대 35 px).
        self.grid.addStretch(1)
        self._ensure_scans()

    def _caret_x(self, k) -> float:
        """고른 타일 가운데 x — 줄은 왼쪽부터 같은 간격으로 놓여 칸 번호만으로 정해진다."""
        return self._col_of[k] * (TILE_PX + TILE_GAP) + TILE_PX / 2

    def _new_band(self, k) -> _DetailBand:
        return _DetailBand(self._detail(self._tile_defects[k], self._tile_why[k]), self._caret_x(k), self.grid_host)

    def _detail(self, d: P.Defect, why) -> QWidget:
        """타일 줄 바로 아래 펼쳐지는 값 — 옅은 강조색 면 + 위아래 1px 선(상자 · 굵은 테두리 없음).  :class:`_DetailBand` 가 감싼다."""
        w = self._w
        wrap = QWidget(self.grid_host)
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 8)
        wl.setSpacing(0)
        box = QFrame(wrap)
        box.setProperty("role", "ovDetail")
        wl.addWidget(box)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 16, 0, 16)
        lay.setSpacing(24)
        pic = _BigPhoto(box)
        pic.setFixedSize(DETAIL_PX, DETAIL_PX)
        pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pic.setCursor(Qt.CursorShape.PointingHandCursor)
        photo = self._pic(d)
        pic.setProperty("role", "tilePic")
        if isinstance(photo, QImage):                   # Scan Crop — Defect 자리를 빨간 박스로(상세 보기에서만)
            pic.setPixmap(self._scan_pixmap(photo, d))
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
        if photo is None:
            pic.setCursor(Qt.CursorShape.ArrowCursor)
        else:                                           # Color = 누르거나 우클릭 [크게보기], Scan Crop = 우클릭(원본 위 범위 표시)
            pic.enlarge.connect(lambda i=key_of(d): self._enlarge_tile(i))
            if not isinstance(photo, QImage):
                pic.clicked.connect(lambda: self._enlarge(photo, d))
            else:
                pic.setCursor(Qt.CursorShape.ArrowCursor)
        lay.addWidget(pic, 0, Qt.AlignmentFlag.AlignTop)
        right = QVBoxLayout()
        right.setSpacing(8)
        name, rec = w.label((d.zone_dir, d.zone))
        top = QHBoxLayout()
        top.setSpacing(8)
        t = QLabel(self._class_name(d.bin), box)
        t.setStyleSheet("font-weight: 700;")
        top.addWidget(t)
        is_scan = isinstance(photo, QImage)
        sub = QLabel(f"Zone {name}" + (f" · {rec}" if rec else "")
                     + (f" · {w.folders[d.wafer].name}" if len(w.folders) > 1 else "")
                     + (f" · {_KO.OVERKILL_SCAN_TAG}" if is_scan else ""), box)
        sub.setProperty("role", "muted")
        top.addWidget(sub, 1)
        k = key_of(d)
        close = NeonButton(_KO.OVERKILL_DETAIL_CLOSE, role="textAction")
        close.clicked.connect(lambda _c=False, i=k: self._toggle(i))
        top.addWidget(close)
        right.addLayout(top)
        self._add_verdict(right, box, d, why)
        if not d.by_filter:                             # 검출 단계가 잡은 Defect — Feature Filter 가 아니라 검출 하한이 거른다
            z = w.zones.get((d.zone_dir, d.zone))
            fixed = d.stage is None or z is None or not any(_shown_limit(z, d.stage, k) for k in ("area", "width", "length"))
            note = QLabel((_KO.OVERKILL_NOT_BY_FILTER_FIXED if fixed else _KO.OVERKILL_NOT_BY_FILTER)
                          .format(name=self._stage_name(d)), box)
            note.setProperty("role", "muted")
            note.setWordWrap(True)
            right.addWidget(note)
        right.addStretch(1)
        src = photo.text("src") if is_scan else ""          # Scan 원본 파일(잘라 올 때 적어 둔다)
        fn = QLabel(f"…/{Path(src).name}" if src else ("" if photo is None or is_scan else f"…/{photo.name}"), box)
        fn.setProperty("role", "ovFile")
        if src or (photo is not None and not is_scan):
            fn.setToolTip(src or str(photo))
        fn.setVisible(bool(fn.text()))
        fn.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        foot = QHBoxLayout()
        foot.setSpacing(12)
        if is_scan:                                     # 빨간 상자는 Scan Crop 에만 있다
            tog = QPushButton(_KO.OVERKILL_DEFECT_BOX, box)
            tog.setProperty("role", "ovBoxToggle")
            tog.setCheckable(True)
            tog.setChecked(self._show_box)
            tog.setCursor(Qt.CursorShape.PointingHandCursor)
            tog.setToolTip(_KO.OVERKILL_DEFECT_BOX_TIP)
            tog.toggled.connect(lambda on, p=pic, im=photo, dd=d: self._set_box(on, p, im, dd))
            foot.addWidget(tog)
            self.detail_box_btn = tog
        foot.addWidget(fn, 1)
        right.addLayout(foot)
        lay.addLayout(right, 1)
        return wrap

    def _scan_pixmap(self, crop: QImage, d: P.Defect) -> QPixmap:
        pix = _pixmap(crop, DETAIL_PX, kind="mid")
        if self._show_box:
            self._mark_defect(pix, crop, d)
        return pix

    def _set_box(self, on: bool, pic: QLabel, crop: QImage, d: P.Defect) -> None:
        """'Defect 표시' — 지금 상세 그림을 다시 그리고, 고른 값은 다음 상세에도 이어진다."""
        self._show_box = bool(on)
        pic.setPixmap(self._scan_pixmap(crop, d))

    def _stage_name(self, d: P.Defect) -> str:
        """검출 단계 Defect 의 결과 이름 — EquipmentClassifications 의 이름(예: Area Dark).  못 읽으면 RT2D 이름 · 비트."""
        rt = d.rtype or 0
        names = [n for bit, (n, _t) in R.surface_result_types(self._w.folder).items() if bit > 0 and rt & bit]
        return " · ".join(names) or (d.stage or str(rt))

    def _detect_criteria(self, d: P.Defect) -> list:
        """검출 단계 Defect 의 칩 — 이 극성의 Area · Diameter 하한(µm), 지금 값으로 걸리는지."""
        w = self._w
        z = w.zones.get((d.zone_dir, d.zone))
        out = []
        for stage, kind, _ini, name, feat in _DETECT_ROWS:
            ok = P.detect_ok(d, z, kind, self._ov) if stage == d.stage else None
            if ok is None or not _shown_limit(z, stage, kind):
                continue
            rec = z.detect[(stage, kind)]
            cut = self._ov.get((d.zone_dir, d.zone, stage, kind), rec)
            _f, _n, unit, _s, dec = next(x for x in _FEATURES if x[0] == feat)
            out.append(Criterion(name, w.to_um(feat, P._detect_value(d, kind)), w.to_um(feat, cut),
                                 w.to_um(feat, rec), dec, "≥", ok, unit))
        return out

    def _criteria(self, d: P.Defect, why) -> list:
        """이 Defect 의 판정이 걸린 묶음의 기준들 — 빠졌으면 빠뜨린 묶음, 남았으면 전부 통과한 묶음(자기 분류 먼저).

        ``preview.why_dropped`` 와 같은 규칙이다: 묶음 안은 전부(AND), 묶음끼리는 하나라도(OR) 통과하면 남는다.
        파일에 값이 없는 기준과 Contrast 0('안 잼')은 빼고, 기준값은 슬라이더로 바꾼 값을 쓴다."""
        w = self._w
        if not d.by_filter:
            return self._detect_criteria(d)
        z = w.zones.get((d.zone_dir, d.zone))
        if z is None or not z.bundles:
            return []

        def active(cls):
            out = []
            for f in z.bundles[cls]:
                v = P._value(d, f.feature)
                if v is None or (f.feature == R.FEATURE_CONTRAST and v == 0):
                    continue
                low = self._ov.get((d.zone_dir, d.zone, cls, f.feature), f.low)
                out.append((f, v, low, type(f)(f.feature, f.ftype, low, max(low, f.high)).passes(v)))
            return out

        if why is not None:
            cls = why[0]
        else:
            order = sorted(z.bundles, key=lambda c: c != d.bin)
            cls = next((c for c in order if all(ok for *_x, ok in active(c))), None)
            if cls is None:
                return []
        crits = []
        for f, v, low, ok in active(cls):
            fname, unit, dec = next(((x[1], x[2], x[4]) for x in _FEATURES if x[0] == f.feature), ("", "", 1))
            crits.append(Criterion(fname, w.to_um(f.feature, v), w.to_um(f.feature, low), w.to_um(f.feature, f.low), dec,
                                   filter_op(f.ftype), ok, unit))
        return crits

    def _add_verdict(self, right: QVBoxLayout, box: QWidget, d: P.Defect, why) -> None:
        """기준마다 칩 하나 — 이름 · 이 Defect 의 값(크게, 지금 기준에 걸리면 빨강) · 레시피 기준 · (옮겼으면) 조정 기준."""
        crits = self._criteria(d, why)
        if not crits:
            return
        row = QHBoxLayout()
        row.setSpacing(8)
        self.detail_chips = []
        for cr in crits:
            chip = QFrame(box)
            chip.setProperty("role", "ovChip")
            cl = QHBoxLayout(chip)
            cl.setContentsMargins(12, 6, 12, 6)
            cl.setSpacing(10)
            for text, role in ((cr.name, "ovChipName"), (f"{cr.value:.{cr.dec}f}", "ovChipValue"),
                               *(((cr.unit, "ovChipName"),) if cr.unit else ()),
                               (_KO.THRESH_PREVIEW_RECIPE_VALUE.format(v=f"{cr.op} {cr.recipe:.{cr.dec}f}"), "ovChipRef"),
                               *(((_KO.OVERKILL_CHIP_ADJUSTED.format(v=f"{cr.op} {cr.cut:.{cr.dec}f}"), "ovChipRef"),)
                                 if cr.adjusted else ())):
                lab = QLabel(text, chip)
                lab.setProperty("role", role)
                if role == "ovChipValue":
                    lab.setProperty("fail", not cr.ok)
                cl.addWidget(lab)
            row.addWidget(chip)
            self.detail_chips.append(chip)
        row.addStretch(1)
        right.addLayout(row)

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
        """타일을 눌러 값 띠를 펼치고 접는다 — 타일은 다시 만들지 않고 띠만 줄 사이에 끼우고 뺀다(썸네일이 깜박이지 않게)."""
        if not isinstance(self.focusWidget(), (QLineEdit, QAbstractSlider)):
            self.setFocus(Qt.FocusReason.MouseFocusReason)      # 이어서 방향키로 다른 사진을 볼 수 있게
        old = self._open
        self._open = None if old == index else index
        self._show_open(old, self._open)

    def move_current(self, dx: int, dy: int) -> bool:
        """방향키 — 펼친(현재) 타일을 격자에서 그 방향으로 옮긴다.  왼쪽 · 오른쪽은 줄을 넘어 이어지고(맨 끝에서는 멈춤),
        위 · 아래는 같은 칸 번호(그 줄이 짧으면 맨 끝)로 간다.  아무것도 안 펼쳤으면 첫 사진.  옮겼으면 True."""
        rows = [r for r in self._tile_rows if r]
        if not rows:
            return False
        cur = self._open
        pos = next(((ri, ci) for ri, r in enumerate(rows) for ci, k in enumerate(r) if k == cur), None)
        if pos is None:
            new = rows[0][0]
        elif dx:
            flat = [k for r in rows for k in r]
            j = flat.index(cur) + (1 if dx > 0 else -1)
            if not 0 <= j < len(flat):
                return False
            new = flat[j]
        else:
            ri = pos[0] + (1 if dy > 0 else -1)
            if not 0 <= ri < len(rows):
                return False
            new = rows[ri][min(pos[1], len(rows[ri]) - 1)]
        if new == cur:
            return False
        self._open = new
        area = self._outer_scroll()
        if area is not None:
            motion.ensure_visible_animated(area, area.widget(), self.tiles[new])   # 옮긴 사진이 화면 밖이면 보이게 스크롤
        self._show_open(cur, new)
        return True

    def keyPressEvent(self, e):         # noqa: N802
        """방향키로 사진 목록의 현재 사진을 옮긴다 — 슬라이더 · 값 칸이 키를 쓰는 동안은 그쪽이 먼저다(그 위젯이 받아 여기까지 안 온다)."""
        step = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}.get(e.key())
        if (step is not None and not e.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
                and self._pick_dlg is None and not isinstance(self.focusWidget(), QAbstractSlider)):
            self.move_current(*step)
            e.accept()
            return
        super().keyPressEvent(e)

    def _outer_scroll(self) -> Optional[QScrollArea]:
        """이 화면이 들어 있는 가장 가까운 바깥 스크롤 영역(앱에서는 설정 화면의 holder).  없으면 None."""
        w = self.parentWidget()
        while w is not None and not isinstance(w, QScrollArea):
            w = w.parentWidget()
        return w

    def _show_open(self, old, new) -> None:
        """펼친 타일이 ``old`` → ``new`` 로 바뀐 것을 화면에 옮긴다.  모션이 꺼진(헤드리스) 때는 모두 바로 적용된다."""
        if new is not None and new not in self.tiles:
            self._render_tiles()                       # 보이는 타일이 아니다 — 처음부터 다시 그린다
            return
        for k in {old, new} - {None}:
            t = self.tiles.get(k)
            if t is not None:
                t.set_open(k == new)
        band = self._band
        if new is None:
            self._close_band(band)
            return
        row = self._row_of[new]
        if band is None:
            self._open_band(new, row)
        elif self._band_row is row:                    # 같은 줄 — 띠 높이는 그대로, 내용만 흐려 바꾸고 삼각형만 미끄러진다
            band.swap_content(self._detail(self._tile_defects[new], self._tile_why[new]))
            band.move_caret(self._caret_x(new))
        else:                                          # 다른 줄 — 띠를 한 번에 옮긴다(접히고 펼쳐지는 두 모션이 겹치면 번쩍였다, 현택님 2026-11-21)
            self._move_band(band, new, row)

    def _move_band(self, band: _DetailBand, k, row: QWidget) -> None:
        """펼친 띠를 다른 줄 아래로 한 프레임에 옮긴다 — 누른 타일은 화면에서 제자리, 내용만 짧게 흐려 바뀐다.
        띠가 위에 있었으면 빠진 높이만큼 스크롤을 되돌리고, 새 띠가 화면 밖으로 나가면 그때만 부드럽게 스크롤한다."""
        area = self._outer_scroll()
        above = self.grid.indexOf(band) < self.grid.indexOf(row)
        shift = band.height() + self.grid.spacing()
        top = self.window()
        frozen = top.updatesEnabled()
        top.setUpdatesEnabled(False)                   # 빼고 끼우는 중간 모습이 칠해지지 않게
        try:
            self._close_band(band, instant=True)
            new = self._new_band(k)
            self.grid.insertWidget(self.grid.indexOf(row) + 1, new)
            self._band, self._band_row = new, row
            new.show()
            _settle_layout()
            if above and area is not None:
                bar = area.verticalScrollBar()
                bar.setValue(bar.value() - shift)
        finally:
            top.setUpdatesEnabled(frozen)
        if motion.enabled():
            motion.fade_in(new.content, duration=motion.DUR_TILE_TOGGLE)
        call_later(self, 0, self._reveal_band)

    def _open_band(self, k, row: QWidget) -> None:
        band = self._new_band(k)
        self.grid.insertWidget(self.grid.indexOf(row) + 1, band)
        self._band, self._band_row = band, row
        band.show()
        h = band.sizeHint().height()
        if motion.enabled():
            motion.animate_height(band, 0, h, duration=motion.DUR_ROW_RESIZE, on_tick=_settle_layout)
            motion.fade_in(band.content, duration=motion.DUR_TILE_IN)
            anim = getattr(band, "_motionHeightAnim", None)
            if anim is not None:                       # 다 펼쳐진 뒤 띠가 보이게 스크롤(그 전에는 아직 자리가 모자라 끝이 잘린다)
                anim.finished.connect(self._reveal_band, Qt.ConnectionType.SingleShotConnection)
                return
        call_later(self, 0, self._reveal_band)

    def _reveal_band(self) -> None:
        band = self._band
        area = self._outer_scroll()
        if band is not None and area is not None:
            motion.ensure_visible_animated(area, area.widget(), band)

    def _close_band(self, band: Optional[_DetailBand], instant: bool = False) -> None:
        if band is None:
            return
        if self._band is band:
            self._band = self._band_row = None
        h = band.height()
        if instant or not motion.enabled() or h <= 0 or not band.isVisible():
            band.dispose()
            return
        motion.animate_height(band, h, 0, duration=motion.DUR_ROW_SHRINK, easing=motion.EASE_EXIT,
                              on_tick=_settle_layout)
        band._motionHeightAnim.finished.connect(band.dispose, Qt.ConnectionType.SingleShotConnection)

    def _more(self) -> None:
        self._shown += PAGE_TILES * 2
        self._render_tiles()

    def _enlarge(self, photo: Path, d: Optional[P.Defect] = None) -> None:
        """Color 사진 크게보기.  ``d`` 를 주면 그 Defect 의 Scan 이 있는지 알리고 [Scan image 보기] 를 단다."""
        from .zoom_window import FullscreenViewer
        viewer = FullscreenViewer(photo, self, scan_slot=d is not None)
        if d is not None:
            self._probe_scan(viewer, d)
        sheets.run(viewer, full_bleed=True)

    def _probe_scan(self, viewer, d: P.Defect) -> None:
        """뷰어에 이 Defect 의 Scan Crop 을 알린다 — 이미 잘라 둔 것은 바로, 아니면 뒤에서 찾는다(UI 를 막지 않는다)."""
        idx = self._scan_index().get(key_of(d))
        if idx is None:                                    # 이 웨이퍼 폴더에는 Scan 목록이 없다
            viewer.set_scan_crop(None)
            return
        crop = self._scans.get(idx[0])
        if crop is not None:
            viewer.set_scan_crop(crop)
            return
        th = _ScanProbe(idx)
        th.signals.done.connect(viewer.set_scan_crop)       # 뷰어가 닫혀도 안전하다 — 묶인 메서드는 위젯이 죽으면 끊긴다
        th.start()

    def _enlarge_tile(self, k) -> None:
        """우클릭 [크게보기] — Color 사진은 바로, Scan Crop 은 Scan 원본 위에 Crop 범위를 그려(뒤에서 읽어) 연다."""
        d = self._tile_defects.get(k)
        if d is None and self._w is not None:
            d = next((x for x in self._w.defects + self._w.goods if key_of(x) == k), None)
        photo = self._pic(d) if d is not None else None
        if photo is None:
            return
        if not isinstance(photo, QImage):
            self._enlarge(photo, d)
            return
        idx = self._scan_index().get(key_of(d))
        if idx is None:
            self._on_full_scan(photo, None, None, "")
            return
        th = _FullScanLoad(photo, idx[1], idx[2], photo.text("src"))
        th.signals.done.connect(self._on_full_scan)
        self._full_scan = th
        th.start()

    def _on_full_scan(self, crop: QImage, full, box, path: str) -> None:
        """Scan 원본을 읽었다 — 원본 + 범위, 못 읽었으면 Crop 그 자체를 크게 본다."""
        self._full_scan = None
        from .zoom_window import FullscreenViewer
        if full is not None and box is not None:
            viewer = FullscreenViewer(Path(path), self, image=full, overlay=box)
        else:
            viewer = FullscreenViewer(Path(crop.text("src") or _KO.OVERKILL_SCAN_TAG), self, image=crop)
        sheets.run(viewer, full_bleed=True)

    def _zone_dirty(self) -> bool:
        """지금 Zone 에 바꾼 값이나 고른 Defect 가 있다 — [레시피 값으로 되돌리기] 를 켜는 기준."""
        z = self._zone
        return z is not None and (any(k[:2] == z for k in self._ov)
                                  or any((d.zone_dir, d.zone) == z for d in self._picked.values()))

    def reset(self) -> None:
        """레시피 값으로 되돌리기 — **지금 Zone 만**.  그 Zone 의 바꾼 값 · 꼭 잡기 · Overkill 을 지운다(다른 Zone 은 그대로)."""
        z = self._zone
        if z is None:
            return
        had = any((d.zone_dir, d.zone) == z for d in (*self._picked.values(), *self._overkill.values()))
        self._ov = {k: v for k, v in self._ov.items() if k[:2] != z}
        self._picked = {k: d for k, d in self._picked.items() if (d.zone_dir, d.zone) != z}
        self._overkill = {k: d for k, d in self._overkill.items() if (d.zone_dir, d.zone) != z}
        self._rec_zones.discard(z)
        self._open = None
        self._save_picks({z} if had else set())   # 고른 것 없이 값만 되돌렸으면 저장분은 그대로
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
            d = self._picked.pop(k)
        else:
            d = next((x for x in self._w.defects if key_of(x) == k), None)
            if d is None:
                return
            self._picked[k] = d
        self._recommend()
        self._save_picks({(d.zone_dir, d.zone)})
        self._render_zones()
        self._render_rules()
        self._changed(True)

    def _recommend(self) -> None:
        """고른 것이 있는(또는 있던) Zone 만 다시 계산한다 — 다른 Zone 의 직접 조정은 그대로 둔다."""
        rec, _missed = P.recommend(list(self._picked.values()), self._w.zones, self._snap)
        self._merge_recommended(rec)

    def _merge_recommended(self, rec: dict) -> None:
        """``P.recommend`` 결과를 직접 조정 값(`_ov`)에 넣는다 — 지금 고른 것의 Zone 만 바뀐다."""
        zones_now = {(d.zone_dir, d.zone) for d in self._picked.values()}
        for k in [k for k in self._ov if k[:2] in zones_now | self._rec_zones]:
            del self._ov[k]
        self._ov.update(rec)
        self._rec_zones = zones_now

    # ------------------------------------------------------------------ 고른 것 저장 · 불러오기
    def _wafer_folders(self) -> list:
        """``[(웨이퍼 순번, 폴더, 그 웨이퍼의 Defect 수)]`` — 저장한 index 가 아직 맞는지 보는 데 쓴다."""
        w = self._w
        if w is None:
            return []
        folders = w.folders or (w.folder,)
        return [(i, f, sum(1 for d in w.defects + w.goods if d.wafer == i)) for i, f in enumerate(folders)]

    def _save_picks(self, touched=frozenset()) -> None:
        """지금 고른 것(꼭 잡기 · Overkill)을 폴더별 · Zone 별로 남긴다 — 이 PC 안에서만(:mod:`...threshold.saved_picks`).

        ``touched`` = 이번에 사용자가 고른 것을 바꾼 Zone.  저장분은 **Zone 단위로** 고친다: 지금 고른 것이 있는 Zone 과
        ``touched`` Zone 만 지금 것으로 바꾸고, 나머지 Zone 의 저장분은 그대로 둔다 — [저장한 선택 불러오기] 를
        누르기 전에 다른 Zone 을 고르거나 값만 되돌렸다고 예전에 저장한 선택이 지워지지 않게."""
        for i, folder, n in self._wafer_folders():
            zones: dict = {}
            for which, src in ((0, self._picked), (1, self._overkill)):
                for d in src.values():
                    if d.wafer == i:
                        zones.setdefault((d.zone_dir, d.zone), (set(), set()))[which].add(d.index)
            kept = {z: v for z, v in SP.load(folder, n).items() if z not in touched and z not in zones}
            SP.save(folder, n, {**kept, **zones})
        self._refresh_saved_btn()

    def _saved_picks(self) -> tuple[dict, dict]:
        """저장해 둔 것 → ``(꼭 잡기, Overkill)`` — 각 ``{키: Defect}``.  지금 폴더에 없는 Defect 는 뺀다."""
        w = self._w
        picked: dict = {}
        over: dict = {}
        if w is None:
            return picked, over
        by_idx = {(d.wafer, d.index): d for d in w.defects}
        for i, folder, n in self._wafer_folders():
            for (zdir, zid), (pk, ov) in SP.load(folder, n).items():
                for idxs, out in ((pk, picked), (ov, over)):
                    for idx in idxs:
                        d = by_idx.get((i, idx))
                        if d is not None and (d.zone_dir, d.zone) == (zdir, zid):
                            out[key_of(d)] = d
        return picked, over

    def _refresh_saved_btn(self) -> None:
        picked, over = self._saved_picks()
        self.saved_btn.setEnabled(bool(picked or over))

    def load_saved_picks(self) -> None:
        """[저장한 선택 불러오기] — 저장해 둔 꼭 잡기 · Overkill 을 지금 것에 더하고 추천값을 다시 계산한다."""
        picked, over = self._saved_picks()
        if self._w is None or not (picked or over):
            return
        self._picked.update(picked)
        self._overkill.update(over)
        self._recommend()
        self._render_zones()
        self._render_rules()
        self._changed(True)
        self._save_picks()

    # ------------------------------------------------------------------ Defect 고르기 팝업
    def _on_mode_clicked(self, key: str) -> None:
        """모드 버튼을 눌렀다 — 자동 추천으로 처음 넘어왔는데 고른 것이 없으면 [Defect 고르기] 를 한 번 바로 연다."""
        prev = self._mode
        self.set_mode(key)
        if key == "auto" and prev != "auto" and not self._picked:
            self.open_pick_dialog()

    def open_pick_dialog(self) -> None:
        """[Defect 고르기] — 지금 Zone 의 사진을 Overkill ← → Defect 로 가르는 팝업.  [적용] 해야 값이 바뀐다."""
        w = self._w
        if w is None or w.note or self._pick_dlg is not None:
            return
        zones = []
        for k in self.zone_keys():
            name, rec = w.label(k)
            zones.append((k, f"{name}" + (f"  · {rec}" if rec else "")))
        dlg = OverkillPickDialog(zones=zones, zone=self._zone, items_of=self._pick_items, key_of=key_of,
                                 pixmap_of=self._pick_pixmap, request_pics=self._pick_request,
                                 caption_of=self._pick_caption, picked=self._picked, overkill=self._overkill,
                                 preview=self._pick_preview,
                                 enlarge=lambda d: self._enlarge_tile(key_of(d)), parent=self)
        dlg.applied.connect(self._on_pick_applied)
        dlg.finished.connect(self._on_pick_closed)
        self._pick_dlg = dlg
        sheets.run(dlg, full_bleed=True)          # 창 전체로 — 큰 사진이 자리를 다 쓴다

    def _on_pick_closed(self, _code: int = 0) -> None:
        dlg, self._pick_dlg = self._pick_dlg, None
        self._pick_wanted = set()
        self._pick_extra = {}
        if dlg is not None:
            dlg.deleteLater()

    def _refresh_pick(self) -> None:
        if self._pick_dlg is not None and not self._pick_refresh.isActive():
            self._pick_refresh.start()

    def _refresh_pick_now(self) -> None:
        if self._pick_dlg is not None:
            self._pick_dlg.refresh_pics()

    def _pick_items(self, zone_key) -> list:
        """팝업 목록 — 그 Zone 의 Defect 중 사진이 있거나 Scan Crop 을 자르는 중인 것(타일과 같은 기준, 폴더 칩은 따지지 않는다)."""
        return [d for d in self._in_zone(zone_key) if self._pic(d) is not None or self._scan_pending(d)]

    def _pick_pixmap(self, d: P.Defect, size: int):
        photo = self._pic(d)
        if photo is None:
            return None
        if isinstance(photo, QImage):
            return _pixmap(photo, size)
        key = _tile_key(photo, size) if size <= Sizing.THUMB_PX else _mid_key(photo, size)
        return image_io._get_tile_cache().get(key)

    def _pick_request(self, ds, size: int) -> None:
        kind = "thumb" if size <= Sizing.THUMB_PX else "mid"
        for d in ds:
            photo = self._pic(d)
            if isinstance(photo, QImage):
                continue
            if photo is not None:
                self._want(photo, size, kind)
                self._pick_wanted.add((str(photo), int(size), kind))
            elif self._scan_pending(d):
                self._pick_extra[key_of(d)] = d
        if self._pick_extra:
            self._ensure_scans(list(self._pick_extra.values()))

    def _pick_caption(self, d: P.Defect) -> str:
        parts = [self._class_name(d.bin)]
        for feat, name, unit, _step, dec in _FEATURES:
            v = P._value(d, feat)
            if v is None or (feat == R.FEATURE_CONTRAST and v == 0):
                continue
            parts.append(f"{name} {self._w.to_um(feat, v):.{dec}f}" + (f" {unit}" if unit else ""))
        return " · ".join(parts)

    def _overrides_for(self, picked) -> dict:
        """``_recommend`` 가 만들 값 — 상태는 건드리지 않는다(팝업 미리보기용)."""
        zones_now = {(d.zone_dir, d.zone) for d in picked}
        ov = {k: v for k, v in self._ov.items() if k[:2] not in zones_now | self._rec_zones}
        ov.update(P.recommend(picked, self._w.zones, self._snap)[0])
        return ov

    def _pick_preview(self, picked, overkill) -> tuple:
        """적용하면 → ``(새로 빠지는 Defect 수(전체 Zone), 그중 Overkill 로 고른 것, 고른 Defect 중 놓치는 것)``."""
        w = self._w
        ov = self._overrides_for(list(picked))
        gone = {key_of(d) for d in w.defects if not P.is_kept(d, w.zones, ov)}
        return (len(gone), sum(1 for d in overkill if key_of(d) in gone),
                sum(1 for d in picked if key_of(d) in gone))

    def _overkill_gone(self, ov: dict) -> int:
        """Overkill 로 고른 Defect 중 ``ov`` 기준으로 빠지는 수."""
        return sum(1 for d in self._overkill.values() if not P.is_kept(d, self._w.zones, ov))

    def _on_pick_applied(self, picked: dict, overkill: dict) -> None:
        """팝업 [적용] — 고른 Defect · Overkill 를 받아 추천값을 다시 계산하고 화면을 다시 그린다."""
        if self._w is None:
            return
        self._pick_touched = {(d.zone_dir, d.zone) for src in (self._picked, self._overkill, picked, overkill)
                              for d in src.values()}     # 팝업에서 비운 Zone 도 — 그 Zone 저장분을 지운다
        self._picked = dict(picked)
        self._overkill = dict(overkill)
        # 고른 것이 많으면 추천 계산이 길다 — 로딩을 띄우고 계산은 백그라운드에서(UI 스레드를 잡지 않는다).
        self._pick_token += 1
        token = self._pick_token
        self._loading.show_overlay(_KO.THRESH_PREVIEW_APPLYING)
        self._loading.set_progress(0, 0, _KO.THRESH_PREVIEW_APPLYING)     # 양을 모르니 busy — 0 에 멈추지 않는다
        self._overlay_on = True
        self._loading.repaint()
        zones, snap, chosen = self._w.zones, self._snap, list(self._picked.values())

        def work() -> None:
            rec = None
            try:
                rec = P.recommend(chosen, zones, snap)[0]
            except Exception:
                _LOG.exception("Defect 고르기 적용 계산 실패")
            self._pick_bus.done.emit(token, rec)

        threading.Thread(target=work, name="overkill-apply", daemon=True).start()

    def _on_pick_computed(self, token: int, rec) -> None:
        """GUI 스레드 — 계산이 끝났다.  추천값을 반영하고 화면을 다시 그린 뒤 로딩을 걷는다."""
        if token != self._pick_token:
            return                                   # 그 사이 다시 적용했다 — 옛 결과
        try:
            if self._w is not None and rec is not None:
                self._merge_recommended(rec)
                self._save_picks(self._pick_touched)
            if self._w is not None:
                self._render_zones()
                self._render_rules()
                self._changed(True)
        finally:
            if self._overlay_on:
                self._loading.hide_overlay()
                self._overlay_on = False

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
        for (zdir, zid, cls, feat), px in sorted(self._ov.items(), key=lambda kv: tuple(map(str, kv[0]))):
            z = w.zones.get((zdir, zid))
            if z is not None and isinstance(cls, str):          # 검출 단계 하한
                if (cls, feat) not in z.detect:
                    continue
                fno = R.FEATURE_AREA if feat == "area" else R.FEATURE_WIDTH
                _f, _n, unit, _step, dec = next(x for x in _FEATURES if x[0] == fno)
                name, rec = w.label((zdir, zid))
                vals = [w.to_um(fno, P._detect_value(d, feat)) for d in w.defects
                        if (d.zone_dir, d.zone, d.stage) == (zdir, zid, cls) and not d.by_filter]
                changes.append(RP.Change(name + (f" · {rec}" if rec else ""), _KO.THRESH_PREVIEW_DETECT_STAGE,
                                         detect_label(cls, feat), unit, w.to_um(fno, z.detect[(cls, feat)]),
                                         w.to_um(fno, px), dec, tuple(vals)))
                continue
            if z is None or cls not in z.bundles:
                continue
            f = next((x for x in z.bundles[cls] if x.feature == feat), None)
            meta = next((x for x in _FEATURES if x[0] == feat), None)
            if f is None or meta is None:
                continue
            _feat, item, unit, _step, dec = meta
            name, rec = w.label((zdir, zid))
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
            name, rec = w.label(key)
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
        if isinstance(cls, str):                        # 검출 단계 — (극성, 'area'|'width'|'length')
            z = self._w.zones.get((d.zone_dir, d.zone))
            cut = self._ov.get((d.zone_dir, d.zone, cls, feat), z.detect.get((cls, feat), 0.0) if z else 0.0)
            return P._detect_value(d, feat) / cut if 0 < cut < math.inf else 0.0
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
        if isinstance(why[0], str):
            feat = R.FEATURE_AREA if why[1] == "area" else R.FEATURE_WIDTH
            dec = next(x[4] for x in _FEATURES if x[0] == feat)
            return f"{detect_label(*why)} {self._w.to_um(feat, P._detect_value(d, why[1])):.{dec}f}"
        feat = why[1]
        dec = next(x[4] for x in _FEATURES if x[0] == feat)
        return f"{next(x[1] for x in _FEATURES if x[0] == feat)[0]} {self._w.to_um(feat, P._value(d, feat)):.{dec}f}"

    def showEvent(self, e):         # noqa: N802
        super().showEvent(e)
        call_later(self, 80, self._drop_initial_focus)

    def _drop_initial_focus(self) -> None:
        """열리자마자 첫 버튼(모드 토글)이 포커스 링을 달고 나오지 않게 — 사용자가 누르거나 Tab 으로 가면 그때부터다."""
        fw = self.focusWidget()
        if isinstance(fw, QPushButton) and not fw.underMouse():
            fw.clearFocus()

    def resizeEvent(self, e):       # noqa: N802
        super().resizeEvent(e)
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
