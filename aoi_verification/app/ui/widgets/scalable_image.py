"""크기 조절 가능한 이미지 위젯.

QScrollArea 안에 넣어서 사용한다.  외부 슬라이더에서 set_target_size() 로
표시 크기를 조절할 수 있고, 잘림 없이 항상 비율 유지.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import QLabel

from ...utils import image_io

#: 다음 사진 미리 읽기 — 한 줄짜리 작업자 하나면 된다(넘기는 순서대로 한 장씩).
#: QPixmap 은 메인 스레드 전용이라 여기서는 QImage 까지만 만든다.
_PREFETCH_POOL: Optional[ThreadPoolExecutor] = None


def _prefetch_pool() -> ThreadPoolExecutor:
    global _PREFETCH_POOL
    if _PREFETCH_POOL is None:
        _PREFETCH_POOL = ThreadPoolExecutor(max_workers=1,
                                            thread_name_prefix="img-prefetch")
    return _PREFETCH_POOL


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
        # (경로, 가운데 비율) → 백그라운드에서 읽는 중이거나 다 읽은 QImage.
        self._prefetched: dict[tuple[Path, float], Future] = {}
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
        img = None
        fut = self._prefetched.pop((path, self._crop), None)
        # ★ 이미 읽는 중이면 **기다린다** — 같은 사진을 두 번 읽기 시작하면 중간 화질
        #   캐시를 쓰는 도중의 파일을 읽어 빈 화면이 될 수 있다(`_ensure_resized` 는
        #   최종 경로에 바로 쓴다).  아직 시작 전이면 취소하고 여기서 읽는다.
        if fut is not None and not fut.cancel():
            try:
                img = fut.result()
            except Exception:
                img = None
        if img is None:
            img = self._decode(path, self._crop)
        if img is None:
            pix = QPixmap(800, 800)
            pix.fill(QColor(8, 16, 32))
        else:
            pix = QPixmap.fromImage(img)
        self._pix_orig = pix
        self._rescale()

    def prefetch(self, paths) -> None:
        """다음에 보일 사진들을 백그라운드에서 미리 읽어 둔다 — 넘길 때 디스크를 기다리지 않게.

        ``paths`` 에 없는 옛 예약은 버린다(되돌리기·건너뛰기로 순서가 바뀌어도 쌓이지 않는다).
        아직 다 못 읽은 사진은 `set_image` 가 그 자리에서 읽는다 — 결과는 같다."""
        want = [(Path(p), self._crop) for p in paths]       # 순서 = 넘길 순서
        for key in list(self._prefetched):
            if key not in want:
                self._prefetched.pop(key).cancel()
        for key in want:
            if key not in self._prefetched:
                self._prefetched[key] = _prefetch_pool().submit(self._decode, *key)

    @classmethod
    def _decode(cls, path: Path, crop: float) -> Optional[QImage]:
        """사진 한 장을 읽는다 — 메인 스레드와 미리 읽기 스레드가 **같은 함수**를 쓴다.

        가운데 확대면 원본의 가운데만, 아니면(또는 원본을 못 읽으면) 중간 화질 캐시."""
        if crop < 1.0:
            img = cls._load_center(path, crop)
            if img is not None:
                return img
        try:
            mid = image_io.get_mid_path(path)
            img = QImage(str(mid))
            if crop < 1.0 and not img.isNull():
                img = img.copy(cls._center_rect(img.width(), img.height(), crop))
        except Exception:
            return None
        return None if img.isNull() else img

    @staticmethod
    def _center_rect(w: int, h: int, frac: float):
        from PyQt6.QtCore import QRect
        cw, ch = max(1, round(w * frac)), max(1, round(h * frac))
        return QRect((w - cw) // 2, (h - ch) // 2, cw, ch)

    @classmethod
    def _load_center(cls, path: Path, frac: float) -> Optional[QImage]:
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
            return None if img.isNull() else img
        except Exception:
            return None

    def clear_image(self) -> None:
        self.prefetch(())
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
