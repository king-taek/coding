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
