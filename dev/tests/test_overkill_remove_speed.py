"""Overkill 튜닝 — 폴더를 뺄 때 렉(현택님 2026-11-23).

뺄 때 하는 일(다시 합치기 · 다시 그리기)이 UI 스레드에서 돌므로, 빨라진 계산이 **예전과 같은 결과**를 내는지와
웨이퍼 수만큼 되풀이되던 일(저장분 파일 읽기 · NAS resolve · Zone 마다 전체 훑기)이 한 번으로 줄었는지를 본다.
측정(합성 LOT 3 × 25장 × Defect 2000, Zone 20, 가운데 LOT 빼기): 1.43초 → 0.52초, resolve 한 번에 30 ms 지연을
넣으면 2.93초 → 0.69초."""

from __future__ import annotations

import os
import random
from dataclasses import fields, replace
from pathlib import Path

import pytest

from aoi_verification.app.threshold import preview as P
from aoi_verification.app.threshold import saved_picks as SP

T = pytest.importorskip("aoi_verification.app.ui.widgets.threshold_preview_dialog")


def _defects(n: int, seed: int = 0, wafer: int = 0) -> tuple:
    rnd = random.Random(seed)
    return tuple(P.Defect(index=i, zone_dir=rnd.choice(("Zones", "Recipe2-Zones")), zone=rnd.randint(1, 5),
                          bin=rnd.randint(1, 30), cls=rnd.randint(-5, 30), area=rnd.uniform(1, 500),
                          width=rnd.uniform(1, 30), length=rnd.uniform(1, 60), contrast=rnd.uniform(0, 90),
                          x=rnd.uniform(0, 3e5), y=rnd.uniform(0, 3e5), wafer=wafer,
                          by_filter=rnd.random() < 0.8, rtype=rnd.choice((None, 8)),
                          stage=rnd.choice((None, "dark"))) for i in range(n))


def test_with_wafer_equals_dataclass_replace():
    ds = _defects(300, seed=1)
    for d in ds:
        for i in (0, 1, 7):
            got, ref = T._with_wafer(d, i), replace(d, wafer=i)
            assert got == ref and hash(got) == hash(ref)
            assert all(getattr(got, f.name) == getattr(ref, f.name) for f in fields(P.Defect))
            assert type(got) is P.Defect
    assert T._with_wafer(ds[0], 0) is ds[0]                   # 이미 그 웨이퍼면 그대로
    with pytest.raises(Exception):
        T._with_wafer(ds[0], 3).wafer = 1                     # frozen 그대로


def _ref_merge(ws):
    """예전 merge_wafers 의 Defect 부분(replace) — 같은 결과인지 비교용."""
    same = [w for w in ws if not w.note and T.same_base(ws[0], w)]
    defects, goods = [], []
    for i, w in enumerate(same):
        ds = {d.index: replace(d, wafer=i) for d in w.defects + w.goods}
        defects += [ds[d.index] for d in w.defects]
        goods += [ds[d.index] for d in w.goods]
    return tuple(defects), tuple(goods)


def test_merge_wafers_same_result_as_before(tmp_path):
    ws = []
    for k in range(4):
        all_ = _defects(200, seed=10 + k)
        ws.append(T.WaferData(tmp_path / f"w{k}", defects=all_[:150], goods=all_[150:], px=(1.0, 1.0),
                              photos={(0, i): tmp_path / f"w{k}" / f"{i}.jpg" for i in range(0, 200, 3)}))
    m = T.merge_wafers(ws)
    assert (m.defects, m.goods) == _ref_merge(ws)
    assert [d.wafer for d in m.defects] == [i for i in range(4) for _ in range(150)]
    assert m.photos == {(i, k[1]): p for i, w in enumerate(ws) for k, p in w.photos.items()}


def test_by_zone_matches_full_scan_and_follows_new_defects(tmp_path):
    w = T.WaferData(tmp_path, defects=_defects(500, seed=3))
    keys = {(d.zone_dir, d.zone) for d in w.defects}
    for k in keys:
        assert w.by_zone()[k] == [d for d in w.defects if (d.zone_dir, d.zone) == k]     # 순서도 같다
    w.defects = _defects(40, seed=4)                           # 바꾸면 다시 나눈다(옛 묶음을 쓰지 않는다)
    assert sum(map(len, w.by_zone().values())) == 40
    w2 = replace(w, defects=w.defects[:10])                    # replace 로 옮겨 간 캐시도 쓰지 않는다
    assert sum(map(len, w2.by_zone().values())) == 10


@pytest.fixture
def dlg(styled_qapp, tmp_path):
    """LOT 3개(각 4장, 레시피 같음) — 읽기 스레드 없이 메모리에 올린다."""
    zones = {}
    d = T.ThresholdPreviewDialog()
    lots = []
    for L in range(3):
        lot = tmp_path / f"LOT{L}"
        ents = []
        for k in range(4):
            f = lot / f"W{L}{k}"
            f.mkdir(parents=True)
            all_ = _defects(120, seed=L * 10 + k)
            d._cache[f] = T.WaferData(f, defects=all_[:100], goods=all_[100:], zones=zones, px=(1.0, 1.0))
            ents.append((f, os.path.normcase(str(f))))
        d._expand[lot] = ents
        lots.append(lot)
    d.set_folders(lots)
    d.lots = lots
    yield d
    d.deleteLater()


def _state(d):
    return (d._w.folders, d._w.defects, d._w.goods, d.zone_keys(), d._zone,
            {k: d._count(k) for k in d.zone_keys()}, sorted(d._picked))


def test_remove_folder_same_as_never_added(dlg, tmp_path, styled_qapp):
    """가운데 LOT 를 뺀 화면 = 처음부터 두 LOT 만 고른 화면(남은 꼭 잡기도 같다)."""
    w = dlg._w
    dlg._picked = {T.key_of(x): x for x in w.defects[::37]}
    keep = {(T._folder_of(w, x), x.index) for x in w.defects[::37] if T._folder_of(w, x).parent != dlg.lots[1]}
    dlg.remove_folder(dlg.lots[1])
    got = _state(dlg)
    assert {(T._folder_of(dlg._w, x), x.index) for x in dlg._picked.values()} == keep

    fresh = T.ThresholdPreviewDialog()
    fresh._cache, fresh._expand = dlg._cache, dlg._expand
    fresh.set_folders([dlg.lots[0], dlg.lots[2]])
    fresh._picked = {T.key_of(x): x for x in fresh._w.defects if (T._folder_of(fresh._w, x), x.index) in keep}
    assert got == _state(fresh)
    fresh.deleteLater()


def test_render_reads_saved_picks_once(dlg, monkeypatch):
    """그릴 때 저장분 파일은 웨이퍼 수와 상관없이 한 번만 읽는다(예전: 웨이퍼마다 읽고 · resolve 했다)."""
    reads = []
    orig = SP._read_all
    monkeypatch.setattr(SP, "_read_all", lambda: reads.append(1) or orig())
    dlg.remove_folder(dlg.lots[0])
    assert len(dlg._w.folders) == 8 and len(reads) == 1
