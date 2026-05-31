"""회사 보안 정책 — 빌드/실행 환경에 금지된 도구가 끼어 있는지 검사.

검사 항목:
  (a) 설치된 패키지 메타데이터에 금지 이름이 있는지(pip 로 들어왔는지).
  (b) 현재 Python 자체가 금지 환경(Anaconda/Miniconda/Spyder)에서 온 건지
      (sys.prefix / sys.executable 경로 검사).
하나라도 걸리면 exit 1.  bat/스크립트는 ``pip install`` 직후 이 검사를 호출한다.
"""

from __future__ import annotations

import re
import sys

_BAD_NAMES = {
    "spyder", "spyder-kernels",
    "conda", "anaconda", "miniconda",
    "anaconda-client", "conda-build",
}
_PATH_RE = re.compile(r"\b(?:ana|mini)?conda\b|\bspyder\b", re.IGNORECASE)


def _installed_offenders() -> list[str]:
    try:
        import importlib.metadata as md
    except Exception:
        return []
    out: list[str] = []
    for dist in md.distributions():
        name = ((dist.metadata.get("Name") if dist.metadata else "") or "").strip().lower()
        if not name:
            continue
        if name in _BAD_NAMES:
            out.append(name)
    return sorted(set(out))


def _env_offender_paths() -> list[str]:
    """현재 인터프리터/환경 경로에 금지 키워드가 들어 있으면 그 경로를 반환."""
    paths = [getattr(sys, "prefix", ""), getattr(sys, "executable", ""),
             getattr(sys, "base_prefix", "")]
    return [p for p in paths if p and _PATH_RE.search(p)]


def main() -> int:
    pkgs = _installed_offenders()
    paths = _env_offender_paths()
    if not pkgs and not paths:
        print("[OK] 금지 패키지·환경 없음.")
        return 0
    sys.stderr.write("[금지] 회사 보안 정책 위반 — 빌드를 중단합니다.\n")
    if pkgs:
        sys.stderr.write(f"  · 설치된 금지 패키지: {pkgs}\n")
        sys.stderr.write(
            f"  · 현재 Python: {sys.executable}\n"
            "  · 조치(아래 명령을 그대로 실행):\n")
        for name in pkgs:
            sys.stderr.write(f"      python -m pip uninstall -y {name}\n")
        sys.stderr.write(
            "  · 무엇이 끌어들였는지 확인하려면(Required-by 항목 보기):\n")
        for name in pkgs:
            sys.stderr.write(f"      python -m pip show {name}\n")
        sys.stderr.write(
            "  · 비어 있지 않으면 requirements.txt 의 해당 의존을 검토하세요.\n"
            "    (보통은 과거 시스템 Python 에 남은 잔재 — 제거 후 재실행하면 통과)\n")
    if paths:
        sys.stderr.write(
            "  · Python 환경 경로에 금지 키워드 — 다른 Python(공식 설치본)으로 실행하세요:\n")
        for p in paths:
            sys.stderr.write(f"      {p}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
