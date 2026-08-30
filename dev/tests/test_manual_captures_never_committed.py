"""설명서 캡처가 저장소에 **쌓이지 않는지** 못 박는다.

저장소 정책은 "이미지를 쌓지 않는다 — 실제 사진은 지우고 이름만 `사진목록.txt` 에"
이고, 평소엔 `make_manual_pdf.sweep_captures` 가 인쇄 뒤 자동으로 치운다.

배경(실제로 그럴 뻔했다): 인쇄가 중간에 멈추면 캡처 18장(4.8 MB)이 그대로 남는데
`.gitignore` 에 규칙이 없어 `git add -A` 한 번이면 통째로 들어갔다.  **정책이
스크립트의 성공에만 기대고 있었다** — 스크립트가 실패하는 바로 그때 정책도 함께
무너지는 구조다.

여기서 세우는 두 겹:
  1. 추적 중인 파일에 캡처가 없다(이미 들어갔으면 잡는다).
  2. `.gitignore` 가 그 폴더의 이미지를 막는다(들어가려는 것을 미리 막는다).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANUAL = "dev/사용설명서_자료"
_IMG = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")


def _tracked() -> list[str]:
    # ★ `core.quotepath=false` 가 없으면 한글 경로가 `"dev/\354\202\254…"` 로 이스케이프돼
    #   돌아와 어떤 이름 비교도 성립하지 않는다(이 저장소는 경로가 전부 한글이다).
    # ★ `text=True` 만 주면 **로케일 인코딩**(한국어 Windows = cp949)으로 디코드한다.
    #   git 은 경로를 UTF-8 로 내보내므로 한글 폴더 이름에서 리더 스레드가
    #   UnicodeDecodeError 로 죽고, `run()` 은 그걸 삼킨 채 `stdout=None` 을 돌려준다
    #   — 그래서 이 테스트가 Windows 에서 늘 `'NoneType' has no attribute
    #   'splitlines'` 로 실패했다.  인코딩을 명시하면 OS 로케일과 무관해진다.
    out = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files", MANUAL],
                         cwd=ROOT, capture_output=True, check=True,
                         encoding="utf-8", errors="replace")
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def test_no_capture_is_tracked() -> None:
    bad = [p for p in _tracked() if p.lower().endswith(_IMG)]
    assert bad == [], (
        "설명서 캡처가 저장소에 커밋돼 있다 — 이름만 `사진목록.txt` 에 남기는 것이 정책이다:\n  "
        + "\n  ".join(bad))


def test_gitignore_blocks_captures() -> None:
    """스크립트가 실패해도 정책이 지켜지는지 — 실제로 `git check-ignore` 로 묻는다."""
    probe = ROOT / MANUAL / "_pytest_probe.png"
    probe.write_text("x", encoding="utf-8")
    try:
        r = subprocess.run(["git", "check-ignore", "-q", str(probe)], cwd=ROOT)
        assert r.returncode == 0, (
            f"`.gitignore` 가 {MANUAL} 의 이미지를 막지 않는다 — 인쇄가 중간에 실패해 "
            "캡처가 남은 상태에서 `git add -A` 하면 그대로 커밋된다")
    finally:
        probe.unlink(missing_ok=True)


def test_the_photo_list_is_tracked_instead() -> None:
    """대신 남기는 것 — 이름 목록은 반드시 추적돼야 한다."""
    names = _tracked()
    assert any(p.endswith("사진목록.txt") for p in names), (
        "`사진목록.txt` 가 추적되지 않는다 — 캡처를 지우고 남기는 유일한 기록이다")
