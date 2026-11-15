"""Threshold HTML 내보내기 — 기존 → 변경 값, 빠지는 수, 대표 사진 고르기(Qt 없이)."""

from __future__ import annotations

from aoi_verification.app.threshold import report as RP


def test_pick_samples_spreads_from_near_to_far():
    assert RP.pick_samples(list(range(10)), 3) == [0, 4, 9]       # 기준 바로 아래 · 가운데 · 가장 먼 것
    assert RP.pick_samples([1, 2], 8) == [1, 2]
    assert RP.pick_samples([], 8) == [] and RP.pick_samples([1], 0) == []


def test_build_html_has_values_counts_and_escapes():
    ch = RP.Change("RDL", "기준 1 → B R <Dark>", "Contrast", "", 24.0, 26.0, 1, (20.0, 25.0, 30.0))
    z = RP.ZoneResult("RDL", 2309, 1719, (RP.Sample("data:image/jpeg;base64,AAAA", "B R Surface", "C 25.6"),), 3)
    text = RP.build_html(folder=r"I:\AOI-05\W1", when="2026-11-16 02:30", total=2309, dropped=1719,
                         changes=[ch], zones=[z])
    assert "24.0" in text and "→ 26.0" in text
    assert "1,719" in text and "590" in text and "2,309" in text           # 빠짐 · 남음 · 전체
    assert "&lt;Dark&gt;" in text and "<Dark>" not in text                  # 이름은 이스케이프
    assert "data:image/jpeg;base64,AAAA" in text and "<svg" in text
    assert "사진 없음 3" in text
