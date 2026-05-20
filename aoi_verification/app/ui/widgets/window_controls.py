"""다이얼로그 창 제어(window control) 헬퍼 (#9).

일부 플랫폼에서 ``QDialog`` 는 닫기 버튼만 보이고 최소화/최대화 버튼이 없다.
``enable_window_controls`` 가 창 플래그에 최소화/최대화/닫기 힌트를 추가한다.
또한 ``add_fullscreen_shortcut`` 으로 F11 전체화면 토글을 붙일 수 있다.

주의: ``setWindowFlags`` 를 show 이후에 호출하면 창이 숨겨질 수 있으므로,
반드시 위젯의 ``__init__`` 안 (첫 show 이전) 에서 호출해야 한다.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut


def enable_window_controls(w, *, maximized: bool = True) -> None:
    """위젯의 타이틀바에 최소화/최대화/닫기 버튼을 노출하고, 기본적으로 창을
    최대화(전체창)로 띄운다 (#13).

    반드시 첫 show 이전 (보통 ``__init__``) 에 호출할 것 — show 이후 플래그를
    바꾸면 창이 사라질 수 있다.  ``maximized=True`` 면 이벤트 루프 진입 직후
    (exec/show 이후) ``showMaximized`` 가 적용되도록 예약한다.
    """
    w.setWindowFlags(
        w.windowFlags()
        | Qt.WindowType.WindowMinimizeButtonHint
        | Qt.WindowType.WindowMaximizeButtonHint
        | Qt.WindowType.WindowCloseButtonHint
    )
    if maximized:
        # exec()/show() 가 위젯을 띄운 직후 최대화 — singleShot(0) 은 이벤트
        # 루프 첫 tick 에 실행되므로 안전.
        QTimer.singleShot(0, w.showMaximized)


def add_fullscreen_shortcut(widget) -> QShortcut:
    """F11 로 전체화면/일반화면을 토글하는 단축키를 위젯에 붙인다."""

    def _toggle() -> None:
        try:
            if widget.isFullScreen():
                widget.showNormal()
            else:
                widget.showFullScreen()
        except Exception:
            pass

    sc = QShortcut(QKeySequence("F11"), widget)
    sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
    sc.activated.connect(_toggle)
    return sc
