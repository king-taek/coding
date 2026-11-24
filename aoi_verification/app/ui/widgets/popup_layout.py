"""팝업 버튼 줄 — 시트와 대화상자가 같은 함수로 버튼을 놓는다(현택님 2026-11-24 결정).

- **모든 팝업**(메시지 시트 ask · info · choose 와 대화상자 저장 이름 · Notch · 슬롯 선택 ·
  다중 선택 · 슬롯 짝 등): 오른쪽 아래 [취소][그 밖][확인] — 확인이 맨 오른쪽.
  대화상자의 보조 도구(전체 선택 등)는 맨 왼쪽.
  (현택님 2026-11-25 Q1 B안: 작은 알림 창만 [확인][취소] 이던 것을 큰 창과 같게.)

버튼 높이는 모두 ``control_h_lg``(44)다.
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
    """메시지 시트 — 오른쪽 정렬, [취소·닫기][그 밖][확인] 순서(대화상자와 같다)."""
    _prep(bar)
    bar.addStretch(1)
    for b in (*cancels, *others, *primaries):
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
