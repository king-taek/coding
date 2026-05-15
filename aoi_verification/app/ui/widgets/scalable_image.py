"""크기 조절 가능한 이미지 위젯.

QScrollArea 안에 넣어서 사용한다.  외부 슬라이더에서 set_target_size() 로
표시 크기를 조절할 수 있고, 잘림 없이 항상 비율 유지.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QLabel

from ...utils import image_io


class ScalableImage(QLabel):
    """원본(=mid 캐시) 픽스맵을 보존하면서 슬라이더 값으로 크기를 조절."""

    DEFAULT_LONG_EDGE = 400     # 최대 700 / 최소 300 / 기본 400
    MIN_LONG_EDGE = 300
    MAX_LONG_EDGE = 700

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pix_orig: Optional[QPixmap] = None
        self._target_long_edge = self.DEFAULT_LONG_EDGE
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "background: #050810; border: 1px solid #1F2A3F; border-radius: 8px;"
        )
        self.setMinimumSize(QSize(self.MIN_LONG_EDGE, self.MIN_LONG_EDGE))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_image(self, path: Path) -> None:
        try:
            mid = image_io.get_mid_path(path)
            pix = QPixmap(str(mid))
        except Exception:
            pix = QPixmap(800, 800)
            pix.fill(QColor(8, 16, 32))
        if pix.isNull():
            pix = QPixmap(800, 800)
            pix.fill(QColor(8, 16, 32))
        self._pix_orig = pix
        self._rescale()

    def clear_image(self) -> None:
        self._pix_orig = None
        self.clear()
        self.setMinimumSize(QSize(self.MIN_LONG_EDGE, self.MIN_LONG_EDGE))

    def set_target_size(self, long_edge: int) -> None:
        long_edge = max(self.MIN_LONG_EDGE, min(self.MAX_LONG_EDGE, long_edge))
        if long_edge == self._target_long_edge:
            return
        self._target_long_edge = long_edge
        self._rescale()

    def target_size(self) -> int:
        return self._target_long_edge

    # ------------------------------------------------------------------
    def _rescale(self) -> None:
        if self._pix_orig is None or self._pix_orig.isNull():
            return
        scaled = self._pix_orig.scaled(
            self._target_long_edge, self._target_long_edge,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)
        # 라벨의 고정 크기를 픽스맵에 맞춰서 QScrollArea 가 정확히 스크롤 영역을 계산하도록
        self.setFixedSize(scaled.size())
