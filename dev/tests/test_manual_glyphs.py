"""설명서가 쓰는 글자가 **인쇄물에 실제로 찍히는지** 못 박는다.

배경(실제 사고): 캡션의 번호 마커 ❶❷❸(U+2776~2778)가 PDF 에서 **통째로 사라져
있었다.**  텍스트 레이어에는 있어서 `pdftotext` 로는 추출되는데, 220dpi 로 확대해
보면 그 자리가 빈 칸이다.

원인은 동봉한 `NanumSquare` 다 — 그 코드포인트를 **cmap 에 갖고 있으면서 글리프가
0바이트**다.  Chromium 은 cmap 에 있으니 다른 글꼴로 폴백하지 않고, 폭만 차지한
빈 칸을 찍는다.  '글꼴에 없는 글자' 보다 나쁘다: 없으면 폴백이라도 되는데,
있다고 거짓말하는 글리프는 조용히 사라진다.

그래서 표지가 약속한 "① 아래 번호 설명과 짝" 이 본문에서 성립하지 않았다 —
독자는 그림의 동그라미 번호가 아래 어느 문장을 가리키는지 알 방법이 없었다.

여기서 세우는 불변식: **설명서 본문이 쓰는 문자는 NanumSquare 에서 외곽선이 있거나,
아예 cmap 에 없어(→ 폴백) 한다.**  '있는데 비어 있는' 것만 금지한다.
"""
from __future__ import annotations

import io
import re
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANUAL = ROOT / "dev" / "사용설명서_자료"
FONTS = [ROOT / "aoi_verification/app/ui/assets/font/NanumSquareR.ttf",
         ROOT / "aoi_verification/app/ui/assets/font/NanumSquareB.ttf"]


def _tables(d: bytes) -> dict[str, tuple[int, int]]:
    n = struct.unpack(">H", d[4:6])[0]
    out = {}
    for i in range(n):
        o = 12 + 16 * i
        tag = d[o:o + 4].decode("latin1")
        off, ln = struct.unpack(">II", d[o + 8:o + 16])
        out[tag] = (off, ln)
    return out


def _cmap4(d: bytes, tabs) -> int | None:
    co = tabs["cmap"][0]
    n = struct.unpack(">H", d[co + 2:co + 4])[0]
    sub = None
    for i in range(n):
        pid, eid, off = struct.unpack(">HHI", d[co + 4 + 8 * i:co + 12 + 8 * i])
        if (pid, eid) in ((3, 1), (3, 10), (0, 3)):
            sub = co + off
    if sub is None or struct.unpack(">H", d[sub:sub + 2])[0] != 4:
        return None
    return sub


def _gid(d: bytes, sub: int, cp: int) -> int:
    segx2 = struct.unpack(">H", d[sub + 6:sub + 8])[0]
    seg = segx2 // 2
    ends = [struct.unpack(">H", d[sub + 14 + 2 * i:sub + 16 + 2 * i])[0] for i in range(seg)]
    sb = sub + 16 + segx2
    starts = [struct.unpack(">H", d[sb + 2 * i:sb + 2 + 2 * i])[0] for i in range(seg)]
    db = sb + segx2
    deltas = [struct.unpack(">h", d[db + 2 * i:db + 2 + 2 * i])[0] for i in range(seg)]
    rb = db + segx2
    ranges = [struct.unpack(">H", d[rb + 2 * i:rb + 2 + 2 * i])[0] for i in range(seg)]
    for i in range(seg):
        if starts[i] <= cp <= ends[i]:
            if ranges[i] == 0:
                return (cp + deltas[i]) & 0xFFFF
            gi = rb + 2 * i + ranges[i] + 2 * (cp - starts[i])
            g = struct.unpack(">H", d[gi:gi + 2])[0]
            return 0 if g == 0 else (g + deltas[i]) & 0xFFFF
    return 0


