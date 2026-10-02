"""AVAGO 재리뷰 — 1차 Reject 사진 폴더(파일명 좌표)로 die 를 빼는 경로.

실물 골든(PH3Q42 2장): '#11 재 저장' 사진 이름만으로 뺀 결과가 Map 으로 뺀 결과와 **같은 사진 집합**이다
(서로 독립인 두 소스가 같은 답)."""
from pathlib import Path

import pytest

from aoi_verification.app.coords import rereview as rr
from .test_rereview_golden import ROOT, CASES, _names, _plan, _clear_caches  # noqa: F401
from .test_rereview import WAFER, _wafer_folder, _map_dir, _clear_caches as _cc  # noqa: F401


def _reject_root(tmp_path, wafer, names):
    d = tmp_path / "rj" / wafer
    d.mkdir(parents=True)
    for n in names:
        (d / n).write_bytes(b"")
    return tmp_path / "rj"


@pytest.mark.parametrize("wafer,n11,unscanned,n_excl", CASES)
def test_folder_filenames_exclude_the_same_photos_as_the_map(tmp_path, wafer, n11, unscanned, n_excl):
    by_map, sdir, imgs = _plan(wafer)
    rj = _reject_root(tmp_path, wafer, _names(ROOT / wafer / "사진목록_#11재저장.txt"))
    plan = rr.plan_wafer(wafer, sdir, imgs, None, wafer=wafer, reject_dir=rj)
    assert plan.no_map and not plan.warnings
    assert set(plan.excluded) == set(by_map.excluded)
    assert len(plan.excluded) == n_excl and len(plan.review) == len(imgs) - n_excl


def test_union_with_map_and_wafer_folder_lookup_is_case_insensitive(tmp_path):
    folder = _wafer_folder(tmp_path / "lot", WAFER, [("a", 27, 9), ("b", 5, 5), ("c", 6, 5)])
    imgs = [folder / f"{s}.jpeg" for s in "abc"]
    plan0 = rr.plan_wafer(WAFER, folder, imgs, None)
    (c0, r0), (c1, r1) = plan0.die_of[imgs[1]], plan0.die_of[imgs[2]]
    rj = _reject_root(tmp_path, WAFER.lower(), [f"X_Y_{WAFER}_{c0}_{r0}_Bump_1_2.jpg"])
    plan = rr.plan_wafer(WAFER, folder, imgs, None, reject_dir=rj)
    assert plan.excluded == [imgs[1]] and imgs[0] in plan.review and imgs[2] in plan.review
    # Map 도 줄 때: Map 의 Reject die(27,9) 와 합집합.
    plan = rr.plan_wafer(WAFER, folder, imgs, _map_dir(tmp_path), reject_dir=rj)
    assert set(plan.excluded) == {imgs[0], imgs[1]}


def test_warnings_when_folder_is_unusable(tmp_path):
    folder = _wafer_folder(tmp_path / "lot", WAFER, [("a", 27, 9)])
    img = folder / "a.jpeg"
    # 웨이퍼 하위 폴더가 없다 → 빼지 않고 경고
    empty = tmp_path / "empty"
    empty.mkdir()
    plan = rr.plan_wafer(WAFER, folder, [img], None, reject_dir=empty)
    assert plan.review == [img] and not plan.excluded
    assert (rr.W_RJ_NO_FOLDER, None) in plan.warnings
    # die 가 하나도 안 맞는다 → 경고(폴더가 틀렸을 수 있다)
    rj = _reject_root(tmp_path, WAFER, ["X_Y_Z_0_0_Bump_1_2.jpg", "not-a-name.jpg"])
    plan = rr.plan_wafer(WAFER, folder, [img], None, reject_dir=rj)
    assert plan.review == [img]
    codes = {c for c, _ in plan.warnings}
    assert rr.W_RJ_NO_MATCH in codes and rr.W_RJ_BAD_NAME in codes
    assert all(rr.warning_lines({WAFER: plan}))


def test_setup_collects_reject_dir(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.pages import setup_page as sp
    scan, rj = tmp_path / "scan", tmp_path / "rj"
    scan.mkdir()
    rj.mkdir()
    warned = []
    monkeypatch.setattr(sp.sheets, "warn", lambda *a, **k: warned.append(a))
    page = sp.SetupPage()
    try:
        page.set_rereview_mode(True)
        assert not page._rj_host.isHidden()
        page.val_path_edit.setText(str(scan))
        page.val_machine_edit.setText("LOT")
        page.rj_path_edit.setText(str(rj))
        inp = page._collect_input()
        assert inp.rereview_reject_dir == rj and inp.rereview_no_map
        page.rj_path_edit.setText(str(tmp_path / "nope"))
        assert page._collect_input() is None and warned
        page.set_rereview_mode(False)
        assert page._rj_host.isHidden()
    finally:
        page.deleteLater()
