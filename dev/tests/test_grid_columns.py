"""격자 열 계산 — 공식은 `columns_for_width` 한 곳에만 있다.

세 화면(옵션 타일 줄·다중 선택 그리드·썸네일 격자)이 같은 계산을 **각자** 적고
있었는데, 그중 둘은 ``avail // (min_w + spacing)`` 이라 **마지막 열에도** 간격을
물려 한 열을 덜 줬다.  같은 폭에서 화면마다 열 수가 달랐다는 뜻이다.

판정 기준은 하나다: **가로 스크롤이 나지 않을 것.**  그래서 순수 공식(넘치지
않는 최대 열)과 실제 위젯 배치(여러 폭에서 내용 폭 ≤ 뷰포트 폭) 둘 다 본다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from aoi_verification.app.ui.widgets.option_group import columns_for_width


# ── 순수 공식 (Qt 없이) ────────────────────────────────────────────────────
def _fits(cols: int, min_w: int, spacing: int) -> int:
    """``cols`` 열을 놓는 데 실제로 필요한 폭."""
    return cols * min_w + (cols - 1) * spacing


@pytest.mark.parametrize("avail, min_w, spacing", [
    (736, 360, 8),      # 보정 없으면 1열로 계산되던 실측 사례
    (1000, 194, 8),
    (368, 180, 8),      # 딱 2열이 들어맞는 경계
    (367, 180, 8),      # 1px 모자람 → 1열
    (1920, 194, 8),
    (300, 300, 0),      # 간격 0
])
def test_result_is_the_largest_column_count_that_fits(avail, min_w, spacing):
    """넘치지 않는 **최대** 열 수 — 한 열 더 놓으면 반드시 넘친다."""
    cols = columns_for_width(avail, min_w, spacing)
    assert _fits(cols, min_w, spacing) <= avail or cols == 1
    assert _fits(cols + 1, min_w, spacing) > avail


def test_exact_fit_is_not_rounded_down():
    """★ 옛 공식(`avail // (min_w + spacing)`)이 틀렸던 지점.

    2열에 필요한 폭은 360+360+8 = 728 인데, 옛 공식은 736 을 주고도 1열을 냈다."""
    assert columns_for_width(736, 360, 8) == 2
    assert columns_for_width(728, 360, 8) == 2      # 정확히 들어맞는 폭
    assert columns_for_width(727, 360, 8) == 1


def test_unknown_width_falls_back_to_one_column():
    """폭을 아직 모를 때(0/음수)는 1열 — 그 상태로 배치해도 넘치지 않는다."""
    assert columns_for_width(0, 180, 8) == 1
    assert columns_for_width(-50, 180, 8) == 1
    assert columns_for_width(1, 180, 8) == 1


def test_degenerate_inputs_do_not_divide_by_zero():
    assert columns_for_width(500, 0, 0) >= 1
    assert columns_for_width(500, 180, -5) >= 1


# ── 실제 위젯 — 여러 폭에서 가로 스크롤 0 ──────────────────────────────────
pytest.importorskip("PyQt6.QtWidgets")

from pathlib import Path                                        # noqa: E402

from aoi_verification.app.models.slot import ImageItem          # noqa: E402
from aoi_verification.app.ui.widgets import thumb_grid as tg    # noqa: E402


@pytest.fixture(scope="module")
def qapp(styled_qapp):
    return styled_qapp


_WIDTHS = (400, 640, 800, 1024, 1280, 1600, 1920)


@pytest.mark.parametrize("width", _WIDTHS)
def test_thumb_grid_never_overflows_horizontally(qapp, width, monkeypatch):
    """썸네일 격자 — 어느 폭에서도 내용이 패널 폭을 넘지 않는다."""
    # 디코드 없이 배치만 본다(파일이 없어도 되게).
    monkeypatch.setattr(tg._ThumbTile, "_load", lambda self: None, raising=False)
    grid = tg.ThumbGrid(columns=6)
    try:
        grid.set_entries([
            tg.ThumbEntry(ImageItem("S1", Path(f"/tmp/{i}.png"), "val"))
            for i in range(12)
        ])
        grid.resize(width, 600)
        qapp.processEvents()
        cols = grid._effective_columns()
        assert 1 <= cols <= 6
        tile_w = (grid._tile_px or tg.THUMB_PX) + tg._TILE_CHROME_W
        need = cols * tile_w + (cols - 1) * grid._grid.spacing()
        assert need <= width, f"{width}px 에서 {cols}열은 {need}px 를 먹는다"
        # 한 열 더 놓을 수 있는데 안 놓고 있지는 않은지(낭비 방지).
        if cols < 6:
            assert (cols + 1) * tile_w + cols * grid._grid.spacing() > width
    finally:
        grid.deleteLater()


def test_thumb_grid_keeps_configured_columns_before_layout(qapp):
    """폭이 아직 0 이면 설정된 열 수를 유지한다(첫 배치 전 깜빡임 방지)."""
    grid = tg.ThumbGrid(columns=4)
    try:
        assert grid.width() <= 0 or grid._effective_columns() >= 1
        grid.resize(0, 0)
        assert grid._effective_columns() == 4
    finally:
        grid.deleteLater()


from aoi_verification.app.models.slot import ImageItem as _II    # noqa: E402
from aoi_verification.app.ui.widgets import (                    # noqa: E402
    bulk_select_dialog as bsd)


def _grid_cols(grid) -> int:
    """그리드에 **실제로** 쓰인 열 수 — 공식을 다시 계산하지 않고 배치를 읽는다."""
    return max((grid.getItemPosition(i)[1] + 1 for i in range(grid.count())),
               default=0)


@pytest.mark.parametrize("width", _WIDTHS)
def test_bulk_select_grid_never_overflows_horizontally(qapp, width):
    """다중 선택 그리드 — 어느 폭에서도 **실제 배치**가 가로로 넘치지 않는다.

    ★ 공식을 여기서 다시 계산하지 않는다.  재계산하면 위젯 실폭과 열 계산이
    갈라져도(예: 한쪽만 옛 상수를 들고 있어도) 테스트 쪽 계산은 양쪽 다 새 값을
    써서 조용히 통과한다 — 실제로 그런 누락이 있었다.  그래서 **운영 경로**
    (``_relayout_grids``)를 태우고 **배치된 열 수와 타일의 실제 폭**을 읽는다.

    이 화면은 ``_COLS`` 상한(5)을 함께 건다 — 공식을 공유해도 그 상한은 화면
    고유 규칙이라 여기 남는다."""
    data = {"S1": [_II(slot="S1", path=Path(f"/tmp/b{i}.jpg"), side="ref")
                   for i in range(12)]}
    dlg = bsd.BulkSelectDialog("t", data, actions=[("x", "X", "primary")])
    try:
        # 스크롤 뷰포트가 실제 폭을 갖도록 한 번 띄운다(offscreen).
        dlg.resize(width, 800)
        dlg.show()
        qapp.processEvents()
        dlg._relayout_grids()                      # 운영 경로
        qapp.processEvents()
        avail = dlg._scroll.viewport().width()
        assert avail > 0, "뷰포트 폭을 못 얻었다 — 가드가 헛돈다"

        _items, grid = dlg._slot_grids[0]
        cols = _grid_cols(grid)
        assert 1 <= cols <= bsd._COLS

        # 타일 실폭은 위젯에게 직접 묻는다(setFixedSize 라 값이 확정돼 있다).
        tile_w = max(w.width() for w in
                     (grid.itemAt(i).widget() for i in range(grid.count()))
                     if w is not None)
        need = cols * tile_w + (cols - 1) * grid.spacing()
        assert need <= avail, \
            f"가용 {avail}px 인데 {cols}열 × {tile_w}px = {need}px 를 먹는다"
        # 상한(_COLS)에 걸린 게 아니라면 한 열 더는 못 놓는 최대값이어야 한다.
        if cols < bsd._COLS:
            assert (cols + 1) * tile_w + cols * grid.spacing() > avail
    finally:
        dlg.hide()
        dlg.deleteLater()


def test_bulk_select_window_width_matches_its_column_cap(qapp):
    """창 기본 폭이 상한 열 수를 실제로 담는다 — 열자마자 가로 스크롤이면 실패다."""
    want_w = bsd._COLS * (bsd._TILE_PX + bsd._TILE_CHROME_W
                          + bsd._GRID_SPACING) + 80
    inner = want_w - 80                       # 여백을 뺀 그리드 가용 폭
    assert columns_for_width(inner, bsd._TILE_PX + bsd._TILE_CHROME_W,
                             bsd._GRID_SPACING) >= bsd._COLS
