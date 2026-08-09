"""설명서 HTML 의 **구조가 깨지지 않았는지** 본다.

배경(실제 사고): 절 사이에 문단·상자를 끼워 넣다가 `</section>` 을 하나 더 남겼다.
Chromium 은 잘못 닫힌 태그를 조용히 복구해 인쇄하므로 **PDF 는 그럴듯하게 나온다** —
대신 그 절부터 쪽 나눔(`section{page-break-before:always}`)이 어긋나 엉뚱한 곳에서
쪽이 갈린다.  `_check_images`·`_check_shared` 는 구조를 보지 않는다.

두 번 냈으니 기계에 맡긴다.  깊은 검증이 아니라 **한 번 세면 되는 것들**만 센다.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

MANUAL = Path(__file__).resolve().parents[2] / "dev" / "사용설명서_자료"
DOCS = ("사용설명서.html", "상세설명서.html")

# 스스로 닫는 태그·void 요소를 뺀, 짝이 맞아야 하는 블록 태그.
_PAIRED = ("section", "figure", "table", "thead", "tbody", "ul", "ol", "div")


def _src(doc: str) -> str:
    return io.open(MANUAL / doc, encoding="utf-8").read()


@pytest.mark.parametrize("doc", DOCS)
@pytest.mark.parametrize("tag", _PAIRED)
def test_block_tags_are_balanced(doc: str, tag: str) -> None:
    src = _src(doc)
    opens = len(re.findall(rf"<{tag}\b", src))
    closes = len(re.findall(rf"</{tag}>", src))
    assert opens == closes, (
        f"{doc}: <{tag}> {opens}개 / </{tag}> {closes}개 — 짝이 맞지 않는다.\n"
        "Chromium 은 조용히 복구해 인쇄하지만 그 절부터 쪽 나눔이 어긋난다.")


@pytest.mark.parametrize("doc", DOCS)
def test_sections_nest_correctly(doc: str) -> None:
    """수를 세는 것만으로는 '닫고 열기' 를 못 잡는다 — 순서도 본다."""
    src = _src(doc)
    depth = 0
    for m in re.finditer(r"<section\b|</section>", src):
        depth += 1 if m.group(0).startswith("<section") else -1
        if depth < 0:
            line = src[: m.start()].count("\n") + 1
            pytest.fail(f"{doc}:{line} — 열리지 않은 </section> 을 닫는다")
        if depth > 1:
            line = src[: m.start()].count("\n") + 1
            pytest.fail(f"{doc}:{line} — <section> 이 중첩됐다(절 안에 절)")
    assert depth == 0, f"{doc}: 닫히지 않은 <section> 이 {depth}개 남았다"


@pytest.mark.parametrize("doc", DOCS)
def test_every_figure_keeps_its_caption_inside(doc: str) -> None:
    """캡션이 `figure` 밖으로 나가면 쪽 나눔이 그림과 캡션을 갈라놓는다(G3-01)."""
    src = _src(doc)
    stray = re.findall(r"</figure>\s*\n\s*<figcaption", src)
    assert not stray, (
        f"{doc}: `figcaption` {len(stray)}개가 `figure` 밖에 있다 — "
        "`page-break-inside:avoid` 가 캡션을 지키지 못해 다음 쪽에 홀로 찍힌다")


def test_the_guard_would_catch_an_extra_close_tag() -> None:
    """가드가 늘 통과하는 빈 껍데기가 아님을 스스로 증명한다."""
    broken = "<section>a</section>\n</section>\n<section>b</section>"
    depth, failed = 0, False
    for m in re.finditer(r"<section\b|</section>", broken):
        depth += 1 if m.group(0).startswith("<section") else -1
        if depth < 0:
            failed = True
            break
    assert failed, "여분의 </section> 을 통과시킨다"