def _glyf_len(d: bytes, tabs, fmt_loca: int, g: int) -> int:
    lo = tabs["loca"][0]
    if fmt_loca == 0:
        a = struct.unpack(">H", d[lo + 2 * g:lo + 2 + 2 * g])[0] * 2
        b = struct.unpack(">H", d[lo + 2 + 2 * g:lo + 4 + 2 * g])[0] * 2
    else:
        a = struct.unpack(">I", d[lo + 4 * g:lo + 4 + 4 * g])[0]
        b = struct.unpack(">I", d[lo + 4 + 4 * g:lo + 8 + 4 * g])[0]
    return b - a


def _empty_glyph_codepoints(font: Path) -> set[int]:
    """cmap 에 **있는데** 글리프가 비어 있는 코드포인트 — 조용히 안 찍히는 것들."""
    d = font.read_bytes()
    tabs = _tables(d)
    sub = _cmap4(d, tabs)
    if sub is None or "loca" not in tabs or "glyf" not in tabs:
        pytest.skip(f"{font.name}: format4 cmap / glyf 아님")
    fmt_loca = struct.unpack(">h", d[tabs["head"][0] + 50:tabs["head"][0] + 52])[0]
    out = set()
    for cp in _manual_codepoints():
        g = _gid(d, sub, cp)
        if g and _glyf_len(d, tabs, fmt_loca, g) == 0:
            out.add(cp)
    return out


_TAGS = re.compile(r"<[^>]+>")
_ENT = re.compile(r"&[a-z]+;|&#\d+;")


def _manual_codepoints() -> set[int]:
    cps: set[int] = set()
    for name in ("사용설명서.html", "상세설명서.html"):
        text = io.open(MANUAL / name, encoding="utf-8").read()
        text = _ENT.sub(" ", _TAGS.sub(" ", text))
        cps |= {ord(ch) for ch in text if ord(ch) > 0x7F}
    return cps


@pytest.mark.parametrize("font", FONTS, ids=lambda p: p.name)
def test_manual_uses_no_silently_blank_glyph(font: Path) -> None:
    if not font.exists():
        pytest.skip(f"{font.name} 없음")
    bad = _empty_glyph_codepoints(font)
    assert bad == set(), (
        "설명서가 쓰는 글자 중 이 글꼴이 **빈 글리프로 갖고 있는** 것이 있다 — "
        "인쇄물에서 그 자리가 통째로 빈 칸이 된다(폴백도 안 된다):\n  "
        + "\n  ".join(f"U+{c:04X} {chr(c)!r}" for c in sorted(bad))
        + "\n대안: CSS 로 그리거나(`.kn` 참조) 같은 글꼴에서 외곽선이 있는 글자를 쓴다.")


def test_the_guard_would_catch_the_real_case() -> None:
    """가드가 빈 껍데기가 아님을 스스로 증명한다 — 실제 사고 문자로 확인."""
    font = FONTS[0]
    if not font.exists():
        pytest.skip("글꼴 없음")
    d = font.read_bytes()
    tabs = _tables(d)
    sub = _cmap4(d, tabs)
    fmt_loca = struct.unpack(">h", d[tabs["head"][0] + 50:tabs["head"][0] + 52])[0]
    for cp in (0x2776, 0x2777, 0x2778):                 # ❶❷❸ — 사고를 낸 그 문자
        g = _gid(d, sub, cp)
        assert g, f"U+{cp:04X} 가 cmap 에서 사라졌다 — 이 가드의 전제가 바뀌었다"
        assert _glyf_len(d, tabs, fmt_loca, g) == 0, (
            f"U+{cp:04X} 에 외곽선이 생겼다 — 글꼴이 바뀐 것이니 이 테스트를 다시 보라")
    for cp in (0x2460, 0x2461, 0x2462):                 # ①②③ — 같은 글꼴에서 정상
        g = _gid(d, sub, cp)
        assert g and _glyf_len(d, tabs, fmt_loca, g) > 0
