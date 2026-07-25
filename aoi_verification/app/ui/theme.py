"""전역 디자인 토큰 — 색·서체·구조의 단일 출처 ('도면' 디자인).

컨셉: **도면(Datum)** — 따뜻한 벨럼 제도 시트 위의 청사진 잉크.
검토 화면은 '카드 목록' 이 아니라 **치수가 적힌 제도 시트**로 읽히게 한다:
고정 컬럼 헤더(타이틀블록) · 하이라인 눈금 · 샤프 스탬프 판정 칩 · 모노 수치 ·
weight 300 / 자간 −1 의 얇은 표제.

구조:
- **PROFILE**(:class:`Profile`): 구조 파라미터(서체 크기·반경·밀도·컨트롤 높이 등).
  화면들이 생성 시점에 읽는다.
- 모듈 전역(BG/PANEL/…/ACCENT/…)은 인라인 f-string 스타일이 쓰는 색 상수.
- ``TOKENS``: ``style.qss`` 의 ``$token`` 치환용(색 + 틴트 + 서체 + 구조).
  ``render_qss`` 가 치환하고 ``apply_to_app`` 이 앱에 적용한다.

규칙: ACCENT(청사진 블루)는 주요 액션·현재 선택 **전용**, 상태는 PASS/DANGER/WARN,
정보 텍스트는 INK 계열. 한 화면에 강조 하나. 전 기능 색상쌍 WCAG AA 여유(≥5.0).
"""

from __future__ import annotations

from dataclasses import dataclass
from string import Template

from ..config import Fonts


# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Profile:
    """구조 파라미터(px 단위, 별도 표기 제외) — 화면이 생성 시점에 읽는다."""
    font_base: int = 14
    font_title: int = 26
    font_subtitle: int = 15
    font_caption: int = 12
    title_weight: int = 300         # 얇은 표제(제도 시트 성격)
    title_tracking: int = -1
    # 모서리 — '너무 각지다'는 지적에 따라 중간 라운드(8~10px)로. 계측기의 정밀함은
    # 얇은 표제·하이라인 눈금·모노 수치가 계속 담당한다.
    radius: int = 9
    radius_sm: int = 6
    chip_radius: int = 8            # 판정 칩도 각을 덜어낸다
    row_radius: int = 0             # 하이라인 행은 면이 없어 라운드가 보이지 않는다
    page_margin: int = 32
    section_gap: int = 28
    card_pad: int = 20
    row_pad_v: int = 8
    row_gap: int = 0                # 하이라인 눈금이 행을 가른다(간격 0)
    control_h: int = 32
    control_h_lg: int = 44          # 타일·액션바 — 터치 패널(WCAG 2.5.5 AAA 44px)
    control_pad_v: int = 6
    control_pad_h: int = 14
    # 클릭 타깃 하한 — WCAG 2.5.8(AA) 24px 을 **모든** 컨트롤이 넘어야 한다.
    # 밀집 컨트롤(체크박스 행·? 버튼·슬라이더)의 min-height 로 쓴다.
    target_min: int = 26
    input_h: int = 38               # 입력란 — 34px 이었고 필드가 눌리기에 얕았다
    check_sz: int = 18
    chip_w: int = 88
    chip_h: int = 20
    toggle_w: int = 52
    toggle_h: int = 30
    thumb_default_px: int = 118
    focus_ring_px: int = 2
    motion_scale: float = 0.8       # 제도 시트답게 담백한 모션


# ── 색 — 라이트/다크 두 팔레트.  구조(PROFILE)는 공유하고 색만 교체한다. ────────
#
# 다크는 라이트의 단순 반전이 아니다.  '도면' 의 야간판은 **청사진(cyanotype)** —
# 역사적으로 청사진은 짙은 청색 종이에 흰 선을 앉힌 것이다.  그래서 어두운 모드가
# 컨셉을 배신하지 않고 오히려 더 정통이 된다: 짙은 청색 바탕 + 밝은 잉크 + 밝힌 블루.
_LIGHT: dict[str, str] = {
    "bg": "#ECE9E2",        # 벨럼 바탕
    "panel": "#F5F3ED",     # 시트 면
    "elev": "#FBFAF7",
    "line": "#89836F",      # 제도 눈금 — 가장 옅은 면(elev)에서도 3:1 이상
    "line2": "#B7B2A6",     # 장식용 옅은 눈금(비-상호작용 전용)
    # 상호작용 컨트롤의 **평상시 경계** — WCAG 1.4.11(비-텍스트 3:1) 전용 토큰.
    # line2 로는 elev/panel/bg 어디에서도 2.0 을 못 넘겨 '보이지 않는 입력란'이 됐다.
    "line_strong": "#847F75",
    "ink": "#1B1A17",
    "ink2": "#3D3B35",
    "mute": "#5A574E",
    "accent": "#2C5A86",    # 청사진 블루 — 주요 액션·현재 선택 전용
    "accent_hover": "#356B9C",
    "accent_pressed": "#244B70",
    "on_accent": "#F5F3ED",
    "pass": "#3B6438",      # 제도 녹색
    "danger": "#A5271E",    # 정정 적색
    # ★ 이전 값 #1B1A17 은 ink 와 **바이트 단위로 같았다** — 경고 채널의 신호가
    #   0 이었고, 대비표는 그 쌍을 15.68 로 적어 표에서 가장 좋아 보이게 했다.
    #   제도 정정색(번트 시에나 계열)으로 분리한다: 면 대비 6.10/5.58, ink 와 2.57.
    "warn": "#8C4A0F",
    "focus": "#2B4C6F",
    "thumb_frame": "#A39D8F",   # 어두운 다이가 시트에 묻히지 않게
}

