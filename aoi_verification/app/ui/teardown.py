"""파괴 도중의 파이썬 오버라이드 보호.

★ GC 가 순환 참조(예: 창 ↔ 그 창에 이벤트 필터를 건 `SheetHost`)를 치울 때는 먼저 파이썬
  쪽 속성을 비우고(``__dict__`` 가 빈다) 그다음 C++ 객체를 지운다.  그런데 ``~QWidget`` 은
  자식을 떼어 내며 이벤트를 **필터에 통과시킨다** — 속성이 이미 사라진 필터가
  ``self._stack`` 을 읽다가 ``AttributeError`` 를 내고, PyQt6 는 슬롯·오버라이드의 미처리
  예외에서 **프로세스를 끝낸다**(실측: core dump — ``sipWrapper_clear`` →
  ``~QMainWindow`` → ``sendThroughObjectEventFilters`` → ``pyqt6_err_print`` → abort).
  앱에서는 종료 때 같은 경로를 탄다.
"""

from __future__ import annotations

import functools


def tolerate_teardown(method):
    """``eventFilter`` 용 — 파괴 도중(GC 가 속성을 비운 뒤)에 난 ``AttributeError`` 만 흡수한다.

    필터는 평소대로 돈다.  ``AttributeError`` 가 났고 **그때 ``__dict__`` 가 비어 있으면**
    'GC 가 비운 중' 이므로 거르지 않고 통과시킨다.  ★ 빈 ``__dict__`` 만으로 미리 건너뛰면
    안 된다 — 속성이 아예 없는 필터(`motion._InputSwallow`)는 평소에도 비어 있어 키 차단이
    통째로 꺼졌다(실측).  살아 있는 객체의 ``AttributeError`` 는 그대로 올라간다."""
    @functools.wraps(method)
    def wrapper(self, obj, event):
        try:
            return method(self, obj, event)
        except AttributeError:
            if not self.__dict__:
                return False
            raise
    return wrapper
