"""verify_ground_truth — 합성 폴더로 정답대조 단일 md 생성 검증."""

import importlib.util
import struct
from pathlib import Path

_MOD = Path(__file__).resolve().parents[1] / "verify_ground_truth.py"
_spec = importlib.util.spec_from_file_location("verify_ground_truth", _MOD)
vgt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vgt)


def _record(ax, ay, area, bb, bf, contrast, zone, recipe):
    buf = bytearray(152)
    struct.pack_into("<d", buf, 24, ax)
    struct.pack_into("<d", buf, 32, ay)
    struct.pack_into("<d", buf, 80, area)
    struct.pack_into("<d", buf, 88, bb)
    struct.pack_into("<d", buf, 104, bf)
    struct.pack_into("<d", buf, 136, contrast)
    struct.pack_into("<B", buf, 61, zone)
    struct.pack_into("<B", buf, 62, recipe)
    return bytes(buf)


def test_ground_truth_md(tmp_path, monkeypatch):
    folder = tmp_path / "00RXN059XYD5"
    folder.mkdir()
    px = 0.8452
    bb, bf, area_px = 9.122, 22.16, 207.0
    (folder / "Surface.flt").write_bytes(
        _record(152756.0, 222197.0, area_px, bb, bf, 40.3, 2, 1))
    (folder / "ColorImageGrabingInfo.ini").write_text(
        "[152756.222197.c.2068531159.1.jpeg]\nFaultX=152756\nFaultY=222197\n"
        "Col=4\nRow=4\n", encoding="utf-8")
    (folder / "ProductInfo.ini").write_text(
        f"Scan2DPixelSize={px}\n[Z]\nZoneName=RDL\nZoneID=2\n"
        "[R]\nRecipeName=PI_Bubble\nRecipeNumber=1\n", encoding="utf-8")

    img = folder / "152756.222197.c.2068531159.1.jpeg"
    monkeypatch.setattr(vgt, "EXAMPLES", [dict(
        path=str(img), area=round(area_px * px * px, 2), width=round(bb * px, 2),
        length=round(bf * px, 2), contrast=40.3, recipe="PI_Bubble",
        col=2, row=3, zone="RDL")])
    out = tmp_path / "정답대조.md"
    vgt.main(["--out", str(out)])
    text = out.read_text(encoding="utf-8")
    # 동적 px·이름·좌표변환이 다 맞아 전 항목 ✅.
    assert "px = **0.8452**" in text
    assert "RDL" in text and "PI_Bubble" in text
    assert "변환 2/3" in text
    assert "❌" not in text  # 합성값이 정확히 맞으므로 불일치 없음


def test_parse_flt_full_schema(tmp_path):
    (tmp_path / "Surface.flt").write_bytes(
        _record(1.0, 2.0, 73.0, 4.717, 12.535, 36.7, 1, 2))
    recs = vgt.parse_flt(tmp_path / "Surface.flt")
    assert len(recs) == 1
    r = recs[0]
    assert r["zone"] == 1 and r["recipe"] == 2
    assert abs(r["area"] - 73.0) < 1e-6 and abs(r["BlobBreadth"] - 4.717) < 1e-3
    assert abs(r["ActualX"] - 1.0) < 1e-9
