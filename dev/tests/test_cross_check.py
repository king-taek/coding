"""교차검증(양방향) — 기준→검증(A) 다음 검증→기준(B).

사용자 결정(2026-10-24): B 의 사진도 하나씩 검토하되, **A 에서 이미 짝지은 사진은
B 의 선별·검토에 다시 나타나지 않고 결과에만 남는다.**  여기서 못 박는 것:

  · B 의 큐에서 A 매칭 검증 사진이 빠진다(순서 유지).
  · B 의 후보 풀은 기준 사진 **전부**다 — A 에서 짝지은 기준 사진을 빼면 '기준 결함
    하나 ↔ 검증 사진 둘' 의 두 번째 사진이 '검증에만 있음' 으로 거짓 보고된다.
  · B 결과는 정방향(``ref_path`` = 기준 사진)으로 돌아와 결과에 합쳐지고, 한쪽에서
    놓친 사진이 다른 방향에서 짝을 찾으면 미매칭에서 빠진다(측면별로).
  · 결과 화면 실패 검토로 생긴 매치는 A·B 몫으로 정확히 갈린다.
  · 엑셀: 검증에만 있는 사진은 **검증 열(D)** 에 놓인다.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from aoi_verification.app.models import cross_check as cc
from aoi_verification.app.models.result import FinalResult, MatchResult, MissEntry
from aoi_verification.app.models.slot import ImageItem


def _m(slot, r, v, score=0.9):
    return MatchResult(slot=slot, ref_path=Path(r), val_path=Path(v), score=score)


def _it(slot, p, side):
    return ImageItem(slot=slot, path=Path(p), side=side)


# ---------------------------------------------------------------------------
# 순수 로직
# ---------------------------------------------------------------------------
def test_swap_is_its_own_inverse():
    m = _m("S1", "/r/a.jpg", "/v/b.jpg", 0.7)
    s = cc.swap(m)
    assert (s.ref_path, s.val_path, s.score) == (Path("/v/b.jpg"), Path("/r/a.jpg"), 0.7)
    assert cc.swap(s) == m


def test_reverse_queue_drops_only_a_matched_vals_and_keeps_order():
    vals = {"S1": [_it("S1", f"/v/{n}.jpg", "val") for n in "abcd"],
            "S2": [_it("S2", "/v2/x.jpg", "val")]}
    a = [_m("S1", "/r/1.jpg", "/v/b.jpg"), _m("S1", "/r/2.jpg", "/v/d.jpg")]
    q = cc.reverse_queue(vals, a)
    assert [it.path.name for it in q["S1"]] == ["a.jpg", "c.jpg"]
    assert [it.path.name for it in q["S2"]] == ["x.jpg"]


def test_reverse_queue_compares_paths_not_slot_names():
    """KLA 해석으로 슬롯명이 바뀌어도 같은 파일이면 같은 사진이다."""
    vals = {"WAFER01": [_it("WAFER01", "/v/a.jpg", "val")]}
    a = [_m("old-name", "/r/1.jpg", "/v/a.jpg")]
    assert cc.reverse_queue(vals, a) == {"WAFER01": []}


def test_as_val_misses_flips_side_only():
    e = MissEntry(slot="S1", side="ref", path=Path("/v/a.jpg"), note="n")
    (u,) = cc.as_val_misses([e])
    assert (u.slot, u.side, u.path, u.note) == ("S1", "val", Path("/v/a.jpg"), "n")


def test_merge_resolves_misses_per_side_and_dedupes():
    a = [_m("S1", "/r/1.jpg", "/v/1.jpg")]
    a_miss = [MissEntry("S1", "ref", Path("/r/2.jpg")),      # B 에서 짝을 찾는다
              MissEntry("S1", "ref", Path("/r/3.jpg"))]      # 끝까지 못 찾는다
    b = [_m("S1", "/r/2.jpg", "/v/9.jpg"),
         _m("S1", "/r/1.jpg", "/v/1.jpg")]                   # 같은 쌍 — 한 번만
    b_miss = [MissEntry("S1", "val", Path("/v/7.jpg")),
              MissEntry("S1", "val", Path("/v/7.jpg")),      # 중복
              MissEntry("S1", "val", Path("/v/9.jpg"))]      # 짝 있음 → 빠진다
    matches, misses = cc.merge(a, a_miss, b, b_miss)
    assert [m.key for m in matches] == [("S1", "1.jpg", "1.jpg"),
                                        ("S1", "2.jpg", "9.jpg")]
    assert [(u.side, u.path.name) for u in misses] == [("ref", "3.jpg"),
                                                       ("val", "7.jpg")]


def test_merge_does_not_cross_sides():
    """기준 쪽 미매칭은 **기준** 경로가 짝지어졌을 때만 빠진다 — 같은 이름의 검증
    사진이 매치에 있어도 무관하다(측면을 섞으면 실제 미탐이 사라진다)."""
    a_miss = [MissEntry("S1", "ref", Path("/x/same.jpg"))]
    b = [_m("S1", "/r/other.jpg", "/x/same.jpg")]
    _matches, misses = cc.merge([], a_miss, b, [])
    assert [(u.side, u.path) for u in misses] == [("ref", Path("/x/same.jpg"))]


def test_split_new_matches_routes_by_val_side_miss():
    known = [_m("S1", "/r/1.jpg", "/v/1.jpg")]
    new_a = _m("S1", "/r/2.jpg", "/v/5.jpg")      # 기준 쪽 미매칭을 푼 것
    new_b = _m("S1", "/r/1.jpg", "/v/7.jpg")      # 검증 쪽 미매칭(/v/7)을 푼 것
    a, b = cc.split_new_matches(known + [new_a, new_b], known, [Path("/v/7.jpg")])
    assert a == [new_a] and b == [new_b]


# ---------------------------------------------------------------------------
# 메인 창 흐름 (Qt 대역)
# ---------------------------------------------------------------------------
def _scan():
    s1 = SimpleNamespace(
        ref_images=[_it("S1", "/r/1.jpg", "ref"), _it("S1", "/r/2.jpg", "ref")],
        val_images=[_it("S1", f"/v/{n}.jpg", "val") for n in (1, 2, 3)])
    return SimpleNamespace(slots={"S1": s1}, common_slot_names=["S1"])


class _SelectStub:
    def __init__(self):
        self.loaded = None
        self.reverse = None

    def set_extract_mode(self, on):
        pass

    def set_reverse(self, on):
        self.reverse = on

    def load_state(self, **kw):
        self.loaded = kw


def _window(automation):
    import aoi_verification.app.ui.main_window as MW
    win = MW.MainWindow.__new__(MW.MainWindow)
    win._reset_cross_state()
    win._cross_stage = "A"
    win._input = SimpleNamespace(mode="cross", automation_level=automation,
                                 extract=False)
    win._scan = _scan()
    win._reviewed_matches = [_m("S1", "/r/1.jpg", "/v/2.jpg")]   # A 확정분
    win._a_reviewed = True
    win._matches_a = list(win._reviewed_matches)
    win._match_page = SimpleNamespace(review_context=lambda: SimpleNamespace(
        fast_results={("S1", Path("/r/1.jpg")): []},
        coord_classical_refs=frozenset()))
    win._select_page = _SelectStub()
    win._shown = []
    win._show_page = lambda w, **kw: win._shown.append(w)
    win._finished = []
    win._finish_session = lambda: win._finished.append(True)
    win._started = []
    win._start_stage2 = lambda: win._started.append(win._calib_next)
    return MW, win


def test_b_select_queue_excludes_a_matched_vals(styled_qapp):
    from aoi_verification.app.utils.prefs import AutomationLevel
    MW, win = _window(AutomationLevel.USER_SELECT)
    MW.MainWindow._enter_cross_b(win)
    q = win._select_page.loaded["queue"]
    assert [it.path.name for it in q] == ["1.jpg", "3.jpg"], \
        "A 에서 짝지은 검증 사진(2.jpg)이 B 선별에 다시 나타났다"
    assert win._select_page.reverse is True
    assert win._phase == MW.PHASE_B_SELECT
    assert win._cross_stage == "B"
    assert win._ctx_a is not None, "A 의 선계산 사본을 뜨지 않으면 실패 검토가 비게 된다"


def test_b_auto_pool_keeps_every_ref_photo(styled_qapp):
    from aoi_verification.app.utils.prefs import AutomationLevel
    MW, win = _window(AutomationLevel.AUTO_ALL)
    MW.MainWindow._enter_cross_b(win)
    (nxt,) = win._started
    queue, pool, auto = nxt
    assert [it.path.name for it in queue] == ["1.jpg", "3.jpg"]
    assert [it.path.name for it in pool["S1"]] == ["1.jpg", "2.jpg"], \
        "A 에서 짝지은 기준 사진(1.jpg)을 B 후보에서 빼면 거짓 '검증에만 있음' 이 생긴다"
    assert auto is True


def test_b_with_nothing_left_goes_straight_to_result(styled_qapp):
    from aoi_verification.app.utils.prefs import AutomationLevel
    MW, win = _window(AutomationLevel.USER_SELECT)
    win._reviewed_matches = [_m("S1", "/r/1.jpg", f"/v/{n}.jpg") for n in (1, 2, 3)]
    MW.MainWindow._enter_cross_b(win)
    assert win._finished == [True] and win._select_page.loaded is None


def test_b_parts_come_back_in_forward_orientation(styled_qapp):
    from aoi_verification.app.utils.prefs import AutomationLevel
    MW, win = _window(AutomationLevel.USER_SELECT)
    # B 자동 매치(큐 방향: ref_path = 검증 사진) + 짝 못 찾은 검증 사진
    win._matches_b = [_m("S1", "/v/1.jpg", "/r/2.jpg")]
    win._skipped_b["S1"].append(_it("S1", "/v/3.jpg", "val"))
    matches, misses = MW.MainWindow._cross_b_parts(win)
    assert [(m.ref_path.name, m.val_path.name) for m in matches] == [("2.jpg", "1.jpg")]
    assert [(u.side, u.path.name) for u in misses] == [("val", "3.jpg")]


def test_result_edit_resolving_val_miss_lands_in_b(styled_qapp):
    from aoi_verification.app.utils.prefs import AutomationLevel
    MW, win = _window(AutomationLevel.USER_SELECT)
    win._cross_stage = "B"
    win._reviewed_all_matches = list(win._reviewed_matches)
    win._reviewed_unmatched_keys = set()
    win._reviewed_unmatched = []
    win._skipped_a = {}
    win._skipped_b["S1"].append(_it("S1", "/v/3.jpg", "val"))
    newly = _m("S1", "/r/2.jpg", "/v/3.jpg", 0.8)      # 실패 검토가 짝을 찾아 줬다
    MW.MainWindow._on_result_edited(win, win._reviewed_matches + [newly], [])
    assert newly in win._b_kept, "검증 쪽 미매칭을 푼 매치가 B 보관처에 없다"
    assert newly not in win._reviewed_matches, "B 몫이 A 보관처로 새었다"
    assert not win._skipped_b["S1"], "짝을 찾은 검증 사진이 '자동 매칭 실패' 에 남았다"
    assert cc.swap(newly) in win._b_all, "검토 왕복 기반(큐 방향)에 없다"


# ---------------------------------------------------------------------------
# 엑셀 — 검증에만 있는 사진은 검증 열(D)
# ---------------------------------------------------------------------------
def test_export_puts_val_only_photo_in_val_column(isolated_cache, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6.QtWidgets")
    openpyxl = pytest.importorskip("openpyxl")
    from aoi_verification.app.i18n import ko
    from aoi_verification.app.workers.exporter import ExcelExporter

    res = FinalResult(
        mode="cross", ref_machine="1호기", val_machine="2호기",
        unmatched_refs=[MissEntry("S1", "ref", tmp_path / "ref_only.jpg"),
                        MissEntry("S1", "val", tmp_path / "val_only.jpg")])
    exp = ExcelExporter(res, dst_path=tmp_path / "out.xlsx",
                        template_path=tmp_path / "no_template.xlsx")
    exp.run()
    ws = openpyxl.load_workbook(tmp_path / "out.xlsx")[ko.SHEET_UNMATCHED]
    rows = {str(ws[f"C{r}"].value) + "|" + str(ws[f"D{r}"].value): r
            for r in range(3, ws.max_row + 1) if ws[f"C{r}"].value}
    ref_row = next(r for k, r in rows.items() if k.startswith("ref_only.jpg|"))
    val_row = next(r for k, r in rows.items() if k.endswith("|val_only.jpg"))
    # 사진(여기선 파일이 없어 이름 글자)이 그 장비의 열에, 정보는 반대 열에.
    assert ws[f"D{ref_row}"].comment.text == "미매칭"
    assert ws[f"C{val_row}"].comment.text == ko.NOTE_ONLY_VAL
    assert "val_only.jpg" in str(ws[f"C{val_row}"].value)


def test_export_lists_matched_rows_before_unmatched(isolated_cache, tmp_path):
    """결과 시트는 매치된 줄이 위, 짝 없는 줄이 아래다 — 슬롯·이름이 앞서도 마찬가지."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6.QtWidgets")
    openpyxl = pytest.importorskip("openpyxl")
    from aoi_verification.app.workers.exporter import ExcelExporter

    res = FinalResult(
        mode="single", ref_machine="1호기", val_machine="2호기",
        matches=[MatchResult("S2", tmp_path / "z_ref.jpg", tmp_path / "z_val.jpg", 0.9)],
        unmatched_refs=[MissEntry("S1", "ref", tmp_path / "a_only.jpg")])
    ExcelExporter(res, dst_path=tmp_path / "out.xlsx",
                  template_path=tmp_path / "no_template.xlsx").run()
    wb = openpyxl.load_workbook(tmp_path / "out.xlsx")
    ws = wb["out"]                         # 요약 시트 = 결과 파일명
    first, second = (str(ws[f"C{r}"].value) for r in (3, 4))
    assert "z_ref.jpg" in first and "a_only.jpg" in second, (first, second)


