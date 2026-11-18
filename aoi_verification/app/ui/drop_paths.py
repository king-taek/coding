"""끌어다 놓기 · 붙여넣기로 들어온 경로를 다듬는 공통 도구.

★ ``dragEnter``/``dragMove`` 에서 디스크를 만지지 마라(``is_dir`` 등) — NAS 경로면 화면이
  멈춘다.  ``local_paths`` 는 문자열만 다루고, 폴더/사진 판별(``split_paths``)은 drop 에서만 한다.
"""

from __future__ import annotations

import os
import re
from typing import Callable, Optional
from urllib.parse import unquote

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QAbstractSpinBox, QApplication, QLineEdit,
                             QPlainTextEdit, QTextEdit)

from .teardown import tolerate_teardown

_WIN_ABS = re.compile(r"^[A-Za-z]:[\\/]")


def clean_path_text(text: str) -> str:
    """붙여 넣은 글자에서 경로를 꺼낸다 — 따옴표('경로로 복사')·``file://`` 를 벗긴다.

    절대 경로(``C:\\``, ``\\\\서버\\``, ``/``)처럼 보이지 않으면 ``""``."""
    t = (text or "").strip().strip('"').strip()
    if t.lower().startswith("file://"):
        rest = unquote(t[7:])
        if rest.startswith("/") and _WIN_ABS.match(rest[1:]):
            t = rest[1:]                       # file:///C:/x
        elif rest.startswith("/"):
            t = rest                           # file:///home/x (POSIX)
        else:
            t = "//" + rest                    # file://server/share
    if _WIN_ABS.match(t) or t.startswith(("\\\\", "//", "/")):
        return t
    return ""


def split_paths(paths, *, is_dir: Callable[[str], bool], is_image: Callable[[str], bool],
                is_xlsx: Optional[Callable[[str], bool]] = None
                ) -> tuple[list[str], list[str], list[str]]:
    """경로를 (폴더, 사진, 그 밖) 으로 가른다 — 순서 유지, 같은 경로는 한 번만.

    ``is_xlsx`` 를 주면 엑셀 파일은 사진 판정보다 먼저 '그 밖' 으로 간다(판별은 주입)."""
    folders: list[str] = []
    images: list[str] = []
    others: list[str] = []
    seen: set[str] = set()
    for p in paths:
        key = os.path.normcase(os.path.normpath(p))
        if key in seen:
            continue
        seen.add(key)
        if is_dir(p):
            folders.append(p)
        elif is_xlsx is not None and is_xlsx(p):
            others.append(p)
        elif is_image(p):
            images.append(p)
        else:
            others.append(p)
    return folders, images, others


_FILE_SUFFIXES = frozenset({
    ".txt", ".csv", ".pdf", ".zip", ".7z", ".rar", ".exe", ".ini", ".dat", ".md", ".json",
    ".xls", ".doc", ".docx", ".ppt", ".pptx", ".py", ".log", ".bat", ".lnk", ".db",
    ".tif", ".tiff", ".gif", ".webp"})


def guess_kinds(paths, is_image: Optional[Callable[[str], bool]] = None) -> set[str]:
    """경로들의 종류를 **이름만 보고** 짐작한다 — ``image`` · ``xlsx`` · ``folder`` · ``other``.

    dragEnter/dragMove 용이라 디스크를 보지 않는다.  확장자 없는 이름은 ``folder`` 다.
    ``TB500.RDL4`` 같은 점 든 폴더 이름을 잘못 거절하지 않도록, 알려진 파일 확장자
    (``_FILE_SUFFIXES``)만 ``other`` 로 본다 — 모르는 확장자는 ``folder`` 쪽이다."""
    if is_image is None:
        from ..config import CONFIG
        is_image = CONFIG.is_image
    kinds: set[str] = set()
    for p in paths:
        suffix = os.path.splitext(p)[1].lower()
        if suffix in (".xlsx", ".xlsm"):
            kinds.add("xlsx")
        elif is_image(p):
            kinds.add("image")
        elif not suffix or suffix not in _FILE_SUFFIXES:
            kinds.add("folder")
        else:
            kinds.add("other")
    return kinds


