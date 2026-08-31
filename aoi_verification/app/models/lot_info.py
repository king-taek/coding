"""자재·Layer 이름 — 장비가 쓴 ``WaferInfo.ini`` 에서 읽는다.

결과 엑셀의 **추천 파일 제목**을 만드는 데 쓴다(사용자 요청):

    "장비Layer_자재검증(장비기준)"  →  `4F-AOI-03 RDL4_GFW 검증(AOI-24 기준).xlsx`

실물 근거(`docs/WaferInfo.ini`)::

    [AutoCycleInfo]
    InputLot=TBD-PIDS3          ← 자재 = TBD, Layer = PIDS3

⚠ **``Params_WaferInfo.ini`` 와 다른 파일이다.**  이름이 비슷하지만 그쪽은 die
  pitch·웨이퍼 중심 같은 기하가 들어 있고(`coords/wafer_geometry.py` 가 읽는다),
  `InputLot` 은 없다.  실물 샘플 4종을 확인했다 — 둘을 헷갈리면 조용히 아무것도
  못 읽는다.

⚠ 슬롯 **하나만** 읽는다.  같은 로트의 슬롯은 자재·Layer 가 같다(사용자 확인).
  전부 읽으면 폴더 수만큼 왕복이 늘 뿐이다 — 슬롯 25개 자재에서 그 비용이 실제로
  문제가 됐던 전례가 있다(설정 화면 die 안내 스캔).

전 구간 fail-safe — 못 읽으면 ``None``.  파일 제목은 사용자가 고칠 수 있으므로
(마지막 저장 직전에 묻는다) 여기서 실패해도 흐름이 멈추지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..coords.ini_text import read_ini_text

__all__ = ["LotInfo", "read_lot_info"]

_INI_NAME = "WaferInfo.ini"
# `InputLot=GFW-RDL4` — 값 안의 **첫 하이픈**에서 자재와 Layer 로 나뉜다.
# ★ `UseLot` 도 같은 값을 갖지만(실물 확인) `InputLot` 만 본다 — 하나로 정해 두지
#   않으면 둘이 다를 때 무엇을 읽었는지 알 수 없다.
_INPUT_LOT = re.compile(r"(?im)^\s*InputLot\s*=\s*(\S+)\s*$")


@dataclass(frozen=True)
class LotInfo:
    """``InputLot`` 이 알려주는 것.  ``source`` 는 읽은 파일 경로(진단용)."""
    material: str          # 자재 — 예: GFW
    layer: str             # Layer — 예: RDL4
    source: Path


def _find_ini(folder: Path) -> Optional[Path]:
    """폴더 자신 + 한 단계 위에서 ``WaferInfo.ini`` 를 찾는다.

    장비마다 결과 폴더 구조가 조금씩 달라 파일이 슬롯 폴더 옆에 놓이기도 한다
    (`coords/wafer_geometry._search_dirs` 가 같은 이유로 부모를 함께 본다)."""
    for base in (folder, folder.parent):
        try:
            p = base / _INI_NAME
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def read_lot_info(root: Path) -> Optional[LotInfo]:
    """``root`` 아래 **첫 슬롯**의 ``WaferInfo.ini`` 에서 자재·Layer 를 읽는다.

    ``root`` 자체에 파일이 있으면 그것을 먼저 쓴다(슬롯으로 나뉘지 않은 폴더).
    못 읽거나 형식이 다르면 ``None`` — 호출부는 추천 제목을 비워 두면 된다.
    """
    from .slot import list_slot_dirs

    try:
        root = Path(root)
        if not root.is_dir():
            return None
        candidates = [root]
        # 숨김 폴더는 표본에서 뺀다(설정 화면 die 안내와 같은 관습 — 결과 폴더 옆의
        # 점 폴더가 이름순 첫 자리를 차지하면 표본이 통째로 엉뚱해진다).
        candidates += [d for n, d in sorted(list_slot_dirs(root).items())
                       if not n.startswith(".")][:1]
        for folder in candidates:
            ini = _find_ini(folder)
            if ini is None:
                continue
            text = read_ini_text(ini) or ""
            m = _INPUT_LOT.search(text)
            if not m:
                continue
            material, _, layer = m.group(1).partition("-")
            material, layer = material.strip(), layer.strip()
            if material and layer:
                return LotInfo(material=material, layer=layer, source=ini)
        return None
    except Exception:
        return None
