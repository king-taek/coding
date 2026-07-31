"""비활성 버튼은 **한눈에 못 누르는 것**으로 보여야 한다.

사용자 지적: "비활성화 된 버튼과 활성화된 버튼의 차이가 별로 안나는 문제 있음".
원인은 비활성 신호가 `$mute`(활성 `$ink2` 에서 한 단)와 점선 테두리뿐이었다는 것 —
둘 다 밝기 차가 작아 곁눈으로는 구분되지 않았다.  이제 버튼 계열 `:disabled` 는
바탕 쪽으로 당긴 `$disabled_ink`/`$disabled_line` 을 쓴다.

여기서 고정하는 것:
- 두 색 모드 모두에서 토큰이 존재한다(빠지면 `render_qss` 가 KeyError 로 죽는다).
- 비활성 글자가 활성(`$ink2`)·기존(`$mute`)보다 **확실히 옅다** — 그러나 사라지진 않는다.
- 버튼 계열 `:disabled` 규칙이 실제로 그 토큰을 쓴다(테마만 고치고 QSS 를 잊는 실수 방지).

※ 대비 하한을 AA(4.5) 로 잡지 않는다 — WCAG 1.4.3 은 **비활성 컨트롤을 제외**한다.
  '읽히긴 하되 죽어 보이는' 상태가 목표다.
"""

from __future__ import annotations

from pathlib import Path

from aoi_verification.app.ui import theme

_QSS = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui"
        / "style.qss").read_text(encoding="utf-8")

# 버튼 계열 — 활성/비활성 구분이 필요한 클릭 대상 전부.
_BUTTON_DISABLED_SELECTORS = (
    "QPushButton:disabled",
    'QPushButton[role="stepper"]:disabled',
    'QPushButton[role="primary"]:disabled',
    'QPushButton[role="ghost"]:disabled',
    'QPushButton[role="option"]:disabled',
    'QPushButton[role="segment"]:disabled',
    'QPushButton[role="slotTile"]:disabled',
    'QPushButton[role="rowAction"]:disabled',
)


def _lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(hexv: str) -> float:
    r, g, b = theme._rgb(hexv)
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _with_mode(name: str):
    theme.set_color_mode(name)
    return dict(theme.TOKENS)


def test_disabled_tokens_exist_in_both_modes():
    try:
        for mode in ("light", "dark"):
            tokens = _with_mode(mode)
            for key in ("disabled_ink", "disabled_line"):
                assert key in tokens, f"{mode} 모드에 ${key} 토큰이 없다"
                assert tokens[key].startswith("#"), \
                    f"${key} 는 불투명 색이어야 한다(면 위에서 일정하게 옅어야 한다)"
    finally:
        theme.set_color_mode(theme.DEFAULT_COLOR_MODE)


def test_disabled_ink_is_clearly_fainter_than_the_active_label():
    """비활성 글자는 활성보다 **눈에 띄게** 옅다 — 그러나 사라지진 않는다."""
    try:
        for mode in ("light", "dark"):
            tokens = _with_mode(mode)
            bg = tokens["bg"]
            active = _contrast(tokens["ink2"], bg)
            old = _contrast(tokens["mute"], bg)
            now = _contrast(tokens["disabled_ink"], bg)
            assert now < old, (
                f"{mode}: 비활성이 예전($mute {old:.2f})보다 옅어지지 않았다 "
                f"(현재 {now:.2f})")
            assert now < active * 0.6, (
                f"{mode}: 활성({active:.2f}) 대비 비활성({now:.2f})이 충분히 죽지 않았다")
            assert now >= 1.5, (
                f"{mode}: 비활성이 바탕에 묻혀 아예 안 보인다({now:.2f})")
    finally:
        theme.set_color_mode(theme.DEFAULT_COLOR_MODE)


def test_disabled_border_is_fainter_than_the_active_border():
    try:
        for mode in ("light", "dark"):
            tokens = _with_mode(mode)
            bg = tokens["bg"]
            assert _contrast(tokens["disabled_line"], bg) \
                < _contrast(tokens["line_strong"], bg), \
                f"{mode}: 비활성 테두리가 활성 경계만큼 진하다"
    finally:
        theme.set_color_mode(theme.DEFAULT_COLOR_MODE)


def test_button_disabled_rules_use_the_disabled_tokens():
    """테마만 고치고 QSS 를 잊으면 화면은 그대로다 — 규칙 쪽도 함께 고정한다."""
    blocks = _QSS.split("}")
    for selector in _BUTTON_DISABLED_SELECTORS:
        owning = [b for b in blocks if selector in b and "{" in b]
        assert owning, f"{selector} 규칙이 사라졌다"
        body = owning[0].split("{", 1)[1]
        assert "$disabled_ink" in body, f"{selector} 가 아직 옛 글자색을 쓴다"


def test_render_still_substitutes_every_token():
    """토큰 오타는 즉시 드러나야 한다(`Template.substitute` 는 미지 토큰에 KeyError)."""
    try:
        for mode in ("light", "dark"):
            theme.set_color_mode(mode)
            assert "$" not in theme.render_qss(_QSS)
    finally:
        theme.set_color_mode(theme.DEFAULT_COLOR_MODE)
