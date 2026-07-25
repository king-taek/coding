"""셋업 화면 컨트롤 — 빌더 seam 계약 · 액션바 인덱스 · (이후 단계에서 모드 스위치 추가)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import inspect

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication                        # noqa: E402

from aoi_verification.app.ui.pages import setup_page as sp      # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# 배치안(서브클래스)이 갈아끼워도 공유 로직이 동작하려면 이 속성들이 있어야 한다.
CONTRACT_ATTRS = (
    "ref_path_edit", "val_path_edit", "ref_machine_edit", "val_machine_edit",
    "coord_tol_spin", "slider", "threshold_label",
    "update_btn", "start_btn", "_action_bar",
    "_tol_row", "_threshold_row",
    "dev_bench_btn", "dev_label_btn",
)


def test_builder_seam_exists():
    """본문이 작은 빌더로 쪼개져 있어야 배치안을 값싸게 만들 수 있다."""
    src = inspect.getsource(sp.SetupPage)
    for name in ("_build_body", "_build_title", "_build_view_options",
                 "_build_howto", "_build_automation_card", "_build_device_row",
                 "_build_scope_row", "_build_engine_card", "_build_action_bar",
                 "_build_credit"):
        assert f"def {name}" in src, f"{name} 빌더 누락"


def test_page_satisfies_attr_contract(qapp):
    page = sp.SetupPage()
    try:
        missing = [a for a in CONTRACT_ATTRS if not hasattr(page, a)]
        assert not missing, f"속성 계약 위반: {missing}"
    finally:
        page.deleteLater()


def test_action_bar_index_contract(qapp, monkeypatch):
    """[0]=update_btn, 개발자 모드에서 [1..2]=개발자 버튼.

    ``_refresh_dev_buttons`` 가 insertWidget(1/2, …) 를 가정하므로, 새 위젯은 반드시
    stretch 뒤에 붙어야 한다.  이 계약이 깨지면 개발자 버튼이 엉뚱한 자리에 들어간다."""
    page = sp.SetupPage()
    try:
        bar = page._action_bar
        assert bar.itemAt(0).widget() is page.update_btn
        # 개발자 모드 on → 인덱스 1·2 에 개발자 버튼이 삽입된다.
        monkeypatch.setattr(page, "_dev_mode_enabled", lambda: True)
        page._refresh_dev_buttons()
        assert page.dev_bench_btn is not None and page.dev_label_btn is not None
        assert bar.itemAt(0).widget() is page.update_btn
        assert bar.itemAt(1).widget() is page.dev_bench_btn
        assert bar.itemAt(2).widget() is page.dev_label_btn
        # 마지막 위젯은 여전히 주 액션이어야 한다(stretch 뒤).
        last = bar.itemAt(bar.count() - 1).widget()
        assert last is page.start_btn
    finally:
        page.deleteLater()


def test_help_tooltip_comes_from_i18n():
    """사용자 노출 문자열 하드코딩 금지(CLAUDE.md) — '?' 툴팁도 i18n 에서."""
    src = inspect.getsource(sp)
    assert "설명 보기/숨기기" not in src
    assert "HELP_TOGGLE_TOOLTIP" in src


# ── 구형 모드: 명시 스위치만이 모드를 바꾼다 (핵심 회귀) ─────────────────────
def test_fresh_prefs_starts_in_coordinate(qapp):
    from aoi_verification.app.utils.prefs import EngineMode
    page = sp.SetupPage()
    try:
        assert page.legacy_switch.is_on() is False
        assert page._current_engine_mode() == EngineMode.COORDINATE
    finally:
        page.deleteLater()


def test_reading_the_explanation_does_not_change_engine(qapp):
    """★ 회귀 핵심: 설명을 열어봐도 엔진은 그대로다.

    예전에는 접이식 섹션의 펼침 상태가 곧 엔진 모드였기 때문에, 설명을 읽으려고
    펼치기만 해도 좌표 → 유사도로 조용히 바뀌었다."""
    from aoi_verification.app.utils.prefs import EngineMode
    page = sp.SetupPage()
    try:
        before = page._current_engine_mode()
        page._auto_help_btn.setChecked(True)          # 도움말 펼치기
        page._auto_help_btn.setChecked(False)
        if hasattr(page, "_howto_section"):           # 사용 방법 섹션도 펼쳐 본다
            page._howto_section.set_expanded(True, animate=False)
            page._howto_section.set_expanded(False, animate=False)
        assert page._current_engine_mode() == before == EngineMode.COORDINATE
        # 접이식 상태를 모드 판단에 쓰는 코드가 남아 있지 않아야 한다.
        src = inspect.getsource(sp.SetupPage._current_engine_mode)
        assert "is_expanded" not in src.split('"""')[-1]
    finally:
        page.deleteLater()


