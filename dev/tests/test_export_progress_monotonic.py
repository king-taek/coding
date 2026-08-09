"""C-4 — 엑셀 저장 진행률은 **저장 전체 기준**으로 한 번만 0 → 100 을 지난다.

``_fill_rows`` 는 시트마다 불린다(전체 양식 · 미매칭 · 요약).  시트별로 1..N 을
새로 세면 진행률이 매 시트 처음으로 되돌아가고, ``LoadingOverlay.set_progress``
는 값이 **줄면 tween 없이 즉시 스냅**하므로 사용자에겐 '다 찼다가 처음부터 다시'
가 세 번 보인다 — CLAUDE.md 의 로딩 계약(단조 증가·결정형) 위반이다.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")
pytest.importorskip("openpyxl")

from aoi_verification.app.models.result import (                  # noqa: E402
    FinalResult, MatchResult, MissEntry,
)
from aoi_verification.app.workers.exporter import ExcelExporter    # noqa: E402


def _result(tmp_path: Path, n_match: int = 3, n_miss: int = 2) -> FinalResult:
    """사진 실물은 만들지 않는다 — 임베드 실패는 파일명 텍스트로 대체되고(Bug #3)
    진행률 보고는 그와 무관하게 행마다 나간다."""
    return FinalResult(
        mode="single", ref_machine="1호기", val_machine="2호기",
        matches=[MatchResult(slot="S1", ref_path=tmp_path / f"r{i}.jpg",
                             val_path=tmp_path / f"v{i}.jpg", score=0.9)
                 for i in range(n_match)],
        unmatched_refs=[MissEntry(slot="S2", side="ref",
                                  path=tmp_path / f"u{i}.jpg", note="미매칭")
                        for i in range(n_miss)],
    )


def _run_and_collect(tmp_path: Path, **kw) -> list[tuple[int, int]]:
    exp = ExcelExporter(_result(tmp_path), dst_path=tmp_path / "out.xlsx",
                        template_path=tmp_path / "no_template.xlsx", **kw)
    seen: list[tuple[int, int]] = []
    exp.signals.progress.connect(lambda d, t, _m: seen.append((d, t)))
    exp.run()                        # QThread.run() 을 동기 실행
    return seen


def test_progress_never_restarts_across_sheets(isolated_cache, tmp_path):
    """미매칭 시트 + 요약 시트 + 전체 양식 — 세 번 채워도 바는 한 번만 찬다."""
    seen = _run_and_collect(tmp_path, include_full_template=True)
    dones = [d for d, _ in seen]
    totals = {t for _, t in seen}

    assert dones, "진행 보고가 하나도 없다"
    assert len(totals) == 1, f"총량이 시트마다 달라졌다: {totals}"
    total = totals.pop()
    assert total > 0, "결정형(총 > 0)이어야 한다 — 0 이면 busy 로 오해된다"
    assert dones == sorted(dones), f"진행률이 되돌아갔다: {dones}"
    # 전체 양식(5) + 미매칭(2) + 요약(5) = 12 행 → 마지막 보고가 끝을 가리킨다.
    assert total == 12
    assert dones[0] == 1 and dones[-1] == total


def test_progress_covers_every_sheet_without_the_full_template(isolated_cache,
                                                               tmp_path):
    """기본(전체 양식 off)에서도 총량은 실제로 채우는 시트들의 합이다."""
    seen = _run_and_collect(tmp_path)
    dones = [d for d, _ in seen]
    totals = {t for _, t in seen}
    assert len(totals) == 1
    assert totals.pop() == 7, "미매칭(2) + 요약(5)"
    assert dones == sorted(dones)
    assert dones[-1] == 7
