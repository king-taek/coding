"""개발자 모드 — 업데이트를 받을 저장소·브랜치를 고르는 시트.

사용자 요청: "지금은 기본 브랜치에서만 자동 업데이트를 진행하는데, 개발자 모드에서는
내가 고를 수 있도록.  그리고 업데이트 있는지 확인하고 업데이트가 없어도 해당 브랜치대로
다운받을 수 있도록."

- 고른 값은 ``prefs`` 의 ``dev_update_repo``/``dev_update_branch`` 에 남는다.  개발자
  모드가 켜져 있는 동안 자동 업데이트도 이 값을 따른다(``updater.dev_target``).
- [이 브랜치로 설치] 는 최신 여부와 상관없이 그 브랜치의 HEAD 를 받는다.  이 창은
  받을 대상(:attr:`install_info`)만 정하고 닫힌다 — 다운로드·로딩바는 메인 창의
  기존 업데이트 흐름이 맡는다(같은 경로 두 벌 금지).
- 네트워크는 백그라운드 스레드에서, 결과는 시그널로 메인 스레드에 넘긴다.
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel,
                             QLineEdit, QVBoxLayout)

from ... import i18n
from ...utils import prefs as _prefs
from ...utils import updater
from .. import theme
from .neon_button import NeonButton

__all__ = ["DevModeDialog"]


class DevModeDialog(QDialog):
    """:attr:`install_info` 가 결과다 — [이 브랜치로 설치] 를 눌렀으면 받을 대상 dict."""

    _branches_loaded = pyqtSignal(list, str)           # (이름들, 실패 사유)
    _checked = pyqtSignal(str, dict, bool)             # (status, info, 설치까지)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.DEV_MODE_TITLE)
        self.install_info: dict | None = None
        self._busy = False
        self._closed = False         # 닫힌 뒤 늦게 온 결과는 버린다(prefs 덮어쓰기 방지)
        self._branches_loaded.connect(self._on_branches_loaded)
        self._checked.connect(self._on_checked)
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        m = theme.PROFILE.page_margin // 2
        root.setContentsMargins(m, m, m, m)
        root.setSpacing(10)

        head = QLabel(i18n.KO.DEV_MODE_TITLE, self)
        head.setProperty("role", "title")
        root.addWidget(head)

        body = QLabel(i18n.KO.DEV_MODE_BODY, self)
        body.setProperty("role", "muted")
        body.setWordWrap(True)
        root.addWidget(body)

        cur = updater.current_version()
        if cur and cur.get("sha"):
            cur_text = i18n.KO.DEV_MODE_CURRENT_FMT.format(
                branch=cur.get("branch", ""), sha=str(cur["sha"])[:7],
                repo=cur.get("repo") or updater.DEFAULT_REPO)
        else:
            cur_text = i18n.KO.DEV_MODE_CURRENT_UNKNOWN
        root.addWidget(QLabel(cur_text, self))

        p = _prefs.load()
        root.addWidget(QLabel(i18n.KO.DEV_MODE_REPO, self))
        self.repo_edit = QLineEdit(p.dev_update_repo, self)
        self.repo_edit.setPlaceholderText(updater.DEFAULT_REPO)
        self.repo_edit.setMinimumWidth(460)
        root.addWidget(self.repo_edit)

        root.addWidget(QLabel(i18n.KO.DEV_MODE_BRANCH, self))
        brow = QHBoxLayout()
        brow.setSpacing(8)
        self.branch_combo = QComboBox(self)
        self.branch_combo.setEditable(True)     # 목록을 못 불러와도 직접 적는다
        self.branch_combo.lineEdit().setPlaceholderText(
            i18n.KO.DEV_MODE_BRANCH_DEFAULT_HINT)
        self.branch_combo.setEditText(p.dev_update_branch)
        brow.addWidget(self.branch_combo, 1)
        self.btn_load = NeonButton(i18n.KO.DEV_MODE_LOAD_BRANCHES, role="ghost")
        self.btn_load.clicked.connect(self._load_branches)
        brow.addWidget(self.btn_load)
        root.addLayout(brow)

        self.status = QLabel("", self)
        self.status.setProperty("role", "muted")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(16)        # 자리 예약(창 높이가 흔들리지 않게)
        root.addWidget(self.status)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_reset = NeonButton(i18n.KO.DEV_MODE_RESET, role="ghost")
        self.btn_reset.clicked.connect(self._reset)
        row.addWidget(self.btn_reset)
        # app.log 가 있는 캐시 폴더를 탐색기로 연다 — 로그를 보내 달라고 할 때마다
        # 경로를 설명하지 않아도 되게(사용자 요청).  툴팁에 실제 경로를 적는다.
        self.btn_log = NeonButton(i18n.KO.DEV_MODE_OPEN_LOG, role="ghost")
        self.btn_log.setToolTip(i18n.KO.DEV_MODE_LOG_PATH_FMT.format(
            path=self.log_dir() / "app.log"))
        self.btn_log.clicked.connect(self._open_log_dir)
        row.addWidget(self.btn_log)
        row.addStretch(1)
        self.btn_close = NeonButton(i18n.KO.DEV_MODE_CLOSE, role="ghost")
        self.btn_close.clicked.connect(self.reject)
        self.btn_check = NeonButton(i18n.KO.DEV_MODE_CHECK, role="ghost")
        self.btn_check.clicked.connect(lambda: self._check(install=False))
        self.btn_install = NeonButton(i18n.KO.DEV_MODE_INSTALL, role="primary")
        self.btn_install.clicked.connect(lambda: self._check(install=True))
        for b in (self.btn_close, self.btn_check, self.btn_install):
            row.addWidget(b)
        root.addLayout(row)

    # ------------------------------------------------------------------
    @staticmethod
    def log_dir():
        """`main._setup_logging` 이 app.log 를 두는 곳 — 캐시 루트와 같다."""
        from ...utils import paths
        return paths.cache_root()

    def _open_log_dir(self) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_dir())))

    # ------------------------------------------------------------------
    def target(self) -> tuple:
        """화면의 ``(repo, branch)``.  비어 있으면 기본 저장소·기본 브랜치로 채운다."""
        repo = self.repo_edit.text().strip() or updater.DEFAULT_REPO
        branch = self.branch_combo.currentText().strip()
        return repo, branch

    def _save(self) -> None:
        """고른 값을 저장한다 — 기본값과 같으면 비워 둔다(기본을 따르게)."""
        repo = self.repo_edit.text().strip()
        if repo == updater.DEFAULT_REPO:
            repo = ""
        _prefs.patch(dev_update_repo=repo,
                     dev_update_branch=self.branch_combo.currentText().strip())

    def done(self, r: int) -> None:          # 닫힐 때(어느 버튼이든) 고른 값을 남긴다
        if not self._closed:
            self._closed = True
            self._save()
        super().done(r)

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        for b in (self.btn_load, self.btn_check, self.btn_install, self.btn_reset):
            b.setEnabled(not busy)
        if text:
            self.status.setText(text)

    def _emit_safely(self, sig, *args) -> None:
        try:
            sig.emit(*args)
        except RuntimeError:                 # 창이 먼저 닫혀 지워졌다 — 버린다
            pass

    # ------------------------------------------------------------------
    def _reset(self) -> None:
        self.repo_edit.setText("")
        self.branch_combo.setEditText("")
        self.status.setText("")

    def _load_branches(self) -> None:
        if self._busy:
            return
        repo, _ = self.target()
        self._set_busy(True, i18n.KO.DEV_MODE_LOADING_BRANCHES)

        def _work() -> None:
            names: list = []
            try:
                names = updater.list_branches(repo)
            except Exception:
                names = []
            self._emit_safely(self._branches_loaded, names,
                              "" if names else updater.last_error())

        threading.Thread(target=_work, name="dev-branches", daemon=True).start()

    def _on_branches_loaded(self, names: list, error: str) -> None:
        if self._closed:
            return
        self._set_busy(False)
        if not names:
            msg = i18n.KO.DEV_MODE_BRANCHES_FAILED
            self.status.setText(f"{msg}{i18n.KO.CAUSE_PREFIX}{error}" if error else msg)
            return
        typed = self.branch_combo.currentText()
        self.branch_combo.clear()
        self.branch_combo.addItems(names)
        self.branch_combo.setEditText(typed)    # 적어 둔 값을 목록이 덮지 않게
        self.status.setText(i18n.KO.DEV_MODE_BRANCHES_LOADED_FMT.format(n=len(names)))

    def _check(self, install: bool) -> None:
        if self._busy:
            return
        repo, branch = self.target()
        self._set_busy(True, i18n.KO.DEV_MODE_CHECKING)

        def _work() -> None:
            status, info = "unknown", {}
            try:
                status, info = updater.check_target(repo, branch)
            except Exception:
                status, info = "unknown", {}
            self._emit_safely(self._checked, status, info or {}, install)

        threading.Thread(target=_work, name="dev-check", daemon=True).start()

    def _on_checked(self, status: str, info: dict, install: bool) -> None:
        if self._closed:
            return
        self._set_busy(False)
        sha = str(info.get("sha") or "")[:7]
        if status == "update":
            text = i18n.KO.DEV_MODE_RESULT_UPDATE_FMT.format(
                sha=sha, message=info.get("message", ""))
        elif status == "latest":
            text = i18n.KO.DEV_MODE_RESULT_LATEST_FMT.format(sha=sha)
        else:
            text = i18n.KO.DEV_MODE_RESULT_UNKNOWN_FMT.format(
                error=info.get("error") or i18n.KO.UPDATE_ERR_GITHUB)
        self.status.setText(text)
        if install and info.get("sha"):
            self.install_info = dict(info)
            self.accept()
