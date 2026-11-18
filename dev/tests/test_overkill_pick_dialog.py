"""Overkill 튜닝 — Defect 고르기 팝업(`overkill_pick_dialog`) 헤드리스 검증."""

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QPixmap  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QMenu  # noqa: E402

from aoi_verification.app import i18n  # noqa: E402
from aoi_verification.app.ui import motion  # noqa: E402
from aoi_verification.app.ui.widgets.overkill_pick_dialog import OverkillPickDialog  # noqa: E402


class D:
    def __init__(self, zone, n):
        self.zone, self.n = zone, n
        self.zone_dir = f"/z/{zone}"


def _pm():
    pm = QPixmap(40, 40)
    pm.fill(QColor("#888888"))
    return pm


class Env:
    def __init__(self, ready=True, missed=0):
        self.data = {"a": [D("a", i) for i in range(4)], "b": [D("b", i) for i in range(3)]}
        self.ready = ready
        self.missed = missed
        self.requests = []
        self.enlarged = []
        self.calls = []

    def make(self, picked=None, overkill=None, zone="a"):
        return OverkillPickDialog(
            zones=[("a", "구역 A"), ("b", "구역 B")], zone=zone,
            items_of=lambda z: self.data[z], key_of=lambda d: (d.zone, d.n),
            pixmap_of=lambda d, s: _pm() if self.ready else None,
            request_pics=lambda ds, s: self.requests.append((list(ds), s)),
            caption_of=lambda d: f"cap {d.zone}{d.n}",
            picked=picked or {}, overkill=overkill or {},
            preview=self._preview, enlarge=self.enlarged.append)

    def _preview(self, picked, overkill):
        self.calls.append((len(picked), len(overkill)))
        return (len(picked) * 2, len(overkill), self.missed)


@pytest.fixture(autouse=True)
def _no_motion(monkeypatch):
    monkeypatch.setattr(motion, "enabled", lambda: False)


def test_defect_advances_to_next_undecided(styled_qapp):
    env = Env()
    dlg = env.make()
    assert dlg.current_key() == ("a", 0)
    dlg.btn_def.click()
    assert list(dlg.picked()) == [("a", 0)]
    assert dlg.current_key() == ("a", 1)
    assert dlg.right.count() == 1


def test_left_marks_overkill_and_undo(styled_qapp):
    env = Env()
    dlg = env.make()
    dlg.btn_over.click()
    assert list(dlg.overkill()) == [("a", 0)] and dlg.left.count() == 1
    dlg.btn_undo.click()
    assert dlg.overkill() == {} and dlg.left.count() == 0
    assert dlg.current_key() == ("a", 0)


def test_keys_left_right_and_z(styled_qapp):
    env = Env()
    dlg = env.make()
    dlg.show()
    dlg.activateWindow()
    QTest.qWaitForWindowActive(dlg)
    QTest.keyClick(dlg, Qt.Key.Key_Right)
    QTest.keyClick(dlg, Qt.Key.Key_Left)
    assert list(dlg.picked()) == [("a", 0)] and list(dlg.overkill()) == [("a", 1)]
    QTest.keyClick(dlg, Qt.Key.Key_Z)
    assert dlg.overkill() == {}
    dlg.close()


def test_click_panel_tile_makes_current_and_redecide_moves(styled_qapp):
    env = Env()
    dlg = env.make()
    dlg.btn_def.click()
    dlg.btn_def.click()
    tile = dlg.right.tile(("a", 0))
    tile.clicked.emit(tile.d)
    assert dlg.current_key() == ("a", 0)
    dlg.btn_over.click()
    assert ("a", 0) in dlg.overkill() and ("a", 0) not in dlg.picked()
    assert dlg.left.count() == 1 and dlg.right.count() == 1


def test_all_decided_shows_done(styled_qapp):
    env = Env()
    dlg = env.make()
    for _ in range(4):
        dlg.btn_def.click()
    assert not dlg.done_label.isHidden()
    assert dlg.counts.text() == i18n.KO.OVERKILL_PICK_COUNTS_FMT.format(total=4, left=0)


