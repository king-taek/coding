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


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("surface", ["elev", "panel", "bg"])
def test_interactive_border_meets_non_text_contrast(mode, surface):
    """평상시 컨트롤 경계가 **모든** 면에서 3:1 이상 — 안 보이는 입력란 재발 방지."""
    theme.set_color_mode(mode)
    c = theme.COLORS
    r = _ratio(c["line_strong"], c[surface])
    assert r >= 3.0, f"{mode}: line_strong on {surface} = {r:.2f}"


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_structural_rule_meets_non_text_contrast(mode):
    """카드 외곽·행 구분 눈금($line)도 시트 면·바탕에서 3:1 이상."""
    theme.set_color_mode(mode)
    c = theme.COLORS
    for surface in ("panel", "bg"):
        r = _ratio(c["line"], c[surface])
        assert r >= 3.0, f"{mode}: line on {surface} = {r:.2f}"


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_focus_ring_meets_contrast(mode):
    """포커스 링은 링이 놓이는 모든 면에서 3:1 이상이어야 보인다."""
    theme.set_color_mode(mode)
    c = theme.COLORS
    for surface in ("elev", "panel", "bg"):
        r = _ratio(c["focus"], c[surface])
        assert r >= 3.0, f"{mode}: focus on {surface} = {r:.2f}"


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
def _focus_pixels(qapp, mode: str, make, pick=None) -> bool:
    """포커스 색 픽셀이 실제로 그려졌는지 — QSS 에 링을 '선언'한 것만으로는 증명이 안 된다.

    ★ 자식 위젯을 단독으로 show() 하면 창이 활성화되지 않아 포커스가 실제로 들어가지
    않는다(그래서 링이 없는 것처럼 보인다).  반드시 최상위 창 안에 넣고 활성화한 뒤,
    포커스를 옮길 상대 위젯까지 둬서 진짜 탭 포커스 상태를 만든다."""
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
    fh = theme.COLORS["focus"].lstrip("#")
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


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("make,label", [
    (lambda p: QLineEdit(p), "입력란"),
    (lambda p: NeonButton("버튼", role="ghost", parent=p), "ghost 버튼"),
    (lambda p: NeonButton("시작", role="primary", parent=p), "primary 버튼"),
    (lambda p: NeonButton("삭제", role="danger", parent=p), "danger 버튼"),
    (lambda p: QDoubleSpinBox(p), "스핀박스"),
    (lambda p: QCheckBox("모션 줄이기", p), "체크박스"),
])
def test_focus_ring_actually_renders(qapp, mode, make, label):
    """★ 이 테스트가 ghost/danger 버튼의 링이 **아예 없던** 버그를 잡아냈다:
    `QPushButton[role="ghost"]` 의 border-color 가 특이도 동급·후순위로 일반
    `QPushButton:focus` 를 덮어쓰고 있었다."""
    assert _focus_pixels(qapp, mode, make), \
        f"{mode}/{label}: 포커스 링이 실제로 렌더되지 않았다"


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_toggle_switch_focus_ring_renders(qapp, mode):
    """커스텀 페인트 위젯 — QSS 가 아니라 paintEvent 가 링을 그린다."""
    assert _focus_pixels(
        qapp, mode,
        lambda p: SwitchRow("구형 모드", checked=False, parent=p),
        pick=lambda row: row.switch,
    ), f"{mode}: 스위치 포커스 링 미렌더"


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_option_tile_focus_ring_renders(qapp, mode):
    assert _focus_pixels(
        qapp, mode,
        lambda p: OptionGroup([("a", "가"), ("b", "나")], parent=p),
        pick=lambda g: g.button("a"),
    ), f"{mode}: 옵션 타일 포커스 링 미렌더"
