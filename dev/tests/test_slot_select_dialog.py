"""'진행할 슬롯 선택' 다이얼로그 — 검색 + 밀집 그리드 다중 선택.

이 파일이 지키는 계약은 재설계에서 실제로 고친 결함들이다(자세한 배경은 위젯 docstring):

- 선택 상태는 **QSS 가 칠한다** — 죽은 네온 팔레트 인라인 하드코딩 금지.
- 포커스가 **렌더된다** — StrongFocus 만 켜고 안 그리면 2.4.7 실패다.
- 토글은 **release-inside** 에서 — 터치 스크롤이 선택을 바꾸지 않게.
- 열 수는 ``reflow_into_grid`` 가 계산한다 — off-by-one 을 두 곳에 복제하지 않는다.
- **0개 선택으로 확인 불가** — 호출부가 빈 집합을 '전체 진행'으로 정규화하므로,
  전부 해제하고 확인하면 의도와 정반대로 모든 슬롯이 돌아간다.
- 검색은 **보기**다 — 숨은 타일의 선택을 지우지 않는다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import inspect
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt                            # noqa: E402
from PyQt6.QtWidgets import QApplication               # noqa: E402

from aoi_verification.app.ui import theme              # noqa: E402
from aoi_verification.app.ui.widgets import slot_select_dialog as ssd  # noqa: E402
from aoi_verification.app.ui.widgets.slot_select_dialog import (  # noqa: E402
    SlotSelectDialog)

_NAMES = ["S01", "S02", "S03", "S04", "S05"]
_MANY = [f"PI_Opening_{c}{i}" for c in "AB" for i in range(1, 13)]
_QSS = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui"
        / "style.qss").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _shown(qapp, names=_NAMES, w=620, h=560):
    """★ `resize()` 는 show() **뒤에** 걸어야 실제로 먹는다.

    이 다이얼로그는 `enable_window_controls(maximized=False)` 로 스스로 크기를 정한
    뒤(`_size_to_content`) 열린다.  이전 구현은 `maximized=True`(기본값) 였고, 다음
    이벤트 tick 의 showMaximized() 가 크기 요청을 덮어써 `resize(560, 600)` 이 **죽은
    코드**였다 — 그걸 모르고 짠 테스트는 '요청한 폭' 을 검사하며 통과했을 것이다.
    """
    qapp.setStyleSheet(theme.render_qss(_QSS))
    dlg = SlotSelectDialog(names)
    dlg.show()
    dlg.resize(w, h)
    for _ in range(16):
        qapp.processEvents()
    return dlg


# ── 선택 로직 ────────────────────────────────────────────────────────────
def test_preselected_reflected_in_selected(qapp):
    dlg = SlotSelectDialog(_NAMES, preselected={"S02", "S04"})
    assert dlg.selected == {"S02", "S04"}


def test_default_preselects_all(qapp):
    """preselected 미지정 → 전체 선택으로 시작(기존 동작 보존)."""
    dlg = SlotSelectDialog(_NAMES)
    assert dlg.selected == set(_NAMES)


def test_tile_toggle_updates_selection(qapp):
    dlg = SlotSelectDialog(_NAMES, preselected=set())
    assert dlg.selected == set()
    dlg._tiles["S03"].click()              # 좌클릭(release-inside)과 동일
    assert dlg.selected == {"S03"}
    dlg._tiles["S03"].click()
    assert dlg.selected == set()


def test_tiles_are_not_mutually_exclusive(qapp):
    """★ 부모를 공유하는 checkable QPushButton 은 Qt 가 라디오처럼 묶는다.

    autoExclusive 를 끄지 않으면 두 번째 선택이 첫 번째를 **해제**해 다중 선택이
    조용히 단일 선택이 된다."""
    dlg = SlotSelectDialog(_NAMES, preselected=set())
    dlg._tiles["S01"].click()
    dlg._tiles["S02"].click()
    assert dlg.selected == {"S01", "S02"}, "다중 선택이 단일 선택으로 퇴화했다"


def test_set_all(qapp):
    dlg = SlotSelectDialog(_NAMES, preselected={"S01"})
    dlg._set_all(True)
    assert dlg.selected == set(_NAMES)
    dlg._set_all(False)
    assert dlg.selected == set()


def test_head_states_what_will_run(qapp):
    """머리글이 '무슨 일이 일어나는지' 를 큰 글자로 말한다(이전엔 작은 '선택 2 / 5' 뿐)."""
    dlg = SlotSelectDialog(_NAMES, preselected={"S01", "S02"})
    assert "2" in dlg._head.text() and "5" in dlg._head.text()
    dlg._set_all(True)
    assert "모든 슬롯" in dlg._head.text()


# ── 0개 선택 차단 (호출부 정규화 함정) ──────────────────────────────────
def test_zero_selection_cannot_confirm(qapp):
    """전부 해제 후 확인은 막혀야 한다 — 안 막으면 **모든 슬롯**이 돌아간다.

    `setup_page._open_slot_select` 는 `if not chosen ... _reset_slot_selection()` 로
    빈 집합을 '전체 진행'으로 정규화한다.  즉 0개 확인은 조용히 정반대 결과를 낸다."""
    dlg = SlotSelectDialog(_NAMES, preselected=set())
    assert dlg._ok.isEnabled() is False
    assert dlg._blocked.text(), "왜 확인할 수 없는지 말하지 않는다"
    dlg._on_ok()                        # 단축키·Enter 로도 새지 않아야 한다
    assert dlg.accepted_ok is False
    dlg._tiles["S01"].click()
    assert dlg._ok.isEnabled() is True
    assert dlg._blocked.text() == ""


def test_caller_normalization_still_holds(qapp):
    """차단의 **근거**가 호출부에 그대로 있는지 확인한다.

    호출부가 언젠가 빈 집합을 다르게 다루기로 하면 이 차단의 이유도 달라진다 —
    근거를 테스트에 적어 두면 다음 사람이 둘을 함께 본다."""
    from aoi_verification.app.ui.pages import setup_page as sp
    src = inspect.getsource(sp.SetupPage._open_slot_select)
    assert "if not chosen" in src and "_reset_slot_selection" in src


# ── 검색(필터) ──────────────────────────────────────────────────────────
def test_search_filters_without_destroying_selection(qapp):
    """검색은 **보기**다.  숨은 타일의 선택이 풀리면 검색이 파괴적 동작이 된다."""
    dlg = _shown(qapp, _MANY)
    assert len(dlg.selected) == len(_MANY)
    dlg._search.setText("A1")
    for _ in range(6):
        qapp.processEvents()
    assert dlg._visible == ["PI_Opening_A1", "PI_Opening_A10",
                            "PI_Opening_A11", "PI_Opening_A12"]
    assert len(dlg.selected) == len(_MANY), "숨겼더니 선택이 풀렸다"
    dlg._search.setText("")
    for _ in range(6):
        qapp.processEvents()
    assert len(dlg._visible) == len(_MANY)


def test_select_visible_narrows_to_search_hits(qapp):
    """'검색 결과 선택' = 이것만 돌린다 — 나머지는 해제한다."""
    dlg = _shown(qapp, _MANY)
    dlg._search.setText("A1")
    for _ in range(6):
        qapp.processEvents()
    dlg._select_visible()
    assert dlg.selected == set(dlg._visible)


def test_empty_search_result_says_so(qapp):
    """빈 화면을 남기지 않는다 — 왜 아무것도 없는지 말한다."""
    dlg = _shown(qapp, _MANY)
    dlg._search.setText("존재하지않는슬롯")
    for _ in range(6):
        qapp.processEvents()
    assert dlg._visible == []
    assert dlg._empty.isVisible() and dlg._empty.text()
    # 검색 중일 때만 의미 있는 '검색 결과 선택' 은 결과가 없으면 숨는다.
    assert dlg._btn_visible.isVisible() is False


# ── 기하 · 타깃 · 좁은 창 ───────────────────────────────────────────────
def test_no_horizontal_scroll_and_columns_adapt(qapp):
    """열 수는 폭에 맞춰 줄어들고, 가로 스크롤은 어느 폭에서도 생기지 않는다."""
    dlg = _shown(qapp, _MANY, w=620, h=560)
    seen: list[tuple[int, int]] = []
    for w in (620, 520, 420, 380):
        dlg.resize(w, 480)
        for _ in range(12):
            qapp.processEvents()
        bar = dlg._scroll.horizontalScrollBar()
        assert bar.maximum() == 0, f"{w}px 에서 가로 스크롤 {bar.maximum()}px"
        seen.append((w, dlg._grid_cols()))
    cols = [c for _w, c in seen]
    assert cols == sorted(cols, reverse=True), f"열 수가 폭을 안 따른다: {seen}"
    assert cols[0] > cols[-1], f"좁혀도 열이 줄지 않았다: {seen}"


def test_dialog_fits_a_small_screen(qapp):
    """★ 창이 화면 가용 영역을 넘지 않는다.

    800×600 노트북에서 큰 화면 기준 크기를 그대로 쓰면 [확인]이 화면 밖으로 나간다.
    그리고 이 다이얼로그는 **최대화하지 않는다** — 짧은 이름 24개를 고르는 창이 27인치를
    채우면 여백만 늘고 눈이 멀리 이동한다(이전 구현은 항상 최대화됐다)."""
    from PyQt6.QtWidgets import QApplication as _QA
    dlg = SlotSelectDialog(_MANY)
    dlg.show()
    for _ in range(16):
        qapp.processEvents()
    g = (dlg.screen() or _QA.primaryScreen()).availableGeometry()
    assert dlg.width() <= g.width() and dlg.height() <= g.height(), \
        f"창 {dlg.width()}x{dlg.height()} > 화면 {g.width()}x{g.height()}"
    assert not dlg.isMaximized(), "슬롯 선택 창이 화면을 통째로 차지한다"


def test_tile_and_action_targets(qapp):
    dlg = _shown(qapp, _MANY)
    tile = dlg._tiles[_MANY[0]]
    assert tile.height() >= 24, f"타일 {tile.height()}px < 24px (2.5.8 AA)"
    assert tile.height() >= theme.PROFILE.target_min
    assert dlg._ok.height() >= theme.PROFILE.control_h_lg - 1, \
        f"[확인] {dlg._ok.height()}px"


def test_long_name_stays_readable(qapp):
    """긴 슬롯명은 **가운데** 생략 + 툴팁에 전체 — 앞/뒤 생략은 A1/A2 를 구분 못 한다."""
    long_name = "PI_Opening_Zone3_VeryLongRecipeName_A1"
    dlg = _shown(qapp, [long_name] + _NAMES, w=620)
    tile = dlg._tiles[long_name]
    assert tile.toolTip() == long_name
    text = tile.text()
    if text != long_name:                       # 폭이 좁아 줄였다면
        assert "…" in text
        assert text[0] == long_name[0] and text[-1] == long_name[-1], \
            f"가운데 생략이 아니다: {text!r}"


# ── 포커스 · 키보드 ─────────────────────────────────────────────────────
def test_focus_ring_actually_renders(qapp):
    """★ 포커스를 **렌더된 픽셀**로 증명한다.

    이전 타일은 StrongFocus 를 켜 두고 링을 그리지 않아, 키보드로 이동해도 지금 어디
    인지 알 수 없었다.  QSS 에 선언만 하는 것으로는 증명이 안 된다."""
    for mode in theme.color_mode_keys():
        theme.set_color_mode(mode)
        dlg = _shown(qapp, _NAMES)
        dlg.activateWindow()
        tile = dlg._tiles["S01"]
        tile.setFocus(Qt.FocusReason.TabFocusReason)
        for _ in range(10):
            qapp.processEvents()
        img = tile.grab().toImage()
        want = theme.COLORS["focus"].lstrip("#")
        wr, wg, wb = (int(want[i:i + 2], 16) for i in (0, 2, 4))
        hit = any(
            abs(img.pixelColor(x, y).red() - wr)
            + abs(img.pixelColor(x, y).green() - wg)
            + abs(img.pixelColor(x, y).blue() - wb) <= 24
            for y in range(img.height()) for x in range(img.width()))
        assert hit, f"{mode}: 포커스 링 픽셀이 렌더되지 않았다"
    theme.set_color_mode("light")


def test_arrow_keys_move_focus_without_toggling(qapp):
    """방향키는 **이동만** 한다 — 훑는 것이 선택이 되면 안 된다."""
    dlg = _shown(qapp, _MANY)
    dlg._set_all(False)
    first = dlg._tiles[dlg._visible[0]]
    first.setFocus(Qt.FocusReason.TabFocusReason)
    dlg._move_focus(first, Qt.Key.Key_Right)
    for _ in range(4):
        qapp.processEvents()
    assert dlg._tiles[dlg._visible[1]].hasFocus()
    assert dlg.selected == set(), "방향키가 선택을 바꿨다"


def test_down_from_search_enters_grid(qapp):
    """검색란에서 ↓ → 첫 타일.  타이핑 → 고르기가 끊기지 않는다."""
    from PyQt6.QtGui import QKeyEvent
    dlg = _shown(qapp, _MANY)
    dlg._search.setFocus()
    for _ in range(4):
        qapp.processEvents()
    dlg.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Down,
                                Qt.KeyboardModifier.NoModifier))
    for _ in range(4):
        qapp.processEvents()
    assert dlg._tiles[dlg._visible[0]].hasFocus()


# ── 구현 규율 ───────────────────────────────────────────────────────────
def _code_only() -> str:
    """주석·docstring 을 걷어낸 '코드만' 텍스트.

    스타일시트 문자열은 남긴다(그 안의 색 하드코딩을 잡아야 하므로)."""
    import io
    import tokenize
    src = inspect.getsource(ssd)
    parts: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and tok.string.startswith(('"""', "'''")):
            continue
        parts.append(tok.string)
    return "\n".join(parts)


