#!/usr/bin/env python3
"""설명서 HTML → PDF **두 개**.

    python scripts/internal/make_manual_pdf.py

    dev/사용설명서_자료/사용설명서.html  → docs/사용설명서.pdf    핵심판
    dev/사용설명서_자료/상세설명서.html  → docs/상세설명서.pdf    완전판

핵심판은 **한 번의 검증을 끝내는 길만** 담고, 완전판은 전 기능을 담는다("설명서가 너무
길다"는 요청으로 갈라 낸 것이다).  스타일은 `설명서_공통.css` 하나를 둘이 나눠 쓴다 —
복사본을 만들면 두 문서가 서서히 다른 종이처럼 보인다.

**PDF 만 고칠 수는 없다.**  고치는 순서는 늘 이렇다:

    1) python scripts/internal/capture_manual_shots.py   화면 캡처 + shots.json
    2) dev/사용설명서_자료/*.html 수정 (앱 동작이 바뀌면 **둘 다**)
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
ASSETS = REPO / "dev" / "사용설명서_자료"
# (원본, 결과) — 순서가 곧 출력 순서다.
DOCS = [
    (ASSETS / "사용설명서.html", REPO / "docs" / "사용설명서.pdf"),
    (ASSETS / "상세설명서.html", REPO / "docs" / "상세설명서.pdf"),
]
# 두 문서에 **똑같이** 실리는 대목.  핵심판 9장 = 완전판 12장(상황별 표)이며, 한쪽만
# 고치면 현장에서 서로 다른 안내를 읽게 된다 — `_check_shared` 가 대조해 빌드를 세운다.
_SHARED_SECTION = ("이럴 땐 이 버튼", "9", "12")

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

    ★ 두 문서를 **합쳐서** 본다.  핵심판이 안 쓰는 캡처를 완전판이 쓰고 있을 수 있으니
      '남는 캡처' 판정은 둘 다 확인한 뒤에 해야 한다.
    """
    import re

    used: set[str] = set()
    for src, _ in DOCS:
        html = src.read_text(encoding="utf-8")
        found = set(re.findall(r'<img\s+src="([^"]+)"', html))
        missing = sorted(n for n in found if not (ASSETS / n).exists())
        if missing:
            have = sorted(p.name for p in ASSETS.iterdir()
                          if p.suffix in (".png", ".jpg"))
            raise SystemExit(
                f"{src.name} 이 가리키는 그림이 없습니다: " + ", ".join(missing)
                + "\n현재 있는 그림: " + ", ".join(have)
                + "\n→ capture_manual_shots.py 를 다시 실행하거나 본문의 확장자를 맞추세요.")
        used |= found
    spare = sorted(p.name for p in ASSETS.iterdir()
                   if p.suffix in (".png", ".jpg") and p.name not in used)
    if spare:
        print("쓰이지 않는 캡처: " + ", ".join(spare))


def _check_stylesheet() -> None:
    """두 문서가 **같은 CSS 하나**를 가리키는지 — 그리고 그 파일이 있는지.

    Chromium 은 스타일시트를 못 찾아도 조용히 기본 글꼴로 인쇄한다.  여기서 막지 않으면
    '멀쩡히 만들어졌는데 아무 서식도 없는 PDF' 가 나온다."""
    import re

    for src, _ in DOCS:
        html = src.read_text(encoding="utf-8")
        links = re.findall(r'<link\s+rel="stylesheet"\s+href="([^"]+)"', html)
        if len(links) != 1:
            raise SystemExit(
                f"{src.name} 의 스타일시트 링크가 {len(links)}개입니다(정확히 1개여야 함).")
        css = (src.parent / links[0])
        if not css.exists():
            raise SystemExit(f"{src.name} 이 가리키는 스타일시트가 없습니다: {css}")


def _check_shared() -> None:
    """두 문서에 똑같이 실리는 대목이 **글자까지 같은지** 대조한다.

    ★ 왜 대조하는가 — 상황별 표는 현장에서 가장 많이 들추는 부분이라 양쪽에 다 넣었다.
      복제는 언젠가 어긋나고, 어긋난 설명서 두 벌은 없느니만 못하다.  한쪽만 고치면
      여기서 빌드가 서고 **어느 쪽을 안 고쳤는지** 알려 준다."""
    title, core_n, full_n = _SHARED_SECTION
    core = _section_body(DOCS[0][0], core_n, title)
    full = _section_body(DOCS[1][0], full_n, title)
    if core != full:
        raise SystemExit(
            f"‘{title}’ 대목이 두 설명서에서 다릅니다 "
            f"(사용설명서 {core_n}장 vs 상세설명서 {full_n}장).\n"
            "→ 한쪽만 고치셨습니다.  같은 내용이 되도록 나머지 한쪽도 맞춰 주세요.")


def _section_body(src: Path, number: str, title: str) -> str:
    """``<h2><span class="n">N</span>제목</h2>`` 절의 첫 ``<h3>`` 부터 ``</section>`` 까지."""
    html = src.read_text(encoding="utf-8")
    head = f'<h2><span class="n">{number}</span>{title}</h2>'
    if head not in html:
        raise SystemExit(f"{src.name} 에서 ‘{number}장 {title}’ 를 찾지 못했습니다.")
    start = html.index(head)
    end = html.index("</section>", start)
    body = html[start:end]
    return body[body.index("<h3>"):].strip()


def main() -> int:
    for src, _ in DOCS:
        if not src.exists():
            raise SystemExit(f"원본 HTML 이 없습니다: {src}")
    _check_images()
    _check_stylesheet()
    _check_shared()

    chrome = _chromium()
    for src, out in DOCS:
        cmd = [
            chrome, "--headless", "--no-sandbox", "--disable-gpu",
            "--no-pdf-header-footer",
            # 저장소 파일을 상대경로로 읽어야 글꼴·그림·스타일시트가 붙는다.
            "--allow-file-access-from-files",
            f"--print-to-pdf={out}",
            src.as_uri(),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if not out.exists():
            sys.stderr.write(proc.stderr[-2000:] + "\n")
            raise SystemExit(f"PDF 생성 실패: {out.name}")

        size = out.stat().st_size
        if size < 200_000:
            raise SystemExit(
                f"{out.name} 이 너무 작습니다({size} bytes) — 그림이 빠졌을 수 있습니다.")
        # ★ 글꼴이 실제로 박혔는지 — 스타일시트를 못 읽으면 Chromium 은 조용히 기본
        #   글꼴로 인쇄한다.  '만들어졌다' 는 성공의 증거가 아니다.
        if b"NanumSquare" not in out.read_bytes():
            raise SystemExit(
                f"{out.name} 에 NanumSquare 가 박히지 않았습니다 — "
                "설명서_공통.css 의 글꼴 상대경로를 확인하세요.")
        print(f"{out}  ({size / 1e6:.1f} MB, {_pages(out)} 쪽)")
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