# ---------------------------------------------------------------------------
# 화면 — 역방향일 때 문구가 '검증 → 기준' 을 말하고, 되돌리면 원래대로
# ---------------------------------------------------------------------------
def test_pages_label_the_reverse_direction(styled_qapp):
    from aoi_verification.app.i18n import ko
    from aoi_verification.app.ui.pages.match_page import MatchPage
    from aoi_verification.app.ui.pages.match_review_page import MatchReviewPage
    from aoi_verification.app.ui.pages.select_page import SelectPage

    sel, mat, rev = SelectPage(), MatchPage(), MatchReviewPage()
    try:
        for on in (True, False):
            sel.set_reverse(on)
            mat.set_reverse(on)
            rev.set_reverse(on)
            assert sel.title.text().endswith(ko.CROSS_REVERSE_SUFFIX) is on
            assert rev.title.text().endswith(ko.CROSS_REVERSE_SUFFIX) is on
            assert mat._ref_pane_title.text() == (
                ko.PANEL_MATCH_REF_REVERSE if on else ko.PANEL_MATCH_REF)
            # 검토 표의 왼쪽 열은 큐 쪽 사진 — 역방향이면 검증이다.
            assert rev._hdr_ref.text() == (ko.COL_VAL if on else ko.COL_REF)
            assert rev._hdr_val.text() == (ko.COL_REF if on else ko.COL_VAL)
            assert rev._ref_caption_fmt() == (
                ko.COMPARE_VAL_CAPTION_FMT if on else ko.COMPARE_REF_CAPTION_FMT)
    finally:
        for w in (sel, mat, rev):
            w.deleteLater()


