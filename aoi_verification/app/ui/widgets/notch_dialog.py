"""Notch 방향 고르기 — 좌표 매칭 직전, 기준·검증 폴더의 notch 가 어느 쪽인지.

두 줄(기준 / 검증)에 위·왼쪽·오른쪽·아래 네 칸.  매칭 전에 자동 감지를 먼저 돌려 둘 다
아래면 이 창은 뜨지 않는다(사용자 지시 2026-10-28) — 뜨면 감지 결과가 처음 값으로 들어 있다.  방향이 다르면 매칭이 그 폴더의 좌표를 돌려서
(`coords.notch`) 계산한다 — 엑셀·화면 좌표는 그대로다.  결과는 :attr:`dirs` (기준, 검증).
"""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout

from ... import i18n
from ...coords import notch
from .. import theme
from .neon_button import NeonButton
from .option_group import OptionGroup

__all__ = ["NotchDialog"]


class NotchDialog(QDialog):
    def __init__(self, ref_label: str, val_label: str, ref_dir: str, val_dir: str,
                 parent=None, *, auto_args=None, auto_result=None) -> None:
        """``auto_args`` = ``(groups, ref_root, val_root, tol)`` — 있으면 [자동 감지] 가 생긴다.
        ``auto_result`` = 미리 돌린 감지 결과(:class:`~..workers.notch_auto.AutoNotch`) — 검증
        방향과 안내 글에 바로 반영한다."""
        super().__init__(parent)
        self._auto_args = auto_args
        self._worker = None
        self.setWindowTitle(i18n.KO.NOTCH_TITLE)
        root = QVBoxLayout(self)
        m = theme.PROFILE.page_margin // 2
        root.setContentsMargins(m, m, m, m)
        root.setSpacing(12)

        head = QLabel(i18n.KO.NOTCH_TITLE, self)
        head.setProperty("role", "title")
        root.addWidget(head)

        opts = [(d, i18n.KO.NOTCH_DIRS[d]) for d in notch.DIRECTIONS]
        self.ref_group = self._row(root, i18n.KO.NOTCH_REF, ref_label, opts, ref_dir)
        self.val_group = self._row(root, i18n.KO.NOTCH_VAL, val_label, opts, val_dir)

        bar = QHBoxLayout()
        self.status = QLabel("", self)
        self.status.setProperty("role", "muted")
        self.status.setWordWrap(True)
        bar.addWidget(self.status, 1)
        self.auto_btn = NeonButton(i18n.KO.NOTCH_AUTO, role="ghost", parent=self)
        self.auto_btn.setAutoDefault(False)
        self.auto_btn.setVisible(auto_args is not None)
        self.auto_btn.clicked.connect(self._run_auto)
        bar.addWidget(self.auto_btn)
        ok = NeonButton(i18n.KO.NOTCH_OK, role="primary", parent=self)
        ok.clicked.connect(self.accept)
        bar.addWidget(ok)
        root.addLayout(bar)
        self.setMinimumWidth(460)
        if auto_result is not None:
            self._on_auto_done(auto_result)

    def _run_auto(self) -> None:
        """기준 방향은 그대로 두고 검증 방향을 찾는다(백그라운드 — 창이 멈추지 않는다)."""
        if self._worker is not None or self._auto_args is None:
            return
        from ...workers.notch_auto import NotchAutoWorker
        groups, ref_root, val_root, tol = self._auto_args
        self.auto_btn.setEnabled(False)
        self.status.setText(i18n.KO.NOTCH_AUTO_BUSY)
        self._worker = NotchAutoWorker(groups, ref_root, val_root, self.dirs[0], tol, self)
        self._worker.done.connect(self._on_auto_done)
        self._worker.start()

    def _on_auto_done(self, res) -> None:
        w, self._worker = self._worker, None
        if w is not None:
            w.wait()
        self.auto_btn.setEnabled(True)
        if res.direction is None:
            self.status.setText(i18n.KO.NOTCH_AUTO_UNSURE)
            return
        self.val_group.set_current_key(res.direction)
        self.status.setText(i18n.KO.NOTCH_AUTO_FOUND_FMT.format(
            dir=i18n.KO.NOTCH_DIRS[res.direction], hit=res.support))

    def done(self, code: int) -> None:           # type: ignore[override]
        # 감지가 도는 중에 창을 닫아도 스레드가 먼저 끝나게 한다(실행 중 QThread 파괴 = 종료).
        if self._worker is not None:
            self._worker.wait()
            self._worker = None
        super().done(code)

    def _row(self, root, side: str, machine: str, opts, current: str) -> OptionGroup:
        lbl = QLabel(i18n.KO.NOTCH_SIDE_FMT.format(side=side, machine=machine), self)
        lbl.setProperty("role", "muted")
        root.addWidget(lbl)
        grp = OptionGroup(opts, current=notch.normalize(current), fixed_cols=4,
                          role="option", parent=self)
        root.addWidget(grp)
        return grp

    @property
    def dirs(self) -> tuple:
        return (self.ref_group.current_key() or notch.DEFAULT,
                self.val_group.current_key() or notch.DEFAULT)
