"""Camtek LIVE 파일명에서 좌표 추출.

두 가지 파일명 형식을 지원한다:
  A) ..._col_row_x_y[_DefectName]  — x/y 뒤에 DefectName (선택)
     예) R_TB500_LIVE_PI4_PXU-PIDS3_00RMF043XYE0_5_3_21620.211_7230.807_Foreign Material
  B) ..._col_row_DefectName_x_y    — x/y 앞에 DefectName
     예) R_TB500_LIVE_PI4_VLP-PDIS3_W6317098XYB5_4_5_Over Sized Bump_30229.803_1987.994

col/row 는 정수, x/y 는 정수 또는 소수점 실수(µm). 형식 A 를 먼저 시도한다.

★ **col/row 토큰은 보정하지 않는다.** :mod:`.camtek_ini` 가 내는
``row = row_total − y_index`` 와 같은 규약이다.  예전에 row 토큰을 −1 하던 코드가 있었는데,
그건 ``CAMTEK_ROW_TOTAL`` 을 7→6 으로 바꾸던 변경과 **짝**이었다.  그 변경은 장비 화면
판독으로 되돌려졌지만(``docs/디바이스_하드코딩_조사.md`` §1-B) −1 만 남아, INI 와 LIVE 가
줄곧 1 어긋난 채 ``(col,row) ±1`` 게이트에 가려져 있었다.  근거 두 가지:

* −1 의 전제("row 토큰은 1..6 표시 규약")를 실물이 반증한다 — row 토큰이 **0** 인 파일이 있다.
* 같은 웨이퍼·같은 결함의 LIVE↔INI 쌍에서 die 좌표는 0.5 µm 안에 겹치는데, −1 이 있으면
  ``Δrow`` 가 2 가 돼 **버킷 게이트에서 탈락**한다(= 매치 실패).

자세한 경위는 ``docs/디바이스_하드코딩_조사.md`` §6-G.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .models import DefectCoord

__all__ = ["resolve"]

# 형식 A: ..._col_row_x[.xx]_y[.yy][_DefectName]
_PAT_A = re.compile(
    r'_(\d+)_(\d+)_([\d]+(?:\.[\d]+)?)_([\d]+(?:\.[\d]+)?)(?:_.+)?$'
)

# 형식 B: ..._col_row_DefectName_x[.xx]_y[.yy]  (DefectName 이 x/y 앞)
_PAT_B = re.compile(
    r'_(\d+)_(\d+)_.+_([\d]+(?:\.[\d]+)?)_([\d]+(?:\.[\d]+)?)$'
)


def _extract(m) -> Optional[DefectCoord]:
    try:
        col = int(m.group(1))
        row = int(m.group(2))       # 보정 없음 — camtek_ini 와 같은 규약(모듈 docstring)
        x = float(m.group(3))
        y = float(m.group(4))
    except (ValueError, IndexError):
        return None
    return DefectCoord(col=col, row=row, x=x, y=y, source="camtek_live")


def resolve(image_path: Path) -> Optional[DefectCoord]:
    """LIVE 형식 파일명에서 DefectCoord 추출. 형식이 맞지 않으면 None."""
    stem = image_path.stem
    for pat in (_PAT_A, _PAT_B):
        m = pat.search(stem)
        if m and '_' in stem[:m.start()]:
            return _extract(m)
    return None
