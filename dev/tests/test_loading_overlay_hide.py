"""로딩 오버레이가 숨을 때 **돌던 것을 전부 멈추는지** 못 박는다.

배경(실제 사고): 이 클래스에 ``hideEvent`` 가 두 번 정의돼 있었다.  파이썬은 뒤에
정의된 것만 남기므로 앞의 '애니메이션 정지' 본문은 한 줄도 실행되지 않았고,
`_finish_hide` 를 거치지 않는 퇴장(매칭 [중지] → 페이지 전환)에서 스피너 타이머
(16ms)와 busy 무한 애니메이션이 **숨은 채로 세션 내내 계속 돌았다** — 유휴 CPU 를
먹는 조용한 누수였다.
"""
from __future__ import annotations

import ast
import collections
import pathlib

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app.ui import motion  # noqa: E402
from aoi_verification.app.ui.widgets.loading_overlay import LoadingOverlay  # noqa: E402

from PyQt6.QtWidgets import QWidget  # noqa: E402


_SRC = (pathlib.Path(__file__).resolve().parents[2]
        / "aoi_verification" / "app" / "ui" / "widgets" / "loading_overlay.py")


def test_hide_event_is_defined_exactly_once() -> None:
    """같은 이름의 메서드를 두 번 정의하면 앞의 것이 조용히 죽는다."""
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "LoadingOverlay")
    names = collections.Counter(
        n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    dupes = {k: v for k, v in names.items() if v > 1}
    assert dupes == {}, f"LoadingOverlay 에 중복 정의된 메서드: {dupes}"


def test_hide_stops_every_running_thing(styled_qapp, monkeypatch) -> None:
    """hide() 한 번으로 busy·바·래치 타이머가 모두 멈춘다.

    ※ 예전엔 여기서 회전 링(스피너) 타이머도 함께 봤다.  링은 제거됐다 — 상태 정보가
    없는 장식인데 62.5Hz 로 상시 돌아, UI 스레드가 바쁠 때 가장 먼저 끊기며 '로딩이
    버벅인다' 로 보이던 그 애니메이션이다.  총량 미상 구간의 '살아 있다' 신호는 이제
    상단 눈금의 혜성 스윕(`_busy`)이 전담하므로, 그것이 멈추는지만 보면 된다."""
    # 헤드리스에서는 motion 이 꺼져 있어 fade/rise 가 아예 시작하지 않는다 — 켜서 태운다.
    monkeypatch.setattr(motion, "enabled", lambda: True)

    host = QWidget()
    host.resize(600, 400)
    ov = LoadingOverlay(host)
    ov.show_overlay("테스트", cancelable=True)     # busy 로 시작(총량 미상)

    assert ov._busy._anim.state() != ov._busy._anim.State.Stopped

    ov.hide()

    assert ov._busy._anim.state() == ov._busy._anim.State.Stopped, "busy 애니메이션이 남았다"
    assert not ov._bar_timer.isActive(), "진행바 스태거 타이머가 남았다"
    assert not ov._hide_timer.isActive(), "최소표시 래치 타이머가 남았다"
    assert ov._fade_anim.state() == ov._fade_anim.State.Stopped
    assert ov._rise_anim.state() == ov._rise_anim.State.Stopped


def test_hide_after_cancel_then_page_switch_leaves_nothing_running(
        styled_qapp, monkeypatch) -> None:
    """실제 사고 시나리오: [중지] → 최소표시 래치 대기 중 부모 페이지가 숨는다."""
    monkeypatch.setattr(motion, "enabled", lambda: True)

    host = QWidget()
    host.resize(600, 400)
    ov = LoadingOverlay(host)
    ov.show_overlay("매칭 중", cancelable=True)
    ov.hide_overlay()          # 최소 표시(350ms) 가 안 지나 래치만 걸린 상태
    host.hide()                # 페이지 전환 — 오버레이도 함께 숨는다

    assert ov._busy._anim.state() == ov._busy._anim.State.Stopped
    assert not ov._hide_timer.isActive()


def test_cancel_button_has_no_baked_stylesheet(styled_qapp) -> None:
    """[중지] 는 role 로 칠한다 — 인라인 스타일은 다크 전환을 영영 못 따라온다."""
    host = QWidget()
    ov = LoadingOverlay(host)
    assert ov._cancel_btn.styleSheet() == ""
    assert ov._cancel_btn.property("role") == "danger"
