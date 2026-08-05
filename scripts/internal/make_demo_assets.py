#!/usr/bin/env python3
"""체험판(docs/체험하기.html)에 넣을 자산을 만든다 — 글꼴 서브셋 + 사진 썸네일.

    python scripts/internal/make_demo_assets.py

체험판은 **파일 하나로 끝나야** 한다(사내 PC 에서 그냥 열어 보는 용도라 서버도, 인터넷도
없다).  그래서 글꼴과 사진을 base64 로 문서 안에 넣는데, 그대로 넣으면 수 MB 가 되므로
여기서 미리 줄인다.

- **글꼴**: 저장소에 동봉된 NanumSquare 를 체험판이 실제로 쓰는 글자만 남겨 woff2 로
  서브셋한다(전체 720 KB → 수십 KB).  글꼴을 통째로 넣지 않는 이유는 용량뿐이고,
  빼면 안 되는 이유는 **앱과 같은 글꼴이어야 화면이 같아 보이기** 때문이다.
- **사진**: 저장소의 실제 장비 사진(``docs/RDL4 LOT files…``)을 잘라 작게 줄인다.
  가짜 그림을 그리지 않는다 — 체험판에서 보는 것이 현장에서 보는 것과 같아야 한다.

결과는 체험판 문서 안의 ``<script id="assets">`` 자리에 **바로 박는다** — 중간 파일을
두면 둘이 어긋난 채 커밋될 수 있다.
"""

from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FONT_DIR = REPO / "aoi_verification" / "app" / "ui" / "assets" / "font"
SAMPLE = REPO / "docs" / "RDL4 LOT files(26년 05월, FDV)"
DEMO = REPO / "docs" / "체험하기.html"

# 체험판에 반드시 들어가는 글자 — 문서가 아직 없을 때(최초 생성)도 글꼴이 나오게 한다.
_BASE_TEXT = (
    "AOI 레시피 검증 기준 장비 검증 장비 최상위 폴더 호기 번호 폴더 선택 실행 옵션 "
    "자동화 수준 사진 직접 선택 모든 사진 자동 진행 범위 모든 슬롯 일부 슬롯만 "
    "매칭 설정 허용 오차 유사도 엔진 구형 사용 좌표 매칭 중 판정 기준 다크 모드 "
    "사용 방법 업데이트 확인 사진 정보 보기 검증 시작 준비 폴더를 선택해 주세요 예 "
    "스캔 슬롯 후보 선별 결정할 검증하기로 한 사진들 남은 되돌리기 제외 선택 종료 모드 "
    "단계 유사도 기반 기준 사진 장비 후보 매칭 없음 설정으로 크기 슬롯별 장수 "
    "검토 완료 유지 일치 초과 매치 실패 거리 판정 크게 보기 접기 한 줄 더 "
    "결과 성공 파일 위치 원본 화질로 넣기 전체 양식 수기 영역 포함 새 매칭 검토 "
    "엑셀로 저장 확인 취소 아니오 닫기 다음 이전 건너뛰기 안내 시작하기 "
    "이 화면은 실제 프로그램과 같은 색과 움직임을 씁니다 체험판이라 파일은 저장되지 않습니다 "
    "µm 장 개 쪽 % · — ← → ✓ ✕ × ↩ ▾ ▴ ◀ ▶ ⟳ 0123456789 "
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.,()[]{}/\\:;'\"_-+= "
)


def _subset(src: Path, text: str) -> str:
    from fontTools import subset

    opts = subset.Options()
    opts.flavor = "woff2"
    opts.desubroutinize = True
    opts.layout_features = ["*"]
    opts.notdef_outline = True
    font = subset.load_font(str(src), opts)
    subsetter = subset.Subsetter(options=opts)
    subsetter.populate(text=text)
    subsetter.subset(font)
    buf = io.BytesIO()
    subset.save_font(font, buf, opts)
    font.close()
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _thumb(src: Path, box: tuple[int, int, int, int], px: int, q: int) -> str:
    from PIL import Image

    img = Image.open(src).convert("RGB").crop(box).resize((px, px), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=q, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def main() -> int:
    text = _BASE_TEXT
    if DEMO.exists():
        # 이미 만든 체험판이 있으면 **그 안의 글자를 전부** 넣는다 — 글꼴에 없는 글자가
        # 생기면 그 글자만 다른 서체로 튀어 화면이 어긋난다.
        raw = DEMO.read_text(encoding="utf-8")
        raw = re.sub(r"data:[^\"')]+", "", raw)          # base64 덩어리는 뺀다
        text += raw

    fonts = {
        "regular": _subset(FONT_DIR / "NanumSquareR.ttf", text),
        "bold": _subset(FONT_DIR / "NanumSquareB.ttf", text),
    }

    ref_src = SAMPLE / "17호기" / "W7548304XYG4" / "255350.116718.c.-1759125033.1.jpeg"
    val_src = SAMPLE / "18호기" / "W7548304XYG4" / "255348.116718.c.473266639.1.jpeg"
    alt_src = SAMPLE / "17호기" / "W7548306XYA2" / "51211.183932.c.-1479751086.2.jpeg"
    cuts = [(0, 0), (330, 60), (660, 120), (120, 300), (450, 280), (680, 336)]

    shots: dict[str, str] = {}
    for i, (cx, cy) in enumerate(cuts, start=1):
        box = (cx, cy, cx + 700, cy + 700)
        shots[f"r{i}"] = _thumb(ref_src, box, 120, 72)
        shots[f"v{i}"] = _thumb(val_src, (cx + 8, cy + 6, cx + 708, cy + 706), 120, 72)
        shots[f"a{i}"] = _thumb(alt_src, box, 120, 72)
        # 가운데 큰 자리용 — 작은 썸네일을 늘리면 흐려서 실제 화면과 다르게 보인다.
        shots[f"m{i}"] = _thumb(ref_src, box, 300, 74)
    # 크게 보는 화면용 — 두 장만 큰 것으로.
    shots["big_ref"] = _thumb(ref_src, (0, 0, 700, 700), 460, 78)
    shots["big_val"] = _thumb(val_src, (8, 6, 708, 706), 460, 78)

    payload = json.dumps({"fonts": fonts, "shots": shots}, separators=(",", ":"))
    print(f"글꼴 R {len(fonts['regular']) / 1024:.0f} KB · B {len(fonts['bold']) / 1024:.0f} KB "
          f"· 사진 {len(shots)}장 {sum(len(v) for v in shots.values()) / 1024:.0f} KB")

    if not DEMO.exists():
        raise SystemExit(f"체험판 문서가 없습니다: {DEMO}")
    html = DEMO.read_text(encoding="utf-8")
    new, n = re.subn(
        r'(<script type="application/json" id="assets">).*?(</script>)',
        lambda m: m.group(1) + payload + m.group(2), html, count=1, flags=re.S)
    if n != 1:
        raise SystemExit('체험판에서 자산 자리(<script id="assets">)를 찾지 못했습니다.')
    DEMO.write_text(new, encoding="utf-8")
    print(f"{DEMO}  ({DEMO.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
