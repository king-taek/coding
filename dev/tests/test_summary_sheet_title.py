"""요약 시트 이름 — 31자 상한에서 토큰 중간을 자르지 않는다 (SM-E2d, 현택님 2026-11-24).

예전엔 파일 이름 앞 31자를 그대로 잘라 ``4F-AOI-03 TB500_RDL4_GFW 검증(AOI-2`` 처럼 호기
번호가 반쯤 잘렸다.  호기 · 날짜 토큰은 남기고 그 밖의 토큰을 앞에서부터 줄인다.
파일 이름 만드는 법은 그대로다(시트 이름만).
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtCore")

from aoi_verification.app.workers.exporter import _fit_sheet_title     # noqa: E402


@pytest.mark.parametrize("stem, machines, keep", [
    ("4F-AOI-03 TB500_RDL4_GFW 검증(AOI-24 기준)", ("AOI-24", "4F-AOI-03"),
     ("4F-AOI-03", "검증(AOI-24", "기준)")),
    ("AOI검증결과_AOI-17_vs_AOI-2_20261124_153012", ("AOI-17", "AOI-2"),
     ("AOI-17", "AOI-2", "20261124", "153012")),
    ("4F-AOI-03 TB500_RDL4_GFW_SM1234 Defect 추출", ("4F-AOI-03", ""),
     ("4F-AOI-03", "추출")),
])
def test_long_names_keep_machine_and_date_and_whole_tokens(stem, machines, keep):
    title = _fit_sheet_title(stem, machines)
    assert len(stem) > 31 and len(title) <= 31, title
    for tok in keep:
        assert tok in title, f"{tok!r} 가 빠졌다: {title!r}"
    # 토큰 경계에서만 잘린다 — 남은 토큰은 전부 원래 이름의 온전한 토큰이다.
    import re
    whole = set(re.split(r"[\s_]+", stem))
    assert set(re.split(r"[\s_]+", title)) <= whole, title


def test_short_name_is_unchanged():
    assert _fit_sheet_title("AOI 4F-AOI-03 검증 (AOI-24 기준)", ("AOI-24",)) == \
        "AOI 4F-AOI-03 검증 (AOI-24 기준)"


def test_single_huge_token_is_cut_as_last_resort():
    assert _fit_sheet_title("x" * 40) == "x" * 31