# ★ 채도 실측 지적 반영: 이전 bg(#0E1620)는 채도지표 0.071 로, 실제 청사진 원지의
#   1/3 밖에 안 됐다 — 이름만 청사진이고 눈에는 '어두운 슬레이트'였다.  0.22 대로
#   올려 진짜 청색 종이가 되게 한다(잉크·경계 대비는 전부 재측정해 게이트 통과).
_DARK: dict[str, str] = {
    "bg": "#12304C",        # 청사진 원지 — 채도지표 0.228
    "panel": "#1A3A59",     # 시트 면
    "elev": "#204465",
    "line": "#698DB0",      # 제도 눈금 — panel 3.37 / bg 3.89(구분선은 elev 를 지나지 않는다)
    "line2": "#33557A",     # 장식용 옅은 눈금(비-상호작용 전용)
    "line_strong": "#7EA2C4",   # 상호작용 경계 — elev 에서도 3.78:1
    "ink": "#EAF0F6",       # 백선(白線)
    "ink2": "#C3D0DC",
    "mute": "#A9BCCB",
    "accent": "#8EC2F0",    # 밝힌 청사진 블루(짙어진 원지 위에서 다시 띄운다)
    "accent_hover": "#A6D0F6",
    "accent_pressed": "#74AEDF",
    "on_accent": "#0B1F33",
    "pass": "#7FD79E",
    "danger": "#FFA9A1",
    "warn": "#F0D8A2",      # 라이트에선 정정색이지만 어두운 바탕에선 밝은 톤이어야 한다
    "focus": "#B4D6FF",
    "thumb_frame": "#6B8CAB",
}

# ── 두 번째 어두운 모드: **흑연(graphite)** ─────────────────────────────────────
#
# 청사진 다크가 '짙은 청색 종이 + 백선'(유채·차가움)이라면, 흑연 다크는 **같은 벨럼
# 시트의 불을 끈 것**(무채·따뜻함)이다.  둘은 색 온도와 채도가 반대라 서로의 변주가
# 아니라 다른 판단이고, 그러면서 둘 다 '도면' 안에 있다:
#   · 청사진 — 청색 감광지에 흰 선 (bg 채도지표 0.228)
#   · 흑연  — 어두운 제도지에 흑연 선, 청사진 블루는 잉크로만 (bg 채도지표 0.028)
# 웜-무채 바탕 위에서는 같은 블루 강조가 훨씬 유채로 읽혀, 강조 하나 원칙이 더 또렷해진다.
_GRAPHITE: dict[str, str] = {
    "bg": "#1C1A15",        # 불 끈 제도지
    "panel": "#26231C",
    "elev": "#302C24",
    "line": "#7C7565",      # 흑연 눈금 — panel 3.43 / bg 3.80
    "line2": "#3F3A31",     # 장식 전용
    "line_strong": "#8E8675",   # 상호작용 경계 — elev 에서도 3.85
    "ink": "#F3F0E8",       # 흑연 선(밝게)
    "ink2": "#D4CEC0",
    "mute": "#A8A192",
    "accent": "#8FBEEA",    # 청사진 블루 — 웜 무채 바탕에서 유일한 유채
    "accent_hover": "#A6CDF1",
    "accent_pressed": "#76A9DC",
    "on_accent": "#16140F",
    "pass": "#8ACB8E",
    "danger": "#FFA398",
    "warn": "#E9C478",
    "focus": "#ABCEF6",
    "thumb_frame": "#6F6859",
}

