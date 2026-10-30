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


def test_kla_frame_refuses_guessed_pitch(tmp_path):
    """DiePitch 가 없어 TB500 기본값을 댄 KLA 폴더는 돌리지 않는다(중심만 있어도) — 다른 die
    크기의 자재를 조용히 틀리게 돌리지 않게 `NotchError` 로 간다(리뷰 지적)."""
    from aoi_verification.app.coords import wafer_geometry as wg
    (tmp_path / "wafer.001").write_text(
        'WaferID "W1";\nSampleCenterLocation 150000 150000;\n', encoding="utf-8")
    wg.kla_geometry.cache_clear()
    assert wg.kla_geometry(tmp_path).source == "fallback"
    assert notch.folder_frame(tmp_path, "kla") is None


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
    """`_ask_notch` — 둘 다 아래로 감지되면 창 없이 비우고, 아니면 창에서 고른 (루트, 방향) 쌍."""
    pytest.importorskip("PyQt6.QtWidgets")
    from types import SimpleNamespace
    from aoi_verification.app.ui import main_window as mw
    from aoi_verification.app.ui.widgets import notch_dialog
    from aoi_verification.app.workers.notch_auto import AutoNotch

    choice = {"v": ("down", "down")}
    shown = []

    class FakeDlg:
        def __init__(self, *a, **k):
            shown.append(k.get("auto_result"))
        dirs = property(lambda self: choice["v"])

    monkeypatch.setattr(notch_dialog, "NotchDialog", FakeDlg)
    monkeypatch.setattr(mw.sheets, "run", lambda dlg, **k: 1)
    me = SimpleNamespace(
        _input=SimpleNamespace(ref_machine="A", val_machine="B",
                               ref_root=tmp_path / "r", val_root=tmp_path / "v"),
        _notch_dirs=("stale",), _notch_needs_confirm=mw.MainWindow._notch_needs_confirm)
    ask = mw.MainWindow._ask_notch
    ask(me, "down", "down", AutoNotch("down", 30, 2, 40))
    assert me._notch_dirs == () and shown == []         # 둘 다 아래 — 묻지 않는다
    right = AutoNotch("right", 30, 2, 40)
    choice["v"] = ("down", "right")
    ask(me, "down", "down", right)
    assert shown == [right]                              # 감지 결과를 창에 넘긴다
    assert me._notch_dirs == ((str(tmp_path / "r"), "down"), (str(tmp_path / "v"), "right"))
    choice["v"] = ("down", "down")
    ask(me, "down", "down", right)                       # 창에서 아래로 되돌리면 보정 없음
    assert me._notch_dirs == ()


@pytest.mark.parametrize("ref_dir,direction,expected", [
    ("down", "down", False),
    ("down", "right", True),
    ("up", "down", True),          # 기준(KLA 정보파일)이 아래가 아니다
    ("down", None, True),          # 감지가 방향을 못 냈다 — 아래로 두지 않고 묻는다
])
def test_notch_confirm_only_when_not_both_down(ref_dir, direction, expected):
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui import main_window as mw
    from aoi_verification.app.workers.notch_auto import AutoNotch
    res = AutoNotch(direction, 30, 2, 40)
    assert mw.MainWindow._notch_needs_confirm(ref_dir, res) is expected
    assert mw.MainWindow._notch_needs_confirm("down", None) is True


# ---------------------------------------------------------------------------
# 자동 감지
def _auto_setup(monkeypatch, val_dir, pts_ref, pts_val):
    from aoi_verification.app.workers import notch_auto
    die = (11000.0, 8000.0)
    rc, _ = _scan(pts_ref, notch.DOWN, *die, 700.0, 300.0)
    vc, vframe = _scan(pts_val, val_dir, *die, 700.0, 300.0)
    refs = [Path(f"/r/s/{i}.jpg") for i in range(len(rc))]
    vals = [Path(f"/v/s/{i}.jpg") for i in range(len(vc))]
    table = dict(zip(refs, rc)) | dict(zip(vals, vc))
    monkeypatch.setattr(notch, "folder_frame", lambda folder, source: vframe)
    return notch_auto, [(refs, vals)], table


@pytest.mark.parametrize("val_dir", notch.DIRECTIONS)
def test_auto_detect_finds_val_direction(monkeypatch, val_dir):
    pytest.importorskip("PyQt6.QtCore")
    pts = _points(60, seed=3)
    na, groups, table = _auto_setup(monkeypatch, val_dir, pts, pts)
    res = na.score_directions(groups, "/r", "/v", "down", 200.0,
                              resolve=lambda paths: {p: table[p] for p in paths})
    assert res.direction == val_dir and res.support >= 50


@pytest.mark.parametrize("val_dir", notch.DIRECTIONS)
@pytest.mark.parametrize("shift", [(-635.0, 3.0), (90.0, -380.0)])
def test_auto_detect_survives_kla_camtek_offset(monkeypatch, val_dir, shift):
    """KLA ↔ Camtek 처럼 같은 결함이 tol(200) 보다 멀리 어긋나도 방향을 찾는다.

    관측(2026-10-28, Kendall RDL4 · Camtek Notch Right): 90° 돌린 뒤 짝 10쌍이 한 방향으로
    약 635 µm 어긋났다(흩어짐 수 µm).  어긋남을 빼지 않고 세면 맞는 방향도 0 이었다."""
    pytest.importorskip("PyQt6.QtCore")
    from dataclasses import replace
    pts = _points(60, seed=3)
    na, groups, table = _auto_setup(monkeypatch, val_dir, pts, pts)
    refs = groups[0][0]
    # 기준 쪽(=KLA)에 어긋남을 준다 — 보정은 die 경계를 넘어도 번호를 다시 매기지 않는다.
    table.update({p: replace(table[p], x=table[p].x - shift[0], y=table[p].y - shift[1])
                  for p in refs})
    res = na.score_directions(groups, "/r", "/v", "down", 200.0,
                              resolve=lambda paths: {p: table[p] for p in paths})
    assert res.direction == val_dir and res.support >= 50
    assert res.offset == pytest.approx(shift, abs=20.0)


