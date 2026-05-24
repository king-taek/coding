"""좌(기준)·우(후보) 나란히 크게보기 뷰어 (#1e/#4).

기준 사진은 고정하고, 후보를 이전/다음으로 순환하며 비교한다.  두 이미지 모두
원본 파일을 직접 디코드해 ‘최고 화질’ 로 보여준다(팝업이므로 비용 허용).
선택적으로 하단에 액션 버튼(예: ‘이 후보로 선택/매치’)을 두고, 누르면 현재
후보 ``ImageItem`` 을 ``action_requested`` 로 내보내고 닫는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                             QVBoxLayout, QWidget)

from ...models.slot import ImageItem
from .neon_button import NeonButton
from .window_controls import add_fullscreen_shortcut, enable_window_controls


def _decode_original(path: Path) -> QPixmap:
    pix = QPixmap(str(path))
    if pix.isNull():
        pix = QPixmap(800, 600)
        pix.fill(QColor(20, 28, 40))
    return pix


class _Pane(QWidget):
    """제목 + 비율 유지로 꽉 채우는 이미지 라벨 (원본 화질)."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self._pix: Optional[QPixmap] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self._title = QLabel(title, self)
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setStyleSheet("color: #00D4FF; font-weight: 700;")
        lay.addWidget(self._title)
        self._img = QLabel(self)
        self._img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img.setStyleSheet("background: #000; border: 1px solid #1F2A3F;")
        lay.addWidget(self._img, stretch=1)

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_pixmap(self, pix: QPixmap) -> None:
        self._pix = pix
        self._redraw()

    def resizeEvent(self, e):  # noqa: N802
        self._redraw()
        super().resizeEvent(e)

    def _redraw(self) -> None:
        if self._pix is None or self._pix.isNull():
            return
        target = self._img.size()
        if target.width() <= 0 or target.height() <= 0:
            return
        self._img.setPixmap(self._pix.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))


class SideBySideViewer(QDialog):
    """기준(좌) + 후보(우, 이전/다음 순환) 비교 팝업.

    ``candidates`` 는 ``(ImageItem, caption)`` 리스트(점수 등 캡션 포함).
    ``action_label`` 이 주어지면 하단에 액션 버튼을 두고, 클릭 시 현재 후보
    ``ImageItem`` 을 ``action_requested`` 로 emit 하고 닫는다.
    """

    action_requested = pyqtSignal(object)        # 현재 후보 ImageItem

    def __init__(self,
                 ref_path: Path,
                 candidates: List[Tuple[ImageItem, str]],
                 start_index: int = 0,
                 *,
                 ref_caption: str = "기준 사진",
                 action_label: Optional[str] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setModal(True)
        self.setStyleSheet("background-color: #050810;")
        self._ref_path = Path(ref_path)
        self._candidates = list(candidates)
        self._idx = max(0, min(int(start_index), len(self._candidates) - 1)) \
            if self._candidates else 0
        self._ref_caption = ref_caption

        scr = QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.resize(int(g.width() * 0.9), int(g.height() * 0.88))
        else:
            self.resize(1400, 850)
        enable_window_controls(self)
        add_fullscreen_shortcut(self)
        self._build(action_label)
        QShortcut(QKeySequence("Esc"), self, activated=self.close)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=self._prev)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=self._next)

        self._ref_pane.set_pixmap(_decode_original(self._ref_path))
        self._render_candidate()

    # ------------------------------------------------------------------
    def _build(self, action_label: Optional[str]) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # 상단 바: 이전 · 위치 · 다음 · (액션) · 닫기
        bar = QHBoxLayout()
        self.btn_prev = NeonButton("◀ 이전", role="ghost")
        self.btn_prev.clicked.connect(self._prev)
        bar.addWidget(self.btn_prev)
        self.pos_label = QLabel("", self)
        self.pos_label.setStyleSheet("color: #7FB3D5; font-weight: 700;")
        self.pos_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar.addWidget(self.pos_label, stretch=1)
        self.btn_next = NeonButton("다음 ▶", role="ghost")
        self.btn_next.clicked.connect(self._next)
        bar.addWidget(self.btn_next)
        if action_label:
            self.btn_action = NeonButton(action_label, role="primary")
            self.btn_action.clicked.connect(self._fire_action)
            bar.addWidget(self.btn_action)
        self.btn_close = NeonButton("닫기", role="ghost")
        self.btn_close.clicked.connect(self.close)
        bar.addWidget(self.btn_close)
        root.addLayout(bar)

        body = QHBoxLayout()
        body.setSpacing(10)
        self._ref_pane = _Pane(self._ref_caption, self)
        self._cand_pane = _Pane("후보", self)
        body.addWidget(self._ref_pane, stretch=1)
        body.addWidget(self._cand_pane, stretch=1)
        root.addLayout(body, stretch=1)

    # ------------------------------------------------------------------
    def _current_item(self) -> Optional[ImageItem]:
        if not self._candidates:
            return None
        return self._candidates[self._idx][0]

    def _render_candidate(self) -> None:
        if not self._candidates:
            self.pos_label.setText("후보 없음")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            return
        item, caption = self._candidates[self._idx]
        self._cand_pane.set_title(caption or item.filename)
        self._cand_pane.set_pixmap(_decode_original(Path(item.path)))
        self.pos_label.setText(f"{self._idx + 1} / {len(self._candidates)}")
        self.btn_prev.setEnabled(self._idx > 0)
        self.btn_next.setEnabled(self._idx < len(self._candidates) - 1)

    def _prev(self) -> None:
        if self._idx > 0:
            self._idx -= 1
            self._render_candidate()

    def _next(self) -> None:
        if self._idx < len(self._candidates) - 1:
            self._idx += 1
            self._render_candidate()

    def _fire_action(self) -> None:
        item = self._current_item()
        if item is not None:
            self.action_requested.emit(item)
        self.accept()
