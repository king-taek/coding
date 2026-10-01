"""테마 밖에서 **색을 굽는** 코드가 다시 들어오지 못하게 막는다.

배경(실제 사고): 위젯층 세 곳이 `theme.X` 를 모듈/클래스 상수의 f-string 에 박아
넣었다.  그러면 색이 **import 시점**에 굳어 색의 단일 출처(QSS 토큰)를 벗어난다 —
당시 있던 다크 모드에서 [중지] 글자 대비가 2.19:1 로 무너진 것이 그 결과였다.  같은
자리에 현 팔레트에 없는 색(옛 네온 초록·앰버·다크 네이비)까지 함께 있었다.

`test_theme.py` 가 style.qss 를 지키고, 이 파일은 **파이썬 쪽**을 지킨다.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[2]
_UI = _REPO / "aoi_verification" / "app" / "ui"

# 현 팔레트에 없는데 코드에 살아 있던 값들 — 이름 없는 리터럴이라 색 가드를 빠져나갔다.
_DEAD_LITERALS = (
    "rgba(57, 255, 20",     # 옛 네온 초록 (다중 선택 타일 틴트)
    "rgba(57,255,20",
    "rgba(224, 163, 74",    # 옛 앰버 (Stage 1 인라인 선택 배경)
    "rgba(26,29,35",        # 옛 다크 네이비 (썸네일 확대 버튼)
    "rgba(106,166,255",     # 팔레트 밖 파랑 (슬롯 매핑 선택 배경)
)


def _py_files():
    return sorted(p for p in _UI.rglob("*.py") if p.name != "theme.py")


def _strip_comments_and_docstrings(text: str) -> str:
    """주석·독스트링은 사고 이력을 적어 두는 자리라 검사 대상이 아니다."""
    import io
    import tokenize

    out = []
    prev_type = tokenize.INDENT
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError):        # pragma: no cover
        return text
    for tok in toks:
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and prev_type in (
                tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE, tokenize.NL):
            continue                                       # 독스트링
        out.append(tok.string)
        if tok.type not in (tokenize.NL, tokenize.NEWLINE):
            prev_type = tok.type
    return "\n".join(out)


def test_no_dead_palette_literals_in_ui_code():
    hits = []
    for f in _py_files():
        body = _strip_comments_and_docstrings(f.read_text(encoding="utf-8"))
        for lit in _DEAD_LITERALS:
            if lit in body:
                hits.append(f"{f.relative_to(_REPO)}: {lit}")
    assert not hits, "현 팔레트에 없는 색 리터럴이 살아 있다:\n" + "\n".join(hits)


def test_theme_colors_are_not_baked_at_module_or_class_scope():
    """`theme.X` 를 함수 **밖**에서 대입하면 import 시점에 색이 굳는다.

    모듈 본문과 클래스 본문의 대입만 본다 — 함수 안은 호출 시점에 평가되므로 안전하다.
    (이 검사가 있었으면 `_SEL_STYLE`·`_LIST_SEL_QSS` 를 만들 때 바로 걸렸다.)
    """
    import ast

    def _refs_theme_color(node: ast.AST) -> bool:
        return any(
            isinstance(n, ast.Attribute) and n.attr.isupper() and len(n.attr) > 1
            and isinstance(n.value, ast.Name) and n.value.id == "theme"
            for n in ast.walk(node)
        )

    baked = []
    for f in _py_files():
        tree = ast.parse(f.read_text(encoding="utf-8"))
        scopes = [tree] + [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        for scope in scopes:
            for stmt in scope.body:
                if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and stmt.value is not None:
                    if _refs_theme_color(stmt.value):
                        baked.append(f"{f.relative_to(_REPO)}:{stmt.lineno}")
    assert not baked, (
        "모듈/클래스 본문에서 테마 색을 구웠다 — 호출 시점에 읽는 함수로 옮겨라:\n"
        + "\n".join(baked))


def test_new_qss_roles_exist():
    """QSS 로 옮긴 두 등급이 실제로 렌더된다(인라인 스타일로 되돌아가지 않게)."""
    theme = pytest.importorskip("aoi_verification.app.ui.theme")
    out = theme.render_qss((_UI / "style.qss").read_text(encoding="utf-8"))
    assert 'QFrame[role="card-soft"][inlineSelected="true"]' in out
    assert 'QToolButton[role="tileExpand"]' in out
    # 상호작용 컨트롤의 평상시 경계에 장식 전용 토큰(line2)을 쓰지 않는다.
    m = re.search(r'QToolButton\[role="tileExpand"\]\s*\{[^}]*\}', out)
    assert m and theme.LINE_STRONG in m.group(0)


def test_viewer_background_token_exists():
    """토큰이 빠지면 render_qss 가 KeyError 로 앱을 죽인다."""
    theme = pytest.importorskip("aoi_verification.app.ui.theme")
    assert theme.VIEWER_BG, "VIEWER_BG 비어 있음"
    theme.render_qss((_UI / "style.qss").read_text(encoding="utf-8"))
