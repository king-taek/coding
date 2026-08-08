"""엑셀 저장 옵션 3개는 **한 줄에 나란히**, 버튼 줄 **위**에 있다(사용자 선택 1안).

실측 버그: '사진을 원본 화질로 넣기' 는 버튼 줄 위, '전체 양식 포함' 은 버튼 줄
아래로 **110px 갈라져** 있었다.  전부 '엑셀로 저장' 에만 걸리는 옵션인데 버튼
줄이 사이를 가르니, 원본 화질이 저장 옵션인지 화면 보기 옵션인지 알 수 없었다.

여기서 못박는 것은 '같은 y 에 있다'(한 줄) 와 '버튼보다 위'(옵션 → 실행 순서),
그리고 '미매칭만 원본' 이 '사진을 원본 화질로' **왼쪽**이라는 순서다.
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


def _options(page):
    return (page.unmatched_original_chk, page.original_quality_chk,
            page.full_template_chk)


def test_export_options_share_one_row(page):
    opts = _options(page)
    tops = [_top(w) for w in opts]
    gap = max(tops) - min(tops)
    assert gap <= 4, (
        f"저장 옵션들의 세로 차이가 {gap}px — 한 줄에 있어야 한다")


def test_options_come_before_the_action_buttons(page):
    """옵션이 버튼보다 위 — 고르고 나서 누르는 순서."""
    btn_y = _top(page.export_btn)
    for w in _options(page):
        assert _top(w) < btn_y, (
            f"저장 옵션(y={_top(w)})이 '엑셀로 저장'(y={btn_y}) 보다 아래에 있다")


def test_options_do_not_overlap_each_other(page):
    """한 줄에 뒀으니 서로 겹치지 않아야 한다(라벨이 길어지면 겹칠 수 있다)."""
    ordered = sorted(_options(page), key=_left)
    for first, second in zip(ordered, ordered[1:]):
        assert _left(first) + first.width() <= _left(second), (
            "체크박스가 가로로 겹친다")


def test_unmatched_option_is_left_of_original_quality(page):
    """'미매칭 사진만 원본' 은 '사진을 원본 화질로' **왼쪽**(사용자 요청 배치)."""
    assert _left(page.unmatched_original_chk) < _left(page.original_quality_chk)


def test_all_options_still_drive_the_export(page):
    """배치만 바꿨지 기능은 그대로 — 옵션이 살아 있고 기본은 해제다."""
    for chk in _options(page):
        assert chk.isChecked() is False
        chk.setChecked(True)
        assert chk.isChecked() is True
        chk.setChecked(False)
