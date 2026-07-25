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


# 페이지를 다시 만들거나 조각을 옮겨도 공유 로직이 동작하려면 이 속성들이 있어야 한다.
CONTRACT_ATTRS = (
    "ref_path_edit", "val_path_edit", "ref_machine_edit", "val_machine_edit",
    "coord_tol_spin", "slider", "threshold_label",
    "update_btn", "start_btn", "_action_bar",
    "_tol_row", "_threshold_row",
    "auto_group", "scope_group",
    "dev_bench_btn", "dev_label_btn",
)


def test_builder_seam_exists():
    """본문이 작은 빌더로 쪼개져 있어야 조각을 값싸게 옮길 수 있다.

    ※ `_build_automation_card` + `_build_scope_row` 는 **`_build_run_options_card`
    하나로 합쳐졌다** — 값이 둘뿐인 두 설정이 전체폭 카드 두 장을 쓰고 있었다."""
    src = inspect.getsource(sp.SetupPage)
    for name in ("_build_body", "_build_title", "_build_view_options",
                 "_build_howto", "_build_run_options_card", "_build_device_row",
                 "_build_engine_card", "_build_action_bar", "_build_credit"):
        assert f"def {name}" in src, f"{name} 빌더 누락"
    # 합쳐진 두 빌더는 되살아나지 말아야 한다(같은 선택을 두 카드가 다시 나누지 않게).
    for gone in ("_build_automation_card", "_build_scope_row"):
        assert f"def {gone}" not in src, f"{gone} 부활 — 카드가 다시 갈라졌다"


def test_binary_settings_use_compact_segments(qapp):
    """★ 이진 선택은 44px 전체폭 타일이 아니라 34px 세그먼트다.

    타일 크기는 고르는 값의 수가 아니라 **그 선택의 무게**를 따른다 — 자동화 수준·진행
    범위는 값이 둘뿐이고 매번 바꾸지도 않는데 화면의 두 덩어리를 차지하고 있었다."""
    page = sp.SetupPage()
    try:
        for name in ("auto_group", "scope_group"):
            g = getattr(page, name)
            assert len(g.keys()) == 2, f"{name} 이 이진 선택이 아니다"
            for k in g.keys():
                role = g.button(k).property("role")
                assert role == "segment", f"{name}[{k}] role={role!r}"
    finally:
        page.deleteLater()


def test_scope_segment_does_not_open_modal_on_arrow_keys(qapp):
    """'일부 슬롯만' 은 **모달**을 띄운다 — 방향키로 훑다 열려선 안 된다.

    실측: 이 그룹에 → 키 한 번으로 경고창이 떠 테스트 하네스가 2분간 블로킹됐다."""
    page = sp.SetupPage()
    try:
        assert page.scope_group._activate_on_arrow is False
        # 부수효과가 없는 자동화 수준은 반대로 방향키 즉시 커밋이 맞다(라디오 감각).
        assert page.auto_group._activate_on_arrow is True
    finally:
        page.deleteLater()


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


def test_only_the_active_engine_params_are_visible(qapp):
    """**지금 쓰는 파라미터만 보인다** — 무효한 쪽은 숨긴다.

    ★ 이 테스트는 한때 **반대 계약**을 고정하고 있었다
    (`test_inert_params_disabled_but_never_hidden`: 비활성이되 `isVisibleTo` 는 True).
    그 근거는 "어느 파라미터가 어느 엔진에 속하는지 눈으로 가르쳐 준다" 였다.
    사용자가 숨기기로 결정했으므로 계약을 뒤집는다 — 지우지 않고 뒤집는 이유는, 다음
    사람이 '왜 안 보이지?'라고 물을 때 여기서 답을 찾게 하려는 것이다.

    '가르쳐 주는' 역할은 상단 모드 배지(판정 기준 **수치**까지 표시)와
    `_engine_inert_hint` 한 줄이 대신한다."""
    page = sp.SetupPage()
    try:
        page.show()
        for _ in range(6):
            qapp.processEvents()
        # 좌표 모드(기본): 허용 오차만 보인다.
        assert page._tol_row.isVisibleTo(page) is True
        assert page._threshold_row.isVisibleTo(page) is False
        assert page.legacy_group.isVisibleTo(page) is False
        # 구형 모드: 정확히 반대.
        page.legacy_switch.set_on(True, emit=True)
        for _ in range(6):
            qapp.processEvents()
        assert page._tol_row.isVisibleTo(page) is False
        assert page._threshold_row.isVisibleTo(page) is True
        assert page.legacy_group.isVisibleTo(page) is True
        # 되돌리면 원래대로 — 한쪽만 바뀌고 굳지 않는다.
        page.legacy_switch.set_on(False, emit=True)
        for _ in range(6):
            qapp.processEvents()
        assert page._tol_row.isVisibleTo(page) is True
        assert page._threshold_row.isVisibleTo(page) is False
    finally:
        page.deleteLater()