def test_switch_and_sub_choice_drive_engine_mode(qapp):
    from aoi_verification.app.utils.prefs import EngineMode
    page = sp.SetupPage()
    try:
        page.legacy_switch.set_on(True, emit=True)
        assert page._current_engine_mode() == EngineMode.BASIC
        page.legacy_group.set_current_key(EngineMode.EFFICIENCY, emit=True)
        assert page._current_engine_mode() == EngineMode.EFFICIENCY
        page.legacy_switch.set_on(False, emit=True)
        assert page._current_engine_mode() == EngineMode.COORDINATE
    finally:
        page.deleteLater()


def test_inert_params_disabled_but_never_hidden(qapp):
    """무효한 파라미터는 비활성으로 '왜 못 쓰는지' 보여준다 — 숨기지 않는다."""
    page = sp.SetupPage()
    try:
        page.show()
        for _ in range(4):
            qapp.processEvents()
        # 좌표 모드: 허용 오차 활성, 임계치·하위선택 비활성
        assert page._tol_row.isEnabled() is True
        assert page._threshold_row.isEnabled() is False
        assert page.legacy_group.isEnabled() is False
        # 구형 모드: 반대
        page.legacy_switch.set_on(True, emit=True)
        assert page._tol_row.isEnabled() is False
        assert page._threshold_row.isEnabled() is True
        assert page.legacy_group.isEnabled() is True
        # 어느 모드에서도 숨기지 않는다.
        assert page._tol_row.isVisibleTo(page) is True
        assert page._threshold_row.isVisibleTo(page) is True
    finally:
        page.deleteLater()


def test_active_mode_is_stated_in_words(qapp):
    page = sp.SetupPage()
    try:
        assert "좌표" in page._engine_inert_hint.text()
        page.legacy_switch.set_on(True, emit=True)
        assert "구형" in page._engine_inert_hint.text()
    finally:
        page.deleteLater()


def test_collect_input_uses_explicit_switch(qapp, monkeypatch, tmp_path):
    """_collect_input 의 engine_mode 가 스위치 상태를 따른다."""
    from aoi_verification.app.utils.prefs import EngineMode
    page = sp.SetupPage()
    try:
        ref = tmp_path / "ref"; ref.mkdir()
        val = tmp_path / "val"; val.mkdir()
        page.ref_path_edit.setText(str(ref))
        page.val_path_edit.setText(str(val))
        inp = page._collect_input()
        assert inp is not None and inp.engine_mode == EngineMode.COORDINATE
        page.legacy_switch.set_on(True, emit=True)
        page.legacy_group.set_current_key(EngineMode.EFFICIENCY, emit=True)
        inp2 = page._collect_input()
        assert inp2 is not None and inp2.engine_mode == EngineMode.EFFICIENCY
    finally:
        page.deleteLater()


def test_legacy_hint_no_longer_claims_expanding_switches(qapp):
    """거짓 설명 제거 — '펼치면 전환됩니다' 는 더 이상 사실이 아니다."""
    from aoi_verification.app import i18n
    assert "펼치면" not in i18n.KO.LEGACY_MODE_HINT
    # 좌표 데이터 없음 안내는 켤 컨트롤을 지목해야 한다.
    assert "유사도 엔진(구형) 사용" in i18n.KO.COORD_NO_DATA_MSG


def test_no_horizontal_scroll_at_800x600(qapp):
    from PyQt6.QtWidgets import QScrollArea
    page = sp.SetupPage()
    try:
        page.resize(800, 600)
        page.show()
        for _ in range(8):
            qapp.processEvents()
        scroll = page.findChild(QScrollArea)
        assert scroll is not None
        assert scroll.horizontalScrollBar().maximum() == 0
    finally:
        page.deleteLater()
