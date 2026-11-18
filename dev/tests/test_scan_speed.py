"""Overkill 튜닝 Scan 사진 로딩 속도 개선 회귀 가드 — 값은 그대로이고 일만 줄었는지 본다.

측정(합성 Scan 3000장 · 지연 시뮬레이션)에서 나온 네 가지:
  - ``rules.read_zones`` 가 폴더 항목마다 stat 하지 않는다(사진 3000장 → stat 3006회, NAS 12 초).
  - ``resolve_xy_many`` = ``resolve_xy`` 를 하나씩 부른 것과 같은 결과(stat 만 한 번).
  - ``crops_from`` 의 부분 디코드 = 전체 디코드와 같은 픽셀.
  - Crop 영속 캐시(``scan_crop_cache``) 적중 · 빗나감 · 깨짐 · 원본 변경.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from aoi_verification.app.coords import scan_crop_cache as scc  # noqa: E402
from aoi_verification.app.coords import scan_image as si  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_caches():
    si.clear_caches()
    yield
    si.clear_caches()


def _noise_jpeg(path: Path, w=400, h=300, mode="L", **save):
    rng = np.random.default_rng(7)
    a = (rng.integers(0, 256, (h, w), dtype=np.uint8) // 2 + np.arange(w, dtype=np.uint8)[None, :] // 4).astype(np.uint8)
    im = Image.fromarray(a, "L")
    if mode == "RGB":
        im = Image.merge("RGB", (im, im.transpose(Image.Transpose.FLIP_LEFT_RIGHT), im))
    im.save(path, quality=90, **save)
    return path


def _full_cut(path, box):
    with Image.open(path) as s:
        im = s.copy()
    im.load()
    return np.array(si.cut(im, box))


@pytest.mark.parametrize("mode,save", [("L", {}), ("RGB", {}), ("L", {"progressive": True})])
def test_partial_decode_equals_full_decode(tmp_path, mode, save):
    p = _noise_jpeg(tmp_path / "s.jpg", mode=mode, **save)
    for box in [(100, 50, 60, 60), (-20, -20, 60, 60), (350, 200, 60, 60), (10, 120, 60, 60), (0, 0, 400, 299)]:
        got = si.crops_from(p, [box])
        assert got is not None and np.array_equal(np.array(got[0]), _full_cut(p, box)), box
    two = si.crops_from(p, [(10, 10, 30, 30), (200, 100, 30, 30)])
    assert [np.array_equal(np.array(c), _full_cut(p, b)) for c, b in zip(two, [(10, 10, 30, 30), (200, 100, 30, 30)])] == [True, True]


def test_partial_decode_really_stops_early(tmp_path):
    """맨 아래까지 안 닿는 Crop 이면 부분 디코드 경로(_decode_top)를 탄다 — 아니면 속도 개선이 조용히 사라진다."""
    p = _noise_jpeg(tmp_path / "s.jpg")
    assert si._decode_top(p, 100) is not None
    assert si._decode_top(p, 300) is None                    # 전체 높이 이상 — 전체 디코드로


def test_partial_decode_truncated_file_is_not_trusted(tmp_path):
    p = _noise_jpeg(tmp_path / "s.jpg")
    data = p.read_bytes()
    bad = tmp_path / "t.jpg"
    bad.write_bytes(data[: len(data) // 5])                  # 아래 줄은 파일에 없다
    assert si.crops_from(bad, [(10, 200, 30, 30)]) is None   # 전체 디코드와 같은 결론(못 읽음)


def _scan_folder(tmp_path):
    folder = tmp_path / "W"
    folder.mkdir()
    (folder / "Params_WaferInfo.ini").write_text("[Info]\nRefPixelSizeX=1.0\nRefPixelSizeY=1.0\n")
    rows = ["Version=1"]
    for i in range(6):
        name = f"{i}.{i}.t.{i}.jpeg"
        _noise_jpeg(folder / name, 200, 100)
        rows.append(f"{name},{1000 + i * 150},{2000 + i * 40}")
    (folder / si.SCAN_LIST_NAME).write_text("\n".join(rows) + "\n")
    return folder


def test_resolve_xy_many_equals_one_by_one(tmp_path):
    folder = _scan_folder(tmp_path)
    xys = [(1000.0, 2000.0), (1180.0, 2060.0), (1500.0, 2100.0), (9e6, 9e6), (1090.0, 2020.0)]
    many = si.resolve_xy_many(folder, xys)
    one = [si.resolve_xy(folder, xy) for xy in xys]
    assert many == one and [m.status for m in many][3] == si.NO_CANDIDATE


def test_resolve_xy_many_stats_manifest_once(tmp_path, monkeypatch):
    folder = _scan_folder(tmp_path)
    si.resolve_xy_many(folder, [(1000.0, 2000.0)])           # 색인 만들기
    calls = []
    real = si._mtime
    monkeypatch.setattr(si, "_mtime", lambda p: (calls.append(p), real(p))[1])
    si.resolve_xy_many(folder, [(1000.0 + i, 2000.0) for i in range(50)])
    assert len(calls) == 2                                   # 목록 + 폴더, 결함 수와 무관


def test_read_zones_does_not_stat_every_entry(tmp_path, monkeypatch):
    from aoi_verification.app.threshold import rules as R
    w = tmp_path / "W"
    (w / "Zones").mkdir(parents=True)
    (w / "Zones" / "a.ini").write_text("[Zone]\nZoneID=3\nName=x\n")
    for i in range(30):
        (w / f"{i}.t.jpeg").write_bytes(b"x")
    R.read_zones.cache_clear()
    monkeypatch.setattr(Path, "is_dir", lambda self: pytest.fail("항목마다 is_dir() 하면 NAS 에서 느리다"))
    assert isinstance(R.read_zones(w), dict)
    R.read_zones.cache_clear()


# ---------------------------------------------------------------- Crop 캐시
def _src(tmp_path):
    p = tmp_path / "src.jpeg"
    _noise_jpeg(p, 120, 80)
    return p


def _crop():
    return Image.fromarray(np.arange(30 * 30, dtype=np.uint16).reshape(30, 30).astype(np.uint8), "L")


def test_crop_cache_roundtrip_is_lossless(tmp_path):
    folder, src = tmp_path / "F", _src(tmp_path)
    folder.mkdir()
    (folder / si.SCAN_LIST_NAME).write_text("Version=1\n")
    sig = scc.manifest_sig(folder)
    crop = _crop()
    assert scc.get(folder, sig, (1.0, 2.0), (1.5, 1.5)) is None          # 빗나감
    scc.put(folder, sig, (1.0, 2.0), (1.5, 1.5), crop, src)
    got = scc.get(folder, sig, (1.0, 2.0), (1.5, 1.5))
    assert got is not None and got[1] == str(src) and np.array_equal(np.array(got[0]), np.array(crop))
    assert scc.get(folder, sig, (1.0, 2.5), (1.5, 1.5)) is None          # 좌표가 다르면 다른 키
    assert scc.get(folder, sig, (1.0, 2.0), (1.6, 1.5)) is None          # 픽셀 크기가 다르면 다른 키


def test_crop_cache_misses_when_manifest_or_source_changes(tmp_path):
    folder, src = tmp_path / "F", _src(tmp_path)
    folder.mkdir()
    lst = folder / si.SCAN_LIST_NAME
    lst.write_text("Version=1\n")
    sig = scc.manifest_sig(folder)
    scc.put(folder, sig, (1.0, 2.0), (1.5, 1.5), _crop(), src)
    lst.write_text("Version=1\nx.jpg,1,2\n")                              # 목록이 바뀜 → 키가 달라진다
    assert scc.get(folder, scc.manifest_sig(folder), (1.0, 2.0), (1.5, 1.5)) is None
    assert scc.get(folder, sig, (1.0, 2.0), (1.5, 1.5)) is not None
    src.write_bytes(src.read_bytes() + b"\0")                              # 원본이 바뀜(크기) → 적중 아님
    assert scc.get(folder, sig, (1.0, 2.0), (1.5, 1.5)) is None
    os.remove(src)                                                         # 원본이 사라짐
    assert scc.get(folder, sig, (1.0, 2.0), (1.5, 1.5)) is None


def test_crop_cache_corrupt_or_partial_file_is_a_miss(tmp_path):
    folder, src = tmp_path / "F", _src(tmp_path)
    folder.mkdir()
    (folder / si.SCAN_LIST_NAME).write_text("Version=1\n")
    sig = scc.manifest_sig(folder)
    scc.put(folder, sig, (1.0, 2.0), (1.5, 1.5), _crop(), src)
    f = scc._path(folder, sig, (1.0, 2.0), (1.5, 1.5), si.CROP_SIDE_UM)
    f.write_bytes(f.read_bytes()[:40])                                     # 쓰다 끊긴 파일
    assert scc.get(folder, sig, (1.0, 2.0), (1.5, 1.5)) is None
    assert not list(f.parent.glob("*.part.png"))                           # 임시 파일이 남지 않는다
    assert scc.get(folder, None, (1.0, 2.0), (1.5, 1.5)) is None           # 목록이 없으면 캐시를 쓰지 않는다
    scc.put(folder, sig, (1.0, 2.0), None, _crop(), src)                   # 픽셀 크기를 모르면 저장 안 함
    assert len(list(f.parent.glob("*.png"))) == 1


def test_scan_crops_second_call_is_served_from_cache(styled_qapp, tmp_path, monkeypatch):
    """같은 웨이퍼를 다시 열면 Scan 원본을 열지 않고도 같은 Crop 이 나온다(새 세션 = 프로세스 캐시 없음)."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    folder = _scan_folder(tmp_path)
    items = [(("k", i), folder, (1000.0 + 30 * i, 2000.0 + 8 * i)) for i in range(6)]
    first = T.scan_crops(items)
    assert len(first) == 6
    si.clear_caches()                                                      # 새 실행처럼 프로세스 캐시를 비운다
    opened = []
    real = Image.open
    monkeypatch.setattr(Image, "open", lambda p, *a, **k: (opened.append(str(p)), real(p, *a, **k))[1])
    again = T.scan_crops(items)
    assert not [p for p in opened if p.endswith(".jpeg")]                  # Scan 원본은 열지 않았다(캐시 PNG 만)
    assert set(again) == set(first)
    for k in first:
        assert first[k] == again[k] and first[k].text("src") == again[k].text("src")   # 픽셀·원본 이름 동일


