"""KLA 정보파일(.001)이 비어 있을 때 — 파일 이름 + 카세트 슬롯 번호로만 짝짓는다.

실측(2026-10-25, LSU TB500.INT846@6412): 25장 중 5장의 .001 이 0~1 바이트라 WaferID 를
못 읽어 '기준 전용' 으로 남았다.  사용자 결정: 이름의 WaferID 와 슬롯 번호(KLA 폴더명 끝 =
Camtek ActiveSlot)가 **둘 다** 맞을 때만 짝짓고, 깨진 파일은 경고한다.
"""

from __future__ import annotations

from aoi_verification.app.coords import kla_info
from aoi_verification.app.models import lot_info
from aoi_verification.app.models.slot import (ScanResult, Slot, kla_folder_slot_no,
                                              merge_unmatched_by_wafer_id,
                                              wafer_ids_from_broken_info)

LOT = "4DT-TB500-H-M1_TB500.INT846@6412"


def _clear():
    kla_info.read_header_ids.cache_clear()
    lot_info.read_active_slot.cache_clear()


def _kla(root, run, wid, body=b""):
    d = root / "kla" / run
    d.mkdir(parents=True)
    (d / f"{LOT}_{wid}.001").write_bytes(body)
    (d / f"{LOT}_{wid}.pass").write_text("[REVIEWED]\nStatus = 1\n")
    (d / f"{wid}_-1_-3_23_5.jpg").write_bytes(b"")
    return d


def _cam(root, wid, slot):
    d = root / "cam" / wid
    d.mkdir(parents=True)
    if slot is not None:
        (d / "WaferInfo.ini").write_text(f"[AutoCycleInfo]\nActiveSlot={slot}\n")
    return d


def test_folder_slot_number():
    assert kla_folder_slot_no("2026-09-24-15-51_1") == "1"
    assert kla_folder_slot_no("2026-09-24-17-42_12") == "12"
    assert kla_folder_slot_no("59295774EWF1") is None


def test_name_and_slot_both_match_pairs(tmp_path):
    _clear()
    k = _kla(tmp_path, "2026-09-24-15-51_1", "59295774EWF1")
    c = _cam(tmp_path, "59295774EWF1", 1)
    assert kla_info.read_wafer_id(k) is None              # 헤더가 없다(빈 파일)
    ids, broken = wafer_ids_from_broken_info({k.name: k}, {c.name: c}, {})
    assert ids == {k.name: "59295774EWF1"}
    assert broken == [(k.name, "59295774EWF1", True)]


def test_slot_number_mismatch_does_not_pair(tmp_path):
    _clear()
    k = _kla(tmp_path, "2026-09-24-16-22_4", "59295771EWH1", body=b"S")
    c = _cam(tmp_path, "59295771EWH1", 5)                 # 슬롯 번호가 다르다
    ids, broken = wafer_ids_from_broken_info({k.name: k}, {c.name: c}, {})
    assert ids == {}
    assert broken == [(k.name, "59295771EWH1", False)]   # 그래도 경고는 한다


def test_missing_active_slot_or_partner_does_not_pair(tmp_path):
    _clear()
    k1 = _kla(tmp_path, "2026-09-24-17-12_9", "34155099EWA2")
    c1 = _cam(tmp_path, "34155099EWA2", None)            # ActiveSlot 없음
    k2 = _kla(tmp_path, "2026-09-24-18-11_15", "34155092EWA0")   # Camtek 폴더 없음
    ids, broken = wafer_ids_from_broken_info({k1.name: k1, k2.name: k2}, {c1.name: c1}, {})
    assert ids == {}
    assert {b[0] for b in broken} == {k1.name, k2.name}


def test_readable_or_already_known_folders_are_left_alone(tmp_path):
    _clear()
    good = _kla(tmp_path, "2026-09-24-16-03_2", "59295773EWA7",
                body=b'FileVersion 1 2;\nWaferID "59295773EWA7";\nSlot 2;\n')
    c = _cam(tmp_path, "59295773EWA7", 2)
    ids, broken = wafer_ids_from_broken_info({good.name: good}, {c.name: c},
                                             {good.name: "59295773EWA7"})
    assert ids == {} and broken == []


def test_two_differently_named_info_files_give_no_id(tmp_path):
    _clear()
    d = _kla(tmp_path, "2026-09-24-15-51_1", "59295774EWF1")
    (d / f"{LOT}_59295773EWA7.001").write_bytes(b"")
    assert kla_info.wafer_id_from_info_name(d) is None


def test_fallback_ids_merge_into_one_slot(tmp_path):
    """앱 흐름 그대로 — 채택한 WaferID 로 기준 전용·검증 전용이 한 슬롯이 된다."""
    _clear()
    k = _kla(tmp_path, "2026-09-24-15-51_1", "59295774EWF1")
    c = _cam(tmp_path, "59295774EWF1", 1)
    sr = ScanResult(slots={k.name: Slot(name=k.name, ref_dir=k),
                           c.name: Slot(name=c.name, val_dir=c)},
                    ref_only=[k.name], val_only=[c.name])
    ids, _ = wafer_ids_from_broken_info({k.name: k}, {c.name: c}, {})
    merge_unmatched_by_wafer_id(sr, ids, {})
    assert sr.ref_only == [] and sr.val_only == []
    assert "59295774EWF1" in sr.slots
