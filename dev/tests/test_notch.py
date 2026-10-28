"""Notch 방향 보정 — 위/아래/오른쪽/왼쪽 4방향과 조합이 같은 결함을 같은 번호로 돌려놓는지.

합성 웨이퍼를 만든다: 웨이퍼 좌표의 결함 점들을 notch 아래로 스캔한 좌표(기준)와, 웨이퍼를
돌려 놓고 스캔한 좌표(위/오른쪽/왼쪽)로 각각 Camtek 변환식(`camtek_ini`)대로 바꾼다.  보정이
옳으면 돌려 놓은 쪽을 보정한 결과가 기준과 col/row 정확 일치, x/y 거의 일치해야 한다.
(합성 자료다 — 실물 notch 스캔 쌍으로 검증한 것이 아니다.)
"""
from __future__ import annotations

import itertools
import math
import random
from pathlib import Path

import pytest

from aoi_verification.app.coords import notch
from aoi_verification.app.coords.models import DefectCoord

R = 150000.0


def _scan_frame(cx, cy, px, py):
    """Camtek 규칙: 첫 온전한 열 / 마지막 온전한 행이 0."""
    col_origin = math.ceil((cx - R) / px)
    row_total = math.floor((cy + R) / py) - 1
    return col_origin, row_total, notch.Frame(
        px, py, cx / px - col_origin, row_total + 1 - cy / py, R)


def _to_scan(a, b, direction):
    """웨이퍼 좌표(a 오른쪽, b 위) → 그 방향으로 놓고 찍은 스캔 틀의 좌표."""
    return {notch.DOWN: (a, b), notch.RIGHT: (-b, a),
            notch.UP: (-a, -b), notch.LEFT: (b, -a)}[direction]


def _scan(points, direction, die_w, die_h, ox, oy):
    """notch ``direction`` 으로 스캔했을 때의 (좌표 목록, Frame).

    die 격자는 웨이퍼에 붙어 있다(꼭짓점 (ox + i·die_w, oy + j·die_h)).
    """
    swap = direction in (notch.LEFT, notch.RIGHT)
    px, py = (die_h, die_w) if swap else (die_w, die_h)
    # 스캔 틀에서 격자 꼭짓점의 위치(웨이퍼 중심 기준)
    sa, sb = _to_scan(ox, oy, direction)
    # 꼭짓점이 stage 격자선에 오도록 stage 중심을 정한다(X = cx + a, Y = cy − b)
    cx = 40 * px - sa
    cy = 40 * py + sb
    col_origin, row_total, frame = _scan_frame(cx, cy, px, py)
    out = []
    for a, b in points:
        a2, b2 = _to_scan(a, b, direction)
        X, Y = cx + a2, cy - b2
        xi, yi = math.floor(X / px), math.floor(Y / py)
        out.append(DefectCoord(col=xi - col_origin, row=row_total - yi,
                               x=X - xi * px, y=Y - yi * py, source="camtek_ini"))
    return out, frame


def _points(n=300, seed=1):
    rnd = random.Random(seed)
    pts = []
    while len(pts) < n:
        a, b = rnd.uniform(-R, R), rnd.uniform(-R, R)
        if math.hypot(a, b) < R - 3000:
            pts.append((a, b))
    return pts


@pytest.mark.parametrize("direction", notch.DIRECTIONS)
@pytest.mark.parametrize("die", [(10000.0, 10000.0), (12000.0, 7000.0)])
def test_rotated_scan_matches_standard_scan(direction, die):
    pts = _points()
    ox, oy = 3100.0, -2300.0
    ref, _ = _scan(pts, notch.DOWN, *die, ox, oy)
    got, frame = _scan(pts, direction, *die, ox, oy)
    for r, g in zip(ref, got):
        fixed = notch.rotate_coord(g, direction, frame)
        assert (fixed.col, fixed.row) == (r.col, r.row)
        assert fixed.x == pytest.approx(r.x, abs=1e-3)
        assert fixed.y == pytest.approx(r.y, abs=1e-3)