def test_view_options_has_only_the_dark_mode_switch(qapp):
    """'모션 줄이기' 토글은 제거했다 — 모션은 항상 켜진다(사용자 결정)."""
    page = sp.SetupPage()
    try:
        assert hasattr(page, "_dark_switch")
        assert not hasattr(page, "_reduce_switch"), "'모션 줄이기' 스위치가 되살아났다"
        assert not hasattr(page, "_on_reduce_motion")
    finally:
        page.deleteLater()


def test_motion_module_has_no_reduce_knobs():
    """모션 줄이기 손잡이는 모듈에서도 사라졌다.

    ★ 단 `enabled()` 의 offscreen 게이트는 남아 있어야 한다 — 사용자 설정이 아니라
    테스트·캡처 **결정성**이고, 크래시 회귀 테스트가 이걸 켜서 애니메이션 경로를
    재현한다."""
    from aoi_verification.app.ui import motion
    for gone in ("set_reduce_motion", "reduce_motion", "os_reduce_motion"):
        assert not hasattr(motion, gone), f"motion.{gone} 잔존"
    from aoi_verification.app.utils import prefs
    assert not hasattr(prefs.UiPrefs(), "reduce_motion"), "prefs.reduce_motion 잔존"
    import inspect
    assert "offscreen" in inspect.getsource(motion.enabled)


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


# ── 유효성 게이트 · 상태를 컨트롤 안에 ──────────────────────────────────────
def test_start_disabled_until_folders_valid(qapp, tmp_path):
    page = sp.SetupPage()
    try:
        assert page.start_btn.isEnabled() is False       # 초기: 폴더 미지정
        assert page._start_hint.text() != ""             # 이유가 보인다
        (tmp_path / "r").mkdir()
        (tmp_path / "v").mkdir()
        page.ref_path_edit.setText(str(tmp_path / "r"))
        page.val_path_edit.setText(str(tmp_path / "v"))
        page._validate()
        assert page.start_btn.isEnabled() is True
        assert page._start_hint.text() == ""
    finally:
        page.deleteLater()


def test_invalid_folder_shows_inline_reason(qapp):
    page = sp.SetupPage()
    try:
        page.ref_path_edit.setText("/definitely/not/here")
        page._validate()
        assert page.ref_path_edit.property("state") == "invalid"
        err = page.ref_path_edit.property("_errLabel")
        assert err is not None and err.text() != "" and err.isVisibleTo(page)
        assert page.start_btn.isEnabled() is False
    finally:
        page.deleteLater()


def test_messagebox_validation_path_preserved(qapp, monkeypatch):
    """비활성화는 '추가 레이어' — 기존 **경고 팝업** 경로가 살아 있어야 한다.

    (검증 통과 후 폴더가 삭제된 경우처럼 늦게 드러나는 실패를 계속 잡아준다.)

    ※ 팝업은 이제 별도 OS 창(QMessageBox)이 아니라 **앱 내 시트**(`sheet_host`)로 뜬다 —
    가로채는 지점만 바뀌었고 '경고가 뜨고 진행이 중단된다'는 계약은 그대로다."""
    page = sp.SetupPage()
    try:
        calls = []
        monkeypatch.setattr(sp.sheets, "warn",
                            lambda *a, **k: calls.append(a))
        page.ref_path_edit.setText("/definitely/not/here")
        page.val_path_edit.setText("/also/not/here")
        assert page._collect_input() is None      # 중단
        assert calls, "경로 경고 팝업이 사라졌다"
    finally:
        page.deleteLater()


