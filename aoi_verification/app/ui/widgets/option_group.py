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

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
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

    ★ 열 수가 그대로면 **아무것도 하지 않는다**.  이전엔 리사이즈마다 전부 떼고 다시
    붙여서, 60px 드래그 한 번에 재조립이 119회 일어났다(실측 지적).
    """
    if not widgets:
        return 0
    spacing = max(0, grid.spacing())
    min_w = max(1, int(min_item_w))
    avail = int(available_w) if available_w and available_w > 1 else min_w
    # ★ n 열에 필요한 폭은 n*min_w + (n-1)*spacing 이다 — `avail // (min_w + spacing)` 은
    #   **마지막 열에도** 간격을 물려 열을 하나 덜 준다(예: 736px / 360px 최소 → 2열이
    #   들어가는데 1열로 계산).  간격 하나를 더해 보정한다.
    cols = max(1, min(len(widgets), (avail + spacing) // (min_w + spacing)))
    # 이미 그 열 수로 그 위젯들이 배치돼 있으면 그대로 둔다.
    if grid.count() == len(widgets) and _grid_cols(grid) == cols:
        return cols
    # 기존 배치를 떼어낸다(삭제하지 않고 재사용 — 상태·연결 보존).
    while grid.count():
        grid.takeAt(0)
    for i, w in enumerate(widgets):
        grid.addWidget(w, i // cols, i % cols)
    for c in range(cols):
        grid.setColumnStretch(c, 1)
    grid.setColumnStretch(cols, 0)
    return cols


def _grid_cols(grid: QGridLayout) -> int:
    """현재 그리드에 실제로 쓰인 열 수(재조립이 필요한지 판단용)."""
    cols = 0
    for i in range(grid.count()):
        r, c, rs, cs = grid.getItemPosition(i)
        cols = max(cols, c + 1)
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
                 fixed_cols: int = 0,
                 role: str = "option",
                 activate_on_arrow: bool = False,
                 parent: Optional[QWidget] = None) -> None:
        """``fixed_cols`` 를 주면 폭에 상관없이 그 열 수로 고정한다(한 줄 세그먼트 등).

        ``role`` — QSS 등급.  ``"option"``(기본, 44px 타일) 또는 ``"segment"``(34px
        축소판).  세그먼트는 **이진 선택**용이다: 값이 둘뿐인 설정에 전체폭 타일 두 장을
        쓰면 화면의 한 덩어리를 매번 바꾸지도 않는 값에 내주게 된다.  타일 크기는 고르는
        값의 수가 아니라 그 선택의 무게를 따른다.

        ``activate_on_arrow`` — 방향키로 선택까지 확정할지.  **기본이 ``False``** 다:
        방향키는 **포커스만** 옮기고 확정은 Space/Enter 로 한다.

        왜 기본이 False 인가 — 이 위젯의 선택은 **부수효과를 가진다**.  배치·색 모드는
        페이지 재생성(입력 이관·포커스 소실)이고, 진행 범위 'subset' 은 **모달 다이얼로그**
        를 띄운다.  실측: 진행 범위 타일에 → 키 한 번으로 "먼저 기준 폴더를 선택하세요"
        경고창이 떠 테스트 하네스가 블로킹됐다.  방향키로 목록을 훑는 것은 탐색이지
        실행이 아니므로, 부수효과가 **없는** 그룹에서만 ``True`` 를 켜라.
        """
        super().__init__(parent)
        self._keys: list[str] = []
        self._buttons: dict[str, QPushButton] = {}
        self._min_tile_w = int(min_tile_w)
        self._fixed_cols = int(fixed_cols)
        self._role = role
        self._activate_on_arrow = bool(activate_on_arrow)
        self._last_cols = 0

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        # 세그먼트는 '한 컨트롤'로 읽혀야 하므로 간격을 좁힌다(타일은 별개 카드처럼 넓게).
        self._grid.setSpacing(4 if role == "segment" else 8)

        # 명시적 배타 그룹 — 부모를 공유해 '우연히' 배타가 되는 상태에 의존하지 않는다.
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for key, label in options:
            btn = NeonButton(label, role=role, parent=self)
            btn.setCheckable(True)
            btn.setObjectName(f"opt_{key}")
            btn.setAccessibleName(label)
            if role == "option":
                btn.setMinimumHeight(theme.PROFILE.control_h_lg)
            # ★ 세그먼트 높이는 **QSS 가 정한다**(min-height 24 + 패딩/보더 = 34).
            #   여기서 setMinimumHeight 를 부르지 않는 이유: QSS `min-height` 가
            #   `setMinimumHeight()` 를 덮어써 '주장한 높이'와 '렌더된 높이'가 갈린다
            #   ([검증 시작]이 46 을 주장하고 40 으로 렌더된 적이 있다).  출처를 하나로.
            btn.setSizePolicy(QSizePolicy.Policy.Expanding,
                              QSizePolicy.Policy.Fixed)
            btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            # ★ 방향키를 **타일에서** 가로챈다.  `QAbstractButton` 은 autoExclusive 일 때
            #   방향키를 스스로 처리해 `click()` 까지 호출하므로, 컨테이너의
            #   keyPressEvent 만 믿으면 Qt 버전·배타 설정에 따라 우회될 수 있다.
            #   여기서 먼저 소비하면 어떤 경우에도 계약이 지켜진다.
            btn.setAutoExclusive(False)      # 배타는 QButtonGroup 이 담당한다
            btn.installEventFilter(self)
            btn.clicked.connect(lambda _c=False, k=key: self._on_clicked(k))
            self._group.addButton(btn)
            self._keys.append(key)
            self._buttons[key] = btn

        if self._keys:
            self.set_current_key(current if current in self._buttons
                                 else self._keys[0])
        self._equalize_segments()
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
            # 라벨이 길어지면 균등 폭을 다시 잡는다('일부 슬롯만…' → '일부 슬롯 (3/12)').
            self._equalize_segments()

    def _equalize_segments(self) -> None:
        """세그먼트를 **서로 같은 폭**으로 — 짝이 하나의 컨트롤로 읽히게.

        내용 폭에 맡기면 '모든 슬롯'(82px)과 '일부 슬롯만…'(106px)처럼 들쭉날쭉해져
        두 조각이 각자 다른 버튼처럼 보인다.  가장 넓은 조각에 맞추고 ``min_tile_w``
        를 하한으로 둔다(짧은 라벨이 손가락에 너무 좁아지지 않게).
        """
        if self._role != "segment" or not self._buttons:
            return
        need = max(b.sizeHint().width() for b in self._buttons.values())
        need = max(need, self._min_tile_w)
        for b in self._buttons.values():
            b.setMinimumWidth(need)

    def set_option_tooltip(self, key: str, text: str) -> None:
        btn = self._buttons.get(key)
        if btn is not None:
            btn.setToolTip(text)

    # ------------------------------------------------------------------
    def _on_clicked(self, key: str) -> None:
        self.selection_changed.emit(key)

    def _reflow(self) -> None:
        widgets = [self._buttons[k] for k in self._keys]
        if self._fixed_cols > 0:
            # 폭 계산 없이 고정 열 — 한 줄 칩 그룹이 좁은 부모에서 세로로 접히지 않게.
            big = self._fixed_cols * (self._min_tile_w + self._grid.spacing()) + 1
            self._last_cols = reflow_into_grid(self._grid, widgets, big,
                                               self._min_tile_w)
            return
        avail = self.width() if self.width() > 1 else 0
        if not avail:
            p = self.parentWidget()
            avail = p.width() if p is not None else 0
        self._last_cols = reflow_into_grid(self._grid, widgets, avail,
                                           self._min_tile_w)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._reflow()

    _ARROWS = (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Up)

    def eventFilter(self, obj, event):  # noqa: N802
        """타일에 온 방향키를 **Qt 버튼 처리보다 먼저** 소비한다(위 주석 참조)."""
        if (event.type() == QEvent.Type.KeyPress
                and obj in self._buttons.values()
                and event.key() in self._ARROWS):
            self._move(+1 if event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down)
                       else -1)
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):  # noqa: N802
        """←→↑↓ — 포커스 이동(+``activate_on_arrow`` 면 선택까지)."""
        key = event.key()
        step = 0
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            step = +1
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            step = -1
        if step and self._keys:
            self._move(step)
            event.accept()
            return
        super().keyPressEvent(event)

    def _move(self, step: int) -> None:
        """방향키 이동의 유일한 구현 — 컨테이너와 타일 필터가 공유한다."""
        if not self._keys:
            return
        # 기준점은 '지금 포커스가 있는 타일' — 없으면 선택된 타일.
        focused = next((k for k, b in self._buttons.items() if b.hasFocus()), "")
        base = focused or self.current_key()
        idx = self._keys.index(base) if base in self._keys else 0
        idx = max(0, min(idx + step, len(self._keys) - 1))
        new_key = self._keys[idx]
        # ★ activate_on_arrow=False(기본) 면 **포커스만** 옮긴다.  이 위젯의 선택은
        #   부수효과를 가진다(페이지 재생성·모달) — 훑는 것이 실행이 되어선 안 된다.
        if self._activate_on_arrow and new_key != base:
            self.set_current_key(new_key, emit=True)
        btn = self._buttons.get(new_key)
        if btn is not None:
            btn.setFocus(Qt.FocusReason.TabFocusReason)
