"""두 Scanresult 스캔의 번호 출발점 차이 — 매칭에서만 맞춘다(``coords/scan_start.py``, 사용자 결정 A 2026-11-15).

골든(관측): 같은 웨이퍼 SQ67P01-D1 을 6412 CTX(AOI-11)와 6321 CTX(AOI-8)가 찍었고, 두 스캔에 같이 찍힌 Defect 5개는
die 안 x·y 가 3 µm 안에서 같은데 장비 번호가 (1, 2) 어긋났다.  6321 쪽은 Wafer2Table 이 없어 앱 번호 기준이 장비와도
다르다((2, 327), ⚠) — 그래도 출발점은 앱이 쓴 기준으로 구하므로 차이가 맞게 나와야 한다.
"""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from aoi_verification.app.coords import camtek_ini, scan_start as ss
from aoi_verification.app.coords.models import DefectCoord
from aoi_verification.app.coords.wafer_geometry import CamtekGeometry

ROOT = Path(__file__).resolve().parents[2]
CTX6412 = ROOT / "dev" / "좌표 확인" / "장비격자(ExportData)" / "Camtek_CTX" / "SQ67P01-D1"
CTX6321 = ROOT / "dev" / "좌표 확인" / "장비격자(ExportData)" / "Camtek_CTX_6321" / "SQ67P01-D1"


@pytest.fixture(autouse=True)
def _fresh_cache():
    ss.scan_start.cache_clear()
    yield
    ss.scan_start.cache_clear()


def _cache(folder: Path):
    return {folder / f"{stem}.jpeg": c for stem, c in camtek_ini.load_folder(folder).items()}


def _items(cache):
    return [SimpleNamespace(path=p) for p in cache]


def _pairs(ref, val):
    """die 안 x·y 가 10 µm 안이고 die 번호가 8 칸 안인 짝 — 같은 Defect(두 스캔에 같이 찍힌 것은 이 5개뿐)."""
    return [(r, v) for r in ref.values() for v in val.values()
            if math.hypot(r.x - v.x, r.y - v.y) < 10 and abs(r.col - v.col) <= 8 and abs(r.row - v.row) <= 8]


def test_real_two_scans_line_up_after_the_start_shift():
    assert ss.scan_start(CTX6412) == (1, 313)          # Δ (6, 6) · 번호표 (5, 319)
    assert ss.scan_start(CTX6321) == (4, 320)          # Δ (6, 7) · 앱 기준 (2, 327) — 중심 없음
    ref, val = _cache(CTX6321), _cache(CTX6412)
    before = _pairs(ref, val)
    assert len(before) == 5 and all((v.col - r.col, v.row - r.row) == (-3, -7) for r, v in before)
    tasks = [("SQ67P01-D1", _items(ref), _items(val))]
    out, applied = ss.apply_start_shift({**ref, **val}, tasks)
    assert applied == {"SQ67P01-D1": (-3, -7)}
    moved = {p: out[p] for p in val}
    assert all((v.col, v.row) == (r.col, r.row) for r, v in _pairs(ref, moved))   # 같은 die = 같은 번호
    assert len(_pairs(ref, moved)) == 5
    assert all(out[p] is ref[p] for p in ref)          # 기준 쪽은 손대지 않는다
    assert all(val[p].col == camtek_ini.load_folder(CTX6412)[p.stem].col for p in val)   # 원본 캐시도 그대로


def test_slot_with_notch_rotation_is_left_alone():
    """notch 를 돌릴 사진이 한 장이라도 있으면 손대지 않는다 — 회전이 번호를 웨이퍼 중심에서 다시 매긴다."""
    ref, val = _cache(CTX6321), _cache(CTX6412)
    cache = {**ref, **val}
    tasks = [("SQ67P01-D1", _items(ref), _items(val))]
    one = next(iter(ref))
    assert ss.apply_start_shift(cache, tasks, lambda p: p == one) == (cache, {})
    assert ss.apply_start_shift(cache, tasks, lambda p: False)[1] == {"SQ67P01-D1": (-3, -7)}


def test_rotation_already_cancels_the_start_difference():
    """왜 회전 슬롯을 빼는가: 번호 기준이 (3, 7) 다른 두 폴더의 같은 Defect 는 돌리면 이미 같은 칸이다(합성)."""
    from aoi_verification.app.coords import notch
    f1 = notch.Frame(1000.0, 1000.0, 150.4, 160.6, 150000.0)
    f2 = notch.Frame(1000.0, 1000.0, 150.4 - 3, 160.6 - 7, 150000.0)
    c1 = _c(col=120, row=140)
    c2 = _c(col=117, row=133)
    r1, r2 = notch.rotate_coord(c1, notch.UP, f1), notch.rotate_coord(c2, notch.UP, f2)
    assert (r1.col, r1.row) == (r2.col, r2.row)


def test_same_scan_twice_changes_nothing():
    cache = _cache(CTX6412)
    out, applied = ss.apply_start_shift(cache, [("s", _items(cache), _items(cache))])
    assert out is cache and applied == {}


# ── 조건 — 합성 폴더(가정): 기하는 stub, WaferMapRecipe.ini 만 쓴다 ──────────────────────────
_GEOM = CamtekGeometry(pitch_x=1000.0, pitch_y=1000.0, col_origin=5, row_total=319, source="ProductInfo.ini[ExportData]")


