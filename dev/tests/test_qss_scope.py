"""전역 QSS 는 **면을 칠하지 않는다** — 그러면 앱 전체가 느린 그리기 경로로 간다.

배경(#렉): `QWidget { background-color: … }` 처럼 모든 위젯에 매칭되면서 **면을 칠하는**
규칙이 있으면 Qt 는 앱의 위젯 전부를 `QStyleSheetStyle` 로 그린다.  네이티브 그리기
빠른 길이 꺼지고, 위젯 하나하나가 스타일시트의 모든 규칙과 대조된다.  타일이 수백 개인
화면(썸네일 그리드·매치 검토)에서 그 비용이 곱해져 스크롤이 끊겼다.

대신 **면은 창·시트·최상위 팝업만** 칠한다.  그 안의 위젯은 그 바탕이 비쳐 보인다.
그래서 여기서 고정하는 것은 두 가지다:

1. 전역 `QWidget` 규칙이 다시 `background-color` 를 갖지 않는다(성능 회귀 가드).
2. 그럼에도 화면에 **네이티브 회색 구멍이 생기지 않는다** — 페이지를 실제로 렌더해
   모서리 픽셀이 테마 바탕색인지 본다(시각 회귀 가드).  1번만 있으면 배경을 통째로
   잃는 반대 사고를 못 막는다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QColor                                  # noqa: E402
from PyQt6.QtWidgets import QMainWindow, QVBoxLayout, QWidget    # noqa: E402

from aoi_verification.app.ui import theme                        # noqa: E402

_QSS = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui"
        / "style.qss").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def qapp(styled_qapp):
    """테마 적용된 앱 — conftest 가 `setStyleSheet` 을 세션당 1회만 부른다."""
    return styled_qapp


def _strip_comments(text: str) -> str:
    out, i = [], 0
    while True:
        j = text.find("/*", i)
        if j < 0:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:j])
        k = text.find("*/", j)
        if k < 0:
            return "".join(out)
        i = k + 2


def _global_widget_block() -> str:
    """`QWidget { … }` (속성 선택자 없는 순수 전역 규칙) 의 본문."""
    for block in _strip_comments(_QSS).split("}"):
        head, _, body = block.partition("{")
        if head.strip() == "QWidget":
            return body
    raise AssertionError("전역 QWidget 규칙이 사라졌다")


def test_global_widget_rule_paints_no_surface():
    body = _global_widget_block()
    assert "background" not in body, (
        "전역 QWidget 이 다시 면을 칠한다 — 앱 전체가 스타일시트 그리기 경로로 떨어져 "
        "타일이 많은 화면의 스크롤이 끊긴다.  면은 창·시트·최상위 팝업만 칠하라.")
    # 상속되는 것(글자·서체)은 전역이 맞다 — 여기까지 지우면 색이 통째로 빠진다.
    assert "color:" in body and "font-family:" in body


def test_top_level_popups_still_carry_their_own_surface():
    """창 바탕이 닿지 않는 최상위 위젯 — 빠지면 네이티브 회색으로 뜬다."""
    for selector in ("QMainWindow, QDialog", "QMenu, QComboBox QAbstractItemView"):
        i = _QSS.find(selector)
        assert i >= 0, f"{selector} 규칙이 없다"
        body = _QSS[i:_QSS.index("}", i)]
        assert "background-color" in body, f"{selector} 가 면을 잃었다"


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_page_still_renders_on_the_theme_surface(qapp, mode):
    """설정 화면을 실제로 그려 **바탕이 테마 색**인지 본다(회색 구멍 방지).

    ★ 스타일시트를 **창에만** 건다(`QApplication.setStyleSheet` 금지).  앱 단위
    호출은 Qt 내부 스타일 캐시가 프로세스 수명 동안 누적돼 부를수록 비싸진다
    (conftest 주석: 21회 = 82초).  위젯 단위 시트는 그 위젯과 자식에 적용되므로
    여기서 검사하려는 범위와 정확히 같다."""
    from aoi_verification.app.ui.pages.setup_page import SetupPage
    theme.set_color_mode(mode)
    win = QMainWindow()
    win.setStyleSheet(theme.render_qss(_QSS))
    win.resize(1000, 700)
    body = QWidget(win)
    lay = QVBoxLayout(body)
    lay.setContentsMargins(0, 0, 0, 0)
    page = SetupPage()
    lay.addWidget(page)
    win.setCentralWidget(body)
    win.show()
    try:
        for _ in range(8):
            qapp.processEvents()
        img = win.grab().toImage()
        expected = QColor(theme.BG)
        # 가장자리 표본 — 페이지가 차지하지 않는 자리는 창 바탕이 그대로 보여야 한다.
        # ※ 오른쪽 위는 스크롤바가 앉는 자리라 제외한다(그 색은 스크롤바 스타일이다).
        for x, y in ((2, 2), (img.width() // 2, 2),
                     (2, img.height() - 3), (img.width() - 3, img.height() - 3)):
            got = QColor(img.pixel(x, y))
            assert got.name() == expected.name(), (
                f"{mode}: ({x},{y}) 바탕이 {got.name()} — 기대 {expected.name()}. "
                "전역 면을 없앤 자리를 창이 못 메우고 있다")
    finally:
        win.close()
        page.deleteLater()
        win.deleteLater()
        qapp.processEvents()
        theme.set_color_mode(theme.DEFAULT_COLOR_MODE)
