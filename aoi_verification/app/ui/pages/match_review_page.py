"""올인원 / 사진 직접 선택 모드의 ‘매치 검토’ 페이지.

자동 매치 결과를 사용자가 스크롤하며 확인하고, 잘못된 매치는 ‘매치 없음’
처리해서 엑셀에 ‘기준 사진 + 빨간 파일명’ 행으로 들어가도록 한다.  또한
차순위 후보를 클릭하면 그것으로 매치를 ‘교체’ 할 수 있다.

흐름:
- 입력: list[MatchResult] (자동 매치 결과) + score_cache + val_pool (차순위 lookup 용)
- 출력 (finished 시): kept_matches, unmatched_refs
  · kept_matches : 사용자가 ‘유지’ 또는 ‘swap’ 한 매치들
  · unmatched_refs : 사용자가 ‘잘못된 매치’ 라고 표시한 ref 들 (MissEntry 로 변환)
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea,
                              QSizePolicy, QVBoxLayout, QWidget)

from ... import i18n
from ...models.result import MatchResult, MissEntry
from ...models.slot import ImageItem
from ...utils import image_io
from ..widgets.neon_button import NeonButton


_THUMB_PX = 140
_RUNNERUP_PX = int(_THUMB_PX * 0.8)         # 차순위는 20% 작게


class _RunnerUpTile(QFrame):
    """클릭 가능한 차순위 후보 썸네일.  클릭 시 swap_requested(item, score)."""

    swap_requested = pyqtSignal(object, float)        # (ImageItem, score)

    def __init__(self, item: ImageItem, score: float, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self.score = float(score)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(i18n.KO.RUNNERUP_TOOLTIP)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        self._img = QLabel(self)
        self._img.setFixedSize(_RUNNERUP_PX, _RUNNERUP_PX)
        self._img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img.setStyleSheet(
            "border: 1px dashed #1F2A3F; border-radius: 6px;"
        )
        self._img.setPixmap(image_io.load_thumb_qpixmap(item.path, _RUNNERUP_PX))
        lay.addWidget(self._img, alignment=Qt.AlignmentFlag.AlignCenter)

        self._score_label = QLabel(f"{int(round(self.score * 100))} %", self)
        self._score_label.setStyleSheet("color: #7FB3D5; font-size: 11px;")
        self._score_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._score_label)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.swap_requested.emit(self.item, self.score)
        super().mousePressEvent(event)


class _MatchRow(QFrame):
    """한 매치 — ref + 1위 매치 + 점수 + 차순위 2장 (20% 작게, 클릭 가능)."""

    toggle_requested = pyqtSignal(object)                  # MatchResult
    swap_requested = pyqtSignal(object, object, float)     # (old_match, new_val_item, new_score)

    def __init__(self,
                 match: MatchResult,
                 runners_up: list[tuple] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.match = match
        self._is_unmatched = False
        self._runners_up = list(runners_up or [])     # [(ImageItem, score), ...]
        self.setProperty("role", "card-soft")
        self.setMinimumHeight(_THUMB_PX + 32)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(12)

        # slot 라벨
        self._slot_label = QLabel(match.slot, self)
        self._slot_label.setStyleSheet(
            "color: #00D4FF; font-weight: 700; font-size: 14px;"
        )
        self._slot_label.setMinimumWidth(80)
        row.addWidget(self._slot_label)

        # ref 이미지
        self._ref_img = self._make_thumb(match.ref_path, size=_THUMB_PX)
        row.addWidget(self._ref_img)

        # 화살표
        arrow = QLabel("→", self)
        arrow.setStyleSheet("color: #7FB3D5; font-size: 28px;")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(arrow)

        # 1위 매치 이미지 + 점수 (수직 라벨링)
        primary_host = QWidget(self)
        primary_lay = QVBoxLayout(primary_host)
        primary_lay.setContentsMargins(0, 0, 0, 0)
        primary_lay.setSpacing(2)
        self._val_img = self._make_thumb(match.val_path, size=_THUMB_PX)
        primary_lay.addWidget(self._val_img,
                              alignment=Qt.AlignmentFlag.AlignCenter)
        score_label = QLabel(f"{int(round(match.score * 100))} %", primary_host)
        score_label.setStyleSheet(
            "color: #FFD600; font-weight: 700; font-size: 14px;"
        )
        score_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        primary_lay.addWidget(score_label)
        row.addWidget(primary_host)

        # 차순위 2장 — 클릭하면 그 사진으로 매치 교체 (swap_requested emit).
        if self._runners_up:
            sep = QLabel("│", self)
            sep.setStyleSheet("color: #1F2A3F; font-size: 36px;")
            row.addWidget(sep)
            for item, score in self._runners_up[:2]:
                tile = _RunnerUpTile(item, score, parent=self)
                tile.swap_requested.connect(
                    lambda it, s: self.swap_requested.emit(self.match, it, s)
                )
                row.addWidget(tile)

        row.addStretch(1)

        # ✕ 매치 없음 / ↩ 되돌리기 버튼
        self.btn_toggle = NeonButton(i18n.KO.BTN_MARK_NO_MATCH, role="danger")
        self.btn_toggle.clicked.connect(
            lambda: self.toggle_requested.emit(self.match)
        )
        row.addWidget(self.btn_toggle)

    def _make_thumb(self, path: Path, *, size: int = _THUMB_PX,
                    subtle: bool = False) -> QLabel:
        lab = QLabel(self)
        lab.setFixedSize(size, size)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 차순위 (subtle) 는 더 옅은 보더로 시각적으로 보조 정보임을 표시.
        if subtle:
            lab.setStyleSheet(
                "border: 1px dashed #1F2A3F; border-radius: 6px;"
            )
        else:
            lab.setStyleSheet(
                "border: 1px solid #1F2A3F; border-radius: 6px;"
            )
        lab.setPixmap(image_io.load_thumb_qpixmap(path, size))
        return lab

    def set_unmatched(self, unmatched: bool) -> None:
        self._is_unmatched = unmatched
        if unmatched:
            self.setStyleSheet(
                "QFrame { border: 2px solid #FF2D55; border-radius: 6px; "
                "  background: rgba(255, 45, 85, 0.05); }"
            )
            self.btn_toggle.setText(i18n.KO.BTN_RESTORE_MATCH)
            self.btn_toggle.setRole("ghost")
        else:
            self.setStyleSheet("")
            self.btn_toggle.setText(i18n.KO.BTN_MARK_NO_MATCH)
            self.btn_toggle.setRole("danger")


class MatchReviewPage(QWidget):
    """자동 매치 결과 검토 — 잘못된 매치를 ‘매치 없음’ 으로 표시."""

    finished = pyqtSignal(list, list)        # (kept_matches, unmatched_refs)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._matches: list[MatchResult] = []
        self._unmatched_keys: set[tuple] = set()    # MatchResult.key set
        self._rows: list[_MatchRow] = []
        self._rows_by_key: dict[tuple, _MatchRow] = {}
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        title = QLabel(i18n.KO.MATCH_REVIEW_TITLE, self)
        title.setProperty("role", "title")
        root.addWidget(title)

        hint = QLabel(i18n.KO.MATCH_REVIEW_HINT, self)
        hint.setProperty("role", "subtitle")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #7FB3D5;")
        root.addWidget(hint)

        # 요약 라벨
        self._summary_label = QLabel("", self)
        self._summary_label.setStyleSheet("color: #00FFA3; font-weight: 700;")
        root.addWidget(self._summary_label)

        # 매치 리스트 (스크롤)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        host = QWidget()
        host.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.MinimumExpanding)
        scroll.setWidget(host)
        self._list_layout = QVBoxLayout(host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        self._list_layout.addStretch(1)
        root.addWidget(scroll, stretch=1)

        # 하단 [완료] 버튼
        bar = QHBoxLayout()
        bar.addStretch(1)
        self.btn_done = NeonButton(i18n.KO.BTN_FINISH_REVIEW, role="primary")
        self.btn_done.setMinimumWidth(220)
        self.btn_done.setMinimumHeight(46)
        self.btn_done.clicked.connect(self._on_done)
        bar.addWidget(self.btn_done)
        root.addLayout(bar)

    # ------------------------------------------------------------------
    def load_state(self,
                   matches: list[MatchResult],
                   *,
                   score_cache=None,
                   val_pool: dict | None = None) -> None:
        """매치 검토 화면 초기화.

        ``score_cache`` 와 ``val_pool`` 이 함께 주어지면 각 매치 행에 차순위
        2 장(20% 작게) 을 클릭 가능한 형태로 보여주고, 클릭 시 그 후보로
        매치를 교체한다.
        """
        self._matches = list(matches)
        self._unmatched_keys.clear()
        # 차순위 swap / 재계산용으로 score_cache + val_pool 참조 보관.
        self._score_cache = score_cache
        self._val_pool = val_pool

        while self._list_layout.count():
            it = self._list_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        self._rows.clear()
        self._rows_by_key.clear()

        if not self._matches:
            empty = QLabel(
                "자동 매치된 항목이 없습니다.  [완료] 를 누르면 결과 화면으로 이동합니다.",
                self,
            )
            empty.setStyleSheet("color: #7FB3D5; padding: 20px;")
            self._list_layout.addWidget(empty)
        else:
            ordered = sorted(
                self._matches,
                key=lambda m: (m.slot, m.ref_path.name.lower()),
            )
            for m in ordered:
                self._append_row(m)
        self._list_layout.addStretch(1)
        self._update_summary()

    def _append_row(self, match: MatchResult) -> "_MatchRow":
        runners = self._lookup_runners_up(match, self._score_cache, self._val_pool)
        row = _MatchRow(match, runners_up=runners, parent=self)
        row.toggle_requested.connect(self._on_toggle)
        row.swap_requested.connect(self._on_swap)
        # stretch 직전에 삽입 (마지막 stretch 가 있으면).
        idx = self._list_layout.count()
        if idx > 0:
            last = self._list_layout.itemAt(idx - 1)
            # stretch item 은 widget=None.
            if last is not None and last.widget() is None:
                self._list_layout.insertWidget(idx - 1, row)
                self._rows.append(row)
                self._rows_by_key[match.key] = row
                return row
        self._list_layout.addWidget(row)
        self._rows.append(row)
        self._rows_by_key[match.key] = row
        return row

    def _on_swap(self,
                 old_match: MatchResult,
                 new_val_item,
                 new_score: float) -> None:
        """차순위 후보 클릭 시 매치 교체.  엔트리/행을 in-place 갱신."""
        from ...models.result import MatchResult as _M
        new_match = _M(
            slot=old_match.slot,
            ref_path=old_match.ref_path,
            val_path=new_val_item.path,
            score=float(new_score),
            direction=old_match.direction,
        )
        # matches 리스트에서 old → new 교체
        for i, m in enumerate(self._matches):
            if m.key == old_match.key:
                self._matches[i] = new_match
                break
        # unmatched 표시는 swap 시 자동 해제 (사용자가 새 매치를 골랐으니).
        self._unmatched_keys.discard(old_match.key)
        # 행 위젯 제거 후 같은 자리에 새 행 삽입.
        old_row = self._rows_by_key.pop(old_match.key, None)
        if old_row is not None:
            layout_idx = self._list_layout.indexOf(old_row)
            self._rows = [r for r in self._rows if r is not old_row]
            old_row.setParent(None)
            old_row.deleteLater()
            new_row = _MatchRow(
                new_match,
                runners_up=self._lookup_runners_up(
                    new_match, self._score_cache, self._val_pool,
                ),
                parent=self,
            )
            new_row.toggle_requested.connect(self._on_toggle)
            new_row.swap_requested.connect(self._on_swap)
            self._list_layout.insertWidget(layout_idx, new_row)
            self._rows.append(new_row)
            self._rows_by_key[new_match.key] = new_row
        self._update_summary()

    @staticmethod
    def _lookup_runners_up(match: MatchResult, score_cache, val_pool) -> list:
        """주어진 매치의 ref 와 같은 slot 내 다른 val 들 중 점수 상위 2 개 (자기 자신 제외)."""
        if score_cache is None or val_pool is None:
            return []
        slot_vals = val_pool.get(match.slot, []) or []
        scored: list[tuple] = []
        for v in slot_vals:
            if v.path == match.val_path:
                continue
            s = score_cache.get_pair(match.slot, match.ref_path, v.path)
            if s is None:
                continue
            scored.append((v, float(s)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:2]

    def _on_toggle(self, match: MatchResult) -> None:
        key = match.key
        if key in self._unmatched_keys:
            self._unmatched_keys.remove(key)
        else:
            self._unmatched_keys.add(key)
        row = self._rows_by_key.get(key)
        if row is not None:
            row.set_unmatched(key in self._unmatched_keys)
        self._update_summary()

    def _update_summary(self) -> None:
        total = len(self._matches)
        unmatched = len(self._unmatched_keys)
        kept = total - unmatched
        self._summary_label.setText(
            f"유지: {kept} 쌍  ·  매치 없음 처리: {unmatched} 장"
        )

    def _on_done(self) -> None:
        kept: list[MatchResult] = []
        unmatched_refs: list[MissEntry] = []
        for m in self._matches:
            if m.key in self._unmatched_keys:
                unmatched_refs.append(MissEntry(
                    slot=m.slot, side="ref", path=m.ref_path,
                    note="미매칭 (사용자 검토)",
                ))
            else:
                kept.append(m)
        self.finished.emit(kept, unmatched_refs)