# ★ 키 `"dark"` 는 청사진에 그대로 둔다 — 기존 prefs(`color_mode:"dark"`)가 마이그레이션
#   없이 계속 동작한다.  새 모드만 새 키를 받는다.
PALETTES: dict[str, dict[str, str]] = {
    "light": _LIGHT, "dark": _DARK, "graphite": _GRAPHITE,
}
# 사용자에게 보이는 이름 — 스위처 순서 = 이 dict 순서(밝음 → 어두움).
COLOR_MODE_LABELS: dict[str, str] = {
    "light": "벨럼", "dark": "청사진", "graphite": "흑연",
}
DEFAULT_COLOR_MODE = "light"
COLOR_MODE = DEFAULT_COLOR_MODE

# LoadingOverlay 스크림 — 뒤 화면이 보이도록 옅게(모드별).
# ★ 이전 값(96/120)은 (a) 다크의 뒤 텍스트 대비를 4.43 으로 떨궈 자체 게이트 5.0 을
#   깼고 (b) 여유가 **적은** 다크에 오히려 더 두꺼운 디밍을 줘 거꾸로였다.
#   실측: light 84 → 7.07 · dark 96 → 5.67 (둘 다 게이트 통과).
_SCRIMS = {"light": (27, 26, 23, 84), "dark": (5, 16, 28, 96),
           "graphite": (11, 10, 8, 96)}     # 실측 뒤 텍스트 6.48 (게이트 5.0)

COLORS: dict[str, str] = dict(_LIGHT)   # 현재 모드의 색(set_color_mode 가 갱신)
SCRIM_RGBA = _SCRIMS[DEFAULT_COLOR_MODE]


def _rgb(hexv: str) -> tuple:
    h = hexv.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _tint(hexv: str, alpha: int) -> str:
    r, g, b = _rgb(hexv)
    return f"rgba({r}, {g}, {b}, {alpha})"


# ── 서체 (config.Fonts 재사용 — 타이포 정체성은 PROFILE 크기/굵기로) ──────────
FONT_BODY = Fonts.BODY
FONT_TITLE = Fonts.TITLE
FONT_MONO = Fonts.MONO

PROFILE = Profile()

# 인라인 f-string 스타일이 쓰는 색 상수 — set_color_mode() 가 일괄 갱신한다.
BG = PANEL = ELEV = LINE = LINE2 = LINE_STRONG = ""
INK = INK2 = MUTE = ""
ACCENT = ACCENT_HOVER = ACCENT_PRESSED = ON_ACCENT = ""
PASS = DANGER = WARN = FOCUS = THUMB_FRAME = ""
ACCENT_TINT = ACCENT_TINT_SOFT = PASS_TINT = ""
DANGER_TINT = DANGER_TINT_SOFT = WARN_TINT = ""


def _derive_tokens() -> dict:
    """QSS 토큰 dict (색 + 틴트 + 서체 + 구조)."""
    c = COLORS
    p = PROFILE
    ring = p.focus_ring_px
    # 포커스 링 두께만큼 패딩을 줄여 :focus 시 크기 점프 방지.
    comp = ring - 1
    return {
        # 색
        **c,
        # 틴트(베이스 색에서 파생 — 강조/상태와 항상 일치)
        "accent_tint": ACCENT_TINT, "accent_tint_soft": ACCENT_TINT_SOFT,
        "pass_tint": PASS_TINT, "danger_tint": DANGER_TINT,
        "danger_tint_soft": DANGER_TINT_SOFT, "warn_tint": WARN_TINT,
        # 서체
        "font_body": FONT_BODY, "font_title": FONT_TITLE, "font_mono": FONT_MONO,
        "font_base_sz": f"{p.font_base}px", "font_title_sz": f"{p.font_title}px",
        "font_sub_sz": f"{p.font_subtitle}px",
        "font_caption_sz": f"{p.font_caption}px",
        "title_weight": str(p.title_weight),
        "title_tracking": f"{p.title_tracking}px",
        # 형태
        "radius": f"{p.radius}px", "radius_sm": f"{p.radius_sm}px",
        "chip_radius": f"{p.chip_radius}px", "row_radius": f"{p.row_radius}px",
        # 컨트롤 패딩(+포커스 보정)
        "pad_control": f"{p.control_pad_v}px {p.control_pad_h}px",
        "pad_control_focus": f"{p.control_pad_v - comp}px {p.control_pad_h - comp}px",
        "pad_input": "7px 12px",
        "pad_input_focus": f"{7 - comp}px {12 - comp}px",
        "focus_w": f"{ring}px",
        "check_sz": f"{p.check_sz}px", "radio_sz": f"{p.check_sz - 2}px",
        # 클릭 타깃 — 밀집 컨트롤도 WCAG 2.5.8(AA, 24px)을 넘게.
        "target_min": f"{p.target_min}px",
        "input_h": f"{p.input_h}px",
        "control_h_lg": f"{p.control_h_lg}px",
        # 판정 칩 — 채운 '스탬프'(사각). 예외가 한눈에 띄게.
        "chip_bg_ok": PASS_TINT, "chip_bg_over": DANGER_TINT,
        "chip_border_none": c["line2"], "chip_pad": "2px 8px", "chip_ls": "0px",
        # 행 — 면 없이 하이라인 눈금만(제도 시트).
        "row_bg": "transparent", "row_border": "transparent",
        "row_divider": c["line"],
        # 점수 컬럼 눈금 — ink 반투명이라 시트 위에서 또렷.
        "score_rule": _tint(c["ink"], 55),
    }


