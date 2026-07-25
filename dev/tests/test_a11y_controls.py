"""접근성 계약 — 비-텍스트 대비(WCAG 1.4.11)·클릭 타깃(2.5.8)·포커스 가시성(2.4.7).

이 파일이 존재하는 이유: 세 항목 모두 **정적 캡처로는 통과처럼 보였다**.  경계선은
'디자인적으로 절제된' 것처럼 보였지만 실측 1.5~2.0:1 로 사실상 안 보였고, 포커스 링은
QSS 에 선언만 되어 있었을 뿐 22장의 캡처 어디에도 실제로 렌더된 적이 없었다.
그래서 여기서는 **렌더된 픽셀**과 **측정된 비율**로만 판정한다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt                                    # noqa: E402
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDoubleSpinBox,  # noqa: E402
                             QLineEdit, QSlider, QToolButton, QWidget)

from aoi_verification.app.ui import theme                       # noqa: E402
from aoi_verification.app.ui.widgets.neon_button import NeonButton  # noqa: E402
from aoi_verification.app.ui.widgets.option_group import OptionGroup  # noqa: E402
from aoi_verification.app.ui.widgets.switch_row import SwitchRow      # noqa: E402

_QSS = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui"
        / "style.qss").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ★ 모드 목록을 복제하지 않는다 — theme 에서 읽어야 새 색 모드가
#   추가되는 순간 모든 대비·포커스 계약이 자동으로 걸린다.
_MODES = list(theme.color_mode_keys())


@pytest.fixture(autouse=True)
def _restore_light():
    yield
    theme.set_color_mode("light")


# ── 1.4.11 비-텍스트 대비 ────────────────────────────────────────────────
def _lum(hexv: str) -> float:
    h = hexv.lstrip("#")
    out = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255.0
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]


def _ratio(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("surface", ["elev", "panel", "bg"])
def test_interactive_border_meets_non_text_contrast(mode, surface):
    """평상시 컨트롤 경계가 **모든** 면에서 3:1 이상 — 안 보이는 입력란 재발 방지."""
    theme.set_color_mode(mode)
    c = theme.COLORS
    r = _ratio(c["line_strong"], c[surface])
    assert r >= 3.0, f"{mode}: line_strong on {surface} = {r:.2f}"


@pytest.mark.parametrize("mode", _MODES)
def test_structural_rule_meets_non_text_contrast(mode):
    """카드 외곽·행 구분 눈금($line)도 시트 면·바탕에서 3:1 이상."""
    theme.set_color_mode(mode)
    c = theme.COLORS
    for surface in ("panel", "bg"):
        r = _ratio(c["line"], c[surface])
        assert r >= 3.0, f"{mode}: line on {surface} = {r:.2f}"


@pytest.mark.parametrize("mode", _MODES)
def test_focus_ring_meets_contrast(mode):
    """포커스 링은 링이 놓이는 모든 면에서 3:1 이상이어야 보인다."""
    theme.set_color_mode(mode)
    c = theme.COLORS
    for surface in ("elev", "panel", "bg"):
        r = _ratio(c["focus"], c[surface])
        assert r >= 3.0, f"{mode}: focus on {surface} = {r:.2f}"


@pytest.mark.parametrize("mode", _MODES)
def test_filled_button_focus_ring_contrasts_with_its_own_fill(mode):
    """★ 채운 버튼의 링은 **자기 채움**과 대비해야 한다 — 페이지 배경이 아니다.

    이 검사가 없어서 실제로 구멍이 났다: `$focus` 는 `$accent` 와 1.23:1 이라
    [검증 시작]에 링을 그려도 자기 색에 묻혀 보이지 않았다(캡처 실측).  픽셀 검사만으로는
    잡히지 않는다 — 링 색 픽셀이 '존재'하기는 했으니까.  그래서 색 관계로 못 박는다."""
    theme.set_color_mode(mode)
    c = theme.COLORS
    ring = _ring_color_for_primary()
    r = _ratio(ring, c["accent"])
    assert r >= 3.0, f"{mode}: primary 링({ring}) vs 채움({c['accent']}) = {r:.2f}"


def _ring_color_for_primary() -> str:
    """style.qss 의 `QPushButton[role="primary"]:focus` 가 실제로 쓰는 토큰 값을 읽는다.

    상수를 테스트에 복제하면 QSS 를 바꿔도 테스트가 계속 통과한다 — 렌더된 QSS 에서
    직접 뽑아 그 함정을 막는다."""
    rendered = theme.render_qss(_QSS)
    marker = 'QPushButton[role="primary"]:focus {'
    block = rendered[rendered.index(marker) + len(marker):]
    block = block[:block.index("}")]
    for line in block.splitlines():
        if "border:" in line:
            return line.strip().rstrip(";").split()[-1]
    raise AssertionError("primary:focus 에 border 선언이 없다")


def test_weak_token_is_not_used_for_interactive_borders():
    """$line2 는 장식 전용 — 상호작용 컨트롤의 **평상시** 경계에 쓰면 회귀다.

    허용: 비활성(파선/dashed)·hover·툴팁·키캡 같은 비-상호작용 또는 예외 상태."""
    offenders = []
    block: list[str] = []
    selector = ""
    for raw in _QSS.splitlines():
        line = raw.strip()
        if line.startswith("/*") or line.startswith("*"):
            continue
        if line.endswith("{"):
            selector = line[:-1].strip()
            block = []
            continue
        if line.startswith("}"):
            selector, block = "", []
            continue
        if "$line2" not in line:
            continue
        interactive = any(w in selector for w in
                          ("QPushButton", "QLineEdit", "QSpinBox", "QDoubleSpinBox",
                           "QComboBox", "QCheckBox", "QRadioButton", "QToolButton"))
        resting = not any(s in selector for s in
                          (":disabled", ":hover", ":pressed", ":checked", ":focus"))
        if interactive and resting and ("border" in line):
            offenders.append(f"{selector} → {line}")
    assert not offenders, "상호작용 평상시 경계에 약한 토큰: " + "; ".join(offenders)


# ── 2.5.8 클릭 타깃 (AA, 24px) ───────────────────────────────────────────
def _sized(widget: QWidget, qapp) -> tuple[int, int]:
    widget.setStyleSheet(theme.render_qss(_QSS))
    widget.show()
    for _ in range(6):
        qapp.processEvents()
    h = widget.sizeHint()
    return max(h.height(), widget.height()), max(h.width(), widget.width())


MIN_TARGET = 24


def test_option_tile_target(qapp):
    g = OptionGroup([("a", "가"), ("b", "나")])
    try:
        btn = g.button("a")
        h, _ = _sized(btn, qapp)
        assert h >= MIN_TARGET, f"옵션 타일 {h}px"
        # 타일은 액션 등급이라 44px(2.5.5 AAA)까지 올려 뒀다.
        assert h >= theme.PROFILE.control_h_lg - 1, f"옵션 타일 {h}px"
    finally:
        g.deleteLater()


@pytest.mark.parametrize("factory,label", [
    (lambda: QLineEdit(), "입력란"),
    (lambda: QDoubleSpinBox(), "스핀박스"),
    (lambda: QCheckBox("체크"), "체크박스 행"),
    (lambda: NeonButton("버튼", role="ghost"), "버튼"),
])
def test_control_meets_min_target(qapp, factory, label):
    w = factory()
    try:
        h, _ = _sized(w, qapp)
        assert h >= MIN_TARGET, f"{label} {h}px < {MIN_TARGET}px"
    finally:
        w.deleteLater()


def test_slider_and_help_button_targets(qapp):
    sl = QSlider(Qt.Orientation.Horizontal)
    tb = QToolButton()
    tb.setObjectName("helpToggle")
    tb.setText("?")
    try:
        assert _sized(sl, qapp)[0] >= MIN_TARGET
        h, w = _sized(tb, qapp)
        assert h >= MIN_TARGET and w >= MIN_TARGET, f"? 버튼 {w}×{h}"
    finally:
        sl.deleteLater()
        tb.deleteLater()


def test_switch_row_target(qapp):
    row = SwitchRow("구형 모드", checked=False)
    try:
        # 행 전체가 클릭영역이므로 행 높이가 실제 타깃이다.
        assert _sized(row, qapp)[0] >= MIN_TARGET
    finally:
        row.deleteLater()


# ── 2.4.7 포커스 가시성 — 선언이 아니라 **렌더된 픽셀**로 증명 ─────────────
def _focus_pixels(qapp, mode: str, make, pick=None, ring: str = "focus") -> bool:
    """포커스 색 픽셀이 실제로 그려졌는지 — QSS 에 링을 '선언'한 것만으로는 증명이 안 된다.

    ★ 자식 위젯을 단독으로 show() 하면 창이 활성화되지 않아 포커스가 실제로 들어가지
    않는다(그래서 링이 없는 것처럼 보인다).  반드시 최상위 창 안에 넣고 활성화한 뒤,
    포커스를 옮길 상대 위젯까지 둬서 진짜 탭 포커스 상태를 만든다.

    ``ring`` — 기대 링 색 토큰.  채운 primary 는 자기 채움과 대비해야 하므로
    ``on_accent`` 를 쓴다(위 test_filled_button_focus_ring_contrasts_with_its_own_fill).
    """
    theme.set_color_mode(mode)
    qapp.setStyleSheet(theme.render_qss(_QSS))
    host = QWidget()
    lay = _QVBox(host)
    w = make(host)
    lay.addWidget(w)
    lay.addWidget(QLineEdit(host))          # 포커스를 주고받을 상대
    host.resize(320, 160)
    host.show()
    host.activateWindow()
    for _ in range(10):
        qapp.processEvents()
    target = pick(w) if pick else w
    target.setFocus(Qt.FocusReason.TabFocusReason)
    for _ in range(10):
        qapp.processEvents()
    assert target.hasFocus(), "하네스 문제: 포커스가 들어가지 않았다"
    img = target.grab().toImage()
    fh = theme.COLORS[ring].lstrip("#")
    fr, fg, fb = (int(fh[i:i + 2], 16) for i in (0, 2, 4))
    hit = any(
        abs(c.red() - fr) <= 12 and abs(c.green() - fg) <= 12
        and abs(c.blue() - fb) <= 12
        for y in range(img.height())
        for c in (img.pixelColor(x, y) for x in range(img.width()))
    )
    host.deleteLater()
    return hit


from PyQt6.QtWidgets import QVBoxLayout as _QVBox        # noqa: E402


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("make,label,ring", [
    (lambda p: QLineEdit(p), "입력란", "focus"),
    (lambda p: NeonButton("버튼", role="ghost", parent=p), "ghost 버튼", "focus"),
    # 채운 primary 는 링을 자기 채움과 대비되는 색으로 그린다.
    (lambda p: NeonButton("시작", role="primary", parent=p), "primary 버튼",
     "on_accent"),
    (lambda p: NeonButton("삭제", role="danger", parent=p), "danger 버튼", "focus"),
    (lambda p: QDoubleSpinBox(p), "스핀박스", "focus"),
    (lambda p: QCheckBox("모션 줄이기", p), "체크박스", "focus"),
])
def test_focus_ring_actually_renders(qapp, mode, make, label, ring):
    """★ 이 테스트가 ghost/danger 버튼의 링이 **아예 없던** 버그를 잡아냈다:
    `QPushButton[role="ghost"]` 의 border-color 가 특이도 동급·후순위로 일반
    `QPushButton:focus` 를 덮어쓰고 있었다."""
    assert _focus_pixels(qapp, mode, make, ring=ring), \
        f"{mode}/{label}: 포커스 링({ring})이 실제로 렌더되지 않았다"


@pytest.mark.parametrize("mode", _MODES)
def test_toggle_switch_focus_ring_renders(qapp, mode):
    """커스텀 페인트 위젯 — QSS 가 아니라 paintEvent 가 링을 그린다."""
    assert _focus_pixels(
        qapp, mode,
        lambda p: SwitchRow("구형 모드", checked=False, parent=p),
        pick=lambda row: row.switch, ring="on_accent",
    ), f"{mode}: 스위치 포커스 링 미렌더"


@pytest.mark.parametrize("mode", _MODES)
def test_slider_focus_ring_renders(qapp, mode):
    """★ QSS 로는 불가능한 케이스 — Qt 는 서브컨트롤에 :focus 를 지원하지 않고, 위젯
    border 도 서브컨트롤이 스타일링된 슬라이더에선 렌더되지 않는다.  그래서
    `NoWheelSlider.paintEvent` 가 직접 그린다.  이 테스트가 그 계약을 지킨다."""
    from aoi_verification.app.ui.widgets.no_wheel_slider import NoWheelSlider

    def make(p):
        sl = NoWheelSlider(Qt.Orientation.Horizontal, p)
        sl.setRange(0, 100)
        sl.setValue(55)
        return sl

    assert _focus_pixels(qapp, mode, make), f"{mode}: 슬라이더 포커스 링 미렌더"


@pytest.mark.parametrize("mode", _MODES)
def test_option_tile_focus_ring_renders(qapp, mode):
    assert _focus_pixels(
        qapp, mode,
        lambda p: OptionGroup([("a", "가"), ("b", "나")], parent=p),
        pick=lambda g: g.button("a"),
    ), f"{mode}: 옵션 타일 포커스 링 미렌더"


# ── 합성면 대비 — "전 쌍 통과"가 거짓이었던 구멍을 막는다 ────────────────────────
# 이전 대비표는 **컨트롤 경계만** 재고 전 쌍 통과라고 주장했다.  실제 하한은 표에 없던
# 합성면 쌍이었다: 반투명 틴트가 면 위에 얹힌 뒤의 라벨, 스크림 아래 본문, 로딩 지시자.
def _mix(fg: str, alpha: int, bg: str) -> str:
    def rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    f, b = rgb(fg), rgb(bg)
    a = alpha / 255
    return "#%02X%02X%02X" % tuple(round(f[i] * a + b[i] * (1 - a)) for i in range(3))


def _tint_alpha(rgba: str) -> int:
    """``rgba(r, g, b, A)`` 에서 A — ★ 알파를 테스트에 복제하지 않는다(드리프트 방지)."""
    return int(rgba.rsplit(",", 1)[1].strip(" )"))


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("base", ["panel", "bg"])
def test_selected_tile_label_on_its_own_tint(mode, base):
    """선택 타일 **라벨**은 자기 틴트 위에서 프로젝트 게이트(5.0)를 넘어야 한다.

    알파 36 에서 4.66~4.87 로 게이트를 깨고 있었다 — 라벨-대-면만 재던 표가 놓친 쌍."""
    theme.set_color_mode(mode)
    c = theme.COLORS
    surface = _mix(c["accent"], _tint_alpha(theme.ACCENT_TINT), c[base])
    r = _ratio(c["accent"], surface)
    assert r >= 5.0, f"{mode}/{base}: 선택 타일 라벨 {r:.2f}"


@pytest.mark.parametrize("mode", _MODES)
def test_text_behind_scrim_stays_readable(mode):
    """반투명 스크림의 **요점**은 뒤 화면이 읽히는 것이다 — 게이트 5.0 을 지킨다.

    이전 알파(96/120)는 다크에서 4.43 이었고, 여유가 **적은** 다크에 오히려 더 두꺼운
    디밍을 줘 모드 간 관계가 거꾸로였다."""
    theme.set_color_mode(mode)
    c = theme.COLORS
    sr, sg, sb, sa = theme.SCRIM_RGBA
    scrim = "#%02X%02X%02X" % (sr, sg, sb)
    r = _ratio(_mix(scrim, sa, c["ink"]), _mix(scrim, sa, c["bg"]))
    assert r >= 5.0, f"{mode}: 스크림(알파 {sa}) 아래 본문 {r:.2f}"


def test_scrim_is_not_heavier_in_the_mode_with_less_headroom():
    """다크가 라이트보다 더 두꺼우면 안 된다(여유가 적은 쪽을 더 가리는 셈)."""
    theme.set_color_mode("light")
    light_a = theme.SCRIM_RGBA[3]
    theme.set_color_mode("dark")
    dark_a = theme.SCRIM_RGBA[3]
    assert dark_a <= light_a + 16, f"라이트 {light_a} / 다크 {dark_a}"


@pytest.mark.parametrize("mode", _MODES)
def test_loading_indicator_reads_against_its_track(mode):
    """스피너 호·busy 혜성이 **자기 트랙**과 3:1 이상.

    '모션 줄이기' + busy 조합에서는 이 대비가 '살아 있다'는 유일한 신호다.
    트랙을 `LINE` 으로 두면 1.90/1.84 로 진행분이 보이지 않았다."""
    theme.set_color_mode(mode)
    c = theme.COLORS
    r = _ratio(c["accent"], c["line2"])
    assert r >= 3.0, f"{mode}: 지시자 vs 트랙 {r:.2f}"
    # 실제로 그 토큰을 쓰는지 — 코드가 LINE 으로 되돌아가면 위 계산이 무의미해진다.
    import inspect

    from aoi_verification.app.ui.widgets import loading_overlay as lo
    for fn in (lo._SpinnerDot.paintEvent, lo._BusyStripe.paintEvent):
        src = inspect.getsource(fn)
        code = "\n".join(ln for ln in src.splitlines()
                         if not ln.strip().startswith("#"))
        assert "theme.LINE2" in code, f"{fn.__qualname__} 트랙이 LINE2 가 아니다"


@pytest.mark.parametrize("mode", _MODES)
def test_warn_is_a_distinct_channel_from_body_ink(mode):
    """★ 라이트 `warn` 이 `ink` 와 **바이트 단위로 같았다** — 경고 채널의 신호가 0.

    그런데 대비표는 그 쌍을 15.68 로 적어 표에서 가장 좋아 보이게 했다(측정이
    거짓을 보증한 사례).  경고는 본문과 구분되어야 하고 면 위에서 읽혀야 한다."""
    theme.set_color_mode(mode)
    c = theme.COLORS
    assert c["warn"].upper() != c["ink"].upper(), "warn 이 ink 와 동일하다"
    # ★ 휘도비로 재면 안 된다 — 어두운 모드에서는 warn·ink 가 **둘 다 밝아** 휘도가
    #   비슷하고(1.22) 구분은 색상이 한다.  채널 차이로 '다른 채널인가'를 본다.

    def ch(h):
        h = h.lstrip("#")
        return [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    delta = max(abs(a - b) for a, b in zip(ch(c["warn"]), ch(c["ink"])))
    assert delta >= 60, f"{mode}: warn 이 ink 와 구분되지 않는다 (maxΔ={delta})"
    for surface in ("panel", "bg"):
        r = _ratio(c["warn"], c[surface])
        assert r >= 5.0, f"{mode}: warn on {surface} = {r:.2f}"


@pytest.mark.parametrize("mode", _MODES)
def test_toggle_switch_focus_ring_contrasts_with_its_track(mode):
    """★ 커스텀 페인트 스위치 — ON 이면 트랙이 `accent` 라 `focus` 링이 1.23:1 로 묻힌다.

    채운 primary 버튼에서 고친 것과 **같은 함정**이 여기 남아 있었고, 픽셀 테스트가
    `checked=False` 만 검사해 통과했다.  두 상태를 모두 검사한다."""
    theme.set_color_mode(mode)
    c = theme.COLORS
    # OFF 트랙 = line_strong, ON 트랙 = accent.  각 상태의 링 색이 그 위에서 보여야 한다.
    # 링은 트랙 위에 그려진다 — 하나의 링 색(on_accent)이 **두 트랙 색** 모두와
    # 대비되어야 한다.  FOCUS 는 OFF 트랙에서 1.78(다크)로 묻혔다.
    assert _ratio(c["on_accent"], c["line_strong"]) >= 3.0, "OFF 링이 트랙에 묻힌다"
    assert _ratio(c["on_accent"], c["accent"]) >= 3.0, "ON 링이 트랙에 묻힌다"


@pytest.mark.parametrize("mode", _MODES)
def test_toggle_switch_focus_ring_renders_when_on(qapp, mode):
    """ON 상태에서도 링이 실제로 렌더되는지 — 이전 테스트는 OFF 만 봤다."""
    assert _focus_pixels(
        qapp, mode,
        lambda p: SwitchRow("구형 모드", checked=True, parent=p),
        pick=lambda row: row.switch, ring="on_accent",
    ), f"{mode}: ON 스위치 포커스 링 미렌더"


def test_action_grade_controls_actually_render_at_44(qapp):
    """★ QSS `min-height` 는 `setMinimumHeight()` 를 덮어쓴다.

    일반 QPushButton 의 min-height(26)가 액션바의 setMinimumHeight(46)를 이겨
    [검증 시작]이 실측 40px 로 렌더됐다 — 옵션 타일(58px)보다 작았다.
    '주장 44' 가 아니라 **렌더 44** 를 검사한다."""
    from aoi_verification.app.ui.pages import setup_layouts as sl
    page = sl.LAYOUTS["a"]()
    try:
        qapp.setStyleSheet(theme.render_qss(_QSS))
        page.resize(1512, 982)
        page.show()
        for _ in range(14):
            qapp.processEvents()
        for name, w in (("검증 시작", page.start_btn),
                        ("업데이트 확인", page.update_btn)):
            assert w.height() >= 44, f"{name} 실측 {w.height()}px < 44"
    finally:
        page.deleteLater()


def test_role_variants_have_their_own_disabled_rule():
    """★ `:focus` 와 **완전히 같은 특이도 함정** — role 규칙이 일반 `:disabled` 를 덮는다.

    `[role="ghost"] { color: $ink2 }` 때문에 비활성 ± 버튼 글리프가 활성과 픽셀
    동일(#3D3B35)하게 렌더됐다."""
    for role in ("ghost", "danger", "warn", "primary"):
        assert f'QPushButton[role="{role}"]:disabled' in _QSS, \
            f'role="{role}" 전용 :disabled 규칙이 없다'


def test_subcontrol_selectors_use_the_valid_order():
    """★ Qt 는 `위젯::서브컨트롤:상태` 만 인식한다.

    `위젯:상태::서브컨트롤` 로 쓰면 규칙이 **무시**되거나(비활성 슬라이더가 활성과
    픽셀 동일) **상태와 무관하게 항상** 적용된다(체크박스에 상시 가짜 포커스 링).
    둘 다 실제로 버그를 냈다."""
    import re
    bad = re.findall(r"^[A-Za-z]+:[a-z-]+::[a-z-]+", _QSS, re.MULTILINE)
    assert not bad, f"의사상태가 서브컨트롤 앞에 온 선택자: {bad}"
