"""Surface.flt 파서 + geometry 리졸버 — 합성 스키마로 로직 검증.

실제 바이트 오프셋 스키마(Surface.flt.md)는 저장소에 없으므로, 테스트는 모듈의
스키마 상수를 합성 레이아웃으로 monkeypatch 해 파서/리졸버 로직을 검증한다.
(스키마가 채워지면 실데이터로 추가 검증 가능 — 로직 자체는 동일.)

무거운 의존성(PyQt6/openpyxl/cv2) 불필요 — 순수 stdlib 로직.
"""

import struct
from pathlib import Path

from aoi_verification.app.coords import (abs_coord, camtek_ini, geometry,
                                         pixel_size, surface_flt)


# 합성 레코드 레이아웃: 32바이트. geometry 는 float32, zone/recipe 는 uint8.
_SYN_FIELDS = {
    "actual_x":       (0, "f"),
    "actual_y":       (4, "f"),
    "area":           (8, "f"),
    "blob_breadth":   (12, "f"),
    "blob_feret_max": (16, "f"),
    "contrast":       (20, "f"),
    "zone":           (24, "B"),
    "recipe":         (25, "B"),
}
_SYN_SIZE = 32
_SYN_HEADER = 0
_INT_FMTS = ("B", "b", "h", "H", "i", "I")


def _install_schema(monkeypatch):
    """surface_flt 에 합성 스키마를 설치(_SCHEMA_READY=True)."""
    monkeypatch.setattr(surface_flt, "_FIELDS", dict(_SYN_FIELDS))
    monkeypatch.setattr(surface_flt, "_RECORD_SIZE", _SYN_SIZE)
    monkeypatch.setattr(surface_flt, "_HEADER_BYTES", _SYN_HEADER)
    monkeypatch.setattr(surface_flt, "_BYTE_ORDER", "<")
    monkeypatch.setattr(surface_flt, "_SCHEMA_READY", True)
    surface_flt.load_folder.cache_clear()
    camtek_ini.load_abs_folder.cache_clear()


def _pack_record(**vals) -> bytes:
    buf = bytearray(_SYN_SIZE)
    for name, (off, fmt) in _SYN_FIELDS.items():
        v = vals.get(name, 0)
        v = int(v) if fmt in _INT_FMTS else float(v)
        struct.pack_into("<" + fmt, buf, _SYN_HEADER + off, v)
    return bytes(buf)


def _write_flt(folder: Path, *records: bytes) -> None:
    (folder / "Surface.flt").write_bytes(b"".join(records))


# ── 파서 ────────────────────────────────────────────────────────────────
def test_parse_two_records(monkeypatch, tmp_path):
    _install_schema(monkeypatch)
    _write_flt(
        tmp_path,
        _pack_record(actual_x=100.0, actual_y=200.0, area=55.0,
                     blob_breadth=2.0, blob_feret_max=11.0, contrast=108.0),
        _pack_record(actual_x=300.0, actual_y=400.0, area=108.0,
                     blob_breadth=5.0, blob_feret_max=16.0, contrast=64.0),
    )
    recs = surface_flt.load_folder(tmp_path)
    assert len(recs) == 2
    assert recs[0].actual_x == 100.0 and recs[0].area == 55.0
    assert recs[1].contrast == 64.0


def test_missing_file_returns_empty(monkeypatch, tmp_path):
    _install_schema(monkeypatch)
    assert surface_flt.load_folder(tmp_path) == ()
    assert surface_flt.has_flt(tmp_path) is False


def test_truncated_bytes_no_raise(monkeypatch, tmp_path):
    _install_schema(monkeypatch)
    # 레코드 크기보다 짧은 꼬리는 조용히 버린다(raise 없음).
    (tmp_path / "Surface.flt").write_bytes(
        _pack_record(actual_x=1, actual_y=2, area=3, blob_breadth=4,
                     blob_feret_max=5, contrast=6) + b"\x00\x07")
    recs = surface_flt.load_folder(tmp_path)
    assert len(recs) == 1


def test_schema_live_by_default():
    """확정 스키마가 채워져 기본 활성(_SCHEMA_READY=True)."""
    assert surface_flt._SCHEMA_READY is True


