"""1단계 패널 제목·타일 파일명 캡션이 **1024·1280 폭에서 잘리지 않는다**.

배경(실측): `role=paneTitle` QLabel 은 wordWrap 도 elide 도 없어 **말줄임표 없이 하드
클립**된다.  1024 폭에서

  · PANEL_RIGHT_TARGETS "검증 대상 (검증하기로 한 사진들)" — 자리 135px / 필요 189px
    → 화면에는 "검증 대상 (검증하기로 " 까지만 보였다.
  · PANEL_LEFT_CANDIDATES "검증 후보들 (남은 사진)" — 자리 105px / 필요 134px.

1280 에서도 여유가 0~1px 이라 서체가 조금만 넓어져도 넘어갔다.  제목에서 괄호 부연을
빼고(툴팁으로 내림) 짧게 만들어 고쳤다.

★ 왜 elide 로 덮지 않았나 — 라벨이 스스로 줄이면 실측 하네스도 이 테스트도 '잘림 없음'
  으로 읽혀, 다음에 제목이 길어져도 **아무도 모른다**.  파일명처럼 줄이는 게 옳은 자리는
  줄이고(아래 캡션 테스트), 제목은 **온전히 들어가는지** 를 잰다.

★ 문자열을 손으로 적어 굳히지 않는다 — 라벨 텍스트와 폭을 화면에서 읽어 대조한다.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QFontMetrics                    # noqa: E402
from PyQt6.QtWidgets import QLabel                      # noqa: E402

from aoi_verification.app import i18n                   # noqa: E402

_SIZES = [(1280, 800), (1024, 720)]


def _pane_titles(page):
    return [w for w in page.findChildren(QLabel)
            if w.property("role") == "paneTitle" and w.isVisible()]


def _short(label: QLabel) -> int:
    """부족한 픽셀 수(0 이하면 들어간다)."""
    fm = QFontMetrics(label.font())
    return fm.horizontalAdvance(label.text()) - label.width()


def _page(styled_qapp, w: int, h: int):
    from aoi_verification.app.ui.pages.select_page import SelectPage
    page = SelectPage()
    page.resize(w, h)
    page.show()
    for _ in range(30):
        styled_qapp.processEvents()
    return page


@pytest.mark.parametrize("w, h", _SIZES)
def test_pane_titles_are_not_clipped(styled_qapp, w, h):
    page = _page(styled_qapp, w, h)
    try:
        titles = _pane_titles(page)
        assert titles, "패널 제목을 찾지 못했다(테스트가 아무것도 검증하지 않는다)"
        offenders = [f"{t.text()!r} {_short(t):+d}px" for t in titles
                     if _short(t) > 0]
        assert offenders == [], (
            f"[{w}x{h}] 패널 제목이 잘린다(말줄임표 없이 하드 클립된다): "
            + ", ".join(offenders))
    finally:
        page.close()


@pytest.mark.parametrize("w, h", _SIZES)
def test_the_guard_would_catch_the_old_long_titles(styled_qapp, monkeypatch,
                                                   w, h):
    """가드가 늘 통과하는 빈 껍데기가 아님을 스스로 증명한다.

    되돌려진 옛 제목을 넣으면 1024 에서 실제로 잘려야 한다(1280 은 여유 0~1px 이라
    '잘림' 으로는 안 잡히지만, 아래에서 여유가 2px 미만임을 함께 못 박는다)."""
    monkeypatch.setattr(i18n.KO, "PANEL_RIGHT_TARGETS",
                        "검증 대상 (검증하기로 한 사진들)", raising=True)
    monkeypatch.setattr(i18n.KO, "PANEL_LEFT_CANDIDATES",
                        "검증 후보들 (남은 사진)", raising=True)
    page = _page(styled_qapp, w, h)
    try:
        worst = max(_short(t) for t in _pane_titles(page))
        if w == 1024:
            assert worst > 0, "옛 제목이 1024 에서 잘리지 않았다 — 측정이 고장났다"
        else:
            assert worst >= -2, "옛 제목의 1280 여유가 실측(0~1px)과 다르다"
    finally:
        page.close()


def test_pane_titles_keep_the_explanation_in_a_tooltip(styled_qapp):
    """제목에서 뺀 괄호 부연은 **툴팁으로 남는다** — 정보를 버린 게 아니다."""
    page = _page(styled_qapp, 1024, 720)
    try:
        tips = {t.text(): t.toolTip() for t in _pane_titles(page)}
        assert tips.get(i18n.KO.PANEL_RIGHT_TARGETS) == \
            i18n.KO.PANEL_RIGHT_TARGETS_TOOLTIP
        assert tips.get(i18n.KO.PANEL_LEFT_CANDIDATES) == \
            i18n.KO.PANEL_LEFT_CANDIDATES_TOOLTIP
        assert i18n.KO.PANEL_RIGHT_TARGETS_TOOLTIP.strip()
        assert i18n.KO.PANEL_LEFT_CANDIDATES_TOOLTIP.strip()
    finally:
        page.close()


# ---------------------------------------------------------------------------
# 타일 파일명 캡션 — 여기는 **줄이는 것이 옳다**(파일명은 앞뒤가 다 정보라 가운데를
# 줄인다).  실패 검토 타일이 원래 그렇게 하고 있었고, 썸네일 그리드만 말줄임표 없이
# 하드 클립했다(실측 -99px, 1280·1024 공통).
# ---------------------------------------------------------------------------
_LONG_NAME = "255350.116718.c.1047514.6.jpeg"


def test_thumb_grid_caption_is_elided_in_the_middle(styled_qapp, tmp_path):
    from aoi_verification.app.models.slot import ImageItem
    from aoi_verification.app.ui.widgets import thumb_grid

    img = tmp_path / _LONG_NAME
    img.write_bytes(b"")
    entry = thumb_grid.ThumbEntry(
        item=ImageItem(slot="S1", path=img, side="ref"))
    tile = thumb_grid._ThumbTile(entry, footer=_LONG_NAME, tile_px=120)
    tile.show()
    for _ in range(10):
        styled_qapp.processEvents()
    try:
        cap = tile._cap
        assert cap.text() != _LONG_NAME, "긴 파일명이 그대로다(줄이지 않았다)"
        assert "…" in cap.text(), f"말줄임표가 없다: {cap.text()!r}"
        # 가운데를 줄인다 — 앞뒤가 모두 남아야 한다.
        assert cap.text().startswith(_LONG_NAME[:4])
        assert cap.text().endswith(_LONG_NAME[-4:])
        assert _short(cap) <= 0, f"줄였는데도 넘친다: {_short(cap):+d}px"
        assert cap.toolTip() == _LONG_NAME, "전체 이름은 툴팁으로 남아야 한다"
    finally:
        tile.close()


def test_both_tile_captions_use_the_same_elide_mode(styled_qapp):
    """같은 내용(파일명)을 두 화면이 **같은 방식**으로 줄인다."""
    from pathlib import Path

    from aoi_verification.app.ui.widgets import (thumb_grid,
                                                 unmatched_review_dialog)
    for mod in (thumb_grid, unmatched_review_dialog):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "ElideMiddle" in src, f"{mod.__name__} 이 가운데 줄임을 쓰지 않는다"
