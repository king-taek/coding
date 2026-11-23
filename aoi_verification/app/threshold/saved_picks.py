"""자동 추천에서 사용자가 고른 것(꼭 잡기 · Overkill)을 웨이퍼 폴더별 · Zone 별로 이 PC 에 남긴다.

``<캐시 폴더>/overkill_picks.json`` 한 파일.  [Defect 고르기] 에서 [적용] 할 때 자동 저장하고, 화면의
[저장한 선택 불러오기] 로 다시 읽는다(사용자 요청 2026-11-20).  이 PC 안에서만 쓴다 — 서버로 보내지 않는다.

Defect 는 ``Surface.flt`` 안 순서(``Defect.index``)로 가리키므로 그 파일의 Defect 수(``n``)가 저장 때와 다르면
(다시 스캔한 폴더) 쓰지 않는다.  못 읽거나 못 써도 raise 하지 않는다(검증 흐름을 막지 않는다).

형식: ``{폴더 키: {"n": Defect 수, "zones": {"Zones|1": {"picked": [index…], "overkill": [index…]}}}}``
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from ..utils import paths

__all__ = ["folder_key", "load", "load_many", "save", "save_many"]

_LOG = logging.getLogger("aoi.threshold")
_FILE = "overkill_picks.json"


def _path() -> Path:
    return paths.cache_root() / _FILE


_KEYS: dict = {}       # 폴더 → 키 — resolve() 는 NAS 를 한 번 다녀오므로 폴더마다 한 번만(화면을 그릴 때마다 웨이퍼 수만큼 불렸다)


def folder_key(folder) -> str:
    """같은 폴더를 어떻게 열어도 같은 키 — 대소문자(윈도) · 상대 경로를 맞춘다.

    resolve 가 성공한 것만 기억한다 — NAS 가 잠깐 끊겨 못 맞춘 키를 남기면 그 폴더의 저장분이 화면을 닫을 때까지 안 보인다."""
    p = Path(folder)
    key = _KEYS.get(p)
    if key is not None:
        return key
    try:
        key = os.path.normcase(str(p.resolve()))
    except OSError:
        return os.path.normcase(str(p))
    _KEYS[p] = key
    return key


def _read_all() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load(folder, n_defects: int) -> dict:
    """``{(zone_dir, zone): ({꼭 잡기 index}, {Overkill index})}`` — 저장한 것이 없거나 Defect 수가 다르면 빈 dict."""
    return _entry(_read_all(), folder, n_defects)


def load_many(items) -> list:
    """``[(폴더, Defect 수)]`` → 폴더마다 :func:`load` 결과 — 파일은 한 번만 읽는다(웨이퍼마다 다시 읽고 풀었다)."""
    allv = _read_all()
    return [_entry(allv, folder, n) for folder, n in items]


def _entry(allv: dict, folder, n_defects: int) -> dict:
    entry = allv.get(folder_key(folder))
    if not isinstance(entry, dict) or entry.get("n") != n_defects:
        return {}
    out: dict = {}
    for zk, v in (entry.get("zones") or {}).items():
        try:
            zdir, zid = str(zk).rsplit("|", 1)
            out[(zdir, int(zid))] = ({int(i) for i in v.get("picked", ())}, {int(i) for i in v.get("overkill", ())})
        except (ValueError, AttributeError, TypeError):
            continue
    return out


def save(folder, n_defects: int, zones: dict) -> None:
    """이 폴더의 저장분을 ``zones``(``{(zone_dir, zone): (picked, overkill)}``)로 바꾼다.  비어 있으면 그 폴더 항목을 지운다."""
    save_many([(folder, n_defects, zones)])


def save_many(items) -> None:
    """``[(폴더, Defect 수, zones)]`` — 폴더마다 :func:`save` 와 같게 바꾸되 파일은 한 번 읽고 (바뀐 게 있을 때만) 한 번 쓴다."""
    allv = _read_all()
    changed = False
    for folder, n_defects, zones in items:
        zones = {k: (sorted(p), sorted(o)) for k, (p, o) in zones.items() if p or o}
        key = folder_key(folder)
        if zones:
            allv[key] = {"n": n_defects, "zones": {f"{zd}|{zi}": {"picked": p, "overkill": o}
                                                   for (zd, zi), (p, o) in zones.items()}}
            changed = True
        elif key in allv:
            del allv[key]
            changed = True
    if not changed:
        return
    p = _path()
    tmp = p.with_name(p.name + ".tmp")
    try:
        tmp.write_text(json.dumps(allv, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        _LOG.warning("자동 추천 선택 저장 실패: %s", e)
