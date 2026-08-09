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

**인쇄가 끝나면 캡처는 스스로 지운다**(`sweep_captures`).  PDF 는 자기완결적이라
그림이 이미 안에 박혀 있고, 캡처는 1) 로 언제든 다시 찍히는 재생성물이다 — 저장소에
6 MB 를 쌓아 둘 이유가 없다.  파일명만 ``사진목록.txt`` 로 남는다.  그래서 **1) 을
건너뛰고 3) 만 다시 돌릴 수는 없다** — 그림이 없으면 `_check_images` 가 막는다.

엔진은 저장소에 이미 선례가 있는 **헤드리스 Chromium 인쇄**다
(``docs/좌표_계산_검증_보고서.pdf`` 가 같은 방식으로 만들어졌다).  별도 파이썬 PDF
라이브러리를 requirements 에 넣지 않으려는 선택이기도 하다 — 설명서를 만들려고
배포 의존성을 늘리면 사용자 PC 의 업데이트가 그만큼 위험해진다.

한글 글꼴은 **저장소에 동봉된 NanumSquare** 를 HTML 의 ``@font-face`` 가 상대경로로
불러온다(빌드 서버에 한글 글꼴이 없어도 된다).  그래서 ``file://`` 로 열어야 한다.

**왜 CLI ``--print-to-pdf`` 가 아니라 CDP(``Page.printToPDF``) 인가**

쪽 꼬리말은 CSS 의 ``@page`` 여백 상자가 찍는다(``설명서_공통.css`` 맨 위 주석).
거기 들어갈 **문서명**은 두 설명서가 CSS 하나를 나눠 쓰기 때문에 CSS 에 박을 수 없고,
인쇄하는 쪽이 넣어야 한다 — 이 스크립트가 인쇄 직전 그 문서의 ``<title>`` 을
``--doc-title`` 로 넣는다.  CLI 인쇄에는 그 값을 넣을 통로가 없다.  덤으로 배경
인쇄·용지·여백을 기본값에 기대지 않고 명시하게 되고, 실패가 예외로 드러난다
(파일이 생겼는지로 짐작하지 않는다).

⚠ CDP 의 ``footerTemplate`` 은 **쓰지 않는다** — 인쇄 설정 여백에 그려지는 물건이라
  ``@page:first{margin:0}`` 을 줘도 **표지에까지 찍힌다**(여백·용지 파라미터 조합을
  바꿔 가며 네 번 인쇄해 확인했다).  ⚠ 그렇다고 CLI 에서 ``--no-pdf-header-footer``
  를 빼도 안 된다 — Chromium 기본 꼬리말이 날짜와 ``file:///…`` 전체 경로를 찍는다.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

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


