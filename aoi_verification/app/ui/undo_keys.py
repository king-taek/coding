"""되돌리기 키 — 앱 어디서나 **Z** 다(Ctrl+Z 도 그대로 받는다).  현택님 결정 2026-11-24.

버튼 라벨도 같이 "되돌리기 (Z)" 로 적는다(``i18n.KO.BTN_UNDO`` 등).

★ 글자 입력칸(QLineEdit·스핀박스·텍스트 편집)에 포커스가 있으면 Qt 가 그 칸에 먼저
  ``ShortcutOverride`` 를 묻고, 칸이 글자를 받겠다고 하면 단축키는 발화하지 않는다 —
  값을 치다가 'z' 한 글자에 결정이 되돌려지지 않는다(``dev/tests/test_undo_keys.py``).
★ 단축키는 ``owner`` 가 **보일 때만** 산다(Qt 의 창 단축키 규칙).  되돌릴 것이 있을 때만
  보이는 위젯(알림 줄의 [되돌리기] 버튼)에 걸면 '되돌릴 것 없음' 을 따로 막지 않아도 된다.
"""
from __future__ import annotations

from PyQt6.QtGui import QKeySequence, QShortcut


def bind_undo(owner, fn) -> list[QShortcut]:
    """``owner`` 에 Z · Ctrl+Z 를 건다.  누르고 있어도 한 번만(자동 반복 끔)."""
    out = []
    for seq in (QKeySequence("Z"), QKeySequence(QKeySequence.StandardKey.Undo)):
        sc = QShortcut(seq, owner, activated=fn)
        sc.setAutoRepeat(False)
        out.append(sc)
    return out
