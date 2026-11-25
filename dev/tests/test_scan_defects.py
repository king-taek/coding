"""흑백 Scan 사진 한 장 → 그 안에 찍힌 Defect (`scan_image.defects_in`) — 회귀 가드.

사용자 요청(2026-11-24): 사진 정보 화면에 흑백 Scan(``X.Y.t.N.jpeg``)만 넣어도 Defect 위치를 잘라
정보를 보인다.  지키는 계약:

- **Color → Scan(`resolve`)의 정확한 역방향**이다 — 같은 식·같은 포함 조건·같은 Crop 범위.
  실측 11쌍(`test_scan_image.GOLDEN`, 사용자 클릭으로 확인)을 거꾸로 돌려 같은 자리가 나와야 한다.
- 값은 전부 폴더 파일에서 읽는다 — Scan 파일명의 두 수치는 절대좌표가 아니라 쓰지 않는다
  (GX57011871: 파일명 64715,-109148 ↔ 목록 230006.85,93208.63, `파일`).
- 레시피 N 의 픽셀 크기를 못 읽으면 실패다(레시피 1 값으로 대신하지 않는다).
- 레시피는 거르지 않는다(정방향도 거르지 않는다) — 같은 레시피가 먼저.
- 정렬 사진(``.wlup``)은 Defect 가 아니다.
- 정보 표는 Color 사진과 같은 생산자(`single_info.describe`)라 숫자가 같다.
"""
from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("PIL")
from PIL import Image                                          # noqa: E402

from aoi_verification.app import i18n                          # noqa: E402
from aoi_verification.app.coords import scan_image as si       # noqa: E402
from aoi_verification.app.coords import single_info            # noqa: E402



