"""2단계 매칭 화면에는 **사용자가 누를 것이 없어야** 한다.

배경(실제 사고): 선계산이 끝나면 `_on_precompute_finished` 가 오버레이를 내리고
곧바로 자동 매치 사슬을 시작한다.  그런데 그 뒤로는 `set_progress` 만 부르는데,
`LoadingOverlay.set_progress` 는 **숨은 오버레이를 다시 띄우지 않는다.**

그래서 자동 매치가 도는 대부분의 시간 동안 화면이 무방비다 —
실측(ref 400장, 좌표 모드): 오버레이가 **359ms** 에 사라지고 남은 **356장(89%)** 이
덮개 없이 처리됐다.  사진이 많을수록 노출 구간이 길어진다.

그 사이 [매칭 없음] 이나 `N` 을 누르면 처리 중이던 기준 사진이 조용히 미탐으로
확정돼 **엑셀에 사실이 아닌 미탐이 남는다.**  사용자는 자기가 무엇을 바꿨는지도
모른다 — 이 저장소가 가장 비싸게 치는 오류다.

**누를 이유가 없다.**  `AutomationLevel.AUTO_MODES` 는 `{USER_SELECT, AUTO_ALL}`
이라 `is_auto()` 가 모든 자동화 수준에서 True 이고, 2단계 매칭은 언제나 자동이다
(수동 스트리밍 경로는 도달하지 않는다).  그래서 오버레이를 다시 띄우는 대신
**누를 수 있는 것 자체를 없앴다** — 더 확실한 방어다.

여기서 세우는 계약: 이 화면에 '매칭 없음' 을 확정하는 **사람 경로가 없다.**
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QShortcut                                  # noqa: E402
from PyQt6.QtWidgets import QPushButton                            # noqa: E402

from aoi_verification.app.ui.pages.match_page import MatchPage     # noqa: E402
from aoi_verification.app.utils.prefs import AutomationLevel       # noqa: E402


def test_every_automation_level_is_automatic() -> None:
    """이 제거의 전제 — 수동 모드가 되살아나면 이 테스트가 먼저 깨진다."""
    assert AutomationLevel.AUTO_MODES == frozenset(
        {AutomationLevel.USER_SELECT, AutomationLevel.AUTO_ALL})
    for level in (AutomationLevel.USER_SELECT, AutomationLevel.AUTO_ALL):
        assert AutomationLevel.is_auto(level), (
            f"{level} 이 더 이상 자동이 아니다 — 수동 모드가 생겼다면 "
            "[매칭 없음] 버튼과 N 단축키를 그 모드에서만 되살려야 한다")


def test_no_match_button_is_gone(styled_qapp):
    page = MatchPage()
    assert not hasattr(page, "no_match_btn"), (
        "[매칭 없음] 버튼이 되살아났다 — 자동 매치가 도는 동안 눌리면 "
        "사실이 아닌 미탐이 엑셀에 남는다")
    labels = [b.text() for b in page.findChildren(QPushButton)]
    assert not any("매칭 없음" in (t or "") for t in labels), (
        f"화면에 '매칭 없음' 버튼이 있다: {labels}")
    page.deleteLater()


def test_the_n_shortcut_is_gone(styled_qapp):
    """버튼만 지우고 단축키를 남기면 **보이지 않는 손잡이**가 되어 더 나쁘다."""
    page = MatchPage()
    keys = [s.key().toString() for s in page.findChildren(QShortcut)]
    assert "N" not in keys, (
        f"'N' 단축키가 살아 있다 — 화면에 아무것도 없는데 키 하나로 "
        f"미탐이 확정된다 (지금 걸린 단축키: {keys})")
    page.deleteLater()


def test_undo_shortcut_is_kept(styled_qapp):
    """대조군 — 되돌리기(Ctrl+Z)는 그대로 둔다(사용자가 지시한 범위가 아니다)."""
    page = MatchPage()
    keys = [s.key().toString() for s in page.findChildren(QShortcut)]
    assert "Ctrl+Z" in keys, f"되돌리기 단축키까지 지웠다: {keys}"
    page.deleteLater()


def test_the_program_path_still_confirms_no_match(styled_qapp, tmp_path):
    """자동 경로는 그대로 — 후보가 없는 기준 사진은 여전히 미탐으로 확정된다."""
    from aoi_verification.app.models.slot import ImageItem

    page = MatchPage()
    items = []
    for i in range(2):
        p = tmp_path / f"r{i}.jpg"
        p.write_bytes(b"")
        items.append(ImageItem(slot="S1", path=p, side="ref"))
    page.load_state(queue=items, val_pool_by_slot={"S1": []},
                    threshold=0.5, auto_mode=True)
    styled_qapp.processEvents()

    # `_confirm_no_match` 의 계약: `_current` 는 큐의 머리다(`queue.pop(0)` 를 한다).
    page._state.queue[:] = list(items)
    page._current = items[0]
    page._confirm_no_match()                 # 기본값이 이제 user=False 다
    assert items[0] in page._state.no_match["S1"], (
        "프로그램 경로까지 막혔다 — 후보 0개인 기준 사진을 미탐으로 확정하지 못한다")
    page.deleteLater()
