"""변경 6개 기능의 end-to-end 통합 검증 (실제 이미지/위젯/워커 구동).

크리티컬 버그(데드락·정확도 변동·UI 상태 깨짐) 회귀 방지가 목적.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PIL import Image                                          # noqa: E402
from PyQt6.QtCore import QRect                                 # noqa: E402
from PyQt6.QtWidgets import QApplication                       # noqa: E402

from aoi_verification.app.models import slot as slot_mod       # noqa: E402
from aoi_verification.app.models.slot import ImageItem         # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _mk_img(path: Path, seed: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.random.RandomState(seed).randint(0, 255, (64, 64, 3), dtype=np.uint8)
    Image.fromarray(arr).save(str(path))


# ===========================================================================
# A. scan + 웨이퍼 전경 사진 제외 + 슬롯 부분집합
# ===========================================================================
def test_scan_keeps_dot_t_and_slot_subset(qapp, isolated_cache, tmp_path):
    ref, val = tmp_path / "ref", tmp_path / "val"
    seed = 0
    t_names: dict[Path, str] = {}
    for s in ("S01", "S02", "S03"):
        for side_root in (ref, val):
            _mk_img(side_root / s / "good1.png", seed); seed += 1
            _mk_img(side_root / s / "good2.png", seed); seed += 1
            # ★ .t 파일도 결함 사진이라 매칭에 들어간다
            t_names[side_root / s] = f"-869.{seed}.t.1.png"
            _mk_img(side_root / s / t_names[side_root / s], seed); seed += 1
            # 웨이퍼 전경 사진은 좌표가 없어 여전히 빠진다
            _mk_img(side_root / s / "CognexInSight17xx_Bottom.png", seed); seed += 1

    sr = slot_mod.scan(ref, val)
    assert sr.common_slot_names == ["S01", "S02", "S03"]
    for name in sr.common_slot_names:
        names = sorted(i.filename for i in sr.slots[name].ref_images)
        assert names == sorted(["good1.png", "good2.png",
                                t_names[ref / name]]), names

    # main_window._on_start 의 부분집합 필터와 동일 로직
    sel = {"S01", "S03"}
    sr.slots = {n: s for n, s in sr.slots.items() if n in sel}
    sr.ref_only = [n for n in sr.ref_only if n in sel]
    sr.val_only = [n for n in sr.val_only if n in sel]
    assert sr.common_slot_names == ["S01", "S03"]


def _build_tasks(tmp_path, sizes):
    """sizes = {slot: (n_ref, n_val)} → (tasks, real images on disk)."""
    seed = 100
    tasks = []
    for s, (nr, nv) in sizes.items():
        refs, vals = [], []
        for i in range(nr):
            p = tmp_path / "ref" / s / f"r{i}.png"
            _mk_img(p, seed); seed += 1
            refs.append(ImageItem(s, p, "ref"))
        for i in range(nv):
            p = tmp_path / "val" / s / f"v{i}.png"
            _mk_img(p, seed); seed += 1
            vals.append(ImageItem(s, p, "val"))
        tasks.append((s, refs, vals))
    return tasks


def _drain(scores, tasks):
    out = {}
    for slot, refs, vals in tasks:
        for r in refs:
            for v in vals:
                out[(slot, r.path, v.path)] = scores.get_pair(slot, r.path, v.path)
    return out


# ===========================================================================
# B. 디스크 점수 캐시(persist) 왕복 — 두 번째 실행이 재계산 없이 같은 결과를 낸다
#
# ※ 한때 이 자리에 '파이프라인 == 순차' 계열 테스트 4개가 있었다.  파이프라인
#    실행 경로(`_run_pipelined`)는 CNN 임베딩이 켜져야만 선택되는데 그 기능이
#    통째로 제거되면서 도달 불가가 되어 코드와 함께 지웠다.  다만 이 테스트만은
#    **디스크 점수 캐시 왕복을 검증하는 유일한 곳**이라 순차 경로로 옮겨 살린다.
# ===========================================================================
def test_persist_scores_round_trip(qapp, isolated_cache, tmp_path):
    from aoi_verification.app.config import SimilarityConfig
    from aoi_verification.app.similarity.slot_features import (
        SlotFeatureCache, SlotPrecomputeWorker, SlotScoreCache,
    )
    cfg = SimilarityConfig(persist_scores=True)
    tasks = _build_tasks(tmp_path, {"S01": (2, 3), "S02": (3, 2)})

    first = SlotScoreCache()
    w = SlotPrecomputeWorker(tasks, SlotFeatureCache(keep_lookahead=False),
                             first, release_after_slot=True, cfg=cfg)
    fin = []
    w.signals.finished.connect(lambda: fin.append(True))
    w.run()
    assert fin == [True]
    assert first.size() == (2 * 3 + 3 * 2)

    # 두 번째 실행 — 디스크 캐시에서 로드(재계산 없이)해도 동일 결과.
    second = SlotScoreCache()
    w2 = SlotPrecomputeWorker(tasks, SlotFeatureCache(keep_lookahead=False),
                              second, release_after_slot=True, cfg=cfg)
    w2.run()
    assert _drain(second, tasks) == _drain(first, tasks)


def _items(slot, n):
    return [ImageItem(slot, Path(f"/tmp/{slot}_{i}.png"), "ref") for i in range(n)]


def test_bulk_dialog_e2e(qapp, monkeypatch):
    from aoi_verification.app.ui.widgets import bulk_select_dialog as bsd
    fired = {}

    dlg = bsd.BulkSelectDialog(
        "t", {"S1": _items("S1", 1100)},
        actions=[("act", "ACT", "primary")],
    )
    # 1 페이지에서 한 장 선택 → 마지막 페이지로 가도 유지
    first = dlg._page_slice()[0][1]
    dlg._on_tile_toggle(first, True)
    dlg._go_page(dlg._page_count - 1)
    assert first.key in dlg._selected_keys
    last = dlg._page_slice()[-1][1]
    dlg._on_tile_toggle(last, True)

    # 슬라이더 변경(재렌더)해도 선택 유지 + 타일 크기 반영
    dlg._on_size_changed(240)
    assert dlg._tile_px == 240
    assert any(t._tile_px == 240 for t in dlg._tiles_by_key.values())

    # 우클릭 확대 — FullscreenViewer 를 가짜로 대체(디스플레이/블로킹 회피)
    opened = []

    class _FakeViewer:
        def __init__(self, path, parent=None):
            opened.append(path)

        def exec(self):
            return 0
    monkeypatch.setattr("aoi_verification.app.ui.widgets.zoom_window.FullscreenViewer",
                        _FakeViewer)
    dlg._open_zoom(last)
    assert opened == [last.path]

    # 교차 페이지 선택이 _fire 에 모두 포함
    captured = []
    dlg.selection_action.connect(lambda a, items: captured.append((a, items)))
    dlg._fire("act")
    assert captured and captured[0][0] == "act"
    keys = {it.key for it in captured[0][1]}
    assert first.key in keys and last.key in keys
    dlg.deleteLater()


def test_bulk_dialog_drag_select(qapp):
    from aoi_verification.app.ui.widgets import bulk_select_dialog as bsd
    dlg = bsd.BulkSelectDialog(
        "t", {"S1": _items("S1", 12)}, actions=[("act", "A", "primary")])
    dlg.resize(900, 700)
    dlg.show()
    QApplication.processEvents()
    dlg._relayout_grids()
    QApplication.processEvents()
    # 전체 viewport 를 덮는 사각형으로 드래그 선택 → 현재 페이지 타일 모두 선택.
    vp = dlg._scroll.viewport()
    dlg._select_in_rect(QRect(0, 0, vp.width() + 50, vp.height() + 5000))
    assert len(dlg._selected_keys) == 12
    dlg.deleteLater()


# ===========================================================================
# F. SelectPage 에 복원된 targets 가 검증 대상으로 들어가는지 (요청 6 가시적 끝단)
# ===========================================================================
def test_selectpage_restored_targets(qapp, isolated_cache):
    from aoi_verification.app.ui.pages.select_page import SelectPage
    sp = SelectPage()
    queue = _items("S1", 3) + _items("S2", 2)
    # 첫 장(S1_0)을 이전 기준 사진으로 복원했다고 가정
    restored = {"S1": [queue[0]]}
    remaining = [it for it in queue if it is not queue[0]]
    sp.load_state(queue=remaining, targets=restored)
    st = sp.get_state()
    # 복원 항목은 검증 대상(targets)에, 큐엔 없음
    assert st.targets["S1"] == [queue[0]]
    assert queue[0] not in st.queue
    # 남은 후보는 그대로 큐에 (현재 결정중 항목도 큐에 남아 표시만 제외됨)
    assert st.queue == remaining
    assert sp._current == remaining[0]


# ===========================================================================
# G. ref_history 매칭 복원 로직 (요청 6) — has_history/get_chosen → 매칭
# ===========================================================================
def test_ref_history_match_logic(isolated_cache, tmp_path):
    from aoi_verification.app.utils import ref_history
    ref_root = tmp_path / "ref"
    ref_root.mkdir()
    ref_history.save_chosen(ref_root, {"S1": ["a.png"], "S2": ["c.png"]})

    queue = [
        ImageItem("S1", Path("/x/S1/a.png"), "ref"),   # 매칭
        ImageItem("S1", Path("/x/S1/b.png"), "ref"),   # 비매칭
        ImageItem("S2", Path("/x/S2/c.png"), "ref"),   # 매칭
    ]
    chosen = ref_history.get_chosen(ref_root)
    wanted = {(s, n) for s, names in chosen.items() for n in names}
    matched = [it for it in queue if (it.slot, it.filename) in wanted]
    assert sorted(i.filename for i in matched) == ["a.png", "c.png"]
