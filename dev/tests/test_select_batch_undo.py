"""Stage 1 일괄 처리도 되돌리기(Z) 에 기록되어야 한다 (U-02).

배경(실제 사고): U-02 로 왼쪽 패널의 타일 클릭 → `[선택 N 장 검증/제외]` 를 살리면서
액션 자체는 `_on_batch_action` 에 이어 붙였는데, 그 경로가 **`_state.history` 에
아무것도 남기지 않았다.**  `_decide`(가운데에서 한 장씩)만 기록을 남긴다.

그래서 `Z` 를 누르면 방금 일괄 처리한 사진이 돌아오는 대신 **그 이전의 한 장짜리
결정이 대신 취소**됐다 — 사용자가 손대지도 않은 사진이 조용히 큐로 되돌아온다.
실측: 단건 '검증' 1건 → 3장 일괄 제외 → `Z` 1회 →
``excluded 3 그대로 / targets 1 → 0``.

되돌릴 수 없는 것보다 **엉뚱한 것이 되돌아가는 것**이 나쁘다.  사용자는 방금 보낸
3장이 돌아왔다고 믿고 넘어가는데, 실제로는 다른 사진의 결정이 사라져 있다.

기록 단위는 `_end_selection_now` 와 같은 **장당 1건**이다 — 화면과 설명서가 약속한
"`Z` 는 직전 한 장" 을 그대로 지킨다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app.ui.pages.select_page import SelectPage   # noqa: E402


def _page(styled_qapp, tmp_path: Path, n: int = 6):
    from aoi_verification.app.models.slot import ImageItem

    page = SelectPage()
    items = []
    for i in range(n):
        p = tmp_path / f"s1_{i}.jpg"
        p.write_bytes(b"")
        items.append(ImageItem(slot="S1", path=p, side="ref"))
    page.load_state(list(items))
    # ★ `_decide`·`_undo` 는 `isVisible()` 가드를 갖는다(다른 페이지가 보일 때
    #   →/←/Z 가 여기로 새는 것을 막는 장치).  띄우지 않으면 아무 일도 안 일어나
    #   테스트가 조용히 통과한다.
    page.show()
    styled_qapp.processEvents()
    assert page.isVisible(), "전제가 깨졌다 — 페이지가 보이지 않으면 키 경로가 죽는다"
    return page, items


def _pools(page):
    st = page._state
    return (sum(len(v) for v in st.targets.values()),
            sum(len(v) for v in st.excluded.values()),
            len(st.queue))


def test_batch_exclude_is_undone_by_z(styled_qapp, tmp_path):
    """일괄 제외한 사진이 `Z` 로 큐에 돌아온다."""
    page, items = _page(styled_qapp, tmp_path)
    page._on_batch_action(page.PANEL_LEFT, "batch_exclude", items[:3])
    styled_qapp.processEvents()
    assert _pools(page)[1] == 3, "전제가 깨졌다 — 일괄 제외가 반영되지 않았다"

    page._undo()
    styled_qapp.processEvents()
    t, e, q = _pools(page)
    assert e == 2, f"일괄 제외가 되돌아가지 않았다 (excluded={e})"
    assert q == 4, f"되돌린 사진이 큐로 돌아오지 않았다 (queue={q})"
    page.deleteLater()


def test_z_does_not_undo_an_untouched_earlier_decision(styled_qapp, tmp_path):
    """가장 나쁜 증상 — 일괄 처리 뒤 `Z` 가 **손대지 않은 이전 결정**을 취소한다."""
    page, items = _page(styled_qapp, tmp_path)

    page._current = items[0]                    # 가운데에서 한 장 '검증'
    page._decide("verify")
    styled_qapp.processEvents()
    assert _pools(page)[0] == 1

    page._on_batch_action(page.PANEL_LEFT, "batch_exclude", items[1:4])
    styled_qapp.processEvents()

    page._undo()
    styled_qapp.processEvents()
    t, e, q = _pools(page)
    assert t == 1, (
        "`Z` 가 일괄 처리를 건너뛰고 **이전의 한 장짜리 검증 결정**을 취소했다 — "
        f"사용자가 손대지 않은 사진이 사라진다 (targets {t}, 기대 1)")
    assert e == 2, f"되돌아간 것은 방금 일괄 제외한 사진이어야 한다 (excluded={e})"
    page.deleteLater()


def test_batch_verify_is_recorded_too(styled_qapp, tmp_path):
    page, items = _page(styled_qapp, tmp_path)
    page._on_batch_action(page.PANEL_LEFT, "batch_verify", items[:2])
    styled_qapp.processEvents()
    assert _pools(page)[0] == 2

    page._undo()
    page._undo()
    styled_qapp.processEvents()
    t, e, q = _pools(page)
    assert (t, e, q) == (0, 0, 6), (
        f"일괄 검증 2장이 `Z` 두 번으로 전부 돌아와야 한다 — 지금 {(t, e, q)}")
    page.deleteLater()


def test_history_is_one_entry_per_photo(styled_qapp, tmp_path):
    """기록 단위 계약 — `_end_selection_now` 와 같은 장당 1건."""
    page, items = _page(styled_qapp, tmp_path)
    page._on_batch_action(page.PANEL_LEFT, "batch_exclude", items[:3])
    assert len(page._state.history) == 3, (
        f"장당 1건이어야 한다 — 지금 {len(page._state.history)}건. "
        "묶어서 1건으로 남기면 `Z` 가 '직전 한 장' 이라는 화면·설명서의 약속을 깬다")
    assert all(a == "exclude" for a, _ in page._state.history)
    page.deleteLater()


def test_right_panel_moves_stay_out_of_history(styled_qapp, tmp_path):
    """오른쪽/제외 패널의 이동은 **결정이 아니라 정정**이라 기록하지 않는다.

    `_undo` 의 계약은 '풀에서 빼 큐로 되돌린다' 인데, 이미 큐를 떠난 사진을 옮기는
    정정에는 그 되돌림이 성립하지 않는다(같은 사진이 큐에 두 번 들어간다).
    """
    page, items = _page(styled_qapp, tmp_path)
    page._on_batch_action(page.PANEL_LEFT, "batch_verify", items[:2])
    before = len(page._state.history)

    page._on_batch_action(page.PANEL_RIGHT, "to_exclude", items[:1])
    styled_qapp.processEvents()
    assert len(page._state.history) == before, (
        "정정 이동까지 기록하면 `Z` 가 같은 사진을 큐에 두 번 넣는다")
    page.deleteLater()
