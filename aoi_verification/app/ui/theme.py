"""전역 디자인 토큰 — 색·서체·구조의 단일 출처 (다중 변형 지원).

구조:
- **Profile**: 변형별 구조 파라미터(서체 크기·반경·밀도·컨트롤 높이·칩/행 스타일·
  모션 강도 등). 색만 바꾸는 게 아니라 레이아웃·타이포·형태까지 변형마다 다르게.
- **Variant**: (key, label, colors 16키, profile, scrim, shadow).
- **VARIANTS**: 삽입 순서 = 화면 스타일 스위처 표시 순서.
- ``set_variant(name)``: 모듈 전역(BG/PANEL/… + 틴트 + PROFILE + SCRIM/SHADOW)을
  일괄 재할당하고 ``TOKENS`` 를 **in-place** 갱신(보유 참조 유지). ``theme.BG`` 식
  속성 접근이 ~90곳이라 전역 재할당만으로 코드 무수정 반영.
- ``style.qss`` 는 ``$token`` 템플릿 — ``render_qss`` 가 TOKENS 로 치환. 색 + 구조
  토큰(서체 px·반경·패딩·칩·행)을 함께 공급해 QSS 로 구조까지 변형.

규칙(전 변형 공통): ACCENT 는 주요 액션·현재 선택 **전용**, 상태는 PASS/DANGER/WARN,
정보 텍스트는 INK 계열. 한 화면에 강조 하나. 전 색상쌍 WCAG AA.
"""

from __future__ import annotations

from dataclasses import dataclass
from string import Template

from ..config import Fonts


# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Profile:
    """변형별 구조 파라미터(px 단위, 별도 표기 제외)."""
    font_base: int = 14
    font_title: int = 20
    font_subtitle: int = 15
    font_caption: int = 11
    title_weight: int = 700
    title_tracking: int = 0
    radius: int = 10
    radius_sm: int = 6
    chip_radius: int = 9
    row_radius: int = 10
    page_margin: int = 40
    section_gap: int = 20
    card_pad: int = 14
    row_pad_v: int = 8
    row_gap: int = 6
    control_h: int = 34
    control_h_lg: int = 46
    control_pad_v: int = 8
    control_pad_h: int = 19
    check_sz: int = 18
    chip_style: str = "pill"        # "pill" | "text"
    row_style: str = "card"         # "card" | "hairline"
    card_shadow: bool = True
    primary_glow: bool = True
    chip_w: int = 74
    chip_h: int = 24
    toggle_w: int = 44
    toggle_h: int = 40
    thumb_default_px: int = 140
    focus_ring_px: int = 2
    motion_scale: float = 1.0


@dataclass(frozen=True)
class Variant:
    key: str
    label: str                      # 스위처 표기(한국어 짧게)
    colors: dict                    # 16 색 키
    profile: Profile
    scrim: tuple                    # LoadingOverlay 스크림 rgba
    shadow: tuple                   # NeonCard elevation 그림자 rgba


_COLOR_KEYS = (
    "bg", "panel", "elev", "line", "line2", "ink", "ink2", "mute",
    "accent", "accent_hover", "accent_pressed", "on_accent",
    "pass", "danger", "warn", "focus",
)


def _rgb(hexv: str) -> tuple:
    h = hexv.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _tint(hexv: str, alpha: int) -> str:
    r, g, b = _rgb(hexv)
    return f"rgba({r}, {g}, {b}, {alpha})"


# ---------------------------------------------------------------------------
# 변형 정의.  ①instrument(현재) 가 기준선.  신규 4종은 이후 커밋에서 추가.
VARIANTS: dict[str, "Variant"] = {
    "instrument": Variant(
        key="instrument", label="계측기",
        colors={
            "bg": "#0C0D10", "panel": "#131519", "elev": "#1A1D23",
            "line": "#282C34", "line2": "#333844",
            "ink": "#EAECEF", "ink2": "#B7BCC6", "mute": "#7E858F",
            "accent": "#E0A34A", "accent_hover": "#EBB668",
            "accent_pressed": "#C98F3D", "on_accent": "#14161A",
            "pass": "#4FB06A", "danger": "#E5605A", "warn": "#D6A430",
            "focus": "#6AA6FF",
        },
        profile=Profile(),
        scrim=(12, 13, 16, 200),
        shadow=(0, 0, 0, 140),
    ),
}

DEFAULT_VARIANT = "instrument"
CURRENT_VARIANT = DEFAULT_VARIANT

# ── 서체 (config.Fonts 재사용 — 변형 간 공통, 타이포 정체성은 PROFILE 크기/굵기로) ──
FONT_BODY = Fonts.BODY
FONT_TITLE = Fonts.TITLE
FONT_MONO = Fonts.MONO

# 아래 전역은 set_variant() 가 채운다(모듈 말미 1회 호출).
BG = PANEL = ELEV = LINE = LINE2 = ""
INK = INK2 = MUTE = ""
ACCENT = ACCENT_HOVER = ACCENT_PRESSED = ON_ACCENT = ""
PASS = DANGER = WARN = FOCUS = ""
ACCENT_TINT = ACCENT_TINT_SOFT = PASS_TINT = ""
DANGER_TINT = DANGER_TINT_SOFT = WARN_TINT = ""
PROFILE = Profile()
SCRIM_RGBA = (12, 13, 16, 200)
SHADOW_RGBA = (0, 0, 0, 140)

