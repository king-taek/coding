"""결과 엑셀 제목의 **자재·Layer·LOT 추천값**(:func:`read_title_parts`) 과 카세트 슬롯
번호(:func:`read_active_slot`) 를 장비 파일에서 읽는다.

추천 제목(사용자 요청)::

    `{검증장비} {Layer}_{자재}_{LOT} 검증({기준장비} 기준).xlsx`
        →  `4F-AOI-03 RDL4_TB500_TB500.INT895 검증(AOI-24 기준).xlsx`

자재 = **제품 이름**(TB500·T254)이다(사용자 결정 2026-10-30).  예전에는
``WaferInfo.ini`` 의 ``InputLot``(GFW-RDL4) 을 잘라 썼는데, 그 값은 장비에 넣은 **LOT
이름**이라 자재 자리에 GFW·FDV 같은 LOT 앞글자가 들어갔고, 띄어쓰기가 든 LOT 은 아예
못 읽었다.  LOT 이름은 자재와 **따로** 한 칸으로 넣는다(사용자 요청 2026-10-30) — 장비가
적어 둔 LOT ID 의 ``@`` 앞(``TB500.INT895@6412`` → ``TB500.INT895``, ``@`` 뒤는 공정 번호).

읽는 곳(실물 근거: 저장소 `dev/좌표 확인` 샘플)::

    Camtek  Params_WaferInfo.ini  SetupPath=c:\\job\\TB500_RDL4 - Multi\\Setup1\\...
            ProductInfo.ini       JobDesc=DEV=4DT-TB500-H-M1~LOT=TB500.INT888@6324~...
    KLA     정보파일(.001) 헤더   DeviceID "4DT-TB500-H-M1";  StepID "RDL4_AUTO_RBB";
                                  LotID "TB500.INT895@6412";

⚠ **추천값일 뿐이다.**  Job 이름 모양이 제품마다 달라(`2D@R2-15966TA0-BW2_…`) 못 읽는
제품이 있다 — 그때는 칸을 비워 두고 저장 창에서 사용자가 채운다(사용자 결정: 제품코드 →
이름 표는 만들지 않는다).  틀린 값을 채우느니 비워 두게 규칙을 좁게 잡았다:
자재는 ``TB500``·``T254`` 처럼 **영문 1~2자 + 숫자 3자** 토큰, Layer 는 ``RDL4``·``PI4``
처럼 **아는 Layer 이름(RDL·PI·PIDS·UBM) + 숫자** 토큰(자재 토큰 **뒤**에서만, KLA 는
StepID 첫 토큰).

⚠ 슬롯 **하나만** 읽는다.  같은 로트의 슬롯은 자재·Layer 가 같다(사용자 확인).
  전부 읽으면 폴더 수만큼 왕복이 늘 뿐이다 — 슬롯 25개 자재에서 그 비용이 실제로
  문제가 됐던 전례가 있다(설정 화면 die 안내 스캔).

전 구간 fail-safe — 못 읽으면 빈 값.  저장 직전에 사용자가 고칠 수 있으므로 여기서
실패해도 흐름이 멈추지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from ..coords.ini_text import read_ini_text

__all__ = ["TitleParts", "parse_title_parts", "read_title_parts", "read_active_slot"]

_INI_NAME = "WaferInfo.ini"
# `ActiveSlot=6` — 웨이퍼가 꽂혀 있던 **카세트 슬롯 번호**.  결과 엑셀 slot# 칸에
# slot명 아래 `(#6)` 으로 함께 찍는다(사용자 요청).
_ACTIVE_SLOT = re.compile(r"(?im)^\s*ActiveSlot\s*=\s*(\d+)\s*$")

# Camtek Job 이름 — 레시피 경로의 `\job\<이름>\`, 없으면 결과 경로의 `\ScanResults\<이름>\`.
_SETUP_PATH = re.compile(r"(?im)^\s*SetupPath\s*=.*?\\job\\([^\\\r\n]+)")
_RESULT_PATH = re.compile(r"(?im)^\s*ScanResultsPath\s*=.*?\\ScanResults\\([^\\\r\n]+)")
# ProductInfo.ini `JobDesc=DEV=4DT-TB500-H-M1~LOT=…`
_JOB_DEV = re.compile(r"(?im)^\s*JobDesc\s*=.*?DEV=([^~\r\n]+)")
# ProductInfo.ini `JobDesc=…~LOT=TB500.INT888@6324~…` — LOT 이름은 `@` 앞(뒤는 공정 번호).
_JOB_LOT = re.compile(r"(?im)^\s*JobDesc\s*=.*?LOT=([^~\r\n]+)")
# 파일 이름에 쓸 수 없는 글자 — LOT 은 장비 문자열을 그대로 쓰므로 여기서 걸러 낸다.
_BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|]')
_TOKEN_SPLIT = re.compile(r"[\s_\-@~()]+")
_MATERIAL_TOKEN = re.compile(r"^[A-Z]{1,2}\d{3}$")     # TB500 · T254
# Layer 는 아는 이름만 — 모양만 보면 `ECO3`·`AUTO1` 같은 레시피 토큰도 Layer 로 잡힌다.
# 새 Layer 이름이 나오면 여기 더한다(못 읽으면 빈 칸 — 저장 창에서 채운다).
_LAYER_TOKEN = re.compile(r"^(?:RDL|PIDS|PI|UBM)\d{1,2}$")      # RDL4 · PI4 · PIDS3


@dataclass(frozen=True)
class TitleParts:
    """제목 추천값 — 못 읽은 칸은 빈 문자열."""
    material: str = ""     # 자재 — 예: TB500
    layer: str = ""        # Layer — 예: RDL4
    lot: str = ""          # LOT 이름 — 예: TB500.INT895


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split((text or "").strip()) if t]


def parse_title_parts(job: str = "", device: str = "", step: str = "") -> TitleParts:
    """장비 문자열 셋에서 자재·Layer 를 고른다(순수 함수 — 헤드리스 테스트).

    - 자재: Job 이름 → 제품코드(DEV/DeviceID) 순으로 첫 자재 토큰.
    - Layer: KLA StepID 의 첫 토큰(``RDL4_AUTO_RBB`` → RDL4) → Job 이름에서 자재 토큰
      **뒤**의 첫 Layer 토큰(``TB500_RDL4 - Multi`` → RDL4).  자재 앞(`2D@R5-…`)은 보지
      않는다 — 레시피 접두가 Layer 처럼 생겼다.
    """
    material = ""
    job_tokens = _tokens(job)
    job_mat_at = -1
    for i, t in enumerate(job_tokens):
        if _MATERIAL_TOKEN.match(t):
            material, job_mat_at = t, i
            break
    if not material:
        material = next((t for t in _tokens(device) if _MATERIAL_TOKEN.match(t)), "")

    layer = ""
    step_tokens = _tokens(step)
    if step_tokens and _LAYER_TOKEN.match(step_tokens[0]):
        layer = step_tokens[0]
    elif job_mat_at >= 0:
        layer = next((t for t in job_tokens[job_mat_at + 1:]
                      if _LAYER_TOKEN.match(t)), "")
    return TitleParts(material=material, layer=layer)


def clean_lot(raw: str) -> str:
    """장비 LOT ID → 제목에 쓸 LOT 이름: ``@`` 앞만, 파일 이름에 못 쓰는 글자는 빼고,
    공백은 하나로(``E1X000.C03 DCC20`` 처럼 띄어쓰기가 든 LOT 도 있다)."""
    name = (raw or "").split("@", 1)[0]
    return " ".join(_BAD_NAME_CHARS.sub("", name).split())


def _read_folder(folder: Path) -> TitleParts:
    """한 폴더의 Camtek INI 와 KLA 정보파일에서 추천값을 읽는다."""
    from ..coords.kla_info import read_device_step

    # ★ 부모는 보지 않는다 — 후보가 루트와 그 안의 슬롯뿐이라 슬롯의 부모는 루트(이미
    #   후보)이고, 루트의 부모는 관계없는 공용 폴더일 수 있다.
    text = read_ini_text(folder / "Params_WaferInfo.ini") or ""
    m = _SETUP_PATH.search(text) or _RESULT_PATH.search(text)
    job = m.group(1).strip() if m else ""
    product = read_ini_text(folder / "ProductInfo.ini") or ""
    m = _JOB_DEV.search(product)
    device = m.group(1).strip() if m else ""
    m = _JOB_LOT.search(product)
    lot = m.group(1) if m else ""
    step = ""
    if not job and not device:              # Camtek 파일이 없으면 KLA 로 본다
        kdev, kstep, klot = read_device_step(folder)
        device, step, lot = kdev or "", kstep or "", klot or ""
    tp = parse_title_parts(job, device, step)
    return TitleParts(material=tp.material, layer=tp.layer, lot=clean_lot(lot))


def read_title_parts(root: Path) -> TitleParts:
    """``root`` 자신과 **첫 슬롯** 폴더에서 자재·Layer·LOT 추천값을 읽는다.

    두 칸을 따로 채운다 — 앞 폴더에서 못 읽은 칸만 다음 폴더로 메운다.
    """
    from .slot import list_slot_dirs

    got = TitleParts()
    try:
        root = Path(root)
        if not root.is_dir():
            return got
        candidates = [root]
        # 숨김 폴더는 표본에서 뺀다(설정 화면 die 안내와 같은 관습 — 결과 폴더 옆의
        # 점 폴더가 이름순 첫 자리를 차지하면 표본이 통째로 엉뚱해진다).  Windows 시스템
        # 폴더(`$RECYCLE.BIN`)도 `$` 가 글자·숫자보다 앞에 정렬돼 같은 일을 한다.
        candidates += [d for n, d in sorted(list_slot_dirs(root).items())
                       if not n.startswith((".", "$"))][:1]
        for folder in candidates:
            p = _read_folder(folder)
            got = TitleParts(material=got.material or p.material,
                             layer=got.layer or p.layer, lot=got.lot or p.lot)
            if got.material and got.layer and got.lot:
                break
    except Exception:
        pass
    return got


@lru_cache(maxsize=256)
def read_active_slot(folder: Path) -> Optional[str]:
    """``folder`` 의 ``WaferInfo.ini`` 에서 ``ActiveSlot``(카세트 슬롯 번호) 를 읽는다.

    실물 근거(`docs/WaferInfo.ini`)::

        [AutoCycleInfo]
        ActiveSlot=5

    ⚠ :func:`read_title_parts` 처럼 **부모 폴더는 보지 않는다**.  자재·Layer 는 로트
    공통이라 아무 슬롯에서 읽어도 같지만 ``ActiveSlot`` 은 **웨이퍼 한 장의 값**이다 —
    슬롯 폴더 옆에 놓인 INI 를 읽으면 다른 웨이퍼의 번호를 그 슬롯에 붙인다.  못 찾으면
    번호 없이 slot명만 찍히므로, 잘못된 번호를 찍는 것보다 없는 편이 낫다.

    못 읽으면 ``None`` — 전 구간 fail-safe(엑셀 표기용 부가 정보라 흐름을 멈추지 않는다).
    """
    try:
        ini = Path(folder) / _INI_NAME
        if not ini.is_file():
            return None
        m = _ACTIVE_SLOT.search(read_ini_text(ini) or "")
        return m.group(1) if m else None
    except Exception:
        return None
