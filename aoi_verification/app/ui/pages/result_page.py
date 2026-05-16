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

        self.review_btn = NeonButton(i18n.KO.BTN_REVIEW_MATCHES, role="warn")
        self.review_btn.clicked.connect(self._on_review)
        bar.addWidget(self.review_btn)

        self.export_btn = NeonButton(i18n.KO.BTN_EXPORT_EXCEL, role="primary")
        self.export_btn.setMinimumWidth(240)
        self.export_btn.setMinimumHeight(46)
        self.export_btn.clicked.connect(self._on_export)
        bar.addWidget(self.export_btn)
        root.addLayout(bar)

    # ------------------------------------------------------------------
    def show_result(self, result: FinalResult,
                    template_path: Path | None = None,
                    target_path: Path | None = None,
                    auto_mode: bool = False) -> None:
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

        # 자동 매치 모드면 검토를 권하는 안내를 가장 위에 노출.
        if auto_mode:
            n_match = len(result.matches)
            n_miss = len(result.unmatched_refs)
            hint = QLabel(
                i18n.KO.AUTO_REVIEW_HINT_FMT.format(
                    n_match=n_match, n_miss=n_miss,
                ),
                self._summary_card,
            )
            hint.setWordWrap(True)
            hint.setStyleSheet(
                "color: #FFD600; font-weight: 700; padding: 4px;"
            )
            self._summary_layout.addWidget(hint)

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
    def _on_review(self) -> None:
        if self._result is None:
            return
        from ..widgets.matches_review import MatchesReviewDialog
        dlg = MatchesReviewDialog(self._result.matches, parent=self)
        dlg.exec()
        removed = dlg.removed
        if not removed:
            return
        # 결과에서 제외 + 요약 라벨 갱신
        keys = {(m.slot, m.ref_path.name, m.val_path.name) for m in removed}
        self._result.matches = [
            m for m in self._result.matches
            if (m.slot, m.ref_path.name, m.val_path.name) not in keys
        ]
        QMessageBox.information(
            self, i18n.KO.APP_TITLE,
            i18n.KO.REVIEW_REMOVED_FMT.format(n=len(removed)),
        )
        # 요약을 다시 그린다.
        self.show_result(self._result,
                         template_path=self._template_path,
                         target_path=self._target_path)

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
        # 저장 알림 닫힌 직후 — 학습 데이터 동의 모달.
        self._ask_training_consent()

    def _ask_training_consent(self) -> None:
        """저장 완료 후 ‘학습 자료로 사용?’ 모달을 띄우고 동의 시 누적."""
        if self._result is None or not self._result.matches:
            return
        n = len(self._result.matches)
        body = i18n.KO.CONSENT_BODY_FMT.format(n=n)
        r = QMessageBox.question(
            self, i18n.KO.CONSENT_TITLE, body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if r != QMessageBox.StandardButton.Yes:
            return

        from ...learning.dataset import TrainingDataStore
        try:
            store = TrainingDataStore()
            added = store.append_session(
                self._result.matches,
                ref_machine=self._result.ref_machine,
                val_machine=self._result.val_machine,
            )
        except Exception as exc:
            QMessageBox.warning(
                self, i18n.KO.APP_TITLE,
                i18n.KO.CONSENT_FAIL_FMT.format(error=str(exc)),
            )
            return
        QMessageBox.information(
            self, i18n.KO.APP_TITLE,
            i18n.KO.CONSENT_OK_FMT.format(n=added),
        )
        # 데이터 누적이 끝났으면 곧장 자동 재학습 트리거 (#5).
        # torch 가 없거나 학습 가능 페어가 부족하면 조용히 스킵.
        self._maybe_auto_retrain()

    def _maybe_auto_retrain(self) -> None:
        """학습 데이터 동의 후 모델 재학습을 자동으로 시작.

        사용자가 SetupPage 의 [모델 재학습 시작] 버튼을 직접 누르지 않아도
        검증을 끝낼 때마다 모델이 새 데이터를 흡수한다.  학습 모델은
        ‘성능 보장’ 로직(trainer 내부) 으로 기본 모드보다 떨어질 때 active
        승급을 막아 안전.
        """
        try:
            from ...learning import registry, triplet_model
            from ...learning.dataset import TrainingDataStore
            from ...learning.trainer import TrainHeadWorker
        except Exception:
            return
        if not triplet_model.is_available():
            return
        try:
            store = TrainingDataStore()
            pairs = store.load_all()
        except Exception:
            return
        # 학습이 의미 있을 만큼의 페어가 모인 경우에만 시작.
        if len(pairs) < TrainHeadWorker.MIN_PAIRS:
            return
        QMessageBox.information(
            self, i18n.KO.APP_TITLE,
            i18n.KO.AUTO_RETRAIN_STARTED_FMT.format(n=len(pairs)),
        )
        # 백그라운드 학습 — UI 는 그대로 ResultPage 에 머문다.
        worker = TrainHeadWorker(store, parent=self)
        # 부모가 ResultPage 라 가비지 컬렉트되지 않고 안전하게 동작.
        self._auto_retrain_worker = worker
        worker.signals.finished.connect(self._on_auto_retrain_done)
        worker.signals.failed.connect(
            lambda msg: QMessageBox.warning(
                self, i18n.KO.APP_TITLE,
                i18n.KO.TRAIN_FAIL_FMT.format(error=msg),
            )
        )
        worker.start()

    def _on_auto_retrain_done(self, result) -> None:
        # trainer 가 active 변경 여부를 result.activated 로 알려줌.
        try:
            name = getattr(result, "name", "") or str(result)
            activated = bool(getattr(result, "activated", True))
            if activated:
                msg = i18n.KO.AUTO_RETRAIN_DONE_FMT.format(name=name)
            else:
                msg = i18n.KO.AUTO_RETRAIN_KEPT_BASIC_FMT.format(name=name)
            QMessageBox.information(self, i18n.KO.APP_TITLE, msg)
        except Exception:
            pass

    def _on_export_failed(self, msg: str) -> None:
        self._loading.hide_overlay()
        QMessageBox.warning(
            self, i18n.KO.APP_TITLE,
            i18n.KO.SAVE_FAIL_FMT.format(error=msg),
        )
