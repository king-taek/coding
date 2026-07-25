#!/usr/bin/env python3
"""설정 화면 가로 여백 개선 **후보안 5개**를 갈아 끼우며 직접 써 보는 도구.

사용법 (저장소 루트에서):

    python scripts/try_layout.py            # 지금 어느 안이 적용돼 있는지
    python scripts/try_layout.py B          # B 안 적용
    python scripts/try_layout.py off        # 원상 복구(안 적용 없음)

적용 후 앱을 띄워서 확인하고, 마음에 안 들면 다른 안을 바로 넣으면 된다
(``off`` 를 거치지 않아도 알아서 이전 안을 걷어낸다).

각 안의 성격·실측·알려진 결함은 ``dev/layout_variants/README.md`` 참조.
채택할 안을 정하면 그 patch 를 정식 커밋으로 옮기고 이 폴더는 지우면 된다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VARIANT_DIR = ROOT / "dev" / "layout_variants"
STATE = VARIANT_DIR / ".applied"          # 지금 적용된 안 (gitignore 대상)

NAMES = {
    "A": "두 카드를 가로로 나란히",
    "B": "카드 내부를 반응형 2열로  (채점 1위 8.28)",
    "C": "본문 전체를 576px 단일 컬럼으로",
    "D": "라벨 왼쪽 / 값 오른쪽 축 정렬",
    "E": "본론 열 + 오른쪽 264px 보조 레일",
}


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args],
                          capture_output=True, text=True, check=check)


def _applied() -> str | None:
    if STATE.exists():
        v = STATE.read_text(encoding="utf-8").strip()
        return v or None
    return None


def _touched_paths() -> set[str]:
    """후보안 patch 들이 건드리는 파일 목록."""
    touched = set()
    for p in VARIANT_DIR.glob("*.patch"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith("+++ b/"):
                touched.add(line[6:])
    return touched


def _conflicting_edits(cur: str | None) -> list[str]:
    """**후보안이 건드리는 파일**을 직접 손댔는지 — 그것만 막는다.

    ★ 무관한 파일이 더러운 것은 막지 않는다.  이 도구는 `git apply` 로 해당 파일만
    건드리므로 다른 수정은 애초에 위험하지 않고, 그걸 막으면 정상 사용까지 멈춘다
    (실제로 그렇게 만들었다가 아무 안도 못 넣는 상태가 됐다)."""
    touched = _touched_paths()
    dirty = []
    for ln in _git("status", "--porcelain").stdout.splitlines():
        path = ln[3:].strip()
        if path in touched:
            dirty.append(path)
    if cur:
        # 안이 적용된 상태라면 그 파일들이 더러운 게 정상이다.
        applied = set()
        for line in (VARIANT_DIR / f"{cur}.patch").read_text(
                encoding="utf-8").splitlines():
            if line.startswith("+++ b/"):
                applied.add(line[6:])
        dirty = [p for p in dirty if p not in applied]
    return dirty


def _revert(cur: str) -> None:
    patch = VARIANT_DIR / f"{cur}.patch"
    r = _git("apply", "-R", str(patch), check=False)
    if r.returncode != 0:
        print(f"[!] {cur} 안을 되돌리지 못했다 — 파일을 직접 고쳤을 수 있다.\n"
              f"    {r.stderr.strip()}\n"
              f"    `git checkout -- .` 로 통째 복구한 뒤 다시 시도해라.")
        sys.exit(1)
    STATE.unlink(missing_ok=True)


def main() -> int:
    if not VARIANT_DIR.exists():
        print(f"[!] {VARIANT_DIR} 가 없다.")
        return 1

    arg = (sys.argv[1].strip().upper() if len(sys.argv) > 1 else "")
    cur = _applied()

    if not arg:
        print(f"현재 적용: {cur or '없음(원본)'}")
        print("\n후보안:")
        for k, desc in NAMES.items():
            mark = " ←" if k == cur else ""
            print(f"  {k}  {desc}{mark}")
        print("\n  python scripts/try_layout.py B     # 적용")
        print("  python scripts/try_layout.py off   # 원상 복구")
        return 0

    if arg not in NAMES and arg != "OFF":
        print(f"[!] 모르는 안: {arg!r}. A~E 또는 off.")
        return 1

    stray = _conflicting_edits(cur)
    if stray:
        print("[!] 후보안이 건드리는 파일을 직접 수정했다 — 덮어쓰지 않으려고 멈춘다:")
        for f in stray:
            print(f"      {f}")
        print("    `git checkout -- <파일>` 로 되돌린 뒤 다시 시도해라.")
        return 1

    if cur:
        _revert(cur)
        print(f"    {cur} 안 해제")

    if arg == "OFF":
        print("원본 상태로 돌아왔다.")
        return 0

    patch = VARIANT_DIR / f"{arg}.patch"
    r = _git("apply", str(patch), check=False)
    if r.returncode != 0:
        print(f"[!] {arg} 적용 실패:\n{r.stderr.strip()}")
        return 1
    STATE.write_text(arg, encoding="utf-8")
    print(f"✓ {arg} 안 적용 — {NAMES[arg]}")
    print("\n  이제 앱을 띄워 확인해라:  python main.py")
    print("  다른 안:  python scripts/try_layout.py <A~E>")
    print("  원상복구: python scripts/try_layout.py off")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
