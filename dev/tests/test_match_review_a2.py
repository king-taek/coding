"""매치 검토 A2 밀집 리스트 — 상태 분류/집계·필터·키보드·800px 클램프."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication          # noqa: E402

from aoi_verification.app.models.result import MatchResult          # noqa: E402
from aoi_verification.app.ui.pages.match_review_page import (       # noqa: E402
    classify_row, tally)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _m(slot: str, score: float, name: str = "r.jpg") -> MatchResult:
    return MatchResult(slot=slot, ref_path=Path(f"/tmp/{slot}_{name}"),
                       val_path=Path(f"/tmp/{slot}_v.jpg"), score=score)


# ── classify_row — 기존 score 인코딩 그대로, 새 판정 로직 없음 ──────────────
def test_classify_ok_similarity_mode():
    assert classify_row(0.9, False, False) == "ok"


def test_classify_over_only_in_coord_mode():
    # 좌표 모드의 음수 score = '허용범위 초과' 인코딩.
    assert classify_row(-0.2, True, False) == "over"
    # 일반(유사도) 모드에서는 음수여도 over 로 분류하지 않는다.
    assert classify_row(-0.2, False, False) == "ok"


def test_classify_unmatched_wins():
    assert classify_row(0.9, True, True) == "unmatched"
    assert classify_row(-0.2, True, True) == "unmatched"


def test_tally_counts():
    ms = [_m("S1", 0.9), _m("S2", -0.1), _m("S3", 0.8), _m("S4", 0.7)]
    unmatched = {ms[3].key}
    assert tally(ms, unmatched, True) == (2, 1, 1)
    # 유사도 모드에선 음수도 ok.
    assert tally(ms, unmatched, False) == (3, 0, 1)
