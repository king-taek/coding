"""AVAGO 재리뷰 — Map 경로를 비워 두면 제외 없이 전부 재리뷰한다(경고가 아니라 의도된 동작)."""
import pytest

from aoi_verification.app.coords import rereview as rr
from .test_rereview import WAFER, _wafer_folder, _clear_caches  # noqa: F401


def test_plan_without_map_dir_reviews_everything_without_warning(tmp_path):
    folder = _wafer_folder(tmp_path / "lot", WAFER, [("a", 27, 9), ("b", 28, 9)])
    imgs = [folder / "a.jpeg", folder / "b.jpeg"]
    plan = rr.plan_wafer(WAFER, folder, imgs, None)
    assert plan.no_map and plan.review == imgs and not plan.excluded
    assert not plan.warnings and rr.warning_lines({WAFER: plan}) == []
    assert set(plan.die_of) == set(imgs)              # die 표시는 그대로 채운다


def test_without_map_every_known_die_reject_counts_as_new(tmp_path):
    folder = _wafer_folder(tmp_path / "lot", WAFER, [("a", 27, 9), ("b", 28, 9)])
    imgs = [folder / "a.jpeg", folder / "b.jpeg"]
    plan = rr.plan_wafer(WAFER, folder, imgs, None)
    assert rr.confirmed_new_rejects(plan, imgs) == imgs
    assert rr.wafer_stats(plan, imgs).new_reject_dies == 2


def test_setup_allows_empty_map_path(styled_qapp, tmp_path):
    from aoi_verification.app.ui.pages import setup_page as sp
    scan = tmp_path / "scan"
    scan.mkdir()
    page = sp.SetupPage()
    try:
        page.set_rereview_mode(True)
        page.ref_path_edit.setText("")
        page.val_path_edit.setText(str(scan))
        page.val_machine_edit.setText("LOT")
        assert page._validate() is True
        inp = page._collect_input()
        assert inp.rereview and inp.rereview_no_map and inp.ref_root == scan
        # 지정했는데 없는 경로는 여전히 막는다.
        page.ref_path_edit.setText(str(tmp_path / "nope"))
        assert page._validate() is False
    finally:
        page.deleteLater()
