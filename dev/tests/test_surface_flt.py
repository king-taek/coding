"""Surface.flt 파서 + geometry 리졸버 — 합성 스키마로 로직 검증.

실제 바이트 오프셋 스키마(Surface.flt.md)는 저장소에 없으므로, 테스트는 모듈의
스키마 상수를 합성 레이아웃으로 monkeypatch 해 파서/리졸버 로직을 검증한다.
(스키마가 채워지면 실데이터로 추가 검증 가능 — 로직 자체는 동일.)

무거운 의존성(PyQt6/openpyxl/cv2) 불필요 — 순수 stdlib 로직.
"""

import struct
from pathlib import Path

from aoi_verification.app.coords import abs_coord, camtek_ini, geometry, surface_flt


# 합성 레코드 레이아웃: 32바이트, 모두 little-endian float32.
_SYN_FIELDS = {
    "actual_x":       (0, "f"),
    "actual_y":       (4, "f"),
    "area":           (8, "f"),
    "blob_breadth":   (12, "f"),
    "blob_feret_max": (16, "f"),
    "contrast":       (20, "f"),
}
_SYN_SIZE = 32
_SYN_HEADER = 0


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
        struct.pack_into("<" + fmt, buf, _SYN_HEADER + off, float(vals[name]))
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


def test_disabled_when_schema_unfilled(tmp_path):
    """기본 상태(스키마 미충전): 파일이 있어도 파서는 빈 결과(비활성)."""
    (tmp_path / "Surface.flt").write_bytes(b"\x00" * 304)
    surface_flt.load_folder.cache_clear()
    assert surface_flt._SCHEMA_READY is False
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
        blob_breadth=2.1823, blob_feret_max=11.3868, contrast=108.18))
    img = _write_ini(tmp_path, "147206.243725.c.2104939970.2",
                     147203.82, 243724.57)
    res = geometry.resolve(img)
    assert res.status == "ok"
    g = res.geometry
    assert round(g.area_um2, 1) == round(55.0 * 0.5929, 1)
    assert round(g.width_um, 3) == round(2.1823 * 0.77, 3)
    assert round(g.length_um, 3) == round(11.3868 * 0.77, 3)
    assert round(g.contrast) == 108


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


def test_geometry_disabled_default(tmp_path):
    """스키마 미충전이면 status='disabled' — 엑셀이 기존과 동일하게 렌더."""
    surface_flt.load_folder.cache_clear()
    img = tmp_path / "x.1.2.3.jpeg"
    img.write_bytes(b"")
    res = geometry.resolve(img)
    assert res.status == "disabled"


def test_dotted_filename_abs_coord(tmp_path):
    """INI 없이 점표기 파일명에서 절대 X.Y 추출."""
    camtek_ini.load_abs_folder.cache_clear()
    img = tmp_path / "147206.243725.c.2104939970.2.jpeg"
    img.write_bytes(b"")
    xy = abs_coord.absolute_xy(img)
    assert xy == (147206.0, 243725.0)
