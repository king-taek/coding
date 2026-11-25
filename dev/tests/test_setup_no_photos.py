"""E2 — 사진이 한 장도 없는 폴더는 설정 화면 입력 검사를 통과하지 못한다."""
from __future__ import annotations

import pytest

from aoi_verification.app.models.slot import has_any_image


def test_has_any_image_sees_root_and_slot_subfolders(tmp_path):
    assert has_any_image(tmp_path) is False
    (tmp_path / "slot1").mkdir()
    (tmp_path / "slot1" / "note.txt").write_text("x")
    assert has_any_image(tmp_path) is False
    (tmp_path / "slot1" / "a.jpg").write_bytes(b"x")
    assert has_any_image(tmp_path) is True          # LOT 폴더 > 슬롯 폴더 > 사진


def test_has_any_image_ignores_non_defect_photos(tmp_path):
    (tmp_path / "CognexInSight17xx_Slot1.jpg").write_bytes(b"x")
    assert has_any_image(tmp_path) is False         # 스캔이 무시하는 사진은 세지 않는다
    (tmp_path / "1.2.c.3.jpg").write_bytes(b"x")
    assert has_any_image(tmp_path) is True


def test_setup_blocks_start_on_photoless_folder(styled_qapp, tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app import i18n
    from aoi_verification.app.ui.pages.setup_page import SetupPage

    ref, val = tmp_path / "r", tmp_path / "v"
    ref.mkdir(); val.mkdir()
    page = SetupPage()
    try:
        page.set_backend_ready(True)
        page.ref_path_edit.setText(str(ref))
        page.val_path_edit.setText(str(val))
        assert page._validate() is False
        err = page.ref_path_edit.property("_errLabel")
        assert err.text() == i18n.KO.SETUP_NO_PHOTOS
        assert not page.start_btn.isEnabled()
        (ref / "a.jpg").write_bytes(b"x")
        (val / "a.jpg").write_bytes(b"x")
        assert page._validate() is True
        assert err.text() == ""
    finally:
        page.deleteLater()
