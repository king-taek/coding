"""다크 모드 전환 — 크로스페이드 + **전환 중 토글 잠금**.

색이 한 프레임에 튀지 않도록, 옛 색 화면을 스냅샷으로 떠 두고 아래에서 새 색 페이지로
갈아 끼운 뒤 스냅샷을 빼낸다(`motion.crossfade_from`).

전환 중 연타는 두 겹으로 막는다:
  (a) `_appearance_busy` — 재진입 자체를 차단(눌러도 아무 일이 없음)
  (b) 새 페이지 스위치 비활성 — 눌리지 않는 것이 **눈에 보이게**
(a) 만 있으면 '먹은 클릭'이 되고, (b) 만 있으면 페이지 재생성 사이 틈으로 두 번째
요청이 새어 든다.

★ 잠금이 풀리지 않으면 다크 모드를 **영구히** 못 바꾼다 — 원래 버그보다 나쁘다.
그래서 '풀린다'를 성공 경로·즉시완료 경로 양쪽에서 확인한다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import inspect

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QElapsedTimer                       # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QWidget    # noqa: E402

from aoi_verification.app.ui import motion, theme            # noqa: E402
from aoi_verification.app.ui import main_window as mw        # noqa: E402
from aoi_verification.app.utils import prefs as _prefs       # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def motion_on(monkeypatch):
    monkeypatch.setattr(motion, "enabled", lambda: True)
    yield


@pytest.fixture
def window(qapp, monkeypatch, isolated_cache):
    """MainWindow — ★ 시작 시 400ms 뒤 뜨는 **업데이트 확인**을 막는다.

    막지 않으면 모달 이벤트 루프가 열려 `processEvents()` 가 영원히 돌아오지 않는다
    (실측: 375ms 지점에서 테스트가 멈췄다).  제품 동작이 아니라 하네스 문제이므로
    여기서만 끈다."""
    monkeypatch.setattr(mw.MainWindow, "_check_for_update_async", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_maybe_offer_openvino", lambda self: None)
    theme.set_color_mode("light")
    w = mw.MainWindow()
    w.resize(900, 700)
    w.show()
    for _ in range(20):
        qapp.processEvents()
    yield w
    w.close()
    w.deleteLater()
    qapp.processEvents()
    theme.set_color_mode("light")


def _spin(qapp, ms: int) -> None:
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        qapp.processEvents()


def _spin_recolor(qapp) -> None:
    """색 전환이 **끝나고도 남을 만큼** 돌린다.

    ★ 고정 900ms 를 쓰면 안 된다 — `DUR_RECOLOR` 를 늘리는 순간(420 → 700) 대기가
    전환보다 짧아져 테스트가 시간에 기대 흔들린다(실제로 그렇게 깨졌다).
    지속시간에서 유도한다."""
    _spin(qapp, motion.DUR_RECOLOR + 250)


def _overlay(w):
    # ★ 오버레이는 **창 전체**의 자식이다(스택이 아니다) — 상태바처럼 스택 밖에 있는
    #   것도 함께 덮어야 전환 중 색이 갈라지지 않는다.
    return w.findChild(QLabel, "_pageRecolorOverlay")


# ── 크로스페이드 자체 ────────────────────────────────────────────────────
def test_crossfade_from_fires_on_done_exactly_once(qapp, motion_on):
    host = QWidget()
    host.resize(300, 200)
    host.show()
    for _ in range(8):
        qapp.processEvents()
    calls: list[int] = []
    try:
        motion.crossfade_from(host, host.grab(), on_done=lambda: calls.append(1))
        assert host.findChild(QLabel, "_pageRecolorOverlay") is not None
        assert calls == [], "아직 끝나지 않았는데 on_done 이 불렸다"
        _spin(qapp, motion.DUR_RECOLOR + 250)
        assert calls == [1], f"on_done 이 {len(calls)}회 불렸다(정확히 1회여야 한다)"
        assert host.findChild(QLabel, "_pageRecolorOverlay") is None, "오버레이 잔존"
    finally:
        host.deleteLater()
        qapp.processEvents()


def test_crossfade_is_instant_when_motion_disabled(qapp, monkeypatch):
    """모션이 꺼져 있으면 오버레이를 만들지 않고 즉시 완료 — 잠금이 남지 않는다."""
    monkeypatch.setattr(motion, "enabled", lambda: False)
    host = QWidget()
    host.resize(300, 200)
    calls: list[int] = []
    try:
        motion.crossfade_from(host, host.grab(), on_done=lambda: calls.append(1))
        assert calls == [1]
        assert host.findChild(QLabel, "_pageRecolorOverlay") is None
    finally:
        host.deleteLater()
        qapp.processEvents()


def test_crossfade_survives_a_null_snapshot(qapp, motion_on):
    """스냅샷이 없어도 on_done 은 불린다 — 안 불리면 잠금이 영구히 남는다."""
    host = QWidget()
    calls: list[int] = []
    try:
        motion.crossfade_from(host, None, on_done=lambda: calls.append(1))
        assert calls == [1]
    finally:
        host.deleteLater()


# ── main_window 배선 ────────────────────────────────────────────────────
def test_transition_locks_then_releases(qapp, window, motion_on):
    w = window
    assert w._appearance_busy is False
    assert w._setup_page._dark_switch.isEnabled() is True

    _prefs.patch(color_mode="dark")
    w._on_appearance_changed()

    # 전환 중: 색은 이미 바뀌었고, 옛 화면이 위에 얹혀 있고, 토글은 잠겼다.
    assert theme.COLOR_MODE == "dark"
    assert w._appearance_busy is True
    assert _overlay(w) is not None, "크로스페이드 오버레이가 없다(색이 튄다)"
    assert w._setup_page._dark_switch.isEnabled() is False, "전환 중 토글이 살아 있다"

    _spin_recolor(qapp)
    assert w._appearance_busy is False, "잠금이 풀리지 않았다 — 다크 모드가 영구히 잠긴다"
    assert w._setup_page._dark_switch.isEnabled() is True
    assert _overlay(w) is None, "오버레이가 정리되지 않았다"


def test_rapid_toggling_is_ignored_during_transition(qapp, window, motion_on):
    """★ 전환 중 두 번째 요청은 **무시**된다(연타로 페이지 재생성이 겹치지 않게)."""
    w = window
    _prefs.patch(color_mode="dark")
    w._on_appearance_changed()
    first_page = w._setup_page

    _prefs.patch(color_mode="light")          # 전환 중 반대로 눌렀다고 가정
    w._on_appearance_changed()
    assert theme.COLOR_MODE == "dark", "전환 중 요청이 색을 또 바꿨다"
    assert w._setup_page is first_page, "전환 중 페이지가 다시 만들어졌다"

    _spin_recolor(qapp)
    # 끝난 뒤에는 정상적으로 다시 전환된다(잠금이 일회성이어야 한다).
    w._on_appearance_changed()
    assert theme.COLOR_MODE == "light"
    _spin_recolor(qapp)
    assert w._appearance_busy is False


def test_lock_is_released_even_when_recreate_fails(qapp, window, motion_on,
                                                   monkeypatch):
    """예외 경로에서도 잠금이 풀린다 — 잠긴 채 남는 것이 원래 버그보다 나쁘다."""
    w = window

    def _boom(self, *a, **k):          # 호출부가 apply_qss=… 를 넘긴다
        raise RuntimeError("의도된 실패")

    monkeypatch.setattr(mw.MainWindow, "_recreate_pages", _boom)
    _prefs.patch(color_mode="dark")
    with pytest.raises(RuntimeError):
        w._on_appearance_changed()
    assert w._appearance_busy is False, "예외 뒤 잠금이 남았다"


def test_toggle_lets_the_knob_move_before_the_heavy_work(qapp, window, motion_on):
    """★ 토글 슬롯 안에서 **곧바로** 전환을 시작하지 않는다.

    사용자 보고: "누르자마자 전환이 시작되어야 하는데 반응시간이 느림".  전환 작업은
    메인 스레드를 ~140ms 잡는다(실측: apply_to_app 55~83 + 페이지 재생성 45~85 + 스냅샷
    5~8).  그 일을 `toggled` 슬롯 안에서 동기로 하면, 방금 시작한 손잡이 이동
    애니메이션이 **한 프레임도 그려지지 못하고** 얼어 누름이 씹힌 것처럼 보인다.

    그래서 손잡이 이동(DUR_SWITCH)이 끝난 뒤에 emit 한다 — 이 테스트는 '즉시 emit 하지
    않는다'와 '조금 뒤 반드시 emit 한다'를 함께 고정한다(뒤쪽이 없으면 전환이 아예
    안 되는 것도 통과한다)."""
    page = window._setup_page
    seen: list[str] = []
    page.appearance_changed.connect(seen.append)
    page._dark_switch.switch._toggle()          # 실제 클릭과 같은 경로
    assert seen == [], "토글 슬롯에서 즉시 emit 했다(손잡이가 얼어붙는다)"
    _spin(qapp, max(60, motion.dur(motion.DUR_SWITCH) + 120))
    assert seen == ["dark"], f"지연 emit 이 오지 않았다: {seen}"
    _spin_recolor(qapp)


def test_rapid_double_toggle_coalesces_into_one_transition(qapp, window, motion_on):
    """지연 구간의 연타는 **하나로 합쳐진다** — prefs 와 화면이 어긋나지 않게.

    옛 동작에서는 두 번째 누름이 prefs 만 바꾸고 `_appearance_busy` 에 막혀 화면은 그대로
    였다(저장된 색 ≠ 보이는 색).  지연 타이머가 재시작되므로 emit 은 한 번, 값은 마지막
    누름의 것이 된다."""
    page = window._setup_page
    seen: list[str] = []
    page.appearance_changed.connect(seen.append)
    sw = page._dark_switch.switch
    sw._toggle()                                 # light → dark
    sw._toggle()                                 # 곧바로 되돌림 → dark → light
    _spin(qapp, max(80, motion.dur(motion.DUR_SWITCH) + 150))
    assert seen == [], "제자리로 돌아온 연타인데 전환을 요청했다"
    assert theme.COLOR_MODE == "light"
    assert _prefs.load().color_mode == "light", "prefs 가 보이는 색과 어긋났다"


def test_transition_does_not_read_prefs_from_disk(qapp, window, motion_on):
    """색 모드는 **시그널로 실려 온다** — 전환 경로에서 prefs 를 다시 읽지 않는다.

    누름 → 저장 → 다시 읽기는 디스크 왕복 2회다.  회사 환경에서 prefs 가 네트워크 홈에
    있으면 그 한 번이 전환 지연에 그대로 더해진다.

    ※ 재는 것은 '전환 중 prefs 를 한 번도 안 읽는가'가 **아니다** — 새 페이지들은 원래
    자기 상태를 prefs 에서 복원한다(그건 이 전환의 비용이 아니라 페이지 생성 비용이다).
    재는 것은 **색 모드의 출처**다: 인자가 곧 진실이어야 하고, 없을 때만 prefs 를 본다."""
    # 디스크가 반대 값을 말해도 **인자**가 이긴다 = 색 모드를 위해 디스크를 읽지 않는다.
    _prefs.patch(color_mode="light")
    window._on_appearance_changed("dark")
    assert theme.COLOR_MODE == "dark", "인자로 실어 보낸 색 모드가 무시됐다"
    _spin_recolor(qapp)

    # 인자가 없으면(옛 연결·직접 호출) 예전처럼 prefs 에서 읽는 폴백이 살아 있어야 한다.
    _prefs.patch(color_mode="light")
    window._on_appearance_changed()
    assert theme.COLOR_MODE == "light", "인자 없는 호출의 prefs 폴백이 사라졌다"
    _spin_recolor(qapp)


def test_recolor_is_slow_enough_to_read(qapp):
    """사용자 요청: 색 전환을 **더 느리게**.  220ms 는 '튀는' 쪽에 가까웠다."""
    assert motion.DUR_RECOLOR >= 400, f"DUR_RECOLOR={motion.DUR_RECOLOR} — 너무 빠르다"
    # 손잡이 이동보다 충분히 길어야 두 모션이 '하나의 흐름'으로 읽힌다.
    assert motion.DUR_RECOLOR > motion.DUR_SWITCH * 2


def test_recolor_does_not_slide(qapp):
    """색만 바뀌는 전환에 위치 이동을 섞지 않는다 — '화면이 옮겨졌다'는 거짓 신호다."""
    src = inspect.getsource(motion.crossfade_from)
    assert "move(" not in src, "크로스페이드가 슬라이드를 한다"
    assert "EASE_SOFT" in src        # 색 전환은 더 완만한 감속(사용자 지정)