def test_disabled_fallback_when_schema_off(monkeypatch, tmp_path):
    """스키마를 끄면(_SCHEMA_READY=False) 파일이 있어도 빈 결과(안전 폴백)."""
    monkeypatch.setattr(surface_flt, "_SCHEMA_READY", False)
    (tmp_path / "Surface.flt").write_bytes(b"\x00" * 304)
    surface_flt.load_folder.cache_clear()
    assert surface_flt.load_folder(tmp_path) == ()


# ── geometry 리졸버 (status 구분) ─────────────────────────────────────────
def _write_ini(folder: Path, stem: str, x: float, y: float) -> Path:
    (folder / "ColorImageGrabingInfo.ini").write_text(
        f"[{stem}.jpeg]\nX={x}\nY={y}\nCol=3\nRow=5\n", encoding="utf-8")
    img = folder / f"{stem}.jpeg"
    img.write_bytes(b"")  # 존재만 하면 됨(파서는 경로만 사용).
    return img


def test_geometry_ok(monkeypatch, tmp_path):
    _install_schema(monkeypatch)
    _write_flt(tmp_path, _pack_record(
        actual_x=147203.82, actual_y=243724.57, area=55.0,
        blob_breadth=2.1823, blob_feret_max=11.3868, contrast=108.18,
        zone=1, recipe=2))
    img = _write_ini(tmp_path, "147206.243725.c.2104939970.2",
                     147203.82, 243724.57)
    res = geometry.resolve(img)
    assert res.status == "ok"
    g = res.geometry
    # 픽셀크기 파일이 없으면 0.77 폴백.
    assert abs(g.pixel_um - 0.77) < 1e-9
    assert round(g.area_um2, 1) == round(55.0 * 0.77 * 0.77, 1)
    assert round(g.width_um, 3) == round(2.1823 * 0.77, 3)
    assert round(g.length_um, 3) == round(11.3868 * 0.77, 3)
    assert round(g.contrast) == 108
    assert g.zone == 1 and g.recipe == 2


def test_geometry_dynamic_pixel_size(monkeypatch, tmp_path):
    """결과 폴더에 Params_WaferInfo.ini 가 있으면 그 RefPixelSizeX 로 환산(0.77 아님)."""
    _install_schema(monkeypatch)
    pixel_size.scan_pixel_size.cache_clear()
    _write_flt(tmp_path, _pack_record(
        actual_x=100.0, actual_y=200.0, area=55.0,
        blob_breadth=2.0, blob_feret_max=11.0, contrast=5.0, zone=1, recipe=2))
    (tmp_path / "Params_WaferInfo.ini").write_text(
        "[Wafer]\nRefPixelSizeX=0.8452004\nRefPixelSizeY=0.8452004\n", encoding="utf-8")
    img = _write_ini(tmp_path, "x.100.200", 100.0, 200.0)
    g = geometry.resolve(img).geometry
    assert abs(g.pixel_um - 0.8452004) < 1e-9
    assert abs(g.width_um - 2.0 * 0.8452004) < 1e-6
    assert abs(g.area_um2 - 55.0 * 0.8452004 ** 2) < 1e-4


def test_pixel_size_priority(tmp_path):
    """우선순위: Params_WaferInfo.ini(RefPixelSizeX) > ProductInfo.ini(Scan2DPixelSize)."""
    pixel_size.scan_pixel_size.cache_clear()
    (tmp_path / "ProductInfo.ini").write_text("Scan2DPixelSize=0.7708\n", encoding="utf-8")
    assert abs(pixel_size.scan_pixel_size(tmp_path) - 0.7708) < 1e-9
    pixel_size.scan_pixel_size.cache_clear()
    (tmp_path / "Params_WaferInfo.ini").write_text("RefPixelSizeX=0.7707764\n", encoding="utf-8")
    assert abs(pixel_size.scan_pixel_size(tmp_path) - 0.7707764) < 1e-9  # 우선
    pixel_size.scan_pixel_size.cache_clear()
    assert pixel_size.scan_pixel_size(tmp_path / "none") is None  # 없으면 None


