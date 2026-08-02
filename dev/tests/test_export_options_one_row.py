"""엑셀 저장 옵션 2개는 **한 줄에 나란히**, 버튼 줄 **위**에 있다(사용자 선택 1안).

실측 버그: '사진을 원본 화질로 넣기' 는 버튼 줄 위, '전체 양식 포함' 은 버튼 줄
아래로 **110px 갈라져** 있었다.  둘 다 '엑셀로 저장' 에만 걸리는 옵션인데 버튼
줄이 사이를 가르니, 원본 화질이 저장 옵션인지 화면 보기 옵션인지 알 수 없었다.

여기서 못박는 것은 '같은 y 에 있다'(한 줄) 와 '버튼보다 위'(옵션 → 실행 순서) 다.
픽셀 값이 아니라 **관계**를 검사한다 — 여백을 조금 바꿨다고 깨지면 안 된다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")


@pytest.fixture()
def page(styled_qapp):
    from aoi_verification.app.ui.pages import result_page as rp

    p = rp.ResultPage()
    p.resize(1512, 950)
    p.show()
    for _ in range(8):
        styled_qapp.processEvents()
    yield p
    p.close()


def _top(w) -> int:
    """페이지 좌표계에서의 y."""
    return w.mapTo(w.window(), w.rect().topLeft()).y()


def _left(w) -> int:
    return w.mapTo(w.window(), w.rect().topLeft()).x()


def test_two_export_options_share_one_row(page):
    a, b = page.original_quality_chk, page.full_template_chk
    gap = abs(_top(a) - _top(b))
    assert gap <= 4, (
        f"두 저장 옵션의 세로 차이가 {gap}px — 한 줄에 있어야 한다")


def test_options_come_before_the_action_buttons(page):
    """옵션이 버튼보다 위 — 고르고 나서 누르는 순서."""
    opt_y = _top(page.original_quality_chk)
    btn_y = _top(page.export_btn)
    assert opt_y < btn_y, (
        f"저장 옵션(y={opt_y})이 '엑셀로 저장'(y={btn_y}) 보다 아래에 있다")
    assert _top(page.full_template_chk) < btn_y


def test_options_do_not_overlap_each_other(page):
    """한 줄에 뒀으니 서로 겹치지 않아야 한다(라벨이 길어지면 겹칠 수 있다)."""
    a, b = page.original_quality_chk, page.full_template_chk
    first, second = sorted((a, b), key=_left)
    assert _left(first) + first.width() <= _left(second), (
        "두 체크박스가 가로로 겹친다")


def test_both_options_still_drive_the_export(page):
    """배치만 바꿨지 기능은 그대로 — 두 옵션이 살아 있고 기본은 해제다."""
    assert page.original_quality_chk.isChecked() is False
    assert page.full_template_chk.isChecked() is False
    for chk in (page.original_quality_chk, page.full_template_chk):
        chk.setChecked(True)
        assert chk.isChecked() is True
        chk.setChecked(False)
