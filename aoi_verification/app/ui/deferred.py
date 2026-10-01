"""주인과 함께 죽는 지연 호출.

★ ``QTimer.singleShot(ms, lambda: self.x...)`` 를 쓰지 마라.  정적 singleShot 의 람다는
  **아무 객체에도 묶이지 않아**, 그 사이 위젯이 지워지면 죽은 C++ 객체를 만져
  ``RuntimeError`` 가 나고 PyQt6 는 슬롯의 미처리 예외에서 **프로세스를 통째로 끝낸다**
  (실측: 테스트 워커가 abort·세그폴트로 죽었다).  바운드 메서드
  (``singleShot(0, self._load)``)는 PyQt 가 그 객체를 수신자로 삼아 안전하다 — 람다·
  지역 변수가 필요할 때만 이걸 쓴다.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QObject, QTimer


def call_later(owner: QObject, ms: int, fn: Callable[[], object]) -> None:
    """``ms`` 뒤 ``fn`` 을 부른다 — ``owner`` 가 먼저 지워지면 **조용히 취소**된다.

    타이머를 ``owner`` 의 자식으로 만들어 주인과 함께 파괴되게 하고, 발화 뒤에는 스스로
    지운다(연속 호출해도 자식이 쌓이지 않는다)."""
    t = QTimer(owner)
    t.setSingleShot(True)
    t.timeout.connect(fn)
    t.timeout.connect(t.deleteLater)
    t.start(ms)