def test_geometry_no_flt_marker(monkeypatch, tmp_path):
    """Surface.flt 자체가 없으면 status='no_flt' (미지원 자재)."""
    _install_schema(monkeypatch)
    img = _write_ini(tmp_path, "abc.1.2.3", 100.0, 200.0)
    res = geometry.resolve(img)
    assert res.status == "no_flt" and res.geometry is None


def test_geometry_no_data_when_coord_far(monkeypatch, tmp_path):
    """flt 는 있으나 좌표가 tol 밖이면 status='no_data'."""
    _install_schema(monkeypatch)
    _write_flt(tmp_path, _pack_record(
        actual_x=0.0, actual_y=0.0, area=10, blob_breadth=1,
        blob_feret_max=2, contrast=5))
    img = _write_ini(tmp_path, "far.9.9.9", 99999.0, 99999.0)
    res = geometry.resolve(img)
    assert res.status == "no_data" and res.geometry is None


def test_geometry_disabled_when_schema_off(monkeypatch, tmp_path):
    """스키마를 끄면 status='disabled' — 엑셀이 기존과 동일하게 렌더."""
    monkeypatch.setattr(surface_flt, "_SCHEMA_READY", False)
    surface_flt.load_folder.cache_clear()
    img = tmp_path / "x.1.2.3.jpeg"
    img.write_bytes(b"")
    res = geometry.resolve(img)
    assert res.status == "disabled"


# ── 실데이터 회귀 — 예시에서 추출한 실제 152byte record(확정 오프셋 가드) ────
# (hex, area, blob_breadth, blob_feret_max, contrast, actual_x, actual_y, zone, recipe)
_REAL_RECORDS = [
    ("00001d0000000020906dab41602b134138fb1269788e0e41906dab41602b134138fb1269788e0e41010000006900000000000000000000000000000000010200000000000000144000000000000018400000000000003840000000e089dc09400000008059b21d40000000c0ff8919400000000000e0504000000000000014400000000000000000000000a0aaea5e400000000000000000",
     24.0, 3.232685, 6.384765, 123.666664, 314072.064131, 250319.051306, 1, 2),
    ("1d00000000040000a6756ba85096134148d30a475e9b0f41a6756ba85096134148d30a475e9b0f41ffffffff0f0000083613000000a0404400a01344003f0200000000000000284000000000000024400000000000804740000000c00f3f0a4000000080d0a62c40000000c0d3ae2b400000000000803640000000c076f81440000000000000000000000000000000000000000000000000",
     47.0, 3.280792, 13.841459, 0.0, 320916.164472, 258923.784689, 63, 2),
]


def test_real_record_decodes(tmp_path):
    """실측 Surface.flt record 가 확정 오프셋으로 정확히 디코딩된다(스키마 가드).

    area/width/length/contrast/zone/recipe 6개 — 오프셋/타입/엔디안이 어긋나면 실패.
    """
    for hx, area, breadth, feret, contrast, ax, ay, zone, recipe in _REAL_RECORDS:
        raw = bytes.fromhex(hx)
        assert len(raw) == 152
        surface_flt.load_folder.cache_clear()
        (tmp_path / "Surface.flt").write_bytes(raw)
        recs = surface_flt.load_folder(tmp_path)
        assert len(recs) == 1
        r = recs[0]
        assert abs(r.actual_x - ax) < 1e-3
        assert abs(r.actual_y - ay) < 1e-3
        assert abs(r.area - area) < 1e-3
        assert abs(r.blob_breadth - breadth) < 1e-5
        assert abs(r.blob_feret_max - feret) < 1e-5
        assert abs(r.contrast - contrast) < 1e-4
        assert r.zone == zone and r.recipe == recipe


def test_dotted_filename_abs_coord(tmp_path):
    """INI 없이 점표기 파일명에서 절대 X.Y 추출."""
    camtek_ini.load_abs_folder.cache_clear()
    img = tmp_path / "147206.243725.c.2104939970.2.jpeg"
    img.write_bytes(b"")
    xy = abs_coord.absolute_xy(img)
    assert xy == (147206.0, 243725.0)
