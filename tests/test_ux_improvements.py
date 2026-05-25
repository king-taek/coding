"""UX 개선 회귀 테스트 — 매치 검토 접기/크게보기, 실패 검토 재사용/슬라이더 제거,
크게보기 뷰어 동일 크기/버튼 배치, 로딩 갯수 표시, 효율 진행 카운트."""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QWidget

from aoi_verification.app.models.result import MatchResult, MissEntry
from aoi_verification.app.models.slot import ImageItem


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


# ── 로딩 오버레이: % 대신 갯수 ────────────────────────────────────────────
def test_loading_overlay_shows_counts(qapp):
    from aoi_verification.app.ui.widgets.loading_overlay import LoadingOverlay
    host = QWidget()
    ov = LoadingOverlay(host)
    ov.set_progress(150, 300, "유사도 계산")
    assert ov._progress.format() == "%v / %m"
    assert ov._progress.maximum() == 300
    # 총량이 바뀌면(단계 전환) 새 최대값으로 스냅.
    ov.set_progress(0, 40, "후보 생성")
    assert ov._progress.maximum() == 40


# ── 크게보기 뷰어: 이전/다음 인접 + 공통 박스 ─────────────────────────────
def test_side_by_side_viewer_layout(qapp, tmp_path):
    from aoi_verification.app.ui.widgets.side_by_side_viewer import SideBySideViewer
    cands = [(ImageItem(slot="S", path=tmp_path / "a.jpg", side="val"), "후보")]
    v = SideBySideViewer(tmp_path / "ref.jpg", cands, 0, action_label="매치")
    assert hasattr(v, "_sync_panes")
    assert hasattr(v._ref_pane, "set_target_box")
    # 양쪽 패널이 같은 목표 박스로 스케일된다.
    v._sync_panes()
    assert v._ref_pane._box == v._cand_pane._box


# ── 매치 검토: 접기 = 전부 접기 + 시그널 ──────────────────────────────────
def _mk_match(slot="S1"):
    return MatchResult(slot=slot, ref_path=Path("/tmp/r.jpg"),
                       val_path=Path("/tmp/v.jpg"), score=0.9, direction="A→B")


def test_match_review_collapse_all(qapp):
    from aoi_verification.app.ui.pages import match_review_page as mrp
    runners = [(ImageItem(slot="S1", path=Path(f"/tmp/c{i}.jpg"), side="val"),
                0.8 - i * 0.01) for i in range(20)]
    row = mrp._MatchRow(_mk_match(), runners_up=runners)
    less_emitted = []
    row.less_clicked.connect(lambda r: less_emitted.append(r))
    row._visible_lines = 4
    row._on_less()
    assert row._visible_lines == 1            # 전부 한 번에 접힘
    assert less_emitted == [row]              # 페이지에 스크롤 복귀 요청


def test_match_review_has_view_button(qapp):
    from aoi_verification.app.ui.pages import match_review_page as mrp
    row = mrp._MatchRow(_mk_match(), runners_up=[])
    assert hasattr(row, "btn_view")
    assert hasattr(row, "_open_compare")
    # 1위 매치가 비교 후보 맨 앞에 포함된다.
    prim = row._primary_val_item()
    assert prim.path == row.match.val_path


# ── 실패 검토: 슬라이더 제거 + ≥300 재사용 ────────────────────────────────
def test_unmatched_dialog_no_slider_and_reuse(qapp):
    from aoi_verification.app.ui.widgets.unmatched_review_dialog import (
        UnmatchedReviewDialog)
    miss = [MissEntry(slot="S1", side="ref", path=Path("/tmp/r.jpg"), note="")]
    pool = {("S1", "ref"): [ImageItem(slot="S1", path=Path(f"/tmp/v{i}.jpg"),
                                      side="val") for i in range(350)]}
    fr = {("S1", Path("/tmp/r.jpg")):
          [(Path(f"/tmp/v{i}.jpg"), 0.9 - i * 0.001) for i in range(40)]}
    dlg = UnmatchedReviewDialog(miss, pool, score_cache=None, fast_results=fr)
    assert not hasattr(dlg, "size_slider")
    calls = []
    dlg._lookup_or_compute_score = lambda r, v, allow_compute=True: calls.append(1) or 0.5
    scored = dlg._score_candidates(miss[0], pool[("S1", "ref")], allow_compute=False)
    assert calls == []                        # ≥300 → CPU 재계산 없음
    assert len(scored) == 40                  # 선계산 top-K 재사용
    # <300 → 그 자리 계산.
    dlg._score_candidates(miss[0], pool[("S1", "ref")][:5], allow_compute=True)
    assert len(calls) == 5
