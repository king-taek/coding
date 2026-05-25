"""정답 만들기 페이지 — 복수 정답 기록/저장 헤드리스 테스트."""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PyQt6.QtWidgets")
pytest.importorskip("cv2")
from PyQt6.QtWidgets import QApplication

from aoi_verification.app.models.slot import ImageItem


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _mk(path, seed):
    import cv2
    rng = np.random.default_rng(seed)
    cv2.imwrite(str(path), (rng.random((40, 40, 3)) * 255).astype(np.uint8))


def test_groundtruth_multi_answer_save(qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.pages.groundtruth_page import GroundTruthPage
    from aoi_verification.app.utils import paths as _paths
    from aoi_verification.app import config
    monkeypatch.setattr(_paths, "results_dir", lambda: tmp_path)

    refs, vals = [], []
    for i in range(2):
        p = tmp_path / f"r{i}.jpg"; _mk(p, i); refs.append(ImageItem(slot="S1", path=p, side="ref"))
    for i in range(4):
        p = tmp_path / f"v{i}.jpg"; _mk(p, 50 + i); vals.append(ImageItem(slot="S1", path=p, side="val"))

    page = GroundTruthPage()
    page.load_state([("S1", refs, vals)], cfg=config.SimilarityConfig(engine="basic"),
                    session_id="gt1")
    assert len(page._queue) == 2

    # ref0 정답으로 2개 선택(복수), ref1 은 1개 — truth 딕셔너리에 직접 기록
    page._idx = 0
    page._truth[page._key(0)] = ["v1.jpg", "v3.jpg"]
    page._idx = 1
    page._truth[page._key(1)] = ["v0.jpg"]
    page._on_save()

    out = tmp_path / "레퍼런스" / "groundtruth_gt1.jsonl"
    assert out.exists()
    recs = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    cfg = [r for r in recs if r["type"] == "truth_config"][0]
    assert cfg["n_refs"] == 2 and cfg["n_refs_with_answer"] == 2
    truths = {r["ref_filename"]: r for r in recs if r["type"] == "truth"}
    assert sorted(truths["r0.jpg"]["correct"]) == ["v1.jpg", "v3.jpg"]   # 복수 정답
    assert truths["r0.jpg"]["n_correct"] == 2
    assert truths["r1.jpg"]["correct"] == ["v0.jpg"]


def test_groundtruth_build_smoke(qapp):
    from aoi_verification.app.ui.pages.groundtruth_page import GroundTruthPage
    p = GroundTruthPage()
    assert hasattr(p, "grid") and hasattr(p, "_on_save")
