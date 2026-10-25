"""좌표 파서의 **실패 경로** 회귀 가드 — 정상 결과는 건드리지 않고, 깨진 입력이 실행 전체를
중단시키거나(M6) 이웃 줄을 삼키거나(M20) 조용히 사라지는(L3·L6·L7) 것을 막는다."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from aoi_verification.app.coords import (camtek_ini, geometry, kla_info, recipe_name,
                                         scan_image, surface_flt, wafer_geometry,
                                         wafer_txt, zone_name)

_KLA_HEAD = "DiePitch 1000.0 2000.0;\n"


def _kla_folder(tmp_path: Path, rows: str) -> Path:
    d = tmp_path / "kla"
    d.mkdir()
    (d / "W1.001").write_text(
        _KLA_HEAD + 'TiffFileName a.tif;\n' + rows, encoding="utf-8")
    return d


# ── M6 ───────────────────────────────────────────────────────────────────

def test_missing_kla_folder_does_not_raise(tmp_path):
    gone = tmp_path / "nope"
    assert kla_info._find_info_file(gone) is None
    kla_info.load_folder_raw.cache_clear()
    kla_info.load_folder.cache_clear()
    assert kla_info.load_folder_raw(gone) == {}
    assert kla_info.resolve(gone / "a.jpg") is None


def test_unreadable_kla_folder_returns_none(tmp_path, monkeypatch):
    def boom(self):
        raise PermissionError("denied")
    monkeypatch.setattr(Path, "iterdir", boom)
    assert kla_info._find_info_file(tmp_path) is None


def test_kla_infinite_xrel_row_is_skipped_not_fatal(tmp_path, caplog):
    d = _kla_folder(tmp_path, "1 10 20 1e400 50 0 0\n")
    d2 = tmp_path / "kla2"
    d2.mkdir()
    (d2 / "W1.001").write_text(
        _KLA_HEAD + "TiffFileName a.tif;\n1 10 20 1e400 50 0 0\n"
        "TiffFileName b.tif;\n2 10 20 30.4 50 1 1\n", encoding="utf-8")
    kla_info.load_folder_raw.cache_clear()
    kla_info.load_folder.cache_clear()
    with caplog.at_level(logging.WARNING, logger="aoi.coords"):
        assert kla_info.load_folder(d) == {}
        got = kla_info.load_folder(d2)
    assert set(got) == {"b"} and got["b"].x == 30.0 and got["b"].y == 1950.0
    assert any("inf/nan" in r.getMessage() for r in caplog.records)


# ── M20 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("nl", ["\r\n", "\n"])
def test_empty_valued_key_does_not_swallow_next_line(tmp_path, nl):
    (tmp_path / "ColorImageGrabingInfo.ini").write_text(
        nl.join(["[a.jpg]", "Foo=", "X=100.0", "Y=200.5", "Col=1", "Row=2",
                 "RecipeNumber=3", "[b.jpg]", "Foo=   ", "X=7", "Y=8", "Col=0", "Row=0", ""]),
        encoding="utf-8", newline="")
    kv = camtek_ini._kv("Foo=" + nl + "X=100.0" + nl)
    assert kv["X"] == "100.0" and kv.get("FOO", "") == ""
    camtek_ini.load_abs_folder.cache_clear()
    camtek_ini.load_raw_folder.cache_clear()
    camtek_ini.load_recipe_folder.cache_clear()
    assert camtek_ini.load_abs_folder(tmp_path) == {"a": (100.0, 200.5), "b": (7.0, 8.0)}
    assert camtek_ini.load_raw_folder(tmp_path)["a"] == (100.0, 200.5, 1, 2)
    assert camtek_ini.load_recipe_folder(tmp_path) == {"a": 3, "b": 0}


def test_empty_recipe_name_does_not_take_next_line(tmp_path):
    (tmp_path / "x.ini").write_text(
        "[r]\r\nRecipeName=\r\nRecipeNumber=5\r\n[s]\r\nRecipeName=Good\r\nRecipeNumber=6\r\n",
        encoding="utf-8", newline="")
    assert recipe_name._scan([tmp_path / "x.ini"]) == {6: "Good"}
    assert recipe_name._SEC_NAME.search("Name=\r\nOther=1") is None
    assert recipe_name._GENERAL_NAME.search("Recipe Name=\r\nLastCam=3") is None


def test_empty_zone_name_does_not_take_next_line(tmp_path):
    (tmp_path / "z.ini").write_text(
        "[z]\r\nZoneName=\r\nZoneID=4\r\n[y]\r\nZoneName=Edge\r\nZoneID=5\r\n",
        encoding="utf-8", newline="")
    assert zone_name._scan([tmp_path / "z.ini"]) == {5: "Edge"}


# ── L3 ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("v", ["inf", "1e400", "nan", "-1", "0"])
def test_die_um_rejects_non_finite_and_non_positive(v):
    assert wafer_txt._die_um({"DUTMS": "mm", "XDIES": v}, "XDIES") is None


def test_die_um_still_accepts_normal_value():
    assert wafer_txt._die_um({"DUTMS": "mm", "XDIES": "14.28"}, "XDIES") == 14280.0


# ── L6 ───────────────────────────────────────────────────────────────────

def test_scan_resolve_unexpected_exception_is_unreadable_not_no_candidate(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(scan_image, "_index", boom)
    m = scan_image.resolve(tmp_path / "1.2.c.1.jpeg")
    assert m.status == scan_image.UNREADABLE and not m.ok


def test_scan_image_size_failure_is_logged(tmp_path, caplog):
    bad = tmp_path / "broken.jpeg"
    bad.write_bytes(b"not a jpeg")
    with caplog.at_level(logging.WARNING, logger="aoi.scan"):
        assert scan_image._image_size(str(bad)) is None
    assert caplog.records


# ── L7 ───────────────────────────────────────────────────────────────────

def _boom(*a, **k):
    raise RuntimeError("boom")


def _warned(caplog) -> bool:
    return any(r.levelno >= logging.WARNING and r.exc_info for r in caplog.records)


@pytest.mark.parametrize("fn,fallback", [
    ("load_folder", {}), ("load_raw_folder", {}),
    ("load_recipe_folder", {}), ("load_abs_folder", {}),
])
def test_camtek_ini_failures_are_logged_with_same_fallback(tmp_path, monkeypatch, caplog, fn, fallback):
    monkeypatch.setattr(camtek_ini, "_sections", _boom)
    getattr(camtek_ini, fn).cache_clear()
    with caplog.at_level(logging.WARNING, logger="aoi.coords"):
        assert getattr(camtek_ini, fn)(tmp_path) == fallback
    assert _warned(caplog)


def test_surface_flt_failure_is_logged(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(surface_flt, "_SCHEMA_READY", True)
    monkeypatch.setattr(surface_flt, "_find_flt", _boom)
    surface_flt.load_folder.cache_clear()
    with caplog.at_level(logging.WARNING, logger="aoi.coords"):
        assert surface_flt.load_folder(tmp_path) == ()
    assert _warned(caplog)


def test_geometry_resolve_failure_is_logged(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(surface_flt, "_SCHEMA_READY", True)
    monkeypatch.setattr(surface_flt, "has_flt", _boom)
    with caplog.at_level(logging.WARNING, logger="aoi.coords"):
        r = geometry.resolve(tmp_path / "1.jpg")
    assert r.status == "no_data" and r.geometry is None
    assert _warned(caplog)


def test_camtek_geometry_failure_is_logged(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(wafer_geometry, "_pitch_candidates", _boom)
    wafer_geometry.camtek_geometry.cache_clear()
    with caplog.at_level(logging.WARNING, logger="aoi.coords"):
        assert wafer_geometry.camtek_geometry(tmp_path) is None
    assert _warned(caplog)


def test_kla_geometry_failure_is_logged(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(kla_info, "_find_info_file", _boom)
    wafer_geometry.kla_geometry.cache_clear()
    with caplog.at_level(logging.WARNING, logger="aoi.coords"):
        assert wafer_geometry.kla_geometry(tmp_path) is wafer_geometry.FALLBACK_KLA
    assert _warned(caplog)
