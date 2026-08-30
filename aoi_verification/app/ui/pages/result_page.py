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


def _clear_layout(layout) -> None:
    """레이아웃을 비우고 **중첩 레이아웃 안의 위젯까지** 지운다.

    `item.widget()` 만 보면 `addLayout` 으로 들어간 것들을 놓친다 — 그 안의 위젯은
    부모(카드)를 그대로 두고 있어서 레이아웃에서 빠져도 화면에 남는다.
    """
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)          # 이벤트 루프를 안 돌려도 즉시 화면에서 빠지게
            w.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            _clear_layout(child)
            child.deleteLater()


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
        self._review_scores = None
        self._coord_classical_refs: frozenset = frozenset()
        self._coord_mode: bool = False
        self._tolerance: float = _DFLT_TOL
        self._loading = LoadingOverlay(self)
        self._exporter: ExcelExporter | None = None
        # 이번 결과를 한 번이라도 저장했는가 — [새 검증 시작] 확인의 근거(U-06).
        self._exported = False
        # '저장 완료 HH:MM' 자리 — 요약 카드를 다시 그릴 때마다 새로 만들어진다
        # (즉 새 결과가 들어오면 자연히 비워진다).
        self._saved_label: QLabel | None = None
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
        # ★ `addWidget(..., alignment=AlignLeft)` 로 왼쪽에 붙이지 **않는다.**  정렬 플래그를
        #   주면 Qt 는 위젯을 늘리지 않고 **sizeHint 크기 그대로** 놓는데(실측 폭 306px),
        #   그러면 안쪽 줄바꿈 라벨이 좁아져 줄 수가 늘고 — 높이는 이미 정해진 뒤라 —
        #   통계 숫자와 파일 경로가 세로로 잘린다(실측 타일 35px / 필요 69px).
        #   대신 스트레치로 밀어 왼쪽에 두면 폭은 maximumWidth 까지 정상적으로 늘어난다.
        card_row = QHBoxLayout()
        card_row.setContentsMargins(0, 0, 0, 0)
        # 카드가 먼저 늘어나 maximumWidth 에서 멈추고, **남는 폭은 오른쪽 스페이서**가
        # 가져간다 — 그래야 카드가 왼쪽에 붙는다(스페이서가 없으면 남는 폭이 양쪽으로
        # 갈려 카드가 가운데로 밀린다).
        card_row.addWidget(self._summary_card, 100)
        card_row.addStretch(1)
        root.addLayout(card_row)

        root.addStretch(1)

        # ★ 엑셀 저장 옵션을 **모두 한 줄에** 모은다(사용자 선택 1안).
        #   전에는 '원본 화질' 은 버튼 줄 위, '전체 양식' 은 버튼 줄 아래로 갈라져
        #   110px 떨어져 있었다(실측).  전부 '엑셀로 저장' 에만 걸리는 옵션인데
        #   버튼 줄이 사이를 가르니, 원본 화질이 저장 옵션인지 화면 보기 옵션인지
        #   알 수 없었다.  옵션 → 실행 순서로 위에서 아래로 읽히게 한다.
        #   기본은 모두 해제 — 가볍고 빠른 출력이 기본값이다.
        #   ★ 정렬 축도 [엑셀로 저장](우측 끝)에 맞춘다.  옵션이 가운데, 실행이
        #     오른쪽이면 축이 갈려 소속이 보이지 않는다 — 실제로 '화면 보기 옵션'
        #     으로 오독됐다.  앞에 캡션을 세워 무엇에 걸리는 옵션인지 밝힌다.
        opt_row = QHBoxLayout()
        opt_row.setSpacing(22)
        opt_row.addStretch(1)
        opt_caption = QLabel(i18n.KO.EXPORT_OPTIONS_CAPTION, self)
        opt_caption.setProperty("role", "colHead")
        opt_row.addWidget(opt_caption)
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

        # ★ 29안 — '사진을 원본 화질로' 는 '미매칭만 원본' 을 **삼킨다**
        #   (`exporter._embed_image_path` 가 전역 옵션에서 먼저 끊는다).  지금까지는
        #   둘 다 켤 수 있었고, 그때 앞의 체크는 **아무 일도 하지 않으면서 켜져**
        #   있었다 — 화면이 사실과 다른 상태를 보여 준 것이다.  삼켜지는 쪽을
        #   비활성으로 내려 '지금은 의미 없음' 을 화면이 직접 말하게 한다.
        #   (묶음 캡션은 사용자 결정으로 삭제 — 배치·순서는 현행 그대로다.)
        self.original_quality_chk.toggled.connect(self._sync_option_dependency)

        self.full_template_chk = QCheckBox(i18n.KO.EXPORT_FULL_TEMPLATE_LABEL, self)
        self.full_template_chk.setChecked(False)
        self.full_template_chk.setToolTip(i18n.KO.EXPORT_FULL_TEMPLATE_TOOLTIP)
        opt_row.addWidget(self.full_template_chk)
        self._sync_option_dependency(self.original_quality_chk.isChecked())

        # ★ 31안 — 누르기 **전에** 목적지를 말한다.  [엑셀로 저장] 을 눌러야
        #   저장 대화상자에서 파일명을 처음 봤고, 두 번째 저장부터는 대화상자
        #   없이 같은 경로에 **덮어쓰는데** 그 사실이 버튼에 드러나지 않았다.
        #   덮어쓰기가 놀람이 아니라 예고가 된다.
        target_row = QHBoxLayout()
        target_row.addStretch(1)
        self.save_target_label = QLabel("", self)
        self.save_target_label.setProperty("role", "monoMuted")
        target_row.addWidget(self.save_target_label)

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
        # 옵션 줄과 실행 줄은 한 덩어리로 읽혀야 한다 — root 의 20px 대신 8px 로
        # 묶어 '옵션 → 실행' 이 하나의 블록이 되게 한다.
        tail = QVBoxLayout()
        tail.setContentsMargins(0, 0, 0, 0)
        tail.setSpacing(8)
        tail.addLayout(opt_row)
        tail.addLayout(target_row)
        tail.addLayout(bar)
        root.addLayout(tail)

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
                    tolerance: float = _DFLT_TOL,
                    review_scores=None,
                    coord_classical_refs=()) -> None:
        # ★ 새 결과가 들어오면 '저장했음' 은 무효다.  검토로 되돌아가 결과를 바꾼 뒤
        #   다시 들어오는 경로도 여기를 지나므로 자연히 리셋된다.
        if result is not self._result:
            self._exported = False
            # ★ 저장 경로도 함께 버린다.  ResultPage 는 세션마다 새로 만들지 않고
            #   하나를 재사용하므로, 남겨 두면 새 검증의 목적지 캡션이 **지난
            #   세션에 저장한 파일**을 가리킨다("다시 저장: 같은 파일에 덮어씀 ·
            #   옛 경로") — 누르기 전에 목적지를 보여주려던 것이 거짓말이 된다.
            self._save_path = None
        self._result = result
        self._template_path = template_path
        self._target_path = target_path
        # 매치 실패 검토에 사용할 후보 풀 / 점수 캐시 / 선계산 결과 (#8/#1).
        self._val_pool = val_pool
        self._score_cache = score_cache
        self._fast_results = fast_results
        # 실패 검토 창이 계산한 점수의 세션 보관처 + 좌표 세션의 고전 폴백 표식.
        # ★ 둘 다 **창보다 오래 산다** — [실패 검토] 는 누를 때마다 창을 새로 만든다.
        self._review_scores = review_scores
        self._coord_classical_refs = frozenset(coord_classical_refs or ())
        self._coord_mode = bool(coord_mode)
        self._tolerance = float(tolerance) if tolerance > 0 else _DFLT_TOL
        # 검토 후 다시 그려도 ‘자동 매치 결과 검토 권장’ 라벨이 살아 있도록
        # 마지막 auto_mode 값을 기억해 재렌더링에서 재사용한다.
        self._auto_mode = bool(auto_mode)
        # 기존 요약 비우기 — ★ **중첩 레이아웃 안까지** 훑어야 한다.  통계 타일은
        #   `addLayout` 으로 들어간 QHBoxLayout 안에 있어서 `item.widget()` 이 None 이고,
        #   예전에는 그래서 지워지지 않았다.  타일은 카드를 부모로 두므로 레이아웃에서
        #   빠져도 **그 자리에 그대로 남아** 새 타일 뒤로 삐져나왔다(결과를 다시 그릴
        #   때마다 누적).  재귀로 위젯까지 확실히 지운다.
        _clear_layout(self._summary_layout)

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

        # 결과 파일 위치 + 저장 완료 표시.  ★ 저장 성공 시트를 닫고 나면 화면에
        # '내가 저장을 했던가?' 에 답할 것이 아무것도 없었다(_exported 는 내부
        # 플래그였다).  자리를 예약해 두고 문자열만 갱신한다 — show/hide 를 쓰면
        # 저장 순간 줄이 생기며 레이아웃이 흔들린다.
        self._saved_label = None
        if self._target_path is not None:
            file_row = QHBoxLayout()
            file_row.setContentsMargins(0, 0, 0, 0)
            path_lab = QLabel(
                f"{i18n.KO.WORKING_FILE_LABEL}: {self._target_path}",
                self._summary_card)
            path_lab.setProperty("role", "monoMuted")
            path_lab.setWordWrap(True)
            file_row.addWidget(path_lab, 1)
            self._saved_label = QLabel("", self._summary_card)
            self._saved_label.setProperty("role", "statusPass")
            file_row.addWidget(self._saved_label)
            self._summary_layout.addLayout(file_row)

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

        self._refresh_save_target()      # 31안 — 누르기 전에 목적지를

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
            review_scores=self._review_scores,
            coord_classical_refs=self._coord_classical_refs,
        )
        sheets.run(dlg, full_bleed=True)
        if not dlg.new_matches:
            return
        # ★ 저장한 뒤에 결과를 바꿨다 — '저장했음' 은 여기서 무효가 된다.
        #   아래 `show_result` 는 **같은 객체**를 그대로 넘기므로(제자리 수정)
        #   그쪽의 `result is not self._result` 리셋에 걸리지 않는다.
        self._exported = False
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
        # ★ 실패 검토가 쓰는 값은 **전부** 다시 넘긴다.  `fast_results` 가 빠져 있어서
        #   한 번 검토하고 나면 두 번째 [실패 검토] 는 후보 풀 ≥300 인 슬롯에서 후보가
        #   0 장이 되고(재계산 금지 + 선계산 결과 없음), 좌표 세션이면 점수 분류
        #   (C-2)도 함께 무너진다.
        self.show_result(self._result,
                         template_path=self._template_path,
                         target_path=self._target_path,
                         val_pool=self._val_pool,
                         score_cache=self._score_cache,
                         fast_results=self._fast_results,
                         coord_mode=self._coord_mode,
                         tolerance=self._tolerance,
                         review_scores=self._review_scores,
                         coord_classical_refs=self._coord_classical_refs)

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
        # ★ 30안 — 워커가 보내는 문구(어느 시트·어느 슬롯)를 **그대로 띄운다**.
        #   예전엔 `msg` 를 버리고 고정 문구만 썼다.  행 수치는 여전히 오버레이의
        #   진행 라벨 몫이라 문구에 숫자가 들어가지 않는다(ko.py 단일 출처 규칙).
        self._exporter.signals.progress.connect(
            lambda d, t, msg: self._loading.set_progress(
                d, t, msg or i18n.KO.LOAD_EXPORT)
        )
        self._exporter.signals.done.connect(self._on_export_done)
        self._exporter.signals.failed.connect(self._on_export_failed)
        self._exporter.start()

    def _sync_option_dependency(self, original_on: bool) -> None:
        """'사진을 원본 화질로' 가 켜지면 '미매칭만 원본' 은 의미가 없다 (29안).

        비활성으로 내리되 **체크 상태는 건드리지 않는다** — 전역 옵션을 껐을 때
        사용자가 원래 두었던 선택이 그대로 돌아와야 한다(끄는 순간 값이 바뀌면
        '내가 언제 저걸 껐지' 가 된다).  툴팁은 그대로 유지한다(시안 명시)."""
        self.unmatched_original_chk.setEnabled(not bool(original_on))

    def _refresh_save_target(self) -> None:
        """[엑셀로 저장] 위에 **목적지**를 적는다 (31안).

        두 상태다 — 저장 전에는 '무엇이 생기는가', 한 번 저장한 뒤에는 '같은
        파일에 덮어쓴다'.  두 번째 저장이 조용히 덮어쓰던 것이 이 화면에서
        가장 놀라운 동작이었다."""
        lab = getattr(self, "save_target_label", None)
        if lab is None:
            return
        path = self._save_path or self._target_path
        if path is None:
            lab.setText("")
            lab.setToolTip("")
            return
        if self._exported:
            lab.setText(i18n.KO.SAVE_TARGET_AGAIN_FMT.format(path=path))
        else:
            lab.setText(i18n.KO.SAVE_TARGET_FMT.format(name=path.name))
        lab.setToolTip(str(path))

    def _on_export_done(self, path: str) -> None:
        self._loading.hide_overlay()
        self._exported = True
        # 시트를 닫아도 '저장했다' 는 사실이 화면에 남는다(자리 예약 라벨).
        if self._saved_label is not None:
            self._saved_label.setText(i18n.KO.RESULT_SAVED_AT_FMT.format(
                time=datetime.now().strftime("%H:%M")))
        # 이제부터는 '다시 저장 = 덮어쓰기' 다 — 캡션이 그 사실로 바뀐다(31안).
        self._refresh_save_target()
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