@pytest.mark.parametrize("a,b", list(itertools.product(notch.DIRECTIONS, repeat=2)))
def test_both_sides_rotated_end_up_identical(a, b):
    """기준·검증이 서로 다른 방향으로 스캔돼도 보정 후 같은 번호(조합 16가지)."""
    pts = _points(120, seed=7)
    die = (11000.0, 9000.0)
    ra, fa = _scan(pts, a, *die, 500.0, 900.0)
    rb, fb = _scan(pts, b, *die, 500.0, 900.0)
    for ca, cb in zip(ra, rb):
        x = notch.rotate_coord(ca, a, fa)
        y = notch.rotate_coord(cb, b, fb)
        assert (x.col, x.row) == (y.col, y.row)
        assert math.hypot(x.x - y.x, x.y - y.y) < 1e-3


def test_standard_direction_is_untouched():
    c = DefectCoord(col=3, row=4, x=10.0, y=20.0, source="camtek_ini")
    f = notch.Frame(10000, 10000, 15.2, 14.7, R)
    assert notch.rotate_coord(c, notch.DOWN, f) is c
    assert notch.rotate_coord(c, "garbage", f) is c


def test_apply_notch_returns_new_dict_and_keeps_originals(monkeypatch):
    f = notch.Frame(10000, 12000, 15.2, 14.7, R)
    monkeypatch.setattr(notch, "folder_frame", lambda folder, source: f)
    p_down, p_left, p_none = Path("a/1.jpg"), Path("b/2.jpg"), Path("b/3.jpg")
    c = DefectCoord(col=3, row=4, x=1000.0, y=2000.0, source="camtek_ini")
    src = {p_down: c, p_left: c, p_none: None}
    out = notch.apply_notch(src, lambda p: notch.LEFT if p.parent.name == "b" else notch.DOWN)
    assert out is not src and src[p_left] is c           # 원본 불변
    assert out[p_down] is c and out[p_none] is None
    assert out[p_left] != c and out[p_left].source == "camtek_ini"


def test_apply_notch_raises_when_frame_missing(monkeypatch):
    monkeypatch.setattr(notch, "folder_frame", lambda folder, source: None)
    c = DefectCoord(col=3, row=4, x=1.0, y=2.0, source="camtek_abs")
    with pytest.raises(notch.NotchError):
        notch.apply_notch({Path("a/1.jpg"): c}, lambda p: notch.UP)
    # 표준 방향이면 재료가 없어도 통과
    assert notch.apply_notch({Path("a/1.jpg"): c}, lambda p: notch.DOWN)


def test_normalize_and_detect_defaults(tmp_path):
    assert notch.normalize(None) == notch.DOWN
    assert notch.normalize(" UP ") == notch.UP
    assert notch.detect(tmp_path) == notch.DOWN        # 정보파일 없음


@pytest.mark.parametrize("word,expected", [("DOWN", "down"), ("UP", "up"),
                                           ("LEFT", "left"), ("RIGHT", "right")])
def test_detect_reads_kla_header(tmp_path, word, expected):
    (tmp_path / "wafer.001").write_text(
        f'FileVersion 1 2;\nOrientationMarkLocation {word};\nEndOfFile;\n')
    assert notch.detect(tmp_path) == expected


# ---------------------------------------------------------------------------
# 매칭 연결 · 화면
def test_notch_for_path_picks_deepest_root():
    dirs = (("/data/ref", "up"), ("/data/val", "left"), ("/data/val/sub", "right"))
    assert notch.notch_for_path(dirs, Path("/data/ref/s1/a.jpg")) == "up"
    assert notch.notch_for_path(dirs, Path("/data/val/s1/a.jpg")) == "left"
    assert notch.notch_for_path(dirs, Path("/data/val/sub/s1/a.jpg")) == "right"
    assert notch.notch_for_path(dirs, Path("/elsewhere/a.jpg")) == "down"
    assert notch.notch_for_path((), Path("/x/a.jpg")) == "down"