# ---------------------------------------------------------------------------
# 결과 화면 실패 검토 — 짝지은 상대가 반대쪽 미매칭이면 그것도 함께 해결
# ---------------------------------------------------------------------------
def test_unmatched_review_resolves_the_counterpart_entry(styled_qapp, monkeypatch):
    """기준 r1 ↔ 검증 v1 을 짝지으면 v1 의 '검증에만 있음' 항목도 해결이다.

    빼먹으면 v1 이 엑셀에 매치와 '검증에만 있음' 으로 두 번 찍히고, 같은 창에서
    v1 항목이 r1 을 다시 골라 같은 쌍이 한 번 더 생긴다(점검 쓰레드 지적)."""
    from aoi_verification.app.ui.widgets import unmatched_review_dialog as ur

    r1, v1 = Path("/tmp/xc_r1.png"), Path("/tmp/xc_v1.png")
    e_ref = MissEntry("S1", "ref", r1)
    e_val = MissEntry("S1", "val", v1)
    pool = {("S1", "ref"): [ImageItem("S1", v1, "val")],
            ("S1", "val"): [ImageItem("S1", r1, "ref")]}
    monkeypatch.setattr(ur.UnmatchedReviewDialog, "_lookup_or_compute_score",
                        lambda self, e, it: 0.5)
    dlg = ur.UnmatchedReviewDialog(unmatched=[e_ref, e_val], val_pool=pool,
                                   already_used_vals=set(), parent=None)
    try:
        dlg._make_match(e_ref, ImageItem("S1", v1, "val"))
        assert dlg._entry_resolved(1), "짝이 된 v1 의 '검증에만 있음' 이 남았다"
        assert r1 in dlg._used_vals, "v1 항목이 r1 을 다시 고를 수 있다(중복 쌍)"
        assert [(m.ref_path, m.val_path) for m in dlg.new_matches] == [(r1, v1)]
    finally:
        dlg.deleteLater()


