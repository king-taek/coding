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

    DEFAULT_LONG_EDGE = 400
    MIN_LONG_EDGE = 250
    MAX_LONG_EDGE = 700

    @staticmethod
    def auto_fit_long_edge() -> int:
        """현재 모니터 크기에 맞춰 적절한 시작값 — 화면 짧은 변의 약 절반."""
        try:
            from PyQt6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                geo = screen.availableGeometry()
                short_edge = min(geo.width(), geo.height())
                return max(ScalableImage.MIN_LONG_EDGE,
                           min(ScalableImage.MAX_LONG_EDGE,
                               int(short_edge * 0.5)))
        except Exception:
            pass
        return ScalableImage.DEFAULT_LONG_EDGE

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pix_orig: Optional[QPixmap] = None
        self._path: Optional[Path] = None
        # 가운데 확대 — 사진 가운데의 이 비율(가로·세로)만 잘라 크게 보인다.  1.0 = 전체.
        self._crop = 1.0
        self._target_long_edge = self.DEFAULT_LONG_EDGE
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 색은 QSS 가 준다(role="imagePlate") — 인스턴스 스타일시트로 굽지 않는다.
        self.setProperty("role", "imagePlate")
        self.setMinimumSize(QSize(self.MIN_LONG_EDGE, self.MIN_LONG_EDGE))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_center_crop(self, frac: float) -> None:
        """사진 가운데 ``frac``(0~1) 만 잘라 보인다 — 결함은 사진 정중앙에 있다.

        자른 부분은 **원본 사진**에서 읽는다(중간 화질 캐시를 자르면 30% 가 수백 px 라
        크게 볼 때 흐려진다).  원본을 못 읽으면 캐시를 자른다."""
        frac = 1.0 if not frac or frac >= 1.0 else max(0.05, float(frac))
        if frac == self._crop:
            return
        self._crop = frac
        if self._path is not None:
            self.set_image(self._path)

    def center_crop(self) -> float:
        return self._crop

    def set_image(self, path: Path) -> None:
        self._path = path
        if self._crop < 1.0:
            pix = self._load_center(path, self._crop)
            if pix is not None:
                self._pix_orig = pix
                self._rescale()
                return
        try:
            mid = image_io.get_mid_path(path)
            pix = QPixmap(str(mid))
            if self._crop < 1.0 and not pix.isNull():
                pix = pix.copy(self._center_rect(pix.width(), pix.height(), self._crop))
        except Exception:
            pix = QPixmap(800, 800)
            pix.fill(QColor(8, 16, 32))
        if pix.isNull():
            pix = QPixmap(800, 800)
            pix.fill(QColor(8, 16, 32))
        self._pix_orig = pix
        self._rescale()

    @staticmethod
    def _center_rect(w: int, h: int, frac: float):
        from PyQt6.QtCore import QRect
        cw, ch = max(1, round(w * frac)), max(1, round(h * frac))
        return QRect((w - cw) // 2, (h - ch) // 2, cw, ch)

    @classmethod
    def _load_center(cls, path: Path, frac: float) -> Optional[QPixmap]:
        """원본 사진의 가운데 ``frac`` 만 디코드한다(QImageReader 의 clip)."""
        from PyQt6.QtGui import QImageReader
        try:
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)        # 전체 보기(image_io)와 같은 EXIF 방향
            size = reader.size()
            if not size.isValid():
                return None
            reader.setClipRect(cls._center_rect(size.width(), size.height(), frac))
            img = reader.read()
            return None if img.isNull() else QPixmap.fromImage(img)
        except Exception:
            return None

    def clear_image(self) -> None:
        self._path = None
        self._pix_orig = None
        self.clear()
        self.setMinimumSize(QSize(self.MIN_LONG_EDGE, self.MIN_LONG_EDGE))

    def set_target_size(self, long_edge: int) -> None:
        long_edge = max(self.MIN_LONG_EDGE, min(self.MAX_LONG_EDGE, long_edge))
        if long_edge == self._target_long_edge:
            return
        self._target_long_edge = long_edge
        self._rescale()

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
