"""Camtek LIVE 파일명에서 좌표 추출.

정규식으로 위치를 맞추지 않고 **``_`` 로 토큰을 쪼개서** 해석한다.  관찰된 배치가
셋이고(아래) 정규식으로는 서로 잡아먹기 때문이다 — 실제로 그래서 R-0 이 났다.

  A) ..._col_row_x_y[_DefectName]                 x/y 뒤에 이름(선택)
     예) ..._FDV-RDL4_W7548304XYG4_5_4_31863.2366998812_26908.4427394723_Irregular Bump
  B) ..._col_row_DefectName_x_y                   x/y 앞에 이름
     예) ..._VLP-PDIS3_W6317098XYB5_4_5_Over Sized Bump_30229.803_1987.994
  C) ..._col_row_DefectName_x_y_DXSize_DYSize_DArea    ★ x/y 뒤에 크기·면적이 더 붙는다

규칙 세 개로 셋을 다 덮는다:

1. ``col``/``row`` = 파일명에서 **처음 등장하는 '연속한 두 정수 토큰'**.
2. 그 앞에 식별자 토큰이 **2개 이상** 있어야 한다 — KLA 파일명 배제용(아래).
3. ``x``/``y`` = ``col``/``row`` **뒤에서 처음 등장하는 두 수치 토큰**.
   그 뒤에 수치가 더 있으면 (C) 의 크기·면적이며 **좌표로 쓰지 않는다.**

★★ R-0 — (C) 에서 x·y 에 크기·면적이 들어가던 버그 (2026-08-05 수정)

옛 구현은 ``_(\\d+)_(\\d+)_.+_(num)_(num)$`` 로 (B) 를 잡았는데, greedy ``.+`` 와 ``$``
앵커 때문에 (C) 에서 **뒤쪽 두 수치**(DYSize·DArea)를 집었다::

    ..._4_5_Over Sized Bump_30229.803_1987.994_12.5_18.3_229.1
    옛 결과  x=18.3      y=229.1      ← DYSize·DArea
    정답     x=30229.803 y=1987.994   ← x 오차 30,212 µm

``col``/``row`` 는 그대로 맞아서 **자체모순이 없고, ±1 게이트도 통과**한다.
즉 조용히 틀린다 — 그래서 리스크 등급이 '높' 이었다.
규칙 3 이 '처음 두 개' 를 쓰므로 (A)(B)(C) 가 한 경로로 처리된다.

⚠ **(C) 는 실물을 확보하지 못했다**(출처 등급 ``문서``).  근거는 참고 저장소
``king-taek/Defect_Tracking`` 의 ``camtek_filename.py`` 주석에 적힌 토큰 순서
``{col}_{row}_{Name}_{x}_{y}_{DXSize}_{DYSize}_{DArea}`` 뿐이다.  경쟁 가설
(크기·면적이 x/y **앞**에 오는 배치)은 **지금 표본으로 구분할 수 없다** — 실물 1건이
오면 닫힌다(`docs/좌표_계산_검증_보고서_상세.html` §9-1 자료 2).
다만 어느 쪽이든 **옛 동작보다 나쁘지는 않다**: (A)(B) 는 실물로 검증돼 있고,
(C) 에서 옛 코드는 확실히 틀렸다.

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
from typing import NamedTuple, Optional

from .models import DefectCoord

__all__ = ["resolve", "parse_live_name", "LiveName"]

# 정수 토큰.  **음수를 포함시키는 게 중요하다** — KLA 파일명
# ``00MEU018XYG1_-1_4_23_1`` 에서 `-1` 을 정수로 보지 않으면 '처음 연속한 정수 쌍' 이
# `4`,`23` 으로 밀려 식별자 토큰이 2개가 돼(규칙 2 통과) col=4,row=23 으로 오인한다.
_INT = re.compile(r'^-?\d+$')
_NUM = re.compile(r'^-?\d+(?:\.\d+)?$')

# col/row 앞에 있어야 하는 최소 식별자 토큰 수.
# KLA 이미지 파일명은 `{WaferID}_{...}` 로 앞이 **1개**(웨이퍼 ID)뿐이고,
# LIVE 는 레시피·로트·웨이퍼가 앞에 붙어 2개 이상이다.  실물 대조:
#   LIVE  TB500_RDL4 - Multi_FDV-RDL4_W7548304XYG4_5_4_…   → 앞 4개  ✓
#   KLA   W6459076XYG1_2_0_23_2                            → 앞 1개  ✗
_MIN_PREFIX_TOKENS = 2


class LiveName(NamedTuple):
    """파일명 토큰 해석 결과.  ``extra`` 는 x/y 뒤에 남은 수치(=(C) 의 크기·면적).

    ``extra`` 는 지금 좌표에 쓰지 않는다 — **x/y 를 오염시키지 않는 것이 목적**이다.
    엑셀에 크기·면적 열을 추가하는 건 별개 작업이고, 그때 이 값을 쓰면 된다.
    """
    col: int
    row: int
    x: float
    y: float
    extra: tuple[float, ...]


def parse_live_name(stem: str) -> Optional[LiveName]:
    """LIVE 파일명 stem → :class:`LiveName`.  형식이 아니면 ``None``.

    무거운 의존성 없는 순수 함수다 — 헤드리스로 단위 테스트한다.
    """
    toks = stem.split('_')

    # 규칙 1 — 처음 등장하는 '연속한 두 정수 토큰'
    at = next((i for i in range(len(toks) - 1)
               if _INT.match(toks[i]) and _INT.match(toks[i + 1])), None)
    if at is None:
        return None

    # 규칙 2 — 앞에 식별자 토큰이 2개 이상 (KLA 파일명 배제)
    if at < _MIN_PREFIX_TOKENS:
        return None

    col, row = int(toks[at]), int(toks[at + 1])
    # 0-based 웨이퍼 맵에 음수 die 는 존재할 수 없다(§6-H).  규칙 2 를 우연히 통과한
    # KLA 계열 이름(`LOT_W1_-2_-2_31_2` 같은 것)을 여기서 한 번 더 거른다.
    if col < 0 or row < 0:
        return None

    # 규칙 3 — col/row 뒤에서 **처음** 두 수치.  ★ R-0 수정 지점
    nums = [float(t) for t in toks[at + 2:] if _NUM.match(t)]
    if len(nums) < 2:
        return None

    return LiveName(col=col, row=row, x=nums[0], y=nums[1],
                    extra=tuple(nums[2:]))


def resolve(image_path: Path) -> Optional[DefectCoord]:
    """LIVE 형식 파일명에서 DefectCoord 추출. 형식이 맞지 않으면 None."""
    got = parse_live_name(image_path.stem)
    if got is None:
        return None
    return DefectCoord(col=got.col, row=got.row, x=got.x, y=got.y,
                       source="camtek_live")
