"""설정 화면 배치안(A~E) — 앱 안에서 버튼으로 즉시 갈아끼운다.

이전에는 ``dev/layout_variants/*.patch`` 를 적용하고 앱을 다시 띄워야 비교할 수
있었다.  이제 첫 화면의 '배치안' 버튼이 본문만 다시 그린다.

여기서 지키는 것:
- 6개 안(원본 + A~E) 모두 전환되고, 전환해도 **입력값이 살아 있다**.
- 각 안이 실제로 **서로 다른 배치**를 만든다(이름만 다른 게 아니다).
- 최종안을 고른 뒤 나머지를 지울 수 있도록 안 구현이 서로 독립이다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QLabel, QWidget      # noqa: E402

from aoi_verification.app import i18n                          # noqa: E402
from aoi_verification.app.ui import theme                      # noqa: E402
from aoi_verification.app.ui.pages.setup_page import (         # noqa: E402
    LAYOUT_DEFAULT, LAYOUT_VARIANTS, SetupPage)
from aoi_verification.app.ui.widgets.neon_card import NeonCard  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    theme.apply_to_app(app)
    return app


_TITLES = {i18n.KO.RUN_OPTIONS_TITLE, i18n.KO.ENGINE_CARD_TITLE}


def _setting_cards(page: SetupPage) -> list[NeonCard]:
    """'실행 옵션'·'매칭 설정' 두 카드만 골라낸다(폴더 카드 제외)."""
    out = []
    for card in page.findChildren(NeonCard):
        for lb in card.findChildren(QLabel):
            if lb.text() in _TITLES:
                out.append(card)
                break
    return out


def _shape(page: SetupPage, qapp) -> tuple:
    """배치를 요약한 지문 — (카드 폭들, 높이 합)."""
    for _ in range(3):
        qapp.processEvents()
    cards = _setting_cards(page)
    return (tuple(c.width() for c in cards), sum(c.height() for c in cards))


def _page(qapp, variant: str, width: int = 1512) -> SetupPage:
    p = SetupPage()
    p.resize(width, 900)
    p._apply_layout_variant(variant)
    p.show()
    for _ in range(3):
        qapp.processEvents()
    return p


def test_default_is_current_layout():
    assert LAYOUT_DEFAULT == "base"
    assert [k for k, _l, _t in LAYOUT_VARIANTS][0] == "base"
    assert {k for k, _l, _t in LAYOUT_VARIANTS} == {"base", "A", "B", "C", "D", "E"}


def test_every_variant_switches_and_keeps_input(qapp):
    """모든 안으로 전환되고, 입력해 둔 폴더 경로가 살아남는다."""
    p = _page(qapp, LAYOUT_DEFAULT)
    try:
        p.ref_path_edit.setText("/tmp/ref")
        p.val_path_edit.setText("/tmp/val")
        for key, _label, _tip in LAYOUT_VARIANTS:
            p._apply_layout_variant(key)
            qapp.processEvents()
            assert p._layout_variant == key
            assert p.ref_path_edit.text() == "/tmp/ref", f"{key}: 기준 폴더 유실"
            assert p.val_path_edit.text() == "/tmp/val", f"{key}: 검증 폴더 유실"
            assert p._variant_buttons[key].isChecked(), f"{key}: 버튼 선택 표시 안 됨"
    finally:
        p.close()


def test_variants_produce_distinct_layouts(qapp):
    """안마다 배치 지문이 달라야 한다 — 같으면 '이름만 다른 안'이다."""
    shapes = {}
    for key, _label, _tip in LAYOUT_VARIANTS:
        p = _page(qapp, key)
        try:
            shapes[key] = _shape(p, qapp)
        finally:
            p.close()
    # 원본과 같은 지문을 갖는 안이 없어야 한다.
    base = shapes["base"]
    same = [k for k, v in shapes.items() if k != "base" and v == base]
    assert not same, f"원본과 배치가 같은 안: {same} ({base})"


def test_side_by_side_halves_card_width(qapp):
    """A — 두 카드가 가로로 나란히 서면 각 카드 폭이 원본의 절반 수준."""
    base = _page(qapp, "base")
    a = _page(qapp, "A")
    try:
        (bw, _), (aw, _) = _shape(base, qapp), _shape(a, qapp)
        assert len(aw) == 2 and len(bw) == 2
        assert max(aw) < max(bw) * 0.6, f"A 가 나란히 서지 않았다: {aw} vs {bw}"
    finally:
        base.close()
        a.close()


def test_single_column_narrows_content(qapp):
    """C — 본문을 읽기 좋은 폭으로 좁힌다(카드가 창 전체를 쓰지 않는다)."""
    p = _page(qapp, "C", width=1512)
    try:
        widths, _ = _shape(p, qapp)
        assert widths and max(widths) <= 600, f"C 가 좁아지지 않았다: {widths}"
    finally:
        p.close()


def test_side_rail_is_fixed_width(qapp):
    """E — 오른쪽 보조 레일은 고정폭(264px)이고 본론이 남는 폭을 갖는다."""
    p = _page(qapp, "E", width=1512)
    try:
        widths, _ = _shape(p, qapp)
        assert 264 in widths, f"레일 고정폭이 없다: {widths}"
        assert max(widths) > 264 * 2, f"본론이 남는 폭을 못 가졌다: {widths}"
    finally:
        p.close()


def test_two_column_variants_are_shorter(qapp):
    """B·D — 카드 안을 2열로 흘리면 세로가 짧아진다(빈 가로를 세로와 바꾼다)."""
    base = _page(qapp, "base")
    try:
        _, base_h = _shape(base, qapp)
    finally:
        base.close()
    for key in ("B", "D"):
        p = _page(qapp, key)
        try:
            _, h = _shape(p, qapp)
            assert h < base_h, f"{key}: 세로가 안 줄었다 {h} >= {base_h}"
        finally:
            p.close()
