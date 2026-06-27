"""마우스 휠로 값이 바뀌지 않는 슬라이더.

드래그/클릭/키보드 입력은 정상 동작하고, 휠 이벤트만 무시한다.  스크롤 영역
안에 두어도 휠은 슬라이더가 아니라 스크롤로 전달된다.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QSlider


class NoWheelSlider(QSlider):
    """휠 이벤트를 무시하는 ``QSlider``."""

    def wheelEvent(self, event):  # noqa: N802
        event.ignore()
