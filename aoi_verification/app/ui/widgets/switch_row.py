"""제목 + 설명 + 토글 스위치가 한 줄인 컨트롤 — 켜짐/꺼짐이 한눈에 보이는 on/off.

왜 커스텀 페인트인가: 기본 `QCheckBox` 는 상태가 작은 사각형 하나에만 담겨,
'지금 켜져 있나?'를 멀리서 못 읽는다.  이 위젯은 **행 전체가 클릭영역**(집안 관습)이고
노브 위치·트랙 색으로 상태를 크게 말한다.

색은 전부 ``theme`` 토큰에서 읽으므로 라이트/다크 팔레트를 자동으로 따른다.
노브 이동은 ``motion`` 을 거치므로 '모션 줄이기'·헤드리스에서는 즉시 스냅한다.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import (QEasingCurve, QEvent, QRectF, QSize, Qt,
                          QVariantAnimation, pyqtSignal)
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout,
                             QWidget)

from .. import theme

_TRACK_W = 48
_TRACK_H = 28
_KNOB_M = 3                      # 트랙 안쪽 여백
# 행 전체가 클릭영역이므로 **행 높이**가 실제 타깃이다 — 설명 없는 스위치(어두운 화면)
# 에서도 WCAG 2.5.8(AA, 24px)을 넉넉히 넘기게 하한을 둔다.
_ROW_MIN_H = 34


class ToggleSwitch(QWidget):
    """접근성 있는 on/off 스위치(트랙 + 노브).  라벨은 :class:`SwitchRow` 가 담당."""

    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._checked = bool(checked)
        self._pos = 1.0 if self._checked else 0.0     # 0=off, 1=on (노브 위치)
        self._pressed = False
        self._anim: Optional[QVariantAnimation] = None
        self.setFixedSize(QSize(_TRACK_W, _TRACK_H))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    # ------------------------------------------------------------------
    def is_on(self) -> bool:
        return self._checked

    def set_on(self, on: bool, *, emit: bool = False, animate: bool = True) -> None:
        on = bool(on)
        if on == self._checked:
            return
        self._checked = on
        self._animate_to(1.0 if on else 0.0, animate=animate)
        if emit:
            self.toggled.emit(on)

    def _animate_to(self, target: float, *, animate: bool = True) -> None:
        from .. import motion
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        if not animate or not motion.enabled():
            self._pos = target
            self.update()
            return
        anim = QVariantAnimation(self)
        anim.setStartValue(float(self._pos))
        anim.setEndValue(float(target))
        anim.setDuration(motion.dur(140))
        anim.setEasingCurve(QEasingCurve.Type.OutQuart)
        anim.valueChanged.connect(self._on_tween)
        anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
        self._anim = anim

    def _on_tween(self, v) -> None:
        self._pos = float(v)
        self.update()

    def _toggle(self) -> None:
        self.set_on(not self._checked, emit=True)

    # ------------------------------------------------------------------
    # ★ press 에서 토글하지 않는다 — 버튼처럼 **release 에서** 토글한다.
    #   press 즉시 토글하면 터치 패널에서 스위치에 손가락을 얹고 미는 **스크롤 제스처**가
    #   판정 엔진을 바꾼다(취소할 방법도 없다).  press 로 눌림만 표시하고, 커서가 위젯
    #   안에 있을 때만 release 에서 실행한다 — 밖으로 끌어내면 취소된다.
    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._pressed = True
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            self.update()
            if self.isEnabled() and self.rect().contains(event.position().toPoint()):
                self._toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.isEnabled():
                self._toggle()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = h / 2.0
        on = self._pos

        enabled = self.isEnabled()
        # 트랙 — off 는 중립 면, on 은 강조색. 비활성은 채도를 뺀다.
        # ★ off 트랙을 LINE2 로 칠하면 시트 면에서 1.5~1.7:1 밖에 안 돼 '꺼진 스위치가
        #   안 보인다'(WCAG 1.4.11 실패, 실측).  LINE_STRONG 으로 3:1 을 확보한다.
        if enabled:
            off_c = QColor(theme.LINE_STRONG)
            on_c = QColor(theme.ACCENT)
        else:
            off_c = QColor(theme.LINE)
            on_c = QColor(theme.LINE)
        track = QColor(
            int(off_c.red() + (on_c.red() - off_c.red()) * on),
            int(off_c.green() + (on_c.green() - off_c.green()) * on),
            int(off_c.blue() + (on_c.blue() - off_c.blue()) * on),
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, w, h), r, r)

        # 포커스 링 — 키보드 사용자가 지금 어디 있는지 보이게.
        # ★ 링을 **트랙 안쪽**에 `FOCUS` 로 그리면 안 된다: ON 일 때 트랙이 `ACCENT` 인데
        #   `FOCUS` vs `ACCENT` 는 1.23:1(라이트)/1.30(다크) 이라 링이 자기 트랙에
        #   묻힌다 — 채운 primary 버튼에서 고친 것과 **같은 함정**이 커스텀 페인트
        #   위젯에 남아 있었다.  링 색을 트랙 상태에 맞춰 고른다.
        if self.hasFocus():
            from PyQt6.QtGui import QPen
            #   링은 **트랙 위에** 그려지므로 두 트랙 색(OFF=line_strong, ON=accent)
            #   모두와 대비되는 하나의 색을 쓴다: ON_ACCENT — OFF 3.59/6.24,
            #   ON 6.50/8.57(실측).  FOCUS 는 OFF 트랙에서 1.78(다크)로 묻힌다.
            ring = QColor(theme.ON_ACCENT)
            p.setBrush(Qt.BrushStyle.NoBrush)
            fp = QPen(ring)
            fp.setWidth(2)
            p.setPen(fp)
            p.drawRoundedRect(QRectF(2, 2, w - 4, h - 4), r - 2, r - 2)
            p.setPen(Qt.PenStyle.NoPen)

        # 노브
        knob_d = h - 2 * _KNOB_M
        x = _KNOB_M + on * (w - knob_d - 2 * _KNOB_M)
        p.setBrush(QColor(theme.ON_ACCENT if enabled else theme.ELEV))
        p.drawEllipse(QRectF(x, _KNOB_M, knob_d, knob_d))
        p.end()


class SwitchRow(QWidget):
    """제목(+설명) + 스위치가 한 줄.  **제목과 스위치만** 클릭영역이다.

    왜 '행 전체'가 아닌가 — 집안 관습(CLAUDE.md)의 "타일/카드 전체가 클릭영역"은
    **배타 선택 타일**을 위한 것이다.  이 위젯은 판정 엔진을 바꾸는 스위치라 성격이
    다르다.  넓은 카드 안에서 행 전체를 클릭영역으로 두면 1300px 짜리 **보이지 않는
    핫존**이 생기고, 시각 어포던스(48×28 토글)는 반대쪽 끝에 있다.  설명을 읽으려고
    빈 곳을 누르는 것만으로 판정 기준이 바뀌면, 고쳐 낸 '펼치기 = 켜기' 버그가
    '누르기 = 켜기' 로 옷만 갈아입은 셈이다.  그래서:

    - 클릭영역 = **제목 + 스위치**(설명·빈 여백 제외).
    - 실행은 **release 에서**, 커서가 안에 있을 때만(드래그로 취소 가능).
    """

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, *, description: str = "",
                 checked: bool = False,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(_ROW_MIN_H)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        text_host = QWidget(self)
        # 맨 QWidget 은 전역 `QWidget { background: $bg }` 를 물려받아 카드 면 위에
        # 색이 다른 띠로 보인다 — 로딩 패널·행 호스트와 같은 함정.
        text_host.setProperty("role", "switchRowText")
        col = QVBoxLayout(text_host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self._title = QLabel(title, text_host)
        self._title.setProperty("role", "cardTitle")
        # 제목만 클릭영역 — 손가락 커서도 여기에만 준다(핫존과 어포던스 일치).
        self._title.setCursor(Qt.CursorShape.PointingHandCursor)
        self._title.installEventFilter(self)
        col.addWidget(self._title)
        self._desc: Optional[QLabel] = None
        if description:
            self._desc = QLabel(description, text_host)
            self._desc.setProperty("role", "muted")
            self._desc.setWordWrap(True)
            col.addWidget(self._desc)
        # 제목이 자기 글자 폭만 차지하게 — 여백까지 늘어나면 다시 넓은 핫존이 된다.
        self._title.setSizePolicy(QSizePolicy.Policy.Maximum,
                                  QSizePolicy.Policy.Preferred)
        row.addWidget(text_host, stretch=1)

        self.switch = ToggleSwitch(checked, parent=self)
        self.switch.toggled.connect(self.toggled.emit)
        row.addWidget(self.switch, alignment=Qt.AlignmentFlag.AlignVCenter)

    # ------------------------------------------------------------------
    def is_on(self) -> bool:
        return self.switch.is_on()

    def set_on(self, on: bool, *, emit: bool = False) -> None:
        self.switch.set_on(on, emit=emit)

    def set_description(self, text: str) -> None:
        if self._desc is not None:
            self._desc.setText(text)

    def eventFilter(self, obj, event):  # noqa: N802
        """제목 라벨 클릭 = 토글(release 기준, 라벨 밖으로 끌면 취소)."""
        if obj is self._title and self.isEnabled():
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._label_pressed = True
                    return True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                if getattr(self, "_label_pressed", False):
                    self._label_pressed = False
                    if self._title.rect().contains(event.position().toPoint()):
                        self.switch._toggle()
                    return True
        return super().eventFilter(obj, event)