def test_no_inline_palette_hardcoding():
    """★ 선택 틴트를 인라인으로 칠하지 않는다.

    이전 구현은 `rgba(57, 255, 20, 0.10)` — 지금 어느 팔레트에도 없는 죽은 네온 초록을
    하드코딩했다.  주석·docstring 은 제거한 버그를 **설명**하므로 검사에서 제외한다
    (그러지 않으면 설명이 위반으로 잡혀 없는 결함을 인증한다)."""
    import re
    code = _code_only()
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", code), "hex 색 리터럴"
    assert not re.search(r"\brgba?\(\s*\d", code), "rgb(a) 색 리터럴"
    assert not re.search(r"theme\.(ACCENT|INK|LINE|PANEL|BG|MUTE|WARN|PASS|DANGER"
                         r"|ELEV|FOCUS)[A-Z_]*\b", code), "theme 색 상수 인라인"


def test_column_math_is_reused_not_duplicated():
    """열 계산은 ``reflow_into_grid`` 하나만 쓴다 — off-by-one 을 두 곳에 두지 않는다."""
    src = inspect.getsource(ssd)
    assert "reflow_into_grid" in src
    # ★ 전체 소스를 훑으면 **제거한 버그를 설명하는 docstring** 이 위반으로 잡힌다
    #   (실제로 잡혔다).  없는 결함을 인증하는 것도 측정 실패다 — 코드만 본다.
    assert "// (_TILE" not in _code_only(), "직접 열 계산 부활"


def test_qss_paints_the_selected_state():
    for state in (":checked", ":focus", ":hover", ":disabled"):
        assert f'QPushButton[role="slotTile"]{state}' in _QSS, f"slotTile{state} 없음"
    # 차단 이유 라벨도 등급이 있어야 한다(QPushButton 전용 role="warn" 만 있던 함정).
    assert 'QLabel[role="warn"]' in _QSS