@pytest.mark.parametrize("val_dir", notch.DIRECTIONS)
def test_scheduler_matches_rotated_scan_only_after_notch_fix(monkeypatch, val_dir):
    """검증을 어느 방향으로 찍었든, 방향을 알려 주면 기준과 같은 결함으로 확정된다."""
    pytest.importorskip("PyQt6.QtCore")
    from aoi_verification.app import config
    from aoi_verification.app.models.slot import ImageItem
    from aoi_verification.app.workers import coord_matcher as cm

    die = (11000.0, 8000.0)
    pt = [(41234.0, -53210.0)]
    (rc,), _ = _scan(pt, notch.DOWN, *die, 700.0, 300.0)
    (vc,), vframe = _scan(pt, val_dir, *die, 700.0, 300.0)
    ref = ImageItem("s", Path("/r/s/a.jpg"), "ref")
    val = ImageItem("s", Path("/v/s/b.jpg"), "val")
    base = {ref.path: rc, val.path: vc}
    monkeypatch.setattr(cm, "_resolve_batch", lambda paths, cb=None: dict(base))
    monkeypatch.setattr(notch, "folder_frame", lambda folder, source: vframe)

    def run(dirs):
        cfg = config.SimilarityConfig(engine="coordinate", notch_dirs=dirs)
        res: dict = {}
        cm.CoordScheduler([("s", [ref], [val])], cfg=cfg, results=res)._run()
        return res[("s", ref.path)]

    fixed = run((("/r", "down"), ("/v", val_dir)))
    assert fixed == [(val.path, pytest.approx(1.0, abs=1e-3))]
    assert base[val.path] is vc                       # 원본 좌표는 그대로
    if val_dir != notch.DOWN:                         # 모르는 채로는 맞지 않는다
        assert not run(()) or run(())[0][1] < 0.5


def test_scheduler_reports_failure_when_geometry_missing(monkeypatch):
    pytest.importorskip("PyQt6.QtCore")
    from aoi_verification.app import config
    from aoi_verification.app.models.slot import ImageItem
    from aoi_verification.app.workers import coord_matcher as cm

    c = DefectCoord(col=3, row=4, x=1.0, y=2.0, source="camtek_ini")
    ref = ImageItem("s", Path("/r/s/a.jpg"), "ref")
    val = ImageItem("s", Path("/v/s/b.jpg"), "val")
    monkeypatch.setattr(cm, "_resolve_batch",
                        lambda paths, cb=None: {ref.path: c, val.path: c})
    monkeypatch.setattr(notch, "folder_frame", lambda folder, source: None)
    cfg = config.SimilarityConfig(engine="coordinate", notch_dirs=(("/v", "up"),))
    sch = cm.CoordScheduler([("s", [ref], [val])], cfg=cfg, results={})
    msgs: list = []
    sch.signals.failed.connect(msgs.append)
    sch._run()
    assert len(msgs) == 1 and "Notch" in msgs[0]


def test_dialog_defaults_and_choice(styled_qapp):
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets.notch_dialog import NotchDialog

    dlg = NotchDialog("KLA-1", "AOI-5", "down", "garbage")
    assert dlg.dirs == ("down", "down")                # 모르는 값은 표준
    assert dlg.ref_group.keys() == notch.DIRECTIONS
    dlg.val_group.set_current_key("left")
    dlg.ref_group.set_current_key("up")
    assert dlg.dirs == ("up", "left")


def test_main_window_ask_notch_stores_dirs_only_when_not_standard(styled_qapp, monkeypatch, tmp_path):
    """`_ask_notch` — 좌표 모드가 아니면 비우고, 표준만 고르면 비우고, 다르면 (루트, 방향) 쌍."""
    pytest.importorskip("PyQt6.QtWidgets")
    from types import SimpleNamespace
    from aoi_verification.app.ui import main_window as mw
    from aoi_verification.app.ui.widgets import notch_dialog

    choice = {"v": ("down", "down")}

    class FakeDlg:
        def __init__(self, *a, **k):
            pass
        dirs = property(lambda self: choice["v"])

    monkeypatch.setattr(notch_dialog, "NotchDialog", FakeDlg)
    monkeypatch.setattr(mw.sheets, "run", lambda dlg, **k: 1)
    slot = SimpleNamespace(ref_dir=tmp_path / "r" / "s", val_dir=tmp_path / "v" / "s")
    me = SimpleNamespace(
        _scan=SimpleNamespace(common_slot_names=["s"], slots={"s": slot}),
        _input=SimpleNamespace(ref_machine="A", val_machine="B",
                               ref_root=tmp_path / "r", val_root=tmp_path / "v"),
        _notch_dirs=("stale",))
    ask = mw.MainWindow._ask_notch
    ask(me, False)
    assert me._notch_dirs == ()
    ask(me, True)
    assert me._notch_dirs == ()                         # 둘 다 표준
    choice["v"] = ("down", "right")
    ask(me, True)
    assert me._notch_dirs == ((str(tmp_path / "r"), "down"), (str(tmp_path / "v"), "right"))
