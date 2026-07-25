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
    radius: int = 2                 # 샤프 — 제도 시트
    radius_sm: int = 1
    chip_radius: int = 0            # 판정 칩 = 사각 '스탬프'
    row_radius: int = 0
    page_margin: int = 32
    section_gap: int = 28
    card_pad: int = 20
    row_pad_v: int = 8
    row_gap: int = 0                # 하이라인 눈금이 행을 가른다(간격 0)
    control_h: int = 32
    control_h_lg: int = 40
    control_pad_v: int = 6
    control_pad_h: int = 14
    check_sz: int = 16
    chip_w: int = 88
    chip_h: int = 20
    toggle_w: int = 52
    toggle_h: int = 30
    thumb_default_px: int = 118
    focus_ring_px: int = 2
    motion_scale: float = 0.8       # 제도 시트답게 담백한 모션


# ── 색 ('도면' 팔레트 — 전 기능 쌍 WCAG AA 여유) ────────────────────────────
COLORS: dict[str, str] = {
    "bg": "#ECE9E2",        # 벨럼 바탕
    "panel": "#F5F3ED",     # 시트 면
    "elev": "#FBFAF7",
    "line": "#948E80",      # 제도 눈금(가시)
    "line2": "#B7B2A6",
    "ink": "#1B1A17",
    "ink2": "#3D3B35",
    "mute": "#5A574E",
    "accent": "#2C5A86",    # 청사진 블루 — 주요 액션·현재 선택 전용
    "accent_hover": "#356B9C",
    "accent_pressed": "#244B70",
    "on_accent": "#F5F3ED",
    "pass": "#3B6438",      # 제도 녹색
    "danger": "#A5271E",    # 정정 적색
    "warn": "#1B1A17",
    "focus": "#2B4C6F",
    "thumb_frame": "#A39D8F",   # 어두운 다이가 시트에 묻히지 않게
}

SCRIM_RGBA = (27, 26, 23, 138)      # LoadingOverlay 스크림


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

# 인라인 f-string 스타일이 쓰는 색 상수.
BG, PANEL, ELEV = COLORS["bg"], COLORS["panel"], COLORS["elev"]
LINE, LINE2 = COLORS["line"], COLORS["line2"]
INK, INK2, MUTE = COLORS["ink"], COLORS["ink2"], COLORS["mute"]
ACCENT, ACCENT_HOVER = COLORS["accent"], COLORS["accent_hover"]
ACCENT_PRESSED, ON_ACCENT = COLORS["accent_pressed"], COLORS["on_accent"]
PASS, DANGER = COLORS["pass"], COLORS["danger"]
WARN, FOCUS = COLORS["warn"], COLORS["focus"]
THUMB_FRAME = COLORS["thumb_frame"]

ACCENT_TINT = _tint(ACCENT, 36)
ACCENT_TINT_SOFT = _tint(ACCENT, 20)
PASS_TINT = _tint(PASS, 30)
DANGER_TINT = _tint(DANGER, 28)
DANGER_TINT_SOFT = _tint(DANGER, 15)
WARN_TINT = _tint(WARN, 30)


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
        # 판정 칩 — 채운 '스탬프'(사각). 예외가 한눈에 띄게.
        "chip_bg_ok": PASS_TINT, "chip_bg_over": DANGER_TINT,
        "chip_border_none": c["line2"], "chip_pad": "2px 8px", "chip_ls": "0px",
        # 행 — 면 없이 하이라인 눈금만(제도 시트).
        "row_bg": "transparent", "row_border": "transparent",
        "row_divider": c["line"],
        # 점수 컬럼 눈금 — ink 반투명이라 시트 위에서 또렷.
        "score_rule": _tint(c["ink"], 55),
    }


TOKENS: dict[str, str] = _derive_tokens()


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
