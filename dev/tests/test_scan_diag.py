"""Scan 진단 수집(`coords/scan_diag`) — 합성 폴더로 zip 내용·판정을 확인한다."""

from __future__ import annotations

import json
import zipfile

import pytest

pytest.importorskip("PIL")
from PIL import Image                                                  # noqa: E402

from aoi_verification.app.coords import scan_diag, scan_image as si    # noqa: E402


@pytest.fixture(autouse=True)
def _fresh():
    si.clear_caches()
    yield
    si.clear_caches()


def _folder(tmp_path):
    f = tmp_path / "W1"
    f.mkdir()
    for n in ("0.0.t.1.jpeg", "5000.0.t.2.jpeg"):
        Image.new("L", (100, 50), 90).save(f / n)
    (f / si.SCAN_LIST_NAME).write_text("Version=1\n0.0.t.1.jpeg,0.0,0.0\n5000.0.t.2.jpeg,5000.0,0.0\n")
    (f / "Params_WaferInfo.ini").write_text("[Info]\nRefPixelSizeX=1.0\nRefPixelSizeY=1.0\n")
    ok, far = "10.5.c.111.1.jpeg", "2000.20.c.222.2.jpeg"
    for n in (ok, far):
        Image.new("RGB", (8, 8), (200, 10, 10)).save(f / n)
    (f / "ColorImageGrabingInfo.ini").write_text(
        f"[{ok}]\nRecipeNumber=1\nFaultX=10.5\nFaultY=5\n\n"
        f"[{far}]\nRecipeNumber=2\nFaultX=2000\nFaultY=20\n")
    return f, f / ok


def test_collect_writes_zip_without_photos(tmp_path):
    f, ok = _folder(tmp_path)
    out_root = tmp_path / "out"
    z = scan_diag.collect(ok, out_root)
    assert z.parent == out_root and z.name.startswith("Scan진단_W1_") and z.suffix == ".zip"
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        assert {"report.txt", "diag.json", "files.txt"} <= set(names)
        assert any(n.endswith("ColorImageGrabingInfo.ini") for n in names)
        assert any(n.endswith(si.SCAN_LIST_NAME) for n in names)
        assert not any(n.lower().endswith((".jpeg", ".jpg")) for n in names)
        diag = json.loads(zf.read("diag.json"))
        assert "0.0.t.1.jpeg" in zf.read("files.txt").decode("utf-8")
        assert zf.read("report.txt").decode("utf-8").index("요약") < zf.read("report.txt").decode("utf-8").index("Scan 목록")
    assert diag["summary"]["1"] == {"ok": 1}
    assert diag["summary"]["2"] == {"no_candidate": 1}
    far = next(p for p in diag["photos"] if p["suffix"] == "2")
    assert far["nearest_tile"]["name"] == "5000.0.t.2.jpeg" or far["nearest_tile"]["name"] == "0.0.t.1.jpeg"
    assert far["nearest_tile"]["outside_px"] > 0
    assert diag["photos"][0]["name"] == ok.name                       # 연 사진이 맨 앞
    assert diag["counts"]["scan_tiles"] == {"1": 1, "2": 1}
    assert diag["scan_tile_sizes"]["1"] == {"100x50": 1}


def test_collect_survives_missing_manifest(tmp_path):
    f, ok = _folder(tmp_path)
    (f / si.SCAN_LIST_NAME).unlink()
    z = scan_diag.collect(ok, tmp_path / "o")
    with zipfile.ZipFile(z) as zf:
        diag = json.loads(zf.read("diag.json"))
    assert diag["manifest"] == {"present": False}
    assert diag["summary"]["1"] == {"no_manifest": 1}


def _flt_bytes(rows):
    """152 B 레코드(TB500 배치: area 80 · breadth 88 · contrast 136 · zone 61 · recipe 62)."""
    import struct
    out = b""
    for zone, recipe, area, width, contrast in rows:
        rec = bytearray(152)
        struct.pack_into("<BB", rec, 61, zone, recipe)
        struct.pack_into("<dd", rec, 80, area, width)
        struct.pack_into("<d", rec, 136, contrast)
        out += bytes(rec)
    return out