TOKENS: dict[str, str] = {}


def set_color_mode(name: str) -> None:
    """색 모드를 ``"light"``/``"dark"`` 로 전환(미지 값은 기본 모드).

    모듈 전역과 ``TOKENS`` 를 일괄 갱신한다.  ``TOKENS`` 는 **in-place** 로 바꿔
    이미 참조를 들고 있는 쪽이 끊기지 않게 한다.

    주의: 위젯이 생성 시점에 ``theme.INK`` 같은 값을 f-string 으로 굽기 때문에, 이미
    만들어진 화면에 즉시 반영하려면 호출부가 **페이지를 다시 만들어야** 한다
    (``main_window`` 가 세션 시작 전에만 그렇게 한다)."""
    global COLOR_MODE, COLORS, SCRIM_RGBA
    global BG, PANEL, ELEV, LINE, LINE2, LINE_STRONG, INK, INK2, MUTE
    global ACCENT, ACCENT_HOVER, ACCENT_PRESSED, ON_ACCENT
    global PASS, DANGER, WARN, FOCUS, THUMB_FRAME
    global ACCENT_TINT, ACCENT_TINT_SOFT, PASS_TINT
    global DANGER_TINT, DANGER_TINT_SOFT, WARN_TINT

    mode = name if name in PALETTES else DEFAULT_COLOR_MODE
    COLOR_MODE = mode
    c = PALETTES[mode]
    COLORS = dict(c)
    SCRIM_RGBA = _SCRIMS[mode]

    BG, PANEL, ELEV = c["bg"], c["panel"], c["elev"]
    LINE, LINE2, LINE_STRONG = c["line"], c["line2"], c["line_strong"]
    INK, INK2, MUTE = c["ink"], c["ink2"], c["mute"]
    ACCENT, ACCENT_HOVER = c["accent"], c["accent_hover"]
    ACCENT_PRESSED, ON_ACCENT = c["accent_pressed"], c["on_accent"]
    PASS, DANGER = c["pass"], c["danger"]
    WARN, FOCUS = c["warn"], c["focus"]
    THUMB_FRAME = c["thumb_frame"]

    # ★ 알파 36 이면 선택 타일 **라벨**(accent)이 자기 틴트 위에서 4.66~4.87 로
    #   프로젝트 게이트(5.0)를 깬다 — 이전 대비표는 라벨-대-면만 재고 이 합성면
    #   쌍을 빼놨다.  24 로 낮추면 최악 5.11(라이트·다크 전부 통과).
    ACCENT_TINT = _tint(ACCENT, 24)
    ACCENT_TINT_SOFT = _tint(ACCENT, 20)
    PASS_TINT = _tint(PASS, 30)
    DANGER_TINT = _tint(DANGER, 28)
    DANGER_TINT_SOFT = _tint(DANGER, 15)
    WARN_TINT = _tint(WARN, 30)

    TOKENS.clear()
    TOKENS.update(_derive_tokens())


def color_mode_keys() -> tuple[str, ...]:
    return tuple(PALETTES.keys())


def render_qss(template_text: str) -> str:
    """``style.qss`` 템플릿의 ``$token`` 을 :data:`TOKENS` 로 치환.

    미정의 토큰은 KeyError 로 즉시 실패(오타 조기 노출 — ``safe_substitute`` 미사용).
    """
    return Template(template_text).substitute(TOKENS)


def apply_to_app(app) -> None:
    """style.qss 를 렌더해 앱 전체에 적용."""
    from pathlib import Path
    from ..utils import paths
    qss_path = paths.resource_path("aoi_verification/app/ui/style.qss")
    text = Path(qss_path).read_text(encoding="utf-8")
    app.setStyleSheet(render_qss(text))


# 모듈 로드 시 기본 모드 확정 — import 만 해도 전역·TOKENS 가 채워져 있다.
set_color_mode(DEFAULT_COLOR_MODE)
