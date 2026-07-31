"""로고 3종 배선 — 앱 아이콘 · 시작 스플래시 · 메인화면 상단 로고.

로고는 ``docs/`` 가 아니라 앱 폴더(``app/ui/assets/``) 안에 있어야 한다.  포터블 빌드
복사(`portable_build._copytree`)와 자동 업데이트 미러가 ``aoi_verification/`` 를 통째로
따라오기 때문 — ``docs/`` 에 두면 배포본에서 로고가 사라진다.

스플래시는 CLAUDE.md 로딩 계약을 따른다: ``set_progress(done, total, message)``,
``total <= 0`` 이면 busy(무한).  창 생성처럼 메인 스레드를 막는 구간은 결정형으로 칸이
올라가야 한다 — 안 그러면 '0 에 멈춰 있다 갑자기 완료' 가 된다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

from aoi_verification.app.utils import paths

_ROOT = Path(__file__).resolve().parents[2]


# ── 리소스 위치 (무거운 의존성 없이) ──────────────────────────────────────
@pytest.mark.parametrize("name", ["logo.ico", "logo_big.png", "logo_clear.png"])
def test_logo_assets_live_inside_the_app_package(name):
    p = paths.logo_path(name)
    assert p.exists(), f"{name} 없음 — 배포본에서 로고가 빠진다"
    assert p.parent == _ROOT / "aoi_verification" / "app" / "ui" / "assets"


def test_specs_wire_icon():
    """빌드 산출물(exe)의 아이콘 — 빠지면 아이콘이 파이썬 것으로 돌아간다.

    리소스(로고·style.qss)는 exe 안에 넣지 않는다.  'exe + app 폴더' 배포에서는
    ``app/aoi_verification/…`` 에 loose 로 있어야 자동 업데이트로 갱신된다."""
    for spec in ("exe_launcher.spec", "online.spec"):
        text = (_ROOT / "scripts" / "internal" / spec).read_text(encoding="utf-8")
        assert "icon=_ICON" in text, spec
        assert "logo.ico" in text, spec


# ── 스플래시 (헤드리스) ───────────────────────────────────────────────────
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QPixmap                             # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel            # noqa: E402

from aoi_verification.app.ui import main_window as mw       # noqa: E402
from aoi_verification.app.ui import theme                   # noqa: E402
from aoi_verification.app.ui.widgets import app_logo        # noqa: E402
from aoi_verification.app.ui.widgets.startup_splash import (  # noqa: E402
    StartupSplash)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def splash(qapp):
    s = StartupSplash(QPixmap(str(paths.logo_path("logo_big.png"))))
    yield s
    s.close()
    s.deleteLater()
    qapp.processEvents()


def test_splash_shows_the_big_logo(splash):
    pm = splash._logo.pixmap()
    assert not pm.isNull(), "logo_big 을 못 읽었다 — 스플래시가 빈 창이 된다"
    assert pm.width() <= StartupSplash.LOGO_W * pm.devicePixelRatio()


def test_splash_busy_when_total_unknown(splash):
    splash.show()
    splash.set_progress(0, 0, "불러오는 중")
    assert splash._busy.isVisible(), "총량을 모를 때 0 에 멈춘 바를 보이면 안 된다"
    assert not splash._progress.isVisible()


def test_splash_determinate_advances(splash):
    splash.show()
    splash.set_progress(0, 0, "불러오는 중")
    splash.set_progress(2, 5, "화면 준비")
    assert splash._progress.isVisible() and not splash._busy.isVisible()
    assert (splash._progress.value(), splash._progress.maximum()) == (2, 5)
    splash.set_progress(5, 5)
    assert splash._progress.value() == 5
    assert splash._label.text() == "화면 준비", "메시지 없이 부르면 직전 문구를 유지"


# ── 메인 창 상단 로고 ─────────────────────────────────────────────────────
@pytest.fixture
def window(qapp, monkeypatch, isolated_cache):
    # 시작 시 뜨는 업데이트 확인 모달을 막는다(test_dark_mode_transition 과 같은 이유).
    monkeypatch.setattr(mw.MainWindow, "_check_for_update_async", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_maybe_offer_openvino", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_warmup_accel_async", lambda self: None)
    # 백엔드 로딩은 테스트가 직접 굴린다 — 스레드 타이밍에 기대지 않는다.
    monkeypatch.setattr(mw.MainWindow, "_start_backend_import_async",
                        lambda self: None)
    theme.set_color_mode("light")
    w = mw.MainWindow()
    yield w
    w.close()
    w.deleteLater()
    qapp.processEvents()
    theme.set_color_mode("light")


def _logos(widget) -> list:
    return [lb for lb in widget.findChildren(QLabel)
            if lb.property("role") == "appLogo"]


def test_page_stack_owns_the_whole_central_area(window):
    """로고는 더 이상 스택 밖 고정 칸이 아니다 — 중앙에는 스택뿐이다.

    사용자 요청: "프로그램 상단에 로고 있는 칸은 스크롤 영향이 없는 고정 칸인데,
    고정이 아니도록 바꾸고 싶음"."""
    layout = window.centralWidget().layout()
    assert layout.count() == 1
    assert layout.indexOf(window._stack) == 0
    assert not _logos(window.centralWidget()) or all(
        _is_inside(lb, window._stack) for lb in _logos(window.centralWidget())), \
        "로고가 아직 스택 밖에 남아 있다"


