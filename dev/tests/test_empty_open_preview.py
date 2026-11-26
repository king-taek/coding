"""EmptyOpen 의 '흐린 화면 미리보기' 배경 — 자산 · 그리기 · 폴백 · 5개 화면의 키."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QColor, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QWidget  # noqa: E402

from aoi_verification.app.utils import paths  # noqa: E402

KEYS = ("info", "map", "gl", "overkill", "zone")
WIDGETS = Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui" / "widgets"
SITES = {
    "info": "image_info_dialog.py", "map": "wafer_map_dialog.py", "gl": "gl_viewer_dialog.py",
    "overkill": "threshold_preview_dialog.py", "zone": "zone_origin_dialog.py",
}


@pytest.mark.parametrize("key", KEYS)
def test_asset_exists_and_loads(styled_qapp, key):
    path = paths.logo_path(f"empty_preview_{key}.jpg")
    assert path.exists()
    assert not QPixmap(str(path)).isNull()


def _host(styled_qapp, preview, w=900, h=600):
    from aoi_verification.app.ui.widgets.tool_open_save import EmptyOpen
    host = QWidget()
    host.resize(w, h)
    eo = EmptyOpen(host, "사진 선택", lambda: None, "끌어다 놓기 가능", preview=preview)
    host.show()
    eo.set_empty(True)
    styled_qapp.processEvents()
    return host, eo


def _dist(a: QColor, b: QColor) -> int:
    return abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue())


def test_preview_paints_content_with_halo_around_button(styled_qapp):
    from aoi_verification.app.ui import theme
    host, eo = _host(styled_qapp, "gl")
    img = eo.grab().toImage()
    bg = QColor(theme.BG)
    # 버튼 둘레(후광 한가운데, 버튼 바로 아래 안내 글자 옆)는 배경색에 아주 가깝다
    c = eo.btn.geometry().center()
    near = img.pixelColor(c.x() - eo.btn.width() // 2 - 25, c.y())
    assert _dist(near, bg) <= 80      # 후광 알파 ≈ 0.7 — 그림이 약간 비친다
    # 멀리 떨어진 가장자리는 그림이 비쳐 배경 한 색이 아니다
    far = {img.pixelColor(x, y).rgb() for x in range(5, 300, 7) for y in range(5, 200, 7)}
    assert len(far) > 20
    host.close()


def test_no_preview_is_plain_background(styled_qapp):
    from aoi_verification.app.ui import theme
    host, eo = _host(styled_qapp, "")
    img = eo.grab().toImage()
    bg = QColor(theme.BG)
    assert _dist(img.pixelColor(5, 5), bg) <= 3
    assert len({img.pixelColor(x, 5).rgb() for x in range(0, 200, 5)}) == 1
    host.close()


def test_missing_asset_falls_back_without_raising(styled_qapp, caplog):
    from aoi_verification.app.ui import theme
    with caplog.at_level("WARNING"):
        host, eo = _host(styled_qapp, "does_not_exist")
    assert any("빈 화면 배경" in r.message for r in caplog.records)
    img = eo.grab().toImage()
    assert _dist(img.pixelColor(5, 5), QColor(theme.BG)) <= 3
    host.close()


def test_resize_rebuilds_cover_cache(styled_qapp):
    host, eo = _host(styled_qapp, "map")
    eo.grab()
    first = eo._scaled.size()
    host.resize(1200, 800)
    styled_qapp.processEvents()
    eo.grab()
    assert eo._scaled.size() == eo.size() != first
    host.close()


@pytest.mark.parametrize("key", KEYS)
def test_call_sites_pass_their_key(key):
    src = (WIDGETS / SITES[key]).read_text(encoding="utf-8")
    m = re.search(r"EmptyOpen\((?:[^()]|\([^()]*\))*\)", src)
    assert m and f'preview="{key}"' in m.group(0)