def test_scope_state_lives_in_the_tile(qapp):
    """부분 선택 상태가 옆 라벨이 아니라 타일 라벨에 나타난다."""
    from aoi_verification.app import i18n
    page = sp.SetupPage()
    try:
        assert page.scope_group.current_key() == "all"
        assert page._selected_slots is None
        page.scope_group.set_option_label(
            "subset", i18n.KO.SCOPE_SUBSET_COUNT_FMT.format(n=12, total=40))
        page.scope_group.set_current_key("subset")
        assert "12/40" in page.scope_group.button("subset").text()
        page._reset_slot_selection()
        assert page.scope_group.current_key() == "all"
        assert page.scope_group.button("subset").text() == i18n.KO.SCOPE_SUBSET
        # 상태가 옆 라벨에 사는 옛 구조는 사라졌다.
        assert not hasattr(page, "slot_select_label")
    finally:
        page.deleteLater()


def test_automation_tiles_replace_radios(qapp):
    from aoi_verification.app.utils.prefs import AutomationLevel
    page = sp.SetupPage()
    try:
        assert not hasattr(page, "radio_auto_user")
        assert page.auto_group.current_key() == AutomationLevel.USER_SELECT
        page.auto_group.set_current_key(AutomationLevel.AUTO_ALL, emit=True)
        assert page.auto_group.current_key() == AutomationLevel.AUTO_ALL
    finally:
        page.deleteLater()


