"""임시 레퍼런스 로깅 회귀 테스트.

- `reference_log` 가 `결과/레퍼런스/*.jsonl` 에 옵션/ref 레코드를 한글 깨짐 없이 기록.
- `EfficiencyScheduler` 가 ref별 처리 장치(cpu/gpu/npu)를 `device_results` 에 기록.
- `match_page._log_reference` 가 top-10 캡 + 장치 + 최종 매치를 남긴다.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from aoi_verification.app.models.slot import ImageItem
from aoi_verification.app.utils import reference_log as rl


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------------------
def test_session_path_and_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(rl.paths, "results_dir", lambda: tmp_path)
    p = rl.session_path("s1")
    assert p is not None
    assert p.parent.name == "레퍼런스"
    assert p.suffix == ".jsonl"

    rl.write_options(p, {"engine": "efficiency", "한글": "테스트", "use_npu": True})
    rl.append_ref(p, {"slot": "A1", "device": "npu",
                      "top10": [{"rank": 0, "filename": "v2.png", "score": 0.91}]})
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    opts, ref = json.loads(lines[0]), json.loads(lines[1])
    assert opts["type"] == "options" and opts["engine"] == "efficiency"
    assert opts["한글"] == "테스트"                 # ensure_ascii=False
    assert ref["type"] == "ref" and ref["device"] == "npu"


def test_writes_never_raise():
    # None 경로/이상 입력에도 예외를 던지지 않는다(세션 흐름 보호).
    rl.write_options(None, {"x": 1})
    rl.append_ref(None, {"x": 1})
    rl.append_ref(Path("/nonexistent_dir_zzz/x.jsonl"), {"x": 1})


# ---------------------------------------------------------------------------
def test_scheduler_records_device_per_ref(qapp):
    from aoi_verification.app.workers import efficiency_matcher as eff

    class _FakeUnit:
        def __init__(self, tag):
            self.tag = tag

        def match_batch(self, refs, vals):
            return {Path(r.path): [] for r in refs}

    import unittest.mock as mock
    refs = [ImageItem(slot="A1", path=Path("r0.png"), side="ref"),
            ImageItem(slot="A1", path=Path("r1.png"), side="ref")]
    vals = [ImageItem(slot="A1", path=Path("v0.png"), side="val")]
    tasks = [("A1", refs, vals)]
    dev: dict = {}
    with mock.patch.object(eff, "build_units", lambda cfg, thr: [_FakeUnit("gpu")]):
        sched = eff.EfficiencyScheduler(tasks, cfg=None, threshold=0.0,
                                        results={}, device_results=dev)
        sched._run()
    assert dev[("A1", Path("r0.png"))] == "gpu"
    assert dev[("A1", Path("r1.png"))] == "gpu"


# ---------------------------------------------------------------------------
def test_match_page_log_reference(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(rl.paths, "results_dir", lambda: tmp_path)
    from aoi_verification.app.ui.pages.match_page import MatchPage
    from aoi_verification.app import config

    mp = MatchPage()
    mp._session_id = "sess"
    mp._engine_cfg = config.SimilarityConfig(engine="efficiency", use_npu=True,
                                             accel_concurrency=32, embed_batch=1)
    mp._threshold = 0.6
    mp._auto_mode = False
    mp._model_name = "basic"
    mp._mode_direction = "A→B"
    mp._start_reference_log()                       # 옵션 헤더 기록
    assert mp._ref_log_path is not None

    ref = ImageItem(slot="A1", path=Path("/tmp/r0.png"), side="ref")
    mp._current = ref
    mp._result_device[("A1", Path("/tmp/r0.png"))] = "gpu"
    # 12개 후보 → top-10 으로 잘려야.
    cands = [(Path(f"/tmp/v{i}.png"), 0.99 - 0.01 * i) for i in range(12)]
    mp._log_reference(cands, Path("/tmp/v3.png"), 3, "pick")

    lines = Path(mp._ref_log_path).read_text(encoding="utf-8").splitlines()
    opts = json.loads(lines[0])
    rec = json.loads(lines[1])
    assert opts["engine"] == "efficiency" and opts["use_npu"] is True
    assert rec["device"] == "gpu"
    assert len(rec["top10"]) == 10                  # 캡
    assert rec["top10"][0]["filename"] == "v0.png"
    assert rec["picked"] == {"filename": "v3.png", "rank": 3}


# ---------------------------------------------------------------------------
def test_result_page_logs_final_matches_on_export(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(rl.paths, "results_dir", lambda: tmp_path)
    from aoi_verification.app.ui.pages.result_page import ResultPage
    from aoi_verification.app.models.result import FinalResult, MatchResult, MissEntry

    ref_log = rl.session_path("sess")            # 빈 세션 파일 경로
    page = ResultPage()
    page.set_reference_log(ref_log)
    # 엔진 뷰: A1 은 정답(b.png)을 후보엔 넣었으나 1위는 x.png(=변별력 실패, rank 1),
    #          A2 는 정답(d.png)이 후보에 아예 없음(=recall 실패, rank -1).
    page.set_engine_view({
        ("A1", "a.png"): {"order": ["x.png", "b.png", "y.png"],
                          "score": {"x.png": 0.94, "b.png": 0.93, "y.png": 0.90},
                          "device": "gpu", "top1": ("x.png", 0.94), "n": 3},
        ("A2", "c.png"): {"order": ["p.png", "q.png"],
                          "score": {"p.png": 0.95, "q.png": 0.91},
                          "device": "npu", "top1": ("p.png", 0.95), "n": 2},
        ("A3", "e.png"): {"order": ["z.png"], "score": {"z.png": 0.9},
                          "device": "npu", "top1": ("z.png", 0.9), "n": 1},
    })
    page._result = FinalResult(
        mode="single", ref_machine="1호기", val_machine="3호기",
        matches=[
            MatchResult(slot="A1", ref_path=Path("/r/a.png"),
                        val_path=Path("/v/b.png"), score=0.88),
            MatchResult(slot="A2", ref_path=Path("/r/c.png"),
                        val_path=Path("/v/d.png"), score=0.71),
        ],
        unmatched_refs=[MissEntry(slot="A3", side="ref", path=Path("/r/e.png"))],
    )
    page._save_path = Path("/tmp/out.xlsx")
    page._log_final_matches()

    lines = Path(ref_log).read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[-1])
    assert rec["type"] == "final"
    assert rec["n_matches"] == 2 and rec["n_unmatched"] == 1
    assert rec["recall_miss"] == 1                      # A2 정답이 엔진 랭킹에 없음
    m0 = rec["matches"][0]
    assert m0["ref_filename"] == "a.png" and m0["user_val"] == "b.png"
    assert m0["device"] == "gpu"
    assert m0["engine_top1_val"] == "x.png"             # 엔진 초기 매치
    assert m0["user_val_engine_rank"] == 1              # 정답은 후보 2위(변별력 실패)
    m1 = rec["matches"][1]
    assert m1["user_val"] == "d.png" and m1["user_val_engine_rank"] == -1   # recall 실패
    assert rec["unmatched"][0]["slot"] == "A3"
    assert rec["unmatched"][0]["engine_top1_val"] == "z.png"
    assert rec["save_path"] == "/tmp/out.xlsx"
