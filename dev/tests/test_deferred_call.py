"""지연 호출이 죽은 위젯을 만지지 않는다 — 간헐 세그폴트·abort 의 회귀 가드.

★ 실측 경위: 정적 ``QTimer.singleShot(0, lambda: self._scroll...)`` 의 람다가 페이지보다
  오래 살아, 페이지가 지워진 뒤 발화하며 ``RuntimeError`` → PyQt6 가 프로세스를 끝냈다.
  테스트의 ``deleteLater()`` 는 ``processEvents()`` 로는 처리되지 않아(아래 첫 테스트)
  삭제가 뒤의 중첩 이벤트 루프에서 한꺼번에 일어났고, 그래서 **엉뚱한 테스트에서
  가끔** 터졌다.  conftest 의 ``_flush_deferred_deletes`` 가 삭제를 제자리로 돌려놓았다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QCoreApplication, QEvent, QTimer  # noqa: E402
from PyQt6.QtWidgets import QLabel, QWidget                # noqa: E402

from aoi_verification.app.ui.deferred import call_later   # noqa: E402

_APP = Path(__file__).resolve().parents[2] / "aoi_verification" / "app"


def _flush_deletes() -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def test_process_events_alone_does_not_run_deferred_deletes(qapp):
    """conftest 픽스처가 존재하는 이유 — 이 전제가 깨지면(Qt 가 바뀌면) 픽스처를 다시 본다."""
    w = QWidget()
    gone: list = []
    w.destroyed.connect(lambda: gone.append(1))
    w.deleteLater()
    qapp.processEvents()
    assert gone == [], "processEvents 가 deleteLater 를 처리한다 — 픽스처 근거 재검토"
    _flush_deletes()
    assert gone == [1]


def test_call_later_runs_while_the_owner_lives(qapp):
    owner = QWidget()
    hits: list = []
    call_later(owner, 0, lambda: hits.append(1))
    for _ in range(3):
        qapp.processEvents()
    assert hits == [1]
    _flush_deletes()
    assert owner.findChildren(QTimer) == [], "발화한 타이머가 자식으로 쌓인다"
    owner.deleteLater()


def test_call_later_is_cancelled_with_its_owner(qapp):
    """주인이 먼저 지워지면 호출이 **사라진다** — 죽은 라벨을 만지지 않는다."""
    owner = QWidget()
    lbl = QLabel(owner)
    hits: list = []

    def _touch() -> None:
        hits.append(1)
        lbl.setText("x")            # 살아 있었다면 여기서 RuntimeError → abort

    call_later(owner, 0, _touch)
    owner.deleteLater()
    _flush_deletes()
    for _ in range(3):
        qapp.processEvents()
    assert hits == []


def test_no_static_single_shot_lambda_in_app():
    """정적 ``singleShot`` 에 람다를 넘기지 않는다 — 바운드 메서드나 ``call_later`` 를 쓴다."""
    pat = re.compile(r"singleShot\(\s*[^,()]+,\s*lambda", re.S)
    # 실제로 터졌던 모양(두 줄로 나뉜 호출)을 잡는지부터 — 안 잡으면 이 가드는 공허하다.
    assert pat.search("QTimer.singleShot(\n    0, lambda: self._scroll.x())")
    assert not pat.search("QTimer.singleShot(0, self._load)")
    bad = [f"{p.relative_to(_APP)}"
           for p in _APP.rglob("*.py")
           if p.name != "deferred.py"          # 독스트링이 금지 예시를 보여 준다
           and pat.search(re.sub(r"#[^\n]*", "", p.read_text(encoding="utf-8")))]
    assert bad == [], f"정적 singleShot 람다: {bad} — ui/deferred.call_later 를 쓴다"
