"""Qt 애니메이션 수명 — **삭제된 객체의 핸들을 들고 있으면 프로세스가 죽는다.**

실사용에서 강제종료 2건이 났고 원인은 하나였다:

    QVariantAnimation.start(DeletionPolicy.DeleteWhenStopped)

로 시작한 애니메이션은 자연 종료 시 C++ 객체가 삭제되는데, 파이썬 쪽 ``self._anim`` 은
여전히 그 껍데기를 들고 있다.  **두 번째** 호출에서 ``anim.stop()`` 이

    RuntimeError: wrapped C/C++ object of type QVariantAnimation has been deleted

를 내고, PyQt6 는 슬롯 안의 미처리 예외를 ``qFatal()`` 로 처리하므로 **앱이 그대로
죽는다**(파이썬 traceback 만 남고 종료).  재현된 두 경로:

- ``ToggleSwitch`` 두 번째 토글        → 구형 모드 on/off 시 강제종료
- ``LoadingOverlay`` 두 번째 show_overlay → 실패목록 두 번째 클릭 시 강제종료

★ 이 테스트는 **모션을 켜야** 재현된다.  ``motion.enabled()`` 는 offscreen 에서 False 를
돌려주고, False 면 애니메이션 경로를 아예 타지 않는다 — 모션을 끄고 짠 테스트는 통과하면서
버그를 놓친다.
"""

from __future__ import annotations

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QElapsedTimer                        # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget             # noqa: E402

from aoi_verification.app.ui import motion                    # noqa: E402
from aoi_verification.app.ui.widgets.loading_overlay import (  # noqa: E402
    LoadingOverlay)
from aoi_verification.app.ui.widgets.switch_row import (       # noqa: E402
    SwitchRow, ToggleSwitch)

_UI_DIR = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def motion_on(monkeypatch):
    """모션을 강제로 켠다 — 이게 없으면 애니메이션 경로를 타지 않아 재현이 안 된다."""
    monkeypatch.setattr(motion, "enabled", lambda: True)
    yield


def _spin(qapp, ms: int) -> None:
    """실제 시간을 ms 만큼 흘리며 이벤트 루프를 돈다(애니메이션이 끝나게)."""
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        qapp.processEvents()


# ── 재현 1: 구형 모드 스위치를 두 번 토글 ────────────────────────────────
def test_toggle_switch_survives_second_toggle(qapp, motion_on):
    sw = ToggleSwitch(False)
    sw.resize(48, 28)
    try:
        sw._toggle()                       # 1회 — 애니메이션 시작
        _spin(qapp, 400)                   # dur(140) 을 충분히 넘겨 자연 종료
        sw._toggle()                       # 2회 — 여기서 죽었다
        _spin(qapp, 200)
        assert sw.is_on() is False          # off → on → off
    finally:
        sw.deleteLater()


def test_switch_row_survives_repeated_toggles(qapp, motion_on):
    """구형 모드 카드가 실제로 쓰는 형태(SwitchRow)로도 확인."""
    row = SwitchRow("구형(유사도) 모드", description="설명", checked=False)
    seen: list[bool] = []
    row.toggled.connect(seen.append)
    try:
        for _ in range(4):
            row.switch._toggle()
            _spin(qapp, 300)
        assert seen == [True, False, True, False]
    finally:
        row.deleteLater()


# ── 재현 2: 로딩 오버레이를 두 번 표시 ──────────────────────────────────
def test_loading_overlay_survives_second_show(qapp, motion_on):
    host = QWidget()
    host.resize(900, 600)
    host.show()
    ov = LoadingOverlay(host)
    try:
        ov.show_overlay("점수 계산 중")
        # BAR_STAGGER_MS(210) + BAR_SLIDE_MS(160) 을 넘겨 바 애니메이션이 자연 종료되게.
        _spin(qapp, 700)
        ov.show_overlay("점수 계산 중")     # 실패목록 두 번째 클릭 = 여기서 죽었다
        _spin(qapp, 200)
        assert ov.isVisible()
    finally:
        ov.deleteLater()
        host.deleteLater()


# ── 패턴 가드: 삭제를 Qt 에 맡긴 애니메이션의 핸들을 보관하지 않는다 ──────
def test_no_stored_handle_to_self_deleting_animation():
    """``DeleteWhenStopped`` 로 시작하면서 그 객체를 ``self.…`` 에 보관하는 코드 금지.

    QSS 선택자 순서 가드와 같은 종류의 소스 테스트다 — 이 조합이 위 두 크래시의
    **유일한** 원인이었으므로, 문장 하나로 재발을 막는다.
    """
    offenders: list[str] = []
    for path in sorted(_UI_DIR.rglob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            m = re.search(r"(\w+)\.start\(\s*[\w.]*DeletionPolicy\.DeleteWhenStopped",
                          line)
            if not m:
                continue
            var = m.group(1)
            # 같은 함수 안에서 그 지역 변수를 self 멤버로 붙들어 두는지 — 앞뒤로 본다.
            window = lines[max(0, i - 12):i + 6]
            if any(re.search(rf"self\.\w+\s*=\s*{re.escape(var)}\s*$", w)
                   for w in window):
                rel = path.relative_to(_UI_DIR.parents[2])
                offenders.append(f"{rel}:{i + 1}")
    assert not offenders, (
        "DeleteWhenStopped 로 시작한 애니메이션을 self 에 보관했다 — 자연 종료 후 "
        "stop() 이 RuntimeError 를 내고 PyQt6 가 프로세스를 종료시킨다. "
        "__init__ 에서 만든 애니메이션을 재사용하라: " + ", ".join(offenders)
    )
