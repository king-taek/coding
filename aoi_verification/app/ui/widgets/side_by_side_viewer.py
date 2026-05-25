"""좌(기준)·우(후보) 나란히 크게보기 뷰어 (#1e/#4).

기준 사진은 고정하고, 후보를 이전/다음으로 순환하며 비교한다.  두 이미지 모두
원본 파일을 직접 디코드해 ‘최고 화질’ 로 보여준다(팝업이므로 비용 허용).
선택적으로 하단에 액션 버튼(예: ‘이 후보로 선택/매치’)을 두고, 누르면 현재
후보 ``ImageItem`` 을 ``action_requested`` 로 내보내고 닫는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                             QSizePolicy, QVBoxLayout, QWidget)

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
        # 기준·후보가 동일한 크기로 보이도록 두 패널이 공유하는 목표 박스 (#3).
        self._box: Optional[QSize] = None
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
        # 크기 제약 없는 QLabel 에 라벨 크기로 스케일한 pixmap 을 넣으면
        # minimumSizeHint 이 그 pixmap 크기로 커져 리사이즈마다 창이 계속 커진다.
        # Ignored 정책 + 1×1 최소크기로 레이아웃 성장 피드백을 끊는다.
        self._img.setSizePolicy(QSizePolicy.Policy.Ignored,
                                QSizePolicy.Policy.Ignored)
        self._img.setMinimumSize(1, 1)
        lay.addWidget(self._img, stretch=1)

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_pixmap(self, pix: QPixmap) -> None:
        self._pix = pix
        self._redraw()

    def set_target_box(self, box: QSize) -> None:
        """두 패널이 같은 박스에 맞춰 스케일하도록 공통 목표 크기 주입 (#3)."""
        self._box = box
        self._redraw()

    def img_size(self) -> QSize:
        return self._img.size()

    def resizeEvent(self, e):  # noqa: N802
        self._redraw()
        super().resizeEvent(e)

    def _redraw(self) -> None:
        if self._pix is None or self._pix.isNull():
            return
        # 공통 박스가 있으면 그 박스에 맞춰(기준·후보 동일 크기), 없으면 라벨 크기.
        target = self._box if (self._box is not None
                               and self._box.width() > 0
                               and self._box.height() > 0) else self._img.size()
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
            self.setMaximumSize(g.size())      # 화면 초과 성장 차단(#방어)
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

        # 상단 바: 위치 · (방향키 안내) · 이전 · 다음 · (액션) · 닫기 (#4).
        # 이전 버튼을 다음 버튼 바로 옆으로 모으고, 방향키 조작 가능을 표기한다.
        bar = QHBoxLayout()
        self.pos_label = QLabel("", self)
        self.pos_label.setStyleSheet("color: #7FB3D5; font-weight: 700;")
        bar.addWidget(self.pos_label)
        bar.addStretch(1)
        key_hint = QLabel("← → 방향키로 이동", self)
        key_hint.setStyleSheet("color: #5A6B82; font-size: 12px;")
        bar.addWidget(key_hint)
        self.btn_prev = NeonButton("◀ 이전", role="ghost")
        self.btn_prev.clicked.connect(self._prev)
        bar.addWidget(self.btn_prev)
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
    def _sync_panes(self) -> None:
        """기준·후보가 동일한 크기로 보이도록 두 패널의 공통 목표 박스를 맞춘다 (#3).

        두 이미지 라벨 크기의 원소별 최소값을 공통 박스로 삼아 양쪽에 주입한다.
        같은 종횡비(같은 웨이퍼 크롭)면 표시 크기가 정확히 일치하고, 종횡비가
        달라도 두 이미지가 같은 박스 안에 동일 기준으로 맞춰진다."""
        rs = self._ref_pane.img_size()
        cs = self._cand_pane.img_size()
        box = QSize(min(rs.width(), cs.width()), min(rs.height(), cs.height()))
        if box.width() <= 0 or box.height() <= 0:
            return
        self._ref_pane.set_target_box(box)
        self._cand_pane.set_target_box(box)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._sync_panes()

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
        self._sync_panes()

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
