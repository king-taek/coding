"""화면 문구는 `app/i18n/ko.py` 에만 산다 — 위젯에 한글을 박지 못하게 하는 영구 장치.

배경(U-12): 리터럴이 위젯에 흩어져 있으면 같은 말이 화면마다 조금씩 달라지고
(‘완료’ vs ‘검토 완료’), 문구를 고칠 때 한 곳을 빠뜨린다.  실제로 30곳이 그렇게
흩어져 있었다.  사람이 매번 기억하는 대신 여기서 막는다.

무엇을 **세지 않는가**:
  · 주석·독스트링 — 코드를 읽는 사람을 위한 것이지 화면에 나가지 않는다.
  · 로거 호출의 인자 — `app.log` 로 갈 뿐 사용자가 보지 않는다.
  · `i18n/` 자체 — 거기가 문구의 집이다.

**범위**: `ui/` **전체 + 아래 `_ALSO_CLEAN` 에 적은 파일들**.

`ui/` 만 보던 시절 이 가드는 사용자에게 실제로 보이는 한글 47건을 통째로 놓쳤다 —
자동 업데이트 로딩바 문구·실패 사유(`utils/updater.py`), OpenVINO 설치 결과
(`learning/openvino_installer.py`), 첫 실행 부트스트랩 콘솔(`utils/bootstrap.py`).
그중 `updater.py` 의 "준비 중…" 은 `ko.py` 에 `BTN_START_PREPARING` 으로 **이미
있었고**, "다운로드 중…" 은 같은 오버레이의 첫 문구(`UPDATE_DOWNLOADING`)와 어긋난
채 화면에서 바뀌었다.  그 셋을 i18n 으로 옮기고 가드 범위에 넣는다.

**아직 넣지 못한 곳**(넣으면 지금 실패한다 — 이유를 적어 둔다):
  · `workers/exporter.py` — 엑셀 시트·열 이름(‘결과’·‘미매칭’·‘기준 전용’).  표기를
    현장 양식과 맞춰야 해서 확인 전까지 건드리지 않기로 했다(같은 이유로 `ko.py` 의
    `SLOT_MISMATCH_SHEET`·`NOTE_UNMATCHED` 도 영문/옛 표기를 유지한다).
  · `learning/embedder_openvino.py` — 'Intel GPU 가속'·'OpenVINO 미설치' 등 5건.
    상태바 표시가 제거돼 **지금 이 문자열이 화면에 닿는 경로가 확인되지 않았다.**
    보이지 않는 것을 옮기면 '고쳤다'는 착각만 남으므로, 노출 경로를 찾은 뒤에 옮긴다.
글꼴 이름과 정규식도 화면 문구가 아니다.

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


# **파일·폴더 이름**은 화면 문구가 아니다 — 디스크에 있는 그 이름을 찾아야 하므로
# i18n 으로 옮길 수 없다(옮기면 번역이 파일을 못 찾게 만든다).  정확히 일치할 때만
# 봐준다 — 문장은 절대 여기 들어올 수 없다.
_FILE_NAMES = {
    "양식.xlsx", "사용설명서.pdf", "상세설명서.pdf", "체험하기.html",
}


def _korean_literals(path: pathlib.Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstring_ids(tree) | _logging_arg_ids(tree)
    hits = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in skip and HANGUL.search(node.value)
                and node.value not in _FILE_NAMES):
            hits.append((node.lineno, node.value[:60]))
    return hits


# `ui/` 밖이지만 **사용자에게 문구를 보이는** 파일 — 함께 깨끗해야 한다.
_ALSO_CLEAN = (
    "utils/updater.py",              # 로딩 오버레이 단계 문구 + 실패 사유
    "utils/bootstrap.py",            # 첫 실행 콘솔(온라인/lite 배포)
    "learning/openvino_installer.py",  # 설치 결과 사유(OPENVINO_INSTALL_FAILED_FMT)
)


def _scan_targets() -> list[pathlib.Path]:
    return sorted((_ROOT / "ui").rglob("*.py")) + [_ROOT / n for n in _ALSO_CLEAN]


def test_ui_code_has_no_hardcoded_korean() -> None:
    offenders = []
    for p in _scan_targets():
        assert p.is_file(), f"가드 대상 파일이 사라졌다: {p}"
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
