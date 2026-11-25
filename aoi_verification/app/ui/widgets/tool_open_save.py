"""도구 화면(Defect 정보 · Defect Map · GL 히스토그램 · Overkill 튜닝 · 어느 Zone에서 왔니?)의
[열기] · [저장] 한 꼴 — 현택님 2026-11-24 결정(B1 안 B · B2 안 A).

- :class:`EmptyOpen` — 아무것도 안 불러온 동안 화면 가운데 큰 파랑 [사진 선택]/[폴더 선택] 하나만 덮는다.
  불러오면 사라지고, 같은 이름의 회색 버튼이 위 왼쪽에 남는다(각 화면의 ``pick_btn``).
- :class:`SavedLine` — 저장한 뒤 저장 버튼 옆에 '저장 완료 HH:MM' + [폴더 열기] 한 줄(결과 화면과 같은 말).
  저장 버튼은 모두 파랑 · 오른쪽 위 · [엑셀로 저장]/[그림으로 저장]/[HTML로 저장].
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, QUrl
from PyQt6.QtGui import (QBrush, QColor, QDesktopServices, QPainter, QPixmap,
                         QRadialGradient)
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ... import i18n
from ...utils import paths
from ..teardown import tolerate_teardown
from .neon_button import NeonButton

log = logging.getLogger(__name__)

#: 빈 화면 가운데 버튼의 최소 폭 — 화면에 하나뿐인 할 일이라 크게(높이 56 은 QSS ``grade="hero"``).
EMPTY_BTN_W = 240

#: 배경 그림 위 덮개(테마 배경색)의 불투명도 · 버튼 둘레 후광의 세기 — 현택님이 시안 B 로 고른 값.
VEIL_ALPHA = 0.60
HALO_MAX = 0.90
#: 후광 반지름 = 버튼+안내 덩어리 크기 × 배율 + 여유(px).
HALO_RX = (1.1, 60)
HALO_RY = (1.6, 60)


class EmptyOpen(QWidget):
    """``host`` 전체를 덮는 빈 화면 — 가운데 큰 파랑 버튼 하나(+ 있으면 짧은 끌어놓기 안내).

    덮는 동안에도 끌어다 놓기는 ``host`` 가 받는다(이 위젯은 drop 을 받지 않아 부모로 넘어간다).
    보이기는 화면이 :meth:`set_empty` 로 정한다 — 스스로 상태를 엿보지 않는다.

    ``preview`` 가 ``ui/assets/empty_preview_<preview>.jpg`` 를 가리키면 버튼 뒤에 그 화면이 불러온 모습을
    흐리게 깐다(그림 → 배경색 덮개 60% → 버튼 둘레 타원 후광).  흐림은 그림에 이미 들어 있고(`make_empty_previews.py`),
    덮개·후광은 테마 색으로 그때그때 그린다.  파일이 없거나 못 읽으면 경고만 남기고 예전처럼 민 배경이다."""

    def __init__(self, host: QWidget, text: str, on_click: Callable[[], None],
                 hint: str = "", preview: str = "") -> None:
        super().__init__(host)
        self._host = host
        self._preview = self._load_preview(preview)
        self._scaled: Optional[QPixmap] = None     # 위젯 크기별 캐시 — 크기가 바뀔 때만 다시 만든다
        self.setObjectName("emptyOpen")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        from .. import theme
        self.setStyleSheet(f"QWidget#emptyOpen {{ background: {theme.BG}; }}")
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addStretch(1)
        self.btn = NeonButton(text, role="primary")
        self.btn.setProperty("grade", "hero")      # 높이는 QSS 가 정한다(QSS min-height 가 setMinimumHeight 를 이긴다)
        self.btn.setMinimumWidth(EMPTY_BTN_W)
        self.btn.setAutoDefault(False)
        self.btn.clicked.connect(on_click)
        lay.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignHCenter)
        self.hint = QLabel(hint, self)
        self.hint.setProperty("role", "muted")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setVisible(bool(hint))
        lay.addWidget(self.hint, 0, Qt.AlignmentFlag.AlignHCenter)
        # 빈 화면이 호스트를 덮는 동안은 호스트의 토스트가 가려진다 — 알릴 말은 여기에 띄운다(:meth:`flash`).
        self.note = QLabel("", self)
        self.note.setProperty("role", "muted")
        self.note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.note.setMinimumHeight(self.note.fontMetrics().height() + 4)   # 자리 예약 — 뜰 때 버튼이 밀리지 않게
        lay.addWidget(self.note, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)
        host.installEventFilter(self)
        self.setGeometry(host.rect())
        self.hide()

    @staticmethod
    def _load_preview(name: str) -> Optional[QPixmap]:
        if not name:
            return None
        path = paths.logo_path(f"empty_preview_{name}.jpg")      # ui/assets 의 파일
        pm = QPixmap(str(path))
        if pm.isNull():
            log.warning("빈 화면 배경 그림을 못 읽었다: %s", path)
            return None
        return pm

    def _cover_pixmap(self) -> QPixmap:
        """그림을 위젯을 덮도록(비율 유지, 왼쪽 위 기준, 오른쪽·아래 잘림) 키워 캐시한다."""
        size = self.size()
        if self._scaled is None or self._scaled.size() != size:
            pm = self._preview.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                      Qt.TransformationMode.SmoothTransformation)
            self._scaled = pm.copy(QRect(0, 0, size.width(), size.height()))
        return self._scaled

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)               # 스타일시트 배경(theme.BG)
        if self._preview is None:
            return
        from .. import theme
        bg = QColor(theme.BG)
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cover_pixmap())
        veil = QColor(bg)
        veil.setAlphaF(VEIL_ALPHA)
        p.fillRect(self.rect(), veil)
        block = self.btn.geometry()
        if self.hint.isVisible():
            block = block.united(self.hint.geometry())
        rx = block.width() * HALO_RX[0] + HALO_RX[1]
        ry = block.height() * HALO_RY[0] + HALO_RY[1]
        # 반지름 rx 의 원 그라데이션을 세로로 ry/rx 만큼 눌러 타원으로 — alpha = clamp(1.25 − d, 0, 1)^1.5 × 0.9
        grad = QRadialGradient(QPointF(0, 0), rx * 1.25)       # 알파가 0 이 되는 d = 1.25 까지
        for i in range(26):
            d = 1.25 * i / 25
            c = QColor(bg)
            c.setAlphaF(max(0.0, min(1.0, 1.25 - d)) ** 1.5 * HALO_MAX)
            grad.setColorAt(i / 25, c)
        p.save()
        p.translate(block.center().x() + 0.5, block.center().y() + 0.5)
        p.scale(1.0, ry / rx)
        p.setBrush(QBrush(grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(QRectF(-rx * 1.3, -rx * 1.3, rx * 2.6, rx * 2.6))   # 알파 0 바깥은 그리지 않아도 같다
        p.restore()
        p.end()

    @tolerate_teardown
    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._host and event.type() == QEvent.Type.Resize:
            self.setGeometry(self._host.rect())
        return super().eventFilter(obj, event)

    def flash(self, text: str, hold_ms: int = 1800) -> None:
        """빈 화면 위에 짧은 알림 한 줄(사진 없음 등) — 버튼 아래에 잠깐 떴다 지워진다."""
        from .. import motion
        motion.flash_text(self.note, text, hold_ms=hold_ms)

    def set_empty(self, empty: bool) -> None:
        if empty:
            self.setGeometry(self._host.rect())
            if not self.isVisibleTo(self._host):
                self.show()
                self.raise_()           # 로딩 막은 그 뒤에 뜨면 스스로 위로 올라온다(LoadingOverlay.show_overlay)
        else:
            self.hide()


class SavedLine(QWidget):
    """저장 버튼 옆 '저장 완료 HH:MM' + [폴더 열기] — 저장하기 전엔 숨는다."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.label = QLabel("", self)
        self.label.setProperty("role", "statusPass")
        lay.addWidget(self.label)
        self.open_btn = NeonButton(i18n.KO.BTN_OPEN_SAVED_FOLDER, role="ghost")
        self.open_btn.setProperty("grade", "panel")
        self.open_btn.setAutoDefault(False)
        self.open_btn.clicked.connect(self.open_folder)
        lay.addWidget(self.open_btn)
        self._path: Optional[Path] = None
        self.hide()

    def show_saved(self, path, when: Optional[datetime] = None) -> None:
        self._path = Path(path)
        self.label.setText(i18n.KO.RESULT_SAVED_AT_FMT.format(
            time=(when or datetime.now()).strftime("%H:%M")))
        self.label.setToolTip(str(self._path))
        self.show()

    def saved_path(self) -> Optional[Path]:
        return self._path

    def open_folder(self) -> None:
        if self._path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._path.parent)))
