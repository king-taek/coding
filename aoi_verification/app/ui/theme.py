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
    list_header: bool = False       # 검토 리스트 상단 컬럼 헤더(표/제도 시트 성격)
    thumb_reticle: bool = False     # 썸네일 모서리 레티클 틱(계측기 성격)
    zebra_rows: bool = False        # 홀수 행 지브라 띠(계측 데이터테이블 성격)
    score_hero: bool = False        # 거리 수치를 크게 — '판독 우선' 대형 변형 성격
    compact_narrow_px: int = 0      # 좁은 창(<900)에서 썸네일 상한(0=미적용)
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
    "pass", "danger", "warn", "focus", "thumb_frame",
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
    # ① 계측기 — 어두운 검사 부스, 앰버 단일광. (개선 전담 에이전트 정제:
    #    면 대비 확대·MUTE 강화·WARN/ACCENT 색상 분리)
    "instrument": Variant(
        key="instrument", label="계측기",
        colors={
            "bg": "#0C0D10", "panel": "#14161C", "elev": "#1E212A",
            "line": "#2A2E37", "line2": "#363B47",
            "ink": "#EAECEF", "ink2": "#B7BCC6", "mute": "#8A909B",
            "accent": "#E0A657", "accent_hover": "#EBB570",
            "accent_pressed": "#C68F41", "on_accent": "#0C0D10",
            "pass": "#52B76E", "danger": "#E8665F", "warn": "#D4A82C",
            "focus": "#74A9FF", "thumb_frame": "#4C5563",
        },
        profile=Profile(
            radius=8, radius_sm=5, chip_radius=7, row_radius=8,
            card_pad=18, control_pad_h=14, font_caption=12,
            thumb_reticle=True,          # 계측기 — 레티클 틱으로 '검사 부스' 성격
        ),
        scrim=(12, 13, 16, 200),
        shadow=(0, 0, 0, 140),
    ),
    # ② 명실 — 밝은 클린룸 스틸 워크톱, 코발트. 하이라인 데이터테이블 + 색 알약.
    "cleanroom": Variant(
        key="cleanroom", label="명실",
        colors={
            "bg": "#EBEEF3", "panel": "#FFFFFF", "elev": "#F4F6F9",
            "line": "#CBD3DD", "line2": "#E4E8EE",
            "ink": "#12161C", "ink2": "#45505E", "mute": "#566070",
            "accent": "#1F63D6", "accent_hover": "#1A55BC",
            "accent_pressed": "#164AA3", "on_accent": "#FFFFFF",
            "pass": "#12784D", "danger": "#C62839", "warn": "#9E6600",
            "focus": "#0A66AC", "thumb_frame": "#AFB9C6",
        },
        profile=Profile(
            font_base=13, font_title=22, font_subtitle=15, font_caption=11,
            title_weight=600, radius=6, radius_sm=4, chip_radius=10,
            row_radius=3, page_margin=24, section_gap=16, card_pad=18,
            row_pad_v=14, row_gap=0, control_h=32, control_h_lg=40,
            control_pad_v=7, control_pad_h=14,
            chip_style="pill", row_style="hairline",
            card_shadow=True, primary_glow=False, list_header=True,
            zebra_rows=True,             # 계측 벤치 — 지브라 띠로 표 판독 강화
            chip_w=82, chip_h=22, toggle_w=44, toggle_h=36,
            thumb_default_px=120, focus_ring_px=2, motion_scale=0.85,
        ),
        scrim=(231, 236, 243, 200),
        shadow=(18, 28, 45, 38),
    ),
    # ③ 도면 — 따뜻한 벨럼 제도 시트, 그래파이트 모노. 하이라인 + 타입 상태.
    "datum": Variant(
        key="datum", label="도면",
        colors={
            "bg": "#ECE9E2", "panel": "#F5F3ED", "elev": "#FBFAF7",
            "line": "#948E80", "line2": "#B7B2A6",
            "ink": "#1B1A17", "ink2": "#3D3B35", "mute": "#5A574E",
            "accent": "#2C5A86", "accent_hover": "#356B9C",
            "accent_pressed": "#244B70", "on_accent": "#F5F3ED",
            "pass": "#3B6438", "danger": "#A5271E", "warn": "#1B1A17",
            "focus": "#2B4C6F", "thumb_frame": "#A39D8F",
        },
        profile=Profile(
            font_base=14, font_title=26, font_subtitle=15, font_caption=12,
            title_weight=300, title_tracking=-1, radius=2, radius_sm=1,
            chip_radius=0, row_radius=0, page_margin=32, section_gap=28,
            card_pad=20, row_pad_v=8, row_gap=0, control_h=32, control_h_lg=40,
            control_pad_v=6, control_pad_h=14, check_sz=16,
            chip_style="pill", row_style="hairline",
            card_shadow=False, primary_glow=False, list_header=True,
            chip_w=88, chip_h=20, toggle_w=52, toggle_h=30,
            thumb_default_px=118, focus_ring_px=2, motion_scale=0.8,
        ),
        scrim=(27, 26, 23, 138),
        shadow=(0, 0, 0, 0),
    ),
    # ④ 맑음 — 접근성 대형, 따뜻한 페이퍼-화이트, 티일. 16px·48px·3px 링.
    "clarity": Variant(
        key="clarity", label="큰 글씨",
        colors={
            "bg": "#E7E4DC", "panel": "#F7F5EF", "elev": "#FFFFFF",
            "line": "#C7C3B8", "line2": "#D8D4CA",
            "ink": "#17191C", "ink2": "#3E4247", "mute": "#5A5F66",
            "accent": "#0F6B78", "accent_hover": "#0C5A66",
            "accent_pressed": "#094A54", "on_accent": "#FFFFFF",
            "pass": "#1A6E37", "danger": "#B4231F", "warn": "#8A5A00",
            "focus": "#1558D6", "thumb_frame": "#B4BBC3",
        },
        profile=Profile(
            font_base=16, font_title=24, font_subtitle=17, font_caption=13,
            title_weight=800, radius=14, radius_sm=9, chip_radius=15,
            row_radius=16, page_margin=32, section_gap=24, card_pad=18,
            row_pad_v=12, row_gap=10, control_h=48, control_h_lg=52,
            control_pad_v=12, control_pad_h=22, check_sz=22,
            chip_style="pill", row_style="card",
            card_shadow=True, primary_glow=False,
            score_hero=True,             # 판독 우선 — 거리 수치를 크게(고유 문양)
            compact_narrow_px=96,        # 800px 라인PC 에서 썸네일 상한(밀도 확보)
            chip_w=88, chip_h=30, toggle_w=48, toggle_h=48,
            # 접근성 대형 변형은 '차분한' 모션이 어울린다 — 가장 빠른(0.6) 대신
            # 넉넉하게(1.1) 하여 전환/로딩이 급하지 않게(모션 디렉터 지적).
            thumb_default_px=128, focus_ring_px=3, motion_scale=1.1,
        ),
        scrim=(20, 22, 26, 120),
        shadow=(28, 32, 38, 38),
    ),
    # ⑤ 청동 — 딥 버디그리스 그라운드 + 구리 액센트, 둥근 물성. 헤리티지 계측기.
    "patina": Variant(
        key="patina", label="청동",
        colors={
            "bg": "#123A38", "panel": "#184843", "elev": "#1F534B",
            "line": "#2C5B54", "line2": "#3E706A",
            "ink": "#F4EDE2", "ink2": "#D9CFC0", "mute": "#CFC5B6",
            "accent": "#F2BC84", "accent_hover": "#F7C994",
            "accent_pressed": "#DCA466", "on_accent": "#2A160B",
            "pass": "#6EDB9C", "danger": "#FFB0B4", "warn": "#F2B24A",
            "focus": "#FFCF7A", "thumb_frame": "#7C9A92",
        },
        profile=Profile(
            font_base=14, font_title=26, font_subtitle=18, font_caption=11,
            title_weight=700, radius=14, radius_sm=9, chip_radius=999,
            row_radius=14, page_margin=24, section_gap=28, card_pad=20,
            row_pad_v=12, row_gap=8, control_h=38, control_h_lg=50,
            control_pad_v=10, control_pad_h=18, check_sz=22,
            chip_style="pill", row_style="card",
            card_shadow=True, primary_glow=False,
            chip_w=96, chip_h=30, toggle_w=52, toggle_h=40,
            thumb_default_px=130, focus_ring_px=3, motion_scale=1.15,
        ),
        scrim=(9, 24, 22, 168),
        shadow=(6, 17, 16, 122),
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
THUMB_FRAME = ""
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
    # 점수 컬럼 눈금 — ink 반투명이라 밝은/어두운 패널 어디서나 또렷(자기적응). 헤더 없는
    # 카드 변형(instrument 등)에서도 점수가 '떠 있지' 않고 눈금 컬럼으로 읽히게(C7).
    tok["score_rule"] = _tint(c["ink"], 55)
    # 지브라(계측 벤치 성격) 행 배경 — cleanroom 전용, 아주 옅게.
    tok["zebra_bg"] = _tint(c["ink"], 8)
    return tok


def set_variant(name: str) -> None:
    """전역 색/구조 토큰을 변형 ``name`` 으로 일괄 전환(미지 키 → 기본 변형)."""
    global CURRENT_VARIANT, PROFILE, SCRIM_RGBA, SHADOW_RGBA
    global BG, PANEL, ELEV, LINE, LINE2, INK, INK2, MUTE
    global ACCENT, ACCENT_HOVER, ACCENT_PRESSED, ON_ACCENT
    global PASS, DANGER, WARN, FOCUS, THUMB_FRAME
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
    THUMB_FRAME = c["thumb_frame"]

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