def test_zone_switch_keeps_decisions(styled_qapp):
    env = Env()
    dlg = env.make()
    dlg.btn_def.click()
    dlg.combo.setCurrentIndex(1)
    dlg.combo.activated.emit(1)
    assert dlg.current_key() == ("b", 0)
    dlg.btn_over.click()
    assert list(dlg.picked()) == [("a", 0)] and list(dlg.overkill()) == [("b", 0)]
    # 다른 구역 타일을 누르면 그 구역으로 돌아간다
    t = dlg.right.tile(("a", 0))
    t.clicked.emit(t.d)
    assert dlg.current_key() == ("a", 0)


def test_preview_line_and_missed_red(styled_qapp):
    env = Env(missed=0)
    dlg = env.make()
    dlg.btn_def.click()
    dlg.btn_over.click()
    dlg.flush_preview()
    assert env.calls[-1] == (1, 1)
    assert dlg.result_label.text() == i18n.KO.OVERKILL_PICK_RESULT_FMT.format(drop=2, n=1, gone=1)
    assert dlg.missed.text() == ""
    env.missed = 3
    dlg.btn_def.click()
    dlg.flush_preview()
    assert dlg.missed.text() == i18n.KO.OVERKILL_PICK_MISSED_FMT.format(k=3)
    assert dlg.missed.property("role") == "pickMissed"
    assert dlg.btn_apply.text() == i18n.KO.OVERKILL_PICK_BTN_APPLY_FMT.format(n=2)


def test_apply_emits_and_esc_does_not(styled_qapp):
    env = Env()
    dlg = env.make(picked={("a", 3): env.data["a"][3]})
    got = []
    dlg.applied.connect(lambda p, o: got.append((p, o)))
    dlg.btn_over.click()
    dlg.btn_apply.click()
    assert len(got) == 1
    p, o = got[0]
    assert list(p) == [("a", 3)] and list(o) == [("a", 0)]

    dlg2 = env.make()
    got2 = []
    dlg2.applied.connect(lambda *a: got2.append(a))
    dlg2.show()
    QTest.keyClick(dlg2, Qt.Key.Key_Escape)
    assert got2 == [] and not dlg2.isVisible()


def test_right_click_enlarges_without_deciding(styled_qapp, monkeypatch):
    env = Env()
    dlg = env.make()
    monkeypatch.setattr(QMenu, "exec", lambda self, *a: self.actions()[0])
    t = dlg._tile_at(2)
    t.context_requested.emit(t.d, QPoint(0, 0))
    dlg.big.context_requested.emit(QPoint(0, 0))
    assert env.enlarged == [env.data["a"][2], env.data["a"][0]]
    assert dlg.picked() == {} and dlg.overkill() == {}


def test_placeholder_then_refresh(styled_qapp):
    env = Env(ready=False)
    dlg = env.make()
    assert not dlg.big.has_photo()
    assert dlg.big.text() == i18n.KO.OVERKILL_PICK_LOADING
    assert any(s > 100 for _, s in env.requests)
    env.ready = True
    dlg.refresh_pics()
    assert dlg.big.has_photo()


# ----------------------------------------------------------------- 큰 사진 즉시 표시 · 미리 불러오기 · 위젯 수 상한
class Many(Env):
    def __init__(self, n=400, cached=None):
        super().__init__()
        self.data = {"a": [D("a", i) for i in range(n)], "b": []}
        self.cached = cached if cached is not None else set()   # (n, size) 이미 캐시에 있는 그림

    def make(self, **kw):
        dlg = super().make(**kw)
        return dlg


