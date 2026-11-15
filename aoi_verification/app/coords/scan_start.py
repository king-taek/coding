"""두 Scanresult 스캔의 **번호 출발점 차이** — 매칭 계산 전용(사용자 결정 A 2026-11-15 '번호 그대로, 매칭만 맞춤').

장비 번호는 ``ProductInfo.ini [ExportData]`` 상자에서 센다(``col = i − cMin``, ``row = rMax − j``,
:func:`.wafer_geometry._export_origins`).  고객 맵을 들여온 스캔은 그 맵을 stage 에 겹쳐 두는데
(``WaferMapRecipe.ini [Input_Update]`` ``Δ = RefPoint1 − CustPoint1``, 맵 칸 = stage − Δ), 보통은
상자가 곧 그 맵이라(``cMin = Δc``, ``rMin = Δr``) 같은 웨이퍼를 두 번 스캔해도 번호가 같다.
QORVO 6H7ENK CTX(6412, 장비 108150)는 상자가 맵보다 넓어 같은 die 가 다른 스캔보다 (1, 2) 큰 번호로 찍혔다
(장비 화면 · Export 파일, 관측 2026-11-15; 맵을 들여온 스캔 101 중 3 이 그랬다).

스캔마다 출발점 ``S = (Δc − cMin, rMax − Δr)`` 를 구하면 고객 맵 칸(아래부터)은 ``장비 번호 − S + (0, ROWCT − 1)`` 이라
두 스캔의 차이 ``S_val − S_ref`` 만큼 검증 쪽 번호를 빼면 같은 die 가 같은 번호가 된다(ROWCT 는 지워진다).
보통 스캔끼리는 차이가 0 이다 — 그래서 다른 제품은 바뀌지 않는다.

★ **매칭용 사본에만 쓴다**(:func:`apply_start_shift` 는 새 dict 를 돌려준다) — 화면 · 엑셀 · Wafer map 번호는 장비 번호 그대로.
★ 출발점을 믿는 조건(하나라도 어긋나면 ``None`` → 그 슬롯은 손대지 않는다):
  die 격자를 폴더 파일에서 읽었고(TB500 상수 폴백 · 블록 제품 아님), ``Enable=1 · PointsSet=1 · Mirror=0 · ImportFlippedMap=0``,
  ``ShiftDieX/Y`` 가 0(또는 없음), 두 기준점 쌍의 Δ 가 같다.  맵을 들이지 않은 스캔(EDGE 레시피 ``Enable=0``)은
  RefPoint 를 다시 맞추지 않아 Δ 가 의미가 없다(관측 2026-11-15 — CTX 와 CTX-EDGE 가 같은 웨이퍼에서 (1,1) 달랐다).
Conder(LIVE 파일명)에는 이 파일이 없어 해당 없다 — 거기서는 :func:`.camtek_live.lot_display_shift` 가 맞춘다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Tuple

from .ini_text import read_ini_text
from .wafer_geometry import camtek_geometry

__all__ = ["scan_start", "start_shift", "apply_start_shift"]

_LOG = logging.getLogger(__name__)
_RECIPE = "WaferMapRecipe.ini"
_SECTION = "Input_Update"
_MUST = {"Enable": 1, "PointsSet": 1, "Mirror": 0, "ImportFlippedMap": 0}
_ZERO_IF_PRESENT = ("ShiftDieX", "ShiftDieY")
_POINT_KEYS = tuple(f"{p}{k}.{a}" for k in (1, 2) for p in ("RefPoint", "CustPoint") for a in ("col", "row"))


def _ints(text: str, section: str) -> Dict[str, int]:
    """``[section]`` 의 정수 값만.  섹션 이름은 대소문자를 가리지 않는다."""
    out: Dict[str, int] = {}
    inside = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            inside = s[1:].split("]", 1)[0].strip().lower() == section.lower()
            continue
        m = re.fullmatch(r"([\w.]+)\s*=\s*(-?\d+)", s) if inside else None
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


def _map_delta(folder: Path) -> Optional[Tuple[int, int]]:
    """고객 맵 겹침 ``Δ = RefPoint1 − CustPoint1`` — 믿을 조건이 아니면 ``None``."""
    text = read_ini_text(folder / _RECIPE)
    if text is None:
        return None
    kv = _ints(text, _SECTION)
    whole = _ints(text, "ImportInfo")
    if any(kv.get(k) != v for k, v in _MUST.items()) or any(k not in kv for k in _POINT_KEYS):
        return None
    if any(whole.get(k, 0) != 0 for k in _ZERO_IF_PRESENT):
        return None
    d1 = (kv["RefPoint1.col"] - kv["CustPoint1.col"], kv["RefPoint1.row"] - kv["CustPoint1.row"])
    d2 = (kv["RefPoint2.col"] - kv["CustPoint2.col"], kv["RefPoint2.row"] - kv["CustPoint2.row"])
    return d1 if d1 == d2 else None


@lru_cache(maxsize=256)
def scan_start(folder: Path) -> Optional[Tuple[int, int]]:
    """이 웨이퍼 폴더 스캔의 번호 출발점 ``S = (Δc − col_origin, row_total − Δr)`` — 믿을 수 없으면 ``None``.

    ``col_origin``·``row_total`` 은 앱이 **실제로 쓴** 번호 기준이다(번호표면 ``cMin``·``rMax``).  어느 규칙으로 정했든
    앱 번호 = stage 칸 − 기준이고 고객 맵 칸 = stage 칸 − Δ 라, 둘의 차이가 곧 출발점이다(stage 칸은 ``DieStep`` 으로 센다)."""
    try:
        geom = camtek_geometry(folder)
        if geom is None or geom.source.startswith("fallback") or geom.block is not None:
            return None
        delta = _map_delta(folder)
        if delta is None:
            return None
        return (delta[0] - geom.col_origin, geom.row_total - delta[1])
    except Exception:
        _LOG.warning("번호 출발점을 읽지 못했습니다 — 이 폴더는 보정하지 않습니다: %s", folder, exc_info=True)
        return None


def _side_start(folders: Iterable[Path]) -> Optional[Tuple[int, int]]:
    starts = {scan_start(f) for f in folders}
    return next(iter(starts)) if len(starts) == 1 else None    # 폴더가 없거나 · 모르는 폴더가 있거나 · 서로 다르면 None


def start_shift(ref_folders: Iterable[Path], val_folders: Iterable[Path]) -> Tuple[int, int]:
    """검증 쪽 번호에서 뺄 값 ``S_val − S_ref``.  한쪽이라도 정하지 못하면 ``(0, 0)``(손대지 않음)."""
    ref_folders, val_folders = set(ref_folders), set(val_folders)
    if not ref_folders or not val_folders:
        return (0, 0)
    sr, sv = _side_start(ref_folders), _side_start(val_folders)
    if sr is None or sv is None:
        if sr != sv:
            _LOG.info("번호 출발점을 한쪽만 알아 보정하지 않습니다: 기준 %s · 검증 %s", sr, sv)
        return (0, 0)
    return (sv[0] - sr[0], sv[1] - sr[1])


def apply_start_shift(coord_cache: dict, tasks,
                      rotated: Optional[Callable[[Path], bool]] = None,
                      ) -> Tuple[dict, Dict[str, Tuple[int, int]]]:
    """슬롯마다 :func:`start_shift` 를 검증 쪽 Scanresult(``camtek_ini``) 좌표에서 뺀 **새 사본**과 적용한 값.

    ``tasks`` 는 ``(slot, refs, vals)`` — 항목에 ``.path`` 가 있다.  양쪽 다 Scanresult 좌표인 사진만 센다.
    ``rotated(path)`` 가 참인 사진(notch 회전 대상)이 한 장이라도 있는 슬롯은 손대지 않는다 — 회전은 폴더의
    원래 번호 기준으로 잡은 웨이퍼 중심에서 번호를 다시 매기므로(:func:`.notch.rotate_coord`), 옮긴 좌표를 돌리면
    차이만큼 거꾸로 어긋난다."""
    out = coord_cache
    applied: Dict[str, Tuple[int, int]] = {}

    def folders(items):
        return {it.path.parent for it in items
                if getattr(coord_cache.get(it.path), "source", None) == "camtek_ini"}

    for slot, refs, vals in tasks:
        dc, dr = start_shift(folders(refs), folders(vals))
        if (dc, dr) == (0, 0):
            continue
        if rotated is not None and any(rotated(it.path) for it in (*refs, *vals)):
            _LOG.info("슬롯 %s: notch 회전 대상이라 번호 출발점 차이 (%d, %d) 는 맞추지 않습니다", slot, dc, dr)
            continue
        if out is coord_cache:
            out = dict(coord_cache)
        for v in vals:
            c = coord_cache.get(v.path)
            if c is not None and c.source == "camtek_ini":
                out[v.path] = replace(c, col=c.col - dc, row=c.row - dr)
        applied[slot] = (dc, dr)
        _LOG.warning("슬롯 %s: 두 스캔의 번호 출발점이 (%d, %d) 달라 매칭에서만 맞춥니다", slot, dc, dr)
    return out, applied
