"""검증 실행 로그 — 컴퓨터별 폴더 기록 + 캐시 빠른 매치 제외 검증."""

from __future__ import annotations

import json

from aoi_verification.app.utils import run_log


def test_machine_id_is_filename_safe():
    mid = run_log.machine_id()
    assert mid and all(c.isalnum() or c in "._-" for c in mid)


def test_path_location_unc_is_remote():
    assert run_log.path_location(r"\\nas\share\data") == "remote"
    assert run_log.path_location("//nas/share") == "remote"
    assert run_log.path_location("") == "unknown"


def test_record_skips_cache_fast_match(tmp_path):
    rec = run_log.build_record(
        options={"engine": "efficiency"}, ref_root="/r", val_root="/v",
        slot_count=3, ref_photos=10, val_photos=12, elapsed_s=0.3,
        kla_used=False, ocr_used=False)
    # 캐시로 빠르게 끝난 매치(2초 미만)는 기록하지 않음.
    assert run_log.record(rec, elapsed_s=0.3) is None


def test_record_writes_per_machine_folder(tmp_path):
    rec = run_log.build_record(
        options={"engine": "efficiency", "threshold": 0.55},
        ref_root=r"\\nas\ref", val_root="/local/val",
        slot_count=5, ref_photos=40, val_photos=50, elapsed_s=12.3,
        kla_used=True, ocr_used=True)
    assert rec["ref_root_location"] == "remote"
    assert rec["total_photos"] == 90
    path = run_log.record(rec, elapsed_s=12.3)
    assert path is not None and path.exists()
    # 컴퓨터(머신ID) 폴더 아래에 저장.
    assert path.parent.name == run_log.machine_id()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["slot_count"] == 5 and saved["kla_used"] is True
