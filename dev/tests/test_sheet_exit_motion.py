"""시트(앱 내 팝업)가 **닫힐 때도** 모션을 탄다 — 등장만 있고 퇴장은 '툭' 끊겼다.

퇴장 모션은 시트 위젯 자체가 아니라 **스냅샷(ghost QLabel)** 에 건다.
``WA_DeleteOnClose`` 다이얼로그는 닫히는 즉시 파괴될 수 있어, 원본에 애니메이션을
걸면 tick 이 죽은 C++ 객체로 들어가 세그폴트가 난다(sheet_host 모듈 주석 5번).
그림만 남기고 원본은 곧바로 정리하면 생명주기가 얽히지 않는다.

★ 모션을 강제로 켜야(offscreen 은 기본 False) 애니메이션 경로를 탄다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import (QAbstractAnimation, QPropertyAnimation,  # noqa: E402
                          Qt, QTimer)
from PyQt6.QtWidgets import (QDialog, QLabel,                # noqa: E402
                             QMainWindow, QWidget)

from aoi_verification.app.ui import motion              # noqa: E402
from aoi_verification.app.ui.widgets import sheet_host as sh  # noqa: E402


@pytest.fixture
def motion_on(monkeypatch):
    monkeypatch.setattr(motion, "enabled", lambda: True)
    yield


def _running_ghost_animations(host) -> list:
    """퇴장 스냅샷에 걸린 opacity 애니메이션들."""
    out = []
    for ghost in host._ghosts:
        out.extend(ghost.findChildren(QPropertyAnimation))
    return out


def _finish_ghost_animations(host) -> None:
    """퇴장 애니메이션을 **끝으로 밀어** finished 를 발화시킨다.

    ★ 벽시계로 기다리지 않는다.  헤드리스 전체 스위트에서는 Qt 전역 애니메이션
    드라이버가 틱을 주지 않아(실측: state=Running 인데 currentTime 이 4초 뒤에도 0)
    시간 기반 대기가 통째로 불안정해진다.  여기서 검증하려는 것은 '시간이 흐르면
    Qt 가 값을 보간한다'가 아니라 **끝났을 때 우리 정리 코드가 도는가** 이므로,
    애니메이션을 종료 시점으로 직접 이동시켜 그 경로만 확인한다.
    """
    anims = _running_ghost_animations(host)
    assert anims, "퇴장 애니메이션이 걸리지 않았다 — 닫기 모션이 없다"
    for a in anims:
        a.setCurrentTime(a.duration())


@pytest.fixture
def host(qapp):
    win = QMainWindow()
    win.resize(600, 400)
    body = QWidget(win)
    win.setCentralWidget(body)
    win.show()
    qapp.processEvents()
    h = sh.SheetHost(win)
    h.resize(600, 400)
    yield h
    h.close_all()
    win.close()


def _dialog(*, delete_on_close: bool) -> QDialog:
    d = QDialog()
    d.resize(200, 120)
    QLabel("내용", d)
    if delete_on_close:
        d.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    return d


def test_close_leaves_fading_ghost(host, qapp, motion_on):
    """닫는 순간 스냅샷이 남아 페이드아웃한다(= 퇴장 모션이 있다)."""
    d = _dialog(delete_on_close=False)
    d.setWindowTitle("")
    entry_before = len(host._stack)
    # run() 은 중첩 루프라 테스트에서 직접 못 쓴다 — 내부 절차를 그대로 흉내낸다.
    d.setParent(host)
    d.setWindowFlags(Qt.WindowType.Widget)
    entry = {"widget": d, "root": d, "frame": None, "loop": _FakeLoop(),
             "prev_parent": None, "prev_flags": d.windowFlags(),
             "full_bleed": False}
    host._stack.append(entry)
    host.show()
    d.show()
    qapp.processEvents()

    host._close(entry)
    assert len(host._stack) == entry_before
    assert host._ghosts, "퇴장 스냅샷이 없다 — 닫기 모션이 걸리지 않았다"
    ghost = host._ghosts[0]
    assert isinstance(ghost, QLabel) and ghost.isVisible()
    # ★ 어둠은 잔상을 기다리지 않는다 — 닫는 그 프레임에 내려간다.
    assert host._scrim.isHidden(), (
        "스크림이 잔상 페이드를 기다린다 — 팝업이 사라진 뒤 어두운 화면만 남는다")
    assert host.isVisible(), "잔상이 얹힐 바닥(호스트)까지 먼저 내려갔다"

    _finish_ghost_animations(host)
    qapp.processEvents()
    assert not host._ghosts, "스냅샷이 정리되지 않았다"
    assert not host.isVisible(), "마지막 시트가 닫혔으면 호스트도 내려가야 한다"


def test_scrim_stays_while_a_lower_sheet_remains(host, qapp, motion_on):
    """중첩 시트에서 위쪽만 닫히면 어둠은 그대로 — 아래 시트가 아직 열려 있다."""
    lower, upper = _dialog(delete_on_close=False), _dialog(delete_on_close=False)
    entries = []
    for d in (lower, upper):
        d.setParent(host)
        d.setWindowFlags(Qt.WindowType.Widget)
        entry = {"widget": d, "root": d, "frame": None, "loop": _FakeLoop(),
                 "prev_parent": None, "prev_flags": d.windowFlags(),
                 "full_bleed": False}
        host._stack.append(entry)
        entries.append(entry)
    host.show()
    host._scrim.show()
    lower.show()
    upper.show()
    qapp.processEvents()

    host._close(entries[1])
    assert not host._scrim.isHidden(), "아래 시트가 남았는데 어둠이 걷혔다"
    assert host.isVisible()


def test_open_fades_the_scrim_in(host, qapp, motion_on):
    """열 때는 어둠이 0→1 로 들어온다(한 프레임에 슬램하지 않는다)."""
    scrim = host._scrim
    scrim.fade_in()
    assert scrim._fade == 0.0, "페이드가 시작점(투명)에서 출발하지 않았다"
    anim = scrim._fade_anim
    assert anim is not None
    anim.setCurrentTime(anim.duration())     # 벽시계 대신 끝으로 민다
    assert scrim._fade == 1.0, "페이드가 끝에서 최대 어둠에 닿지 않았다"


def test_open_is_instant_when_motion_is_off(host, qapp, monkeypatch):
    """헤드리스(모션 off)에서는 트윈 없이 즉시 최대 어둠 — 결정성 유지."""
    monkeypatch.setattr(motion, "enabled", lambda: False)
    scrim = host._scrim
    scrim._fade = 0.0
    scrim.fade_in()
    assert scrim._fade == 1.0
    assert scrim._fade_anim is None, "모션이 꺼졌는데 애니메이션을 만들었다"


def test_nested_open_does_not_restart_the_scrim_fade(host, qapp, motion_on):
    """시트 위에 시트를 열 때 어둠을 다시 깜빡이지 않는다.

    바깥 시트의 페이드를 끝난 상태로 만들어 둔 뒤 안쪽 시트를 연다 — 그때 어둠이
    다시 트윈을 시작하면(애니메이션이 Running) 화면이 한 번 더 깜빡인 것이다."""
    outer, inner = _dialog(delete_on_close=False), _dialog(delete_on_close=False)
    seen = {}

    def open_inner():
        scrim = host._scrim
        if scrim._fade_anim is not None:
            scrim._fade_anim.stop()         # 바깥 시트의 페이드는 끝났다고 본다
        scrim._set_fade(1.0)

        def finish_inner():
            anim = scrim._fade_anim
            seen["running"] = (anim is not None
                               and anim.state() == QAbstractAnimation.State.Running)
            seen["fade"] = scrim._fade
            inner.accept()

        QTimer.singleShot(20, finish_inner)
        host.run(inner)
        outer.accept()

    QTimer.singleShot(20, open_inner)
    host.run(outer)
    assert seen["running"] is False, "중첩 시트가 어둠을 다시 트윈했다"
    assert seen["fade"] == 1.0, "중첩 시트가 어둠을 0 에서 다시 시작했다"


def test_close_with_delete_on_close_does_not_crash(host, qapp, motion_on):
    """원본이 닫히는 즉시 파괴돼도 퇴장 모션이 살아서 끝난다(세그폴트 회귀)."""
    d = _dialog(delete_on_close=True)
    d.setParent(host)
    d.setWindowFlags(Qt.WindowType.Widget)
    entry = {"widget": d, "root": d, "frame": None, "loop": _FakeLoop(),
             "prev_parent": None, "prev_flags": d.windowFlags(),
             "full_bleed": False}
    host._stack.append(entry)
    host.show()
    d.show()
    qapp.processEvents()

    host._close(entry)
    d.deleteLater()                       # 원본을 곧바로 파괴 예약
    _finish_ghost_animations(host)        # 여기서 죽으면 회귀
    qapp.processEvents()
    assert not host._ghosts


def test_new_sheet_drops_stale_ghost(host, qapp, motion_on):
    """퇴장 도중 새 시트가 열리면 남은 잔상을 즉시 치운다(겹침 방지)."""
    d = _dialog(delete_on_close=False)
    d.setParent(host)
    d.setWindowFlags(Qt.WindowType.Widget)
    entry = {"widget": d, "root": d, "frame": None, "loop": _FakeLoop(),
             "prev_parent": None, "prev_flags": d.windowFlags(),
             "full_bleed": False}
    host._stack.append(entry)
    host.show()
    d.show()
    qapp.processEvents()
    host._close(entry)
    assert host._ghosts                    # 아직 페이드 중

    host._drop_ghosts()                    # run() 이 새 시트를 열 때 부르는 것
    assert not host._ghosts


def test_motion_off_closes_immediately(host, qapp, monkeypatch):
    """모션이 꺼진 환경(헤드리스 기본)에서는 스냅샷 없이 즉시 닫힌다."""
    monkeypatch.setattr(motion, "enabled", lambda: False)
    d = _dialog(delete_on_close=False)
    d.setParent(host)
    d.setWindowFlags(Qt.WindowType.Widget)
    entry = {"widget": d, "root": d, "frame": None, "loop": _FakeLoop(),
             "prev_parent": None, "prev_flags": d.windowFlags(),
             "full_bleed": False}
    host._stack.append(entry)
    host.show()
    d.show()
    qapp.processEvents()

    host._close(entry)
    assert not host._ghosts
    assert not host.isVisible()


class _FakeLoop:
    """``QEventLoop`` 대역 — 테스트는 중첩 루프를 돌리지 않는다."""

    def isRunning(self) -> bool:            # noqa: N802
        return False

    def quit(self) -> None:
        pass
