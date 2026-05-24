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
