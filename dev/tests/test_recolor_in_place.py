"""색 모드 전환은 **페이지를 다시 만들지 않는다** — 색만 갈아 끼운다.

예전에는 위젯이 색을 ``setStyleSheet(f"color: {theme.INK}")`` 처럼 **생성 시점에
구웠기 때문에**, 색을 바꾸려면 페이지 5개를 통째로 다시 만들어야 했다(실측 124ms).
그 자리를 전부 QSS role 로 옮겨서 이제 시트 재적용 + 폴리시만으로 끝난다.
실측: 누르고 색이 움직이기까지 346ms → 153ms.

여기서 못박는 것은 세 가지다.
1. 전환 뒤에도 **같은 위젯 객체**가 살아 있다(다시 만들지 않았다는 뜻).
2. 살아 있는 위젯이 **새 색을 입는다**(전환이 실제로 일어났다).
3. QSS 로 못 바꾸는 둘(로고 픽스맵·상태바)을 **잊지 않고** 손본다.
"""

from __future__ import annotations

import inspect
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel                              # noqa: E402

from aoi_verification.app.ui import main_window as mw           # noqa: E402
from aoi_verification.app.ui import theme                       # noqa: E402
from aoi_verification.app.utils import prefs as _prefs          # noqa: E402


@pytest.fixture()
def window(styled_qapp, isolated_cache):
    _prefs.patch(color_mode="light")
    theme.set_color_mode("light")
    w = mw.MainWindow()
    w.resize(1200, 800)
    for _ in range(8):
        styled_qapp.processEvents()
    yield w
    w.close()
    theme.set_color_mode("light")


def test_pages_are_not_recreated(window, styled_qapp):
    """★ 같은 객체가 살아남아야 한다 — 재생성하면 이 테스트가 깨진다."""
    before = window._setup_page
    before_scroll = before._scroll
    window._on_appearance_changed("dark")
    for _ in range(8):
        styled_qapp.processEvents()
    assert window._setup_page is before, "페이지가 다시 만들어졌다"
    assert window._setup_page._scroll is before_scroll


def test_live_widgets_take_the_new_colors(window, styled_qapp):
    """살아 있는 위젯이 실제로 새 색을 입는다 — 안 그러면 '안 만들었다'만 참이다."""
    window._on_appearance_changed("dark")
    for _ in range(8):
        styled_qapp.processEvents()
    assert theme.COLOR_MODE == "dark"
    # 상태바 크레딧은 인라인 스타일이라 따로 칠해야 하는 자리다.
    assert theme.MUTE.lower() in window._credit_label.styleSheet().lower()


def test_recolor_refreshes_the_logo_pixmap(window, styled_qapp):
    """★ 로고는 **픽스맵 반전**이라 QSS 로 못 바꾼다 — 다시 칠하지 않으면
    다크 화면에 어두운 마크가 그대로 남아 안 보인다."""
    logos = [w for w in window.findChildren(QLabel)
             if w.property("role") == "appLogo" and w.pixmap() is not None
             and not w.pixmap().isNull()]
    if not logos:
        pytest.skip("로고 파일을 읽지 못하는 환경")
    before = logos[0].pixmap().toImage()
    window._on_appearance_changed("dark")
    for _ in range(8):
        styled_qapp.processEvents()
    after = logos[0].pixmap().toImage()
    assert after != before, "로고가 옛 색 그대로다 — refresh_all 이 빠졌다"


def test_recolor_path_handles_what_qss_cannot():
    """소스 계약 — QSS 로 못 바꾸는 둘을 손보는 호출이 남아 있어야 한다."""
    src = inspect.getsource(mw.MainWindow._recolor_in_place)
    assert "apply_to_app" in src
    assert "refresh_all" in src, "로고 갱신이 빠졌다"
    assert "_apply_statusbar_theme" in src, "상태바 갱신이 빠졌다"


def test_page_recreation_machinery_is_gone():
    """되살아나면 안 된다 — 되살리면 전환이 다시 느려진다(124ms)."""
    src = inspect.getsource(mw.MainWindow)
    assert "def _recreate_pages" not in src
    from aoi_verification.app.ui.pages import setup_page as sp
    page_src = inspect.getsource(sp.SetupPage)
    for gone in ("def capture_draft", "def restore_draft"):
        assert gone not in page_src, (
            f"{gone} 부활 — 재생성이 없으면 입력값을 옮겨 심을 이유가 없다")


def test_no_baked_theme_colors_in_page_stylesheets():
    """★ 페이지가 색을 다시 굽기 시작하면 그 위젯만 옛 색으로 남는다.

    인라인 ``setStyleSheet`` 에 ``theme.<색>`` 을 f-string 으로 넣지 않는다 —
    색은 style.qss 의 role 규칙이 준다.
    """
    import re
    from pathlib import Path

    root = Path(inspect.getfile(mw)).resolve().parent / "pages"
    offenders = []
    for f in sorted(root.glob("*.py")):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"setStyleSheet\((.{0,240}?)\)\n", text, re.S):
            blob = m.group(1)
            if re.search(r"\{theme\.(BG|PANEL|ELEV|INK|INK2|MUTE|LINE|LINE2|"
                         r"ACCENT|PASS|WARN|DANGER)\b", blob):
                line = text[:m.start()].count("\n") + 1
                offenders.append(f"{f.name}:{line}")
    assert not offenders, (
        "인라인 스타일에 테마 색을 구웠다 — style.qss 의 role 로 옮겨라: "
        + ", ".join(offenders))
