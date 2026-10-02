"""화면은 벨럼(밝은 제도지) **한 가지**로 고정이다 — 다크 모드를 없앤 뒤의 계약.

사용자 요청으로 다크 모드(흑연)와 설정 화면의 '사용 방법' 접이식 안내, 구형 엔진
스위치 아래의 두 줄 설명을 지웠다.  여기서 못 박는 것:

- 테마는 단일 팔레트다 — 모드 전환 API·팔레트 레지스트리가 되살아나지 않는다.
- import 직후 QSS 가 빈 토큰 없이 렌더되고, 사진 판독 뷰어는 여전히 검정 바탕 +
  검정 위 전용 잉크다(화면 색을 따라가면 글자가 안 보인다).
- 옛 prefs(``color_mode``·``howto_expanded``)를 읽어도 나머지 설정은 그대로이고,
  다시 저장하면 폐기 키가 사라진다.
- 설정 화면에 사용 방법·다크 스위치가 없고, 구형 엔진 스위치는 설명 없이 동작한다.

(예전 ``test_color_mode.py`` 가운데 지금도 유효한 계약 — 렌더 완결성·스크림·강조색
구분·옛 디자인 스위처 금지 — 을 이리로 옮겼다.)
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import inspect
import json
from pathlib import Path

import pytest

from aoi_verification.app import i18n
from aoi_verification.app.ui import theme
from aoi_verification.app.utils import prefs

_ROOT = Path(__file__).resolve().parents[2]
_QSS = (_ROOT / "aoi_verification" / "app" / "ui" / "style.qss").read_text(
    encoding="utf-8")
_LIGHT_BG = "#ECE9E2"
_OLD_DARK_BG = "#2B2820"
_REMOVED_LEGACY_DESC = (
    "끄면 좌표 매칭(파일명/INI/KLA 좌표 데이터)을 씁니다",
    "켜면 유사도 점수가 임계치 이상인 가장 가까운 후보를 자동 선택합니다",
)


def _luminance(hexv: str) -> float:
    h = hexv.lstrip("#")

    def lin(v: int) -> float:
        x = v / 255.0
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


# ── 테마: 단일 팔레트 ─────────────────────────────────────────────────────
def test_theme_has_no_color_mode_machinery():
    """모드 전환 API 를 '항상 라이트를 돌려주는 껍데기' 로도 남기지 않는다."""
    for gone in ("set_color_mode", "normalize_color_mode", "is_dark_mode",
                 "color_mode_keys", "COLOR_MODE", "DEFAULT_COLOR_MODE",
                 "COLOR_MODE_LABELS", "PALETTES", "_DARK", "_SCRIMS",
                 "_COLOR_MODE_ALIASES"):
        assert not hasattr(theme, gone), f"theme.{gone} 가 남아 있다"


def test_no_design_variant_machinery_resurrected():
    """옛 '경쟁 디자인 스위처' 이름도 계속 금지."""
    for gone in ("VARIANTS", "set_variant", "variant_keys", "CURRENT_VARIANT",
                 "DEFAULT_VARIANT", "Variant"):
        assert not hasattr(theme, gone), f"theme.{gone} 부활"


def test_import_alone_fills_the_light_tokens():
    assert theme.BG == _LIGHT_BG
    assert theme.COLORS["bg"] == _LIGHT_BG
    assert theme.TOKENS["bg"] == _LIGHT_BG


def test_qss_renders_without_missing_tokens():
    out = theme.render_qss(_QSS)
    assert "$" not in out
    assert _LIGHT_BG.lower() in out.lower()
    assert _OLD_DARK_BG.lower() not in out.lower()


def test_viewer_keeps_black_paper_and_its_own_ink():
    """사진 판독 뷰어 — 바탕은 순검정, 잉크는 검정 위 전용 고정색(예전 값 그대로)."""
    assert theme.TOKENS["viewer_bg"] == "#000000"
    assert theme.TOKENS["viewer_ink"] == "#AEA798"
    assert _contrast(theme.TOKENS["viewer_ink"], "#000000") >= 4.5
    # 화면의 mute 를 썼다면 검정 위에서 읽히지 않는다 — 그래서 분리돼 있다.
    assert theme.TOKENS["viewer_ink"] != theme.MUTE


def test_scrim_is_translucent_enough_to_see_through():
    """로딩·시트 스크림이 화면을 '전부 가리지' 않아야 한다(사용자 요청)."""
    assert theme.SCRIM_RGBA == (27, 26, 23, 84)
    assert 60 <= theme.SCRIM_RGBA[3] <= 130


def test_accent_is_distinguishable_from_its_own_paper():
    """강조색이 바탕과 **색상각**으로도 구분되어야 한다(대비만으로는 안 보이는 결함)."""
    import colorsys

    def hue(hexv: str) -> float:
        h = hexv.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        return colorsys.rgb_to_hsv(r, g, b)[0] * 360.0

    dh = abs(hue(theme.ACCENT) - hue(theme.BG))
    assert min(dh, 360.0 - dh) >= 10.0


# ── prefs: 옛 키 무시 + 나머지 보존 + 재저장 시 제거 ─────────────────────
def test_old_prefs_keys_are_ignored_and_dropped_on_save(isolated_cache):
    old = {
        "color_mode": "dark", "howto_expanded": True,
        "last_ref_root": r"\\nas\ref", "last_val_root": r"\\nas\val",
        "engine_mode": "efficiency", "legacy_enabled": True,
        "legacy_engine": "efficiency", "threshold": 0.71,
        "coord_tolerance": 150.0, "window_width": 1500, "window_height": 900,
        "window_maximized": True, "prefs_version": prefs.PREFS_VERSION,
    }
    path = prefs._file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(old), encoding="utf-8")

    p = prefs.load()
    assert not hasattr(p, "color_mode") and not hasattr(p, "howto_expanded")
    assert (p.last_ref_root, p.last_val_root) == (r"\\nas\ref", r"\\nas\val")
    assert (p.engine_mode, p.legacy_enabled, p.legacy_engine) == (
        "efficiency", True, "efficiency")
    assert (p.threshold, p.coord_tolerance) == (0.71, 150.0)
    assert (p.window_width, p.window_height, p.window_maximized) == (1500, 900, True)

    # 읽기만으로는 파일을 다시 쓰지 않는다.
    assert json.loads(path.read_text(encoding="utf-8")) == old

    prefs.patch(threshold=0.72)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "color_mode" not in saved and "howto_expanded" not in saved
    assert saved["last_ref_root"] == r"\\nas\ref" and saved["threshold"] == 0.72


@pytest.mark.parametrize("mode", ["dark", "graphite", "cyanotype", "light", "??"])
def test_startup_stylesheet_is_light_whatever_prefs_say(isolated_cache, mode):
    """옛 다크 설정이 남아 있어도 첫 화면부터 라이트다."""
    import main as entry

    path = prefs._file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"color_mode": mode}), encoding="utf-8")

    class _FakeApp:
        sheet = ""

        def setStyleSheet(self, text):      # noqa: N802
            self.sheet = text

    app = _FakeApp()
    entry._load_stylesheet(app)
    assert _LIGHT_BG.lower() in app.sheet.lower()
    assert _OLD_DARK_BG.lower() not in app.sheet.lower()
    assert theme.BG == _LIGHT_BG


def test_removed_strings_are_gone():
    for gone in ("DARK_MODE_LABEL", "DARK_MODE_TOOLTIP", "COLOR_MODE_LIGHT",
                 "COLOR_MODE_DARK", "HOWTO_TOGGLE_OPEN", "HOWTO_TOGGLE_CLOSE",
                 "SETUP_HOW_TO_USE_TITLE", "SETUP_HOW_TO_USE_BODY",
                 "LEGACY_SWITCH_DESC", "ENGINE_ACTIVE_COORD"):
        assert not hasattr(i18n.KO, gone), f"i18n.KO.{gone} 가 남아 있다"
    # 스위치 제목·툴팁·배지 문구는 그대로다.
    assert i18n.KO.LEGACY_SWITCH_TITLE == "유사도 엔진(구형) 사용"
    assert i18n.KO.LEGACY_MODE_HINT
    assert i18n.KO.ENGINE_ACTIVE_LEGACY_FMT


# ── 화면 (헤드리스) ───────────────────────────────────────────────────────
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget          # noqa: E402

from aoi_verification.app.ui import motion                       # noqa: E402


@pytest.fixture
def page(qapp, isolated_cache):
    from aoi_verification.app.ui.pages.setup_page import SetupPage
    p = SetupPage()
    p.resize(1280, 900)
    yield p
    p.deleteLater()
    qapp.processEvents()


def _all_texts(root: QWidget) -> list[str]:
    out = [w.text() for w in root.findChildren(QLabel)]
    out += [w.toolTip() for w in root.findChildren(QWidget) if w.toolTip()]
    return out


def test_setup_page_has_no_howto_and_no_dark_switch(page):
    for gone in ("_howto_section", "_dark_switch", "appearance_changed",
                 "_appearance_timer", "_pending_color_mode", "_build_howto",
                 "_build_view_options", "_on_dark_mode_toggled",
                 "_emit_appearance_changed"):
        assert not hasattr(page, gone), f"SetupPage.{gone} 가 남아 있다"
    texts = "\n".join(_all_texts(page))
    for gone in ("사용 방법", "다크 모드"):
        assert gone not in texts, f"'{gone}' 이 설정 화면에 남아 있다"


def test_legacy_switch_has_no_description_but_still_works(page):
    sw = page.legacy_switch
    assert sw._desc is None, "구형 엔진 스위치 아래 설명 라벨이 남아 있다"
    texts = "\n".join(_all_texts(page))
    for line in _REMOVED_LEGACY_DESC:
        assert line not in texts, "지운 두 줄 설명이 화면 어딘가에 남아 있다"
    # 제목·툴팁은 그대로.
    assert sw._title.text() == i18n.KO.LEGACY_SWITCH_TITLE
    assert sw.toolTip() == i18n.KO.LEGACY_MODE_HINT
    # 조작 — 켜면 구형 하위 선택·임계치가 보이고 허용 오차가 잠긴다, 끄면 되돌아간다.
    for on in (True, False):
        sw.set_on(on, emit=True)
        assert sw.is_on() is on
        assert page.legacy_group.isHidden() is (not on)
        assert page._threshold_row.isHidden() is (not on)
        assert page._tol_row.isEnabled() is (not on)
        assert prefs.load().legacy_enabled is on


def test_snapshot_paints_the_theme_background(qapp):
    """전환 스냅샷의 빈 자리는 Qt 기본 회색(#efefef)이 아니라 화면 바탕색이다."""
    w = QWidget()
    QVBoxLayout(w).addWidget(QLabel("내용", w))
    w.resize(240, 160)
    try:
        pix = motion.snapshot(w)
        img = pix.toImage()
        corner = img.pixelColor(2, img.height() - 3)
        assert corner.name().lower() == theme.BG.lower()
        dpr = pix.devicePixelRatio() or 1.0
        assert round(pix.width() / dpr) == w.width()
        assert round(pix.height() / dpr) == w.height()
    finally:
        w.deleteLater()


def test_no_baked_theme_colors_in_page_stylesheets():
    """인라인 ``setStyleSheet`` 에 ``theme.<색>`` 을 f-string 으로 굽지 않는다 —
    색의 단일 출처는 style.qss 의 role 규칙이다."""
    import re
    from aoi_verification.app.ui import main_window as mw

    root = Path(inspect.getfile(mw)).resolve().parent / "pages"
    offenders = []
    for f in sorted(root.glob("*.py")):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"setStyleSheet\((.{0,240}?)\)\n", text, re.S):
            if re.search(r"\{theme\.(BG|PANEL|ELEV|INK|INK2|MUTE|LINE|LINE2|"
                         r"ACCENT|PASS|WARN|DANGER)\b", m.group(1)):
                offenders.append(f"{f.name}:{text[:m.start()].count(chr(10)) + 1}")
    assert not offenders, "인라인 스타일에 테마 색을 구웠다: " + ", ".join(offenders)


def test_wafer_map_png_still_renders_on_the_light_palette(qapp):
    from aoi_verification.app.ui.widgets import wafer_map_view as wm
    img = wm.render_map_image(None, size=120)
    assert not img.isNull() and img.width() == 120
    # 판 가운데는 앱 팔레트의 면색이다.
    c = img.pixelColor(60, 60)
    assert c.alpha() > 0
