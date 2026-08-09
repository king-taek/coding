"""``ko.py`` 에 **아무도 부르지 않는 문구**가 남아 있지 못하게 한다.

배경(실측): 전수 검수에서 참조 0건인 키가 5개 나왔다 —
``SLOT_MAP_OPEN`` · ``INFO_PHASE_TRANSITION_TITLE`` · ``INFO_PHASE_A_TO_MATCH`` ·
``CANCELLED_NOTE`` 계열.  기능을 지우면서 문구만 남은 자리들이다.  죽은 문구는 그
자체로는 무해해 보이지만, **없는 기능을 설명하는 문서**가 되어 다음 사람이 그것을
사실로 읽는다(실제로 `CANCELLED_NOTE` 의 주석이 존재하지 않는 생산자
`result_page._on_review_matches` 를 가리키고 있었다).

두 단계로 본다:
  1. 저장소 어디에서도 부르지 않는 키는 없다 — 동적 조회는 아래에 적어 예외로 둔다.
  2. **앱 코드**가 부르지 않고 테스트만 부르는 키는 '휴면' 이며, 왜 남겨 두는지
     여기에 이유를 적어야 통과한다.
"""
from __future__ import annotations

import pathlib
import re

from aoi_verification.app import i18n

_ROOT = pathlib.Path(__file__).resolve().parents[2]

# `getattr(i18n.KO, …)` 로 이름을 조립해 쓰는 자리 — grep 으로는 안 보인다.
# 여기 추가하려면 **어디가 어떻게 조립하는지** 를 함께 적어라.
_DYNAMIC = {
    # select_page.py: getattr(i18n.KO, f"PANEL_{name.upper()}_EMPTY")
    "PANEL_RIGHT_EMPTY",
}

# 앱 코드가 부르지 않는 '휴면' 키 — 지우지 않고 남겨 두는 이유를 적는다.
_DORMANT = {
    "CANCELLED_NOTE":
        "‘매칭 취소’ 표시를 note 에 남기던 생산자가 사라졌다(result_page._on_review_"
        "matches 는 저장소에 없다).  소비자(_is_cancelled·구분선)는 살아 있어 생산자가"
        " 돌아오면 문자열 하나로 다시 이어진다 — ko.py 주석에 같은 사실을 적어 두었다.",
}


def _key_names() -> list[str]:
    return [n for n in dir(i18n.KO)
            if n.isupper() and isinstance(getattr(i18n.KO, n), str)]


def _sources(*globs: str) -> str:
    out = []
    for g in globs:
        for p in _ROOT.glob(g):
            if "__pycache__" in str(p):
                continue
            out.append(p.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(out)


_APP = ("aoi_verification/**/*.py", "scripts/**/*.py", "scripts/**/*.spec",
        "main.py")
_TESTS = ("dev/tests/**/*.py",)


def test_every_key_is_referenced_somewhere() -> None:
    text = _sources(*_APP, *_TESTS)
    # ko.py 자신은 세지 않는다 — 정의는 참조가 아니다.
    text = text.replace(
        (_ROOT / "aoi_verification/app/i18n/ko.py").read_text(encoding="utf-8"),
        "")
    dead = [n for n in _key_names()
            if n not in _DYNAMIC and not re.search(r"\b" + n + r"\b", text)]
    assert dead == [], (
        "부르는 곳이 없는 문구가 ko.py 에 남아 있다 — 기능을 지웠다면 그 문구도 함께\n"
        "지워야 한다(동적 조회라면 이 파일의 _DYNAMIC 에 근거와 함께 적어라):\n  "
        + "\n  ".join(dead))


def test_keys_only_tests_use_are_declared_dormant() -> None:
    app_text = _sources(*_APP)
    app_text = app_text.replace(
        (_ROOT / "aoi_verification/app/i18n/ko.py").read_text(encoding="utf-8"),
        "")
    unused = {n for n in _key_names()
              if n not in _DYNAMIC and not re.search(r"\b" + n + r"\b", app_text)}
    undeclared = sorted(unused - set(_DORMANT))
    assert undeclared == [], (
        "앱 코드가 부르지 않는 문구다(테스트만 부른다).  지우거나, 남긴다면\n"
        "이 파일의 _DORMANT 에 이유를 적어라:\n  " + "\n  ".join(undeclared))
    # 반대 방향 — 되살아난 키가 '휴면' 목록에 남아 거짓말을 하지 않게.
    stale = sorted(set(_DORMANT) - unused)
    assert stale == [], (
        f"_DORMANT 에 적힌 키가 다시 앱에서 쓰인다 — 목록을 지워라: {stale}")


def test_the_removed_dead_keys_stay_removed() -> None:
    """참조 0건이라 지운 키들 — 되살아나면 이유를 함께 적어야 한다."""
    for name in ("SLOT_MAP_OPEN", "INFO_PHASE_TRANSITION_TITLE",
                 "INFO_PHASE_A_TO_MATCH"):
        assert not hasattr(i18n.KO, name), (
            f"{name} 이 되살아났다 — 부르는 화면이 정말 생겼는지 확인하라")


def test_the_guard_would_catch_a_dead_key() -> None:
    """가드가 늘 통과하는 빈 껍데기가 아님을 스스로 증명한다."""
    text = _sources(*_APP, *_TESTS)
    assert not re.search(r"\bTHIS_KEY_IS_NOWHERE\b", text)