def _recipe(d: Path, **kw):
    v = {"Enable": 1, "PointsSet": 1, "Mirror": 0, "ImportFlippedMap": 0,
         "Ref1": (164, 161), "Cust1": (158, 155), "Ref2": (165, 162), "Cust2": (159, 156), "ShiftDieX": 0}
    v.update(kw)
    d.mkdir(parents=True, exist_ok=True)
    lines = ["[GENERAL]", "Enable=1", "[Input_Update]"]
    lines += [f"{k}={v[k]}" for k in ("Enable", "PointsSet", "Mirror", "ImportFlippedMap")]
    for name in ("Ref1", "Cust1", "Ref2", "Cust2"):
        key = ("RefPoint" if name.startswith("Ref") else "CustPoint") + name[-1]
        lines += [f"{key}.col={v[name][0]}", f"{key}.row={v[name][1]}"]
    lines += ["[ImportInfo]", f"ShiftDieX={v['ShiftDieX']}", "ShiftDieY=0"]
    (d / "WaferMapRecipe.ini").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


@pytest.fixture
def stub_geom(monkeypatch):
    geoms = {}
    monkeypatch.setattr(ss, "camtek_geometry", lambda f: geoms.get(f, _GEOM))
    return geoms


@pytest.mark.parametrize("kw", [{"Enable": 0}, {"PointsSet": 0}, {"Mirror": 1}, {"ImportFlippedMap": 1},
                                {"ShiftDieX": 1}, {"Ref2": (166, 162)}])
def test_untrusted_recipe_gives_no_start(tmp_path, stub_geom, kw):
    assert ss.scan_start(_recipe(tmp_path / "w", **kw)) is None


def test_start_from_the_recipe(tmp_path, stub_geom):
    assert ss.scan_start(_recipe(tmp_path / "w")) == (1, 313)
    assert ss.scan_start(tmp_path) is None                       # 파일 없음


def test_fallback_or_block_geometry_gives_no_start(tmp_path, stub_geom):
    d = _recipe(tmp_path / "w")
    stub_geom[d] = CamtekGeometry(pitch_x=1.0, pitch_y=1.0, col_origin=0, row_total=7, source="fallback")
    assert ss.scan_start(d) is None
    stub_geom[d] = None
    ss.scan_start.cache_clear()
    assert ss.scan_start(d) is None


def _c(src="camtek_ini", col=10, row=20):
    return DefectCoord(col=col, row=row, x=1.0, y=2.0, source=src)


def test_only_scanresult_vals_move_and_unknown_side_leaves_slot(tmp_path, stub_geom):
    a = _recipe(tmp_path / "a")                                    # S (1, 313)
    b = _recipe(tmp_path / "b", Ref1=(163, 160), Ref2=(164, 161))  # S (0, 314)
    cache = {a / "r.jpg": _c(), b / "v.jpg": _c(), b / "k.jpg": _c("kla"), b / "l.jpg": _c("camtek_live")}
    items = lambda *ps: [SimpleNamespace(path=p) for p in ps]     # noqa: E731
    out, applied = ss.apply_start_shift(cache, [("s", items(a / "r.jpg"), items(b / "v.jpg", b / "k.jpg", b / "l.jpg"))])
    assert applied == {"s": (-1, 1)}
    assert (out[b / "v.jpg"].col, out[b / "v.jpg"].row) == (11, 19)
    assert out[b / "k.jpg"] is cache[b / "k.jpg"] and out[b / "l.jpg"] is cache[b / "l.jpg"]
    assert (cache[b / "v.jpg"].col, cache[b / "v.jpg"].row) == (10, 20)      # 원본은 그대로

    c = tmp_path / "c"                                             # WaferMapRecipe 없음 → 모름
    c.mkdir()
    cache2 = {a / "r.jpg": _c(), c / "v.jpg": _c()}
    assert ss.apply_start_shift(cache2, [("s", items(a / "r.jpg"), items(c / "v.jpg"))]) == (cache2, {})
    mixed = {**cache2, b / "v.jpg": _c()}                          # 한쪽 폴더 둘이 서로 다르면(모름 포함) 손대지 않는다
    assert ss.apply_start_shift(mixed, [("s", items(a / "r.jpg"), items(c / "v.jpg", b / "v.jpg"))])[1] == {}


def test_scheduler_matches_the_real_pairs_only_with_the_start_shift(monkeypatch):
    """매칭 화면 경로: 두 스캔에 같이 찍힌 5개가 서로 짝이 된다 — 출발점을 모르면 하나도 안 맞는다."""
    pytest.importorskip("PyQt6.QtCore")
    from aoi_verification.app import config
    from aoi_verification.app.models.slot import ImageItem
    from aoi_verification.app.workers import coord_matcher as cm

    ref, val = _cache(CTX6321), _cache(CTX6412)
    base = {**ref, **val}
    want = {next(p for p in ref if ref[p] is r): next(p for p in val if val[p] is v) for r, v in _pairs(ref, val)}
    monkeypatch.setattr(cm, "_resolve_batch", lambda paths, cb=None: dict(base))
    tasks = [("SQ67P01-D1", [ImageItem("SQ67P01-D1", p, "ref") for p in ref],
              [ImageItem("SQ67P01-D1", p, "val") for p in val])]

    def run():
        res: dict = {}
        sch = cm.CoordScheduler(tasks, cfg=config.SimilarityConfig(engine="coordinate"), results=res)
        sch._run()
        return sch, {r: res[("SQ67P01-D1", r)][0][0] for r in want if res.get(("SQ67P01-D1", r))}

    sch, got = run()
    assert got == want and sch.start_shifts == {"SQ67P01-D1": (-3, -7)}
    assert all(base[p] is c for p, c in {**ref, **val}.items())     # 화면 · 엑셀이 보는 좌표는 그대로
    monkeypatch.setattr(ss, "_map_delta", lambda folder: None)
    ss.scan_start.cache_clear()
    sch, got = run()
    assert got == {} and sch.start_shifts == {}
