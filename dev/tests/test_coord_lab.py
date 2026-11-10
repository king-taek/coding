"""좌표 가설 실험실(``dev/coord_lab.py``) — 저장소 실물 샘플에서 나온 결과를 못 박는다.

도구는 앱 코드와 **독립인 재계산**이다(표준 파이썬만).  여기 단언은 "가설이 옳다" 가 아니라
"이 표본에서 이렇게 나왔다" 다 — 깨지면 샘플이나 도구가 바뀐 것이니 보고서 수치를 다시 본다.
출처 등급: 정답 LIVE = 관측(Conder 파일명) · EXP = 파일(번호표).
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dev"))

import coord_lab as lab  # noqa: E402


@pytest.fixture(scope="module")
def run():
    camtek, kla_files, live = lab.scan(lab.DEFAULT_ROOTS)
    recs = [lab.analyze_camtek(f, live) for f in sorted(camtek)]
    for scope in ("LOT", "PRD"):
        lab.borrow(recs, scope)
    lab.app_check(recs, live)
    klas = [k for k in (lab.analyze_kla(p) for p in sorted(kla_files)) if k]
    return recs, klas, lab.score(recs)


def test_live_name_tokens():
    assert lab.parse_live("2D@RE-W_0860463PD-0A_CTX_SQ67P01-D1_20_140_IC - Other_115_746.jpg") \
        == (20, 140, 115.0, 746.0)
    assert lab.parse_live("00MEU018XYG1_2_0_23_2.jpg") is None        # KLA 사진 이름(식별자 1개)
    assert lab.parse_live("120397.101071.c.1986767493.jpeg") is None


def test_position_grid_is_anchored_at_stage_origin(run):
    """A1 — 위치 쪽은 이미 하나의 식이다: 결함 전부에서 Col == floor(X/pitch_x)."""
    a1 = [r["A1"] for r in run[0] if "A1" in r]
    assert sum(a["n"] for a in a1) > 6000
    assert sum(a["x_bad"] for a in a1) == 0


def test_no_pure_geometry_rule_numbers_every_product(run):
    """번호는 기하만으로 안 나온다 — 계산식 넷 모두 정답(LIVE)과 다른 웨이퍼가 있다."""
    sc = run[2]
    for h in ("C1D", "G0", "G3", "CEN"):
        assert sc[f"{h}|LIVE"]["both"] < sc[f"{h}|LIVE"]["n"], h
    for h in ("EXP", "LUT"):                       # 장비가 적어 둔 표는 전부 맞는다
        assert sc[f"{h}|LIVE"]["both"] == sc[f"{h}|LIVE"]["n"] > 0, h


def test_borrowed_number_position_matches_truth_and_app(run):
    """LOT/PRD — 다른 웨이퍼의 (u, v)를 빌린 번호가 정답과 같고, 앱의 다단 규칙과도 같다.

    ⚠ R2: 정답이 있는 건 고유 웨이퍼 5장 · 제품 2종뿐이다(같은 웨이퍼 사본에서는 빌리지 않는다).  나머지는 '앱과 같다' 일 뿐이다."""
    recs, _, sc = run
    for h in ("LOT", "PRD"):
        assert sc[f"{h}|LIVE"]["both"] == sc[f"{h}|LIVE"]["n"] > 0, h
    # 독립인 빌릴 곳이 없는 웨이퍼(GX57007306 은 사본뿐)는 가설을 내지 않는다 — 자기 답 베끼기 금지.
    assert not any("PRD" in r.get("hyp", {}) for r in recs if r["wafer"] == "GX57007306")
    both = [(r["hyp"]["PRD"], r["hyp"]["APP"]) for r in recs
            if r.get("hyp", {}).get("PRD") and r.get("hyp", {}).get("APP")]
    assert len(both) > 50 and all(tuple(a) == tuple(b) for a, b in both)
    # stage 위 놓인 자리가 한 열 다른 두 스캔(RDL2 AOI-5 col 기준 2 ↔ RDL4 col 기준 1)을 넘나든다.
    assert {tuple(r["hyp"]["PRD"]) for r in recs
            if r.get("hyp", {}).get("PRD") and abs(r["px"] - 37247.9) < 0.01} == {(1, 6)}


def test_app_matches_every_sample_with_truth(run):
    sc = run[2]
    assert sc["APP|LIVE"]["both"] == sc["APP|LIVE"]["n"] > 20


def test_kla_plan_range_follows_center_rule(run):
    k1 = [k["K1"] for k in run[1] if "K1" in k]
    assert len(k1) > 40 and all(all(k) for k in k1)
