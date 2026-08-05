#!/usr/bin/env python3
"""설명서·체험판에 넣을 **합성** 결함 사진을 만든다.

설명서와 체험판은 사외로 나갈 수 있으므로 **실제 장비 사진을 넣지 않는다.**  대신
RDL 배선과 결함처럼 보이는 그림을 그려서 쓴다.  다만 아무 그림이나 넣으면 화면에 적힌
숫자와 앞뒤가 맞지 않으므로, **거리에 타당한 그림**이 되도록 다음 규칙을 지킨다:

- **허용 오차 안(일치)** — 기준과 검증이 *같은 결함*이다.  배선 무늬도 결함 모양도 같고,
  장비가 다른 만큼 밝기·대비·잡음·미세한 흔들림만 다르다.
- **허용 오차 초과** — 두 사진이 *다른 결함*이다.  그래서 사람이 확인해야 하고, 그림도
  실제로 달라 보여야 한다.  같은 그림에 숫자만 크게 적으면 "왜 초과인지" 를 설명하지
  못한다.
- **차순위 후보** — 1위보다 먼 다른 결함이므로 무늬와 결함 위치가 다르다.

같은 ``scene`` 번호는 **항상 같은 그림**을 준다(난수 씨앗 고정).  그래서 설명서 캡처와
체험판이 같은 장면을 말할 때 같은 그림을 보여 준다.
"""

from __future__ import annotations

import math

_SUBSTRATE = (173, 106, 60)          # 기판(배선보다 확실히 어둡게 — 아니면 무늬가 뒤집혀 보인다)
_TRACE = (238, 200, 158)             # 배선(구리) 면
_TRACE_EDGE = (34, 25, 20)           # 배선 외곽
_DEFECT = (46, 34, 27)               # 결함 덩어리


def _rng(seed: int):
    import numpy as np
    return np.random.default_rng(seed)


def _draw_traces(draw, rng, w: int, h: int) -> int:
    """가로 버스바 + 위아래로 뻗는 사선 배선.  실제 RDL 사진의 골격을 흉내낸다.

    끝이 둥근 **굵은** 손가락 모양이어야 배선처럼 보인다 — 가는 사선만 그으면 무늬지
    회로가 아니다.  그래서 뻗는 길이를 화면 안에서 끝나게 잡아 둥근 끝을 보여 준다.
    """
    bus_y = int(h * float(rng.uniform(0.44, 0.56)))
    bus_t = int(h * float(rng.uniform(0.075, 0.105)))
    pitch = int(w * float(rng.uniform(0.13, 0.18)))
    finger_t = int(pitch * float(rng.uniform(0.34, 0.44)))
    slant = float(rng.uniform(0.5, 1.0)) * (1 if rng.random() < 0.5 else -1)
    reach = int(h * float(rng.uniform(0.20, 0.27)))

    def finger(x0: int, up: bool) -> None:
        y0 = bus_y - bus_t // 2 if up else bus_y + bus_t // 2
        y1 = y0 - reach if up else y0 + reach
        x1 = int(x0 + slant * reach * (1 if up else -1))
        for width, color in ((finger_t + 9, _TRACE_EDGE), (finger_t, _TRACE)):
            draw.line([(x0, y0), (x1, y1)], fill=color, width=width, joint="curve")
            r = width // 2
            draw.ellipse([x1 - r, y1 - r, x1 + r, y1 + r], fill=color)

    for x in range(-int(abs(slant) * reach) - pitch * 2, w + pitch * 2, pitch):
        finger(x, True)
        finger(x + pitch // 2, False)

    draw.rectangle([-5, bus_y - bus_t // 2 - 6, w + 5, bus_y + bus_t // 2 + 6],
                   fill=_TRACE_EDGE)
    draw.rectangle([-5, bus_y - bus_t // 2, w + 5, bus_y + bus_t // 2], fill=_TRACE)
    return bus_y


def _draw_defect(draw, rng, cx: int, cy: int, size: int) -> None:
    """불규칙한 덩어리 — 겹친 타원 몇 개로 만든다(원처럼 반듯하면 결함으로 안 보인다)."""
    for _ in range(int(rng.integers(4, 7))):
        a = float(rng.uniform(0, math.tau))
        d = float(rng.uniform(0, size * 0.34))
        ex, ey = cx + d * math.cos(a), cy + d * math.sin(a)
        rx = size * float(rng.uniform(0.26, 0.5))
        ry = rx * float(rng.uniform(0.6, 1.25))
        draw.ellipse([ex - rx, ey - ry, ex + rx, ey + ry], fill=_DEFECT)


def defect_image(scene: int, *, machine: int = 0, px: int = 1000):
    """한 장면(=한 결함)의 사진.

    ``machine`` 이 다르면 **같은 결함을 다른 장비가 찍은 것**처럼 보인다 —
    무늬와 결함은 그대로, 밝기·대비·잡음·미세한 흔들림만 달라진다.
    """
    import numpy as np
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

    big = int(px * 1.5)
    rng = _rng(7919 * scene + 13)
    img = Image.new("RGB", (big, big), _SUBSTRATE)
    draw = ImageDraw.Draw(img)
    bus_y = _draw_traces(draw, rng, big, big)

    # 결함은 버스바 근처에 둔다 — 실제로도 배선이 만나는 곳에서 자주 나온다.
    cx = int(big * float(rng.uniform(0.32, 0.68)))
    cy = int(bus_y + big * float(rng.uniform(-0.05, 0.05)))
    _draw_defect(draw, rng, cx, cy, big * float(rng.uniform(0.045, 0.075)))

    # 장비 차이 — 같은 결함이라도 두 장비의 사진은 이만큼 다르다.
    mr = _rng(scene * 31 + machine * 977 + 5)
    img = img.rotate(float(mr.uniform(-1.2, 1.2)), resample=Image.BICUBIC,
                     translate=(int(mr.integers(-14, 15)), int(mr.integers(-14, 15))))
    left = (big - px) // 2
    img = img.crop((left, left, left + px, left + px))

    arr = np.asarray(img).astype("float32")
    # 표면 거칠기 — 도금면은 매끈하지 않다.  덩어리진 얼룩 + 미세한 알갱이를 겹친다.
    coarse = rng.normal(0, 1, (px // 12 + 2, px // 12 + 2)).astype("float32")
    coarse = np.asarray(Image.fromarray(coarse).resize((px, px), Image.BICUBIC))
    arr += (coarse * 17)[:, :, None]
    arr += mr.normal(0, 6.5, arr.shape)                       # 센서 잡음
    arr *= float(mr.uniform(0.93, 1.07))                      # 노출
    img = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))
    img = ImageEnhance.Contrast(img).enhance(float(mr.uniform(0.95, 1.12)))
    return img.filter(ImageFilter.GaussianBlur(0.6))
