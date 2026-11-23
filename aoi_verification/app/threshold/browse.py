"""'어느 Zone에서 왔니?' — 웨이퍼 폴더의 Defect 를 분류 정보와 함께 모으고, 골라 보기(facet)를 센다.

Overkill 튜닝과 **같은 파일 · 같은 규칙**으로 읽는다(현택님 2026-11-23 — 정보는 이미 앱이 읽고 있다):

  · Surface Defect = :func:`preview.load_all` 그대로(첫 분류 · 지금 분류 · EDC Good · 검출 단계).
  · Height Defect = :mod:`coords.height_flt`(3D 레시피).  사진은 Height 레코드의 ``x`` · ``y`` 와 짝짓는다
    (사진 정보 화면 :mod:`coords.geometry` 와 같은 규칙 — ActualX/Y 는 약 69 µm 어긋났다, 관측 2026-11-23).
  · 분류 이름 = ``ManReClassify.ini`` (:func:`rules.read_classes`), 검출 종류 = ``EquipmentClassifications.ini``
    (:mod:`coords.class_names` · :func:`rules.surface_result_types`), Zone 이름 = Zone ini, 레시피 이름 = ``RecipesInfo.ini``.
  · 크기는 µm 로 바꿔 둔다 — Overkill 튜닝 정렬값과 같은 환산(면적 = px_x·px_y, 폭 · 길이 = px_x).  픽셀 크기를
    못 읽으면 크기는 비워 둔다(0.77 같은 추측값을 쓰지 않는다).

**분류 이름과 검출 종류는 다른 축이다** — 'B R Other' 는 크기 · Contrast 기준 묶음에 붙은 이름표라 실제 모양과
다를 수 있다(B R Other ↔ Dark 조사, 2026-11-23).  그래서 둘을 따로 고르게 한다.

좌표(col · row)는 여기서 계산하지 않는다 — 사진이 있으면 화면이 사진 정보(:mod:`coords.single_info`)로 보여 준다.
전 구간 fail-safe — 못 읽은 것은 ``note`` 에 적고 raise 하지 않는다.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .. import i18n
from ..coords import abs_coord, camtek_ini, class_names
from ..coords import height_flt as H
from ..coords.geometry import GEOMETRY_MATCH_TOL
from ..coords.pixel_size import scan_pixel_size_xy
from ..coords import scan_image as SI
from ..models.lot_info import read_active_slot
from ..models.slot import _list_images
from . import preview as P
from . import rules as R

__all__ = ["Item", "WaferItems", "Flags", "FACETS", "load_folder", "facet_counts", "sort_items", "SORT_KEYS",
           "KIND_SURFACE", "KIND_HEIGHT"]

_LOG = logging.getLogger("aoi.browse")
_KO = i18n.KO

KIND_SURFACE, KIND_HEIGHT = "surface", "height"
SURFACE_PHOTO_TYPE = 0      # 사진 정보 파일 Type — EquipmentClassifications [SURFACE] 의 -1 값(파일, class_names 와 같은 규칙)

# 골라 보는 칸 — (Item 속성, 화면 이름).  순서 = 화면 왼쪽 위→아래.
FACETS = (("first_name", _KO.ZO_FACET_CLASS), ("type_name", _KO.ZO_FACET_TYPE), ("caught", _KO.ZO_FACET_CAUGHT),
          ("recipe", _KO.ZO_FACET_RECIPE), ("zone", _KO.ZO_FACET_ZONE), ("wafer", _KO.ZO_FACET_WAFER))

# 정렬 — (키, 화면 이름).  값이 없는 Defect 는 늘 맨 뒤.
SORT_KEYS = (("area", "Area"), ("width", "Width"), ("length", "Length"), ("contrast", "Contrast"),
             ("height", "Height"), ("wafer", _KO.ZO_SORT_WAFER))


@dataclass(frozen=True)
class Item:
    """Defect 하나 — 화면이 고르고 보여 주는 값만."""
    wafer: int                  # WaferItems 목록 안 몇 번째 웨이퍼인가(화면이 붙인다)
    index: int                  # 그 결과 파일 안 순서(Surface · Height 따로)
    kind: str                   # KIND_SURFACE | KIND_HEIGHT
    recipe: str                 # 레시피 이름(모르면 '레시피 N')
    zone: str                   # Zone 이름(모르면 'Zone N')
    first: int                  # 첫 분류 번호(장비가 붙인 것)
    first_name: str             # '29 · B R Surface Dark'
    cur: int                    # 지금 분류 번호(사람이 검토로 바꾸면 음수 — 이름은 절댓값의 것)
    cur_name: str
    good: bool                  # 첫 분류가 Good(EDC Good) — 기본으로는 숨긴다(Overkill 튜닝과 같다)
    type_name: str              # 검출 종류(EquipmentClassifications 이름)
    caught: str                 # 잡힌 곳: 기준 묶음 · 검출 단계 Dark/Bright · Height
    area: Optional[float]       # µm²
    width: Optional[float]      # µm
    length: Optional[float]     # µm
    contrast: Optional[float]   # 0 = 안 잼 → None
    height: Optional[float]     # µm (Height 만)
    x: float                    # 사진과 짝지은 좌표(µm) — Scan Crop 을 찾을 때 쓴다
    y: float
    photo: Optional[Path] = None

    @property
    def changed(self) -> bool:
        """검토로 분류가 바뀌었나(Accept 포함)."""
        return abs(self.cur) != self.first


@dataclass
class WaferItems:
    folder: Path
    items: tuple = ()
    note: str = ""              # 못 읽은 이유(비면 정상)
    slot: str = ""              # 카세트 슬롯(WaferInfo.ini ActiveSlot) — 모르면 빈 글자
    cap: int = 0                # 장비 저장 상한에 닿았으면 그 상한(Defect 가 일부만 있다), 아니면 0
    has_scan: bool = False      # Scan 목록이 있다 — 상세에서 Scan Crop 을 찾을 수 있다


def _name(classes: dict, no: int) -> str:
    c = classes.get(abs(int(no)))
    return _KO.ZO_CLASS_FMT.format(no=abs(int(no)), name=c.name) if c is not None and c.name else \
        _KO.ZO_CLASS_UNKNOWN.format(no=abs(int(no)))


def _recipe_no(zone_dir: str) -> int:
    return int(zone_dir[len("Recipe"):-len("-Zones")]) if zone_dir.startswith("Recipe") else 1


def _type_label(folder: Path, photo_type: int, bits, surface_types: dict) -> str:
    """결과 비트 → 이름.  사전에 딱 맞는 이름이 있으면 그것, 비트가 여럿이면 켜진 이름들, 그래도 없으면 'Type N'."""
    if bits is None:
        return _KO.ZO_UNKNOWN
    exact = class_names.type_name(folder, photo_type, int(bits))
    if exact:
        return exact
    if photo_type == SURFACE_PHOTO_TYPE:
        names = [n for b, (n, _t) in sorted(surface_types.items()) if b > 0 and int(bits) & b]
        if names:
            return " · ".join(names)
    return _KO.ZO_TYPE_FMT.format(bits=int(bits))


def _caught(d: P.Defect, has_mask: bool) -> str:
    if not has_mask:
        return _KO.ZO_UNKNOWN               # 결과 비트 사전이 없으면 어느 단계인지 모른다 — 지어내지 않는다
    if d.by_filter:
        return _KO.ZO_CAUGHT_FILTER
    return {"dark": _KO.ZO_CAUGHT_DARK, "bright": _KO.ZO_CAUGHT_BRIGHT}.get(d.stage or "", _KO.ZO_CAUGHT_DETECT)


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def load_folder(folder: Path, wafer: int = 0, report: Callable = lambda msg, done=0, total=0: None) -> WaferItems:
    """웨이퍼 폴더 하나 → :class:`WaferItems`(워커 스레드에서).  ``wafer`` = 화면이 그 폴더에 붙인 번호(Item.wafer)."""
    folder = Path(folder)
    w = WaferItems(folder, slot=read_active_slot(folder) or "", has_scan=(folder / SI.SCAN_LIST_NAME).is_file())
    try:
        defects, goods, note = P.load_all(folder)
        if note:
            w.note = note
            return w
        cap = P.wafer_cap(folder)
        if cap and len(defects) + len(goods) >= cap:
            w.cap = cap
        classes = R.read_classes(folder)
        zones = R.read_zones(folder)
        names = R.read_recipe_names(folder)
        surface_types = R.surface_result_types(folder)
        has_mask = R.event_mask(folder) is not None
        px = scan_pixel_size_xy(folder)
        pxx, pxy = (float(px[0]), float(px[1])) if px else (0.0, 0.0)
        heights = H.load_folder(folder)

        report(_KO.ZO_LOADING_PHOTOS)
        photos = _list_images(folder)
        types = camtek_ini.load_type_folder(folder)
        h_photos = [p for p in photos if types.get(p.stem.lower()) == H.HEIGHT_PHOTO_TYPE]
        s_photos = [p for p in photos if types.get(p.stem.lower()) != H.HEIGHT_PHOTO_TYPE]
        everyone = defects + goods
        s_pic = P.match_photos(everyone, s_photos, abs_coord.absolute_xy, GEOMETRY_MATCH_TOL)

        def zone_of(zone_dir: str, zid: int) -> tuple:
            z = zones.get((zone_dir, zid))
            n = _recipe_no(zone_dir)
            return (names.get(n) or _KO.ZO_RECIPE_FMT.format(n=n),
                    (z.name if z is not None and z.name else _KO.ZO_ZONE_FMT.format(n=zid)))

        good_set = {n for n, c in classes.items() if c.good}
        out = []
        for d in everyone:
            rec, zname = zone_of(d.zone_dir, d.zone)
            ok = pxx > 0
            out.append(Item(
                wafer, d.index, KIND_SURFACE, rec, zname, d.bin, _name(classes, d.bin), d.cls, _name(classes, d.cls),
                abs(d.bin) in good_set,
                _type_label(folder, SURFACE_PHOTO_TYPE, d.type_bits, surface_types), _caught(d, has_mask),
                d.area * pxx * pxy if ok else None, d.width * pxx if ok else None, d.length * pxx if ok else None,
                d.contrast or None, None, d.x, d.y, s_pic.get(d.index)))

        if heights:
            hs = [_HRec(i, r.x, r.y) for i, r in enumerate(heights)]
            h_pic = P.match_photos(hs, h_photos, abs_coord.absolute_xy, GEOMETRY_MATCH_TOL)
            for i, r in enumerate(heights):
                cur = int(r.reclassify) if r.reclassify is not None else 0
                first = abs(int(r.org)) if r.org else abs(cur)
                zd = R.zone_dir_for(r.recipe)
                rec, zname = zone_of(zd, int(r.zone))
                out.append(Item(
                    wafer, i, KIND_HEIGHT, rec, zname, first, _name(classes, first), cur, _name(classes, cur),
                    first in good_set,
                    _type_label(folder, H.HEIGHT_PHOTO_TYPE, r.type, surface_types), _KO.ZO_CAUGHT_HEIGHT,
                    None, None, None, None, _num(r.height), float(r.x), float(r.y), h_pic.get(i)))
        w.items = tuple(out)
        if not px:
            w.note = _KO.ZO_NO_PIXEL       # 크기 칸만 비었다 — 분류로 고르기는 된다
    except Exception as e:      # noqa: BLE001 — 한 장 실패가 LOT 전체를 막지 않는다
        _LOG.exception("어느 Zone에서 왔니? 읽기 실패: %s", folder)
        w.note = _KO.ZO_READ_FAIL.format(err=e)
    return w


@dataclass(frozen=True)
class _HRec:
    """Height 레코드를 :func:`preview.match_photos` 에 넘기는 모양(index · x · y)."""
    index: int
    x: float
    y: float


@dataclass
class Flags:
    """고르기 칸 밖의 조건 — 모든 칸에 같이 걸린다."""
    changed: bool = False       # 검토로 분류가 바뀐 것만
    photo: bool = False         # 사진 있는 것만
    good: bool = False          # EDC Good 도 보기
    min_area: float = 0.0       # µm²
    min_length: float = 0.0     # µm

    def ok(self, it: Item) -> bool:
        if it.good and not self.good:
            return False
        if self.changed and not it.changed:
            return False
        if self.photo and it.photo is None:
            return False
        if self.min_area > 0 and (it.area is None or it.area < self.min_area):
            return False
        if self.min_length > 0 and (it.length is None or it.length < self.min_length):
            return False
        return True


def facet_counts(items, sel: dict, flags: Flags) -> tuple[list, dict]:
    """``(고른 조건을 다 통과한 Item 들, {칸: Counter(값 → 수)})``.

    칸마다 숫자는 **그 칸 자신의 고름은 빼고** 센다(다른 칸에서 무엇을 고르면 이 칸 숫자가 줄어든다 — 흔한 쇼핑몰 필터).
    한 번만 훑는다: 다 통과하면 모든 칸에 세고, 딱 한 칸에서만 걸리면 그 칸에만 센다."""
    keys = [k for k, _ in FACETS]
    counts = {k: Counter() for k in keys}
    passed = []
    for it in items:
        if not flags.ok(it):
            continue
        miss = None
        two = False
        for k in keys:
            s = sel.get(k)
            if s and getattr(it, k) not in s:
                if miss is not None:
                    two = True
                    break
                miss = k
        if two:
            continue
        if miss is None:
            passed.append(it)
            for k in keys:
                counts[k][getattr(it, k)] += 1
        else:
            counts[miss][getattr(it, miss)] += 1
    return passed, counts


def sort_items(items, key: str, desc: bool = True) -> list:
    """정렬 — 값이 없는 것은 방향과 상관없이 맨 뒤, 같은 값은 원래 순서."""
    if key == "wafer":
        return sorted(items, key=lambda it: (it.wafer, it.kind, it.index))
    def k(it):
        v = getattr(it, key, None)
        return (v is None, 0.0 if v is None else (-v if desc else v))
    return sorted(items, key=k)
