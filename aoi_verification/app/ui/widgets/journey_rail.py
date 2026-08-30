"""여정 레일 — 창 맨 위에 **늘 보이는** 5단계 진행 지도 (구조개편 1안-A).

왜 상시인가.  이 앱의 실패 비용은 '모르고 되돌아가 결정을 폐기' 하는 쪽에 있다.
수 분짜리 자동 매칭 뒤에 돌아온 사용자는 '지금 몇 번째인지 · 되돌아가면 무엇이
사라지는지' 를 기억으로 붙들어야 했고, 위치 단서는 각 화면 제목뿐이었으며
뒤로가기([← 설정으로])의 의미마저 화면마다 달랐다.  레일은 그 기억 부담을
화면으로 옮긴다 — 진행이 늘 보이고, 완료 단계는 눌러서 돌아간다.

설계 규칙(패널의 여정 큐와 같은 규약):

- ★ **단계 수만큼 위젯을 만들지 않는다.**  한 번의 ``paintEvent`` 로 전부 그리고,
  내용이 바뀔 때만 다시 그린다.  창 크기가 변할 때마다 위젯 5개를 재배치하는
  것보다 싸고, 레일 높이가 흔들리지 않는다.
- ★ 색은 **그리는 시점에** ``theme`` 에서 읽는다.  인스턴스 스타일시트에 색을
  구워 넣으면 다크 모드 전환(`_recolor_in_place`)이 옛 색을 남긴다.
- ★ 되돌아가기는 **레일이 직접 하지 않는다.**  각 화면이 이미 가진 확인 흐름
  (선별=결정 폐기 확인 / 매칭=계산 전부 폐기 확인)을 창이 호출한다.  레일은
  '어디로' 만 말한다 — 폐기 규칙이 두 곳에 생기면 그중 하나가 낡는다.
"""

from __future__ import annotations

from PyQt6.QtCore import (QEasingCurve, QPoint, QRect, Qt, QVariantAnimation,
                          pyqtSignal)
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QPainter, QPen,
                         QPolygon)
from PyQt6.QtWidgets import QWidget

from ... import i18n
from ...config import Fonts as _Fonts
from .. import theme

# QSS 의 `$font_mono` 는 CSS 폴백 목록이라 QFont(문자열)로 못 쓴다 — 목록만 떼어 낸다.
_MONO_FAMILIES = [s.strip().strip('"\'')
                  for s in _Fonts.MONO.split(",") if s.strip()]


