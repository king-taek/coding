"""결과 요약 / 엑셀 저장 페이지."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                              QVBoxLayout, QWidget)

from ... import i18n
from ...models.result import FinalResult
from ...workers.exporter import ExcelExporter
from ..widgets.loading_overlay import LoadingOverlay
from ..widgets.neon_button import NeonButton
from ..widgets.neon_card import NeonCard


class ResultPage(QWidget):
    """검증 결과 요약 + 저장."""

    new_session_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._result: FinalResult | None = None
        self._template_path: Path | None = None
        self._target_path: Path | None = None     # 미리 복사된 작업 파일
        self._save_path: Path | None = None
        self._loading = LoadingOverlay(self)
        self._exporter: ExcelExporter | None = None
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(20)

        self.title = QLabel(i18n.KO.RESULT_TITLE, self)
        self.title.setProperty("role", "title")
        root.addWidget(self.title)

        # 요약 카드
        self._summary_card = NeonCard(role="card", parent=self)
        self._summary_layout = self._summary_card.body()
        root.addWidget(self._summary_card)

        root.addStretch(1)

        bar = QHBoxLayout()
        bar.addStretch(1)
        self.new_btn = NeonButton(i18n.KO.BTN_NEW_SESSION, role="ghost")
        self.new_btn.clicked.connect(self.new_session_requested.emit)
        bar.addWidget(self.new_btn)

        self.export_btn = NeonButton(i18n.KO.BTN_EXPORT_EXCEL, role="primary")
        self.export_btn.setMinimumWidth(240)
        self.export_btn.setMinimumHeight(46)
        self.export_btn.clicked.connect(self._on_export)
        bar.addWidget(self.export_btn)
        root.addLayout(bar)

    # ------------------------------------------------------------------
    def show_result(self, result: FinalResult,
                    template_path: Path | None = None,
                    target_path: Path | None = None) -> None:
        self._result = result
        self._template_path = template_path
        self._target_path = target_path
        # 기존 요약 비우기
        while self._summary_layout.count():
            it = self._summary_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

        # 라인 헬퍼
        def line(text: str, role: str = "subtitle"):
            lab = QLabel(text, self._summary_card)
            lab.setProperty("role", role)
            lab.setWordWrap(True)
            self._summary_layout.addWidget(lab)

        mode_text = "한쪽만 검증" if result.mode == "single" else "양쪽 교차검증"
        line(f"모드: {mode_text}")
        line(f"기준 장비: {result.ref_machine}    검증 장비: {result.val_machine}")
        line(f"매칭 성공 사진: {len(result.matches)} 장")
        if result.mode == "cross":
            line(f"미탐(빠른 호기): {len(result.miss_fast)} 장")
            line(f"미탐(느린 호기): {len(result.miss_slow)} 장")
        if result.slot_only_ref or result.slot_only_val:
            line(
                "Slot 불일치  ·  기준 전용: "
                f"{', '.join(result.slot_only_ref) or '없음'}",
                role="muted",
            )
            line(
                "Slot 불일치  ·  검증 전용: "
                f"{', '.join(result.slot_only_val) or '없음'}",
                role="muted",
            )

        if self._target_path is not None:
            line(
                f"{i18n.KO.WORKING_FILE_LABEL}: {self._target_path}",
                role="muted",
            )

    # ------------------------------------------------------------------
    def _on_export(self) -> None:
        if self._result is None:
            return
        # 양식 → 작업 파일은 이미 검증 시작 시점에 복사되었으므로 그대로 채워 쓴다.
        # 그 경로가 없다면(=양식 없음 + 복사 실패) 사용자에게 위치를 물어본다.
        if self._target_path is not None:
            self._save_path = self._target_path
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = i18n.KO.SAVE_FILENAME_FMT.format(
                ref=self._result.ref_machine,
                val=self._result.val_machine,
                ts=ts,
            )
            dst, _ = QFileDialog.getSaveFileName(
                self, i18n.KO.SAVE_DIALOG_TITLE, filename,
                "Excel (*.xlsx)",
            )
            if not dst:
                return
            self._save_path = Path(dst)

        self._loading.show_overlay(i18n.KO.LOAD_EXPORT)
        self._exporter = ExcelExporter(
            self._result, self._save_path, template_path=self._template_path,
        )
        self._exporter.signals.progress.connect(
            lambda d, t, msg: self._loading.set_progress(d, t, i18n.KO.LOAD_EXPORT)
        )
        self._exporter.signals.done.connect(self._on_export_done)
        self._exporter.signals.failed.connect(self._on_export_failed)
        self._exporter.start()

    def _on_export_done(self, path: str) -> None:
        self._loading.hide_overlay()
        QMessageBox.information(
            self, i18n.KO.APP_TITLE,
            i18n.KO.SAVE_SUCCESS_FMT.format(path=path),
        )

    def _on_export_failed(self, msg: str) -> None:
        self._loading.hide_overlay()
        QMessageBox.warning(
            self, i18n.KO.APP_TITLE,
            i18n.KO.SAVE_FAIL_FMT.format(error=msg),
        )
