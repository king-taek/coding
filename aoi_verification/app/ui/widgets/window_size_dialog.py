"""화면 크기 설정 모달.

게임에서 해상도를 고르는 방식과 동일하게:
- 현재 모니터 해상도 이하의 표준 해상도들을 드롭다운에 채우고,
- ‘사용자 지정’ 옵션을 선택하면 width/height 스핀박스가 노출되며,
- ‘전체 화면’ 체크박스로 borderless fullscreen 진입.

결과는 ``UserSizeChoice`` 객체로 노출된다. 호출자는 ``MainWindow.resize()``
또는 ``showFullScreen()`` / ``showNormal()`` 를 책임진다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                              QFormLayout, QHBoxLayout, QLabel, QSpinBox,
                              QVBoxLayout, QWidget)

from ... import i18n


# 표준 해상도 후보 — 현재 모니터 해상도 이하만 보여줌.
_STANDARD_RESOLUTIONS: List[Tuple[int, int]] = [
    (1280, 720),
    (1366, 768),
    (1600, 900),
    (1920, 1080),
    (2560, 1440),
    (3840, 2160),
]

# 최소 창 크기.
MIN_WIDTH = 1024
MIN_HEIGHT = 640


@dataclass
class UserSizeChoice:
    """사용자가 다이얼로그에서 결정한 결과."""

    width: int
    height: int
    fullscreen: bool = False


# ---------------------------------------------------------------------------
def _monitor_available_size() -> Tuple[int, int]:
    """현재 주 모니터의 사용 가능 영역 (작업표시줄 등 제외)."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return (1920, 1080)
    geo = screen.availableGeometry()
    return (int(geo.width()), int(geo.height()))


def filter_standard_resolutions(max_w: int, max_h: int) -> List[Tuple[int, int]]:
    """모니터 영역 이하인 표준 해상도만."""
    return [(w, h) for (w, h) in _STANDARD_RESOLUTIONS
            if w <= max_w and h <= max_h]


def suggest_default_size() -> Tuple[int, int]:
    """첫 실행 시 기본 크기 — 모니터 영역에서 100px 마진을 뺀 영역에 맞는
    가장 큰 표준 해상도. 없으면 모니터 영역에서 100 만큼 빼고 반환."""
    mw, mh = _monitor_available_size()
    target_w = max(MIN_WIDTH, mw - 100)
    target_h = max(MIN_HEIGHT, mh - 100)
    candidates = filter_standard_resolutions(target_w, target_h)
    if candidates:
        # 가장 큰 후보.
        return candidates[-1]
    return (max(MIN_WIDTH, target_w), max(MIN_HEIGHT, target_h))


# ---------------------------------------------------------------------------
class WindowSizeDialog(QDialog):
    """창 크기 / 전체화면 설정 모달."""

    _CUSTOM_KEY = "__custom__"
    _MAX_KEY = "__monitor_max__"

    def __init__(self,
                 current_width: int = 0,
                 current_height: int = 0,
                 current_fullscreen: bool = False,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.WINDOW_SIZE_DIALOG_TITLE)
        self.setModal(True)

        mw, mh = _monitor_available_size()
        self._monitor_w = mw
        self._monitor_h = mh

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # 해상도 드롭다운 ----------------------------------------------------
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self._combo = QComboBox(self)
        # 1) 현재 모니터 최대값을 맨 위에.
        self._combo.addItem(
            i18n.KO.WINDOW_SIZE_CURRENT_MAX_FMT.format(w=mw, h=mh),
            self._MAX_KEY,
        )
        # 2) 표준 해상도들 (≤ 모니터).
        for (w, h) in filter_standard_resolutions(mw, mh):
            self._combo.addItem(
                i18n.KO.WINDOW_SIZE_PRESET_ITEM_FMT.format(w=w, h=h),
                (w, h),
            )
        # 3) 마지막에 ‘사용자 지정’.
        self._combo.addItem(i18n.KO.WINDOW_SIZE_CUSTOM_LABEL, self._CUSTOM_KEY)
        form.addRow(QLabel(i18n.KO.WINDOW_SIZE_PRESET_LABEL), self._combo)

        # Custom 스핀박스 ---------------------------------------------------
        self._custom_row = QWidget(self)
        crow = QHBoxLayout(self._custom_row)
        crow.setContentsMargins(0, 0, 0, 0)
        self._spin_w = QSpinBox(self)
        self._spin_w.setRange(MIN_WIDTH, mw)
        self._spin_h = QSpinBox(self)
        self._spin_h.setRange(MIN_HEIGHT, mh)
        crow.addWidget(QLabel(i18n.KO.WINDOW_SIZE_WIDTH_LABEL))
        crow.addWidget(self._spin_w)
        crow.addSpacing(12)
        crow.addWidget(QLabel(i18n.KO.WINDOW_SIZE_HEIGHT_LABEL))
        crow.addWidget(self._spin_h)
        form.addRow("", self._custom_row)
        self._custom_row.setVisible(False)

        root.addLayout(form)

        # 전체 화면 ---------------------------------------------------------
        self._check_full = QCheckBox(i18n.KO.WINDOW_SIZE_FULLSCREEN_LABEL, self)
        self._check_full.setChecked(bool(current_fullscreen))
        root.addWidget(self._check_full)

        # OK / Cancel -------------------------------------------------------
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText(
            i18n.KO.WINDOW_SIZE_APPLY
        )
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText(
            i18n.KO.WINDOW_SIZE_CANCEL
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

        # 시그널 ------------------------------------------------------------
        self._combo.currentIndexChanged.connect(self._on_combo_changed)

        # 초기 선택값 적용 --------------------------------------------------
        self._apply_initial(current_width, current_height)

    # ------------------------------------------------------------------
    def _apply_initial(self, w: int, h: int) -> None:
        if w >= MIN_WIDTH and h >= MIN_HEIGHT:
            # 표준 목록에 일치하는 항목이 있으면 그걸로, 아니면 custom.
            for i in range(self._combo.count()):
                data = self._combo.itemData(i)
                if isinstance(data, tuple) and data == (w, h):
                    self._combo.setCurrentIndex(i)
                    return
            # custom 으로.
            idx = self._combo.findData(self._CUSTOM_KEY)
            if idx >= 0:
                self._combo.setCurrentIndex(idx)
            self._spin_w.setValue(w)
            self._spin_h.setValue(h)
        else:
            # 권장 기본값.
            dw, dh = suggest_default_size()
            self._spin_w.setValue(dw)
            self._spin_h.setValue(dh)

    # ------------------------------------------------------------------
    def _on_combo_changed(self, idx: int) -> None:
        data = self._combo.itemData(idx)
        self._custom_row.setVisible(data == self._CUSTOM_KEY)

    # ------------------------------------------------------------------
    def chosen(self) -> UserSizeChoice:
        """다이얼로그가 accepted 된 후 호출. 사용자 선택을 반환."""
        if self._check_full.isChecked():
            return UserSizeChoice(
                width=self._monitor_w,
                height=self._monitor_h,
                fullscreen=True,
            )
        data = self._combo.currentData()
        if data == self._MAX_KEY:
            return UserSizeChoice(self._monitor_w, self._monitor_h, False)
        if data == self._CUSTOM_KEY:
            return UserSizeChoice(
                int(self._spin_w.value()),
                int(self._spin_h.value()),
                False,
            )
        if isinstance(data, tuple):
            return UserSizeChoice(int(data[0]), int(data[1]), False)
        # fallback
        return UserSizeChoice(*suggest_default_size(), False)
