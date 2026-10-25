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

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple, Optional

from .models import DefectCoord

_LOG = logging.getLogger("aoi.coords")

__all__ = ["resolve", "parse_live_name", "LiveName", "lot_key"]

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


def _pair_at(toks: list[str]) -> Optional[int]:
    """규칙 1 — 처음 등장하는 '연속한 두 정수 토큰' 의 위치."""
    return next((i for i in range(len(toks) - 1)
                 if _INT.match(toks[i]) and _INT.match(toks[i + 1])), None)


def lot_key(stem: str) -> Optional[tuple[str, ...]]:
    """같은 LOT 의 LIVE 사진끼리 같은 값 — col/row 앞 토큰에서 **바로 앞(웨이퍼 ID)을 뺀** 것.

    예) ``2D@R5-AS-T254-A01_0857122AY-0B_WVU_GX57004924_0_3_…`` →
    ``('2D@R5-AS-T254-A01', '0857122AY-0B', 'WVU')``.  웨이퍼 ID 가 col/row 바로 앞이라는
    것은 관측(이 형식의 실물 전부)이고, 아니어도 묶음이 웨이퍼 단위로 좁아질 뿐이다.
    LIVE 형식이 아니면 ``None``."""
    if parse_live_name(stem) is None:
        return None
    toks = stem.split('_')
    return tuple(toks[:_pair_at(toks) - 1])


def parse_live_name(stem: str) -> Optional[LiveName]:
    """LIVE 파일명 stem → :class:`LiveName`.  형식이 아니면 ``None``.

    무거운 의존성 없는 순수 함수다 — 헤드리스로 단위 테스트한다.
    """
    toks = stem.split('_')

    # 규칙 1 — 처음 등장하는 '연속한 두 정수 토큰'
    at = _pair_at(toks)
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


# 화면 번호 시프트 추정 — 후보·채택 조건.  근거: T254 FS UBM(``DisplayColShift=-1``)의
# LIVE 42장이 전부 LOT 맵 txt 불량 칸의 한 칸 왼쪽이었고(관측), 시프트 없는 4 LOT 223장은
# 시프트 0 에서 불량 칸에 떨어졌다(관측).  조건은 그 두 표본을 가르는 보수적 선(가정).
_SHIFTS = tuple((dc, dr) for dc in (-1, 0, 1) for dr in (-1, 0, 1))
_SHIFT_MIN_SUPPORT = 3        # 채택에 필요한 서로 다른 (웨이퍼, 칸) 수
_SHIFT_MIN_SHARE = 0.9        # 어느 후보에든 맞은 사진 중 채택 후보가 맞힌 비율 하한
_SHIFT_MAX_ZERO = 0.1         # 그중 시프트 0 이 맞힌 비율 상한
_MAX_LOT_TXT = 200            # LOT 폴더에서 보는 맵 txt 상한(엉뚱한 상위 폴더 방어)


@lru_cache(maxsize=64)
def lot_display_shift(lot_folder: Path) -> tuple[int, int]:
    """LOT 폴더의 LIVE 파일명이 **화면 번호**로 적혔다면 그 시프트 ``(col, row)``, 아니면 ``(0, 0)``.

    Camtek 레시피에 ``DisplayColShift`` 가 있으면 장비는 화면·LIVE 파일명에 실제 die 번호
    + 시프트를 적는다(:func:`.camtek_ini.display_shift`).  Conder scan 폴더엔 ``Recipe.ini``
    가 없어 그 값을 못 읽으므로, 같은 폴더의 LOT 맵 txt(실제 die 번호에 불량 bin) 와 대조해
    **추정**한다: 후보 시프트마다 '파일명 − 시프트' 칸이 불량 bin 인 사진 수를 센다.

    0 이 아닌 값은 아래를 모두 만족할 때만 채택한다(아니면 0 — 지금까지의 동작):
    서로 다른 (웨이퍼, 칸) 이 :data:`_SHIFT_MIN_SUPPORT` 개 이상, 맞은 사진의
    :data:`_SHIFT_MIN_SHARE` 이상을 그 후보가 맞힘, 시프트 0 은 :data:`_SHIFT_MAX_ZERO` 이하.
    ⚠ 맵이 결함을 표시하지 않는 제품(QORVO UBM — 55장 중 0장 일치)은 근거가 없어 0 이다."""
    from . import wafer_txt
    hits = {sh: 0 for sh in _SHIFTS}          # 후보별 맞힌 사진 수
    support = {sh: set() for sh in _SHIFTS}   # 후보별 맞힌 (웨이퍼, 칸)
    n_photos = 0                              # 어느 후보에든 맞은 사진 수
    try:
        txts = sorted(p for p in lot_folder.iterdir()
                      if p.suffix.lower() == ".txt" and p.is_file())[:_MAX_LOT_TXT]
    except OSError:
        return (0, 0)
    for t in txts:
        slot = lot_folder / t.stem
        if not slot.is_dir():
            continue
        wt = wafer_txt.load(slot)
        if wt is None or not wt.bad:
            continue
        try:
            stems = [f.stem for f in slot.iterdir()]
        except OSError:
            continue
        for stem in stems:
            got = parse_live_name(stem)
            if got is None:
                continue
            any_hit = False
            for dc, dr in _SHIFTS:
                cell = (got.col - dc, wt.rows - 1 - (got.row - dr))
                if cell in wt.bad:
                    hits[(dc, dr)] += 1
                    support[(dc, dr)].add((t.stem, cell))
                    any_hit = True
            n_photos += any_hit
    if not n_photos:
        return (0, 0)
    best = max(_SHIFTS, key=lambda sh: (hits[sh], sh == (0, 0)))
    if best == (0, 0):
        return (0, 0)
    if (len(support[best]) >= _SHIFT_MIN_SUPPORT
            and hits[best] >= _SHIFT_MIN_SHARE * n_photos
            and hits[(0, 0)] <= _SHIFT_MAX_ZERO * n_photos):
        _LOG.warning("%s 의 LIVE 파일명이 LOT 맵 txt 와 col %+d · row %+d 어긋난다 — "
                     "레시피 화면 번호 시프트로 보고 실제 die 로 되돌린다(사진 %d/%d장)",
                     lot_folder, best[0], best[1], hits[best], n_photos)
        return best
    _LOG.warning("%s 의 LIVE 파일명이 LOT 맵 txt 불량 칸과 일정하게 맞지 않는다 — "
                 "시프트를 정하지 못해 파일명 번호를 그대로 쓴다(후보별 사진 수 %s)",
                 lot_folder, {sh: n for sh, n in hits.items() if n})
    return (0, 0)


def resolve(image_path: Path) -> Optional[DefectCoord]:
    """LIVE 형식 파일명에서 DefectCoord 추출. 형식이 맞지 않으면 None.

    파일명 번호는 장비 **화면 번호**다.  LOT 맵 txt 로 시프트가 확인되면
    (:func:`lot_display_shift`) ``col/row`` 는 실제 die 로 되돌리고, 화면 번호는
    ``screen_col/screen_row`` 로 남긴다(Camtek INI 와 같은 규약)."""
    got = parse_live_name(image_path.stem)
    if got is None:
        return None
    sc, sr = lot_display_shift(image_path.parent.parent)
    return DefectCoord(col=got.col - sc, row=got.row - sr, x=got.x, y=got.y,
                       source="camtek_live", col_shift=sc, row_shift=sr)
