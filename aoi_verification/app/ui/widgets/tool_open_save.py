"""도구 화면(Defect 정보 조회 · Defect Map · GL 히스토그램 · Overkill 튜닝 · 어느 Zone에서 왔니?)의
[열기] · [저장] 한 꼴 — 현택님 2026-11-24 결정(B1 안 B · B2 안 A).

- :class:`EmptyOpen` — 아무것도 안 불러온 동안 화면 가운데 큰 파랑 [사진 선택]/[폴더 선택] 하나만 덮는다.
  불러오면 사라지고, 같은 이름의 회색 버튼이 위 왼쪽에 남는다(각 화면의 ``pick_btn``).
- :class:`SavedLine` — 저장한 뒤 저장 버튼 옆에 '저장 완료 HH:MM' + [폴더 열기] 한 줄(결과 화면과 같은 말).
  저장 버튼은 모두 파랑 · 오른쪽 위 · [엑셀로 저장]/[그림으로 저장]/[HTML로 저장].
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QEvent, Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ... import i18n
from ..teardown import tolerate_teardown
from .neon_button import NeonButton

#: 빈 화면 가운데 버튼의 최소 폭 — 화면에 하나뿐인 할 일이라 크게(높이 56 은 QSS ``grade="hero"``).
EMPTY_BTN_W = 240


class EmptyOpen(QWidget):
    """``host`` 전체를 덮는 빈 화면 — 가운데 큰 파랑 버튼 하나(+ 있으면 짧은 끌어놓기 안내).

    덮는 동안에도 끌어다 놓기는 ``host`` 가 받는다(이 위젯은 drop 을 받지 않아 부모로 넘어간다).
    보이기는 화면이 :meth:`set_empty` 로 정한다 — 스스로 상태를 엿보지 않는다."""

    def __init__(self, host: QWidget, text: str, on_click: Callable[[], None],
                 hint: str = "") -> None:
        super().__init__(host)
        self._host = host
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
        lay.addStretch(1)
        host.installEventFilter(self)
        self.setGeometry(host.rect())
        self.hide()

    @tolerate_teardown
    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._host and event.type() == QEvent.Type.Resize:
            self.setGeometry(self._host.rect())
        return super().eventFilter(obj, event)

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
