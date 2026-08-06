#!/usr/bin/env python3
"""설명서·체험판에 넣을 **예시 그림**을 만든다 — 직물 짜임 위의 얼룩.

설명서와 체험판은 사외로 나갈 수 있으므로 **실제 검사 사진을 넣지 않는다.**  그렇다고
자재처럼 보이는 그림을 그리면 그것도 자재를 설명하는 셈이라, 아예 **직물** 로 그린다.
설명서가 가르치려는 것은 자재가 아니라 **판정을 읽는 법**이므로 소재는 무관하다.

다만 아무 그림이나 넣으면 화면에 적힌 숫자와 앞뒤가 맞지 않는다.  그래서 규칙을 지킨다:

- **얼룩은 항상 사진 중앙**이다.  두 장비 모두 자기가 찾은 결함을 화면 가운데 놓고 찍기
  때문이고, 덕분에 "같은 것인가" 를 위치가 아니라 **생김새**로 판단하게 된다.
- **같은 장면 = 같은 얼룩**.  짜임(색·굵기·조직)과 얼룩(모양·크기)이 같고, 두 장비의
  차이만큼 밝기·대비·잡음·미세한 흔들림이 다르다.
- **다른 장면 = 다른 얼룩**.  짜임도 얼룩도 눈에 띄게 다르다.  그래서 '허용 초과' 행에서
  두 사진을 나란히 놓으면 왜 사람이 봐야 하는지가 그림만으로 설명된다.

같은 ``scene`` 번호는 **항상 같은 그림**을 준다(난수 씨앗 고정).  그래서 설명서 캡처와
체험판이 같은 장면을 말할 때 같은 그림을 보여 준다.
"""

from __future__ import annotations

import math

# 실 색 후보 — **서로 확실히 구분되는** 다섯 가지.
#
# ★ 비슷한 색만 모아 두면 '다른 얼룩' 이어야 하는 두 사진이 나란히 놓였을 때 그냥
#   같아 보인다.  설명서가 "이래서 초과입니다" 라고 말하는 그림이 정작 똑같아 보이면
#   가르치는 데 실패한다(실제로 그랬다).  색상과 밝기를 둘 다 벌려 둔다.
# ★ 얼룩이 어두우므로 실은 중간~밝은 범위에 둔다 — 어두운 실 위에서는 얼룩이 안 보인다.
_YARNS = [
    ((214, 202, 180), (180, 166, 142)),   # 미표백 면 — 밝은 따뜻함
    ((142, 166, 194), (108, 132, 162)),   # 인디고 — 뚜렷한 파랑
    ((196, 138, 112), (162, 106, 84)),    # 테라코타 — 붉은 계열
    ((166, 184, 158), (130, 150, 122)),   # 세이지 — 초록 계열
    ((176, 178, 184), (140, 143, 150)),   # 슬레이트 — 무채색
]


def _rng(seed: int):
    import numpy as np
    return np.random.default_rng(seed)


