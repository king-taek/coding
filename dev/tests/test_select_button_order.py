"""1단계 버튼 배치와 방향키 계약.

★ 이 화면에는 회귀 가드가 **하나도 없었다** — 버튼 순서도 단축키도 아무 테스트가
  잡지 않아, 방향이 조용히 어긋난 채 오래 남아 있었다(왼쪽을 누르면 사진이 오른쪽
  패널로 갔다).  배치와 키가 **같은 방향**이라는 것이 이 화면의 계약이다.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt                                    # noqa: E402
from PyQt6.QtGui import QKeySequence                           # noqa: E402
from PyQt6.QtWidgets import QApplication                       # noqa: E402

from aoi_verification.app import i18n                          # noqa: E402
from aoi_verification.app.ui.pages import select_page as sp    # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _button_order(page) -> list:
    bar = page._btn_row
    return [bar.itemAt(i).widget() for i in range(bar.count())
            if bar.itemAt(i).widget() is not None]


def test_exclude_sits_left_of_verify(qapp):
    """[제외]가 왼쪽, [검증]이 오른쪽.

    오른쪽 패널이 '검증 대상' 이므로 **오른쪽으로 보내면 오른쪽에 쌓인다.**
    되돌리기는 stretch 앞에 홀로 남아 왼쪽 끝을 지킨다.
    """
    page = sp.SelectPage()
    try:
        order = _button_order(page)
        assert order[0] is page.btn_undo, "되돌리기는 왼쪽 끝"
        assert order[-2:] == [page.btn_exclude, page.btn_verify], \
            "제외 → 검증 순서여야 한다(화면 방향 = 키 방향)"
    finally:
        page.deleteLater()


def test_bulk_actions_follow_the_same_order(qapp):
    """다중 선택 패널의 액션도 같은 순서 — 한 화면에서 순서가 갈리면 안 된다."""
    page = sp.SelectPage()
    try:
        ids = [a[0] for a in page.left_panel._actions]
        assert ids == ["batch_exclude", "batch_verify"]
    finally:
        page.deleteLater()


def _shortcut_map(page) -> dict[str, str]:
    """등록된 QShortcut → 어떤 동작에 묶였는지."""
    from PyQt6.QtGui import QShortcut
    out: dict[str, str] = {}
    seen: list[str] = []
    for sc in page.findChildren(QShortcut):
        seen.append(sc.key().toString())
    return {"keys": seen}


def test_arrow_keys_follow_the_layout(qapp):
    """→ = 검증, ← = 제외.  실제로 눌러서 확인한다(등록 여부만 보지 않는다)."""
    from pathlib import Path
    from PyQt6.QtTest import QTest

    page = sp.SelectPage()
    try:
        queue = [sp.ImageItem(path=Path(f"/tmp/a_{i}.jpg"), slot="A01", side="ref")
                 for i in range(4)]
        page.load_state(queue=queue)
        page.show()
        qapp.processEvents()

        QTest.keyClick(page, Qt.Key.Key_Right)
        assert len(page._state.targets["A01"]) == 1, "→ 는 검증이어야 한다"
        assert not page._state.excluded["A01"]

        QTest.keyClick(page, Qt.Key.Key_Left)
        assert len(page._state.excluded["A01"]) == 1, "← 는 제외여야 한다"
        assert len(page._state.targets["A01"]) == 1
    finally:
        page.hide()
        page.deleteLater()


def test_number_keys_do_nothing(qapp):
    """1·2 는 없앴다 — 방향키가 배치를 그대로 따르므로 외울 것은 하나면 된다.

    남아 있으면 배치와 어긋난 옛 의미(1=검증)가 조용히 살아남는다.
    """
    from pathlib import Path
    from PyQt6.QtTest import QTest

    page = sp.SelectPage()
    try:
        queue = [sp.ImageItem(path=Path(f"/tmp/b_{i}.jpg"), slot="A01", side="ref")
                 for i in range(4)]
        page.load_state(queue=queue)
        page.show()
        qapp.processEvents()
        before = (len(page._state.targets["A01"]), len(page._state.excluded["A01"]))

        for key in (Qt.Key.Key_1, Qt.Key.Key_2):
            QTest.keyClick(page, key)
        after = (len(page._state.targets["A01"]), len(page._state.excluded["A01"]))
        assert after == before, "숫자 키는 아무 일도 하지 않아야 한다"
    finally:
        page.hide()
        page.deleteLater()


def test_tooltip_matches_the_real_keys(qapp):
    """툴팁이 실제 키와 어긋나면 안내가 거짓말이 된다."""
    tip = i18n.KO.SHORTCUT_TOOLTIP
    assert "→ = 검증" in tip and "← = 제외" in tip
    assert "1" not in tip and "2" not in tip, "없앤 키를 안내하지 않는다"

    page = sp.SelectPage()
    try:
        for btn in (page.btn_verify, page.btn_exclude, page.btn_undo):
            assert btn.toolTip() == tip
    finally:
        page.deleteLater()


def test_how_to_use_body_agrees_with_the_keys(qapp):
    """셋업의 '사용 방법' 도 같은 방향을 말해야 한다(문서 두 곳이 갈리면 안 된다)."""
    body = i18n.KO.SETUP_HOW_TO_USE_BODY
    assert "→ = 검증" in body and "← = 제외" in body
    assert "[✕ 제외] / [✓ 검증]" in body, "화면 순서와 같게 적는다"
