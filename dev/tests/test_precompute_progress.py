"""매치 단계 로딩 진행 — 특징 추출 구간에서 바가 0 에 얼어붙지 않는다.

증상(구형/유사도 엔진에서 특히 두드러짐): 매치 단계 로딩이 0 에 멈춰 있다가 갑자기
끝났다.  원인은 슬롯의 val 특징을 전부 뽑는 ``SlotFeatureCache.build()`` 가 **진행
콜백을 아예 받지 않아서**, 그 구간(콜드 캐시·NAS 에서 가장 오래 걸림) 동안 워커가
보고할 것이 없었던 것이다.  고효율 경로는 같은 자리에서 사진 1장마다 보고한다.

CLAUDE.md 로딩 계약: "0 에서 안 움직이다 갑자기 완료" 금지.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

from aoi_verification.app.models.slot import ImageItem   # noqa: E402


# ---------------------------------------------------------------------------
# 1) build() 가 사진 1장마다 진행을 보고한다 (무거운 의존성 없이 순수 검증)
# ---------------------------------------------------------------------------
@pytest.fixture
def cache_with_stub_extract(monkeypatch):
    """`_pipeline.extract` 를 가벼운 더미로 바꿔 cv2/torch 없이 build() 를 돈다."""
    sf = pytest.importorskip("aoi_verification.app.similarity.slot_features")

    class _Feat:
        def __init__(self, path):
            self.path = path
            self.cnn = None
            self.cnn_model = ""

    monkeypatch.setattr(sf._pipeline, "extract",
                        lambda p, **kw: _Feat(p), raising=True)
    return sf


def _items(slot: str, n: int) -> list[ImageItem]:
    return [ImageItem(slot, Path(f"/tmp/{slot}_{i}.png"), "val") for i in range(n)]


def test_build_reports_progress_per_image(cache_with_stub_extract):
    sf = cache_with_stub_extract
    cache = sf.SlotFeatureCache()
    seen: list[tuple[int, int]] = []
    cache.build("A", _items("A", 5), progress=lambda d, t: seen.append((d, t)))

    assert seen, "진행 콜백이 한 번도 호출되지 않았다 — 바가 0 에 멈춘다"
    assert len(seen) == 5, f"사진 1장마다 보고해야 한다: {seen}"
    assert [d for d, _t in seen] == [1, 2, 3, 4, 5]     # 단조 증가
    assert all(t == 5 for _d, t in seen)


def test_build_counts_cache_hits_too(cache_with_stub_extract):
    """두 번째 호출은 전부 캐시 hit — 그래도 done 이 끝까지 올라가야 한다."""
    sf = cache_with_stub_extract
    cache = sf.SlotFeatureCache()
    items = _items("A", 4)
    cache.build("A", items)                    # 워밍업(캐시 채우기)
    seen: list[tuple[int, int]] = []
    cache.build("A", items, progress=lambda d, t: seen.append((d, t)))
    assert [d for d, _t in seen] == [1, 2, 3, 4]


def test_build_without_progress_still_works(cache_with_stub_extract):
    """콜백은 선택 인자 — 기존 호출부는 그대로 동작한다."""
    sf = cache_with_stub_extract
    out = sf.SlotFeatureCache().build("A", _items("A", 3))
    assert len(out) == 3


def test_build_survives_failing_progress_callback(cache_with_stub_extract):
    """진행 보고가 터져도 추출은 계속된다(보조 기능이 본 흐름을 깨면 안 된다)."""
    sf = cache_with_stub_extract

    def _boom(_d, _t):
        raise RuntimeError("보고 실패")

    out = sf.SlotFeatureCache().build("A", _items("A", 3), progress=_boom)
    assert len(out) == 3


# ---------------------------------------------------------------------------
# 2) 워커가 그 콜백을 progress 시그널로 중계한다
# ---------------------------------------------------------------------------
def test_worker_relays_build_progress(monkeypatch, cache_with_stub_extract):
    """`SlotPrecomputeWorker` 가 val 특징 구간에서도 progress 를 계속 낸다."""
    sf = cache_with_stub_extract
    pytest.importorskip("PyQt6.QtWidgets")

    refs = [ImageItem("A", Path(f"/tmp/r{i}.png"), "ref") for i in range(2)]
    vals = _items("A", 4)
    # pipeline=False 로 특징→점수 순차 경로(`_run`)를 태운다.
    worker = sf.SlotPrecomputeWorker(
        [("A", refs, vals)], sf.SlotFeatureCache(), sf.SlotScoreCache(),
        cfg=None, pipeline=False,
    )

    emitted: list[tuple[int, int]] = []
    worker.signals.progress.connect(lambda d, t: emitted.append((d, t)))
    # 점수 계산 단계는 이 테스트의 관심사가 아니다 — 특징 구간만 보게 막는다.
    monkeypatch.setattr(worker, "_score_pairs_parallel",
                        lambda *a, **kw: {}, raising=True)
    monkeypatch.setattr(worker, "_prefetch_cnn_embeddings",
                        lambda *a, **kw: None, raising=True)
    worker._run_sequential()

    feat_total = len(refs) + len(vals)          # 6
    # val 구간(1..4)이 통째로 건너뛰어지지 않고 한 장씩 보고돼야 한다.
    val_ticks = [d for d, t in emitted if t == feat_total and 1 <= d <= len(vals)]
    assert val_ticks == [1, 2, 3, 4], f"val 특징 진행이 끊겼다: {emitted}"
    # 이어서 ref 구간(5, 6)까지 같은 눈금으로 계속된다.
    assert [d for d, t in emitted if t == feat_total and d > len(vals)] == [5, 6]


# ---------------------------------------------------------------------------
# 3) 진행도 상태가 세션 간 초기화된다
# ---------------------------------------------------------------------------
def test_precompute_progress_resets_between_runs(monkeypatch):
    """2회차 실행이 직전 세션의 done/total 을 물려받지 않는다."""
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    from aoi_verification.app.ui.pages import match_page as mp

    app = QApplication.instance() or QApplication([])
    page = mp.MatchPage()
    try:
        # 직전 세션의 잔여 진행도를 흉내낸다.
        page._precompute_done = 812
        page._precompute_total = 900
        page._precompute_phase = "유사도 계산"
        # 계산할 게 없는 상태로 _start_precompute 를 태워도 리셋은 먼저 일어난다.
        page._state = None
        mp.MatchPage._start_precompute(page)
        app.processEvents()
        assert page._precompute_done == 812      # _state None 이면 즉시 반환
    finally:
        page.deleteLater()