def local_paths(mime) -> list[str]:
    """``QMimeData`` 에서 로컬 경로 목록을 꺼낸다(디스크 접근 없음).

    URL 이 있으면 로컬 파일만, 없으면 글자의 각 줄을 ``clean_path_text`` 로 읽는다."""
    if mime.hasUrls():
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile() and u.toLocalFile()]
    if mime.hasText():
        out = (clean_path_text(ln) for ln in mime.text().splitlines() if ln.strip())
        return [p for p in out if p]
    return []


_TEXT_INPUTS = (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit)


def _text_widget(fw):
    """포커스 위젯이 글자 입력칸이면 실제 편집 위젯(스핀박스는 안의 QLineEdit)을, 아니면 None."""
    if isinstance(fw, QAbstractSpinBox):
        fw = fw.lineEdit()
    return fw if isinstance(fw, (QLineEdit, QTextEdit, QPlainTextEdit)) else None


def _is_read_only(w) -> bool:
    return bool(w.isReadOnly())


def _paste_into_text_input(fw) -> bool:
    """편집 가능한 글자 입력칸이 포커스면 평소처럼 붙여 넣고 True.

    읽기 전용 칸은 붙여 넣을 수 없으므로 False — 경로 처리로 넘어간다."""
    w = _text_widget(fw)
    if w is None or _is_read_only(w):
        return False
    w.paste()
    return True


class _PasteFilter(QObject):
    """편집 가능한 입력칸이 Ctrl+V 를 직접 처리(ShortcutOverride)해 QShortcut 이 안 울리는 걸 막는다.

    클립보드에 파일(URL)이 있고 포커스가 글자 입력칸이면, 창의 위쪽으로 올라가며 등록된
    ``_drop_paste`` 처리기를 찾아 넘긴다 — 글자만 있으면 손대지 않아 평소 붙여넣기가 된다."""

    @tolerate_teardown
    def eventFilter(self, obj, event):          # noqa: N802
        if (event.type() != QEvent.Type.KeyPress
                or not event.matches(QKeySequence.StandardKey.Paste)):
            return False
        fw = QApplication.focusWidget()
        if fw is None or not isinstance(fw, _TEXT_INPUTS):
            return False
        mime = QApplication.clipboard().mimeData()
        if mime is None or not mime.hasUrls():
            return False
        paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile() and u.toLocalFile()]
        if not paths:
            return False
        w = fw
        while w is not None:
            h = getattr(w, "_drop_paste", None)
            if h is not None:
                h[0](paths)
                return True
            w = w.parentWidget()
        return False


_filter: Optional[_PasteFilter] = None


def _ensure_filter() -> None:
    global _filter
    app = QApplication.instance()
    if app is None:
        return
    if _filter is None or sip.isdeleted(_filter) or _filter.parent() is not app:
        _filter = _PasteFilter(app)
        app.installEventFilter(_filter)


def install_paste(widget, on_paths: Callable[[list[str]], object],
                  on_no_path: Callable[[bool], object]) -> QShortcut:
    """``widget`` 안에서 Ctrl+V 를 받아 클립보드의 파일 경로를 ``on_paths`` 로 넘긴다.

    경로가 없으면 ``on_no_path(그림이 들었는지)``.  글자 입력칸이 포커스면 그 칸에 붙여 넣되,
    클립보드가 파일이면 입력칸 대신 ``on_paths`` 로 간다(앱 전체 필터)."""
    widget._drop_paste = (on_paths, on_no_path)
    _ensure_filter()
    sc = QShortcut(QKeySequence(QKeySequence.StandardKey.Paste), widget)
    sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

    def _activated() -> None:
        if _paste_into_text_input(QApplication.focusWidget()):
            return
        mime = QApplication.clipboard().mimeData()
        paths = local_paths(mime) if mime is not None else []
        if paths:
            on_paths(paths)
        else:
            on_no_path(bool(mime is not None and mime.hasImage()))

    sc.activated.connect(_activated)
    return sc
