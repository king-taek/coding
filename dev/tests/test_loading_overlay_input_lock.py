"""로딩 오버레이가 떠 있는 동안 **키보드도 막히는가**.

배경 — 실제로 결과를 틀어지게 했던 결함이다.  오버레이는 위젯이라 마우스는 이미
막고 있었지만 **키는 그대로 통과**했다.  그래서 '자동 매치 진행 중…' 진행 바가 도는
동안 사용자가 ``N`` 을 누르면 그때 처리 중이던 기준 사진이 조용히 '매치 없음' 으로
확정되고, **엑셀에 사실이 아닌 미탐**이 남았다.  화면이 어두워 아무것도 안 눌린다고
믿는 상태였으므로 틀어진 줄도 몰랐다.

★ 이 파일의 핵심은 ``QShortcut`` 이다.  ``KeyPress``·``ShortcutOverride`` 를 잡아먹어도
  ``QShortcut`` 은 **그대로 발화한다** — 셋 중 ``QEvent.Type.Shortcut`` 만 실제로 막는다.
  그래서 '키를 막았다' 는 것을 ``keyPressEvent`` 로만 확인하면 **막히지 않은 채로도
  통과하는 테스트**가 된다.  여기서는 앱이 실제로 쓰는 방식(QShortcut)으로 검증한다.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt                                    # noqa: E402
from PyQt6.QtGui import QKeySequence, QShortcut                # noqa: E402
from PyQt6.QtTest import QTest                                 # noqa: E402
from PyQt6.QtWidgets import QPushButton, QWidget               # noqa: E402

from aoi_verification.app.ui.widgets.loading_overlay import (   # noqa: E402
    LoadingOverlay,
)


class _Host(QWidget):
    """단축키를 가진 페이지 — Stage 2 의 ``N``/``Ctrl+Z`` 와 같은 배치."""

    def __init__(self) -> None:
        super().__init__()
        self.resize(600, 400)
        self.fired: list[str] = []
        # 앱과 같게 기본 컨텍스트(WindowShortcut)를 쓴다 — 좁히면 포커스가 없을 때
        # 조용히 죽어서, 정작 막혔는지 확인할 수 없다(match_page 주석과 같은 이유).
        QShortcut(QKeySequence("N"), self, activated=lambda: self.fired.append("N"))
        QShortcut(QKeySequence("Ctrl+Z"), self,
                  activated=lambda: self.fired.append("Ctrl+Z"))


@pytest.fixture()
def host(qapp):
    w = _Host()
    ov = LoadingOverlay(w)
    w.show()
    QTest.qWaitForWindowExposed(w)
    yield w, ov
    ov.hide_overlay()
    w.close()


def _press_both(app, w: _Host) -> list[str]:
    w.fired.clear()
    QTest.keyClick(w, Qt.Key.Key_N)
    QTest.keyClick(w, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    return list(w.fired)


def test_shortcuts_work_without_overlay(qapp, host):
    """대조군 — 오버레이가 없으면 단축키는 정상 동작해야 한다.

    이게 없으면 '아무것도 발화 안 함' 이 잠금 때문인지 애초에 배선이 안 된 탓인지
    구분할 수 없다."""
    w, _ov = host
    assert _press_both(qapp, w) == ["N", "Ctrl+Z"]


def test_overlay_blocks_shortcuts(qapp, host):
    """오버레이가 떠 있으면 뒤 화면의 단축키는 **하나도** 발화하지 않는다."""
    w, ov = host
    ov.show_overlay("자동 매치 진행 중… 3 / 24")
    qapp.processEvents()
    assert ov.isVisible()
    assert _press_both(qapp, w) == []


def test_lock_released_only_when_screen_actually_brightens(qapp, host):
    """잠금은 **오버레이가 실제로 사라진 뒤** 풀린다.

    최소 표시 래치·페이드아웃 동안 화면은 아직 어둡다.  거기서 미리 풀면 '어두운데
    눌리는' 구간이 그대로 남는다 — 고치려던 그 증상이다."""
    w, ov = host
    ov.show_overlay("폴더 스캔 중…")
    qapp.processEvents()
    ov.hide_overlay()                     # 래치/페이드가 남아 있을 수 있다
    if ov.isVisible():
        assert _press_both(qapp, w) == [], "아직 어두운데 키가 통과했습니다"
    # 실제로 사라질 때까지 기다린 뒤에는 다시 동작해야 한다(잠금이 새지 않는다).
    for _ in range(80):
        if not ov.isVisible():
            break
        QTest.qWait(25)
    assert not ov.isVisible(), "오버레이가 끝내 내려가지 않았습니다"
    assert _press_both(qapp, w) == ["N", "Ctrl+Z"]


def test_lock_never_outlives_the_overlay(qapp, host):
    """오버레이가 **정상 경로를 거치지 않고** 숨겨져도 잠금은 반드시 풀린다.

    ★ 이건 실제로 터졌다.  잠금 해제를 `_finish_hide` 에만 걸었더니, 그 경로를 타지
      않고 숨은 오버레이의 전역 필터가 살아남아 **앱 전체가 키보드를 영영 못 받았다.**
      전역 필터를 다는 코드에는 반드시 이 안전판이 함께 있어야 한다."""
    w, ov = host
    ov.show_overlay("썸네일 생성 중…")
    qapp.processEvents()
    assert _press_both(qapp, w) == []          # 잠긴 상태 확인
    ov.hide()                                  # `_finish_hide` 를 건너뛴 숨김
    qapp.processEvents()
    assert _press_both(qapp, w) == ["N", "Ctrl+Z"], "오버레이가 사라졌는데 잠금이 남았습니다"


def test_cancel_button_stays_reachable(qapp, host):
    """‘중지’ 는 오버레이 자신의 것이라 막으면 안 된다 — 막으면 취소가 불가능해진다."""
    w, ov = host
    ov.show_overlay("유사도 계산 중…", cancelable=True)
    qapp.processEvents()
    seen: list[int] = []
    ov.cancel_requested.connect(lambda: seen.append(1))
    btn = ov.findChild(QPushButton)          # 오버레이 안의 유일한 버튼 = 중지
    assert btn is not None and btn.isVisible()
    btn.setFocus()
    qapp.processEvents()
    QTest.keyClick(btn, Qt.Key.Key_Space)           # 키보드로 누르기
    qapp.processEvents()
    assert seen == [1], "오버레이 안으로 가는 키까지 막혔습니다"
