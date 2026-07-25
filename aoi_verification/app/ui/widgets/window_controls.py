"""창 제어(window control) 헬퍼 (#9).

★ **적용 대상은 이제 메인 창 하나다.**  모든 팝업이 별도 OS 창이 아니라 창 안의
시트로 뜨므로(``widgets/sheet_host.py``), 다이얼로그에 이 헬퍼를 붙이면 안 된다 —
자식 위젯에는 타이틀바가 없어 최소화/최대화 힌트가 아무 일도 하지 않고, F11 은
'전체화면'을 약속하면서 실제로는 아무 변화도 만들지 못한다(실측: 자식 위젯에
``showFullScreen()`` 을 걸면 ``isFullScreen()`` 만 True 가 되고 기하는 그대로다).
지키지 못할 약속을 하는 단축키는 없는 것이 낫다.

- ``enable_window_controls`` — 창 플래그에 최소화/최대화/닫기 힌트를 추가한다.
  ``setWindowFlags`` 를 show 이후에 호출하면 창이 숨겨질 수 있으므로 **첫 show 이전**
  (보통 ``__init__``)에 호출할 것.
- ``add_fullscreen_shortcut`` — F11 전체화면 토글.  뷰어가 창 안 시트가 된 뒤로는
  **이것이 '사진을 화면 가득 보는' 유일한 경로**다(옛 뷰어별 F11 의 대체).
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
    """F11 로 전체화면/일반화면을 토글하는 단축키를 위젯에 붙인다(**창에만**).

    ★ 되돌릴 때 **들어올 때의 상태로** 돌아간다.  그냥 ``showNormal()`` 하면 최대화로
    쓰던 사람이 F11 을 두 번 눌렀을 때 작은 창으로 떨어진다 — 토글은 원래 자리로
    돌아와야 토글이다."""
    state = {"maximized": False}

    def _toggle() -> None:
        try:
            if widget.isFullScreen():
                if state["maximized"]:
                    widget.showMaximized()
                else:
                    widget.showNormal()
            else:
                state["maximized"] = bool(widget.isMaximized())
                widget.showFullScreen()
        except Exception:
            pass

    sc = QShortcut(QKeySequence("F11"), widget)
    sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
    sc.activated.connect(_toggle)
    return sc