TOKENS: dict[str, str] = {}


def _derive_tokens(v: "Variant") -> dict:
    """변형 → QSS 토큰 dict (색 + 틴트 + 서체 + 구조)."""
    c = v.colors
    p = v.profile
    ring = p.focus_ring_px
    # 포커스 링 두께만큼 패딩을 줄여 :focus 시 크기 점프 방지.
    comp = ring - 1
    tok = {
        # 색
        **{k: c[k] for k in _COLOR_KEYS},
        # 틴트(베이스 색에서 파생 — 강조/상태와 항상 일치)
        "accent_tint": _tint(c["accent"], 36),
        "accent_tint_soft": _tint(c["accent"], 20),
        "pass_tint": _tint(c["pass"], 30),
        "danger_tint": _tint(c["danger"], 28),
        "danger_tint_soft": _tint(c["danger"], 15),
        "warn_tint": _tint(c["warn"], 30),
        # 서체
        "font_body": FONT_BODY, "font_title": FONT_TITLE, "font_mono": FONT_MONO,
        "font_base_sz": f"{p.font_base}px", "font_title_sz": f"{p.font_title}px",
        "font_sub_sz": f"{p.font_subtitle}px", "font_caption_sz": f"{p.font_caption}px",
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
    }
    # 칩 스타일(pill: 색 배경 / text: 투명 + 자간).
    if p.chip_style == "text":
        tok.update({
            "chip_bg_ok": "transparent", "chip_bg_over": "transparent",
            "chip_border_none": "transparent",
            "chip_pad": "2px 0px", "chip_ls": "1px",
        })
    else:
        tok.update({
            "chip_bg_ok": tok["pass_tint"], "chip_bg_over": tok["danger_tint"],
            "chip_border_none": c["line2"],
            "chip_pad": "2px 8px", "chip_ls": "0px",
        })
    # 행 스타일(card: 면+보더 / hairline: 투명 + 하단 구분선만).
    if p.row_style == "hairline":
        tok.update({"row_bg": "transparent", "row_border": "transparent",
                    "row_divider": c["line"]})
    else:
        tok.update({"row_bg": c["panel"], "row_border": c["line"],
                    "row_divider": c["line"]})
    return tok


def set_variant(name: str) -> None:
    """전역 색/구조 토큰을 변형 ``name`` 으로 일괄 전환(미지 키 → 기본 변형)."""
    global CURRENT_VARIANT, PROFILE, SCRIM_RGBA, SHADOW_RGBA
    global BG, PANEL, ELEV, LINE, LINE2, INK, INK2, MUTE
    global ACCENT, ACCENT_HOVER, ACCENT_PRESSED, ON_ACCENT
    global PASS, DANGER, WARN, FOCUS
    global ACCENT_TINT, ACCENT_TINT_SOFT, PASS_TINT
    global DANGER_TINT, DANGER_TINT_SOFT, WARN_TINT

    v = VARIANTS.get(name) or VARIANTS[DEFAULT_VARIANT]
    c = v.colors
    CURRENT_VARIANT = v.key
    PROFILE = v.profile
    SCRIM_RGBA = v.scrim
    SHADOW_RGBA = v.shadow

    BG, PANEL, ELEV = c["bg"], c["panel"], c["elev"]
    LINE, LINE2 = c["line"], c["line2"]
    INK, INK2, MUTE = c["ink"], c["ink2"], c["mute"]
    ACCENT, ACCENT_HOVER = c["accent"], c["accent_hover"]
    ACCENT_PRESSED, ON_ACCENT = c["accent_pressed"], c["on_accent"]
    PASS, DANGER, WARN, FOCUS = c["pass"], c["danger"], c["warn"], c["focus"]

    ACCENT_TINT = _tint(ACCENT, 36)
    ACCENT_TINT_SOFT = _tint(ACCENT, 20)
    PASS_TINT = _tint(PASS, 30)
    DANGER_TINT = _tint(DANGER, 28)
    DANGER_TINT_SOFT = _tint(DANGER, 15)
    WARN_TINT = _tint(WARN, 30)

    TOKENS.clear()
    TOKENS.update(_derive_tokens(v))


def variant_keys() -> list:
    return list(VARIANTS.keys())


def render_qss(template_text: str) -> str:
    """``style.qss`` 템플릿의 ``$token`` 을 현재 :data:`TOKENS` 로 치환.

    미정의 토큰은 KeyError 로 즉시 실패(오타 조기 노출 — ``safe_substitute`` 미사용).
    """
    return Template(template_text).substitute(TOKENS)


def apply_to_app(app) -> None:
    """현재 변형 기준으로 style.qss 를 렌더해 앱 전체에 적용."""
    from pathlib import Path
    from ..utils import paths
    qss_path = paths.resource_path("aoi_verification/app/ui/style.qss")
    text = Path(qss_path).read_text(encoding="utf-8")
    app.setStyleSheet(render_qss(text))


# 모듈 로드 시 기본 변형 확정 → import 시점 동작이 기존과 동일.
set_variant(DEFAULT_VARIANT)
