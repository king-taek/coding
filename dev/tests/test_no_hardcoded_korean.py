"""화면 문구는 `app/i18n/ko.py` 에만 산다 — 위젯에 한글을 박지 못하게 하는 영구 장치.

배경(U-12): 리터럴이 위젯에 흩어져 있으면 같은 말이 화면마다 조금씩 달라지고
(‘완료’ vs ‘검토 완료’), 문구를 고칠 때 한 곳을 빠뜨린다.  실제로 30곳이 그렇게
흩어져 있었다.  사람이 매번 기억하는 대신 여기서 막는다.

무엇을 **세지 않는가**:
  · 주석·독스트링 — 코드를 읽는 사람을 위한 것이지 화면에 나가지 않는다.
  · 로거 호출의 인자 — `app.log` 로 갈 뿐 사용자가 보지 않는다.
  · `i18n/` 자체 — 거기가 문구의 집이다.

**범위는 `ui/` 뿐이다.**  `workers/exporter.py` 의 한글은 대부분 엑셀 시트·열
이름(‘결과’·‘미매칭’·‘기준 전용’)인데, 그 표기는 현장 양식과의 호환을 확인하기
전까지 건드리지 않기로 했다.  글꼴 이름과 정규식도 화면 문구가 아니다.
엑셀 표기를 옮기게 되면 그때 이 가드에 `workers/` 를 더한다.

새 문구가 필요하면 ko.py 에 키를 만들어 쓰면 된다.  이 테스트가 실패했다면
"테스트를 고칠까?" 가 아니라 "이 문구의 키를 어디에 둘까?" 가 맞는 질문이다.
"""
from __future__ import annotations

import ast
import pathlib
import re

HANGUL = re.compile(r"[가-힣]")
_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
_ROOT = pathlib.Path(__file__).resolve().parents[2] / "aoi_verification" / "app"


def _docstring_ids(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                out.add(id(body[0].value))
    return out


def _logging_arg_ids(tree: ast.AST) -> set[int]:
    """`_LOG.warning("…%s", exc)` 처럼 로거로 가는 문자열은 화면 문구가 아니다."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _LOG_METHODS):
            for sub in ast.walk(node):
                out.add(id(sub))
    return out


def _korean_literals(path: pathlib.Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstring_ids(tree) | _logging_arg_ids(tree)
    hits = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in skip and HANGUL.search(node.value)):
            hits.append((node.lineno, node.value[:60]))
    return hits


def test_ui_code_has_no_hardcoded_korean() -> None:
    offenders = []
    for p in sorted((_ROOT / "ui").rglob("*.py")):
        for lineno, text in _korean_literals(p):
            offenders.append(f"{p.relative_to(_ROOT.parent.parent)}:{lineno}: {text!r}")
    assert offenders == [], (
        "화면 코드에 한글 문구가 직접 박혀 있다 — app/i18n/ko.py 에 키를 만들어 쓰세요:\n  "
        + "\n  ".join(offenders)
    )


def test_the_guard_would_actually_catch_something(tmp_path) -> None:
    """가드가 늘 통과하는 빈 껍데기가 아님을 스스로 증명한다."""
    sample = tmp_path / "bad.py"
    sample.write_text(
        '"""독스트링의 한글은 괜찮다."""\n'
        'import logging\n'
        '_LOG = logging.getLogger("x")\n'
        '_LOG.warning("로그의 한글도 괜찮다: %s", 1)\n'
        'label = "화면에 나가는 한글"        # 이건 잡혀야 한다\n',
        encoding="utf-8")
    hits = _korean_literals(sample)
    assert [t for _, t in hits] == ["화면에 나가는 한글"]
