"""결과 요약 / 엑셀 저장 페이지."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QLabel,
                              QMessageBox, QVBoxLayout, QWidget)

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
        # 매치 실패 사진 검토(#8) 에 필요한 외부 데이터 — main_window 가 주입.
        self._val_pool: dict | None = None
        self._score_cache = None
        # 효율 모드 선계산 top-K — 실패 검토에서 후보 풀≥300 일 때 재사용 (#1).
        self._fast_results: dict | None = None
        self._coord_mode: bool = False
        self._tolerance: float = 500.0
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

        # 사진을 원본 화질로 넣을지 옵션 — 결과 출력 버튼 바로 위에 둔다. 기본
        # 해제(중간 화질 캐시로 가볍고 빠른 출력). 체크하면 원본 그대로 임베드.
        orig_row = QHBoxLayout()
        orig_row.addStretch(1)
        self.original_quality_chk = QCheckBox(
            i18n.KO.EXPORT_ORIGINAL_QUALITY_LABEL, self,
        )
        self.original_quality_chk.setChecked(False)
        self.original_quality_chk.setToolTip(
            i18n.KO.EXPORT_ORIGINAL_QUALITY_TOOLTIP
        )
        orig_row.addWidget(self.original_quality_chk)
        orig_row.addStretch(1)
        root.addLayout(orig_row)

        bar = QHBoxLayout()
        bar.addStretch(1)
        self.new_btn = NeonButton(i18n.KO.BTN_NEW_SESSION, role="ghost")
        self.new_btn.clicked.connect(self.new_session_requested.emit)
        bar.addWidget(self.new_btn)

        self.review_btn = NeonButton(i18n.KO.BTN_REVIEW_MATCHES, role="warn")
        self.review_btn.clicked.connect(self._on_review)
        bar.addWidget(self.review_btn)

        # 매치 실패 사진 검토 — 엑셀 저장 직전, 마지막 한 번 더 매칭 기회 (#8).
        self.review_unmatched_btn = NeonButton(
            i18n.KO.BTN_REVIEW_UNMATCHED, role="warn",
        )
        self.review_unmatched_btn.setMinimumWidth(200)
        self.review_unmatched_btn.clicked.connect(self._on_review_unmatched)
        bar.addWidget(self.review_unmatched_btn)

        self.export_btn = NeonButton(i18n.KO.BTN_EXPORT_EXCEL, role="primary")
        self.export_btn.setMinimumWidth(240)
        self.export_btn.setMinimumHeight(46)
        self.export_btn.clicked.connect(self._on_export)
        bar.addWidget(self.export_btn)
        root.addLayout(bar)

        # 전체 양식(E~H 수기 영역) 포함 옵션 — 기본 해제(#3).  체크 시에만 무거운
        # 전체 양식 시트를 함께 저장한다.
        opt_row = QHBoxLayout()
        opt_row.addStretch(1)
        self.full_template_chk = QCheckBox(i18n.KO.EXPORT_FULL_TEMPLATE_LABEL, self)
        self.full_template_chk.setChecked(False)
        self.full_template_chk.setToolTip(i18n.KO.EXPORT_FULL_TEMPLATE_TOOLTIP)
        opt_row.addWidget(self.full_template_chk)
        opt_row.addStretch(1)
        root.addLayout(opt_row)

        # 개발자 크레딧 (마지막 화면) -----------------------------------
        credit = QLabel(i18n.KO.CREDIT, self)
        credit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credit.setStyleSheet("color: #7FB3D5; padding-top: 8px;")
        root.addWidget(credit)

    # ------------------------------------------------------------------
    def show_result(self, result: FinalResult,
                    template_path: Path | None = None,
                    target_path: Path | None = None,
                    auto_mode: bool = False,
                    val_pool: dict | None = None,
                    score_cache=None,
                    fast_results: dict | None = None,
                    coord_mode: bool = False,
                    tolerance: float = 500.0) -> None:
        self._result = result
        self._template_path = template_path
        self._target_path = target_path
        # 매치 실패 검토에 사용할 후보 풀 / 점수 캐시 / 선계산 결과 (#8/#1).
        self._val_pool = val_pool
        self._score_cache = score_cache
        self._fast_results = fast_results
        self._coord_mode = bool(coord_mode)
        self._tolerance = float(tolerance) if tolerance > 0 else 500.0
        # 검토 후 다시 그려도 ‘자동 매치 결과 검토 권장’ 라벨이 살아 있도록
        # 마지막 auto_mode 값을 기억해 재렌더링에서 재사용한다.
        self._auto_mode = bool(auto_mode)
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
        if self._auto_mode:
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

        line(f"기준 장비: {result.ref_machine}    검증 장비: {result.val_machine}")
        line(f"매칭 성공 사진: {len(result.matches)} 장")
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

        # 검토 가능한 매치 실패 사진이 있을 때만 검토 버튼 활성.
        n_unmatched = len(result.unmatched_refs)
        self.review_unmatched_btn.setEnabled(
            n_unmatched > 0 and self._val_pool is not None
        )
        if n_unmatched > 0:
            self.review_unmatched_btn.setText(
                f"{i18n.KO.BTN_REVIEW_UNMATCHED} ({n_unmatched})"
            )
        else:
            self.review_unmatched_btn.setText(i18n.KO.BTN_REVIEW_UNMATCHED)

    # ------------------------------------------------------------------
    def _on_review(self) -> None:
        if self._result is None:
            return
        from ..widgets.matches_review import MatchesReviewDialog
        dlg = MatchesReviewDialog(self._result.matches, parent=self,
                                  coord_mode=self._coord_mode,
                                  tolerance=self._tolerance)
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
        # 제외된 매치는 '매치 실패(매칭 취소)' 로 재분류 → 매치 실패 검토에서
        # ‘매칭 취소 목록’ 으로 다시 검토 가능 (#14).  중복 추가 방지.
        from ...models.result import MissEntry
        existing = {(u.slot, Path(u.path).name) for u in self._result.unmatched_refs}
        for m in removed:
            k = (m.slot, m.ref_path.name)
            if k in existing:
                continue
            existing.add(k)
            self._result.unmatched_refs.append(MissEntry(
                slot=m.slot, side="ref", path=m.ref_path,
                note="매칭 취소 (검토에서 삭제)",
            ))
        QMessageBox.information(
            self, i18n.KO.APP_TITLE,
            i18n.KO.REVIEW_REMOVED_FMT.format(n=len(removed)),
        )
        # 요약을 다시 그린다 (auto_mode 도 보존).
        self.show_result(self._result,
                         template_path=self._template_path,
                         target_path=self._target_path,
                         auto_mode=getattr(self, "_auto_mode", False),
                         val_pool=self._val_pool,
                         score_cache=self._score_cache,
                         fast_results=self._fast_results,
                         coord_mode=self._coord_mode,
                         tolerance=self._tolerance)

    # ------------------------------------------------------------------
    def _on_review_unmatched(self) -> None:
        """매치 실패 사진을 하나씩 검토 (#8). 신규 매칭이 생기면 result 에 합친다."""
        if self._result is None:
            return
        from ..widgets.unmatched_review_dialog import UnmatchedReviewDialog
        if not self._result.unmatched_refs:
            UnmatchedReviewDialog.show_empty_message(self)
            return
        if self._val_pool is None:
            QMessageBox.information(
                self, i18n.KO.APP_TITLE, i18n.KO.UNMATCHED_REVIEW_EMPTY,
            )
            return
        # 이미 결과에 들어간 모든 경로 — 중복 매칭 방지용. cross 모드에서
        # side="val" 미매칭의 후보가 ref 측 사진이라 ref_path 도 포함해야 한다.
        already_used = set()
        for m in self._result.matches:
            already_used.add(m.val_path)
            already_used.add(m.ref_path)
        dlg = UnmatchedReviewDialog(
            unmatched=self._result.unmatched_refs,
            val_pool=self._val_pool,
            already_used_vals=already_used,
            score_cache=self._score_cache,
            fast_results=self._fast_results,
            parent=self,
            coord_mode=self._coord_mode,
            tolerance=self._tolerance,
        )
        dlg.exec()
        if not dlg.new_matches:
            return
        # 신규 매칭을 결과에 합치고 미매칭 리스트에서 해당 ref 들을 제거.
        self._result.matches.extend(dlg.new_matches)
        resolved_paths = {Path(r.path) for r in dlg.resolved_refs}
        self._result.unmatched_refs = [
            u for u in self._result.unmatched_refs
            if Path(u.path) not in resolved_paths
        ]
        QMessageBox.information(
            self, i18n.KO.APP_TITLE,
            i18n.KO.UNMATCHED_REVIEW_DONE_FMT.format(n=len(dlg.new_matches)),
        )
        # 요약 다시 그리기 (매칭 수 / 미매칭 수 갱신).
        self.show_result(self._result,
                         template_path=self._template_path,
                         target_path=self._target_path,
                         val_pool=self._val_pool,
                         score_cache=self._score_cache,
                         coord_mode=self._coord_mode,
                         tolerance=self._tolerance)

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
            include_full_template=self.full_template_chk.isChecked(),
            original_quality=self.original_quality_chk.isChecked(),
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

