"""col/row 번호가 **틀렸을 수 있는** 웨이퍼 모으기 — 매칭·검토 화면의 큰 경고용.

번호표(``[ExportData]``)·die 목록·같은 셋업 빌리기·작은 die 맵으로 번호 원점을 못 정하면
좌표 파서가 :attr:`DefectCoord.number_warn` 을 남긴다(``wafer_geometry.is_trusted_source``).
2026-11 검증에서 그런 웨이퍼는 장비 화면과 자주 달랐다(장비 확인 13장 중 6장 오답, 전부
그 표시가 붙은 웨이퍼).  사진 한 장 정보에만 작게 적어 두면 놓치므로, 사용자 지시
(2026-11-13 "경고가 나오는 웨이퍼를 진행할 때에는 경고를 크게") 대로 슬롯 단위로 모아
화면 위에 크게 띄운다.  순수 — 헤드리스 테스트한다.
"""
from __future__ import annotations

from typing import Callable, Iterable, Optional

__all__ = ["count_by_slot", "merge", "banner_text"]

# 배너에 슬롯을 몇 개까지 적을지 — 넘으면 '외 N개'.
_SHOW_MAX = 6


def count_by_slot(groups: Iterable[tuple[str, Iterable]],
                  coord_of: Callable[[object], Optional[object]]) -> tuple[tuple[str, int], ...]:
    """``[(슬롯, 사진 경로들)]`` → 번호 경고가 붙은 사진 수 ``((슬롯, n), ...)``.

    ``coord_of(path)`` 는 ``DefectCoord`` 또는 ``None``.  경고 없는 슬롯은 빼고,
    같은 슬롯이 여러 번 오면(기준·검증) 더한다.  순서는 처음 나온 순."""
    out: dict[str, int] = {}
    for slot, paths in groups:
        n = sum(1 for p in paths if getattr(coord_of(p), "number_warn", ""))
        if n:
            out[slot] = out.get(slot, 0) + n
    return tuple(out.items())


def merge(*risks: Iterable[tuple[str, int]]) -> tuple[tuple[str, int], ...]:
    """여러 실행(다중 검증 장비)의 결과를 합친다 — 같은 슬롯은 **큰 값**.

    구간마다 같은 기준 사진을 다시 세므로 더하면 부풀려진다.  큰 값은 하한이지만
    배너의 목적(어느 슬롯을 확인할지)에는 슬롯 목록이 맞으면 된다."""
    out: dict[str, int] = {}
    for risk in risks:
        for slot, n in risk or ():
            out[slot] = max(out.get(slot, 0), n)
    return tuple(out.items())


def banner_text(risk: Iterable[tuple[str, int]]) -> str:
    """큰 경고 문구 — 경고할 것이 없으면 빈 문자열."""
    from .. import i18n
    risk = [(s, n) for s, n in (risk or ()) if n]
    if not risk:
        return ""
    shown = " · ".join(i18n.KO.NUMBER_RISK_SLOT_FMT.format(slot=s, n=n) for s, n in risk[:_SHOW_MAX])
    if len(risk) > _SHOW_MAX:
        shown += i18n.KO.NUMBER_RISK_MORE_FMT.format(n=len(risk) - _SHOW_MAX)
    return "\n".join([i18n.KO.NUMBER_RISK_HEAD, i18n.KO.NUMBER_RISK_BODY, shown])