# ---------------------------------------------------------------------------
# 상단 레일 — 교차검증은 선별·매칭·검토를 1차·2차 두 번 보여 준다(8단계)
# ---------------------------------------------------------------------------
def _rail_window(stage):
    import aoi_verification.app.ui.main_window as MW
    win = MW.MainWindow.__new__(MW.MainWindow)
    win._reset_cross_state()
    win._cross_stage = stage
    win._input = SimpleNamespace(mode="cross" if stage else "single", extract=False)
    (win._setup_page, win._select_page, win._match_page,
     win._match_review_page, win._result_page) = (object() for _ in range(5))
    return win


def test_rail_follows_the_cross_order(styled_qapp):
    from aoi_verification.app import i18n
    a, b = _rail_window("A"), _rail_window("B")
    assert a._rail_labels(a._select_page) == i18n.KO.CROSS_JOURNEY_RAIL_STEPS
    pages = ("_select_page", "_match_page", "_match_review_page")
    assert [a._rail_index(getattr(a, p)) for p in pages] == [1, 2, 3]
    assert [b._rail_index(getattr(b, p)) for p in pages] == [4, 5, 6]
    assert b._rail_index(b._result_page) == 7
    labels = i18n.KO.CROSS_JOURNEY_RAIL_STEPS
    assert labels[4].startswith("2차") and labels[7] == "결과"
    # 복귀: 결과 → 2차 검토(실제 복귀 경로), 그 밖에는 설정만.
    assert b._rail_routes(7) == (6,)
    assert b._rail_routes(4) == (0,) and a._rail_routes(2) == (0,)


