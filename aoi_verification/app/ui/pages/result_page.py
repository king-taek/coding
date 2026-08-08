"""결과 요약 / 엑셀 저장 페이지."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                              QVBoxLayout, QWidget)

from ... import i18n
from .. import theme
from ...models.result import FinalResult
from ...workers.exporter import ExcelExporter
from ..widgets.app_logo import build_logo_label
from ..widgets.loading_overlay import LoadingOverlay
from ..widgets.neon_button import NeonButton
from ..widgets.neon_card import NeonCard
from ..widgets import sheet_host as sheets
from ... import config as _config

# 허용 오차 폴백 — 값이 안 들어왔을 때만 쓴다.  **단일 출처는 config** 다
# (예전엔 리터럴 500 이 곳곳에 박혀 있어 기본값을 바꿔도 옛 값이 되살아났다).
_DFLT_TOL = _config.DEFAULT_COORD_TOLERANCE


class ResultPage(QWidget):
    """검증 결과 요약 + 저장."""

    new_session_requested = pyqtSignal()
    # 결과 → 검토 화면 복귀(U-10).  main_window 가 검토 결과를 보존한 채 되돌린다.
    back_to_review_requested = pyqtSignal()
    # 결과 화면에서 결과가 바뀌었음(실패 검토로 신규 매치 확정 등).
    # ★ 이걸 상위로 되돌리지 않으면, 검토로 갔다 재완료할 때 새 FinalResult 가
    #   만들어지며 여기서 만든 매치가 조용히 사라진다.
    result_edited = pyqtSignal(list, list)      # (matches, unmatched_refs)

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
        self._tolerance: float = _DFLT_TOL
        self._loading = LoadingOverlay(self)
        self._exporter: ExcelExporter | None = None
        # 이번 결과를 한 번이라도 저장했는가 — [새 검증 시작] 확인의 근거(U-06).
        self._exported = False
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(20)

        # 상단 로고 — 이 화면은 전체 스크롤이 없어 맨 위에 그대로 둔다
        # (보이는 결과는 예전과 같다).
        root.addWidget(build_logo_label(self))

        self.title = QLabel(i18n.KO.RESULT_TITLE, self)
        self.title.setProperty("role", "title")
        root.addWidget(self.title)

        # 요약 카드 — ★ 폭을 묶는다.  1600px 창에서 카드가 화면 끝까지 늘어나면
        #   짧은 문장 몇 줄이 가로로 흩어져 '빈 카드' 처럼 보인다.  읽기 좋은 폭으로
        #   제한하면 남는 여백이 사고가 아니라 의도로 읽힌다.
        self._summary_card = NeonCard(role="card", parent=self)
        self._summary_card.setMaximumWidth(820)
        self._summary_layout = self._summary_card.body()
        root.addWidget(self._summary_card, alignment=Qt.AlignmentFlag.AlignLeft)

        root.addStretch(1)

        # ★ 엑셀 저장 옵션을 **모두 한 줄에** 모은다(사용자 선택 1안).
        #   전에는 '원본 화질' 은 버튼 줄 위, '전체 양식' 은 버튼 줄 아래로 갈라져
        #   110px 떨어져 있었다(실측).  전부 '엑셀로 저장' 에만 걸리는 옵션인데
        #   버튼 줄이 사이를 가르니, 원본 화질이 저장 옵션인지 화면 보기 옵션인지
        #   알 수 없었다.  옵션 → 실행 순서로 위에서 아래로 읽히게 한다.
        #   기본은 모두 해제 — 가볍고 빠른 출력이 기본값이다.
        opt_row = QHBoxLayout()
        opt_row.setSpacing(22)
        opt_row.addStretch(1)
        self.unmatched_original_chk = QCheckBox(
            i18n.KO.EXPORT_UNMATCHED_ORIGINAL_LABEL, self,
        )
        self.unmatched_original_chk.setChecked(False)
        self.unmatched_original_chk.setToolTip(
            i18n.KO.EXPORT_UNMATCHED_ORIGINAL_TOOLTIP
        )
        opt_row.addWidget(self.unmatched_original_chk)

        self.original_quality_chk = QCheckBox(
            i18n.KO.EXPORT_ORIGINAL_QUALITY_LABEL, self,
        )
        self.original_quality_chk.setChecked(False)
        self.original_quality_chk.setToolTip(
            i18n.KO.EXPORT_ORIGINAL_QUALITY_TOOLTIP
        )
        opt_row.addWidget(self.original_quality_chk)

        self.full_template_chk = QCheckBox(i18n.KO.EXPORT_FULL_TEMPLATE_LABEL, self)
        self.full_template_chk.setChecked(False)
        self.full_template_chk.setToolTip(i18n.KO.EXPORT_FULL_TEMPLATE_TOOLTIP)
        opt_row.addWidget(self.full_template_chk)
        opt_row.addStretch(1)
        root.addLayout(opt_row)

        bar = QHBoxLayout()
        bar.addStretch(1)
        self.new_btn = NeonButton(i18n.KO.BTN_NEW_SESSION, role="ghost")
        self.new_btn.clicked.connect(self._on_new_session)
        bar.addWidget(self.new_btn)

        # ★ 예전엔 여기서 별도의 '매칭 결과 검토' 다이얼로그를 열었다.  그런데 전용
        #   검토 페이지가 이미 상위 기능(스왑·차순위·매치 없음)을 갖고 있어, 결과
        #   화면이 기능이 더 적은 다른 검토를 또 권하는 꼴이었다('검토' 가 세 이름·세
        #   화면으로 갈렸다).  다이얼로그를 없애고 **그 검토 화면으로 되돌아간다**.
        self.review_btn = NeonButton(i18n.KO.BTN_BACK_TO_REVIEW, role="ghost")
        self.review_btn.clicked.connect(self.back_to_review_requested.emit)
        bar.addWidget(self.review_btn)

        # 매치 실패 사진 검토 — 엑셀 저장 직전, 마지막 한 번 더 매칭 기회 (#8).
        # ★ warn(주의색)은 예외 상태 경고 전용이다 — 권장 이동 액션에 쓰면
        #   '위험한 동작' 처럼 읽힌다.  검토 권장은 상단 안내문이 담당한다.
        self.review_unmatched_btn = NeonButton(
            i18n.KO.BTN_REVIEW_UNMATCHED, role="ghost",
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

        # ★ 크레딧은 여기 두지 않는다 — 상태바(`main_window._credit_label`)가 모든
        #   화면에 공통으로 띄운다.  둘 다 두면 한 화면에 두 번 보인다.

    # ------------------------------------------------------------------
    def show_result(self, result: FinalResult,
                    template_path: Path | None = None,
                    target_path: Path | None = None,
                    auto_mode: bool = False,
                    val_pool: dict | None = None,
                    score_cache=None,
                    fast_results: dict | None = None,
                    coord_mode: bool = False,
                    tolerance: float = _DFLT_TOL) -> None:
        # ★ 새 결과가 들어오면 '저장했음' 은 무효다.  검토로 되돌아가 결과를 바꾼 뒤
        #   다시 들어오는 경로도 여기를 지나므로 자연히 리셋된다.
        if result is not self._result:
            self._exported = False
        self._result = result
        self._template_path = template_path
        self._target_path = target_path
        # 매치 실패 검토에 사용할 후보 풀 / 점수 캐시 / 선계산 결과 (#8/#1).
        self._val_pool = val_pool
        self._score_cache = score_cache
        self._fast_results = fast_results
        self._coord_mode = bool(coord_mode)
        self._tolerance = float(tolerance) if tolerance > 0 else _DFLT_TOL
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

        line(i18n.KO.RESULT_MACHINES_FMT.format(ref=result.ref_machine,
                                                val=result.val_machine))

        # ★ 검증의 종착 화면인데 핵심 수치가 비슷한 크기의 문장 속에 묻혀 있었다.
        #   '몇 장이 맞았고 몇 장이 문제인가' 를 스크롤 없이 2초 안에 읽히게 타일로 낸다.
        self._summary_layout.addLayout(self._build_stat_tiles(result))

        if result.slot_only_ref or result.slot_only_val:
            line(i18n.KO.RESULT_SLOT_ONLY_REF_FMT.format(
                names=", ".join(result.slot_only_ref) or i18n.KO.VALUE_NONE),
                role="muted")
            line(i18n.KO.RESULT_SLOT_ONLY_VAL_FMT.format(
                names=", ".join(result.slot_only_val) or i18n.KO.VALUE_NONE),
                role="muted")

        if self._target_path is not None:
            line(f"{i18n.KO.WORKING_FILE_LABEL}: {self._target_path}",
                 role="monoMuted")

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
    def _on_review_unmatched(self) -> None:
        """매치 실패 사진을 하나씩 검토 (#8). 신규 매칭이 생기면 result 에 합친다."""
        if self._result is None:
            return
        from ..widgets.unmatched_review_dialog import UnmatchedReviewDialog
        if not self._result.unmatched_refs:
            UnmatchedReviewDialog.show_empty_message(self)
            return
        if self._val_pool is None:
            sheets.info(
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
        sheets.run(dlg, full_bleed=True)
        if not dlg.new_matches:
            return
        # 신규 매칭을 결과에 합치고 미매칭 리스트에서 해당 ref 들을 제거.
        self._result.matches.extend(dlg.new_matches)
        resolved_paths = {Path(r.path) for r in dlg.resolved_refs}
        self._result.unmatched_refs = [
            u for u in self._result.unmatched_refs
            if Path(u.path) not in resolved_paths
        ]
        # ★ 상위(main_window)에 되돌려 준다 — 검토 화면으로 갔다 재완료하면
        #   새 FinalResult 가 만들어지므로, 여기서 확정한 매치가 사라진다.
        self.result_edited.emit(list(self._result.matches),
                                list(self._result.unmatched_refs))
        sheets.info(
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

    # ------------------------------------------------------------------
    def _build_stat_tiles(self, result: FinalResult) -> QHBoxLayout:
        """핵심 수치를 큰 모노 숫자 타일로.

        '허용 초과' 는 결과 객체에 따로 없다 — 검토 화면과 **같은 분류 함수**를 써서
        센다(두 화면이 다른 기준으로 세면 사용자가 숫자 불일치를 먼저 발견한다)."""
        from .match_review_page import tally

        _ok, n_over, _none = tally(result.matches, set(), self._coord_mode)
        n_match = len(result.matches)
        n_miss = len(result.unmatched_refs)
        n_slot = len(result.slot_only_ref) + len(result.slot_only_val)

        row = QHBoxLayout()
        row.setSpacing(10)
        specs = [
            (n_match, i18n.KO.STAT_MATCHED, "ok"),
            (n_over, i18n.KO.STAT_OVER_TOLERANCE, "over" if n_over else "none"),
            (n_miss, i18n.KO.STAT_NO_MATCH, "over" if n_miss else "none"),
            (n_slot, i18n.KO.STAT_SLOT_MISMATCH, "none"),
        ]
        if not self._coord_mode:
            del specs[1]              # 유사도 모드엔 '허용 오차' 개념이 없다
        for value, caption, tone in specs:
            row.addWidget(self._stat_tile(value, caption, tone))
        row.addStretch(1)
        return row

    def _stat_tile(self, value: int, caption: str, tone: str) -> QFrame:
        tile = QFrame(self._summary_card)
        tile.setProperty("role", "statTile")
        lay = QVBoxLayout(tile)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(2)
        num = QLabel(f"{value:,}", tile)
        num.setProperty("role", "statValue")
        num.setProperty("tone", tone)
        cap = QLabel(caption, tile)
        cap.setProperty("role", "statCaption")
        lay.addWidget(num)
        lay.addWidget(cap)
        return tile

    def _on_new_session(self) -> None:
        """★ 엑셀로 저장하기 전에 누르면 이번 세션 결과가 통째로 사라진다.

        바로 옆이 검토 버튼이라 오클릭 소지가 크고, 같은 앱의 다른 파괴적 동작
        ([선택 종료]·검토 닫기)은 전부 확인을 받는다 — 여기만 예외였다."""
        if self.has_unsaved_result():
            from PyQt6.QtWidgets import QMessageBox
            r = sheets.ask(
                self, i18n.KO.NEW_SESSION_CONFIRM_TITLE,
                i18n.KO.NEW_SESSION_CONFIRM_BODY,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if r != QMessageBox.StandardButton.Yes:
                return
        self.new_session_requested.emit()

    def has_unsaved_result(self) -> bool:
        """보여 줄 결과가 있는데 아직 한 번도 저장하지 않았는가."""
        return self._result is not None and not self._exported

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
            unmatched_original_quality=self.unmatched_original_chk.isChecked(),
        )
        self._exporter.signals.progress.connect(
            lambda d, t, msg: self._loading.set_progress(d, t, i18n.KO.LOAD_EXPORT)
        )
        self._exporter.signals.done.connect(self._on_export_done)
        self._exporter.signals.failed.connect(self._on_export_failed)
        self._exporter.start()

    def _on_export_done(self, path: str) -> None:
        self._loading.hide_overlay()
        self._exported = True
        # ★ 워커 시그널 슬롯에서 곧바로 모달을 열면 중첩 이벤트 루프가 생긴다 —
        #   한 틱 미뤄 워커가 완전히 빠져나간 뒤에 띄운다.
        self._pending_saved_path = Path(path)
        QTimer.singleShot(0, self._offer_open_saved_file)

    def _offer_open_saved_file(self) -> None:
        """저장 다음 행동은 예외 없이 '그 파일을 여는 것' 이다 — 경로만 보여 주면
        사용자가 긴 문자열을 외워 탐색기에서 다시 찾아가야 한다."""
        path = getattr(self, "_pending_saved_path", None)
        if path is None:
            return
        choice = sheets.choose(
            self, i18n.KO.APP_TITLE,
            i18n.KO.SAVE_SUCCESS_FMT.format(path=path),
            [("open_file", i18n.KO.BTN_OPEN_SAVED_FILE, "primary"),
             ("open_dir", i18n.KO.BTN_OPEN_SAVED_FOLDER, "ghost"),
             ("close", i18n.KO.MSG_BTN_CLOSE, "ghost")],
            default="open_file",
        )
        if choice == "open_file":
            self._open_in_os(path)
        elif choice == "open_dir":
            self._open_in_os(path.parent)

    @staticmethod
    def _open_in_os(target: Path) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _on_export_failed(self, msg: str) -> None:
        self._loading.hide_overlay()
        # ★ 가장 흔한 실패는 '결과 파일이 엑셀에서 열려 있음'(PermissionError) 이다.
        #   OS 원문("[Errno 13] Permission denied: C:\…")만 보여 주면 무엇을 해야
        #   할지 알 수 없다 — 다음 행동을 먼저 말하고, 바로 다시 시도할 수 있게 한다.
        locked = "permission" in msg.lower() or "errno 13" in msg.lower()
        body = (i18n.KO.SAVE_FAIL_LOCKED_FMT.format(error=msg) if locked
                else i18n.KO.SAVE_FAIL_FMT.format(error=msg))
        choice = sheets.choose(
            self, i18n.KO.APP_TITLE, body,
            [("retry", i18n.KO.BTN_RETRY_SAVE, "primary"),
             ("close", i18n.KO.MSG_BTN_CLOSE, "ghost")],
            default="retry",
        )
        if choice == "retry":
            self._on_export()

