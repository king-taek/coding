#!/usr/bin/env python3
"""사용 설명서 HTML → ``docs/사용설명서.pdf``.

    python scripts/internal/make_manual_pdf.py

**PDF 만 고칠 수는 없다.**  고치는 순서는 늘 이렇다:

    1) python scripts/internal/capture_manual_shots.py   화면 캡처 + shots.json
    2) dev/사용설명서_자료/사용설명서.html 수정
    3) python scripts/internal/make_manual_pdf.py        ← 이 스크립트

엔진은 저장소에 이미 선례가 있는 **헤드리스 Chromium 인쇄**다
(``docs/좌표_계산_검증_보고서.pdf`` 가 같은 방식으로 만들어졌다).  별도 파이썬 PDF
라이브러리를 requirements 에 넣지 않으려는 선택이기도 하다 — 설명서를 만들려고
배포 의존성을 늘리면 사용자 PC 의 업데이트가 그만큼 위험해진다.

한글 글꼴은 **저장소에 동봉된 NanumSquare** 를 HTML 의 ``@font-face`` 가 상대경로로
불러온다(빌드 서버에 한글 글꼴이 없어도 된다).  그래서 ``file://`` 로 열어야 한다.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "dev" / "사용설명서_자료" / "사용설명서.html"
OUT = REPO / "docs" / "사용설명서.pdf"

# 이 환경에 미리 깔린 Chromium → 없으면 PATH 에서 찾는다.
_CANDIDATES = [
    Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome"),
    Path("/opt/pw-browsers/chromium/chrome-linux/chrome"),
]


def _chromium() -> str:
    for p in _CANDIDATES:
        if p.exists():
            return str(p)
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("Chromium 을 찾지 못했습니다 — 헤드리스 인쇄에 필요합니다.")


def _check_images() -> None:
    """본문이 가리키는 그림이 **전부 있는지** 확인하고, 남는 캡처도 알려 준다.

    캡처 스크립트는 화면마다 PNG/JPEG 중 작은 쪽을 고르므로 확장자가 바뀔 수 있다.
    Chromium 은 그림이 없어도 조용히 빈 자리로 인쇄하기 때문에, 여기서 먼저 막지 않으면
    **그림 없는 설명서가 조용히 만들어진다.**
    """
    import re

    html = SRC.read_text(encoding="utf-8")
    used = set(re.findall(r'<img\s+src="([^"]+)"', html))
    missing = sorted(n for n in used if not (SRC.parent / n).exists())
    if missing:
        have = sorted(p.name for p in SRC.parent.iterdir()
                      if p.suffix in (".png", ".jpg"))
        raise SystemExit(
            "본문이 가리키는 그림이 없습니다: " + ", ".join(missing)
            + "\n현재 있는 그림: " + ", ".join(have)
            + "\n→ capture_manual_shots.py 를 다시 실행하거나 본문의 확장자를 맞추세요.")
    spare = sorted(p.name for p in SRC.parent.iterdir()
                   if p.suffix in (".png", ".jpg") and p.name not in used)
    if spare:
        print("쓰이지 않는 캡처: " + ", ".join(spare))


def main() -> int:
    if not SRC.exists():
        raise SystemExit(f"원본 HTML 이 없습니다: {SRC}")
    _check_images()

    cmd = [
        _chromium(), "--headless", "--no-sandbox", "--disable-gpu",
        "--no-pdf-header-footer",
        # 저장소 파일을 상대경로로 읽어야 글꼴·그림이 붙는다.
        "--allow-file-access-from-files",
        f"--print-to-pdf={OUT}",
        SRC.as_uri(),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if not OUT.exists():
        sys.stderr.write(proc.stderr[-2000:] + "\n")
        raise SystemExit("PDF 생성 실패")

    size = OUT.stat().st_size
    if size < 200_000:
        raise SystemExit(f"PDF 가 너무 작습니다({size} bytes) — 그림이 빠졌을 수 있습니다.")
    print(f"{OUT}  ({size / 1e6:.1f} MB, {_pages(OUT)} 쪽)")
    return 0


def _pages(pdf: Path) -> str:
    """쪽수 — pdfinfo 가 있으면 쓰고, 없으면 물음표를 돌려준다(있어도 없어도 되는 정보)."""
    exe = shutil.which("pdfinfo")
    if not exe:
        return "?"
    out = subprocess.run([exe, str(pdf)], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("Pages:"):
            return line.split(":", 1)[1].strip()
    return "?"


if __name__ == "__main__":
    raise SystemExit(main())