def test_disabled_primary_is_visually_distinct():
    """비활성 primary 가 꽉 찬 채로 남으면 '누를 수 있어 보이는 못 누르는 버튼'이 된다.

    속성 선택자가 일반 :disabled 보다 특이도가 높아 전용 규칙이 필요하다."""
    from pathlib import Path as _P
    from aoi_verification.app.ui import theme
    qss = (_P(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui"
           / "style.qss").read_text(encoding="utf-8")
    assert 'QPushButton[role="primary"]:disabled' in qss
    out = theme.render_qss(qss)
    assert "$" not in out


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


# ── 색 모드/배치 전환이 작성 중 입력을 날리지 않아야 한다 ──────────────────────
def test_capture_and_restore_draft_round_trip(qapp, tmp_path):
    """★ '세션 시작 전'은 아무것도 입력하지 않았다는 뜻이 아니다.

    폴더·호기·진행 범위·손으로 고른 슬롯·허용 오차는 [검증 시작] 전까지 prefs 에 없어서,
    어두운 화면 토글 한 번(페이지 재생성)에 조용히 사라졌다.  특히 '일부 슬롯 12/40' 이
    '모든 슬롯'으로 되돌아가면 40슬롯을 통째로 돌리게 된다."""
    src = sp.SetupPage()
    dst = sp.SetupPage()
    try:
        src.ref_path_edit.setText(str(tmp_path))
        src.val_path_edit.setText(str(tmp_path))
        src.ref_machine_edit.setText("3호기")
        src.val_machine_edit.setText("7호기")
        src.coord_tol_spin.setValue(750.0)
        src._selected_slots = {f"슬롯 {i:02d}" for i in range(12)}
        src.scope_group.set_option_label("subset", "일부 슬롯 (12/40)")
        src.scope_group.set_current_key("subset")

        draft = src.capture_draft()
        dst.restore_draft(draft)

        assert dst.ref_path_edit.text() == str(tmp_path)
        assert dst.val_path_edit.text() == str(tmp_path)
        assert dst.ref_machine_edit.text() == "3호기"
        assert dst.val_machine_edit.text() == "7호기"
        assert dst.coord_tol_spin.value() == 750.0
        assert dst._selected_slots == {f"슬롯 {i:02d}" for i in range(12)}
        assert dst.scope_group.current_key() == "subset"
        assert "12/40" in dst.scope_group.button("subset").text()
        # 유효한 폴더가 복원됐으니 시작 버튼도 다시 활성.
        assert dst.start_btn.isEnabled() is True
    finally:
        src.deleteLater()
        dst.deleteLater()


def test_restore_draft_does_not_reopen_slot_dialog(qapp, tmp_path, monkeypatch):
    """'subset' 복원 시 emit 하면 슬롯 선택 모달이 다시 뜬다 — emit 하지 않는다."""
    page = sp.SetupPage()
    opened = []
    monkeypatch.setattr(page, "_open_slot_select", lambda: opened.append(1))
    try:
        page.restore_draft({"scope": "subset", "subset_label": "일부 슬롯 (5/9)",
                            "selected_slots": {"a", "b", "c", "d", "e"}})
        assert not opened, "복원이 슬롯 선택 다이얼로그를 열었다"
        assert page.scope_group.current_key() == "subset"
    finally:
        page.deleteLater()


def test_recreate_pages_carries_the_draft():
    """main_window 가 재생성 전에 걷고 후에 심는지 — 주석이 아니라 코드로 확인."""
    import inspect

    from aoi_verification.app.ui import main_window as mw
    src = inspect.getsource(mw.MainWindow._recreate_pages)
    assert "capture_draft" in src
    assert "restore_draft" in src
    # 걷는 것이 파괴보다 **먼저**여야 한다.
    assert src.index("capture_draft") < src.index("deleteLater")


# ── 스위치는 press 가 아니라 release 에서 실행한다(스크롤 제스처 방어) ──────────
def test_switch_does_not_toggle_on_press_alone(qapp):
    """★ press 즉시 토글하면 터치 패널의 스크롤 제스처가 판정 엔진을 바꾼다."""
    from PyQt6.QtCore import QPointF, Qt as _Qt
    from PyQt6.QtGui import QMouseEvent

    from aoi_verification.app.ui.widgets.switch_row import SwitchRow
    row = SwitchRow("구형 모드", checked=False)
    try:
        row.resize(300, 40)
        sw = row.switch
        center = QPointF(sw.width() / 2, sw.height() / 2)
        press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, center,
                            _Qt.MouseButton.LeftButton, _Qt.MouseButton.LeftButton,
                            _Qt.KeyboardModifier.NoModifier)
        sw.mousePressEvent(press)
        assert sw.is_on() is False, "press 만으로 토글됐다"

        # 위젯 **밖**에서 release → 취소(드래그로 빠져나간 경우)
        outside = QPointF(sw.width() + 40, sw.height() + 40)
        rel_out = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, outside,
                              _Qt.MouseButton.LeftButton, _Qt.MouseButton.LeftButton,
                              _Qt.KeyboardModifier.NoModifier)
        sw.mouseReleaseEvent(rel_out)
        assert sw.is_on() is False, "밖에서 release 했는데 토글됐다"

        # 안에서 press→release → 실행
        sw.mousePressEvent(press)
        rel_in = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, center,
                             _Qt.MouseButton.LeftButton, _Qt.MouseButton.LeftButton,
                             _Qt.KeyboardModifier.NoModifier)
        sw.mouseReleaseEvent(rel_in)
        assert sw.is_on() is True, "정상 클릭이 토글되지 않았다"
    finally:
        row.deleteLater()


def test_switch_hot_zone_is_not_the_whole_row(qapp):
    """넓은 카드 안에서 행 전체가 핫존이면 보이지 않는 1300px 클릭영역이 생긴다."""
    from aoi_verification.app.ui.widgets.switch_row import SwitchRow
    row = SwitchRow("유사도 엔진(구형) 사용",
                    description="끄면 좌표 매칭을 씁니다 — 권장 기본값.")
    try:
        row.resize(1300, 60)
        row.show()
        for _ in range(8):
            qapp.processEvents()
        # 제목은 자기 글자 폭만 차지해야 한다(여백까지 늘어나면 다시 넓은 핫존).
        assert row._title.width() < row.width() * 0.5, \
            f"제목 폭 {row._title.width()} / 행 폭 {row.width()}"
        # 행 자신은 mousePressEvent 를 재정의하지 않는다(빈 여백 클릭 = 무반응).
        assert "mousePressEvent" not in SwitchRow.__dict__
    finally:
        row.deleteLater()
