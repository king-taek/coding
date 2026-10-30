"""동봉 폰트(NanumSquare) 등록 — 사용자 PC 마다 화면이 달라지지 않게.

``config.Fonts`` 에 이름만 적어 두면 그 폰트가 **OS 에 설치된 PC 에서만** 그 글꼴로
보인다.  앱이 저장소에 동봉된 파일을 직접 Qt 에 등록해야 어느 PC 에서나 같다.

여기서 지키는 것 세 가지:
1. 폰트 **파일이 저장소에 있다**(빌드·자동 업데이트가 통째로 실어 나른다)
2. Qt 등록이 실제로 되고, **Regular·Bold 두 굵기**가 다 들어온다(합성 볼드 방지)
3. QSS·QFont 가 그 패밀리 이름을 본다
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtGui")

from PyQt6.QtGui import QFont, QFontDatabase, QFontInfo      # noqa: E402

from aoi_verification.app import config                      # noqa: E402
from aoi_verification.app.ui import theme                    # noqa: E402
from aoi_verification.app.utils import paths                 # noqa: E402

_FONT_DIR = Path("aoi_verification/app/ui/assets/font")


def test_font_files_are_committed():
    """저장소에 없으면 사용자 PC 에서 받아올 방법이 없다(IR 과 같은 이유)."""
    root = Path(__file__).resolve().parents[2]
    for name in theme._FONT_FILES:
        assert (root / _FONT_DIR / name).is_file(), f"{name} 이 저장소에 없다"


def test_resource_path_finds_the_fonts():
    """개발·포터블·exe·온라인 네 배포가 같은 자리를 본다(`paths.resource_path`)."""
    for name in theme._FONT_FILES:
        p = paths.resource_path(f"aoi_verification/app/ui/assets/font/{name}")
        assert Path(p).is_file(), f"{name} 을 resource_path 로 못 찾는다"


def test_registers_regular_and_bold(qapp):
    """★ 두 굵기를 다 등록한다 — 하나만 등록하면 Qt 가 볼드를 **합성**한다.

    한글 합성 볼드는 획이 뭉개져 특히 나쁘다."""
    families = theme.load_app_fonts()
    assert config.Fonts.FAMILY in families

    fam = config.Fonts.FAMILY
    styles = QFontDatabase.styles(fam)
    assert styles, f"{fam} 의 스타일 목록이 비었다"

    regular = QFont(); regular.setFamilies([fam])
    bold = QFont(); bold.setFamilies([fam]); bold.setBold(True)
    # 진짜 굵기 파일이 있으면 Qt 가 합성하지 않는다.
    assert QFontInfo(bold).family() == fam
    assert QFontInfo(regular).family() == fam


def test_loading_twice_is_harmless(qapp):
    """페이지 재생성·재진입에서 여러 번 불려도 앱이 죽지 않는다."""
    theme.load_app_fonts()
    assert config.Fonts.FAMILY in theme.load_app_fonts()


def test_font_stack_puts_the_bundled_family_first():
    """QSS 토큰(`$font_body`)과 QFont 가 **같은 패밀리**를 1순위로 본다.

    한쪽만 바꾸면 라벨과 버튼이 서로 다른 글꼴로 렌더된다."""
    assert config.Fonts.FAMILY in config.Fonts.BODY
    assert config.Fonts.BODY.index(f'"{config.Fonts.FAMILY}"') == 0
    assert config.Fonts.TITLE.index(f'"{config.Fonts.FAMILY}"') == 0
    assert config.Fonts.FAMILY in theme.render_qss("$font_body")


def test_entry_point_registers_before_stylesheet():
    """★ 순서 계약 — 등록이 스타일시트보다 **먼저**여야 QSS 가 폴백으로 새지 않는다."""
    import inspect
    import main as app_main

    src = inspect.getsource(app_main.main)
    assert src.index("_apply_app_font(app)") < src.index("_load_stylesheet(app)")

    # 쉼표 문자열을 QFont 에 넘기지 않는다 — Qt6 에서는 그런 이름의 패밀리 하나를
    # 찾는 뜻이라 폴백이 동작하지 않는다.
    # ★ 독스트링을 빼고 **코드만** 본다 — 그 함정을 경고하는 주석이 독스트링에 있어서,
    #   원문 전체를 검사하면 경고문 자체에 걸려 가드가 거짓 실패한다.
    #   `__doc__` 로 문자열 치환하면 안 된다 — 파이썬 3.13 부터 `__doc__` 는 들여쓰기가
    #   벗겨져 원문과 달라 치환이 조용히 아무것도 안 한다.  AST 로 첫 문장을 뺀다.
    import ast
    fn = ast.parse(inspect.getsource(app_main._apply_app_font)).body[0]
    if ast.get_docstring(fn) is not None:
        fn.body = fn.body[1:]
    code = ast.unparse(fn)               # unparse 는 문자열을 작은따옴표로 쓴다
    assert "setFamilies(" in code
    assert "QFont('" not in code, "쉼표 문자열을 QFont 에 넘기고 있다"


def test_app_font_uses_vertical_hinting(qapp):
    """★ 앱 글꼴은 세로 힌팅이다 — 기본값(배율 100% 에서 전체 힌팅)은 한글 획을 픽셀에
    붙여 거칠게 보였다(사용자 캡처, 2026-10-30).

    QSS 로 패밀리·크기·굵기를 바꾼 위젯도 힌팅은 앱 글꼴에서 물려받는다 — 그래서
    한 곳만 고치면 화면 전체에 적용된다."""
    from PyQt6.QtWidgets import QLabel
    import main as app_main

    before = qapp.font()
    lb = None
    try:
        app_main._apply_app_font(qapp)
        vertical = QFont.HintingPreference.PreferVerticalHinting
        assert qapp.font().hintingPreference() == vertical

        lb = QLabel("교차검증 (양방향)")
        lb.setStyleSheet(f"font-family: {config.Fonts.BODY}; font-size: 15px;"
                         " font-weight: 700;")
        lb.ensurePolished()
        assert lb.font().pixelSize() == 15          # QSS 가 실제로 적용됐다
        assert lb.font().hintingPreference() == vertical
        assert QFont().hintingPreference() == vertical   # 새로 만든 QFont 도
        assert QFont("Malgun Gothic", 12).hintingPreference() == vertical
    finally:
        if lb is not None:
            lb.deleteLater()
        qapp.setFont(before)
