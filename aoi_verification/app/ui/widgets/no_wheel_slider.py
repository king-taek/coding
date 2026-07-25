"""마우스 휠로 값이 바뀌지 않는 **슬라이더·스핀박스** + 실제로 보이는 포커스 링.

드래그/클릭/키보드 입력은 정상 동작하고, 휠 이벤트만 무시한다.  스크롤 영역
안에 두어도 휠은 슬라이더가 아니라 스크롤로 전달된다.

포커스 링을 왜 여기서 직접 그리는가:

- Qt 스타일시트는 슬라이더 **서브컨트롤**에 ``:focus`` 를 지원하지 않는다
  (``QSlider::handle:horizontal:focus`` 는 무시된다 — 포커스는 위젯 상태이지
  서브컨트롤 상태가 아니다).
- 위젯 자체에 ``QSlider:focus { border: … }`` 를 줘도 서브컨트롤이 스타일링된
  슬라이더에서는 렌더되지 않는다(실측).
- 핸들 색만 바꾸는 방식도 못 쓴다: 핸들 왼쪽은 채운 트랙(``accent``)이라
  포커스색이 1.23:1 로 묻힌다 — 채운 primary 버튼에서 겪은 것과 같은 함정.

그래서 ``paintEvent`` 뒤에 링을 한 겹 덧그린다.  링은 위젯 테두리를 따라가므로
**카드 면**(``panel``) 위에 앉고, 거기서 ``focus`` 는 8.0(라이트)/10.1(다크):1 이다.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QDoubleSpinBox, QSlider

from .. import theme


class NoWheelSlider(QSlider):
    """휠 이벤트를 무시하고, 포커스 시 위젯 테두리에 링을 그리는 ``QSlider``."""

    def wheelEvent(self, event):  # noqa: N802
        event.ignore()

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        if not self.hasFocus():
            return
        w = max(0, theme.PROFILE.focus_ring_px)
        if not w:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(theme.FOCUS))
        pen.setWidth(w)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        half = w / 2.0
        r = theme.PROFILE.radius_sm
        p.drawRoundedRect(
            QRectF(half, half, self.width() - w, self.height() - w), r, r)
        p.end()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """휠로 값이 바뀌지 않는 ``QDoubleSpinBox``.

    ★ 왜 필요한가: 기본 스핀박스는 **포커스가 없어도** 커서가 위에 있으면 휠로 값이
    바뀐다.  허용 오차는 판정 기준이라, 스크롤 영역 안에서 화면을 훑으려고 굴린 휠
    3노치가 500 → 350 µm 로 바꾸고 그것이 배지·prefs·실제 검증 런까지 따라갔다(실측).
    `SwitchRow` 의 스크롤 제스처 문제와 같은 계열 — 훑는 동작이 설정을 바꿔선 안 된다.

    키보드 ↑↓ · 타이핑 · ±  버튼은 그대로 동작한다.
    """

    def wheelEvent(self, event):  # noqa: N802
        event.ignore()
