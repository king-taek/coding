"""설명서의 강조 상자가 **실제 위젯 좌표**에 앵커돼 있는지 못 박는다.

배경(실제 사고): 두 설명서 HTML 은 그림 위의 강조 상자·번호를 SVG 좌표로 직접
적는다.  그 값의 출처는 `shots.json`(캡처 스크립트가 위젯에서 뽑은 실좌표)이고,
HTML 주석에도 "좌표는 shots.json 의 위젯 실좌표" 라고 적혀 있다.  그런데 **복사해
넣은 숫자**라 화면이 움직이면 조용히 어긋난다.

이번 UI 개선에서 실제로 그렇게 됐다: 검토 화면에 표제가 생기면서 상단이 100px,
행이 33px 내려갔고, 결과 화면 요약 카드는 1200×156 에서 820×210 으로 바뀌었다.
`make_manual_pdf._check_images` 는 **그림이 있는지만** 보므로 빌드는 그대로 통과했고,
**강조 상자가 엉뚱한 곳을 가리키는 PDF 가 조용히 인쇄됐다** — 설명서에서 이건
틀린 그림과 같다(사용자는 상자가 가리키는 것을 누른다).

여기서 세우는 불변식: **HTML 의 모든 `<rect class="mark …">` 는 그 그림의
`shots.json` 사각형과 같아야 한다.**  여러 부품을 한 상자로 묶은 경우는
그 부품들의 합집합이어야 한다(예: KLA 확인 시트의 타일 4개).

화면이 바뀌면 `capture_manual_shots.py` 를 돌린 뒤 이 테스트가 알려 주는 좌표로
HTML 을 고치면 된다 — 눈으로 맞추지 마라.
"""
from __future__ import annotations

import io
import itertools
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANUAL = ROOT / "dev" / "사용설명서_자료"
DOCS = ("사용설명서.html", "상세설명서.html")

_FIGURE = re.compile(r"<figure[^>]*>(.*?)</figure>", re.S)
_IMG = re.compile(r'<img src="([^"]+)"')
_RECT = re.compile(
    r'<rect class="mark[^"]*"[^>]*?\bx="(-?[\d.]+)"\s+y="(-?[\d.]+)"\s+'
    r'width="([\d.]+)"\s+height="([\d.]+)"')


def _shots() -> dict:
    return json.loads(io.open(MANUAL / "shots.json", encoding="utf-8").read())


def _union(boxes) -> tuple[int, int, int, int]:
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)


def _allowed(meta: dict) -> set[tuple[int, int, int, int]]:
    """그 그림에서 그릴 수 있는 상자 — 부품 하나, 또는 부품 여럿의 합집합."""
    rects = [tuple(v) for v in meta.get("rects", {}).values()]
    out = set(rects)
    # 묶음 상자는 실무상 2~4개짜리다.  전수 조합은 부품이 20개면 폭발하므로 여기서 끊는다.
    for n in (2, 3, 4):
        for combo in itertools.combinations(rects, n):
            out.add(_union(combo))
    return out


def _annotated_figures():
    shots = _shots()
    by_file = {v["file"]: (k, v) for k, v in shots.items()}
    for doc in DOCS:
        src = io.open(MANUAL / doc, encoding="utf-8").read()
        for m in _FIGURE.finditer(src):
            block = m.group(1)
            rects = _RECT.findall(block)
            if not rects:
                continue
            img = _IMG.search(block)
            line = src[: m.start()].count("\n") + 1
            name = img.group(1) if img else None
            yield doc, line, name, by_file.get(name, (None, None))[1], rects


def test_every_highlight_box_matches_a_real_widget() -> None:
    problems: list[str] = []
    seen = 0
    for doc, line, name, meta, rects in _annotated_figures():
        if meta is None:
            problems.append(f"{doc}:{line} — shots.json 에 없는 그림 {name!r}")
            continue
        ok = _allowed(meta)
        for r in rects:
            seen += 1
            box = tuple(int(float(v)) for v in r)
            if box in ok:
                continue
            near = sorted(meta["rects"].items(),
                          key=lambda kv: abs(kv[1][0] - box[0]) + abs(kv[1][1] - box[1]))[:2]
            hint = " / ".join(f"{k}={v}" for k, v in near)
            problems.append(
                f"{doc}:{line} [{name}] 상자 {list(box)} 가 어느 위젯과도 맞지 않음 "
                f"— 가까운 것: {hint}")

    assert seen >= 30, f"상자를 {seen}개밖에 못 찾았다 — 정규식이 HTML 을 놓쳤다"
    assert problems == [], (
        "설명서의 강조 상자가 실제 화면 위젯을 가리키지 않는다.\n"
        "`python scripts/internal/capture_manual_shots.py` 를 돌린 뒤 shots.json 의\n"
        "좌표로 HTML 의 rect 를 고쳐라(눈으로 맞추지 말 것):\n  "
        + "\n  ".join(problems))


def test_the_guard_would_catch_a_moved_widget() -> None:
    """가드가 늘 통과하는 빈 껍데기가 아님을 스스로 증명한다."""
    meta = {"rects": {"버튼": [100, 200, 50, 30]}}
    ok = _allowed(meta)
    assert (100, 200, 50, 30) in ok
    assert (100, 210, 50, 30) not in ok, "10px 밀린 상자를 통과시킨다"


def test_manual_names_no_removed_screen() -> None:
    """삭제된 화면의 캡처를 설명서가 다시 부르지 않는다(U-10 매칭 결과 검토)."""
    shots = _shots()
    known = {v["file"] for v in shots.values()}
    missing: list[str] = []
    for doc in DOCS:
        src = io.open(MANUAL / doc, encoding="utf-8").read()
        for name in _IMG.findall(src):
            if name not in known:
                missing.append(f"{doc}: {name}")
    assert missing == [], (
        "설명서가 캡처 스크립트가 만들지 않는 그림을 가리킨다 — PDF 인쇄가 막힌다:\n  "
        + "\n  ".join(missing))


@pytest.mark.parametrize("doc", DOCS)
def test_manual_does_not_name_the_removed_dialog(doc: str) -> None:
    src = io.open(MANUAL / doc, encoding="utf-8").read()
    # 없어진 이유를 설명하는 한 곳(팁 상자)은 허용한다 — 사용자가 옛 화면을 찾을 때 필요하다.
    hits = src.count("매칭 결과 검토")
    assert hits <= 1, (
        f"{doc} 이 삭제된 [매칭 결과 검토] 를 {hits}곳에서 안내한다 "
        "— 없는 버튼을 누르라고 말하게 된다")