def test_ensure_scans_only_cuts_visible_page_and_next(styled_qapp, tmp_path, monkeypatch):
    """Defect 가 수천 개여도 보이는 장 + 한 쪽만 자른다(예전엔 끝까지 이어서 잘랐다)."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    import importlib.util
    spec = importlib.util.spec_from_file_location("_tpd", Path(__file__).with_name("test_threshold_preview_dialog.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _scan_wafer = mod._scan_wafer
    w, target = _scan_wafer(tmp_path)
    asked = []

    class Fake:
        def __init__(self, token, items):
            asked.append(list(items))
            self.signals = type("S", (), {"progress": _Sig(), "batch": _Sig(), "done": _Sig()})()

        def isRunning(self):
            return True

        def start(self):
            pass

    class _Sig:
        def connect(self, *_a):
            pass

    monkeypatch.setattr(T, "_ScanLoad", Fake)
    monkeypatch.setattr(T, "PAGE_TILES", 2)
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(T.load_wafer(w))
        dlg._set_zone((target.zone_dir, target.zone))
        dlg._set_tab("keep")
        n_pending = sum(1 for d, _ in dlg._items() if dlg._scan_pending(d))
        assert n_pending > dlg._shown + 2                    # 자를 것은 더 있지만
        assert asked and 0 < len(asked[-1]) <= dlg._shown + 2  # 이번에는 보이는 장 + 한 쪽까지만 맡긴다
        from PyQt6.QtGui import QImage
        n = len(asked)
        keys = [v[0] for v in asked[-1]]
        dlg._scans.update({k: QImage(4, 4, QImage.Format.Format_RGB888) for k in keys})
        dlg._on_scans(dlg._scan_token, keys)                   # 한 묶음이 끝났다
        assert len(asked) == n                                  # 화면에 설 장이 이미 찼으니 이어서 더 자르지 않는다
    finally:
        dlg._scan_thread = None
        dlg.deleteLater()