def test_single_mode_rail_is_unchanged(styled_qapp):
    from aoi_verification.app import i18n
    w = _rail_window("")
    assert w._rail_labels(w._select_page) == i18n.KO.JOURNEY_RAIL_STEPS
    assert w._rail_index(w._result_page) == 4
    assert w._rail_routes(4) == (3,)


def test_setup_starts_cross_off_and_tolerance_at_default(styled_qapp, monkeypatch):
    """앱을 켤 때마다 교차검증은 꺼짐, 허용 오차는 200 µm 에서 시작한다(사용자 결정)."""
    from aoi_verification.app import config
    from aoi_verification.app.utils import prefs as _prefs
    saved = _prefs.UiPrefs(coord_tolerance=350.0, prefs_version=_prefs.PREFS_VERSION)
    monkeypatch.setattr(_prefs, "load", lambda: saved)
    from aoi_verification.app.ui.pages.setup_page import SetupPage
    page = SetupPage()
    try:
        assert not page.is_cross_mode()
        assert page.coord_tol_spin.value() == config.DEFAULT_COORD_TOLERANCE == 200.0
        assert not hasattr(_prefs.UiPrefs(), "cross_check")
    finally:
        page.deleteLater()


# ---------------------------------------------------------------------------
# 검토에서 매치를 **전부** '매치 없음' 으로 표시해도 자동 매치가 되살아나지 않는다
# ---------------------------------------------------------------------------
def test_review_that_drops_every_match_is_not_treated_as_unreviewed(styled_qapp):
    """빈 kept 는 '검토 안 함' 이 아니라 '전부 버림' 이다.

    ★ 예전에는 `_reviewed_matches` 가 비면 자동 매치(`_merge_matches`)로 돌아가서,
      사용자가 지운 짝이 결과에 그대로 나오고 2차 큐에서도 그 검증 사진이 빠졌다."""
    from aoi_verification.app.utils.prefs import AutomationLevel
    MW, win = _window(AutomationLevel.USER_SELECT)
    a_match = win._reviewed_matches[0]
    win._reviewed_matches = []
    win._reviewed_unmatched = []
    win._reviewed_unmatched_keys = set()
    win._reviewed_all_matches = []
    win._a_reviewed = False
    win._merge_matches = lambda: [a_match]              # 자동 매치는 짝이 있다
    win._match_review_page = SimpleNamespace(
        unmatched_keys=lambda: {a_match.key}, all_matches=lambda: [a_match])
    miss = MissEntry("S1", "ref", a_match.ref_path, "x")
    win._on_match_review_done([], [miss])
    assert win._a_final_matches() == []
    # 2차 큐에 사용자가 짝을 지운 검증 사진이 다시 들어간다.
    queued = [it.path for it in win._select_page.loaded["queue"]]
    assert a_match.val_path in queued
