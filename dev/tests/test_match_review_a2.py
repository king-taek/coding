"""매치 검토 A2 밀집 리스트 — 상태 분류/집계·필터·키보드·800px 클램프."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication          # noqa: E402

from aoi_verification.app.models.result import MatchResult          # noqa: E402
from aoi_verification.app.ui.pages.match_review_page import (       # noqa: E402
    classify_row, tally)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _m(slot: str, score: float, name: str = "r.jpg") -> MatchResult:
    return MatchResult(slot=slot, ref_path=Path(f"/tmp/{slot}_{name}"),
                       val_path=Path(f"/tmp/{slot}_v.jpg"), score=score)


# ── classify_row — 기존 score 인코딩 그대로, 새 판정 로직 없음 ──────────────
def test_classify_ok_similarity_mode():
    assert classify_row(0.9, False, False) == "ok"


def test_classify_over_only_in_coord_mode():
    # 좌표 모드의 음수 score = '허용범위 초과' 인코딩.
    assert classify_row(-0.2, True, False) == "over"
    # 일반(유사도) 모드에서는 음수여도 over 로 분류하지 않는다.
    assert classify_row(-0.2, False, False) == "ok"


def test_classify_unmatched_wins():
    assert classify_row(0.9, True, True) == "unmatched"
    assert classify_row(-0.2, True, True) == "unmatched"


def test_tally_counts():
    ms = [_m("S1", 0.9), _m("S2", -0.1), _m("S3", 0.8), _m("S4", 0.7)]
    unmatched = {ms[3].key}
    assert tally(ms, unmatched, True) == (2, 1, 1)
    # 유사도 모드에선 음수도 ok.
    assert tally(ms, unmatched, False) == (3, 0, 1)


# ── 페이지 통합 — 필터/키보드/완료 페이로드 ────────────────────────────────
def _page(qapp, coord=True):
    from aoi_verification.app.ui.pages.match_review_page import MatchReviewPage
    page = MatchReviewPage()
    ms = [_m("S1", 0.9), _m("S2", -0.4), _m("S3", 0.8)]
    page.load_state(ms, coord_mode=coord, tolerance=20.0)
    return page, ms


def test_filter_hides_only_ok_rows_and_done_unaffected(qapp):
    page, ms = _page(qapp)
    # S1 을 '매치 없음' 처리 → ok(S3) 1 / over(S2) 1 / unmatched(S1) 1
    # (좌표 모드 정렬은 score 오름차순이라 _rows[0] 은 S1 이 아님 — slot 으로 찾는다.)
    s1 = next(r for r in page._rows if r.match.slot == "S1")
    page._on_toggle(s1.match)
    page.btn_filter.setChecked(True)
    hidden = {r.match.slot: r.isHidden() for r in page._rows}
    # over(S2)·unmatched(S1) 는 보이고, ok(S3) 만 숨는다.
    assert hidden["S3"] and not hidden["S2"] and not hidden["S1"]
    # 완료 결과는 표시 여부와 무관 — 전체 3쌍이 kept/unmatched 로 모두 나온다.
    got = []
    page.finished.connect(lambda k, u: got.append((k, u)))
    page._on_done()
    kept, unmatched = got[0]
    assert len(kept) + len(unmatched) == 3
    assert len(unmatched) == 1 and unmatched[0].note == "미매칭 (사용자 검토)"
    page.deleteLater()


def test_keyboard_down_r_enter(qapp):
    from PyQt6.QtTest import QTest
    from PyQt6.QtCore import Qt
    page, ms = _page(qapp)
    QTest.keyClick(page, Qt.Key.Key_Down)
    QTest.keyClick(page, Qt.Key.Key_Down)
    rows = page._visible_rows()
    assert page._current_row is rows[1]
    # R → 현재 행 '매치 없음' 토글
    QTest.keyClick(page, Qt.Key.Key_R)
    assert page._current_row.match.key in page._unmatched_keys
    # Enter → 검토 완료 (finished 1회, 페이로드 유형 검증)
    got = []
    page.finished.connect(lambda k, u: got.append((k, u)))
    QTest.keyClick(page, Qt.Key.Key_Return)
    assert len(got) == 1
    kept, unmatched = got[0]
    assert len(kept) == 2 and len(unmatched) == 1
    page.deleteLater()


def test_swap_keeps_layout_index_and_current(qapp):
    from aoi_verification.app.models.slot import ImageItem
    page, ms = _page(qapp)
    target = page._visible_rows()[1]
    page._set_current(target)
    old_match = target.match
    layout_idx = page._list_layout.indexOf(target)
    new_item = ImageItem(slot=old_match.slot, path=Path("/tmp/new_v.jpg"),
                         side="val")
    page._on_swap(old_match, new_item, 0.95)
    new_row = page._rows_by_key[(old_match.slot, old_match.ref_path.name,
                                 "new_v.jpg")]
    assert page._list_layout.indexOf(new_row) == layout_idx   # 같은 자리
    assert page._current_row is new_row                       # 현재 행 승계
    page.deleteLater()


def test_row_clamp_at_800_window(qapp):
    """800×600 창(행 폭 ~750)에서 가로 넘침 없음 — 최소 썸네일도 수용."""
    from aoi_verification.app.ui.pages.match_review_page import (
        _MatchRow, _SIZE_MIN_PX)
    row = _MatchRow(_m("S1", 0.9), runners_up=[], thumb_px=140)
    row._row_width = lambda: 750
    row.set_thumb_size(360)
    assert 2 * row._thumb_px + row._reserved_fixed_px() <= 750
    assert row._reserved_fixed_px() + 2 * _SIZE_MIN_PX <= 750
    row.deleteLater()


def test_shrink_after_big_thumbs_reclamps_no_hscroll(qapp):
    """큰 썸네일(360) 상태에서 창을 1512→800 으로 줄여도 가로 스크롤 0.

    행이 자기 width() 대신 스크롤 뷰포트 폭으로 재클램프해야 한다 (회귀:
    행 최소폭이 뷰포트보다 커지면 행 resizeEvent 만으로는 복구 불가)."""
    page, ms = _page(qapp)
    page.resize(1512, 982)
    page.show()
    qapp.processEvents()
    page.size_slider.setValue(360)
    page._apply_thumb_size()
    qapp.processEvents()
    page.resize(800, 600)
    qapp.processEvents()
    page._apply_thumb_size()          # debounce 타이머 대신 직접 재클램프
    qapp.processEvents()
    assert page._scroll.horizontalScrollBar().maximum() == 0
    for row in page._rows:
        assert 2 * row._thumb_px + row._reserved_fixed_px() <= \
            page._scroll.viewport().width()
    page.deleteLater()


def test_chip_only_on_exceptions(qapp):
    """정상(일치) 행엔 배지 없음, 예외(초과/매치 없음)만 칩 텍스트."""
    from aoi_verification.app.ui.pages.match_review_page import _MatchRow
    ok = _MatchRow(_m("S1", 0.9), runners_up=[], thumb_px=140, coord_mode=True,
                   tolerance=20.0)
    assert ok._chip.text() == ""                       # 일치 → 배지 없음
    over = _MatchRow(_m("S2", -0.5), runners_up=[], thumb_px=140,
                     coord_mode=True, tolerance=20.0)
    assert over._chip.text() != ""                     # 초과 → 칩 표시
    over.set_unmatched(True)
    assert over._chip.text() != ""                     # 매치 없음 → 칩 표시
    ok.deleteLater(); over.deleteLater()


def test_metric_one_line_no_over_suffix(qapp):
    """metric 컬럼은 '허용범위 초과' 접미어 없이 거리만 (칩이 전담)."""
    from aoi_verification.app.ui.pages.match_review_page import _MatchRow
    over = _MatchRow(_m("S1", -0.5), runners_up=[], thumb_px=140,
                     coord_mode=True, tolerance=20.0)
    assert "초과" not in over._metric_label.text()
    assert "µm" in over._metric_label.text()
    over.deleteLater()


def test_compact_toggle_touch_target_and_intent(qapp):
    """토글은 44px 터치 타깃 + reject 의도 프로퍼티(hover 위험색 트리거)."""
    from aoi_verification.app.ui.pages.match_review_page import _MatchRow
    row = _MatchRow(_m("S1", 0.9), runners_up=[], thumb_px=140)
    assert row.btn_toggle.width() >= 44
    assert row.btn_toggle.property("compact") is True
    assert row.btn_toggle.property("intent") == "reject"
    row.set_unmatched(True)                             # 되돌리기 → 의도 해제
    assert row.btn_toggle.property("intent") == ""
    row.deleteLater()


def test_secondary_actions_are_links(qapp):
    """반복 2차 액션(크게 보기·더 보기)은 링크형 role."""
    from aoi_verification.app.ui.pages.match_review_page import _MatchRow
    from aoi_verification.app.models.slot import ImageItem
    runners = [(ImageItem(slot="S1", path=Path(f"/tmp/c{i}.jpg"), side="val"),
                0.6 - i * 0.1) for i in range(6)]
    row = _MatchRow(_m("S1", 0.9), runners_up=runners, thumb_px=140)
    assert row.btn_view.property("role") == "link"
    assert row.btn_more is not None and row.btn_more.property("role") == "link"
    row.deleteLater()


def test_neon_button_is_matte_and_role_driven(qapp):
    """'도면' 은 무광 — 어떤 role 도 글로우/그림자 이펙트를 달지 않는다(색은 QSS)."""
    from aoi_verification.app.ui import theme
    from aoi_verification.app.ui.widgets.neon_button import NeonButton
    b1 = NeonButton("x", role="primary")
    b2 = NeonButton("y", role="ghost")
    b3 = NeonButton("z", role="danger")
    for b in (b1, b2, b3):
        assert b.graphicsEffect() is None          # 무광 — 이펙트 없음
        assert b.minimumHeight() >= theme.PROFILE.control_h
    b1.setRole("ghost")                             # role 은 QSS 프로퍼티로만
    assert b1.property("role") == "ghost"
    assert b1.graphicsEffect() is None
    for b in (b1, b2, b3):
        b.deleteLater()


# ── 라운드1 개선 회귀 방지 ───────────────────────────────────────────────────
def test_filter_empty_state_when_no_needs_check(qapp):
    """'확인 필요만' 이 0건이면 빈 상태 안내가 노출된다(A3/C23)."""
    from aoi_verification.app.ui.pages.match_review_page import MatchReviewPage
    page = MatchReviewPage()
    # 전부 일치(ok) — 필터를 켜면 남는 행이 없다.
    ms = [_m("S1", 0.9), _m("S2", 0.8), _m("S3", 0.85)]
    page.load_state(ms, coord_mode=True, tolerance=20.0)
    assert page._filter_empty.isHidden()               # 평소엔 숨김
    page.btn_filter.setChecked(True)
    assert not page._filter_empty.isHidden()            # 0건 → 빈 상태 노출
    page.btn_filter.setChecked(False)
    assert page._filter_empty.isHidden()                # 해제 → 다시 숨김
    page.deleteLater()


def test_list_header_and_score_rule_present(qapp):
    """제도 시트 성격 — 상단 컬럼 헤더(타이틀블록) + 점수 컬럼 눈금이 있어야 한다."""
    from PyQt6.QtWidgets import QFrame
    from aoi_verification.app.ui.pages.match_review_page import MatchReviewPage
    page = MatchReviewPage()
    roles = {f.property("role") for f in page.findChildren(QFrame)}
    assert "listHeader" in roles, "컬럼 헤더 누락"
    page.load_state([_m("S1", 0.9)], coord_mode=True, tolerance=20.0)
    roles = {f.property("role") for f in page.findChildren(QFrame)}
    assert "vrule" in roles, "점수 컬럼 눈금 누락"
    page.deleteLater()


def test_over_row_auto_expands_candidates(qapp):
    """허용 초과(실패) 행은 대안 후보를 처음부터 모두 펼친다(현장 C4)."""
    from aoi_verification.app.models.slot import ImageItem
    from aoi_verification.app.ui.pages.match_review_page import _MatchRow
    runners = [(ImageItem(slot="S1", path=Path(f"/tmp/c{i}.jpg"), side="val"),
                0.5 - i * 0.05) for i in range(6)]
    over = _MatchRow(_m("S1", -0.5), runners_up=runners, thumb_px=140,
                     coord_mode=True, tolerance=20.0)
    ok = _MatchRow(_m("S2", 0.9), runners_up=runners, thumb_px=140,
                   coord_mode=True, tolerance=20.0)
    assert over._visible_lines > 1          # 실패 행은 펼침
    assert ok._visible_lines == 1           # 일치 행은 첫 줄만(빠른 확인)
    over.deleteLater(); ok.deleteLater()
