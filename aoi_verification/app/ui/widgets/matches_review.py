"""매칭 결과 검토/편집 다이얼로그 (#18).

- 최종 매칭 목록을 슬롯별로 정렬해 보여줌.
- 각 행에서 잘못된 매칭을 ‘삭제’ 해서 결과에서 제외 가능.
- 닫을 때 호출자는 ``removed`` 로 삭제된 MatchResult 목록을 받아 결과를 갱신.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QScrollArea,
                              QVBoxLayout, QWidget)

from ... import i18n
from ...models.result import MatchResult
from ...utils import image_io
from .neon_button import NeonButton
from .window_controls import add_fullscreen_shortcut, enable_window_controls


_THUMB = 240


class _Row(QWidget):
    delete_requested = pyqtSignal(object)        # MatchResult

    def __init__(self, m: MatchResult, parent=None) -> None:
        super().__init__(parent)
        self.match = m
        self.setMinimumHeight(_THUMB + 40)
        self.setStyleSheet(
            "QWidget { background: #0E1424; border: 1px solid #1F2A3F; "
            "border-radius: 8px; }"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(10)

        # Slot 메타
        meta = QLabel(
            f"{m.slot}\n{m.direction}\nscore {m.score * 100:.1f} %", self,
        )
        meta.setStyleSheet(
            "color: #00D4FF; font-weight: 700; border: none; padding: 4px;"
        )
        meta.setFixedWidth(120)
        meta.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(meta)

        lay.addWidget(self._make_thumb(m.ref_path))
        arrow = QLabel("→", self)
        arrow.setStyleSheet(
            "color: #7FB3D5; font-size: 24px; border: none;"
        )
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(arrow)
        lay.addWidget(self._make_thumb(m.val_path))
        lay.addStretch(1)

        btn = NeonButton(i18n.KO.REVIEW_BTN_DELETE, role="danger")
        btn.clicked.connect(lambda: self.delete_requested.emit(self.match))
        lay.addWidget(btn)

    @staticmethod
    def _make_thumb(p: Path):
        from PyQt6.QtWidgets import QVBoxLayout, QWidget
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)

        lab = QLabel()
        lab.setFixedSize(_THUMB, _THUMB)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lab.setStyleSheet("border: none;")
        try:
            mid = image_io.get_mid_path(Path(p))
            pix = QPixmap(str(mid))
        except Exception:
            pix = QPixmap(_THUMB, _THUMB)
            pix.fill(QColor(20, 28, 40))
        if pix.isNull():
            pix = QPixmap(_THUMB, _THUMB)
            pix.fill(QColor(20, 28, 40))
        pix = pix.scaled(
            _THUMB, _THUMB,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        lab.setPixmap(pix)
        v.addWidget(lab)

        cap = QLabel(Path(p).name, host)
        cap.setProperty("role", "muted")
        cap.setStyleSheet("color: #7FB3D5; font-size: 11px; border: none;")
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setWordWrap(True)
        v.addWidget(cap)
        return host


class MatchesReviewDialog(QDialog):
    def __init__(self, matches: Iterable[MatchResult], parent=None) -> None:
        super().__init__(parent)
        # 닫는 즉시 C++ 위젯 해제 — 부모 (ResultPage) 에 dialog 가 쌓이지 않도록.
        # exec() 가 반환된 직후엔 deleteLater 가 아직 처리되지 않아 Python 측
        # 속성 (self._removed) 접근은 안전.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(i18n.KO.REVIEW_DIALOG_TITLE)
        self.resize(1400, 800)
        self._removed: list[MatchResult] = []
        self._matches: list[MatchResult] = sorted(
            matches, key=lambda m: (m.slot, m.ref_path.name.lower()),
        )
        # 창에 최소화/최대화 버튼 + F11 전체화면 토글 (#9). 첫 show 이전에 설정.
        enable_window_controls(self)
        add_fullscreen_shortcut(self)
        self._build()

    @property
    def removed(self) -> list[MatchResult]:
        return list(self._removed)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)

        info = QLabel(i18n.KO.REVIEW_HINT, self)
        info.setProperty("role", "muted")
        info.setWordWrap(True)
        root.addWidget(info)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        host = QWidget()
        scroll.setWidget(host)
        self._list = QVBoxLayout(host)
        self._list.setContentsMargins(4, 4, 4, 4)
        self._list.setSpacing(8)
        self._list.addStretch(1)
        root.addWidget(scroll, stretch=1)

        bar = QHBoxLayout()
        bar.addStretch(1)
        ok = NeonButton(i18n.KO.BTN_OK, role="primary")
        ok.clicked.connect(self.accept)
        bar.addWidget(ok)
        root.addLayout(bar)

        self._render()

    def _render(self) -> None:
        while self._list.count():
            it = self._list.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        for m in self._matches:
            row = _Row(m)
            row.delete_requested.connect(self._on_delete)
            self._list.addWidget(row)
        self._list.addStretch(1)

    def _on_delete(self, m: MatchResult) -> None:
        self._removed.append(m)
        self._matches = [x for x in self._matches
                         if (x.slot, x.ref_path.name, x.val_path.name) !=
                            (m.slot, m.ref_path.name, m.val_path.name)]
        self._render()
