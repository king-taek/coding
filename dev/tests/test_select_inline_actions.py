"""Stage 1 좌측 패널 — 인라인 선택이 **실제로 쓰이는지** 못 박는다.

배경: 타일 클릭 선택은 오랫동안 배선이 끊겨 있었다.  선택 테두리는 생기는데
`inline_changed` 를 받는 곳이 0곳이고 `is_inline_selected()` 를 읽는 프로덕션 코드도
없어서, 고른 사진으로 할 수 있는 일이 하나도 없었다(죽은 기능).  이제 선택이 1장
이상이면 헤더에 일괄 액션이 뜨고, 그 액션이 기존 일괄 처리 경로로 들어간다.

함께 지키는 것: 결정 1건마다 슬롯 타일을 전부 다시 만들지 않는 단건 제거 경로(P-17).
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app.models.slot import ImageItem            # noqa: E402
from aoi_verification.app.ui.pages.select_page import SelectPage   # noqa: E402


def _items(slot: str, n: int, tmp_path) -> list[ImageItem]:
    out = []
    for i in range(n):
        p = tmp_path / f"{slot}_{i:03d}.jpg"
        p.write_bytes(b"")
        out.append(ImageItem(slot=slot, path=p, side="ref"))
    return out


def _page(styled_qapp, tmp_path, n=6):
    page = SelectPage()
    page.load_state(list(_items("S1", n, tmp_path)))
    page.show()          # `_decide` 는 보이지 않는 페이지에서 무시된다(단축키 오발 방지)
    return page


def test_inline_buttons_appear_only_when_something_is_selected(styled_qapp, tmp_path):
    page = _page(styled_qapp, tmp_path)
    panel = page.left_panel
    assert panel._inline_buttons, "좌측 패널에 인라인 일괄 액션 버튼이 없다"
    assert all(not b.isVisible() for b in panel._inline_buttons)

    sec = next(iter(panel._sections.values()))
    sec.grid._tiles[0].set_inline_selected(True)
    sec.grid._on_sel_toggle(sec.grid._tiles[0].entry, True)   # 타일이 내는 신호
    assert len(panel.inline_selected_items()) == 1
    assert any("1" in b.text() for b in panel._inline_buttons), "선택 개수가 라벨에 없다"
    page.deleteLater()


def test_inline_buttons_are_wide_enough_to_read(styled_qapp, tmp_path):
    """배선만 보고 **크기를 안 봤더니** 라벨이 잘렸다(실화면 검증에서 발견).

    좌측 패널은 폭이 330px 남짓이라 제목 + 액션 2개 + [선택 모드] 를 한 줄에
    넣으면 서로를 밀어낸다 — 실측 필요 113px 에 실제 71px 이 배정돼
    "선택 1 장 검증" 이 "1 장" 으로, "선택 모드" 가 "택 모" 로 잘렸다.
    액션은 제목 줄이 아니라 **그 아래 제 줄**에, 세로로 쌓아 둔다.

    ⚠ 창 크기와 **선택 장수**를 함께 흔들어야 한다.  1600x950·한 자리에서만 재면
    통과하는데 1280x800·두 자리에서 다시 잘린다(실측 필요 122px / 실제 118px) —
    실제로 그렇게 한 번 놓쳤다.
    """
    for w_px, h_px in ((1280, 800), (1600, 950)):
        page = _page(styled_qapp, tmp_path, n=30)
        page.resize(w_px, h_px)
        panel = page.left_panel
        sec = next(iter(panel._sections.values()))
        styled_qapp.processEvents()

        for n in (1, 12, 29):          # 한 자리 · 두 자리 · 거의 전량
            for t in sec.grid._tiles:
                t.set_inline_selected(False)
            for t in sec.grid._tiles[:n]:
                t.set_inline_selected(True)
            sec.grid._on_sel_toggle(sec.grid._tiles[0].entry, True)
            styled_qapp.processEvents()

            for b in panel._inline_buttons:
                assert b.width() >= b.sizeHint().width(), (
                    f"{w_px}x{h_px} 선택 {n}장 — {b.text()!r} 이 잘린다 "
                    f"(필요 {b.sizeHint().width()}px, 실제 {b.width()}px)")
            # 같은 줄을 나눠 쓰던 [선택 모드] 도 온전해야 한다.
            sb = panel._select_btn
            assert sb.width() >= sb.sizeHint().width(), (
                f"{w_px}x{h_px} — [선택 모드] 가 잘린다 "
                f"(필요 {sb.sizeHint().width()}px, 실제 {sb.width()}px)")
        page.deleteLater()


def test_inline_row_collapses_when_nothing_is_selected(styled_qapp, tmp_path):
    """선택이 없으면 그 줄은 자리를 차지하지 않는다 — 빈 띠가 남으면 안 된다."""
    page = _page(styled_qapp, tmp_path)
    page.resize(1600, 950)
    styled_qapp.processEvents()
    panel = page.left_panel
    assert all(not b.isVisible() for b in panel._inline_buttons)
    assert panel._inline_row.sizeHint().height() == 0, \
        "선택이 없는데 일괄 액션 줄이 높이를 차지한다"
    page.deleteLater()


def test_inline_action_routes_into_the_batch_decision_path(styled_qapp, tmp_path):
    """선택 → [선택 n장 검증] → 기존 일괄 처리로 들어가 상태가 실제로 바뀐다."""
    page = _page(styled_qapp, tmp_path)
    panel = page.left_panel
    sec = next(iter(panel._sections.values()))
    picked = [t.entry.item for t in sec.grid._tiles[:2]]
    for t in sec.grid._tiles[:2]:
        t.set_inline_selected(True)

    panel._fire_inline_action("batch_verify")

    moved = [it for v in page._state.targets.values() for it in v]
    assert set(moved) == set(picked), "고른 사진이 검증 대상으로 가지 않았다"
    assert panel.inline_selected_items() == [], "액션 뒤에 선택이 남아 있다"
    page.deleteLater()


def test_single_decision_does_not_rebuild_the_whole_slot(styled_qapp, tmp_path):
    """결정 1건에 타일을 전부 다시 만들면 300장 슬롯에서 매번 수십 ms 가 샌다."""
    page = _page(styled_qapp, tmp_path, n=6)
    sec = next(iter(page.left_panel._sections.values()))
    # 결정될 사진(현재)과 다음 현재를 제외한, 계속 남아 있을 타일의 정체성을 기억한다.
    survivors_before = {id(t) for t in sec.grid._tiles[2:]}
    page._decide("verify")
    sec_after = next(iter(page.left_panel._sections.values()))
    survivors_after = {id(t) for t in sec_after.grid._tiles}
    assert survivors_before & survivors_after, "타일이 통째로 재생성됐다(단건 제거 경로 미사용)"
    page.deleteLater()


def test_empty_target_panel_explains_itself(styled_qapp, tmp_path):
    """우측 '검증 대상' 이 처음엔 완전한 공백이라 무엇을 담는 칸인지 알 수 없었다."""
    from PyQt6.QtWidgets import QLabel
    page = _page(styled_qapp, tmp_path)
    hints = [w for w in page.right_panel.findChildren(QLabel)
             if w.property("role") == "emptyHint" and w.parent() is not None]
    assert len(hints) == 1, f"빈 상태 안내가 정확히 하나 있어야 한다 (found {len(hints)})"
    page._decide("verify")            # 한 장 보내면 안내는 사라진다
    hints = [w for w in page.right_panel.findChildren(QLabel)
             if w.property("role") == "emptyHint" and w.parent() is not None]
    assert hints == []
    page.deleteLater()


def test_back_to_setup_is_reachable_from_the_journey_rail(styled_qapp, tmp_path):
    """폴더를 잘못 고르고 들어왔을 때 돌아갈 길.

    ★ 화면 안의 [← 설정으로] 버튼은 없앴다(구조개편 1안-A) — 뒤로가기의 의미가
    화면마다 달랐던 것이 문제였고, 이제 창 상단 여정 레일이 그 일을 통일해서
    맡는다.  레일은 `request_back_to_setup()` 을 부르고, **무엇이 사라지는지**를
    아는 확인은 계속 이 화면에 남아 있다."""
    page = _page(styled_qapp, tmp_path)
    assert not hasattr(page, "btn_back_to_setup"), \
        "화면 안 뒤로가기 버튼이 되살아났다 — 복귀 경로는 여정 레일 하나다"
    assert callable(page.request_back_to_setup)
    seen = []
    page.cancelled.connect(lambda: seen.append(True))
    page.request_back_to_setup()            # 아직 결정 0 건 → 확인창 없이 즉시
    assert seen == [True]
    page.deleteLater()
