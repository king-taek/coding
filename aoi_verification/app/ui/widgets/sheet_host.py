"""앱 **내부** 창(시트) — 별도 OS 창으로 뜨던 모든 팝업을 프로그램 안에서 띄운다.

사용자 요청: "켜지는 모든 팝업은 새로운 창이 아니라 프로그램 내에서 켜지는 창으로".
바꾼 것과 남긴 것:

- 바꿈: ``QMessageBox`` 정적 호출(확인·경고·오류·질문) 39곳, ``QDialog.exec()`` 20곳.
- **남김**: ``QFileDialog``.  폴더/파일 선택은 앱의 팝업이 아니라 **OS 파일 브라우저**다.
  앱 안에 다시 만들면 네트워크 경로·경로 직접 입력·최근 위치를 잃는다(사용자 결정).

설계 규칙(어긋나면 앱이 멈추거나 죽는다):

1. **동기 의미를 유지한다.**  호출부는 ``if dlg.exec() == Accepted:`` 처럼 결과를 그
   자리에서 쓴다.  그래서 :func:`run` 은 중첩 ``QEventLoop`` 를 돌려 ``exec()`` 과 같은
   의미를 준다 — 호출부를 콜백으로 뒤집지 않는다(뒤집으면 59곳이 전부 위험한 변경이 된다).
2. **폴백을 반드시 남긴다.**  호스트(메인 창)를 못 찾으면 네이티브 ``QMessageBox``/
   ``exec()`` 로 돌아간다.  초기화 중 팝업·단위 테스트·창 밖 컨텍스트에서도 앱이 계속
   동작해야 한다.  구조 변경이 '아무것도 안 뜨는' 경로를 만들면 안 된다.
3. **시트는 쌓인다.**  다이얼로그 안에서 다이얼로그를 여는 곳이 5곳 있다(후보 뷰어 등).
   스택으로 관리하고 스크림은 맨 아래 한 겹만 그린다.
4. **입력은 맨 위 시트만 받는다.**  뒤 페이지를 ``setEnabled(False)`` 로 죽이지 않는다 —
   비활성 QSS(점선 테두리·회색 글자)가 반투명 스크림 아래로 비쳐 '고장난 화면'이 된다.
   대신 오버레이가 마우스를 막고, 앱 이벤트 필터가 시트 밖으로 가는 키를 버린다.
5. **애니메이션 소유권 규칙**(``motion`` 모듈 주석과 동일): 부모 있는
   ``QPropertyAnimation`` 만 쓰고 ``DeleteWhenStopped`` 는 쓰지 않는다.  tick 이 죽은
   객체로 들어가면 파이썬 예외가 아니라 세그폴트다(전례 3건).
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import (QEvent, QEventLoop, QPoint, QPropertyAnimation, QSize,
                          Qt, QVariantAnimation, pyqtSignal)
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (QApplication, QDialog, QGraphicsOpacityEffect,
                             QGridLayout, QHBoxLayout, QLabel, QMessageBox,
                             QSizePolicy, QVBoxLayout, QWidget)

from ... import i18n
from .. import motion, theme
from .neon_button import NeonButton

# 시트가 창 안에서 차지할 최대 비율 — 뷰어는 거의 꽉, 메시지는 내용만큼.
_MARGIN_PX = 24
_VIEWER_MARGIN_PX = 8
_MSG_MIN_W = 360
_MSG_MAX_W = 560
_SHEET_MIN_W = 280       # sizeHint 가 유효하지 않을 때의 하한(0×0 시트 방지)
_SHEET_MIN_H = 140


class _Scrim(QWidget):
    """시트 뒤를 덮는 반투명 막 — 로딩 오버레이와 같은 색(``theme.SCRIM_RGBA``).

    알파에 ``_fade`` 를 곱해 그린다(``widgets/loading_overlay.py`` 의 스크림과 같은
    방식).  들어올 때는 그 값을 0→1 로 트윈해 어둠이 **슬램하지 않게** 하고, 나갈
    때는 트윈하지 않는다 — 퇴장은 :meth:`SheetHost._close` 가 닫는 그 프레임에
    ``hide()`` 로 끝낸다(사용자 결정: 팝업이 사라지는 순간 화면이 같이 밝아진다).
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAutoFillBackground(False)
        self._fade = 1.0
        self._fade_anim: Optional[QVariantAnimation] = None

    def fade_in(self) -> None:
        """어둠을 0→1 로 트윈한다.  모션이 꺼져 있으면 즉시 최대 어둠."""
        if not motion.enabled():
            self._set_fade(1.0)
            return
        anim = self._fade_anim
        if anim is None:
            # 열 때마다 새로 만들면 자식 애니메이션이 세션 내내 쌓인다 — 하나를
            # 재사용한다.  tick 이 건드리는 것은 자기 **부모**(이 스크림)뿐이다.
            anim = QVariantAnimation(self)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(motion.EASE_PRIMARY)
            anim.valueChanged.connect(self._set_fade)
            self._fade_anim = anim
        anim.stop()
        anim.setDuration(motion.DUR_SHEET // 2)
        self._set_fade(0.0)
        anim.start()

    def _set_fade(self, value) -> None:
        self._fade = max(0.0, min(1.0, float(value)))
        self.update()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        r, g, b, a = theme.SCRIM_RGBA
        p.fillRect(self.rect(), QColor(r, g, b, int(a * self._fade)))


class _SheetFrame(QWidget):
    """시트 껍데기 — **제목줄(제목 + ✕)** 과 테두리.

    창 안으로 들어오면 OS 타이틀바가 없다.  그것이 하던 두 가지(무슨 팝업인지 알려주기,
    닫기)를 대신하지 않으면 '정체를 모르고 닫을 수도 없는' 시트가 된다 — 특히 자체 닫기
    버튼이 없는 뷰어에서 치명적이다."""

    close_requested = pyqtSignal()

    def __init__(self, parent: QWidget, title: str) -> None:
        super().__init__(parent)
        self.setProperty("role", "sheet")
        v = QVBoxLayout(self)
        v.setContentsMargins(1, 1, 1, 1)     # 테두리 1px 만 남기고 내용은 꽉
        v.setSpacing(0)

        bar = QWidget(self)
        bar.setProperty("role", "sheetBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 8, 8, 8)
        h.setSpacing(8)
        lbl = QLabel(title, bar)
        lbl.setProperty("role", "sheetTitle")
        h.addWidget(lbl)
        h.addStretch(1)
        btn = NeonButton(i18n.KO.MSG_BTN_CLOSE, role="rowAction", parent=bar)
        btn.setToolTip(i18n.KO.MSG_BTN_CLOSE)
        btn.clicked.connect(self.close_requested.emit)
        h.addWidget(btn)
        v.addWidget(bar)

        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, 0, 0, 0)
        v.addLayout(self._body, 1)

    def set_content(self, w: QWidget) -> None:
        w.setParent(self)
        w.setWindowFlags(Qt.WindowType.Widget)
        self._body.addWidget(w)
        self._content = w

    def sizeHint(self) -> QSize:  # noqa: N802
        """제목줄 + **내용이 원하는 크기**.

        ★ 이걸 안 주면 시트가 내용보다 작게 열린다.  ``_place`` 는 root(=이 프레임)의
        ``sizeHint`` 만 보는데, 기본 구현은 레이아웃이 계산한 값을 쓰고 그 값은 내용
        위젯이 ``sizeHint()`` 를 오버라이드해 요구한 높이를 그대로 반영하지 못했다
        (슬롯 선택 창 실측: 내용은 624px 를 원하는데 프레임은 392px 를 보고했고, 그
        결과 그리드가 두어 줄만 보였다).  내용의 요구를 직접 얹어 전달한다."""
        base = super().sizeHint()
        content = getattr(self, "_content", None)
        if content is None:
            return base
        try:
            ch = content.sizeHint()
        except RuntimeError:
            return base
        if not ch.isValid():
            return base
        bar_h = self.layout().itemAt(0).widget().sizeHint().height()
        margins = self.layout().contentsMargins()
        return QSize(
            max(base.width(), ch.width() + margins.left() + margins.right()),
            max(base.height(),
                ch.height() + bar_h + margins.top() + margins.bottom()),
        )


class SheetHost(QWidget):
    """메인 창 안에서 시트를 띄우는 호스트.  창 크기를 따라간다.

    ``LoadingOverlay`` 와 같은 방식으로 부모를 추종한다(부모에 이벤트 필터 → Resize).
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._scrim = _Scrim(self)
        self._stack: list[dict] = []          # [{widget, loop, prev_parent, prev_flags}]
        # 퇴장 중인 시트의 스냅샷(QLabel) — 원본 위젯과 생명주기를 분리해 둔다.
        self._ghosts: list[QLabel] = []
        self._app_filter_on = False
        self.hide()
        parent.installEventFilter(self)

    # -- 배치 ----------------------------------------------------------
    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        etype = event.type()
        if obj is self.parent() and etype == QEvent.Type.Resize:
            self._cover_parent()
            return super().eventFilter(obj, event)
        if not self._stack:
            return super().eventFilter(obj, event)
        # 시트가 스스로 숨거나 닫히면(accept/reject 없이) 루프를 끝내야 한다 —
        # 안 끝나면 중첩 이벤트 루프가 남아 앱이 그 자리에서 굳는다.
        if etype in (QEvent.Type.Close, QEvent.Type.Hide):
            for entry in list(self._stack):
                if entry["widget"] is obj:
                    if etype == QEvent.Type.Close:
                        event.ignore()       # 창을 파괴하지 않고 시트만 닫는다
                    self._close(entry)
                    return etype == QEvent.Type.Close
        elif etype in (QEvent.Type.Shortcut, QEvent.Type.KeyPress,
                       QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride):
            # ★ 맨 위 시트(또는 그 자손)가 아닌 수신자의 키는 버린다 — Tab 이 뒤 페이지로
            #   새 나가면 '보이지 않는 곳'에 포커스가 생겨 Enter 가 엉뚱한 것을 누른다.
            top = self._stack[-1]["root"]
            owner = self._owner_widget(obj)
            if owner is not None and self._is_within(owner, top):
                return super().eventFilter(obj, event)      # 맨 위 시트의 것
            if etype == QEvent.Type.Shortcut:
                # ★ 창 **자신**의 단축키는 통과시킨다 — F11 전체화면이 그것이고, 시트가
                #   열린 동안 사진을 화면 가득 보는 유일한 경로다
                #   (`test_f11_still_reaches_the_window_while_a_sheet_is_open`).
                #   페이지가 가진 단축키는 창이 아니므로 여기서 걸러진다.
                if owner is not None and owner is owner.window():
                    return super().eventFilter(obj, event)
                return True          # 소유를 못 풀면 막는다 — 새는 쪽이 더 나쁘다
            if owner is not None:
                return True
        return super().eventFilter(obj, event)

    @staticmethod
    def _owner_widget(obj) -> Optional[QWidget]:
        """이벤트 수신자가 **어느 위젯의 것인지** 돌려준다.

        ★ ``Shortcut`` 이벤트의 수신자는 위젯이 아니라 **``QShortcut`` 객체**다(실측).
          그래서 예전처럼 ``isinstance(obj, QWidget)`` 로 걸러 버리면 단축키만 **필터를
          그냥 통과**한다 — 목록에 ``Shortcut`` 을 넣어도 소용이 없다.  실제로 시트가
          열린 채 Stage 1 에서 →/← 를 누르면 **대화상자 뒤에서 사진이 결정됐다**
          (실측: 남은 4장 → 1장).  소유 위젯은 ``parent()`` 로 얻는다."""
        if isinstance(obj, QWidget):
            return obj
        parent = obj.parent() if hasattr(obj, "parent") else None
        return parent if isinstance(parent, QWidget) else None

    def _set_app_filter(self, on: bool) -> None:
        """앱 전역 필터는 **시트가 열려 있는 동안만** 건다.

        전역 필터는 마우스 이동까지 모든 이벤트를 받는다 — 평소에도 걸어 두면 앱 전체가
        조금씩 느려진다.  필요할 때만 켠다."""
        app = QApplication.instance()
        if app is None or on == self._app_filter_on:
            return
        if on:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)
        self._app_filter_on = on

    @staticmethod
    def _is_within(w: QWidget, top: QWidget) -> bool:
        node: Optional[QWidget] = w
        depth = 0
        while node is not None and depth < 80:
            if node is top:
                return True
            node = node.parentWidget()
            depth += 1
        return False

    def _cover_parent(self) -> None:
        p = self.parentWidget()
        if p is None:
            return
        self.setGeometry(0, 0, p.width(), p.height())
        self._scrim.setGeometry(0, 0, self.width(), self.height())
        for entry in self._stack:
            self._place(entry["root"], entry["full_bleed"])

    def _place(self, w: QWidget, full_bleed: bool) -> None:
        m = _VIEWER_MARGIN_PX if full_bleed else _MARGIN_PX
        avail_w = max(160, self.width() - 2 * m)
        avail_h = max(120, self.height() - 2 * m)
        if full_bleed:
            ww, wh = avail_w, avail_h
        else:
            # ★ sizeHint 는 **유효하지 않을 수 있다**(레이아웃 없는 위젯은 (-1,-1)).
            #   그대로 쓰면 setGeometry 가 음수 폭을 무시해 시트가 0×0 으로 남아 화면에
            #   아무것도 안 뜬다 — 하한을 둔다.
            hint = w.sizeHint()
            ww = min(avail_w, max(hint.width(), w.minimumWidth(), _SHEET_MIN_W))
            wh = min(avail_h, max(hint.height(), w.minimumHeight(), _SHEET_MIN_H))
        w.setGeometry((self.width() - ww) // 2, (self.height() - wh) // 2, ww, wh)

    # -- 시트 열기/닫기 ------------------------------------------------
    def run(self, widget: QWidget, *, full_bleed: bool = False,
            chrome: Optional[bool] = None) -> int:
        """``widget`` 을 시트로 띄우고 닫힐 때까지 기다린다 — ``exec()`` 대체.

        ``QDialog`` 면 ``result()`` 를, 아니면 0 을 돌려준다.

        ★ ``chrome`` — 제목줄(제목 + ✕)을 씌운다.  창 안으로 들어오면 **OS 타이틀바가
        사라진다**: 창 제목(무슨 팝업인지)과 닫기 버튼을 그것이 제공했으므로, 그대로
        두면 '이게 뭔지 모르고 닫을 수도 없는' 시트가 생긴다.  기본값은 '창 제목이
        있으면 씌운다' — 스스로 제목을 그리는 메시지/선택 시트는 ``False`` 로 끈다."""
        if chrome is None:
            chrome = bool(widget.windowTitle())
        prev_parent = widget.parentWidget()
        prev_flags = widget.windowFlags()
        frame: Optional[_SheetFrame] = None
        if chrome:
            frame = _SheetFrame(self, widget.windowTitle())
            frame.set_content(widget)
            frame.close_requested.connect(widget.close)
            root: QWidget = frame
        else:
            widget.setParent(self)
            widget.setWindowFlags(Qt.WindowType.Widget)
            root = widget
        loop = QEventLoop()
        entry = {"widget": widget, "root": root, "frame": frame, "loop": loop,
                 "prev_parent": prev_parent, "prev_flags": prev_flags,
                 "full_bleed": full_bleed}
        self._stack.append(entry)

        finished_conn = None
        if isinstance(widget, QDialog):
            finished_conn = widget.finished.connect(
                lambda _code, e=entry: self._close(e))
        self._set_app_filter(True)

        self._drop_ghosts()          # 직전 시트의 퇴장 잔상과 겹치지 않게
        # 직전 퇴장에서 켜 둔 '마우스 통과' 를 되돌린다(아래 `_close` 참조).
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._cover_parent()
        self.show()
        self.raise_()
        self._scrim.show()
        if len(self._stack) == 1:
            # 맨 아래 한 겹만 페이드인한다 — 시트 위에 시트를 열 때 다시 어둡게
            # 깜빡이면 '뒤로 갔다' 는 거짓 신호가 된다.
            self._scrim.fade_in()
        root.show()
        root.raise_()
        widget.show()
        self._place(root, full_bleed)
        widget.setFocus(Qt.FocusReason.PopupFocusReason)
        self._enter(root)
        try:
            loop.exec()                      # ★ exec() 과 같은 동기 의미
        finally:
            if finished_conn is not None:
                try:
                    widget.finished.disconnect(finished_conn)
                except (TypeError, RuntimeError):
                    pass
        return widget.result() if isinstance(widget, QDialog) else 0

    # 시트가 아래에서 올라오는 거리.  ★ 불투명도만 바꾸면 '나타났다'는 사실만 전할 뿐
    # **어디서 왔는지**가 없어 툭 튀어나온 것처럼 읽힌다(사용자 지적).  위치와 불투명도를
    # 같은 시간·같은 곡선으로 **함께** 움직여야 한 덩어리로 떠오른다.
    ENTER_RISE_PX = 18

    def _enter(self, w: QWidget) -> None:
        """시트 등장 — 아래에서 살짝 올라오며 나타난다(불투명도 + 위치)."""
        if not motion.enabled():
            return
        eff = QGraphicsOpacityEffect(w)
        eff.setOpacity(0.0)
        w.setGraphicsEffect(eff)
        # tick 은 자기 부모(w)의 상태만 건드린다 — 형제를 건드리면 파괴 순서 함정에 빠진다.
        anim = QPropertyAnimation(eff, b"opacity", w)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(motion.DUR_SHEET)
        anim.setEasingCurve(motion.EASE_PRIMARY)
        anim.finished.connect(lambda: w.setGraphicsEffect(None))
        anim.start()

        # ★ `_place` 가 잡아 둔 **최종 위치**가 도착점이다.  창 크기가 바뀌면
        #   `_cover_parent` 가 다시 `_place` 를 불러 즉시 제자리로 스냅한다(그게 맞다).
        end = w.pos()
        start = QPoint(end.x(), end.y() + self.ENTER_RISE_PX)
        w.move(start)
        rise = QPropertyAnimation(w, b"pos", w)
        rise.setStartValue(start)
        rise.setEndValue(end)
        rise.setDuration(motion.DUR_SHEET)
        rise.setEasingCurve(motion.EASE_PRIMARY)
        rise.start()

    def _make_ghost(self, entry: dict) -> Optional[QLabel]:
        """닫기 직전 시트의 **스냅샷**을 호스트 소유 QLabel 로 떠서 돌려준다.

        퇴장 모션을 시트 위젯 자체에 걸면 안 된다 — ``WA_DeleteOnClose`` 다이얼로그는
        닫히는 즉시 파괴될 수 있고, 그러면 tick 이 죽은 C++ 객체로 들어가 세그폴트가
        난다(모듈 주석 5번의 전례).  그림만 남기고 원본은 곧바로 정리하면 생명주기가
        얽히지 않는다.  실패하면 None — 호출부가 애니메이션 없이 진행한다."""
        if not motion.enabled():
            return None
        root = entry.get("root")
        try:
            if root is None or not root.isVisible():
                return None
            pm = root.grab()
            if pm.isNull():
                return None
            ghost = QLabel(self)
            ghost.setPixmap(pm)
            ghost.setGeometry(root.geometry())
            ghost.show()
            ghost.raise_()
            return ghost
        except RuntimeError:
            return None

    def _fade_out_ghost(self, ghost: QLabel, *, then_hide_host: bool) -> None:
        """스냅샷을 페이드아웃하고, 마지막 시트였으면 끝난 뒤 호스트를 숨긴다.

        퇴장은 입장보다 짧게(모션 원칙).  애니메이션은 ghost 를 부모로 가져
        ``DeleteWhenStopped`` 없이도 ghost 와 함께 정리된다."""
        eff = QGraphicsOpacityEffect(ghost)
        eff.setOpacity(1.0)
        ghost.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", ghost)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setDuration(motion.DUR_SHEET // 2)   # 퇴장은 입장보다 짧게
        anim.setEasingCurve(motion.EASE_PRIMARY)

        def _done() -> None:
            try:
                if ghost in self._ghosts:
                    self._ghosts.remove(ghost)
                ghost.hide()
                ghost.deleteLater()
            except RuntimeError:
                pass
            # 애니메이션 도중 새 시트가 열렸으면 호스트를 숨기면 안 된다.
            # (스크림은 `_close` 가 이미 내렸다 — 여기서 다시 만지지 않는다.)
            if then_hide_host and not self._stack:
                self.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
                self.hide()

        anim.finished.connect(_done)
        self._ghosts.append(ghost)
        anim.start()

    def _drop_ghosts(self) -> None:
        """남아 있는 퇴장 스냅샷을 즉시 치운다(새 시트가 열릴 때)."""
        for g in list(self._ghosts):
            try:
                g.hide()
                g.deleteLater()
            except RuntimeError:
                pass
        self._ghosts.clear()

    def _close(self, entry: dict) -> None:
        if entry not in self._stack:
            return
        self._stack.remove(entry)
        ghost = self._make_ghost(entry)
        w = entry["widget"]
        try:
            w.hide()
            w.setParent(entry["prev_parent"])
            w.setWindowFlags(entry["prev_flags"])
        except RuntimeError:
            pass                             # 이미 파괴됐다 — 되돌릴 것이 없다
        frame = entry.get("frame")
        if frame is not None:
            try:
                frame.hide()
                frame.deleteLater()
            except RuntimeError:
                pass
        if not self._stack:
            self._set_app_filter(False)
            # ★ 스크림은 **닫는 그 프레임에** 내린다.  잔상이 다 사라질 때까지 붙잡아
            #   두면 팝업이 이미 보이지 않는 ~80ms 동안 '아무것도 없는 어두운 화면'
            #   이 남았다가 마지막에 한 프레임으로 뚝 밝아진다(사용자 지적).
            self._scrim.hide()
            if ghost is not None:
                # 잔상은 계속 페이드아웃한다 — 그림이 얹힐 바닥(호스트)은 남기되,
                # 보이지 않는 전면 위젯이 클릭을 먹지 않게 마우스를 통과시킨다.
                self.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                self._fade_out_ghost(ghost, then_hide_host=True)
            else:
                self.hide()
        else:
            if ghost is not None:
                self._fade_out_ghost(ghost, then_hide_host=False)
            top = self._stack[-1]["root"]
            top.raise_()
            self._stack[-1]["widget"].setFocus(Qt.FocusReason.PopupFocusReason)
        loop = entry["loop"]
        if loop.isRunning():
            loop.quit()

    def close_all(self) -> None:
        """열린 시트를 모두 닫는다(창이 닫힐 때 루프가 남지 않게)."""
        for entry in list(reversed(self._stack)):
            self._close(entry)


# ---------------------------------------------------------------------------
# 호스트 찾기 / 폴백
# ---------------------------------------------------------------------------
def host_for(widget: Optional[QWidget]) -> Optional[SheetHost]:
    """``widget`` 이 속한 창의 시트 호스트.  없으면 ``None``(→ 네이티브 폴백)."""
    if widget is None:
        return None
    try:
        win = widget.window()
    except RuntimeError:
        return None
    host = getattr(win, "_sheets", None)
    if isinstance(host, SheetHost):
        return host
    # 시트 안에서 또 시트를 여는 경우: 창이 시트 자신일 수 있으니 부모를 따라 올라간다.
    node: Optional[QWidget] = widget
    depth = 0
    while node is not None and depth < 80:
        host = getattr(node, "_sheets", None)
        if isinstance(host, SheetHost):
            return host
        node = node.parentWidget()
        depth += 1
    return None


def run(dialog, *, full_bleed: bool = False) -> int:
    """``dialog.exec()`` 대체 — 앱 안 시트로 띄운다.

    ★ 호스팅할 수 없는 것(호스트를 못 찾음, 애초에 위젯이 아님)은 **그대로 ``exec()``**
    한다.  이 함수는 59개 호출부의 유일한 통로이므로, 예상 밖의 입력에 예외를 던지면
    그 기능이 통째로 죽는다 — 폴백이 곧 안전판이다."""
    if not isinstance(dialog, QWidget):
        return dialog.exec() if hasattr(dialog, "exec") else 0
    host = host_for(dialog.parentWidget()) or host_for(dialog)
    if host is None:
        return dialog.exec() if isinstance(dialog, QDialog) else 0
    return host.run(dialog, full_bleed=full_bleed)


# ---------------------------------------------------------------------------
# 메시지 시트 — QMessageBox 대체(반환값 호환)
# ---------------------------------------------------------------------------
_SB = QMessageBox.StandardButton

# 표준 버튼 → (라벨, role).  라벨은 i18n 에 모은다(위젯에 하드코딩 금지 규칙).
_BUTTON_SPEC = {
    _SB.Ok: ("MSG_BTN_OK", "primary"),
    _SB.Cancel: ("MSG_BTN_CANCEL", "ghost"),
    _SB.Yes: ("MSG_BTN_YES", "primary"),
    _SB.No: ("MSG_BTN_NO", "ghost"),
    _SB.Close: ("MSG_BTN_CLOSE", "ghost"),
}
_BUTTON_ORDER = (_SB.No, _SB.Cancel, _SB.Close, _SB.Ok, _SB.Yes)


class _MessageSheet(QDialog):
    """앱 안 메시지 시트 — 제목 · 본문 · 표준 버튼.  선택 결과를 그대로 돌려준다."""

    def __init__(self, parent: Optional[QWidget], title: str, text: str,
                 buttons, default, kind: str) -> None:
        super().__init__(parent)
        self.setProperty("role", "sheet")
        self.setObjectName("_messageSheet")
        self._answer = _SB.NoButton

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        head = QLabel(title, self)
        head.setProperty("role", "sheetTitle")
        head.setWordWrap(True)
        v.addWidget(head)

        body = QLabel(text, self)
        body.setProperty("role", "sheetBody" if kind != "warn" else "warn")
        body.setWordWrap(True)
        body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setMinimumWidth(_MSG_MIN_W)
        body.setMaximumWidth(_MSG_MAX_W)
        v.addWidget(body, 1)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        bar.addStretch(1)
        self._buttons: dict = {}
        for sb in _BUTTON_ORDER:
            if not (buttons & sb):
                continue
            key, role = _BUTTON_SPEC[sb]
            btn = NeonButton(getattr(i18n.KO, key), role=role, parent=self)
            btn.clicked.connect(lambda _c=False, s=sb: self._answer_with(s))
            bar.addWidget(btn)
            self._buttons[sb] = btn
        v.addLayout(bar)

        # ★ 기본값이 **부정 버튼**(아니오·취소·닫기)인 경고에서는 강조 버튼을 없앤다.
        #   예전엔 [예] 가 언제나 채운 파란 버튼이라, "정말 같은 폴더를 비교할까요?"
        #   처럼 기본이 '아니오' 인 경고에서도 시선은 [예] 로 가는데 Enter 는 [아니오]
        #   를 눌렀다 — 시각 강조와 안전 기본값이 정반대를 가리켰다.
        if default in (_SB.No, _SB.Cancel, _SB.Close):
            for btn in self._buttons.values():
                if btn.property("role") == "primary":
                    btn.setRole("ghost")

        if default in self._buttons:
            self._buttons[default].setDefault(True)
            self._buttons[default].setFocus()
        elif self._buttons:
            next(iter(self._buttons.values())).setFocus()
        self.setMaximumWidth(_MSG_MAX_W + 48)

    def _answer_with(self, sb) -> None:
        self._answer = sb
        self.accept()

    def answer(self):
        return self._answer

    def keyPressEvent(self, event):  # noqa: N802
        """Esc = 취소/아니오/닫기(있으면), 없으면 그대로 닫는다 — QMessageBox 와 같게."""
        if event.key() == Qt.Key.Key_Escape:
            for sb in (_SB.Cancel, _SB.No, _SB.Close, _SB.Ok):
                if sb in self._buttons:
                    self._answer_with(sb)
                    return
            self.reject()
            return
        super().keyPressEvent(event)


def _message(parent: Optional[QWidget], title: str, text: str, *,
             buttons=_SB.Ok, default=_SB.NoButton, kind: str = "info"):
    """앱 안 메시지 시트를 띄우고 **누른 표준 버튼**을 돌려준다.

    호스트가 없으면 네이티브 ``QMessageBox`` 로 폴백한다(반환값 동일)."""
    host = host_for(parent)
    if host is None:
        fn = {"info": QMessageBox.information, "warn": QMessageBox.warning,
              "error": QMessageBox.critical,
              "question": QMessageBox.question}.get(kind, QMessageBox.information)
        if default == _SB.NoButton:
            return fn(parent, title, text, buttons)
        return fn(parent, title, text, buttons, default)
    sheet = _MessageSheet(None, title, text, buttons, default, kind)
    try:
        host.run(sheet, chrome=False)
        return sheet.answer()
    finally:
        sheet.deleteLater()


def info(parent, title: str, text: str, buttons=_SB.Ok, default=_SB.NoButton):
    """``QMessageBox.information`` 대체."""
    return _message(parent, title, text, buttons=buttons, default=default,
                    kind="info")


def warn(parent, title: str, text: str, buttons=_SB.Ok, default=_SB.NoButton):
    """``QMessageBox.warning`` 대체."""
    return _message(parent, title, text, buttons=buttons, default=default,
                    kind="warn")


def error(parent, title: str, text: str, buttons=_SB.Ok, default=_SB.NoButton):
    """``QMessageBox.critical`` 대체."""
    return _message(parent, title, text, buttons=buttons, default=default,
                    kind="error")


def ask(parent, title: str, text: str, buttons=_SB.Yes | _SB.No,
        default=_SB.No):
    """``QMessageBox.question`` 대체 — 반환값은 ``QMessageBox.StandardButton``."""
    return _message(parent, title, text, buttons=buttons, default=default,
                    kind="question")


# ---------------------------------------------------------------------------
# 선택지가 3~4개인 질문 — 표준 버튼으로 표현되지 않는 팝업
# ---------------------------------------------------------------------------
class _ChoiceSheet(QDialog):
    """자유 선택지 시트 — ``QMessageBox.addButton`` 을 쓰던 두 곳을 위한 것.

    ``options`` 는 ``(key, label, role)`` 목록이고, 고른 ``key`` 를 돌려준다.
    닫거나 Esc 면 ``None`` — 옛 코드의 '그 밖의 버튼 → None' 과 같은 의미다."""

    def __init__(self, title: str, text: str, options, *,
                 default=None, heading: str = "", tiles: bool = False) -> None:
        super().__init__(None)
        self.setProperty("role", "sheet")
        self.setObjectName("_choiceSheet")
        self._picked = None
        self._tiles = bool(tiles)

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        head = QLabel(title, self)
        head.setProperty("role", "sheetTitle")
        head.setWordWrap(True)
        v.addWidget(head)

        if heading:
            # 강조 문장(예: KLA 쪽 묻기) — 본문보다 큰 경고 등급 라벨.
            lead = QLabel(heading, self)
            lead.setProperty("role", "warn")
            lead.setWordWrap(True)
            v.addWidget(lead)

        body = QLabel(text, self)
        body.setProperty("role", "sheetBody")
        body.setWordWrap(True)
        body.setMinimumWidth(_MSG_MIN_W)
        body.setMaximumWidth(_MSG_MAX_W)
        v.addWidget(body, 1)

        self._buttons: dict = {}
        if self._tiles:
            v.addLayout(self._build_tiles(options))
        else:
            v.addLayout(self._build_button_row(options, default))
        self.setMaximumWidth(_MSG_MAX_W + 48)

    def _build_button_row(self, options, default):
        """오른쪽 정렬 버튼 줄 — 하나가 주 액션인 질문(설치할까요? 등)."""
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        bar.addStretch(1)
        for key, label, role in options:
            btn = NeonButton(label, role=role, parent=self)
            btn.clicked.connect(lambda _c=False, k=key: self._pick(k))
            bar.addWidget(btn)
            self._buttons[key] = btn
        if default in self._buttons:
            self._buttons[default].setDefault(True)
            self._buttons[default].setFocus()
        return bar

    def _build_tiles(self, options):
        """큰 타일 격자 — 선택지가 **서로 대등할 때**.

        ★ 이 모드는 ``role`` 을 무시하고 넷을 **같은 등급**으로 그린다.  KLA 질문이
        정확히 그런 경우였다: 기준/검증/둘 다/KLA 아님 중 하나만 파란 주 버튼이라
        "왜 저것만 강조돼 있지?" 로 읽혔다(사용자 지적).  어느 하나가 정답이 아니면
        어느 하나만 강조해서도 안 된다.

        집안 관습(CLAUDE.md): **클릭 대상은 크고 명확하게** — 타일 전체가 클릭영역이고
        손가락 커서를 준다.  클릭 즉시 답이 확정된다(고르고 다시 [확인] 을 누르게 하면
        한 번 물어볼 것을 두 번 묻는 셈이다)."""
        grid = QGridLayout()
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setSpacing(8)
        cols = 2 if len(options) > 2 else len(options)
        for i, (key, label, _role) in enumerate(options):
            btn = NeonButton(label, role="option", parent=self)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding,
                              QSizePolicy.Policy.Fixed)
            btn.setMinimumHeight(theme.PROFILE.control_h_lg)
            btn.setAccessibleName(label)
            btn.clicked.connect(lambda _c=False, k=key: self._pick(k))
            grid.addWidget(btn, i // cols, i % cols)
            self._buttons[key] = btn
        for c in range(cols):
            grid.setColumnStretch(c, 1)
        return grid

    def _pick(self, key) -> None:
        self._picked = key
        self.accept()

    def picked(self):
        return self._picked

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.reject()                    # picked 는 None 으로 남는다
            return
        super().keyPressEvent(event)


def choose(parent, title: str, text: str, options, *, default=None,
           heading: str = "", tiles: bool = False):
    """선택지 3~4개 질문 — 고른 ``key``(없으면 ``None``)를 돌려준다.

    호스트가 없으면 네이티브 ``QMessageBox`` + ``addButton`` 으로 폴백한다."""
    host = host_for(parent)
    if host is None:
        box = QMessageBox(parent)
        box.setWindowTitle(title)
        box.setText(f"{heading}\n\n{text}" if heading else text)
        made = {}
        for key, label, _role in options:
            made[key] = box.addButton(label, QMessageBox.ButtonRole.ActionRole)
        if default in made:
            box.setDefaultButton(made[default])
        box.exec()
        clicked = box.clickedButton()
        for key, btn in made.items():
            if btn is clicked:
                return key
        return None
    sheet = _ChoiceSheet(title, text, options, default=default,
                        heading=heading, tiles=tiles)
    try:
        host.run(sheet, chrome=False)
        return sheet.picked()
    finally:
        sheet.deleteLater()