def _golden():
    """실측 11쌍은 `test_scan_image.py` 한 곳에만 둔다(두 벌 금지) — 그 파일을 읽어 쓴다."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "_scan_image_golden", Path(__file__).with_name("test_scan_image.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.GOLDEN, mod.SCAN_WH


GOLDEN, SCAN_WH = _golden()


@pytest.fixture(autouse=True)
def _fresh():
    from aoi_verification.app import coords
    coords.clear_caches()
    si.clear_caches()
    yield
    coords.clear_caches()
    si.clear_caches()


def _ini_section(name, x, y, recipe=1):
    return (f"[{name}]\nX={x}\nY={y}\nFaultX={x}\nFaultY={y}\nCol=1\nRow=1\n"
            f"RecipeNumber={recipe}\nType=0\n")


def _folder(tmp_path, name, *, scan, center, defects, wh=(320, 100), px=1.0,
            recipe_px=None, manifest=True, ini=True, scan_mode="L"):
    """``defects`` = [(섹션 이름, X, Y, 레시피)]."""
    f = tmp_path / name
    f.mkdir()
    Image.new(scan_mode, wh, 90).save(f / scan)
    if px is not None:
        (f / "Params_WaferInfo.ini").write_text(
            f"[General]\nRefPixelSizeX={px}\nRefPixelSizeY={px}\n")
    for n, v in (recipe_px or {}).items():
        (f / f"Recipe{n}-ProductInfo.ini").write_text(f"[Scan]\nScan2DPixelSize={v}\n")
    if manifest:
        (f / si.SCAN_LIST_NAME).write_text(f"Version=1\n{scan},{center[0]},{center[1]}\n")
    if ini:
        (f / "ColorImageGrabingInfo.ini").write_text(
            "".join(_ini_section(*d) for d in defects))
    return f


# ---------------------------------------------------------------------------
# 실측 11쌍 — 거꾸로 돌려도 같은 자리
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("row", GOLDEN, ids=[g[1] for g in GOLDEN])
def test_golden_pairs_round_trip(tmp_path, row):
    color, scan, defect, center, px, predicted, _clicked = row
    n = si.tile_recipe(scan)
    f = _folder(tmp_path, "W", scan=scan, center=center, wh=SCAN_WH,
                defects=[(color, *defect, n or 1)], px=px[0],
                recipe_px={n: px[0]} if n and n > 1 else None)
    m = si.defects_in(f / scan)
    assert m.ok and m.size_wh == SCAN_WH and m.pixel_xy == px
    (hit,) = m.hits
    assert hit.path.name == color
    assert abs(hit.uv[0] - predicted[0]) < 1e-6 and abs(hit.uv[1] - predicted[1]) < 1e-6
    # 정방향(Color → Scan)과 같은 Crop 범위
    fwd = si.resolve(f / color)
    assert fwd.ok and fwd.best.path.name == scan and fwd.best.box == hit.box


# ---------------------------------------------------------------------------
# 포함 · 순서 · 거르기
# ---------------------------------------------------------------------------
def test_inside_only_same_recipe_first_then_reading_order(tmp_path):
    # 320×100, px 1, 중심 (1000, 2000) → u = X − 840, v = Y − 1950
    scan = "-5.7.t.2.jpeg"
    f = _folder(tmp_path, "W", scan=scan, center=(1000, 2000), recipe_px={2: 1.0}, defects=[
        ("1100.1990.c.1.2.jpeg", 1100, 1990, 2),     # (260, 40)
        ("900.1960.c.2.2.jpeg", 900, 1960, 2),       # (60, 10) — 위쪽 먼저
        ("950.2000.c.3.1.jpeg", 950, 2000, 1),       # 다른 레시피 — 맨 뒤
        ("840.1950.c.4.2.jpeg", 840, 1950, 2),       # (0, 0) — 안
        ("1160.2000.c.5.2.jpeg", 1160, 2000, 2),     # u = 320 = W — 밖
        ("1000.2050.c.6.2.jpeg", 1000, 2050, 2),     # v = 100 = H — 밖
        ("999.1999.wlup.-12.jpeg", 999, 1999, 2),    # 정렬 사진 — Defect 아님
    ])
    m = si.defects_in(f / scan)
    assert m.ok
    assert [h.path.name for h in m.hits] == [
        "840.1950.c.4.2.jpeg", "900.1960.c.2.2.jpeg", "1100.1990.c.1.2.jpeg",
        "950.2000.c.3.1.jpeg"]
    assert [h.recipe for h in m.hits] == [2, 2, 2, 1]
    h = m.hits[2]
    assert h.uv == (260.0, 40.0) and h.box == si.crop_box((260.0, 40.0), (1.0, 1.0))


def test_scan_file_name_numbers_are_not_used(tmp_path):
    """Scan 파일명 앞 두 수치가 Defect 자리와 같아도 목록의 중심만 쓴다."""
    scan = "1000.2000.t.jpeg"
    f = _folder(tmp_path, "W", scan=scan, center=(50000, 60000),
                defects=[("1000.2000.c.1.jpeg", 1000, 2000, 1)])
    m = si.defects_in(f / scan)
    assert m.ok and m.hits == ()


def test_real_photo_path_when_present_else_section_name(tmp_path):
    scan = "-1.2.t.jpeg"
    f = _folder(tmp_path, "W", scan=scan, center=(1000, 2000), defects=[
        ("1000.2000.c.1.jpeg", 1000, 2000, 1),       # 사진 없음 → 섹션 이름
        ("1010.2000.c.2.jpeg", 1010, 2000, 1),       # 사진이 .JPG 로 있음 → 실제 경로
        ("1020.2000.c.3", 1020, 2000, 1),            # 섹션 이름에 확장자 없음 — INI 키는 '1020.2000.c'

    ])
    Image.new("RGB", (8, 8)).save(f / "1010.2000.c.2.JPG", format="JPEG")
    (f / "1000.2000.c.1.txt").write_text("사진 아님")              # 같은 stem 의 다른 파일은 고르지 않는다
    m = si.defects_in(f / scan)
    names = {h.path.name for h in m.hits}
    assert names == {"1000.2000.c.1.jpeg", "1010.2000.c.2.JPG", "1020.2000.c.3"}
    # 어느 경우든 stem 이 INI 키와 같아 정보가 읽힌다
    for h in m.hits:
        assert single_info.coord_measures(h.path), h.path.name


# ---------------------------------------------------------------------------
# 실패는 '0개' 와 구분되는 이유로
# ---------------------------------------------------------------------------
def test_failure_statuses(tmp_path):
    kw = dict(center=(1000, 2000), defects=[("1000.2000.c.1.jpeg", 1000, 2000, 1)])
    a = _folder(tmp_path, "A", scan="-1.2.t.jpeg", manifest=False, **kw)
    assert si.defects_in(a / "-1.2.t.jpeg").status == si.NO_MANIFEST
    b = _folder(tmp_path, "B", scan="-1.2.t.jpeg", **kw)
    Image.new("L", (10, 10)).save(b / "-9.9.t.jpeg")
    assert si.defects_in(b / "-9.9.t.jpeg").status == si.NOT_LISTED
    c = _folder(tmp_path, "C", scan="-1.2.t.2.jpeg", **kw)       # 레시피 2 값 없음
    assert si.defects_in(c / "-1.2.t.2.jpeg").status == si.NO_PIXEL_SIZE
    d = _folder(tmp_path, "D", scan="-1.2.t.jpeg", ini=False, **kw)
    assert si.defects_in(d / "-1.2.t.jpeg").status == si.NO_COORDINATES
    e = _folder(tmp_path, "E", scan="-1.2.t.jpeg", **kw)
    (e / "-1.2.t.jpeg").write_bytes(b"not a jpeg")
    assert si.defects_in(e / "-1.2.t.jpeg").status == si.UNREADABLE


@pytest.mark.parametrize("name,ok", [
    ("64715.-109148.t.2.jpeg", True), ("-97826.8291.t.jpeg", True),
    ("-97826.8291.T.JPEG", True), ("228789.93958.c.1634440618.2.jpeg", False),
    ("test.jpg", False), ("a.t.b.jpeg", False), ("foo.t.jpg", False),
    ("1.2.3.t.jpeg", False), ("-1.5.t.x.jpeg", False),
])
def test_is_scan_name(name, ok):
    assert si.is_scan_name(name) is ok


# ---------------------------------------------------------------------------
# 정보 표 — Color 사진과 같은 생산자
# ---------------------------------------------------------------------------
def test_info_in_scan_equals_color_photo_info(tmp_path):
    scan = "1000.2000.t.jpeg"          # 앞 두 토큰이 양수 — 점표기로 잘못 읽히면 안 된다
    f = _folder(tmp_path, "W", scan=scan, center=(50000, 60000),
                defects=[("50000.60000.c.1.jpeg", 50000, 60000, 1)])
    (hit,) = si.defects_in(f / scan).hits
    groups = single_info.describe_in_scan(f / scan, hit.path)
    file_rows = groups[0].rows
    assert (file_rows[0].label, file_rows[0].value) == (i18n.KO.IMAGE_INFO_ROW_SCAN, scan)
    assert (file_rows[1].label, file_rows[1].value) == (
        i18n.KO.IMAGE_INFO_ROW_DEFECT_PHOTO, "50000.60000.c.1.jpeg")
    plain = single_info.describe(hit.path)
    assert groups[1:] == plain[1:]
    # Scan 사진 자체의 덩이에는 좌표 줄이 없다(파일명 1000/2000 을 좌표로 읽지 않는다)
    labels = [r.label for r in single_info.scan_file_group(f / scan).rows]
    assert i18n.KO.IMAGE_INFO_ROW_ABS_XY not in labels


# ---------------------------------------------------------------------------
# 사진 정보 화면
# ---------------------------------------------------------------------------
def _dialog_mod():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets import image_info_dialog as iid
    return iid


def _settle(iid):
    from PyQt6.QtWidgets import QApplication
    end = time.monotonic() + 5
    while time.monotonic() < end:
        QApplication.processEvents()
        if not iid._LIVE_SCAN_LOADS:
            return
        time.sleep(0.01)


def _two(tmp_path, name="W"):
    scan = "-5.7.t.jpeg"
    f = _folder(tmp_path, name, scan=scan, center=(1000, 2000), defects=[
        ("1100.1990.c.1.jpeg", 1100, 1990, 1), ("900.1960.c.2.jpeg", 900, 1960, 1)])
    return f / scan


def test_dialog_steps_through_defects_in_scan(qapp, isolated_cache, tmp_path):
    iid = _dialog_mod()
    dlg = iid.ImageInfoDialog(image_path=str(_two(tmp_path)))
    dlg.resize(1100, 760)
    dlg.show()
    _settle(iid)
    assert dlg._sheet is not None and len(dlg._sheet.hits) == 2
    assert not dlg.step_bar.isHidden() and dlg.step_label.text() == "Defect 1 / 2"
    # 아래 칸은 300 µm Crop 이 아니라 고른 Defect 의 Color 사진이다(Color 사진이 없으면 안내).
    assert not dlg.scan_view.isHidden() and dlg._scan_crop is None
    assert dlg.scan_title.text() == i18n.KO.SCAN_DEFECT_COLOR_TITLE
    assert dlg.scan_view.text() == i18n.KO.SCAN_DEFECT_COLOR_MISSING
    assert "900.1960.c.2.jpeg" in dlg.as_text()          # 위쪽(v 작은) Defect 먼저
    first_box = dlg._scan_box
    dlg.next_btn.click()
    assert dlg.step_label.text() == "Defect 2 / 2" and "1100.1990.c.1.jpeg" in dlg.as_text()
    assert dlg._scan_box != first_box
    dlg.next_btn.click()                                  # 끝에서 처음으로
    assert dlg.step_label.text() == "Defect 1 / 2"
    assert dlg.gl_btn.isEnabled()                         # 흑백 사진 — GL 로 볼 수 있다
    dlg.close()


def test_dialog_bottom_shows_color_photo_of_selected_hit(qapp, isolated_cache, tmp_path,
                                                          monkeypatch):
    """Scan 사진을 열면 위 = 전체 Scan(네모), 아래 = 고른 Defect 의 Color 사진 — 넘기면 따라 바뀐다."""
    iid = _dialog_mod()
    scan = _two(tmp_path)
    Image.new("RGB", (60, 40), (200, 30, 30)).save(scan.parent / "900.1960.c.2.jpeg")
    # 둘째 Defect(1100.1990.c.1)는 Color 사진 파일이 없다
    dlg = iid.ImageInfoDialog(image_path=str(scan))
    dlg.resize(1100, 760)
    dlg.show()
    _settle(iid)
    sheet = dlg._sheet
    assert sheet.hits[0].path.name == "900.1960.c.2.jpeg"
    assert sheet.color_ok == {0: True, 1: False}
    assert sheet.full is not None and dlg._scan_full is not None
    assert not dlg.scan_view.pixmap().isNull() and dlg.scan_view.text() == ""
    opened = []
    monkeypatch.setattr(iid.sheets, "run", lambda w, **k: opened.append(w))
    dlg.scan_view.clicked.emit()                       # Color 사진을 크게
    (viewer,) = opened
    assert viewer._path == sheet.hits[0].path and viewer._overlay is None
    viewer.close()
    opened.clear()
    dlg.preview.clicked.emit()                         # 위 Scan 은 고른 네모를 덧그려 크게
    (viewer,) = opened
    assert viewer._path == scan and viewer._overlay == dlg._scan_box
    viewer.close()
    dlg.next_btn.click()                               # 파일 없는 Defect — 안내, 누를 수 없다
    assert dlg.scan_view.pixmap().isNull()
    assert dlg.scan_view.text() == i18n.KO.SCAN_DEFECT_COLOR_MISSING
    opened.clear()
    dlg.scan_view.clicked.emit()
    assert opened == []
    dlg.close()


def test_dialog_scan_photo_overlay_hides_before_full_scan_decodes(qapp, isolated_cache,
                                                                  tmp_path, monkeypatch):
    """느린 Scan 원본 디코드는 전체 화면 덮개를 붙잡지 않는다 — 표가 먼저 뜨고 칸에서만 '불러오는 중'."""
    import threading
    from PyQt6.QtWidgets import QApplication
    iid = _dialog_mod()
    scan = _two(tmp_path)
    Image.new("RGB", (60, 40), (200, 30, 30)).save(scan.parent / "900.1960.c.2.jpeg")
    monkeypatch.setattr(iid, "_async_info", lambda: True)
    gate = threading.Event()
    real = si.load_image
    monkeypatch.setattr(si, "load_image", lambda p: (gate.wait(10), real(p))[1])
    dlg = iid.ImageInfoDialog()
    dlg.resize(1100, 760)
    dlg.show()
    dlg.show_image(scan)
    assert dlg._loading.isVisible()
    end = time.monotonic() + 8
    while time.monotonic() < end and dlg._loading.isVisible():
        QApplication.processEvents()
        time.sleep(0.01)
    assert not dlg._loading.isVisible()
    assert dlg._groups and dlg._sheet is not None and dlg._sheet.full is None   # 표는 떴고 원본은 아직
    while time.monotonic() < end and not dlg._scan_dots.isActive():
        QApplication.processEvents()
        time.sleep(0.01)
    assert dlg.preview.text().startswith(i18n.KO.SCAN_IMAGE_LOADING)     # 위 칸이 '불러오는 중'
    assert dlg._scan_dots.isActive()
    gate.set()
    while time.monotonic() < end and dlg._sheet.full is None:
        QApplication.processEvents()
        time.sleep(0.01)
    _settle(iid)
    assert dlg._sheet.full is not None and not dlg.preview.pixmap().isNull()
    assert dlg.preview.text() == "" and not dlg._scan_dots.isActive()
    dlg.close()


def test_dialog_stale_scan_full_result_is_ignored(qapp, isolated_cache, tmp_path):
    iid = _dialog_mod()
    dlg = iid.ImageInfoDialog(image_path=str(_two(tmp_path)))
    dlg.show()
    _settle(iid)
    from PyQt6.QtGui import QImage
    dlg._scan_gen += 1
    sheet = dlg._sheet
    sheet.full = None
    sheet.color_ok.clear()
    dlg._on_scan_full(dlg._scan_gen - 1, QImage(4, 4, QImage.Format.Format_Grayscale8))
    dlg._on_hit_color(dlg._scan_gen - 1, 0, False)
    assert sheet.full is None and sheet.color_ok == {}
    dlg.close()


def test_dialog_scan_without_defects_says_so(qapp, isolated_cache, tmp_path, monkeypatch):
    iid = _dialog_mod()
    scan = "-5.7.t.jpeg"
    f = _folder(tmp_path, "W", scan=scan, center=(1000, 2000),
                defects=[("90000.1.c.1.jpeg", 90000, 1, 1)])
    dlg = iid.ImageInfoDialog(image_path=str(f / scan))
    dlg.show()
    _settle(iid)
    assert dlg._sheet.hits == () and dlg.step_bar.isHidden() and dlg.scan_view.isHidden()
    texts = [w.text() for w in dlg._scroll.widget().findChildren(iid.QLabel)]
    assert i18n.KO.SCAN_DEFECT_NONE in texts
    # Defect 가 없어도 사진을 누르면 크게 본다(예전과 같다)
    opened = []
    monkeypatch.setattr(iid.sheets, "run", lambda w, **k: opened.append(w))
    dlg._open_full_color()
    assert len(opened) == 1
    dlg.close()


def test_dialog_switching_back_to_color_photo_restores_normal_mode(qapp, isolated_cache, tmp_path):
    iid = _dialog_mod()
    scan = _two(tmp_path)
    dlg = iid.ImageInfoDialog(image_path=str(scan))
    dlg.show()
    _settle(iid)
    color = scan.parent / "1100.1990.c.1.jpeg"
    Image.new("RGB", (40, 30), (60, 90, 60)).save(color)
    dlg.show_image(color)
    assert dlg._sheet is None and dlg.step_bar.isHidden()
    assert dlg.scan_title.text() == i18n.KO.SCAN_IMAGE_TITLE
    assert i18n.KO.IMAGE_INFO_ROW_SCAN not in dlg.as_text()
    _settle(iid)
    dlg.close()
