"""dump_lot_files — 폴더의 모든 파일을 단일 md 로 덤프, 0.77 근처값 탐지."""

import importlib.util
import struct
from pathlib import Path

_MOD = Path(__file__).resolve().parents[1] / "dump_lot_files.py"
_spec = importlib.util.spec_from_file_location("dump_lot_files", _MOD)
dl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dl)


def test_dump_finds_near_077_text_and_binary(tmp_path):
    lot = tmp_path / "00RMF041XYC7"
    lot.mkdir()
    # 텍스트 recipe: 0.7698 은 반올림하면 0.77.
    (lot / "inspection.rcp").write_text("ScanPixelUm=0.7698\nThreshold=12\n",
                                        encoding="utf-8")
    # INI: pixelsize 0.4668(관심 범위지만 0.77 후보 아님).
    (lot / "ColorImageGrabingInfo.ini").write_text("[a.jpeg]\nPixelSizeX=0.4668\n",
                                                   encoding="utf-8")
    # 바이너리: float64 0.77 박기.
    buf = bytearray(32)
    struct.pack_into("<d", buf, 8, 0.77)
    (lot / "Surface.flt").write_bytes(bytes(buf))
    # 이미지: 내용 덤프 제외 대상.
    (lot / "a.jpeg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

    out = tmp_path / "dump.md"
    dl.main([str(lot), "--out", str(out)])
    text = out.read_text(encoding="utf-8")

    # 0.77 근처 요약에 텍스트(0.7698)·바이너리(0.77) 둘 다.
    assert "## ★ 0.77 근처 값 요약" in text
    assert "0.7698" in text and "inspection.rcp" in text
    assert "Surface.flt" in text
    # 텍스트 파일 내용이 그대로 들어감.
    assert "ScanPixelUm=0.7698" in text
    # 이미지는 내용 생략.
    assert "이미지/대용량 — 내용 생략" in text
    # 0.4668 은 관심범위지만 0.77 후보는 아님(요약에 0.4668 단독으로 안 뜸).
    assert "PixelSizeX=0.4668" in text  # 내용엔 있음


def test_is_text_and_scan_floats():
    assert dl.is_text(b"hello=1.23\n") is True
    assert dl.is_text(b"\x00\x01\x02\x03") is False
    buf = bytearray(16)
    struct.pack_into("<d", buf, 0, 0.77)
    hits = dl.scan_floats(bytes(buf), 0.4, 0.8)
    assert any(abs(v - 0.77) < 1e-6 for _o, _f, v in hits)
