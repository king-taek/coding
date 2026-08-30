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


_QSS = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui"
        / "style.qss").read_text(encoding="utf-8")


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


def test_all_rows_stay_visible_and_done_passes_everything(qapp):
    """'확인 필요만' 필터를 지운 뒤 — **모든 행이 항상 보인다.**

    필터는 상단 탤리(일치·허용 초과·매치 없음) 위에 상태를 하나 더 얹고, '지금 보이는
    게 전부인가'를 매번 되묻게 만들었다.  탤리가 이미 무엇을 확인해야 하는지 말한다."""
    page, ms = _page(qapp)
    assert not hasattr(page, "btn_filter"), "'확인 필요만' 버튼이 되살아났다"
    assert not hasattr(page, "_apply_filter"), "필터 로직이 되살아났다"
    s1 = next(r for r in page._rows if r.match.slot == "S1")
    page._on_toggle(s1.match)
    assert all(not r.isHidden() for r in page._rows), "행이 숨겨졌다"
    # 완료 결과는 전체 3쌍이 kept/unmatched 로 모두 나온다(불변).
    got = []
    page.finished.connect(lambda k, u: got.append((k, u)))
    page._on_done()
    kept, unmatched = got[0]
    assert len(kept) + len(unmatched) == 3
    assert len(unmatched) == 1 and unmatched[0].note == "미매칭 (사용자 검토)"
    page.deleteLater()


