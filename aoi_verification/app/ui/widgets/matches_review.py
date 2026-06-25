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
from PyQt6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel,
                              QScrollArea, QVBoxLayout, QWidget)

from ... import config, i18n
from ...models.result import MatchResult
from ...utils import image_io
from .neon_button import NeonButton
from .no_wheel_slider import NoWheelSlider
from .window_controls import add_fullscreen_shortcut, enable_window_controls


_THUMB = config.Sizing.REVIEW_THUMB_PX  # 썸네일 기본 크기 (= 240), 슬라이더로 조절(#2).
_SIZE_MIN_PX = 140
_SIZE_MAX_PX = 480


class _Row(QWidget):
    delete_requested = pyqtSignal(object)        # MatchResult

    _STYLE_NORMAL = (
        "QWidget#matchRow { background: #0E1424; border: 1px solid #1F2A3F; "
        "border-radius: 8px; }"
    )
    _STYLE_PENDING = (
        "QWidget#matchRow { background: #2A0E16; border: 3px solid #FF2D55; "
        "border-radius: 8px; }"
    )

    def __init__(self, m: MatchResult, parent=None, *, size: int = _THUMB) -> None:
        super().__init__(parent)
        self.match = m
        self._size = int(size)
        self._pending = False
        # 슬라이더 리사이즈를 재빌드 없이 처리하기 위한 (label, source_pix) 보관.
        self._thumbs: list[tuple[QLabel, QPixmap]] = []
        self.setObjectName("matchRow")
        # 스타일시트 배경/테두리가 일반 QWidget 에도 칠해지도록.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumHeight(self._size + 40)
        # objectName 선택자로 한정 — 자식 위젯에 빨간 테두리가 번지지 않게.
        self.setStyleSheet(self._STYLE_NORMAL)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(10)

        # Slot 메타
        meta = QLabel(
            f"{m.slot}\nscore {m.score * 100:.1f} %", self,
        )
        meta.setStyleSheet(
            "color: #39FF14; font-weight: 700; border: none; padding: 4px;"
        )
        meta.setFixedWidth(120)
        meta.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(meta)

        lay.addWidget(self._make_thumb(m.ref_path, self._size))
        arrow = QLabel("→", self)
        arrow.setStyleSheet(
            "color: #7FB3D5; font-size: 24px; border: none;"
        )
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(arrow)
        lay.addWidget(self._make_thumb(m.val_path, self._size))
        lay.addStretch(1)

        self.btn = NeonButton(i18n.KO.REVIEW_BTN_DELETE, role="danger")
        self.btn.clicked.connect(lambda: self.delete_requested.emit(self.match))
        lay.addWidget(self.btn)

    def set_thumb_size(self, size: int) -> None:
        """슬라이더로 크기 변경 (#2) — 행을 재생성하지 않고 보관된 source 픽스맵을
        그 자리에서 재스케일한다.  대량 행에서도 즉시 반응(재빌드/재디코드 없음)."""
        self._size = int(size)
        self.setMinimumHeight(self._size + 40)
        for label, source in self._thumbs:
            label.setFixedSize(self._size, self._size)
            label.setPixmap(source.scaled(
                self._size, self._size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))


    def set_pending_delete(self, pending: bool) -> None:
        """삭제 예정 표시 — 빨간 테두리 + 버튼 토글 (확인 전까지 실제 삭제 안 함)."""
        self._pending = bool(pending)
        self.setStyleSheet(self._STYLE_PENDING if pending else self._STYLE_NORMAL)
        if pending:
            self.btn.setText(i18n.KO.REVIEW_BTN_UNDELETE)
            self.btn.setRole("ghost")
        else:
            self.btn.setText(i18n.KO.REVIEW_BTN_DELETE)
            self.btn.setRole("danger")

    def _make_thumb(self, p: Path, size: int = _THUMB):
        from PyQt6.QtWidgets import QVBoxLayout, QWidget
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)

        lab = QLabel()
        lab.setFixedSize(size, size)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lab.setStyleSheet("border: none;")
        try:
            mid = image_io.get_mid_path(Path(p))
            pix = QPixmap(str(mid))
        except Exception:
            pix = QPixmap(size, size)
            pix.fill(QColor(20, 28, 40))
        if pix.isNull():
            pix = QPixmap(size, size)
            pix.fill(QColor(20, 28, 40))
        # source 픽스맵은 최대 슬라이더 크기로 한 번만 다운스케일해 보관 →
        # 슬라이더 리사이즈 시 재디코드 없이 또렷하게 재스케일, 메모리도 상한.
        source = pix.scaled(
            _SIZE_MAX_PX, _SIZE_MAX_PX,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ) if max(pix.width(), pix.height()) > _SIZE_MAX_PX else pix
        self._thumbs.append((lab, source))
        lab.setPixmap(source.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
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
        self._pending_keys: set = set()       # 삭제 예정 (확인 전) MatchResult.key
        self._rows_by_key: dict = {}
        self._thumb_px = _THUMB               # 사진 크기 (#2)
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
        # 한 줄에 매치 2개씩 — 그리드(2열)로 배치.  열은 동일 너비로 늘린다.
        self._list = QGridLayout(host)
        self._list.setContentsMargins(4, 4, 4, 4)
        self._list.setHorizontalSpacing(12)
        self._list.setVerticalSpacing(8)
        self._list.setColumnStretch(0, 1)
        self._list.setColumnStretch(1, 1)
        root.addWidget(scroll, stretch=1)

        bar = QHBoxLayout()
        # 사진 크기 슬라이더 (#2) — 마우스 휠로는 조절 불가 (NoWheelSlider).
        size_label = QLabel(i18n.KO.IMAGE_SIZE_LABEL, self)
        size_label.setProperty("role", "muted")
        bar.addWidget(size_label)
        self.size_slider = NoWheelSlider(Qt.Orientation.Horizontal, self)
        self.size_slider.setRange(_SIZE_MIN_PX, _SIZE_MAX_PX)
        self.size_slider.setValue(self._thumb_px)
        self.size_slider.setSingleStep(20)
        self.size_slider.setPageStep(80)
        self.size_slider.setFixedWidth(180)
        self.size_slider.valueChanged.connect(self._on_size_changed)
        bar.addWidget(self.size_slider)
        self.size_value = QLabel(f"{self._thumb_px} px", self)
        self.size_value.setProperty("role", "muted")
        self.size_value.setFixedWidth(56)
        bar.addWidget(self.size_value)
        bar.addStretch(1)
        ok = NeonButton(i18n.KO.BTN_OK, role="primary")
        ok.clicked.connect(self._confirm)
        bar.addWidget(ok)
        root.addLayout(bar)

        self._render()

    def _on_size_changed(self, value: int) -> None:
        self._thumb_px = int(value)
        self.size_value.setText(f"{value} px")
        # 행을 재생성하지 않고 보관된 픽스맵을 그 자리에서 재스케일 (#2 성능).
        for row in self._rows_by_key.values():
            row.set_thumb_size(self._thumb_px)

    _COLS = 2          # 한 줄에 매치 2개씩 (#2)

    def _render(self) -> None:
        while self._list.count():
            it = self._list.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        self._rows_by_key = {}
        for i, m in enumerate(self._matches):
            row = _Row(m, size=self._thumb_px)
            row.delete_requested.connect(self._on_delete)
            row.set_pending_delete(m.key in self._pending_keys)
            self._rows_by_key[m.key] = row
            # 2열 그리드 — 왼쪽→오른쪽, 위→아래 순으로 채운다.
            self._list.addWidget(row, i // self._COLS, i % self._COLS,
                                 Qt.AlignmentFlag.AlignTop)

    def _on_delete(self, m: MatchResult) -> None:
        # 즉시 삭제하지 않고 '삭제 예정'(빨간 테두리)으로 토글 (#14).
        if m.key in self._pending_keys:
            self._pending_keys.discard(m.key)
        else:
            self._pending_keys.add(m.key)
        row = self._rows_by_key.get(m.key)
        if row is not None:
            row.set_pending_delete(m.key in self._pending_keys)

    def _confirm(self) -> None:
        """[확인] — 삭제 예정으로 표시된 매치들을 실제 제외 처리하고 닫는다."""
        self._removed = [m for m in self._matches if m.key in self._pending_keys]
        self.accept()
