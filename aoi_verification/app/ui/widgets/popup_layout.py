"""팝업 버튼 줄 — 시트와 대화상자가 같은 함수로 버튼을 놓는다(현택님 2026-11-24 결정).

- **메시지 시트**(ask · info · choose): Windows 순서 — 확인이 먼저, 취소·닫기가 맨
  오른쪽(P1·P2 B안).
- **대화상자**(저장 이름 · Notch · 슬롯 선택 · 다중 선택 · 슬롯 짝 등): 오른쪽 아래
  [취소][확인](P3–P6 A안).  보조 도구(전체 선택 등)는 맨 왼쪽.

★ 두 순서가 서로 반대인 것은 **현택님이 일부러 고른 것**이다 — 한쪽에 맞춰 '통일' 하지
마라.  버튼 높이는 모두 ``control_h_lg``(44)다.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout

from .. import theme

__all__ = ["CANCEL_KEYS", "sheet_row", "dialog_row"]

# choose() 선택지 중 '물러나기' 인 키 — 시트에서 맨 오른쪽에 놓는다.
CANCEL_KEYS = frozenset({"close", "back", "cancel", "later"})


def _add(bar: QHBoxLayout, btn) -> None:
    btn.setFixedHeight(theme.PROFILE.control_h_lg)
    bar.addWidget(btn)


def _prep(bar: QHBoxLayout) -> None:
    bar.setContentsMargins(0, 0, 0, 0)
    bar.setSpacing(8)


def sheet_row(bar: QHBoxLayout, *, primaries=(), others=(),
              cancels=()) -> QHBoxLayout:
    """메시지 시트 — 오른쪽 정렬, [확인][그 밖][취소·닫기] 순서."""
    _prep(bar)
    bar.addStretch(1)
    for b in (*primaries, *others, *cancels):
        _add(bar, b)
    return bar


def dialog_row(bar: QHBoxLayout, *, left=(), cancels=(), others=(),
               primaries=()) -> QHBoxLayout:
    """대화상자 — 왼쪽에 보조 도구, 오른쪽 아래에 [취소][그 밖][확인]."""
    _prep(bar)
    for w in left:
        _add(bar, w)
    bar.addStretch(1)
    for b in (*cancels, *others, *primaries):
        _add(bar, b)
    return bar
