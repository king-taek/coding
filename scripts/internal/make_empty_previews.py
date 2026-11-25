#!/usr/bin/env python3
"""도구 5개 화면의 '빈 화면 배경' 그림을 다시 만든다 — 개발 전용(사용자는 실행하지 않는다).

빈 화면(``EmptyOpen``)은 버튼 뒤에 그 화면이 데이터를 불러온 모습을 흐리게 깔아 보여 준다.
이 도구가 그 배경을 만든다: 저장소 샘플 INI + **합성 사진**(``demo_images``)으로 화면을 실제로
불러와 1280x800 에서 내용 영역을 찍고, **가우시안 블러만** 걸어 ``ui/assets/empty_preview_<키>.jpg`` 로 저장한다.
덮개(배경색 60%)와 버튼 둘레 후광은 테마 색이 바뀌어도 따라가도록 앱이 그릴 때 입힌다 — 여기서 넣지 않는다.

실제 결함 사진은 쓰지 않는다(사진 파일 정책). 임시 폴더 경로가 화면에 보이면 샘플 글자로 바꾼다.

    QT_QPA_PLATFORM=offscreen python scripts/internal/make_empty_previews.py [키 ...]
    키: info map gl overkill zone (없으면 전부)
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ASSETS = REPO / "aoi_verification" / "app" / "ui" / "assets"
SAMPLE_SRC = REPO / "dev" / "좌표 확인" / "멀티레시피(Height)" / "GX57527979(T254 FLU-1122N AOI-16)"
SAMPLE_NAME = "GX57527979"
SAMPLE_PATH = "D:\\샘플\\" + SAMPLE_NAME
W, H = 1280, 800
BLUR_PX = 5.0 * 1.0          # 1200 px 폭 기준 반지름 — 폭이 다르면 비례
JPEG_QUALITY = 85
KEYS = ("info", "map", "gl", "overkill", "zone")

_TMP = Path(tempfile.mkdtemp(prefix="aoi-empty-preview-"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = str(_TMP / "home")
os.environ["USERPROFILE"] = str(_TMP / "home")
(_TMP / "home").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "internal"))

from PyQt6.QtCore import QEvent, QRect  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit  # noqa: E402


def pump(n: int = 20) -> None:
    app = QApplication.instance()
    for _ in range(n):
        app.processEvents()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def wait(ms: int) -> None:
    t = time.time()
    while time.time() - t < ms / 1000:
        pump(2)
        time.sleep(0.05)


def make_window():
    from aoi_verification.app.ui import main_window as mw
    from aoi_verification.app.ui import theme
    from aoi_verification.app.ui.pages.match_page import MatchPage
    for attr in ("_check_for_update_async", "_maybe_offer_openvino", "_warmup_accel_async",
                 "_start_backend_import_async", "_prune_old_cache_async", "_run_startup_popups"):
        if hasattr(mw.MainWindow, attr):
            setattr(mw.MainWindow, attr, lambda self: None)
    MatchPage._start_precompute = lambda self: None
    app = QApplication.instance() or QApplication(sys.argv)
    theme.apply_to_app(app)
    win = mw.MainWindow()
    win.resize(W, H)
    win.show()
    pump(30)
    win._on_backend_loaded()
    pump(30)
    return win


def stage_sample(dst: Path) -> Path:
    """샘플 폴더의 INI 를 복사하고 사진목록의 이름으로 합성 사진을 만든다."""
    from demo_images import defect_image
    dst.mkdir(parents=True, exist_ok=True)
    for f in SAMPLE_SRC.iterdir():
        if f.is_file() and f.name != "사진목록.txt":
            shutil.copy2(f, dst / f.name)
        elif f.is_dir():
            shutil.copytree(f, dst / f.name, dirs_exist_ok=True)
    lines = (SAMPLE_SRC / "사진목록.txt").read_text(encoding="utf-8").splitlines()
    for i, n in enumerate(l.strip() for l in lines if l.strip() and not l.startswith("#")):
        if not (dst / n).exists():
            defect_image(i % 40, machine=0, px=500).save(dst / n, quality=85)
    return dst


def hide_temp_paths(win) -> None:
    """화면에 보이는 임시 폴더 경로 글자를 샘플 글자로 바꾼다."""
    tmp = str(_TMP)
    for w in win.findChildren(QLabel) + win.findChildren(QLineEdit):
        if not w.isVisible():
            continue
        text = w.text()
        if tmp in text or tmp.replace("\\", "/") in text:
            w.setText(SAMPLE_PATH)
        elif SAMPLE_NAME in text and "/" in text:
            w.setText(SAMPLE_PATH)


def grab_content(win):
    pump(20)
    hide_temp_paths(win)
    pump(5)
    pm = win.grab()
    pages = win._setup_page._pages
    tl = pages.mapTo(win, pages.rect().topLeft())
    return pm.copy(QRect(tl.x(), tl.y(), pages.width(), pages.height()))


def save_blurred(pm, key: str) -> Path:
    from PIL import Image, ImageFilter
    png = _TMP / f"{key}.png"
    pm.save(str(png))
    img = Image.open(png).convert("RGB")
    img = img.filter(ImageFilter.GaussianBlur(BLUR_PX * img.width / 1200))
    out = ASSETS / f"empty_preview_{key}.jpg"
    img.save(out, quality=JPEG_QUALITY, optimize=True)
    return out


def cap_info(win, d: Path):
    sp = win._setup_page
    sp.show_page("image_info")
    wait(800)
    dlg = sp.tool_dialog("image_info")
    import aoi_verification.app.ui.widgets.image_info_dialog as iid
    iid._async_info = lambda: True
    photos = sorted(d.glob("*.c.*.jpeg"))[:30]
    dlg.open_many(photos[8:])
    wait(9000)


def cap_map(win, d: Path):
    sp = win._setup_page
    sp.show_page("wafer_map")
    wait(800)
    sp.tool_dialog("wafer_map").show_folder(d)
    wait(8000)


def cap_gl(win, d: Path):
    sp = win._setup_page
    sp.show_page("gl")
    wait(800)
    p = [x for x in sorted(d.glob("*.c.*.jpeg")) if x.name.startswith("279514.269167")][0]
    sp.tool_dialog("gl").open_path(p)
    wait(8000)


def cap_overkill(win, d: Path):
    sp = win._setup_page
    sp.show_page("threshold")
    wait(800)
    dlg = sp.tool_dialog("threshold")
    dlg.add_folders([d])
    wait(10000)
    best = max(dlg.zone_keys(), key=dlg._count)
    dlg._set_zone(best)
    wait(1500)

    def setv(k, um):
        e = dlg.sliders[k][1]
        e.setText(str(um))
        e.editingFinished.emit()
    setv(("dark", "width"), 7.0)
    wait(800)
    setv(("dark", "length"), 8.0)
    wait(800)
    setv(("bright", "width"), 6.4)
    wait(6800)


def cap_zone(win, d: Path):
    sp = win._setup_page
    sp.show_page("zone_origin")
    wait(800)
    dlg = sp.tool_dialog("zone_origin")
    dlg.add_folders([d])
    wait(10000)
    for k in list(dlg._tiles)[:60]:      # 스캔 사진이 있는 칸을 찾아 연다
        dlg.open_item(k)
        dlg._scan_current()
        wait(500)
        if dlg._scans.get(k) is not None:
            dlg.open_item(k)
            wait(1500)
            break


CAPTURES = {"info": cap_info, "map": cap_map, "gl": cap_gl, "overkill": cap_overkill, "zone": cap_zone}


def main(argv: list[str]) -> int:
    keys = argv or list(KEYS)
    bad = [k for k in keys if k not in CAPTURES]
    if bad:
        print("알 수 없는 키:", bad, "— 가능:", KEYS)
        return 2
    try:
        d = stage_sample(_TMP / "s" / "T254 FLU" / SAMPLE_NAME)
        app = QApplication(sys.argv)      # 변수에 붙들어 둬야 지워지지 않는다
        for key in keys:
            win = make_window()                 # 화면마다 새 창 — 앞 화면의 상태가 섞이지 않게
            CAPTURES[key](win, d)
            out = save_blurred(grab_content(win), key)
            print(f"{key}: {out.relative_to(REPO)} ({out.stat().st_size // 1024} KB)")
            win.close()
            win.deleteLater()
            pump(20)
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
