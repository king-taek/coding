"""시작 스플래시 — 실행 즉시 로고(``logo_big``)와 로딩 진행을 띄운다.

CLAUDE.md 로딩 계약을 그대로 따른다: 진행은 ``set_progress(done, total, message)``,
``total <= 0`` 이면 busy(무한 진행) 라 **진행량을 몰라도 0 에 멈춰 있지 않는다**.

메인 스레드가 막히면 어떤 애니메이션도 멈춘다.  그래서 가장 오래 걸리는 구간(무거운
모듈 import)은 ``main.py`` 가 백그라운드 스레드로 돌리고, 메인 스레드는 이벤트 루프를
굴려 busy 스윕이 실제로 움직이게 한다.  메인 스레드에서 할 수밖에 없는 구간(위젯 생성)
은 결정형으로 칸을 올리고 :meth:`set_progress` 안에서 곧바로 다시 그린다.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QApplication, QLabel, QProgressBar, QVBoxLayout,
                             QWidget)

from .. import theme
from .loading_overlay import _BusyStripe


class StartupSplash(QWidget):
    """로고 + 로딩 표시.  메인 창이 준비될 때까지 화면 가운데에 떠 있는다."""

    LOGO_W = 560        # 로고 표시 폭(논리 px) — 좁은 화면에서는 아래에서 줄인다.
    BAR_W = 360         # 진행 표시 폭 — busy/결정형이 같아야 전환에 폭이 안 뛴다.

    def __init__(self, logo: QPixmap) -> None:
        super().__init__(None, Qt.WindowType.SplashScreen
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(
            f"QWidget {{ background: {theme.PANEL}; }}"
            f"QLabel {{ color: {theme.INK2}; background: transparent; }}"
        )

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 20)
        v.setSpacing(16)

        self._logo = QLabel(self)
        self._logo.setPixmap(self._fit(logo))
        v.addWidget(self._logo, alignment=Qt.AlignmentFlag.AlignCenter)

        self._label = QLabel("", self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._label)

        # busy(무한) / 결정형 — LoadingOverlay 와 같은 규칙·같은 스윕을 쓴다.
        self._busy = _BusyStripe(self, width=self.BAR_W)
        self._progress = QProgressBar(self)
        self._progress.setFixedWidth(self.BAR_W)
        self._progress.setFixedHeight(6)
        self._progress.setTextVisible(False)
        self._progress.hide()
        v.addWidget(self._busy, alignment=Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._progress, alignment=Qt.AlignmentFlag.AlignCenter)

        self.adjustSize()
        self._center_on_screen()

    def _fit(self, logo: QPixmap) -> QPixmap:
        """화면 폭을 넘지 않게 로고를 줄인다(HiDPI 에서도 또렷하게)."""
        width = self.LOGO_W
        screen = QApplication.primaryScreen()
        if screen is not None:
            width = min(width, int(screen.availableGeometry().width() * 0.5))
        if logo.isNull():
            return logo
        dpr = self.devicePixelRatioF() or 1.0
        scaled = logo.scaledToWidth(int(width * dpr),
                                    Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        return scaled

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = self.frameGeometry()
        geo.moveCenter(screen.availableGeometry().center())
        self.move(geo.topLeft())

    def show(self) -> None:  # noqa: D102
        super().show()
        self._busy.start()
        # 창이 뜨자마자 한 프레임을 강제로 그린다 — 이어지는 무거운 작업 전에
        # 로고가 **먼저** 보여야 한다.
        self.repaint()

    def set_progress(self, done: int, total: int, message: str = "") -> None:
        """진행 표시. ``total <= 0`` 이면 busy, 아니면 결정형(done/total)."""
        if message:
            self._label.setText(message)
        if total > 0:
            self._busy.stop()
            self._busy.hide()
            self._progress.show()
            self._progress.setRange(0, int(total))
            self._progress.setValue(max(0, min(int(done), int(total))))
        else:
            self._progress.hide()
            self._busy.show()
            self._busy.start()
        # 메인 스레드를 막는 구간에서도 갱신이 보이도록 그 자리에서 다시 그린다.
        self.repaint()

    def finish(self, window: QWidget) -> None:
        """메인 창이 뜬 뒤 스플래시를 걷는다."""
        window.raise_()
        window.activateWindow()
        self._busy.stop()
        self.close()
