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
- 준비 완료가 두 번 와도 나머지 페이지를 **한 번만** 만든다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app import i18n                        # noqa: E402
from aoi_verification.app.ui import main_window as mw        # noqa: E402


@pytest.fixture
def window(qapp, monkeypatch, isolated_cache):
    """백엔드 로딩을 **수동으로** 굴리는 창 — 타이밍에 기대지 않는다."""
    monkeypatch.setattr(mw.MainWindow, "_check_for_update_async", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_maybe_offer_openvino", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_warmup_accel_async", lambda self: None)
    # 스레드를 띄우지 않는다 — 테스트가 `_on_backend_loaded` 를 직접 부른다.
    monkeypatch.setattr(mw.MainWindow, "_start_backend_import_async",
                        lambda self: None)
    w = mw.MainWindow()
    yield w
    w.close()
    w.deleteLater()
    qapp.processEvents()


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


# ── 창을 띄우기 전에 로딩창에서 끝낸다(첫 [폴더 선택…] 직후 멈춤) ─────────────
# 사용자 신고: 앱을 켜고 처음 한 번, 폴더 창을 취소하고 돌아오면 몇 초 버튼이 안
# 눌린다.  창이 뜬 뒤 끝나는 import(GIL 점유)와 나머지 화면 생성이 원인이었다.

def _load_main_module():
    import importlib.util
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("aoi_main_for_gating", root / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, (root / "main.py").read_text(encoding="utf-8")


class _Splash:
    def __init__(self):
        self.calls = []

    def set_progress(self, done, total, msg):
        self.calls.append((done, total, msg))


def test_main_waits_for_the_backend_before_showing_the_window():
    _mod, src = _load_main_module()
    body = src[src.index("def _open_main_window"):src.index("def _ensure_deps_installed")]
    assert body.index("_wait_backend(") < body.index("window.show()")


def test_wait_backend_runs_the_queued_page_build_while_hidden(window, qapp):
    """로딩창 대기 중에 '로딩 끝' 신호가 배달돼 나머지 화면이 **창이 숨은 채** 만들어진다."""
    mod, _src = _load_main_module()
    assert not window.backend_settled()
    import threading
    t = threading.Thread(target=window._backend_loaded.emit)   # 실제처럼 다른 스레드에서
    t.start()
    t.join()                                      # 큐 연결 — 이벤트를 돌려야 배달된다
    assert not window.backend_settled()
    splash = _Splash()
    mod._wait_backend(qapp, splash, window, timeout=5.0)
    assert window.backend_settled()
    assert window._select_page is not None
    assert not window.isVisible()
    assert splash.calls and splash.calls[0][:2] == (0, 0), "busy 로 띄워야 한다(0 에 멈춤 금지)"


def test_wait_backend_gives_up_after_timeout(window, qapp):
    mod, _src = _load_main_module()
    mod._wait_backend(qapp, _Splash(), window, timeout=0.1)   # 신호가 안 온다
    assert not window.backend_settled()                        # 그냥 연다 — 예전 경로


def test_backend_error_waits_until_the_window_is_visible(window, monkeypatch):
    """숨은 창에 시트를 열면 보이지 않는 팝업이 시작을 막는다 — 보일 때까지 미룬다."""
    shown = []
    monkeypatch.setattr(mw.sheets, "error", lambda *a, **k: shown.append(a))
    monkeypatch.setattr(window, "_build_remaining_pages",
                        lambda: (_ for _ in ()).throw(ImportError("cv2")))
    window._on_backend_loaded()
    assert window.backend_settled()
    assert shown == [], "창이 숨어 있는데 오류 시트를 열었다"
    window.show()
    window._show_backend_error()
    assert len(shown) == 1


def test_startup_popups_wait_until_the_window_is_visible(window, monkeypatch):
    """로딩창 대기 중 400ms 타이머가 숨은 창에서 발화해도 시작 팝업을 열지 않는다."""
    calls = []
    monkeypatch.setattr(window, "_check_for_update_async", lambda: calls.append("check"))
    window._startup_checks = None
    window._begin_update_check()
    assert calls == [], "창이 숨어 있는데 시작 흐름(업데이트 안내·이어하기)을 시작했다"
    window.show()
    window._begin_update_check()
    assert calls == ["check"]
