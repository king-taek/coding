"""전역 디자인 토큰 — 색·서체의 단일 출처 (絶제된 다크 계측기 테마).

규칙:
- 모든 UI 색은 여기 상수를 쓴다.  인라인 ``setStyleSheet`` 에 raw hex 를 새로
  적지 않는다 (가드: ``dev/tests/test_theme.py`` 의 구팔레트 hex 검사).
- ``style.qss`` 는 ``$token`` 플레이스홀더 템플릿이고, 앱 시작 시
  :func:`render_qss` 가 여기 토큰으로 치환해 적용한다 → QSS 와 파이썬 코드가
  같은 값을 공유한다 (드리프트 방지).
- ``ACCENT``(앰버)는 **주요 액션·현재 포커스 전용**.  상태 표시는 PASS/DANGER/
  WARN, 정보 텍스트는 INK 계열을 쓴다 (한 화면에 강조 하나 원칙).
"""

from __future__ import annotations

from string import Template

from ..config import Fonts

# ── 바탕/면/선 ──────────────────────────────────────────────────────────────
BG = "#0C0D10"          # 창 바탕
PANEL = "#131519"       # 패널/카드 면
ELEV = "#1A1D23"        # 한 단계 떠 있는 면 (입력, 버튼 hover)
LINE = "#282C34"        # 기본 보더
LINE2 = "#333844"       # 강조 보더/구분선

# ── 텍스트 ──────────────────────────────────────────────────────────────────
INK = "#EAECEF"         # 본문
INK2 = "#B7BCC6"        # 보조 본문
MUTE = "#7E858F"        # 흐린 라벨/캡션

# ── 의미 색 ─────────────────────────────────────────────────────────────────
ACCENT = "#E0A34A"      # 앰버 — 주요 액션·현재 포커스 전용
ACCENT_HOVER = "#EBB668"    # 앰버 hover (한 단계 밝게)
ACCENT_PRESSED = "#C98F3D"  # 앰버 pressed (한 단계 어둡게)
ON_ACCENT = "#14161A"       # 앰버 면 위 글자색
PASS = "#4FB06A"        # 정상/일치
DANGER = "#E5605A"      # 위험/초과 (패널 위 AA 대비 확보)
WARN = "#D6A430"        # 주의
FOCUS = "#6AA6FF"       # 키보드 포커스 링 (앰버 선택과 구분되는 파랑)

# 어두운 면 위에 얹는 반투명 틴트 (rgba 문자열).
ACCENT_TINT = "rgba(224, 163, 74, 36)"
ACCENT_TINT_SOFT = "rgba(224, 163, 74, 20)"
PASS_TINT = "rgba(79, 176, 106, 30)"
DANGER_TINT = "rgba(229, 96, 90, 28)"
DANGER_TINT_SOFT = "rgba(229, 96, 90, 15)"
WARN_TINT = "rgba(214, 164, 48, 30)"

# ── 서체 (config.Fonts 재사용 — 재정의 금지) ────────────────────────────────
FONT_BODY = Fonts.BODY
FONT_TITLE = Fonts.TITLE
FONT_MONO = Fonts.MONO

TOKENS: dict[str, str] = {
    "bg": BG, "panel": PANEL, "elev": ELEV, "line": LINE, "line2": LINE2,
    "ink": INK, "ink2": INK2, "mute": MUTE,
    "accent": ACCENT, "accent_hover": ACCENT_HOVER,
    "accent_pressed": ACCENT_PRESSED, "on_accent": ON_ACCENT,
    "pass": PASS, "danger": DANGER, "warn": WARN,
    "focus": FOCUS,
    "accent_tint": ACCENT_TINT, "accent_tint_soft": ACCENT_TINT_SOFT,
    "pass_tint": PASS_TINT, "danger_tint": DANGER_TINT,
    "danger_tint_soft": DANGER_TINT_SOFT, "warn_tint": WARN_TINT,
    "font_body": FONT_BODY, "font_title": FONT_TITLE, "font_mono": FONT_MONO,
}


def render_qss(template_text: str) -> str:
    """``style.qss`` 템플릿의 ``$token`` 을 :data:`TOKENS` 로 치환.

    미정의 토큰은 KeyError 로 즉시 실패한다 (오타를 조용히 넘기지 않기 위해
    ``safe_substitute`` 가 아닌 ``substitute`` 사용).
    """
    return Template(template_text).substitute(TOKENS)
