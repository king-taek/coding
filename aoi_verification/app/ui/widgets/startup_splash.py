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
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (QApplication, QLabel, QProgressBar, QVBoxLayout,
                             QWidget)

from .. import theme
from .loading_overlay import _BusyStripe


class StartupSplash(QWidget):
    """로고 + 로딩 표시.  메인 창이 준비될 때까지 화면 가운데에 떠 있는다.

    ★ 두 단계로 만든다.  ``__init__`` 은 **로고만** 올린다 — 글자(QLabel 텍스트)와 스타일시트를
      만지지 않는다.  Windows 는 글자를 처음 재는 순간 설치된 글꼴을 훑는데, 재부팅 뒤 첫
      실행에서 그것이 5~7초 걸렸다(실측 app.log, `스플래시 생성` 구간).  그래서 로고를 먼저
      보이고(``show``), 글자·진행 표시줄은 :meth:`build_progress` 가 그 뒤에 붙인다.
    ★ 레이아웃을 쓰지 않고 좌표를 고정한다 — 창 크기를 처음부터 최종 크기로 잡아야 2단계에서
      창이 커지며 로고가 움직이지 않는다."""

    LOGO_W = 560        # 로고 표시 폭(논리 px) — 좁은 화면에서는 아래에서 줄인다.
    BAR_W = 360         # 진행 표시 폭 — busy/결정형이 같아야 전환에 폭이 안 뛴다.
    _MARGIN = 24
    _GAP = 16
    _LABEL_H = 22
    _BAR_H = 6

    def __init__(self, logo: QPixmap) -> None:
        super().__init__(None, Qt.WindowType.SplashScreen
                         | Qt.WindowType.WindowStaysOnTopHint)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QColor(theme.PANEL))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        self._logo = QLabel(self)
        self._logo.setPixmap(self._fit(logo))
        self._logo.adjustSize()
        self._label = None
        self._busy = None
        self._progress = None

        m, g = self._MARGIN, self._GAP
        inner_w = max(self._logo.width(), self.BAR_W)
        self._logo.move(m + (inner_w - self._logo.width()) // 2, m)
        self._label_y = m + self._logo.height() + g
        self._bar_y = self._label_y + self._LABEL_H + g
        self.setFixedSize(inner_w + 2 * m, self._bar_y + self._BAR_H + m - 4)
        self._center_on_screen()

    def build_progress(self) -> None:
        """글자와 진행 표시줄을 붙인다(처음 한 번).  글꼴 초기화가 여기서 일어난다."""
        if self._label is not None:
            return
        w = self.width()
        self._label = QLabel("", self)
        self._label.setStyleSheet(
            f"QLabel {{ color: {theme.INK2}; background: transparent; }}")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setGeometry(0, self._label_y, w, self._LABEL_H)

        # busy(무한) / 결정형 — LoadingOverlay 와 같은 규칙·같은 스윕을 쓴다.
        x = (w - self.BAR_W) // 2
        self._busy = _BusyStripe(self, width=self.BAR_W)
        self._busy.move(x, self._bar_y)
        self._progress = QProgressBar(self)
        self._progress.setFixedWidth(self.BAR_W)
        self._progress.setFixedHeight(self._BAR_H)
        self._progress.setTextVisible(False)
        self._progress.move(x, self._bar_y)
        self._progress.hide()
        self._label.show()
        self._busy.show()

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
        if self._busy is not None:
            self._busy.start()
        # 창이 뜨자마자 한 프레임을 강제로 그린다 — 이어지는 무거운 작업 전에
        # 로고가 **먼저** 보여야 한다.
        self.repaint()

    def set_progress(self, done: int, total: int, message: str = "") -> None:
        """진행 표시. ``total <= 0`` 이면 busy, 아니면 결정형(done/total)."""
        self.build_progress()
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
        if self._busy is not None:
            self._busy.stop()
        self.close()