def _weave(draw, rng, px: int, scene: int) -> None:
    """씨실·날실을 교차로 깔아 짜임을 만든다.

    한 줄씩 번갈아 위/아래로 지나가게 그려야 '겹쳐 짠 것' 으로 보인다 — 격자만 그으면
    체크무늬지 직물이 아니다.
    """
    # ★ 실 색은 난수가 아니라 **장면 번호로** 고른다.  난수로 두면 '다른 얼룩' 이어야
    #   하는 두 장면이 우연히 같은 색을 뽑아, 설명서가 "이래서 초과입니다" 라고 말하는
    #   그림이 정작 똑같아 보인다(실제로 그랬다).  ``//100`` 항이 있어 같은 자리의
    #   ref(0..5)와 '다른 얼룩'(900..905)이 절대 겹치지 않는다.
    warp, weft = _YARNS[(scene + scene // 100) % len(_YARNS)]
    # 실 간격도 장면마다 다르게 — 색만으로는 흑백 인쇄에서 구분이 사라진다.
    pitch = int(px * (0.050 + 0.010 * ((scene + scene // 100) % 4)))
    thick = max(3, int(pitch * float(rng.uniform(0.62, 0.80))))
    twill = int(rng.integers(0, 2))       # 0=평직, 1=능직(사문직 — 사선 결이 생긴다)

    cols = list(range(-pitch, px + pitch, pitch))
    rows = list(range(-pitch, px + pitch, pitch))

    def bar(x0, y0, x1, y1, fill):
        draw.rounded_rectangle([x0, y0, x1, y1],
                               radius=min(thick, abs(y1 - y0), abs(x1 - x0)) // 2,
                               fill=fill)

    # ① 날실(세로)을 끝까지 깐다 → ② 씨실(가로)로 전부 덮는다 → ③ 날실이 **위로**
    #    올라오는 교차점만 다시 그린다.  이 세 번째 단계가 '짠 것' 으로 보이게 하는
    #    전부다 — 없으면 가로줄이 세로줄을 통째로 덮어 줄무늬가 된다.
    for x in cols:
        bar(x, -pitch, x + thick, px + pitch, warp)
    for y in rows:
        bar(-pitch, y, px + pitch, y + thick, weft)
    for ri, y in enumerate(rows):
        for ci, x in enumerate(cols):
            over = ((ri + ci) % 2 == 0) if not twill else ((ci - ri) % 3 == 0)
            if over:
                # 씨실 두께보다 **길게** 잡는다 — 딱 맞게 그리면 정사각형이 돼
                # 둥근 점으로 보이고, 길게 빼야 실이 위로 지나가는 것으로 읽힌다.
                pad = max(2, pitch // 5)
                bar(x, y - pad, x + thick, y + thick + pad, warp)


def _stain(draw, rng, px: int) -> None:
    """중앙의 얼룩 — 번진 자국처럼 불규칙하게.

    사진 가운데 고정이므로 '같은 것인가' 의 단서는 **모양과 크기**가 전담한다.
    """
    cx = cy = px // 2
    size = px * float(rng.uniform(0.10, 0.17))
    shade = int(rng.integers(58, 96))
    tint = int(rng.integers(-10, 14))
    core = (shade + tint, shade, max(30, shade - 14))
    # 바깥 번짐 → 안쪽 진한 심 순서로 겹친다.
    for ring, alpha in ((1.55, 0.45), (1.18, 0.72), (0.85, 1.0)):
        col = tuple(int(c + (200 - c) * (1.0 - alpha)) for c in core)
        for _ in range(int(rng.integers(5, 9))):
            a = float(rng.uniform(0, math.tau))
            d = float(rng.uniform(0, size * 0.42 * ring))
            ex, ey = cx + d * math.cos(a), cy + d * math.sin(a)
            rx = size * ring * float(rng.uniform(0.30, 0.52))
            ry = rx * float(rng.uniform(0.65, 1.35))
            draw.ellipse([ex - rx, ey - ry, ex + rx, ey + ry], fill=col)


def defect_image(scene: int, *, machine: int = 0, px: int = 1000):
    """한 장면(=한 얼룩)의 사진.

    ``machine`` 이 다르면 **같은 얼룩을 다른 장비가 찍은 것**처럼 보인다 —
    짜임과 얼룩은 그대로, 밝기·대비·잡음·미세한 흔들림만 달라진다.
    """
    import numpy as np
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

    big = int(px * 1.35)
    rng = _rng(7919 * scene + 13)
    img = Image.new("RGB", (big, big), (196, 186, 168))
    draw = ImageDraw.Draw(img)
    _weave(draw, rng, big, scene)
    _stain(draw, rng, big)

    # 장비 차이 — 같은 얼룩이라도 두 장비의 사진은 이만큼 다르다.
    mr = _rng(scene * 31 + machine * 977 + 5)
    img = img.rotate(float(mr.uniform(-1.1, 1.1)), resample=Image.BICUBIC,
                     translate=(int(mr.integers(-10, 11)), int(mr.integers(-10, 11))))
    left = (big - px) // 2
    img = img.crop((left, left, left + px, left + px))

    arr = np.asarray(img).astype("float32")
    # 조명 얼룩 — 넓게 퍼진 밝기 차.  두 장비의 조명이 똑같을 수 없다.
    coarse = mr.normal(0, 1, (px // 14 + 2, px // 14 + 2)).astype("float32")
    coarse = np.asarray(Image.fromarray(coarse).resize((px, px), Image.BICUBIC))
    arr += (coarse * 11)[:, :, None]
    arr += mr.normal(0, 5.0, arr.shape)                       # 센서 잡음
    arr *= float(mr.uniform(0.94, 1.06))                      # 노출
    img = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))
    img = ImageEnhance.Contrast(img).enhance(float(mr.uniform(0.95, 1.10)))
    return img.filter(ImageFilter.GaussianBlur(0.4))
