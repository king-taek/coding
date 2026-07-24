"""셋업 상단 화면 스타일 스위처 — 선택 칩 '채움' 렌더 회귀 방지.

회귀: QScrollArea 뷰포트에 '맨 선언(bare)' 스타일시트(``background: transparent``)를
주면 자식으로 캐스케이드돼 스위처 칩의 ``:checked`` 배경(채움)을 덮어써 '빈 박스' 로
렌더됐다.  뷰포트 규칙을 objectName 으로 스코프해 해결 — 이 테스트가 그 채움을 지킨다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication          # noqa: E402

from aoi_verification.app.ui import theme          # noqa: E402

_QSS = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui"
        / "style.qss").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _accent_fill_fraction(variant: str, qapp) -> float:
    theme.set_variant(variant)
    qapp.setStyleSheet(theme.render_qss(_QSS))
    from aoi_verification.app.ui.pages.setup_page import SetupPage
    sp = SetupPage()
    sp.resize(1512, 400)
    sp.show()
    for _ in range(15):
        qapp.processEvents()
    btn = sp._variant_btns[variant]              # 현재 선택된 칩
    assert btn.isChecked()
    pm = btn.grab()
    img = pm.toImage()
    ac = theme.VARIANTS[variant].colors["accent"].lstrip("#")
    ar, ag, ab = int(ac[0:2], 16), int(ac[2:4], 16), int(ac[4:6], 16)
    hits = total = 0
    for y in range(2, pm.height() - 2, 2):
        for x in range(2, pm.width() - 2, 2):
            c = img.pixelColor(x, y)
            total += 1
            if (abs(c.red() - ar) < 40 and abs(c.green() - ag) < 40
                    and abs(c.blue() - ab) < 40):
                hits += 1
    sp.deleteLater()
    return hits / max(1, total)


@pytest.mark.parametrize("variant",
                         ["instrument", "cleanroom", "datum", "clarity", "patina"])
def test_selected_switcher_chip_is_filled(qapp, variant):
    """선택 칩 내부가 accent 색으로 '채워져' 있어야 한다(빈 박스 아님).

    스코프 안 된 뷰포트 스타일시트가 다시 새면 채움이 사라져 이 값이 급감한다."""
    try:
        frac = _accent_fill_fraction(variant, qapp)
        assert frac > 0.4, f"{variant}: 선택 칩 채움 {frac:.0%} (빈 박스 회귀 의심)"
    finally:
        theme.set_variant("instrument")
