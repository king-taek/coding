"""폴더 스캔이 진행 개수(done/total)를 콜백으로 보고하는지 검증 (#6)."""

from __future__ import annotations

from pathlib import Path

from aoi_verification.app.models.slot import scan


def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")


def test_scan_reports_progress(tmp_path):
    ref = tmp_path / "ref"
    val = tmp_path / "val"
    for s in ("Slot_01", "Slot_02", "Slot_03"):
        _touch(ref / s / "a.jpg")
        _touch(val / s / "a.jpg")

    seen = []
    scan(ref, val, progress=lambda d, t: seen.append((d, t)))

    assert seen                                  # 콜백이 호출됨
    assert seen[-1] == (3, 3)                     # 마지막은 전체 완료
    assert [d for d, _ in seen] == [1, 2, 3]      # 단조 증가, 슬롯마다 1회
    assert all(t == 3 for _, t in seen)           # total 일정
