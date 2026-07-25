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

from PyQt6.QtCore import QEvent, QEventLoop, QPropertyAnimation, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (QApplication, QDialog, QGraphicsOpacityEffect,
                             QHBoxLayout, QLabel, QMessageBox, QVBoxLayout,
                             QWidget)

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
    """시트 뒤를 덮는 반투명 막 — 로딩 오버레이와 같은 색(``theme.SCRIM_RGBA``)."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAutoFillBackground(False)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        r, g, b, a = theme.SCRIM_RGBA
        p.fillRect(self.rect(), QColor(r, g, b, a))


class SheetHost(QWidget):
    """메인 창 안에서 시트를 띄우는 호스트.  창 크기를 따라간다.

    ``LoadingOverlay`` 와 같은 방식으로 부모를 추종한다(부모에 이벤트 필터 → Resize).
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._scrim = _Scrim(self)
        self._stack: list[dict] = []          # [{widget, loop, prev_parent, prev_flags}]
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
        elif etype in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease,
                       QEvent.Type.ShortcutOverride):
            # ★ 맨 위 시트(또는 그 자손)가 아닌 수신자의 키는 버린다 — Tab 이 뒤 페이지로
            #   새 나가면 '보이지 않는 곳'에 포커스가 생겨 Enter 가 엉뚱한 것을 누른다.
            top = self._stack[-1]["widget"]
            if isinstance(obj, QWidget) and not self._is_within(obj, top):
                return True
        return super().eventFilter(obj, event)

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
            self._place(entry["widget"], entry["full_bleed"])

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
    def run(self, widget: QWidget, *, full_bleed: bool = False) -> int:
        """``widget`` 을 시트로 띄우고 닫힐 때까지 기다린다 — ``exec()`` 대체.

        ``QDialog`` 면 ``result()`` 를, 아니면 0 을 돌려준다."""
        prev_parent = widget.parentWidget()
        prev_flags = widget.windowFlags()
        widget.setParent(self)
        widget.setWindowFlags(Qt.WindowType.Widget)
        loop = QEventLoop()
        entry = {"widget": widget, "loop": loop, "prev_parent": prev_parent,
                 "prev_flags": prev_flags, "full_bleed": full_bleed}
        self._stack.append(entry)

        finished_conn = None
        if isinstance(widget, QDialog):
            finished_conn = widget.finished.connect(
                lambda _code, e=entry: self._close(e))
        self._set_app_filter(True)

        self._cover_parent()
        self.show()
        self.raise_()
        self._scrim.show()
        widget.show()
        widget.raise_()
        self._place(widget, full_bleed)
        widget.setFocus(Qt.FocusReason.PopupFocusReason)
        self._fade_in(widget)
        try:
            loop.exec()                      # ★ exec() 과 같은 동기 의미
        finally:
            if finished_conn is not None:
                try:
                    widget.finished.disconnect(finished_conn)
                except (TypeError, RuntimeError):
                    pass
        return widget.result() if isinstance(widget, QDialog) else 0

    def _fade_in(self, w: QWidget) -> None:
        if not motion.enabled():
            return
        eff = QGraphicsOpacityEffect(w)
        eff.setOpacity(0.0)
        w.setGraphicsEffect(eff)
        # tick 은 자기 부모(w)의 상태만 건드린다 — 형제를 건드리면 파괴 순서 함정에 빠진다.
        anim = QPropertyAnimation(eff, b"opacity", w)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(motion.dur(motion.DUR_BASE))
        anim.setEasingCurve(motion.EASE_PRIMARY)
        anim.finished.connect(lambda: w.setGraphicsEffect(None))
        anim.start()

    def _close(self, entry: dict) -> None:
        if entry not in self._stack:
            return
        self._stack.remove(entry)
        w = entry["widget"]
        try:
            w.hide()
            w.setParent(entry["prev_parent"])
            w.setWindowFlags(entry["prev_flags"])
        except RuntimeError:
            pass                             # 이미 파괴됐다 — 되돌릴 것이 없다
        if not self._stack:
            self._set_app_filter(False)
            self._scrim.hide()
            self.hide()
        else:
            top = self._stack[-1]["widget"]
            top.raise_()
            top.setFocus(Qt.FocusReason.PopupFocusReason)
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


def run(dialog: QWidget, *, full_bleed: bool = False) -> int:
    """``dialog.exec()`` 대체 — 앱 안 시트로 띄운다.  호스트가 없으면 네이티브 exec."""
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
        host.run(sheet)
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


def about(parent, title: str, text: str):
    """``QMessageBox.about`` 대체(확인 하나)."""
    return _message(parent, title, text, buttons=_SB.Ok, kind="info")


# ---------------------------------------------------------------------------
# 선택지가 3~4개인 질문 — 표준 버튼으로 표현되지 않는 팝업
# ---------------------------------------------------------------------------
class _ChoiceSheet(QDialog):
    """자유 선택지 시트 — ``QMessageBox.addButton`` 을 쓰던 두 곳을 위한 것.

    ``options`` 는 ``(key, label, role)`` 목록이고, 고른 ``key`` 를 돌려준다.
    닫거나 Esc 면 ``None`` — 옛 코드의 '그 밖의 버튼 → None' 과 같은 의미다."""

    def __init__(self, title: str, text: str, options, *,
                 default=None, heading: str = "") -> None:
        super().__init__(None)
        self.setProperty("role", "sheet")
        self.setObjectName("_choiceSheet")
        self._picked = None

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

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        bar.addStretch(1)
        self._buttons: dict = {}
        for key, label, role in options:
            btn = NeonButton(label, role=role, parent=self)
            btn.clicked.connect(lambda _c=False, k=key: self._pick(k))
            bar.addWidget(btn)
            self._buttons[key] = btn
        v.addLayout(bar)
        if default in self._buttons:
            self._buttons[default].setDefault(True)
            self._buttons[default].setFocus()
        self.setMaximumWidth(_MSG_MAX_W + 48)

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
           heading: str = ""):
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
    sheet = _ChoiceSheet(title, text, options, default=default, heading=heading)
    try:
        host.run(sheet)
        return sheet.picked()
    finally:
        sheet.deleteLater()