def test_auto_detect_refuses_when_unrelated(monkeypatch):
    pytest.importorskip("PyQt6.QtCore")
    na, groups, table = _auto_setup(monkeypatch, notch.LEFT,
                                    _points(60, seed=3), _points(60, seed=99))
    res = na.score_directions(groups, "/r", "/v", "down", 200.0,
                              resolve=lambda paths: {p: table[p] for p in paths})
    assert res.direction is None


def test_dialog_auto_result_fills_val_only(styled_qapp):
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets.notch_dialog import NotchDialog
    from aoi_verification.app.workers.notch_auto import AutoNotch
    dlg = NotchDialog("A", "B", "up", "down", auto_args=([], "/r", "/v", 200.0))
    dlg._on_auto_done(AutoNotch("right", 40, 3, 50))
    assert dlg.dirs == ("up", "right")
    dlg._on_auto_done(AutoNotch(None, 2, 1, 50))      # 못 찾으면 그대로
    assert dlg.dirs == ("up", "right") and dlg.status.text()


def test_dialog_takes_precomputed_auto_result(styled_qapp):
    """매칭 전에 돌린 감지 결과가 창을 열 때 바로 들어간다(다시 누를 필요 없음)."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets.notch_dialog import NotchDialog
    from aoi_verification.app.workers.notch_auto import AutoNotch
    dlg = NotchDialog("A", "B", "down", "down", auto_result=AutoNotch("right", 9, 0, 14))
    assert dlg.dirs == ("down", "right") and "9" in dlg.status.text()


@pytest.mark.parametrize("found,expect_dialog", [("down", False), ("right", True)])
def test_main_window_runs_notch_auto_before_matching(styled_qapp, monkeypatch, tmp_path,
                                                     found, expect_dialog):
    """좌표 매칭 시작 → 백그라운드 감지 → (둘 다 아래면 창 없이) 다음 단계로 이어진다."""
    QtCore = pytest.importorskip("PyQt6.QtCore")
    from types import SimpleNamespace
    from aoi_verification.app.ui import main_window as mw
    from aoi_verification.app.ui.widgets import notch_dialog
    from aoi_verification.app.workers import notch_auto
    from aoi_verification.app.workers.notch_auto import AutoNotch

    class FakeWorker(QtCore.QThread):
        progress = QtCore.pyqtSignal(int, int)
        done = QtCore.pyqtSignal(object)

        def __init__(self, groups, ref_root, val_root, ref_dir, tol, parent=None):
            super().__init__(parent)

        def run(self):
            self.done.emit(AutoNotch(found, 20, 1, 25))

    shown = []

    class FakeDlg:
        def __init__(self, *a, **k):
            shown.append(k.get("auto_result"))
        dirs = property(lambda self: ("down", "right"))

    monkeypatch.setattr(notch_auto, "NotchAutoWorker", FakeWorker)
    monkeypatch.setattr(notch_dialog, "NotchDialog", FakeDlg)
    monkeypatch.setattr(mw.sheets, "run", lambda dlg, **k: 1)

    class Host(QtCore.QObject):
        _start_notch_auto = mw.MainWindow._start_notch_auto
        _on_notch_progress = mw.MainWindow._on_notch_progress
        _on_notch_auto_done = mw.MainWindow._on_notch_auto_done
        _ask_notch = mw.MainWindow._ask_notch
        _notch_needs_confirm = staticmethod(mw.MainWindow._notch_needs_confirm)

        def _after_notch(self):
            self.after.append(self._notch_dirs)

    slot = SimpleNamespace(ref_dir=tmp_path / "r" / "s", val_dir=tmp_path / "v" / "s",
                           ref_images=[], val_images=[])
    host = Host()
    host.after, host._notch_dirs, host._calib_next = [], (), ([], {}, False)
    host._scan = SimpleNamespace(common_slot_names=["s"], slots={"s": slot})
    host._input = SimpleNamespace(ref_machine="A", val_machine="B", coord_tolerance=200.0,
                                  ref_root=tmp_path / "r", val_root=tmp_path / "v")
    host._loading = SimpleNamespace(show_overlay=lambda *a: None, hide_overlay=lambda: None,
                                    set_progress=lambda *a: None)
    assert host._start_notch_auto() is True
    for _ in range(200):
        styled_qapp.processEvents()
        if host.after:
            break
        QtCore.QThread.msleep(5)
    assert len(host.after) == 1 and (len(shown) == 1) is expect_dialog
    if expect_dialog:
        assert host.after[0] == ((str(tmp_path / "r"), "down"), (str(tmp_path / "v"), "right"))
    else:
        assert host.after[0] == ()


def test_auto_detect_offset_vote_does_not_invent_a_direction_on_dense_noise(monkeypatch):
    """사진이 많아도(서로 무관한 600장) 방향마다 뺀 어긋남이 우연한 짝을 '방향' 으로 만들지 않는다."""
    pytest.importorskip("PyQt6.QtCore")
    na, groups, table = _auto_setup(monkeypatch, notch.RIGHT,
                                    _points(600, seed=5), _points(600, seed=77))
    res = na.score_directions(groups, "/r", "/v", "down", 200.0,
                              resolve=lambda paths: {p: table[p] for p in paths})
    assert res.direction is None