def _is_inside(widget, ancestor) -> bool:
    node = widget.parentWidget()
    for _ in range(40):
        if node is None:
            return False
        if node is ancestor:
            return True
        node = node.parentWidget()
    return False


def test_setup_page_logo_scrolls_with_the_content(window):
    """설정 화면은 전체 스크롤이 있다 — 로고가 그 **안**에 있어야 함께 밀린다."""
    from PyQt6.QtWidgets import QScrollArea
    page = window._setup_page
    scroll = page.findChild(QScrollArea)
    assert scroll is not None, "설정 화면의 스크롤 영역이 사라졌다"
    inside = _logos(scroll.widget())
    assert len(inside) == 1, "로고가 스크롤 host 안에 없다 — 스크롤해도 따라오지 않는다"
    pm = inside[0].pixmap()
    assert not pm.isNull(), "logo_clear 를 못 읽었다"
    assert pm.height() == int(app_logo.LOGO_H * pm.devicePixelRatio())


def test_every_page_carries_the_logo(window, qapp):
    """어느 단계에서도 로고가 보여야 한다 — 이제는 페이지마다 자기 것을 갖는다."""
    window._on_backend_loaded()
    qapp.processEvents()
    pages = (window._setup_page, window._select_page, window._match_page,
             window._match_review_page, window._result_page)
    for page in pages:
        assert len(_logos(page)) == 1, \
            f"{type(page).__name__} 에 로고가 {len(_logos(page))}개다"


def test_logo_inverts_for_dark_mode(qapp):
    """로고 마크가 거의 검정이라, 반전하지 않으면 어두운 화면에서 묻힌다."""
    theme.set_color_mode("light")
    light = app_logo.build_logo_label().pixmap().toImage()
    theme.set_color_mode("dark")
    dark = app_logo.build_logo_label().pixmap().toImage()
    theme.set_color_mode("light")
    assert dark != light
    # 알파는 보존한 채 RGB 만 뒤집혔는지 — 가장 진한 획이 밝아진다.
    mid = (light.width() // 2, light.height() // 2)
    assert (dark.pixelColor(*mid).lightness()
            > light.pixelColor(*mid).lightness())


def test_build_pages_reports_progress_for_the_splash(qapp, monkeypatch,
                                                     isolated_cache):
    """창 생성은 메인 스레드를 막는다 — 페이지마다 진행을 보고해야 바가 움직인다."""
    monkeypatch.setattr(mw.MainWindow, "_check_for_update_async", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_maybe_offer_openvino", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_warmup_accel_async", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_start_backend_import_async",
                        lambda self: None)
    seen: list[tuple[int, int]] = []
    w = mw.MainWindow(progress=lambda done, total, message="": seen.append(
        (done, total)))
    try:
        assert seen[0][0] == 0 and seen[-1][0] == seen[-1][1] > 0
        assert [d for d, _ in seen] == sorted(d for d, _ in seen)
    finally:
        w.close()
        w.deleteLater()
        qapp.processEvents()
