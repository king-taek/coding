"""상호배타 선택을 '타일 버튼 줄'로 — 작은 QRadioButton 대체.

집안 관습(CLAUDE.md): **클릭 대상은 크고 명확하게.**  작은 기본 컨트롤 대신 타일/카드
전체가 클릭영역인 토글을 쓴다.

설계 원칙 두 가지가 중요하다:

1. **선택 상태는 QSS 가 칠한다 — 인라인 스타일시트 금지.**
   ``style.qss`` 의 ``QPushButton[role="option"]:checked`` 가 토큰(``$accent_tint`` 등)으로
   칠하므로 라이트/다크 어느 팔레트에서도 자동으로 맞는다.  기존
   ``_SlotTile``/``_SelectTile`` 은 선택 틴트를 죽은 네온 팔레트로 하드코딩해 두었는데
   (``rgba(57,255,20,…)``), 같은 실수를 반복하지 않기 위한 규칙이다.
2. **열 수는 가용 폭에서 계산한다** — 가로 스크롤이 생기지 않게(800×600 지원).
   위젯은 take/re-add 만 하고 재생성하지 않아 선택 상태·시그널 연결이 살아 있다.
"""

from __future__ import annotations

from typing import Optional, Sequence

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QButtonGroup, QGridLayout, QPushButton, QSizePolicy,
                             QWidget)

from .. import theme
from .neon_button import NeonButton

# 타일 최소 폭 기본값 — 이 폭을 밑돌면 열을 줄인다(가로 넘침 방지).
DEFAULT_MIN_TILE_W = 200


def reflow_into_grid(grid: QGridLayout, widgets: Sequence[QWidget],
                     available_w: int, min_item_w: int) -> int:
    """가로 스크롤이 나지 않는 열 수를 계산해 재배치하고 열 수를 반환.

    순수 계산 + 레이아웃 조작만 하는 헬퍼(위젯 재생성 없음) — 헤드리스 테스트 가능.
    """
    if not widgets:
        return 0
    spacing = max(0, grid.spacing())
    item_w = max(1, int(min_item_w) + spacing)
    avail = int(available_w) if available_w and available_w > 1 else item_w
    cols = max(1, min(len(widgets), avail // item_w))
    # 기존 배치를 떼어낸다(삭제하지 않고 재사용 — 상태·연결 보존).
    while grid.count():
        grid.takeAt(0)
    for i, w in enumerate(widgets):
        grid.addWidget(w, i // cols, i % cols)
    for c in range(cols):
        grid.setColumnStretch(c, 1)
    grid.setColumnStretch(cols, 0)
    return cols


class OptionGroup(QWidget):
    """키-라벨 쌍을 배타 선택 타일 줄로 표시한다.

    ``options`` 는 ``[(key, label), ...]``.  ``key`` 는 호출부의 도메인 값을 그대로 쓸 수
    있어(예: ``AutomationLevel.AUTO_ALL``) 선택 결과를 분기 없이 읽는다.
    """

    selection_changed = pyqtSignal(str)          # 선택된 key

    def __init__(self, options: Sequence[tuple[str, str]], *,
                 current: str = "",
                 min_tile_w: int = DEFAULT_MIN_TILE_W,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._keys: list[str] = []
        self._buttons: dict[str, QPushButton] = {}
        self._min_tile_w = int(min_tile_w)
        self._last_cols = 0

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(8)

        # 명시적 배타 그룹 — 부모를 공유해 '우연히' 배타가 되는 상태에 의존하지 않는다.
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for key, label in options:
            btn = NeonButton(label, role="option", parent=self)
            btn.setCheckable(True)
            btn.setObjectName(f"opt_{key}")
            btn.setAccessibleName(label)
            btn.setMinimumHeight(theme.PROFILE.control_h_lg)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding,
                              QSizePolicy.Policy.Fixed)
            btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            btn.clicked.connect(lambda _c=False, k=key: self._on_clicked(k))
            self._group.addButton(btn)
            self._keys.append(key)
            self._buttons[key] = btn

        if self._keys:
            self.set_current_key(current if current in self._buttons
                                 else self._keys[0])
        self._reflow()

    # ------------------------------------------------------------------
    def keys(self) -> tuple[str, ...]:
        return tuple(self._keys)

    def button(self, key: str) -> Optional[QPushButton]:
        """테스트·캡처용 접근자."""
        return self._buttons.get(key)

    def current_key(self) -> str:
        for key, btn in self._buttons.items():
            if btn.isChecked():
                return key
        return ""

    def set_current_key(self, key: str, *, emit: bool = False) -> None:
        """선택을 바꾼다.  ``emit=False``(기본)면 시그널을 내지 않는다.

        복원(prefs·apply_state)에는 ``emit=False`` 를 써서 저장 루프를 만들지 않는다.
        """
        btn = self._buttons.get(key)
        if btn is None:
            return
        if not btn.isChecked():
            btn.setChecked(True)
        if emit:
            self.selection_changed.emit(key)

    def set_option_label(self, key: str, text: str) -> None:
        """타일 라벨 교체 — 상태를 옆 라벨이 아니라 **컨트롤 자신**이 말하게 한다."""
        btn = self._buttons.get(key)
        if btn is not None:
            btn.setText(text)
            btn.setAccessibleName(text)

    def set_option_tooltip(self, key: str, text: str) -> None:
        btn = self._buttons.get(key)
        if btn is not None:
            btn.setToolTip(text)

    # ------------------------------------------------------------------
    def _on_clicked(self, key: str) -> None:
        self.selection_changed.emit(key)

    def _reflow(self) -> None:
        widgets = [self._buttons[k] for k in self._keys]
        avail = self.width() if self.width() > 1 else 0
        if not avail:
            p = self.parentWidget()
            avail = p.width() if p is not None else 0
        self._last_cols = reflow_into_grid(self._grid, widgets, avail,
                                           self._min_tile_w)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._reflow()

    def keyPressEvent(self, event):  # noqa: N802
        """←→↑↓ 로 선택+포커스 이동 — 라디오 그룹과 같은 감각."""
        key = event.key()
        step = 0
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            step = +1
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            step = -1
        if step and self._keys:
            cur = self.current_key()
            idx = self._keys.index(cur) if cur in self._keys else 0
            idx = max(0, min(idx + step, len(self._keys) - 1))
            new_key = self._keys[idx]
            if new_key != cur:
                self.set_current_key(new_key, emit=True)
            btn = self._buttons.get(new_key)
            if btn is not None:
                btn.setFocus(Qt.FocusReason.TabFocusReason)
            event.accept()
            return
        super().keyPressEvent(event)
