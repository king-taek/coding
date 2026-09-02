"""저장 직전에 결과 엑셀의 **이름만** 확인·수정하는 시트.

사용자 요청: "이 규칙으로 추천 파일 제목을 작성해주고 사용자가 추천 파일 제목에서
수정할 수 있도록 창을 띄워줘.  근데 파일 저장은 (검증과) 동시에 진행하고, 파일
제목을 수정하거나 결정하는 동안 파일은 이미 저장 중인 거로 하자."

그래서 이 창은 **저장을 기다리게 하지 않는다.**  여기서 정하는 것은 결과 파일이
가질 이름뿐이고, [이 이름으로 저장] 을 누르는 즉시 그 이름으로 저장이 시작된다.
폴더는 바꾸지 않는다 — 위치를 고르는 창은 따로 있다(양식이 없어 결과 경로를 못 정한
경우에만 뜬다).

★ 파일은 이 창 **전에는 존재하지 않는다.**  예전에는 [검증 시작] 때 양식을 복사해
  둔 '작업 파일' 이 있어 이름을 바꾸면 그 파일을 옮겨야 했는데, 저장까지 안 간
  세션마다 빈 xlsx 가 남는 것이 문제였다(사용자 신고).  지금은 이름이 곧 저장
  경로이고, 옮길 파일이 없다.

★ 확장자는 화면에 보이되 **사용자가 지우면 되살린다** — `.xlsx` 가 빠진 파일은
  엑셀이 열지 못한다.  이름을 고치라고 열어 준 창이 열 수 없는 파일을 만들면 안 된다.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit,
                             QVBoxLayout)

from ... import i18n
from .. import theme
from .neon_button import NeonButton

__all__ = ["SaveNameDialog", "sanitize_name"]

# Windows 파일명 금지 문자 — 여기서 막지 않으면 저장이 OS 오류로 실패한다.
_BAD = set('\\/:*?"<>|')
_EXT = ".xlsx"


def sanitize_name(text: str) -> tuple[str, str]:
    """``(정리된 이름, 오류 문구)`` — 오류 문구가 비어 있으면 쓸 수 있는 이름이다.

    순수 함수다(파일시스템을 보지 않는다) — 헤드리스로 그대로 테스트한다.
    """
    name = (text or "").strip()
    if not name:
        return ("", i18n.KO.SAVE_NAME_EMPTY)
    if any(ch in _BAD for ch in name):
        return ("", i18n.KO.SAVE_NAME_BAD_CHARS)
    if not name.lower().endswith(_EXT):
        name += _EXT                    # 확장자를 지웠으면 되살린다(위 주석)
    # 확장자만 남는 경우(".xlsx") 는 이름이 없는 것과 같다.
    if name.lower() == _EXT:
        return ("", i18n.KO.SAVE_NAME_EMPTY)
    return (name, "")


class SaveNameDialog(QDialog):
    """추천 이름을 보여주고 고치게 한다.  :attr:`chosen` 이 결과(취소면 ``None``)."""

    def __init__(self, suggested: str, folder: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.SAVE_NAME_TITLE)
        self._chosen: str | None = None
        self._build(suggested, Path(folder))

    @property
    def chosen(self) -> str | None:
        return self._chosen

    # ------------------------------------------------------------------
    def _build(self, suggested: str, folder: Path) -> None:
        root = QVBoxLayout(self)
        m = theme.PROFILE.page_margin // 2
        root.setContentsMargins(m, m, m, m)
        root.setSpacing(10)

        head = QLabel(i18n.KO.SAVE_NAME_TITLE, self)
        head.setProperty("role", "title")
        root.addWidget(head)

        body = QLabel(i18n.KO.SAVE_NAME_BODY, self)
        body.setProperty("role", "muted")
        body.setWordWrap(True)
        root.addWidget(body)

        self.edit = QLineEdit(suggested, self)
        self.edit.setMinimumWidth(420)
        self.edit.textChanged.connect(self._revalidate)
        # 확장자를 뺀 부분만 선택해 둔다 — 바로 타이핑해도 `.xlsx` 가 살아남는다.
        stem = suggested[:-len(_EXT)] if suggested.lower().endswith(_EXT) else suggested
        self.edit.setSelection(0, len(stem))
        root.addWidget(self.edit)

        self._err = QLabel("", self)
        self._err.setProperty("role", "error")
        self._err.setMinimumHeight(16)      # 자리를 예약한다(창 높이가 흔들리지 않게)
        self._err.setWordWrap(True)
        root.addWidget(self._err)

        where = QLabel(i18n.KO.SAVE_NAME_FOLDER_FMT.format(path=str(folder)), self)
        where.setProperty("role", "muted")
        where.setWordWrap(True)
        root.addWidget(where)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        self.btn_cancel = NeonButton(i18n.KO.SAVE_NAME_CANCEL, role="ghost")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok = NeonButton(i18n.KO.SAVE_NAME_OK, role="primary")
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self._on_ok)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_ok)
        root.addLayout(row)

        self.edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self._revalidate()

    def _revalidate(self) -> None:
        """쓸 수 없는 이름이면 **누르기 전에** 이유를 말하고 [확인] 을 잠근다."""
        _name, err = sanitize_name(self.edit.text())
        self._err.setText(err)
        self.btn_ok.setEnabled(not err)

    def _on_ok(self) -> None:
        name, err = sanitize_name(self.edit.text())
        if err:
            self._err.setText(err)
            return
        self._chosen = name
        self.accept()