def test_recipes_compares_per_recipe_pixel(tmp_path):
    """레시피 3(픽셀 0.4)의 기준 px 는 flt(Ref 픽셀 1.6) 숫자와 그대로 비교하면 틀리고, µm 로 맞춰야 맞는다."""
    f, ok = _folder(tmp_path)
    (f / "Params_WaferInfo.ini").write_text("[Info]\nRefPixelSizeX=1.6\nRefPixelSizeY=1.6\n")
    (f / "Recipe3-ProductInfo.ini").write_text("[Geometric]\nScan2DPixelSize=0.4\n")
    z = f / "Recipe3-Zones"
    z.mkdir()
    (z / "RDL.ini").write_text(
        "[General]\nZoneName=RDL\nZoneID=1\n[Surface]\nLow_Delta=40\n"
        "[Surface Feature Filter 1]\nEnable=1\nClassify=29\nFeatureType=2\nFilterType=5\nLowValue=5\nHighValue=5\n")
    (f / "Surface.flt").write_bytes(_flt_bytes([(1, 3, 10.0, 1.3, 50.0), (1, 3, 10.0, 1.2, 50.0)]))
    (f / "Defects.csv").write_text("#,Type,Area,Width,Length,Col,Row,X,Y,Classify,Visit\n"
                                   "1,Surface-RDL,25.6,2.08,3,0,0,1,1,A,\n", encoding="utf-8-sig")
    zp = scan_diag.collect(ok, tmp_path / "o")
    with zipfile.ZipFile(zp) as zf:
        d = json.loads(zf.read("diag.json"))
        assert "copy/Recipe3-Zones/RDL.ini" in zf.namelist()
    row = next(e for e in d["recipes"]["zones"] if e["zone_dir"] == "Recipe3-Zones")
    assert row["flt_n"] == 2 and row["recipe_pixel_xy"] == [0.4, 0.4]
    assert row["pass_raw"] == 0                     # 1.3 px < 5 px
    assert row["pass_um"] == 1                      # 1.3 × 1.6 = 2.08 µm ≥ 5 × 0.4 = 2.0 µm, 1.2 × 1.6 = 1.92 µm 탈락
    assert row["surface_params"]["Low_Delta"] == "40"
    assert d["export_csv"]["by_type"]["Surface-RDL"]["rows"] == 1


def test_collect_many_puts_each_wafer_in_its_folder(tmp_path):
    lot = tmp_path / "LOT"
    lot.mkdir()
    (lot / "setup.ini").write_text("[A]\nB=1\n")
    ws = []
    for n in ("W1", "W2"):
        w = lot / n
        w.mkdir()
        (w / "Recipe.ini").write_text("[General]\n")
        ws.append(w)
    seen = []
    zp = scan_diag.collect_many(ws, tmp_path / "o", progress=lambda d, t: seen.append((d, t)))
    assert zp.name.startswith("Scan진단_LOT_") and seen == [(1, 2), (2, 2)]
    with zipfile.ZipFile(zp) as zf:
        names = set(zf.namelist())
    assert {"W1/diag.json", "W2/diag.json", "W1/copy/Recipe.ini", "W2/copy/Recipe.ini"} <= names
    assert "W1/copy/_상위폴더/setup.ini" in names and "W2/copy/_상위폴더/setup.ini" not in names


def test_collect_many_skips_per_photo_scan_work(tmp_path, monkeypatch):
    """LOT 수집은 빠른 모드 — 사진마다 Scan 을 찾지 않는다(NAS 에서 웨이퍼 하나에 수 분 걸렸다)."""
    f, _ok = _folder(tmp_path)
    monkeypatch.setattr(scan_diag, "_photos", lambda *a: pytest.fail("사진별 Scan 판정을 돌렸다"))
    monkeypatch.setattr(scan_diag, "_tile_sizes", lambda *a: pytest.fail("Scan 타일을 열었다"))
    zp = scan_diag.collect_many([f], tmp_path / "o")
    with zipfile.ZipFile(zp) as zf:
        d = json.loads(zf.read("W1/diag.json"))
    assert "skipped" in d["photos"] and d["recipes"]["names"] is not None


def test_collect_skips_wafer_align_zip(tmp_path):
    f, ok = _folder(tmp_path)
    (f / "WaferAlignData.zip").write_bytes(b"PK" * 100)
    with zipfile.ZipFile(scan_diag.collect(ok, tmp_path / "o")) as zf:
        assert not any(n.endswith("WaferAlignData.zip") for n in zf.namelist())


def test_collect_many_keeps_only_files_the_recipe_analysis_reads(tmp_path):
    """LOT 수집은 분석에 쓰는 파일만 — 큰 .dat · .dcm · .zip · 사진별 목록은 뺀다(사용자 지시 2026-11-21)."""
    lot = tmp_path / "LOT"
    w = lot / "W1"
    (w / "Recipe3-Zones").mkdir(parents=True)
    keep = ("Surface.flt", "Surface.flt.md", "Defects.csv", "RTP.txt", "ManReClassify.ini",
            "EquipmentClassifications.ini", "RecipesInfo.ini", "Recipe3-ProductInfo.ini", "Params_WaferInfo.ini")
    drop = ("s_RichSurface.dat", "DieRegPos.dat.dcm", "Other.zip", "ScanResultImageList.txt",
            "ColorImageGrabingInfo.ini", "FocusMappingDebug_x5.txt")
    for n in keep + drop:
        (w / n).write_bytes(b"x")
    (w / "Big.ini").write_bytes(b"x" * (scan_diag._LEAN_INI_MAX + 1))
    (w / "Recipe3-Zones" / "ScanArea.ini").write_text("[General]\n")
    (w / "Recipe3-Zones" / "mask.dat").write_bytes(b"x")
    (lot / "setup.ini").write_text("[A]\n")
    (lot / "setup.dat").write_bytes(b"x")
    with zipfile.ZipFile(scan_diag.collect_many([w], tmp_path / "o")) as zf:
        copied = {n.split("copy/", 1)[1] for n in zf.namelist() if "/copy/" in n}
    assert copied == set(keep) | {"Recipe3-Zones/ScanArea.ini", "_상위폴더/setup.ini"}