class _WS:
    """CDP 를 나르는 최소 WebSocket 클라이언트 — 이 용도에 필요한 부분만 구현했다.

    ★ 외부 패키지를 쓰지 않으려고 직접 짰다(이 파일 첫머리의 선택과 같은 이유).
      규격상 **클라이언트가 보내는 프레임은 반드시 마스킹**해야 하고, 서버가 보내는
      프레임은 마스킹되지 않는다.  인쇄 결과(base64)는 수 MB 라 64비트 길이와
      이어붙인 프레임(continuation)을 둘 다 다뤄야 한다.
    """

    def __init__(self, url: str) -> None:
        u = urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=600)
        req = (f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode()}\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        head, self._rest = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise SystemExit("DevTools 연결에 실패했습니다:\n" + head.decode("latin1"))
        self._id = 0

    def _read(self, n: int) -> bytes:
        out, self._rest = self._rest[:n], self._rest[n:]
        while len(out) < n:
            chunk = self.sock.recv(min(1 << 20, n - len(out)))
            if not chunk:
                raise SystemExit("Chromium 이 DevTools 연결을 끊었습니다.")
            out += chunk
        return out

    def _recv(self) -> dict:
        while True:
            data = b""
            while True:
                b0, b1 = self._read(2)
                length = b1 & 0x7F
                if length == 126:
                    length = struct.unpack(">H", self._read(2))[0]
                elif length == 127:
                    length = struct.unpack(">Q", self._read(8))[0]
                op, payload = b0 & 0x0F, self._read(length)
                if op == 0x8:
                    raise SystemExit("Chromium 이 DevTools 연결을 닫았습니다.")
                if op == 0x9:                       # ping → pong
                    self._send(0xA, payload)
                    continue
                data += payload
                if b0 & 0x80:                       # FIN
                    break
            if data:
                return json.loads(data)

    def _send(self, op: int, payload: bytes) -> None:
        n = len(payload)
        head = bytes([0x80 | op])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 1 << 16:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask
                          + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def call(self, method: str, params: dict | None = None,
             session: str | None = None) -> dict:
        self._id += 1
        msg: dict = {"id": self._id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self._send(0x1, json.dumps(msg).encode())
        while True:                                 # 오가는 이벤트는 흘려보낸다
            m = self._recv()
            if m.get("id") == self._id:
                if "error" in m:
                    raise SystemExit(f"{method} 실패: {m['error']}")
                return m.get("result", {})


def _launch(chrome: str, log: Path, profile: Path) -> tuple[subprocess.Popen, str]:
    """헤드리스 Chromium 을 띄우고 DevTools 주소를 돌려준다.

    ★ stderr 를 파이프로 받지 마라 — Chromium 은 인쇄 중에도 로그를 계속 뱉는데
      우리가 읽어 주지 않으면 파이프(64KB)가 차서 **인쇄가 그 자리에서 멈춘다**.
      파일로 받아 두면 주소도 거기서 찾고, 실패했을 때 그대로 보여 줄 수 있다.
    """
    with log.open("wb") as errfile:                  # 자식이 물려받은 뒤엔 닫아도 된다
        proc = subprocess.Popen(
            [chrome, "--headless", "--no-sandbox", "--disable-gpu",
             # 저장소 파일을 상대경로로 읽어야 글꼴·그림·스타일시트가 붙는다.
             "--allow-file-access-from-files",
             "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=errfile)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        found = re.search(r"ws://\S+", log.read_text(errors="replace"))
        if found:
            return proc, found.group(0)
        if proc.poll() is not None:
            raise SystemExit("Chromium 이 곧바로 종료됐습니다:\n"
                             + log.read_text(errors="replace")[-2000:])
        time.sleep(0.1)
    proc.kill()
    raise SystemExit("Chromium 이 DevTools 주소를 내놓지 않았습니다:\n"
                     + log.read_text(errors="replace")[-2000:])


def _print_pdf(ws: _WS, src: Path, out: Path) -> None:
    """문서 하나를 인쇄한다 — 꼬리말 문서명을 넣고, 다 그려진 뒤에 찍는다."""
    target = ws.call("Target.createTarget", {"url": src.as_uri()})["targetId"]
    sid = ws.call("Target.attachToTarget",
                  {"targetId": target, "flatten": True})["sessionId"]
    # 로딩 완료는 **폴링으로** 본다 — 이벤트 순서에 기대면 이미 지나간 load 를
    # 영원히 기다리게 된다.  `complete` 는 그림까지 다 받았다는 뜻이고,
    # 동봉 글꼴은 `document.fonts.ready` 로 한 번 더 기다린다.
    deadline = time.monotonic() + 120
    while True:
        state = ws.call("Runtime.evaluate",
                        {"expression": "document.readyState",
                         "returnByValue": True}, session=sid)
        if state["result"]["value"] == "complete":
            break
        if time.monotonic() > deadline:
            raise SystemExit(f"{src.name} 이 2분 안에 다 열리지 않았습니다.")
        time.sleep(0.1)
    # 꼬리말 문서명 — 이 문서의 <title> 을 그대로 CSS 변수로 넣는다.
    title = ws.call("Runtime.evaluate", {
        "expression": "document.fonts.ready.then(() => {"
                      "document.documentElement.style.setProperty("
                      "'--doc-title', JSON.stringify(document.title));"
                      "return document.title})",
        "awaitPromise": True, "returnByValue": True}, session=sid)
    if not title["result"]["value"]:
        raise SystemExit(f"{src.name} 에 <title> 이 없습니다 — 꼬리말에 넣을 "
                         "문서명이 없습니다.")
    data = ws.call("Page.printToPDF", {
        # 용지·여백은 CSS `@page` 하나만 보게 한다(`@page:first` 의 표지 여백 0
        # 까지 맞아야 한다).  여기서 따로 주면 두 곳을 늘 맞춰 둬야 한다.
        "preferCSSPageSize": True,
        # 콜아웃 배경·표 얼룩말이 배경이라 반드시 켠다.
        "printBackground": True,
        # 꼬리말은 CSS 여백 상자가 찍는다 — 첫머리 주석 참조.
        "displayHeaderFooter": False,
    }, session=sid)["data"]
    out.write_bytes(base64.b64decode(data))
    # 인쇄에 실패하면 그 자리에서 멈추므로 닫는 것은 성공한 뒤로 충분하다
    # (실패 경로는 main 의 finally 가 브라우저를 통째로 내린다).
    ws.call("Target.closeTarget", {"targetId": target})


def _check_footer(pdf: Path) -> None:
    """꼬리말이 **매 쪽에 찍히고 표지에는 없는지** 확인한다.

    ``@page`` 여백 상자가 사라지거나 ``@page:first`` 규칙이 깨져도 PDF 는 멀쩡히
    만들어진다 — 조용한 실패라 여기서 본다.  ``pdftotext`` 가 없으면 건너뛴다
    (있으면 좋고 없어도 되는 검사다).
    """
    exe = shutil.which("pdftotext")
    total = _pages(pdf)
    if not exe or total == "?":
        print("  (pdftotext/pdfinfo 가 없어 꼬리말 검사는 건너뜁니다)")
        return

    def text(page: str) -> str:
        return subprocess.run([exe, "-f", page, "-l", page, str(pdf), "-"],
                              capture_output=True, text=True).stdout

    # 쪽 번호는 `12 / 28` 처럼 찍히지만 추출하면 사이 공백이 사라질 수 있다.
    mark = re.compile(rf"\d+\s*/\s*{total}\b")
    if not mark.search(text(total)):
        raise SystemExit(
            f"{pdf.name} 마지막 쪽에 쪽 번호가 없습니다 — 설명서_공통.css 의 "
            "`@page` 여백 상자(@bottom-left/@bottom-right)를 확인하세요.")
    if mark.search(text("1")):
        raise SystemExit(
            f"{pdf.name} 표지에 꼬리말이 찍혔습니다 — 설명서_공통.css 의 "
            "`@page:first` 에서 @bottom-left/@bottom-right 를 지웠는지 확인하세요.")


def main() -> int:
    for src, _ in DOCS:
        if not src.exists():
            raise SystemExit(f"원본 HTML 이 없습니다: {src}")
    _check_images()
    _check_stylesheet()
    _check_shared()

    chrome = _chromium()
    work = Path(tempfile.mkdtemp(prefix="manual-pdf-"))
    proc = None
    try:
        proc, ws_url = _launch(chrome, work / "chrome.log", work / "profile")
        ws = _WS(ws_url)
        for src, out in DOCS:
            _print_pdf(ws, src, out)

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
            _check_footer(out)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(work, ignore_errors=True)

    # ★ 두 PDF 가 **모두** 검증을 통과한 뒤에만 캡처를 치운다(sweep_captures 주석 참조).
    swept = sweep_captures(ASSETS)
    if swept:
        print(f"캡처 {len(swept)}장 삭제 — 이름은 사진목록.txt 에 남겼습니다"
              " (다시 인쇄하려면 capture_manual_shots.py 를 먼저 실행).")
    return 0


def sweep_captures(assets: Path) -> list[str]:
    """인쇄가 **끝난 뒤** 캡처(png·jpg)를 지우고 파일명만 ``사진목록.txt`` 로 남긴다.

    PDF 는 자기완결적이다 — 인쇄 시점에 그림이 PDF 안으로 복사돼 들어가므로, 원본
    캡처는 그 뒤로 아무도 참조하지 않는 중간 산출물이다.  게다가 손으로 만든 자료가
    아니라 `capture_manual_shots.py` 가 앱을 실제로 띄워 다시 찍어내는 **재생성물**이라
    저장소에 쌓아 둘 이유가 없다(21장 ≈ 6 MB).

    ★ 반드시 **두 PDF 가 모두 검증을 통과한 뒤**에 부른다.  인쇄 전에 지우면 그림 없는
      설명서가 나오고, 한쪽만 성공한 채 지우면 나머지 한쪽을 다시 만들 수 없다.
    ★ ``shots.json``·HTML·CSS 는 캡처가 아니라 **원본**이므로 건드리지 않는다.
    """
    shots = sorted(p for p in assets.iterdir() if p.suffix in (".png", ".jpg"))
    if not shots:
        return []
    names = [p.name for p in shots]
    (assets / "사진목록.txt").write_text(
        "# 화면 캡처 목록 — 캡처 파일은 지웠고 **파일명만** 남긴다.\n"
        "#\n"
        "# 이 그림들은 손으로 만든 자료가 아니라 **재생성물**이다.  실제 앱을 offscreen 으로\n"
        "# 띄워 찍는 `python scripts/internal/capture_manual_shots.py` 가 아래 이름 그대로\n"
        "# 다시 만들어 준다(같은 폴더의 shots.json 도 함께).\n"
        "#\n"
        "# 이 목록은 make_manual_pdf.py 가 인쇄를 끝낸 뒤 자동으로 갱신한다.\n"
        "# 사용자에게 내려가는 산출물은 docs/사용설명서.pdf · docs/상세설명서.pdf 이고,\n"
        "# 그림은 이미 그 PDF 안에 박혀 있다.  PDF 를 **다시 인쇄하려면** 위 캡처\n"
        "# 스크립트를 먼저 돌려야 한다 — 그림이 없으면 _check_images 가 인쇄를 막는다.\n"
        + "\n".join(names) + "\n", encoding="utf-8")
    for p in shots:
        p.unlink()
    return names


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
