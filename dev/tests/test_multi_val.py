"""기준 1 ↔ 검증 여러 장비(`models/multi_val.py` · `ui/multi_val_flow.py`).

사용자 결정(2026-10-26): 검토는 한 번(기준 사진 1장 + 장비별 후보 줄) · 교차검증은
장비마다 양방향 · 엑셀은 파일 하나(요약 + 장비별 시트) · 모든 매칭 모드.
좌표 규칙 때문에 스캔·보정·매칭은 장비마다 따로다 — 그 '구간' 이 서로 새지 않는지가
이 파일의 핵심이다.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from aoi_verification.app.models import multi_val
from aoi_verification.app.models.result import FinalResult, MatchResult, MissEntry
from aoi_verification.app.models.slot import ImageItem, Slot


def _m(slot, r, v, score=0.9, equip=0):
    return MatchResult(slot=slot, ref_path=Path(r), val_path=Path(v), score=score,
                       equip=equip)


# ---------------------------------------------------------------------------
# 순수 로직
# ---------------------------------------------------------------------------
def test_single_equipment_key_is_unchanged():
    """장비가 하나(equip 0)면 키가 예전 3칸 그대로다 — 저장된 표시·비교가 안 깨진다."""
    m = _m("S1", "/r/a.jpg", "/v/b.jpg")
    assert m.key == ("S1", "a.jpg", "b.jpg")


def test_same_names_on_two_equipments_do_not_collide():
    """두 장비의 검증 사진 이름이 같아도 검토 화면의 행이 겹치지 않는다."""
    a = _m("S1", "/r/a.jpg", "/aoi4/x.jpg", equip=0)
    b = _m("S1", "/r/a.jpg", "/aoi5/x.jpg", equip=1)
    assert a.key != b.key


def test_tag_and_split_round_trip():
    leg0 = [_m("S1", "/r/a.jpg", "/v4/1.jpg")]
    leg1 = [_m("S1", "/r/a.jpg", "/v5/1.jpg"), _m("S1", "/r/b.jpg", "/v5/2.jpg")]
    tagged = multi_val.tag(leg0, 0) + multi_val.tag(leg1, 1)
    miss = [MissEntry("S1", "ref", Path("/r/b.jpg"), equip=1)]
    kept = [t for t in tagged if t.ref_path.name != "b.jpg"]
    parts = multi_val.split(kept, miss, 2)
    assert parts[0] == (leg0, [])
    assert parts[1][0] == [leg1[0]]
    assert parts[1][1] == [MissEntry("S1", "ref", Path("/r/b.jpg"))]
    # 구간으로 돌아간 값에는 장비 번호가 남지 않는다(단일 실행 코드가 그대로 돈다).
    assert all(m.equip == 0 for p in parts for m in p[0])


def test_review_order_groups_equipments_under_each_ref():
    """같은 기준 사진의 장비별 줄이 붙어 나온다(사용자 제안 '기준 1장에 장비별 후보')."""
    ms = [_m("S1", "/r/b.jpg", "/v5/1.jpg", equip=1),
          _m("S1", "/r/a.jpg", "/v5/2.jpg", equip=1),
          _m("S1", "/r/a.jpg", "/v4/3.jpg", equip=0),
          _m("S1", "/r/b.jpg", "/v4/4.jpg", equip=0)]
    got = [(m.ref_path.name, m.equip) for m in multi_val.review_order(ms)]
    assert got == [("a.jpg", 0), ("a.jpg", 1), ("b.jpg", 0), ("b.jpg", 1)]


def test_leg_targets_follow_paths_not_slot_names():
    """다른 구간에선 KLA 해석으로 슬롯 이름이 바뀔 수 있다 — 경로로 옮긴다."""
    picked = [Path("/ref/W1/a.jpg")]
    slots = {"WAFER-01": Slot(name="WAFER-01", ref_images=[
        ImageItem("WAFER-01", Path("/ref/W1/a.jpg"), "ref"),
        ImageItem("WAFER-01", Path("/ref/W1/b.jpg"), "ref")])}
    got = multi_val.leg_targets(picked, slots, ["WAFER-01"])
    assert [it.path.name for it in got["WAFER-01"]] == ["a.jpg"]
    assert multi_val.leg_targets(picked, slots, []) == {}


def test_summary_rows_mark_found_missed_and_unpaired():
    r = Path("/r/S1/a.jpg"), Path("/r/S1/b.jpg"), Path("/r/S2/c.jpg")
    aoi4 = FinalResult(mode="single", ref_machine="KLA", val_machine="AOI-4",
                       matches=[MatchResult("S1", r[0], Path("/v4/1.jpg"), 0.9),
                                MatchResult("S2", r[2], Path("/v4/2.jpg"), 0.9)],
                       unmatched_refs=[MissEntry("S1", "ref", r[1])])
    aoi5 = FinalResult(mode="single", ref_machine="KLA", val_machine="AOI-5",
                       matches=[MatchResult("S1", r[1], Path("/v5/9.jpg"), 0.9)],
                       unmatched_refs=[MissEntry("S1", "ref", r[0]),
                                       MissEntry("S1", "val", Path("/v5/only.jpg"))])
    rows = multi_val.summary_rows([aoi4, aoi5])
    by = {ref.name: cells for _slot, ref, cells in rows}
    F, M, N = (multi_val.STATUS_FOUND, multi_val.STATUS_MISSED,
               multi_val.STATUS_NO_SLOT)
    assert by["a.jpg"] == [(F, Path("/v4/1.jpg")), (M, None)]
    assert by["b.jpg"] == [(M, None), (F, Path("/v5/9.jpg"))]
    assert by["c.jpg"] == [(F, Path("/v4/2.jpg")), (N, None)]   # AOI-5 는 S2 짝이 없다
    assert "only.jpg" not in by, "검증에만 있는 사진은 기준 사진 행이 아니다"


# ---------------------------------------------------------------------------
# 흐름 — 구간이 서로 새지 않는다
# ---------------------------------------------------------------------------
def _setup_input(tmp_path, extra=2):
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.pages.setup_page import SetupInput
    return SetupInput(mode="single", ref_root=tmp_path / "ref",
                      val_root=tmp_path / "aoi4", ref_machine="KLA",
                      val_machine="AOI-4", threshold=0.5,
                      extra_vals=[(tmp_path / f"aoi{4 + k}", f"AOI-{4 + k}")
                                  for k in range(1, extra)])


def _ctx(fast=None):
    from aoi_verification.app.ui.pages.match_page import ReviewContext
    return ReviewContext(score_cache=None, fast_results=dict(fast or {}),
                         coord_mode=True, tolerance=200.0, coord_failed_count=1,
                         elapsed_s=0.0)


def _window(tmp_path, styled_qapp):
    import aoi_verification.app.ui.main_window as MW
    win = MW.MainWindow.__new__(MW.MainWindow)
    win._reset_cross_state()
    inp = _setup_input(tmp_path)
    win._input = inp
    win._init_legs(inp)
    win._fresh_leg_state()
    win._input = inp
    pages = SimpleNamespace(state=None, fast={})
    win._match_page = SimpleNamespace(
        review_context=lambda: _ctx(pages.fast),
        get_state=lambda: SimpleNamespace(val_pool={"S1": []}),
        build_candidates_by_ref=lambda ms: {})
    win._launched = []
    win._launch_scan = lambda inp: win._launched.append(inp.val_machine)
    win._reviews = []
    win._show_multi_review = lambda: win._reviews.append(True)
    return MW, win, pages


def test_second_leg_starts_clean_and_first_leg_is_kept(tmp_path, styled_qapp):
    MW, win, pages = _window(tmp_path, styled_qapp)
    assert win._multi() and win._leg_labels() == ["AOI-4", "AOI-5"]
    win._matches_a.append(_m("S1", "/r/a.jpg", "/v4/1.jpg"))
    win._coord_offset = (87.0, -91.0)
    pages.fast = {("S1", Path("/r/a.jpg")): [(Path("/v4/1.jpg"), 0.9)]}
    win._multi_after_match()
    assert win._launched == ["AOI-5"], "다음 검증 장비의 스캔이 시작되지 않았다"
    assert win._leg_idx == 1 and win._input.val_machine == "AOI-5"
    assert win._matches_a == [] and win._coord_offset is None, \
        "다음 장비가 앞 장비의 매치·좌표 보정값을 물려받았다"
    leg0 = win._legs[0]["state"]
    assert [m.val_path.name for m in leg0["_matches_a"]] == ["1.jpg"]
    assert leg0["_coord_offset"] == (87.0, -91.0)
    # MatchPage 를 다음 장비가 덮어도 앞 장비의 선계산 사본은 그대로다.
    pages.fast.clear()
    assert leg0["_leg_ctx"].ctx.fast_results, "앞 장비의 선계산을 떠 두지 않았다"


def test_last_leg_goes_to_one_combined_review(tmp_path, styled_qapp):
    MW, win, pages = _window(tmp_path, styled_qapp)
    win._multi_after_match()                 # AOI-4 끝
    win._multi_after_match()                 # AOI-5 끝
    assert win._reviews == [True], "마지막 장비 뒤에 합친 검토로 가지 않았다"


def test_combined_review_inputs_tag_each_lane(tmp_path, styled_qapp):
    MW, win, pages = _window(tmp_path, styled_qapp)
    win._matches_a.append(_m("S1", "/r/a.jpg", "/v4/1.jpg"))
    win._multi_after_match()
    win._matches_a.append(_m("S1", "/r/a.jpg", "/v5/1.jpg"))
    win._skipped_a["S1"].append(ImageItem("S1", Path("/r/b.jpg"), "ref"))
    win._multi_after_match()
    matches, lanes, marked, failed = win._multi_review_inputs()
    assert [(m.val_path.parent.name, m.equip) for m in matches] == [("v4", 0), ("v5", 1)]
    assert [ln["label"] for ln in lanes] == ["AOI-4", "AOI-5"]
    assert Path("/r/b.jpg") in lanes[1]["missing"]
    assert failed == 2                       # 장비마다 1 씩


def test_combined_review_done_splits_back_into_each_leg(tmp_path, styled_qapp):
    MW, win, pages = _window(tmp_path, styled_qapp)
    a4 = _m("S1", "/r/a.jpg", "/v4/1.jpg")
    a5 = _m("S1", "/r/a.jpg", "/v5/1.jpg")
    win._matches_a.append(a4)
    win._multi_after_match()
    win._matches_a.append(a5)
    win._multi_after_match()
    t4, t5 = multi_val.tag([a4], 0)[0], multi_val.tag([a5], 1)[0]
    win._match_review_page = SimpleNamespace(all_matches=lambda: [t4, t5],
                                             unmatched_keys=lambda: {t5.key})
    shown = []
    win._multi_show_result = lambda i: shown.append(i)
    win._multi_review_done([t4], [MissEntry("S1", "ref", Path("/r/a.jpg"),
                                            equip=1)])
    s0, s1 = win._legs[0]["state"], win._legs[1]["state"]
    assert s0["_reviewed_matches"] == [a4] and s0["_reviewed_unmatched"] == []
    assert s1["_reviewed_matches"] == []
    assert s1["_reviewed_unmatched"] == [MissEntry("S1", "ref", Path("/r/a.jpg"))]
    assert s1["_reviewed_unmatched_keys"] == {a5.key}, \
        "결과→검토 왕복 때 되살릴 '매치 없음' 표시가 구간 키로 돌아오지 않았다"
    assert shown == [0]


def test_cross_check_runs_second_direction_for_every_leg(tmp_path, styled_qapp):
    MW, win, pages = _window(tmp_path, styled_qapp)
    for leg in win._legs:
        leg["state"] = None
    win._cross_stage = "A"
    win._save_leg()
    win._legs[1]["state"] = dict(win._legs[0]["state"])
    entered = []
    win._enter_cross_b = lambda: entered.append(win._leg_idx)
    shown = []
    win._multi_show_result = lambda i: shown.append(i)
    win._multi_next_cross_b()
    assert entered == [0]
    assert win._multi_leg_finished() is True      # AOI-4 의 2차 끝 → AOI-5 의 2차
    assert entered == [0, 1] and shown == []
    assert win._multi_leg_finished() is True      # AOI-5 의 2차 끝 → 결과
    assert shown == [0]


def test_single_equipment_run_has_no_legs(tmp_path, styled_qapp):
    import aoi_verification.app.ui.main_window as MW
    win = MW.MainWindow.__new__(MW.MainWindow)
    inp = _setup_input(tmp_path, extra=1)
    assert not inp.extra_vals
    win._init_legs(inp)
    assert not win._multi() and win._legs == []
    assert win._multi_leg_finished() is False


# ---------------------------------------------------------------------------
# 검토 화면 — 기준 사진 1장 + 장비별 줄
# ---------------------------------------------------------------------------
def test_review_page_draws_one_ref_with_a_row_per_equipment(styled_qapp, tmp_path):
    from aoi_verification.app.i18n import ko
    from aoi_verification.app.ui.pages.match_review_page import (
        MatchReviewPage, _MatchRow)
    page = MatchReviewPage()
    a4 = _m("S1", tmp_path / "a.jpg", tmp_path / "v4.jpg", equip=0)
    a5 = _m("S1", tmp_path / "a.jpg", tmp_path / "v5.jpg", equip=1)
    b4 = _m("S1", tmp_path / "b.jpg", tmp_path / "v4b.jpg", equip=0)
    lanes = [{"label": "AOI-4", "val_pool": {}, "candidates_by_ref": {},
              "missing": set()},
             {"label": "AOI-5", "val_pool": {}, "candidates_by_ref": {},
              "missing": {tmp_path / "b.jpg"}}]
    page.load_state([b4, a5, a4], coord_mode=True, tolerance=200.0, lanes=lanes)
    widgets = [page._list_layout.itemAt(i).widget()
               for i in range(page._list_layout.count())]
    rows = [w for w in widgets if isinstance(w, _MatchRow)]
    assert [(r.match.ref_path.name, r.match.equip) for r in rows] == \
        [("a.jpg", 0), ("a.jpg", 1), ("b.jpg", 0)]
    assert not rows[0]._ref_img.isHidden() and rows[1]._ref_img.isHidden(), \
        "같은 기준 사진의 둘째 장비 줄이 기준 사진을 다시 그렸다"
    texts = [w.text() for w in widgets if not isinstance(w, _MatchRow)]
    assert ko.MULTI_VAL_NO_CANDIDATE_FMT.format(machine="AOI-5") in texts, \
        "후보를 못 찾은 장비 자리가 보이지 않는다"
    got = []
    page.finished.connect(lambda k, u: got.append((k, u)))
    page._on_toggle(a5)
    page._on_done()
    kept, miss = got[0]
    assert {m.key for m in kept} == {a4.key, b4.key}
    assert [(u.path.name, u.equip) for u in miss] == [("a.jpg", 1)]


# ---------------------------------------------------------------------------
# 엑셀 — 요약 + 장비별 시트, 파일 하나
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("use_template", [False, True])
def test_export_writes_summary_and_one_sheet_per_equipment(isolated_cache, tmp_path,
                                                           use_template):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6.QtWidgets")
    openpyxl = pytest.importorskip("openpyxl")
    from aoi_verification.app.i18n import ko
    from aoi_verification.app.utils import paths
    from aoi_verification.app.workers.exporter import ExcelExporter

    ra, rb = tmp_path / "a.jpg", tmp_path / "b.jpg"
    aoi4 = FinalResult(mode="single", ref_machine="KLA", val_machine="AOI-4",
                       matches=[MatchResult("S1", ra, tmp_path / "v4.jpg", 0.9)],
                       unmatched_refs=[MissEntry("S1", "ref", rb)])
    aoi5 = FinalResult(mode="single", ref_machine="KLA", val_machine="AOI-5",
                       matches=[MatchResult("S1", rb, tmp_path / "v5.jpg", 0.9)],
                       unmatched_refs=[MissEntry("S1", "ref", ra)],
                       slot_only_ref=["S9"])
    tpl = paths.template_path() if use_template else tmp_path / "none.xlsx"
    if use_template and not tpl.exists():
        pytest.skip("양식.xlsx 없음")
    exp = ExcelExporter(aoi4, dst_path=tmp_path / "out.xlsx", template_path=tpl,
                        legs=[aoi4, aoi5])
    seen = []
    exp.signals.progress.connect(lambda d, t, _m: seen.append((d, t)))
    exp.run()
    wb = openpyxl.load_workbook(tmp_path / "out.xlsx")
    names = wb.sheetnames
    assert names[0] == ko.MULTI_VAL_SUMMARY_SHEET
    assert "AOI-4" in names and "AOI-5" in names
    assert not any(n.startswith("_tmpl") for n in names), "양식 사본이 남았다"
    assert ko.SHEET_UNMATCHED not in names, "여러 장비면 미매칭 시트를 따로 두지 않는다"
    assert any(n.startswith(ko.SLOT_MISMATCH_SHEET) and "AOI-5" in n for n in names)
    summ = wb[ko.MULTI_VAL_SUMMARY_SHEET]
    assert [summ.cell(1, c).value for c in (3, 4)] == ["AOI-4", "AOI-5"]
    vals = {summ.cell(r, 2).value: (summ.cell(r, 3).value, summ.cell(r, 4).value)
            for r in range(2, summ.max_row + 1)}
    assert vals["a.jpg"][0].startswith(ko.MULTI_VAL_STATUS_FOUND)
    assert vals["a.jpg"][1] == ko.MULTI_VAL_STATUS_MISSED
    assert vals["b.jpg"][1].endswith("v5.jpg")
    # 장비 시트마다 D열 머리칸이 그 장비다.
    from aoi_verification.app.workers.exporter import (COL_VAL, HEADER_AOI_ROW,
                                                       _machine_label)
    for name in ("AOI-4", "AOI-5"):
        assert wb[name][f"{COL_VAL}{HEADER_AOI_ROW}"].value == _machine_label(name)
    # 진행률은 파일 전체로 한 번만 0 → 끝(장비마다 되돌아가지 않는다).
    done = [d for d, _t in seen]
    assert done == sorted(done) and len({t for _d, t in seen}) == 1


# ---------------------------------------------------------------------------
# 설정 화면 · 결과 화면
# ---------------------------------------------------------------------------
def test_setup_page_collects_extra_verification_folders(styled_qapp, tmp_path,
                                                         isolated_cache):
    from aoi_verification.app.ui.pages.setup_page import SetupPage
    for name in ("ref", "aoi4", "aoi5"):
        (tmp_path / name).mkdir()
    page = SetupPage()
    page.ref_path_edit.setText(str(tmp_path / "ref"))
    page.val_path_edit.setText(str(tmp_path / "aoi4"))
    page.val_machine_edit.setText("AOI-4")
    page._add_val_row()
    page._add_val_row()                      # 빈 줄은 건너뛴다
    page._val_rows[0]["edit"].setText(str(tmp_path / "aoi5"))
    page._val_rows[0]["machine"].setText("AOI-5")
    inp = page._collect_input()
    assert inp is not None
    assert inp.extra_vals == [(tmp_path / "aoi5", "AOI-5")]
    page._remove_val_row(page._val_rows[0])
    page._remove_val_row(page._val_rows[0])
    assert page._collect_input().extra_vals is None, "줄을 다 빼면 예전 실행과 같아야 한다"


def test_setup_page_refuses_the_same_folder_twice(styled_qapp, tmp_path,
                                                   isolated_cache, monkeypatch):
    from aoi_verification.app.ui.pages import setup_page as sp
    warned = []
    monkeypatch.setattr(sp.sheets, "warn", lambda *a, **k: warned.append(a))
    for name in ("ref", "aoi4"):
        (tmp_path / name).mkdir()
    page = sp.SetupPage()
    page.ref_path_edit.setText(str(tmp_path / "ref"))
    page.val_path_edit.setText(str(tmp_path / "aoi4"))
    page._add_val_row(str(tmp_path / "aoi4"), "AOI-4b")
    assert page._collect_input() is None and warned


def test_result_page_equipment_buttons(styled_qapp):
    from aoi_verification.app.ui.pages.result_page import ResultPage
    page = ResultPage()
    picked = []
    page.equip_requested.connect(picked.append)
    page.set_equipments(["AOI-4", "AOI-5"], 0, lambda: [])
    assert not page._equip_host.isHidden()
    from aoi_verification.app.ui.widgets.neon_button import NeonButton
    btns = [page._equip_lay.itemAt(i).widget() for i in range(page._equip_lay.count())]
    btns = [b for b in btns if isinstance(b, NeonButton)]
    assert [b.text() for b in btns] == ["AOI-4", "AOI-5"]
    btns[1].click()
    assert picked == [1]
    page.set_equipments([], 0, None)
    assert page._equip_host.isHidden() and page._results_provider is None
