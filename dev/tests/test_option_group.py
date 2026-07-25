"""OptionGroup — 배타 선택 타일, 키보드 이동, 열 재배치, QSS 전용 선택 스타일."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt                                    # noqa: E402
from PyQt6.QtWidgets import (QApplication, QGridLayout, QScrollArea,  # noqa: E402
                             QVBoxLayout, QWidget)

from aoi_verification.app.ui import theme                      # noqa: E402
from aoi_verification.app.ui.widgets.option_group import (      # noqa: E402
    OptionGroup, reflow_into_grid)

_QSS = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui"
        / "style.qss").read_text(encoding="utf-8")

OPTS = [("a", "가"), ("b", "나"), ("c", "다")]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_exclusive_selection(qapp):
    g = OptionGroup(OPTS)
    assert g.current_key() == "a"                  # 첫 항목이 기본 선택
    g.button("b").click()
    assert g.current_key() == "b"
    assert g.button("a").isChecked() is False      # 배타
    g.deleteLater()


def test_set_current_key_emit_contract(qapp):
    g = OptionGroup(OPTS)
    seen: list[str] = []
    g.selection_changed.connect(seen.append)
    g.set_current_key("c")                         # 기본 emit=False → 복원용
    assert g.current_key() == "c" and seen == []
    g.set_current_key("b", emit=True)
    assert g.current_key() == "b" and seen == ["b"]
    g.deleteLater()


def test_click_emits_key(qapp):
    g = OptionGroup(OPTS)
    seen: list[str] = []
    g.selection_changed.connect(seen.append)
    g.button("c").click()
    assert seen == ["c"]
    g.deleteLater()


def test_arrow_keys_move_selection_when_opted_in(qapp):
    """부수효과가 없는 그룹에서만 방향키가 선택까지 확정한다(라디오 감각)."""
    from PyQt6.QtTest import QTest
    g = OptionGroup(OPTS, activate_on_arrow=True)
    g.show()
    for _ in range(6):
        qapp.processEvents()
    # ★ 키는 **포커스된 타일**에 보낸다 — 프로덕션에서 키가 실제로 도착하는 곳이다.
    #   컨테이너에 보내면 프로덕션에서 실행되지 않는 경로를 검증하게 된다.
    g.button("a").setFocus(Qt.FocusReason.TabFocusReason)
    for _ in range(4):
        qapp.processEvents()
    QTest.keyClick(g.button("a"), Qt.Key.Key_Right)
    assert g.current_key() == "b"
    QTest.keyClick(g.button("b"), Qt.Key.Key_Down)     # ↓ 도 다음 항목
    assert g.current_key() == "c"
    QTest.keyClick(g.button("c"), Qt.Key.Key_Right)    # 끝에서 넘어가지 않음(클램프)
    assert g.current_key() == "c"
    QTest.keyClick(g.button("c"), Qt.Key.Key_Left)
    assert g.current_key() == "b"
    g.deleteLater()


def test_arrow_keys_do_not_commit_by_default(qapp):
    """★ 기본은 '포커스만 이동'이다 — 이 위젯의 선택은 부수효과를 가진다.

    배치·색 모드는 페이지 재생성이고 진행 범위 'subset' 은 **모달**을 띄운다.
    실측으로 방향키 한 번에 경고창이 떠 하네스가 블로킹된 적이 있다.
    ★ 키는 포커스된 타일에 보낸다 — `QAbstractButton` 이 방향키를 스스로 처리해
    `click()` 까지 호출할 수 있어서, 컨테이너 경로만 검증하면 구멍이 남는다."""
    from PyQt6.QtTest import QTest
    fired = []
    g = OptionGroup(OPTS)                       # activate_on_arrow 기본 False
    g.selection_changed.connect(fired.append)
    g.show()
    for _ in range(6):
        qapp.processEvents()
    g.button("a").setFocus(Qt.FocusReason.TabFocusReason)
    for _ in range(4):
        qapp.processEvents()
    QTest.keyClick(g.button("a"), Qt.Key.Key_Right)
    for _ in range(4):
        qapp.processEvents()
    assert g.current_key() == "a", "방향키가 선택을 커밋했다"
    assert not fired, f"방향키가 selection_changed 를 발화했다: {fired}"
    # 포커스는 다음 타일로 옮겨져 있어야 한다(탐색은 되어야 한다).
    assert g.button("b").hasFocus(), "포커스가 이동하지 않았다"
    # Space 로 확정.
    QTest.keyClick(g.button("b"), Qt.Key.Key_Space)
    for _ in range(4):
        qapp.processEvents()
    assert g.current_key() == "b", "Space 로 확정되지 않았다"
    g.deleteLater()


def test_set_option_label_puts_state_in_control(qapp):
    """상태는 옆 라벨이 아니라 컨트롤 자신이 말한다."""
    g = OptionGroup([("all", "모든 슬롯"), ("subset", "일부 슬롯")])
    g.set_option_label("subset", "일부 슬롯 (12/40)")
    assert g.button("subset").text() == "일부 슬롯 (12/40)"
    assert g.button("subset").accessibleName() == "일부 슬롯 (12/40)"
    g.deleteLater()


def test_reflow_into_grid_column_count(qapp):
    host = QWidget()
    grid = QGridLayout(host)
    grid.setSpacing(8)
    widgets = [QWidget(host) for _ in range(4)]
    assert reflow_into_grid(grid, widgets, 320, 200) == 1      # 좁으면 1열
    assert reflow_into_grid(grid, widgets, 1200, 200) > 1      # 넓으면 여러 열
    assert reflow_into_grid(grid, widgets, 1200, 200) <= 4     # 항목 수를 넘지 않음
    assert reflow_into_grid(grid, [], 1200, 200) == 0          # 빈 목록 안전
    host.deleteLater()


def test_reflow_preserves_widgets_not_recreated(qapp):
    """take/re-add 만 한다 — 위젯 동일성(선택 상태·연결)이 살아 있어야 한다."""
    g = OptionGroup(OPTS)
    before = g.button("b")
    g.button("b").click()
    g.resize(320, 200)
    qapp.processEvents()
    g.resize(1200, 200)
    qapp.processEvents()
    assert g.button("b") is before
    assert g.current_key() == "b"
    g.deleteLater()


def test_selected_state_is_qss_only_inside_scrollarea(qapp):
    """회귀: QScrollArea 안에서도 선택이 유지되고, 인라인 스타일시트는 없어야 한다.

    맨 선언 뷰포트 스타일시트가 자식의 :checked 배경을 덮어써 '빈 박스'로 렌더된 적이
    있다(도면 확정 커밋). 스코프된 형태를 쓰고, 선택 틴트는 QSS 만 담당한다."""
    theme_qss = theme.render_qss(_QSS)
    qapp.setStyleSheet(theme_qss)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet(
        "QScrollArea { background: transparent; border: none; }"
        "QScrollArea > QWidget#qt_scrollarea_viewport { background: transparent; }"
    )
    host = QWidget()
    lay = QVBoxLayout(host)
    g = OptionGroup(OPTS)
    lay.addWidget(g)
    scroll.setWidget(host)
    scroll.resize(600, 200)
    scroll.show()
    for _ in range(6):
        qapp.processEvents()
    g.button("b").click()
    assert g.button("b").isChecked() is True
    for key in ("a", "b", "c"):
        assert g.button(key).styleSheet() == "", f"{key}: 인라인 스타일 금지"
    scroll.deleteLater()


def test_qss_defines_option_checked_rule():
    """선택 상태 규칙이 QSS 에 살아 있어야 한다(삭제되면 타일이 안 칠해진다)."""
    out = theme.render_qss(_QSS)
    assert 'QPushButton[role="option"]:checked' in out
    assert 'QPushButton[role="option"]:disabled' in out
    assert "$" not in out


def test_no_old_neon_palette_in_option_group():
    src = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui"
           / "widgets" / "option_group.py").read_text(encoding="utf-8")
    assert "57, 255, 20" not in src and "#39FF14" not in src.upper()
