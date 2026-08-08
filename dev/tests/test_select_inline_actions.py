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


def test_back_to_setup_exists_and_is_symmetric_with_stage2(styled_qapp, tmp_path):
    """폴더를 잘못 고르고 들어왔을 때 돌아갈 길 — Stage 2 와 같은 자리."""
    page = _page(styled_qapp, tmp_path)
    assert hasattr(page, "btn_back_to_setup")
    seen = []
    page.cancelled.connect(lambda: seen.append(True))
    page.btn_back_to_setup.click()          # 아직 결정 0 건 → 확인창 없이 즉시
    assert seen == [True]
    page.deleteLater()
