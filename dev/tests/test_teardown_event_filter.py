"""파괴 도중의 이벤트 필터가 프로세스를 죽이지 않는다 — 간헐 abort 의 회귀 가드.

★ 실측(core dump): GC 가 '창 ↔ 그 창에 필터를 건 `SheetHost`' 순환을 치우며 먼저 파이썬
  속성을 비우고 창을 지웠다.  `~QWidget` 이 자식을 떼며 이벤트를 필터에 통과시키자
  `self._stack` 이 없어 `AttributeError` → PyQt6 가 abort.  어느 테스트 도중이든 GC 가
  도는 순간 터져 '엉뚱한 테스트의 워커가 죽는다' 로 보였다.  앱에서는 종료 때 같은 경로다.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

_ROOT = Path(__file__).resolve().parents[2]
_APP = _ROOT / "aoi_verification" / "app"

# GC 의 tp_clear 를 흉내 낸다: 파이썬 속성을 비운 뒤 C++ 창을 지운다.
_SCRIPT = r"""
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, sys.argv[1])
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget
from PyQt6 import sip
from aoi_verification.app.ui.widgets.sheet_host import SheetHost
app = QApplication([])
host = QMainWindow()
host.setCentralWidget(QWidget())
sh = SheetHost(host)
host.show()
app.processEvents()
sh.__dict__.clear()
sip.delete(host)
print("survived")
"""


def test_window_teardown_with_a_half_cleared_sheet_host_does_not_abort():
    r = subprocess.run([sys.executable, "-c", _SCRIPT, str(_ROOT)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "survived" in r.stdout, (
        f"rc={r.returncode}\n{r.stderr[-1500:]}")


def test_every_event_filter_tolerates_teardown():
    """새 `eventFilter` 도 같은 보호를 받는다 — 빠뜨리면 그 위젯이 다음 abort 지점이다."""
    bad = []
    for p in _APP.rglob("*.py"):
        lines = p.read_text(encoding="utf-8").splitlines()
        for i, ln in enumerate(lines):
            if re.match(r"\s*def eventFilter\(", ln):
                if i == 0 or "@tolerate_teardown" not in lines[i - 1]:
                    bad.append(f"{p.relative_to(_APP)}:{i + 1}")
    assert bad == [], f"@tolerate_teardown 없는 eventFilter: {bad}"


def test_the_guard_only_absorbs_errors_of_a_cleared_object():
    """빈 `__dict__` 라고 미리 건너뛰지 않는다(속성 없는 필터가 꺼진다) · 산 객체의 버그는 올라간다."""
    from aoi_verification.app.ui.teardown import tolerate_teardown

    class _NoAttrs:
        @tolerate_teardown
        def eventFilter(self, obj, event):      # noqa: N802
            return True                         # 속성 없이도 거른다(`motion._InputSwallow`)

    class _Buggy:
        def __init__(self):
            self.alive = True

        @tolerate_teardown
        def eventFilter(self, obj, event):      # noqa: N802
            return self.missing

    assert _NoAttrs().eventFilter(None, None) is True
    with pytest.raises(AttributeError):
        _Buggy().eventFilter(None, None)
    torn = _Buggy()
    torn.__dict__.clear()                       # GC 의 tp_clear 흉내
    assert torn.eventFilter(None, None) is False
