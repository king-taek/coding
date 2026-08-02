"""KLA 질문 팝업 — 네 선택지를 **대등하게** 보여준다.

사용자 지적: "기준만 파란색 버튼인데 좀 이상합니다."  기준/검증/둘 다/KLA 아님 중
정답이 정해져 있지 않은데 하나만 주 액션(파란 버튼)으로 서 있었다.  어느 하나가
정답이 아니면 어느 하나만 강조해서도 안 된다.

집안 관습(CLAUDE.md): 클릭 대상은 크고 명확하게 — 타일 전체가 클릭영역.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import inspect

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt                                    # noqa: E402
from PyQt6.QtWidgets import QGridLayout                        # noqa: E402

from aoi_verification.app import i18n                          # noqa: E402
from aoi_verification.app.ui import main_window as mw          # noqa: E402
from aoi_verification.app.ui.widgets import sheet_host as sh   # noqa: E402


_OPTIONS = [("ref", i18n.KO.KLA_SIDE_REF, "option"),
            ("val", i18n.KO.KLA_SIDE_VAL, "option"),
            ("both", i18n.KO.KLA_SIDE_BOTH, "option"),
            (None, i18n.KO.KLA_SIDE_NONE, "option")]


def _sheet(styled_qapp, **kw):
    return sh._ChoiceSheet(i18n.KO.KLA_ASK_TITLE, i18n.KO.KLA_ASK_SIDE_BODY,
                           _OPTIONS, heading=i18n.KO.KLA_ASK_SIDE_HEADING, **kw)


def test_four_tiles_in_a_two_by_two_grid(styled_qapp):
    dlg = _sheet(styled_qapp, tiles=True)
    try:
        grids = dlg.findChildren(QGridLayout)
        assert grids, "타일 모드인데 격자가 없다"
        grid = grids[0]
        assert grid.count() == 4
        positions = {grid.getItemPosition(i)[:2] for i in range(grid.count())}
        assert positions == {(0, 0), (0, 1), (1, 0), (1, 1)}, \
            f"2×2 가 아니다: {sorted(positions)}"
    finally:
        dlg.deleteLater()


def test_all_four_look_the_same(styled_qapp):
    """★ 이 팝업의 핵심 — 넷 중 어느 하나도 주 액션으로 서지 않는다."""
    dlg = _sheet(styled_qapp, tiles=True)
    try:
        roles = {k: b.property("role") for k, b in dlg._buttons.items()}
        assert set(roles.values()) == {"option"}, f"등급이 갈렸다: {roles}"
        assert not any(b.isDefault() for b in dlg._buttons.values()), \
            "기본 버튼이 지정돼 하나만 강조된다"
        assert not any(b.isChecked() for b in dlg._buttons.values()), \
            "미리 선택된 타일이 있다 — 그것이 정답처럼 보인다"
    finally:
        dlg.deleteLater()


def test_all_four_answers_are_offered(styled_qapp):
    """기준/검증/둘 다/KLA 아님 네 경우를 모두 유지한다(CLAUDE.md 규칙)."""
    dlg = _sheet(styled_qapp, tiles=True)
    try:
        assert set(dlg._buttons) == {"ref", "val", "both", None}
        labels = [b.text() for b in dlg._buttons.values()]
        assert i18n.KO.KLA_SIDE_NONE in labels
    finally:
        dlg.deleteLater()


def test_clicking_a_tile_answers_immediately(styled_qapp):
    """고르고 다시 [확인] 을 누르게 하면 한 번 물어볼 것을 두 번 묻는 셈이다."""
    dlg = _sheet(styled_qapp, tiles=True)
    try:
        dlg._buttons["val"].click()
        assert dlg.picked() == "val"
        assert dlg.result() == int(dlg.DialogCode.Accepted)
    finally:
        dlg.deleteLater()


def test_none_answer_round_trips(styled_qapp):
    """'KLA 아님' 은 키가 ``None`` 이라 '닫힘'과 헷갈리기 쉽다 — 실제로 눌러 본다."""
    dlg = _sheet(styled_qapp, tiles=True)
    try:
        dlg._buttons[None].click()
        assert dlg.picked() is None
        assert dlg.result() == int(dlg.DialogCode.Accepted)   # 닫은 게 아니라 고른 것
    finally:
        dlg.deleteLater()


def test_tiles_are_large_click_targets(styled_qapp):
    """작은 버튼이 아니라 큰 타일 — 손가락 커서까지(집안 관습)."""
    from aoi_verification.app.ui import theme

    dlg = _sheet(styled_qapp, tiles=True)
    try:
        for b in dlg._buttons.values():
            assert b.minimumHeight() >= theme.PROFILE.control_h_lg
            assert b.cursor().shape() == Qt.CursorShape.PointingHandCursor
    finally:
        dlg.deleteLater()


def test_button_row_mode_still_works(styled_qapp):
    """다른 호출부(OpenVINO 설치 안내)는 주 액션이 **있는** 질문이라 그대로 둔다."""
    dlg = sh._ChoiceSheet("t", "b",
                          [("never", "안 함", "danger"),
                           ("later", "다음에", "ghost"),
                           ("install", "설치", "primary")],
                          default="install")
    try:
        assert not dlg.findChildren(QGridLayout)
        assert dlg._buttons["install"].isDefault()
        assert dlg._buttons["install"].property("role") == "primary"
    finally:
        dlg.deleteLater()


def test_main_window_asks_with_tiles():
    """호출부가 실제로 타일 모드를 쓰는지 — 시트만 고쳐 두면 화면은 그대로다."""
    src = inspect.getsource(mw.MainWindow._ask_kla_side)
    assert "tiles=True" in src
    assert "primary" not in src, "선택지 하나가 다시 주 액션이 됐다"
    assert 'default="ref"' not in src, "기본값이 다시 생겨 하나만 강조된다"
