"""매치 검토 — '매치 없음' 처리(안 A, 사용자 결정 2026-11-11).

현택님 지적: "검토 단계에서 매치없음 처리 UX가 직관적이지 않음".  고친 것:
- 버튼이 '×' 한 글자 → 낱말이 붙은 '✕ 매치 없음' / '↩ 되돌리기'.
- 누르면 후보만 사라지고 짝은 그대로 보였다 → 검증 사진에 사선, 거리에 취소선, 안내 한 줄.
  후보는 **숨기지 않는다** — 그 줄에서 바로 다른 사진을 고를 수 있어야 한다.
- 완료 버튼이 '유지 n' 만 말했다 → 매치 없음 수도 함께.
- **버그**: 결과 화면에서 되돌아오면 표시 집합은 되살아나는데 행은 평범하게 그려졌다
  (위 숫자는 '매치 없음 1' 인데 그 줄은 정상처럼 보이고, × 를 누르면 표시가 **풀렸다**).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app import i18n                                # noqa: E402
from aoi_verification.app.models.result import MatchResult           # noqa: E402
from aoi_verification.app.models.slot import ImageItem               # noqa: E402


def _ms(tmp_path, n=3):
    out = []
    for i in range(n):
        ref = tmp_path / f"r{i}.jpg"
        val = tmp_path / f"v{i}.jpg"
        ref.write_bytes(b"")
        val.write_bytes(b"")
        out.append(MatchResult(slot=f"S{i}", ref_path=ref, val_path=val,
                               score=0.9 - i * 0.1))
    return out


def _page(ms, **kw):
    from aoi_verification.app.ui.pages.match_review_page import MatchReviewPage
    page = MatchReviewPage()
    page.load_state(ms, coord_mode=True, tolerance=200.0, **kw)
    return page


def _row(page, m):
    return page._rows_by_key[m.key]


def test_reentry_draws_restored_marks_on_the_rows(styled_qapp, tmp_path):
    """결과 화면 → 검토 복귀: 되살린 표시가 **행에도** 그려져야 한다."""
    ms = _ms(tmp_path)
    page = _page(ms, unmatched_keys={ms[1].key})
    row = _row(page, ms[1])
    assert row.state() == "unmatched", "표시 집합에는 있는데 행은 평범하게 그려졌다"
    assert row.btn_toggle.text() == i18n.KO.BTN_RESTORE_COMPACT
    assert _row(page, ms[0]).state() == "ok"
    page.deleteLater()


def test_toggle_after_reentry_keeps_screen_and_count_in_step(styled_qapp, tmp_path):
    """복귀 뒤 그 줄의 버튼은 '되돌리기' 여야 한다 — 예전엔 누르면 오히려 표시가 풀렸다."""
    ms = _ms(tmp_path)
    page = _page(ms, unmatched_keys={ms[1].key})
    page._on_toggle(ms[1])
    assert ms[1].key not in page.unmatched_keys()
    assert _row(page, ms[1]).state() == "ok"
    page.deleteLater()


def test_buttons_say_what_they_do(styled_qapp, tmp_path):
    ms = _ms(tmp_path)
    page = _page(ms)
    btn = _row(page, ms[0]).btn_toggle
    assert "매치 없음" in btn.text(), "버튼이 기호만 있다 — 무엇을 하는지 읽히지 않는다"
    page._on_toggle(ms[0])
    assert "되돌리기" in btn.text()
    page.deleteLater()


def test_marked_row_keeps_candidates_and_crosses_out_the_pair(styled_qapp, tmp_path):
    """표시해도 후보는 남는다(바로 다른 사진을 고를 수 있게) — 대신 짝을 지운 모양으로."""
    from aoi_verification.app.ui.pages.match_review_page import _MatchRow
    m = _ms(tmp_path, 1)[0]
    cand = ImageItem(slot=m.slot, path=tmp_path / "c.jpg", side="val")
    row = _MatchRow(m, runners_up=[(cand, 0.5)], thumb_px=118,
                    coord_mode=True, tolerance=200.0)
    row.resize(1280, 200)
    row.show()
    row.set_unmatched(True)
    assert row._runner_host.isVisibleTo(row), "후보를 숨겼다 — 다른 사진을 고르려면 두 번 눌러야 한다"
    assert row._first_line_host.isVisibleTo(row)
    assert row._slash.isVisibleTo(row), "검증 사진에 '지움' 표시가 없다"
    assert row._no_match_note.isVisibleTo(row)
    assert "line-through" in row._metric_label.styleSheet()
    row.set_unmatched(False)
    assert not row._slash.isVisibleTo(row)
    assert not row._no_match_note.isVisibleTo(row)
    assert "line-through" not in row._metric_label.styleSheet()
    row.hide()
    row.deleteLater()


def test_done_button_counts_no_match(styled_qapp, tmp_path):
    ms = _ms(tmp_path)
    page = _page(ms)
    assert page.btn_done.text() == i18n.KO.BTN_FINISH_REVIEW_KEPT_FMT.format(n=3)
    page._on_toggle(ms[0])
    assert page.btn_done.text() == i18n.KO.BTN_FINISH_REVIEW_KEPT_NONE_FMT.format(n=2, m=1)
    page.deleteLater()


def test_toggle_column_fits_the_longer_label(styled_qapp):
    """버튼 낱말 중 긴 쪽이 열 안에 들어가야 한다 — 고정 폭이라 넘치면 잘린다."""
    from PyQt6.QtGui import QFont, QFontMetrics
    from PyQt6.QtWidgets import QApplication
    from aoi_verification.app.ui import theme
    from aoi_verification.app.ui.pages import match_review_page as mrp
    f = QApplication.font()
    f.setPixelSize(theme.PROFILE.font_caption_lg)
    f.setWeight(QFont.Weight.Bold)
    fm = QFontMetrics(f)
    for text in (i18n.KO.BTN_NO_MATCH_COMPACT, i18n.KO.BTN_RESTORE_COMPACT):
        assert fm.horizontalAdvance(text) + 2 * 8 + 2 <= mrp._toggle_col_w(), text
