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


def test_specs_wire_icon_and_bundle_assets():
    """빌드 산출물(exe)의 아이콘과 리소스 동봉 — 빠지면 아이콘이 파이썬 것으로 돌아간다."""
    full = (_ROOT / "scripts" / "internal" / "aoi_verification.spec").read_text(
        encoding="utf-8")
    online = (_ROOT / "scripts" / "internal" / "online.spec").read_text(
        encoding="utf-8")
    for text in (full, online):
        assert "icon=_ICON" in text
        assert "logo.ico" in text
    assert "_ASSETS" in full, "assets 폴더를 datas 에 넣어야 스플래시/상단 로고가 뜬다"


# ── 스플래시 (헤드리스) ───────────────────────────────────────────────────
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QPixmap                             # noqa: E402
from PyQt6.QtWidgets import QApplication                    # noqa: E402

from aoi_verification.app.ui import main_window as mw       # noqa: E402
from aoi_verification.app.ui import theme                   # noqa: E402
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
    theme.set_color_mode("light")
    w = mw.MainWindow()
    yield w
    w.close()
    w.deleteLater()
    qapp.processEvents()
    theme.set_color_mode("light")


def test_header_logo_sits_above_the_page_stack(window):
    """로고는 스택 **밖**에 있어야 어느 단계(셋업·선별·매칭·검토·결과)에서도 보인다."""
    layout = window.centralWidget().layout()
    assert layout.indexOf(window._logo_label) == 0
    assert layout.indexOf(window._stack) == 1
    pm = window._logo_label.pixmap()
    assert not pm.isNull(), "logo_clear 를 못 읽었다"
    assert pm.height() == int(window._LOGO_H * pm.devicePixelRatio())


def test_header_logo_inverts_for_dark_mode(window):
    """로고 마크가 거의 검정이라, 반전하지 않으면 어두운 화면에서 묻힌다."""
    light = window._logo_label.pixmap().toImage()
    theme.set_color_mode("dark")
    window._apply_header_logo()
    dark = window._logo_label.pixmap().toImage()
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
