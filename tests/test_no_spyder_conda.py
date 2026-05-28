"""회사 보안 정책 가드 — 저장소 어디에도 금지 키워드가 들어오지 못하게 막는다.

스캔 대상은 텍스트 소스(코드/스크립트/문서/설정).  단어 경계로 매칭해 ``secondary``
같은 우연한 부분일치는 통과시킨다.  새 커밋이 이 키워드를 들여오면 이 테스트가
실패한다 — '간접적으로라도' 의 정책 가드 역할.

화이트리스트(자기 자신 + 런타임 가드 스크립트)는 의도적으로 키워드를 포함한다.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
# 단어 경계로 보호: '(ana|mini)?conda' 와 'spyder' (대소문자 무관).  TEXT_SECONDARY
# 같은 우연한 substring 은 통과(c 앞·a 뒤가 모두 단어문자라 \b 가 안 잡힘).
_PATTERN = re.compile(r"\b(?:ana|mini)?conda\b|\bspyder\b", re.IGNORECASE)
_TEXT_EXT = {".py", ".pyi", ".bat", ".md", ".txt", ".spec", ".ini", ".toml",
             ".cfg", ".yml", ".yaml", ".json", ".cmd", ".ps1"}
_EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
                 "build", "dist", "dist_portable", ".idea", ".vscode"}
# 이 테스트 자신과 런타임 가드 스크립트, 사고 기록 보고서는 키워드를 포함해야 하므로
# 화이트리스트.  (보고서는 정책 의도에 맞는 *의도된* 참조 — 코드/의존성과 무관.)
_WHITELIST = {Path(__file__).name, "verify_no_forbidden.py",
              "외부도구_탐지_보고서.md"}


def _scan_offenders() -> list[str]:
    hits: list[str] = []
    for p in _REPO.rglob("*"):
        if not p.is_file():
            continue
        if any(part in _EXCLUDE_DIRS for part in p.parts):
            continue
        if p.name in _WHITELIST:
            continue
        rel = p.relative_to(_REPO)
        # 파일 '이름' 자체에 금지 키워드가 들어 있으면 즉시 위반
        # (예: create_conda.py, conda.pyi, spyder_kernels.json).
        if _PATTERN.search(p.name):
            hits.append(f"{rel}: 파일명에 금지 키워드 포함")
            continue
        if p.suffix.lower() not in _TEXT_EXT:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _PATTERN.search(line):
                hits.append(f"{rel}:{lineno}: {line.strip()[:140]}")
    return hits


def test_no_forbidden_keywords_anywhere():
    hits = _scan_offenders()
    assert not hits, (
        "회사 보안 정책 위반 — 금지 키워드 참조 발견:\n  "
        + "\n  ".join(hits)
        + "\n(허용되지 않는 도구를 코드/문서/스크립트에서 참조하지 마세요.)"
    )
