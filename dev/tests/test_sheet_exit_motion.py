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

from PyQt6.QtCore import QElapsedTimer, Qt              # noqa: E402
from PyQt6.QtWidgets import (QApplication, QDialog, QLabel,  # noqa: E402
                             QMainWindow, QWidget)

from aoi_verification.app.ui import motion              # noqa: E402
from aoi_verification.app.ui.widgets import sheet_host as sh  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def motion_on(monkeypatch):
    monkeypatch.setattr(motion, "enabled", lambda: True)
    yield


def _spin(qapp, ms: int) -> None:
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        qapp.processEvents()


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

    _spin(qapp, 400)                      # dur(150) 을 넉넉히 넘긴다
    assert not host._ghosts, "스냅샷이 정리되지 않았다"
    assert not host.isVisible(), "마지막 시트가 닫혔으면 호스트도 내려가야 한다"


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
    _spin(qapp, 400)                      # 여기서 죽으면 회귀
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
