"""고효율 모드 — 점수 정규화([0,1]) 단위 테스트.

임베딩 유닛은 코사인 유사도를 ``(cos+1)/2`` 로 정규화해 고전 파이프라인([0,1])
과 동일 임계치를 적용한다.  실제 OpenVINO 없이 ``device_embed`` 를 모킹해
정규화 + 랭킹을 검증한다."""

from __future__ import annotations

from pathlib import Path

from aoi_verification.app.models.slot import ImageItem
from aoi_verification.app.workers import efficiency_matcher as eff


def test_cos_to_unit_bounds():
    assert eff._cos_to_unit(1.0) == 1.0      # 완전 일치
    assert eff._cos_to_unit(0.0) == 0.5      # 직교
    assert eff._cos_to_unit(-1.0) == 0.0     # 반대
    # 범위 클램프
    assert eff._cos_to_unit(2.0) == 1.0
    assert eff._cos_to_unit(-2.0) == 0.0


def _item(name, slot="S", side="val"):
    return ImageItem(slot=slot, path=Path(name), side=side)