class JourneyRail(QWidget):
    """5단계 여정 표시 + 완료 단계 클릭 복귀.

    높이 :data:`RAIL_H` 를 고정으로 점유한다 — 상시 가시성의 대가다(1366×768 에서
    검토 행 약 0.7 개분).  그 대신 각 화면의 [← 설정으로] 버튼이 사라진다.
    """

    #: 창 상단이 영구히 쓰는 높이.
    RAIL_H = 42
    #: 단계 표식(원) 지름.
    MARK_D = 20
    #: 워드마크와 첫 단계 사이 간격(시안: `margin-right:26px`).
    WORDMARK_GAP = 26
    #: 단계 사이 연결선 길이(시안: `flex:0 1 44px` + 양옆 9px).
    LINK_W = 44
    #: 좌우 여백(시안: `padding:9px 32px`).  `theme.PROFILE.page_margin` 과 같은 값이라
    #: 레일과 페이지 콘텐츠의 좌측 기준선이 한 줄로 맞는다.
    PAD_X = 32

    step_clicked = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("role", "journeyRail")
        self.setFixedHeight(self.RAIL_H)
        self._labels: tuple[str, ...] = tuple(i18n.KO.JOURNEY_RAIL_STEPS)
        self._index = 0
        self._navigable: frozenset[int] = frozenset()
        self._criteria = ""
        # 현재 단계로 **들어오는** 연결선이 얼마나 찼는지(0~1).
        # 21안-A '레일 선행 릴레이': 이 채워짐이 페이지 슬라이드보다 **먼저** 끝나야
        # '레일이 먼저 말하고 화면이 따라온다' 는 한 이야기가 된다.
        self._fill = 1.0
        self._fill_anim = None
        # 마지막 paint 가 남긴 단계별 클릭 영역 — 히트 테스트는 이걸 본다.
        self._hit: list[QRect] = []
        self.setMouseTracking(True)

    # ------------------------------------------------------------------
    def set_current(self, index: int, *, animate: bool = False) -> None:
        """현재 단계(0-based).

        ``animate=True`` 면 새 단계로 들어오는 연결선이 :data:`motion.DUR_RAIL_LEAD`
        동안 차오른다.  창은 그 시간이 지난 **뒤** 페이지 슬라이드를 시작한다 —
        두 모션이 겹치지 않아 동시 애니는 늘 1개이고, 이동의 원인(레일)과
        결과(화면)가 순서대로 읽힌다(21안-A)."""
        index = max(0, min(int(index), len(self._labels) - 1))
        if index == self._index:
            return
        forward = index > self._index
        self._index = index
        self._sync_cursor()
        if animate and forward:
            self._start_fill()
        else:
            # 뒤로 갈 때는 채우지 않는다 — 이미 지나온 길이라 차오를 것이 없다.
            if self._fill_anim is not None:
                self._fill_anim.stop()
            self._fill = 1.0
        self.update()

    def _start_fill(self) -> None:
        from .. import motion
        if not motion.enabled():
            self._fill = 1.0
            return
        anim = self._fill_anim
        if anim is None:
            # ★ 위젯당 하나만 만들어 재사용한다(motion.pulse 와 같은 규약).
            #   tick 은 자기 **부모**(this)만 건드리므로 파괴 순서가 안전하다.
            anim = QVariantAnimation(self)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            # ★ 채움은 **등속**이다(시안: `dsFill .14s linear`).  진행을 나타내는
            #   채움에 감속 곡선을 씌우면 '거의 다 찼는데 안 끝나는' 것처럼 읽힌다 —
            #   이 저장소가 결정형 진행바에 같은 이유로 등속을 쓴다.
            anim.setEasingCurve(QEasingCurve.Type.Linear)
            anim.valueChanged.connect(self._on_fill_tick)
            self._fill_anim = anim
        anim.stop()
        anim.setDuration(motion.DUR_RAIL_LEAD)
        self._fill = 0.0
        anim.start()

    def _on_fill_tick(self, value) -> None:
        self._fill = float(value)
        self.update()

    def set_navigable(self, indexes) -> None:
        """지금 화면에서 **되돌아갈 수 있는** 단계들.

        완료했다고 전부 갈 수 있는 것은 아니다 — 창이 실제로 가진 복귀 경로만
        넘긴다(없는 경로를 눌리게 두면 '눌러도 아무 일 없는' 죽은 클릭이 된다)."""
        new = frozenset(int(i) for i in (indexes or ()))
        if new == self._navigable:
            return
        self._navigable = new
        self._sync_cursor()
        self.update()

    def set_criteria(self, text: str) -> None:
        """오른쪽 끝의 판정 기준 한 줄(예: ``좌표 매칭 · 허용 오차 200 µm``)."""
        text = str(text or "")
        if text == self._criteria:
            return
        self._criteria = text
        self.update()

    # ------------------------------------------------------------------
    def _sync_cursor(self) -> None:
        if self._navigable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()

    def _step_at(self, pos: QPoint) -> int:
        for i, rect in enumerate(self._hit):
            if rect.contains(pos):
                return i
        return -1

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        i = self._step_at(event.position().toPoint())
        if i >= 0 and i in self._navigable:
            self.step_clicked.emit(i)
        return None

    def mouseMoveEvent(self, event):  # noqa: N802
        # 되돌아갈 수 있는 단계 위에서만 손가락 커서 — 나머지는 기본 커서다.
        i = self._step_at(event.position().toPoint())
        if i >= 0 and i in self._navigable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(i18n.KO.JOURNEY_RAIL_BACK_TOOLTIP_FMT.format(
                step=self._labels[i]))
        else:
            self.unsetCursor()
            self.setToolTip("")
        return super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.unsetCursor()
        self.setToolTip("")
        return super().leaveEvent(event)

    # ------------------------------------------------------------------
    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent, line, line2 = (QColor(theme.ACCENT), QColor(theme.LINE),
                               QColor(theme.LINE2))
        ink, ink2, mute = (QColor(theme.INK), QColor(theme.INK2),
                           QColor(theme.MUTE))
        on_accent = QColor(theme.ON_ACCENT)

        h, w, d = self.height(), self.width(), self.MARK_D
        cy = h // 2
        # 아래 경계선 — 레일이 콘텐츠와 같은 면으로 번지지 않게.
        p.setPen(QPen(line))
        p.drawLine(0, h - 1, w, h - 1)

        # ★ QFont 는 참조로 넘어간다 — 매번 복사본을 만들지 않으면 setBold 한 번이
        #   모든 글꼴에 번져 레일 전체가 굵어진다.
        base = self.font()
        cur_font = QFont(base)
        cur_font.setBold(True)
        oth_font = QFont(base)
        oth_font.setBold(False)
        fm_cur, fm_oth = QFontMetrics(cur_font), QFontMetrics(oth_font)

        # 오른쪽 판정 기준 — 먼저 자리를 떼어 놓고 남는 폭에 단계를 그린다.
        right_edge = w - self.PAD_X
        if self._criteria:
            # ★ 모노를 쓰지 않는다 — 이 줄은 한글과 'µ' 가 섞여 있고, 동봉 모노
            #   계열(JetBrains Mono·Consolas)에는 한글 글리프가 없어 PC 마다
            #   대체 글꼴이 달라진다(실측: 'µ' 가 두부로 나왔다).
            f = QFont(base)
            f.setBold(False)
            p.setFont(f)
            fm = QFontMetrics(f)
            cw = fm.horizontalAdvance(self._criteria)
            p.setPen(mute)
            p.drawText(QRect(w - self.PAD_X - cw, 0, cw, h - 1),
                       int(Qt.AlignmentFlag.AlignRight
                           | Qt.AlignmentFlag.AlignVCenter), self._criteria)
            right_edge = w - self.PAD_X - cw - 20

        # ★ 워드마크 — 시안 레일의 **첫 요소**다(세 레일 목업이 모두 갖고 있다).
        #   페이지 안의 로고 밴드는 사용자 요청으로 스크롤과 함께 밀려 올라가므로,
        #   스크롤한 뒤에는 화면 어디에도 앱 이름이 없었다.  레일은 고정이라 그
        #   자리를 대신한다(이미지가 아니라 글자라 로고 밴드를 되살리지 않는다).
        f = QFont(base)
        f.setBold(True)
        p.setFont(f)
        p.setPen(ink)
        mark_w = QFontMetrics(f).horizontalAdvance(i18n.KO.APP_TITLE)
        p.drawText(QRect(self.PAD_X, 0, mark_w, h - 1),
                   int(Qt.AlignmentFlag.AlignLeft
                       | Qt.AlignmentFlag.AlignVCenter), i18n.KO.APP_TITLE)

        self._hit = []
        x = self.PAD_X + mark_w + self.WORDMARK_GAP
        for i, text in enumerate(self._labels):
            done_step, current = i < self._index, i == self._index
            fm = fm_cur if current else fm_oth
            tw = fm.horizontalAdvance(text)
            mark = QRect(x, cy - d // 2, d, d)
            # 표식 — 완료=채운 원+체크 / 현재=2px 테두리+번호 / 예정=옅은 테두리+번호.
            if done_step:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(accent)
            else:
                pen = QPen(accent if current else line2)
                pen.setWidth(2 if current else 1)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(mark)
            if done_step:
                # ★ 체크는 **글자가 아니라 선**이다 — 동봉 폰트(NanumSquare)에
                #   U+2713 글리프가 없어 '✓' 는 두부(□)로 나온다.
                pen = QPen(on_accent)
                pen.setWidth(2)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                mx, my = mark.center().x() + 1, mark.center().y() + 1
                p.drawPolyline(QPolygon([QPoint(mx - 5, my - 1),
                                         QPoint(mx - 2, my + 2),
                                         QPoint(mx + 5, my - 5)]))
            else:
                f = QFont(base)
                f.setBold(True)
                f.setFamilies(_MONO_FAMILIES)
                f.setPointSizeF(max(7.0, base.pointSizeF() - 2.0)
                                if base.pointSizeF() > 0 else 8.0)
                p.setFont(f)
                p.setPen(accent if current else mute)
                p.drawText(mark, int(Qt.AlignmentFlag.AlignCenter), str(i + 1))
            # 이름 — 현재만 본문 잉크(굵게), 완료는 ink2(눌러서 갈 수 있는 곳),
            # 예정은 보조색.
            p.setFont(cur_font if current else oth_font)
            p.setPen(ink if current else (ink2 if done_step else mute))
            text_x = x + d + 7
            p.drawText(QRect(text_x, 0, tw, h - 1),
                       int(Qt.AlignmentFlag.AlignLeft
                           | Qt.AlignmentFlag.AlignVCenter), text)
            # 클릭 영역은 표식+이름 전체 — 작은 점만 노리게 하지 않는다.
            self._hit.append(QRect(x - 4, 0, d + 11 + tw, h - 1))
            # 되돌아갈 수 있는 완료 단계에는 밑줄로 '누를 수 있다'를 말한다
            # (색만으로 말하면 색각 이상에서 사라진다).
            if i in self._navigable:
                p.setPen(QPen(line))
                p.drawLine(text_x, cy + fm.ascent() // 2 + 3,
                           text_x + tw, cy + fm.ascent() // 2 + 3)
            x = text_x + tw
            if i < len(self._labels) - 1:
                # 연결선 — 지나온 구간만 accent.  남는 폭이 모자라면 줄인다.
                link = max(10, min(self.LINK_W,
                                   (right_edge - x) // max(1, len(self._labels) - i)))
                x0, x1 = x + 9, x + link - 9
                pen = QPen(line2)
                pen.setWidth(1)
                p.setPen(pen)
                p.drawLine(x0, cy, x1, cy)          # 바탕(아직 안 지난 길)
                # 현재 단계로 **들어오는** 구간만 부분적으로 찬다 — 그 채워짐이
                # 끝나야 페이지가 움직인다(21안-A).  지나온 구간은 통째로 accent.
                if i < self._index:
                    frac = self._fill if i == self._index - 1 else 1.0
                    if frac > 0:
                        pen = QPen(accent)
                        pen.setWidth(1)
                        p.setPen(pen)
                        p.drawLine(x0, cy, int(x0 + (x1 - x0) * frac), cy)
                x += link
