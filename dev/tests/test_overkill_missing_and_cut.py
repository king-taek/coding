"""Overkill 튜닝 — 말없이 빠지던 두 경우를 빨간 줄로 알린다(2026-11-23, YDD-PIDS3 RE).

① 사진만 있고 결과 파일이 없는 웨이퍼(5장 중 3장, 장비 화면에서도 조회 안 됨)는 튜닝에서 빠진다 — 어느 웨이퍼인지 적는다.
② 장비가 ``GlobalRTP.ini MaxFaultsPerWafer`` 에서 저장을 멈춘 웨이퍼(35174144EWF6: 3,030 중 3,000)는 Defect 가 일부만 있다.
   상한은 파일에서 읽는다 — 숫자를 코드에 두지 않는다."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FS_UBM = ROOT / "dev" / "Threshold 확인" / "T254 FS UBM" / "27125535EWA5"     # Surface.flt 레코드 14개
RECORDS = 14


def _cap(folder: Path, value) -> None:
    (folder / "GlobalRTP.ini").write_text(
        f"[GLOBAL_RTP]\nMaxFaultsPerDie=0\nMaxFaultsPerWafer={value}\n", encoding="utf-8")


def _wafer(dst: Path) -> Path:
    shutil.copytree(FS_UBM, dst)
    return dst


def test_wafer_cap_reads_the_file(tmp_path):
    from aoi_verification.app.threshold import preview as P
    assert P.wafer_cap(tmp_path) == 0                      # 파일 없음 — 모른다
    _cap(tmp_path, 3000)
    assert P.wafer_cap(tmp_path) == 3000                   # MaxFaultsPerDie 와 헷갈리지 않는다
    _cap(tmp_path, 0)
    assert P.wafer_cap(tmp_path) == 0                      # 0 = 상한 없음


def test_cut_only_when_the_file_reaches_the_cap(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    w = _wafer(tmp_path / "w")
    assert T.load_wafer(w).cut == ()                        # 상한 파일 없음
    _cap(w, RECORDS + 1)
    assert T.load_wafer(w).cut == ()
    _cap(w, RECORDS)
    got = T.load_wafer(w)
    assert got.cut == ((w, RECORDS),)
    other = T.load_wafer(_wafer(tmp_path / "v"))
    assert T.merge_wafers([other, got]).cut == ((w, RECORDS),)   # 합쳐도 잘린 웨이퍼를 잃지 않는다


def _lot(tmp_path: Path) -> Path:
    lot = tmp_path / "lot"
    _wafer(lot / "35174143EWB4")
    only = lot / "35174142EWE5"                             # 사진만(썸네일 캐시는 결과 파일이 아니다)
    only.mkdir()
    (only / "1.2.c.3.1.jpeg").write_bytes(b"")
    (only / "Thumbs.db").write_bytes(b"")
    bumps = lot / "35174140EWD4"                            # 결과는 있지만 Surface.flt 가 아니다(DIA 레시피 등)
    bumps.mkdir()
    (bumps / "Bumps.flt").write_bytes(b"\0" * 8)
    (bumps / "4.5.c.6.1.jpeg").write_bytes(b"")
    (lot / "Logs").mkdir()                                  # 사진 없는 폴더는 웨이퍼가 아니다
    (lot / "Logs" / "a.ini").write_text("x")
    return lot


def test_no_flt_dirs_names_the_dropped_wafers(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    lot = _lot(tmp_path)
    assert T.no_flt_dirs(lot) == [(lot / "35174140EWD4", False), (lot / "35174142EWE5", True)]
    assert T.no_flt_dirs(lot / "35174142EWE5") == [(lot / "35174142EWE5", True)]   # 웨이퍼 폴더를 바로 골라도
    assert T.no_flt_dirs(lot / "35174143EWB4") == []
    assert [d for d, _r in T.expand_root(lot)] == [lot / "35174143EWB4"]          # 튜닝에는 여전히 한 장만


def test_dialog_shows_both_in_a_red_line(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    monkeypatch.setattr(T._Load, "start", lambda self: (_ for _ in ()).throw(AssertionError("읽기 스레드 금지")))
    lot = _lot(tmp_path)
    _cap(lot / "35174143EWB4", RECORDS)
    d = T.ThresholdPreviewDialog()
    try:
        d._expand[lot] = T.expand_root(lot)
        for f, _r in d._expand[lot]:
            d._cache[f] = T.load_wafer(f)
        d._no_flt[lot] = T.no_flt_dirs(lot)
        d.set_folders([lot])
        assert not d.alert.isHidden()
        text = d.alert_label.text()
        assert "사진만" in text and "35174142EWE5" in text
        assert "Surface.flt" in text and "35174140EWD4" in text
        assert "35174143EWB4" in text and f"{RECORDS:,}" in text
        d.remove_folder(lot)
        assert d.alert.isHidden()                           # 폴더를 빼면 줄도 사라진다
    finally:
        d.deleteLater()