def _make_many(env):
    from aoi_verification.app.ui.widgets import overkill_pick_dialog as O
    return OverkillPickDialog(
        zones=[("a", "A"), ("b", "B")], zone="a", items_of=lambda z: env.data[z], key_of=lambda d: (d.zone, d.n),
        pixmap_of=lambda d, s: _pm() if (d.n, s) in env.cached else None,
        request_pics=lambda ds, s: env.requests.append(([d.n for d in ds], s)),
        caption_of=lambda d: "c", picked={}, overkill={}, preview=env._preview, enlarge=env.enlarged.append), O


def test_big_photo_shows_thumbnail_at_once_when_mid_is_not_loaded(styled_qapp):
    """중간 이미지가 아직이어도 다음 사진은 썸네일 화질로 바로 뜬다(불러오는 중 글자 없음) — 오면 바꿔 끼운다."""
    env = Many(10)
    dlg, O = _make_many(env)
    env.cached = {(n, O._THUMB) for n in range(10)}                 # 썸네일만 불러 둠
    dlg.btn_def.click()
    assert dlg.big.has_photo() and dlg.big.text() == "" and not dlg._big_final
    assert any(s == O._BIG and 1 in ns for ns, s in env.requests)  # 큰 그림은 따로 부탁해 둔다
    env.cached |= {(n, O._BIG) for n in range(10)}
    dlg.refresh_pics()
    assert dlg._big_final


def test_prefetch_requests_upcoming_big_and_all_zone_thumbs(styled_qapp):
    from aoi_verification.app.ui.widgets import overkill_pick_dialog as O
    env = Many(400)
    dlg, O = _make_many(env)
    thumbs = [ns for ns, s in env.requests if s == O._THUMB]
    assert thumbs and set(range(min(400, O._PRELOAD_MAX))) <= {n for ns in thumbs for n in ns}   # 구역 전체를 미리
    env.requests.clear()
    dlg.btn_def.click()                                             # 0 → 1
    bigs = [n for ns, s in env.requests if s == O._BIG for n in ns]
    assert set(bigs) >= {1, 2, 3, 4}                                # 지금 · 다음 몇 장의 큰 그림


def test_strip_and_panels_keep_widget_count_bounded(styled_qapp):
    """수천 장 구역에서도 띠 · 패널 타일 위젯은 창 크기만큼만 — 닫을 때(재부모화) 위젯 수만큼 느려지던 것."""
    from aoi_verification.app.ui.widgets import overkill_pick_dialog as O
    env = Many(3000)
    dlg, O = _make_many(env)
    assert len(dlg._strip) <= 2 * O._STRIP_WIN + 1
    for _ in range(300):
        dlg.btn_def.click()
    assert len(dlg._strip) <= 4 * O._STRIP_WIN + 2 and dlg._cur in dlg._strip
    assert dlg.right.count() == 300 and len(dlg.right._tiles) == O._PANEL_MAX
    dlg.btn_undo.click()
    assert dlg.right.count() == 299 and len(dlg.right._tiles) == O._PANEL_MAX
    dlg._goto(env.data["a"][2500])                                  # 멀리 건너뛰어도 창이 따라온다
    assert dlg._cur == 2500 and dlg._tile_at(2500) is not None and len(dlg._strip) <= 4 * O._STRIP_WIN + 2


def test_result_label_does_not_shadow_qdialog_result(styled_qapp):
    """`result` 가 QLabel 이면 시트가 닫힐 때 `widget.result()` 가 TypeError → 슬롯 예외로 앱이 조용히 꺼졌다."""
    dlg = Env().make()
    assert callable(dlg.result) and dlg.result() == 0
    dlg.accept()
    assert dlg.result() == 1


def test_preview_is_deferred_until_decisions_pause(styled_qapp):
    env = Env()
    dlg = env.make()
    n0 = len(env.calls)
    dlg.btn_def.click()
    dlg.btn_over.click()
    assert len(env.calls) == n0                                     # 눌러 대는 동안 전체 훑기는 하지 않는다
    dlg.flush_preview()
    assert len(env.calls) == n0 + 1 and env.calls[-1] == (1, 1)
