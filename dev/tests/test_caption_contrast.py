"""12px 캡션 등급의 **실측** 대비 — 색이 아니라 크기가 원인이었다.

배경(실측): 토큰 조합을 전수로 재면 라이트 5.65~16.67 · 다크 5.41~15.27 로 팔레트는
전부 WCAG AA(4.5:1)를 넘는다.  그런데 화면에서 잘라 재면 **캡션 등급만** 3.58~4.23:1 로
떨어졌다 — 결과 통계 타일 캡션 4개(라이트 3.58~3.83 / 다크 3.63~3.74)와 실패 검토 타일의
파일명 캡션(라이트 3.77 · 다크 4.23).  12px 에서는 획이 1px 미만이라 **렌더된 픽셀이
지정색에 도달하지 못한다**(가장 진한 단일 픽셀로 재도 다크 4.61 로 겨우 걸쳤다).
그래서 색을 더 진하게 하는 것으로는 못 고치고, 크기·굵기를 올려서 고쳤다
(`theme.Profile.font_caption_lg` / `font_caption_lg_weight`).

여기서 세우는 불변식: **캡션 등급은 두 색 모드 모두에서 4.5:1 을 넘는다.**
지정색이 아니라 **그려진 픽셀**로 잰다 — 그게 사용자가 보는 것이고, 이 회귀는 지정색만
보면 절대 안 보인다.

★ 비용 주의: `QApplication.setStyleSheet` 은 부를수록 비싸지므로(conftest 주석) 여기서는
  앱이 아니라 **작은 위젯 한 그루에만** 스타일시트를 건다.  세션 테마(`styled_qapp`)를
  건드리지 않으므로 스위트 시간에 영향이 없다.
"""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QImage                                   # noqa: E402
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget  # noqa: E402

from aoi_verification.app.ui import theme                        # noqa: E402

_QSS = Path(__file__).resolve().parents[2] / \
    "aoi_verification/app/ui/style.qss"


