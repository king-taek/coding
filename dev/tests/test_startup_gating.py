"""창을 먼저 띄우고 무거운 것은 뒤에 — 그동안 [검증 시작] 은 '준비 중' 으로 잠긴다.

사용자 요청: "구동에 필수적인거 먼저 하고나서 나머지는 메인 화면 켜지고 나서 로딩
(검증 시작 버튼에 로딩중이라고 표기)".

여기서 고정하는 계약:

- 창이 뜬 직후에는 **첫 화면(SetupPage)만** 존재한다.  나머지 넷은 무거운 모듈이
  올라온 뒤에 만들어진다.
- 그동안 [검증 시작] 은 비활성이고 버튼 글자가 '준비 중…' 이다.  폴더를 제대로
  골라도 아직 눌리면 안 된다 — 누르면 그 자리에서 import 가 터진다.
- 활성 여부의 주인은 여전히 ``SetupPage._validate`` 하나다.  준비 완료를 별도
  경로로 ``setEnabled`` 하면 다음 폴더 입력 디바운스가 그것을 덮어쓴다.
- 준비 전에 색 모드를 바꿔도(페이지 재생성) 깨지지 않고, 뒤늦게 도착한 준비 완료가
  나머지 페이지를 **한 번만** 만든다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication                     # noqa: E402

from aoi_verification.app import i18n                        # noqa: E402
from aoi_verification.app.ui import main_window as mw        # noqa: E402
from aoi_verification.app.ui import theme                    # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch, isolated_cache):
    """백엔드 로딩을 **수동으로** 굴리는 창 — 타이밍에 기대지 않는다."""
    monkeypatch.setattr(mw.MainWindow, "_check_for_update_async", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_maybe_offer_openvino", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_warmup_accel_async", lambda self: None)
    # 스레드를 띄우지 않는다 — 테스트가 `_on_backend_loaded` 를 직접 부른다.
    monkeypatch.setattr(mw.MainWindow, "_start_backend_import_async",
                        lambda self: None)
    theme.set_color_mode("light")
    w = mw.MainWindow()
    yield w
    w.close()
    w.deleteLater()
    qapp.processEvents()
    theme.set_color_mode("light")


def _fill_valid_dirs(page, tmp_path) -> None:
    ref, val = tmp_path / "ref", tmp_path / "val"
    ref.mkdir(exist_ok=True)
    val.mkdir(exist_ok=True)
    page.ref_path_edit.setText(str(ref))
    page.val_path_edit.setText(str(val))


def test_window_opens_with_only_the_setup_page(window):
    assert window._stack.count() == 1, "첫 화면만 있어야 창이 빨리 뜬다"
    assert window._stack.widget(0) is window._setup_page
    assert window._select_page is None
    assert window._match_page is None
    assert window._result_page is None
    assert window._match_review_page is None


def test_start_button_says_preparing_and_stays_locked(window, tmp_path):
    page = window._setup_page
    assert page.start_btn.text() == i18n.KO.BTN_START_PREPARING
    _fill_valid_dirs(page, tmp_path)
    assert page._validate() is False, "준비 전인데 시작이 열렸다"
    assert not page.start_btn.isEnabled()
    assert page._start_hint.text() == i18n.KO.START_PREPARING_HINT


def test_folder_problem_is_reported_before_the_preparing_notice(window):
    """폴더 문제가 먼저다 — 그건 지금 고칠 수 있고, '준비 중' 은 저절로 풀린다."""
    page = window._setup_page
    page.ref_path_edit.setText("/존재하지/않는/폴더")
    page.val_path_edit.setText("")
    page._validate()
    assert page._start_hint.text() == i18n.KO.START_BLOCKED_HINT


def test_backend_ready_builds_the_rest_and_opens_start(window, tmp_path):
    page = window._setup_page
    _fill_valid_dirs(page, tmp_path)
    window._on_backend_loaded()
    assert window._stack.count() == 5, "나머지 페이지가 만들어지지 않았다"
    for attr in ("_select_page", "_match_page", "_result_page",
                 "_match_review_page"):
        assert getattr(window, attr) is not None, f"{attr} 가 비어 있다"
    assert page.start_btn.text() == i18n.KO.BTN_START
    assert page._validate() is True
    assert page.start_btn.isEnabled()
    assert page._start_hint.text() == ""


def test_ready_twice_does_not_duplicate_pages(window):
    window._on_backend_loaded()
    window._on_backend_loaded()
    assert window._stack.count() == 5, "준비 완료가 두 번 와서 페이지가 중복 생성됐다"


def test_color_mode_change_before_ready_is_safe(window, qapp, tmp_path):
    """준비 전 다크 전환 → 첫 화면만 재생성.  뒤늦은 준비 완료가 나머지를 만든다."""
    window._on_appearance_changed("dark")
    qapp.processEvents()
    assert window._stack.count() == 1
    assert window._select_page is None

    window._on_backend_loaded()
    assert window._stack.count() == 5
    page = window._setup_page
    assert page.start_btn.text() == i18n.KO.BTN_START
    _fill_valid_dirs(page, tmp_path)
    assert page._validate() is True


def test_color_mode_change_after_ready_rebuilds_all_five(window, qapp):
    window._on_backend_loaded()
    assert window._stack.count() == 5
    window._on_appearance_changed("dark")
    qapp.processEvents()
    assert window._stack.count() == 5, "재생성 후 페이지 수가 달라졌다"
    assert window._setup_page.start_btn.text() == i18n.KO.BTN_START


def test_setup_page_alone_is_not_locked(qapp):
    """이 페이지를 단독으로 띄우는 곳(테스트·미리보기)에서 버튼이 잠기면 안 된다."""
    from aoi_verification.app.ui.pages.setup_page import SetupPage
    page = SetupPage()
    try:
        assert page._backend_ready is True
        assert page.start_btn.text() == i18n.KO.BTN_START
    finally:
        page.deleteLater()
        qapp.processEvents()