def test_keyboard_shortcuts_are_gone(qapp):
    """★ ↑↓ · R · Enter 는 **아무 일도 하지 않아야** 한다.

    Enter 가 특히 위험했다 — 포커스가 페이지에 있는 동안 무심코 누른 한 번이 검토 전체를
    끝냈다.  기능 손실은 없다: '매치 없음'은 행마다 있는 ✕ 토글, '검토 완료'는 상단 버튼.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    page, ms = _page(qapp)
    page.setFocus()
    for _ in range(4):
        qapp.processEvents()
    before_current = page._current_row
    before_unmatched = set(page._unmatched_keys)
    got: list = []
    page.finished.connect(lambda k, u: got.append((k, u)))

    for key in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_R,
                Qt.Key.Key_Return, Qt.Key.Key_Enter):
        QTest.keyClick(page, key)
        qapp.processEvents()

    assert page._current_row is before_current, "방향키가 현재 행을 옮겼다"
    assert set(page._unmatched_keys) == before_unmatched, "R 이 매치 없음을 토글했다"
    assert got == [], "Enter 가 검토를 끝냈다"
    # 키를 가르치는 힌트 줄도 함께 사라졌다(없는 조작을 안내하지 않는다).
    assert not hasattr(page, "_build_key_hint")
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


def test_view_larger_reads_as_a_button(qapp):
    """★ '크게 보기'는 **버튼으로 보여야** 한다 — 사용자 보고: "버튼처럼 안 보임".

    사진 더블클릭 확대를 없앤 뒤로 이것이 좌우 비교 뷰어로 가는 **유일하게 눈에 보이는
    경로**다(썸네일 우클릭 메뉴는 발견되지 않는다).  링크형(테두리 없음·$mute·12px)은
    '눌러도 되는 것'으로 읽히지 않아 기능이 없는 것과 같았다.

    '펼치기/접기'는 이동이 아니라 제자리 펼침이므로 링크로 남는다(캐럿 ▾/▴ 이 말한다) —
    등급 차이가 의도라는 것을 여기서 고정한다."""
    from aoi_verification.app.ui import theme
    from aoi_verification.app.ui.pages.match_review_page import _MatchRow
    from aoi_verification.app.models.slot import ImageItem
    runners = [(ImageItem(slot="S1", path=Path(f"/tmp/c{i}.jpg"), side="val"),
                0.6 - i * 0.1) for i in range(6)]
    qapp.setStyleSheet(theme.render_qss(_QSS))
    row = _MatchRow(_m("S1", 0.9), runners_up=runners, thumb_px=140)
    row.show()
    for _ in range(12):
        qapp.processEvents()
    try:
        assert row.btn_view.property("role") == "rowAction", \
            "'크게 보기'가 아직 링크형이다"
        # QSS 등급이 실제로 선언돼 있고, :focus·:disabled 가 **역할별로** 따로 있다
        # (없으면 role 의 border-color 가 포커스 링을 덮어 링이 안 그려진다 — 실측 전례).
        assert 'QPushButton[role="rowAction"]' in _QSS
        assert 'QPushButton[role="rowAction"]:focus' in _QSS
        assert 'QPushButton[role="rowAction"]:disabled' in _QSS
        # 렌더된 크기가 터치 하한을 넘는가(WCAG 2.5.8 AA 24 · 프로젝트 하한 26).
        assert row.btn_view.height() >= theme.PROFILE.target_min, \
            f"렌더 높이 {row.btn_view.height()}px < {theme.PROFILE.target_min}"
        assert row.btn_view.width() >= 24
        # 슬롯 컬럼(96px) 밖으로 나가지 않는다 — 나가면 가로 스크롤이 생긴다.
        assert row.btn_view.width() <= 96
        # 펼치기/접기는 링크 유지 + 캐럿으로 성격을 말한다.
        assert row.btn_more is not None and row.btn_more.property("role") == "link"
        assert "▾" in row.btn_more.text() and "▴" in row.btn_less.text()
    finally:
        row.hide()
        qapp.processEvents()
        row.deleteLater()
        qapp.processEvents()


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


def test_header_labels_sit_over_their_own_photos(qapp):
    """★ '기준'·'검증' 라벨이 **자기 사진 위**에 있어야 한다 — 좌표로 잰다.

    이전엔 'COL_IMAGES = 기준 · 검증 · 후보' 한 라벨을 이미지 영역 전체에 얹어, 셋 중
    어느 것도 자기 사진 위에 없었다.  '라벨이 존재한다'로는 증명이 안 되므로 중심
    좌표를 비교한다.

    ★ 썸네일 크기는 슬라이더로 100~360px 사이에서 바뀐다.  헤더가 따라가지 않으면
    크기를 바꾼 순간 정렬이 깨지므로 **여러 크기에서** 확인한다."""
    from aoi_verification.app.ui.pages.match_review_page import MatchReviewPage
    from aoi_verification.app.ui import theme
    qapp.setStyleSheet(theme.render_qss(_QSS))
    page = MatchReviewPage()
    page.load_state([_m("S1", 0.9), _m("S2", 0.8)], coord_mode=True, tolerance=20.0)
    page.resize(1400, 900)
    page.show()
    for _ in range(25):
        qapp.processEvents()

    def center_x(w):
        return w.mapTo(page, w.rect().topLeft()).x() + w.width() / 2

    try:
        for size in (100, 140, 240, 360):
            page.size_slider.setValue(size)
            page._apply_thumb_size()
            for _ in range(18):
                qapp.processEvents()
            row = page._rows[0]
            for label, hdr, img in (("기준", page._hdr_ref, row._ref_img),
                                    ("검증", page._hdr_val, row._val_img)):
                d = abs(center_x(hdr) - center_x(img))
                assert d <= 2.0, f"thumb={size}: '{label}' 헤더가 사진에서 {d:.1f}px 어긋남"
    finally:
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
def test_next_issue_is_locked_when_there_is_nothing_to_visit(qapp):
    """예외가 0 건이면 [다음 예외로] 는 **비활성**이다.

    ★ 예전엔 활성으로 보이는데 눌러도 아무 일이 없었다(순회할 예외 행이 없으면
      루프가 그냥 끝난다) — 같은 시안에서 [묶기]·벌크 액션의 '먹은 클릭' 을 고쳐
      놓고 이 버튼만 남아 있었다.  잠긴 이유는 툴팁이 말한다."""
    from aoi_verification.app import i18n
    from aoi_verification.app.ui.pages.match_review_page import MatchReviewPage
    page = MatchReviewPage()
    try:
        ms = [_m("S1", 0.9), _m("S2", 0.8)]        # 전부 일치
        page.load_state(ms, coord_mode=True, tolerance=20.0)
        assert page._btn_next_issue.isEnabled() is False, (
            "갈 곳이 없는데 버튼이 열려 있다")
        assert (page._btn_next_issue.toolTip()
                == i18n.KO.BTN_NEXT_ISSUE_NONE_TOOLTIP)

        page._on_toggle(ms[0])                     # '매치 없음' → 예외 1 건 생김
        assert page._btn_next_issue.isEnabled() is True
        assert page._btn_next_issue.toolTip() == ""

        page._on_toggle(ms[0])                     # 되돌리면 다시 0 건
        assert page._btn_next_issue.isEnabled() is False
    finally:
        page.deleteLater()


def test_the_toggle_column_fits_its_own_header_word(qapp):
    """토글 열은 **자기 헤더 낱말이 들어가는 폭**이어야 한다.

    ★ 헤더는 `setFixedWidth` 라 넘치면 말줄임 없이 잘린다("매치 없" ) — 칸에 이름을
      준 목적이 사라진다.  폭을 한 조합의 실측 상수로 박아 두면 폰트 프로필이나
      서체가 달라지는 순간 조용히 잘리므로, 실제 서체로 잰 값과 비교해 못 박는다."""
    from PyQt6.QtWidgets import QLabel, QWidget
    from aoi_verification.app import i18n
    from aoi_verification.app.ui import theme
    from aoi_verification.app.ui.pages import match_review_page as mrp
    qapp.setStyleSheet(theme.render_qss(_QSS))
    host = QWidget()
    host.show()
    try:
        lab = QLabel(i18n.KO.CHIP_NO_MATCH, host)
        lab.setProperty("role", "colHead")          # 헤더와 같은 등급 = 같은 서체
        lab.style().unpolish(lab)
        lab.style().polish(lab)
        qapp.processEvents()
        assert lab.sizeHint().width() <= mrp._toggle_col_w(), (
            f"헤더 낱말 {lab.sizeHint().width()}px 가 열 폭 "
            f"{mrp._toggle_col_w()}px 를 넘는다 — 잘려서 나간다")
    finally:
        host.hide()
        host.deleteLater()
        qapp.processEvents()


def test_the_more_candidates_button_never_offers_zero(qapp):
    """'후보 한 줄 더 보기' 는 **남은 게 있을 때만** 개수를 적는다.

    ★ 잔여가 0 이하일 때도 문구를 갈아 끼우면 '(+0)'·'(+-2)' 가 숨겨진 버튼에
      남는다 — setVisible 한 번이면 그대로 화면에 나온다."""
    from aoi_verification.app import i18n
    from aoi_verification.app.models.slot import ImageItem
    from aoi_verification.app.ui.pages.match_review_page import _MatchRow
    runners = [(ImageItem(slot="S1", path=Path(f"/tmp/c{i}.jpg"), side="val"),
                0.6 - i * 0.1) for i in range(6)]
    row = _MatchRow(_m("S1", 0.9), runners_up=runners, thumb_px=140)
    try:
        zero = i18n.KO.RUNNERUP_MORE_ROW_FMT.format(n=0)
        row._visible_lines = 99                     # 전부 펼친다 → 남은 후보 0
        row._layout_runner_tiles()
        assert row.btn_more.isVisibleTo(row) is False
        assert row.btn_more.text() != zero, "숨긴 버튼에 '(+0)' 이 적혀 있다"
        assert "-" not in row.btn_more.text()
    finally:
        row.deleteLater()