# ── WCAG 상대 휘도/대비 ─────────────────────────────────────────────────────
def _lin(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lum(p) -> float:
    return 0.2126 * _lin(p[0]) + 0.7152 * _lin(p[1]) + 0.0722 * _lin(p[2])


def _ratio(a, b) -> float:
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


@pytest.fixture
def restore_mode():
    """색 모드를 원래대로 되돌린다 — 이 파일이 다른 테스트에 다크를 흘리면 안 된다."""
    before = theme.COLOR_MODE
    yield
    theme.set_color_mode(before)


def _measure(mode: str, tile_role: str, caption_role: str, text: str) -> float:
    """`tile_role` 면 위에 `caption_role` 라벨을 실제로 그려 대비를 잰다."""
    from aoi_verification.app.ui import motion

    theme.set_color_mode(mode)
    qss = theme.render_qss(_QSS.read_text(encoding="utf-8"))

    root = QWidget()
    root.setStyleSheet(qss)          # 앱 전역이 아니라 이 그루에만 건다
    lay = QVBoxLayout(root)
    lay.setContentsMargins(12, 12, 12, 12)
    tile = QFrame(root)
    tile.setProperty("role", tile_role)
    tl = QVBoxLayout(tile)
    tl.setContentsMargins(10, 10, 10, 10)
    cap = QLabel(text, tile)
    cap.setProperty("role", caption_role)
    tl.addWidget(cap)
    lay.addWidget(tile)
    root.resize(320, 120)
    root.show()
    for _ in range(5):
        root.repaint()

    img = motion.snapshot(root).toImage().convertToFormat(
        QImage.Format.Format_ARGB32)
    tl_pt = cap.mapTo(root, cap.rect().topLeft())
    px = []
    for y in range(tl_pt.y(), min(tl_pt.y() + cap.height(), img.height())):
        for x in range(tl_pt.x(), min(tl_pt.x() + cap.width(), img.width())):
            c = img.pixelColor(x, y)
            px.append((c.red(), c.green(), c.blue()))
    root.close()
    assert len(px) > 50, "라벨 영역을 잘라내지 못했다(렌더 실패)"

    cnt = Counter(px)
    bg = cnt.most_common(1)[0][0]
    # 안티앨리어싱 끝단 1픽셀을 글자색으로 오인하지 않게, **3회 이상 반복된** 픽셀만
    # 글자 획으로 본다(원 실측 하네스와 같은 규칙).
    core = [c for c, n in cnt.items() if n >= 3] or list(cnt)
    fg = max(core, key=lambda p: abs(_lum(p) - _lum(bg)))
    return _ratio(fg, bg)


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize(
    "tile_role, caption_role, text",
    [
        ("statTile", "statCaption", "슬롯 불일치"),      # 실측 최저였던 문구
        ("statTile", "statCaption", "매치 성공"),
    ],
)
def test_caption_grade_meets_wcag_aa(qapp, restore_mode, mode,
                                     tile_role, caption_role, text):
    r = _measure(mode, tile_role, caption_role, text)
    assert r >= 4.5, (
        f"[{mode}] {caption_role} '{text}' 실측 대비 {r:.2f}:1 — 4.5 미만.\n"
        "색을 진하게 하는 것으로는 못 고친다(지정색은 이미 5:1 이상). "
        "theme.Profile.font_caption_lg / font_caption_lg_weight 를 확인하라.")


def test_file_captions_share_one_grade(qapp):
    """타일 파일명 캡션 두 곳이 **같은 등급**을 쓴다.

    ★ 여기서 그린 픽셀을 재지 않는 이유를 적어 둔다: 파일명 캡션의 실측 미달(라이트
    3.77 · 다크 4.23)은 **실제 다이얼로그 전체를 렌더했을 때** 나온 값이고, 타일 하나만
    떼어 그리면 같은 조건이 재현되지 않는다(떼어 재면 지정색 그대로 6.5/6.1 이 나온다).
    재현되지 않는 측정을 테스트에 굳히는 대신, **고친 메커니즘**(두 화면이 같은 캡션
    등급을 쓴다 + 그 등급이 커졌다)을 못 박는다.  화면 전체 실측은 외부 하네스가 맡는다
    (수리 후 라이트 4.61 · 다크 5.02).
    """
    from aoi_verification.app.ui.widgets import thumb_grid, unmatched_review_dialog
    src = [Path(thumb_grid.__file__).read_text(encoding="utf-8"),
           Path(unmatched_review_dialog.__file__).read_text(encoding="utf-8")]
    for text in src:
        assert '"role", "fileCaption"' in text

    qss = theme.render_qss(_QSS.read_text(encoding="utf-8"))
    block = qss.split('QLabel[role="fileCaption"]')[1].split("}")[0]
    assert f"{theme.PROFILE.font_caption_lg}px" in block
    assert str(theme.PROFILE.font_caption_lg_weight) in block


def test_caption_grade_is_bigger_and_bolder_than_the_chip_grade():
    """등급이 둘로 나뉘어 있어야 한다 — 칩(고정폭)까지 키우면 글자가 넘친다."""
    p = theme.PROFILE
    assert p.font_caption_lg > p.font_caption
    assert p.font_caption_lg_weight >= 600


def test_the_measurement_would_catch_a_low_contrast_caption(qapp, restore_mode):
    """가드가 늘 통과하는 빈 껍데기가 아님을 스스로 증명한다.

    같은 자리에 **거의 배경색인 글자**를 그리면 4.5 아래로 떨어져야 한다."""
    from aoi_verification.app.ui import motion

    theme.set_color_mode("light")
    root = QWidget()
    root.setStyleSheet(
        f"QWidget {{ background: {theme.ELEV}; }} "
        f"QLabel {{ color: {theme.LINE2}; font-size: 12px; }}")
    lay = QVBoxLayout(root)
    cap = QLabel("슬롯 불일치", root)
    lay.addWidget(cap)
    root.resize(320, 80)
    root.show()
    for _ in range(5):
        root.repaint()
    img = motion.snapshot(root).toImage().convertToFormat(
        QImage.Format.Format_ARGB32)
    tl_pt = cap.mapTo(root, cap.rect().topLeft())
    px = [(lambda c: (c.red(), c.green(), c.blue()))(img.pixelColor(x, y))
          for y in range(tl_pt.y(), min(tl_pt.y() + cap.height(), img.height()))
          for x in range(tl_pt.x(), min(tl_pt.x() + cap.width(), img.width()))]
    root.close()
    cnt = Counter(px)
    bg = cnt.most_common(1)[0][0]
    core = [c for c, n in cnt.items() if n >= 3] or list(cnt)
    fg = max(core, key=lambda p: abs(_lum(p) - _lum(bg)))
    assert _ratio(fg, bg) < 4.5
