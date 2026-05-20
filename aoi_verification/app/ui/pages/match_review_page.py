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

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu,
                              QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ... import i18n
from ...models.result import MatchResult, MissEntry
from ...models.slot import ImageItem
from ...utils import image_io
from ..widgets.neon_button import NeonButton
from ..widgets.zoom_window import FullscreenViewer


_THUMB_PX = 140
_RUNNERUP_PX = int(_THUMB_PX * 0.8)         # 차순위는 20% 작게
# 차순위 후보를 한 행에 기본 몇 장 보여줄지 (#16). ‘+더보기’ 로 늘릴 수 있다.
_DEFAULT_RUNNERS = 4
# _lookup_runners_up 가 보관하는 차순위 후보 최대 개수 (#16).
_MAX_RUNNERS = 50


def _open_fullscreen(path: Path, parent=None) -> None:
    """기존 풀스크린 뷰어로 원본 이미지를 크게 보여준다 (#13)."""
    try:
        viewer = FullscreenViewer(Path(path), parent)
        viewer.exec()
    except Exception:
        pass


class _LazyThumb(QLabel):
    """첫 paint 시점에 썸네일을 지연 디코드하고, 우클릭 ‘크게보기’ 를 지원 (#6-4/#13)."""

    def __init__(self, path: Path, *, size: int = _THUMB_PX,
                 subtle: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self._size = int(size)
        self._image_loaded = False
        self.setFixedSize(self._size, self._size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if subtle:
            self.setStyleSheet("border: 1px dashed #1F2A3F; border-radius: 6px;")
        else:
            self.setStyleSheet("border: 1px solid #1F2A3F; border-radius: 6px;")
        # placeholder — 첫 paint 후 실제 이미지로 교체.
        ph = QPixmap(self._size, self._size)
        ph.fill(QColor(20, 28, 40))
        self.setPixmap(ph)
        # 우클릭 컨텍스트 메뉴 (크게보기).
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        if not self._image_loaded:
            self._image_loaded = True
            QTimer.singleShot(0, self._load)

    def _load(self) -> None:
        try:
            self.setPixmap(image_io.load_thumb_qpixmap(self._path, self._size))
        except Exception:
            pass

    def _on_context_menu(self, pos) -> None:
        menu = QMenu(self)
        act = menu.addAction(i18n.KO.CTX_VIEW_LARGER)
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen is act:
            _open_fullscreen(self._path, self.window())


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

        # 지연 로드 + 우클릭 ‘크게보기’ 지원 (#6-4/#13).
        self._img = _LazyThumb(item.path, size=_RUNNERUP_PX, subtle=True,
                               parent=self)
        lay.addWidget(self._img, alignment=Qt.AlignmentFlag.AlignCenter)

        self._score_label = QLabel(f"{self.score * 100:.1f} %", self)
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
        # 전체 차순위 후보 (정렬됨) 를 보관하고, 화면에는 일부만 표시 (#16).
        self._runners_up = list(runners_up or [])     # [(ImageItem, score), ...]
        self._visible_runners = _DEFAULT_RUNNERS
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
        score_label = QLabel(f"{match.score * 100:.1f} %", primary_host)
        score_label.setStyleSheet(
            "color: #FFD600; font-weight: 700; font-size: 14px;"
        )
        score_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        primary_lay.addWidget(score_label)
        row.addWidget(primary_host)

        # 차순위 후보 — 클릭하면 그 사진으로 매치 교체 (swap_requested).
        # 기본 4장만 보여주고 ‘+N개 더 보기’ 로 늘릴 수 있다 (#16).
        if self._runners_up:
            sep = QLabel("│", self)
            sep.setStyleSheet("color: #1F2A3F; font-size: 36px;")
            row.addWidget(sep)
            # 차순위 타일 + ‘더보기’ 버튼을 담는 컨테이너 — in-place 재렌더용.
            self._runner_host = QWidget(self)
            self._runner_lay = QHBoxLayout(self._runner_host)
            self._runner_lay.setContentsMargins(0, 0, 0, 0)
            self._runner_lay.setSpacing(8)
            row.addWidget(self._runner_host)
            self._render_runners()
        else:
            self._runner_host = None
            self._runner_lay = None

        row.addStretch(1)

        # ✕ 매치 없음 / ↩ 되돌리기 버튼
        self.btn_toggle = NeonButton(i18n.KO.BTN_MARK_NO_MATCH, role="danger")
        self.btn_toggle.clicked.connect(
            lambda: self.toggle_requested.emit(self.match)
        )
        row.addWidget(self.btn_toggle)

    def _render_runners(self) -> None:
        """차순위 컨테이너를 비우고 ``_visible_runners`` 만큼 다시 채운다 (#16)."""
        if self._runner_lay is None:
            return
        while self._runner_lay.count():
            it = self._runner_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        shown = self._runners_up[:self._visible_runners]
        for item, score in shown:
            tile = _RunnerUpTile(item, score, parent=self._runner_host)
            tile.swap_requested.connect(
                lambda it, s: self.swap_requested.emit(self.match, it, s)
            )
            self._runner_lay.addWidget(tile)
        remaining = len(self._runners_up) - len(shown)
        if remaining > 0:
            more = NeonButton(
                i18n.KO.RUNNERUP_MORE_FMT.format(n=remaining), role="ghost",
            )
            more.clicked.connect(self._on_more)
            self._runner_lay.addWidget(more)

    def _on_more(self) -> None:
        """‘+N개 더 보기’ — 표시 개수를 사용자에게 물어 재렌더 (#16)."""
        total = len(self._runners_up)
        n, ok = QInputDialog.getInt(
            self, i18n.KO.RUNNERUP_MORE_TITLE, i18n.KO.RUNNERUP_MORE_PROMPT,
            min(total, max(self._visible_runners, _DEFAULT_RUNNERS)),
            1, total, 1,
        )
        if not ok:
            return
        self._visible_runners = int(n)
        self._render_runners()

    def _make_thumb(self, path: Path, *, size: int = _THUMB_PX,
                    subtle: bool = False) -> QLabel:
        # 지연 로드 + 우클릭 ‘크게보기’ 지원 (#6-4/#13).
        return _LazyThumb(path, size=size, subtle=subtle, parent=self)

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

        # 매치 리스트 (스크롤). 위쪽 = 유지 중인 매치(active), 아래쪽 =
        # ‘매치 없음’ 처리된 사진들 (deleted) 을 별도 섹션으로 분리 (#11).
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
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        # active 영역.
        self._list_layout = QVBoxLayout()
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        outer.addLayout(self._list_layout)

        # deleted 섹션 — 헤더 라벨 + 리스트. 비어 있으면 헤더를 숨긴다.
        self._deleted_header = QLabel(
            i18n.KO.MATCH_REVIEW_DELETED_SECTION, host,
        )
        self._deleted_header.setStyleSheet(
            "color: #FF6B81; font-weight: 700; font-size: 15px; "
            "padding: 10px 0 4px 0; border-top: 1px solid #1F2A3F;"
        )
        self._deleted_header.setVisible(False)
        outer.addWidget(self._deleted_header)

        self._deleted_layout = QVBoxLayout()
        self._deleted_layout.setContentsMargins(0, 0, 0, 0)
        self._deleted_layout.setSpacing(6)
        outer.addLayout(self._deleted_layout)

        outer.addStretch(1)
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
        후보를 클릭 가능한 형태로 보여주고, 클릭 시 그 후보로 매치를 교체한다.
        """
        self._matches = list(matches)
        self._unmatched_keys.clear()
        # 차순위 swap / 재계산용으로 score_cache + val_pool 참조 보관.
        self._score_cache = score_cache
        self._val_pool = val_pool

        for lay in (self._list_layout, self._deleted_layout):
            while lay.count():
                it = lay.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.deleteLater()
        self._rows.clear()
        self._rows_by_key.clear()

        if not self._matches:
            empty = QLabel(
                "자동 매치된 항목이 없습니다.  [완료] 를 누르면 결과 화면으로 이동합니다.",
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
        self._update_deleted_header()
        self._update_summary()

    def _append_row(self, match: MatchResult) -> "_MatchRow":
        runners = self._lookup_runners_up(match, self._score_cache, self._val_pool)
        row = _MatchRow(match, runners_up=runners, parent=self)
        row.toggle_requested.connect(self._on_toggle)
        row.swap_requested.connect(self._on_swap)
        self._list_layout.addWidget(row)
        self._rows.append(row)
        self._rows_by_key[match.key] = row
        return row

    def _update_deleted_header(self) -> None:
        """삭제 섹션에 항목이 하나라도 있을 때만 헤더를 보인다 (#11)."""
        has_deleted = self._deleted_layout.count() > 0
        self._deleted_header.setVisible(has_deleted)

    def _move_row(self, row: "_MatchRow", *, to_deleted: bool) -> None:
        """행 위젯을 active ↔ deleted 섹션 사이로 옮긴다 (#11)."""
        src = self._list_layout if to_deleted else self._deleted_layout
        dst = self._deleted_layout if to_deleted else self._list_layout
        idx = src.indexOf(row)
        if idx >= 0:
            src.takeAt(idx)
        dst.addWidget(row)
        self._update_deleted_header()

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
        # 새 매치를 고른 것이므로 unmatched 표시는 자동 해제. 행도 active 로 복귀.
        was_unmatched = old_match.key in self._unmatched_keys
        self._unmatched_keys.discard(old_match.key)
        # 행 위젯 제거 후 active 섹션 같은 자리에 새 행 삽입.
        old_row = self._rows_by_key.pop(old_match.key, None)
        if old_row is not None:
            # swap 으로 행은 항상 active 로 복귀시킨다.
            host_lay = (self._deleted_layout if was_unmatched
                        else self._list_layout)
            layout_idx = host_lay.indexOf(old_row)
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
            if was_unmatched:
                # deleted 섹션에서 빠져나오므로 active 끝에 붙인다.
                self._list_layout.addWidget(new_row)
            else:
                self._list_layout.insertWidget(layout_idx, new_row)
            self._rows.append(new_row)
            self._rows_by_key[new_match.key] = new_row
        self._update_deleted_header()
        self._update_summary()

    @staticmethod
    def _lookup_runners_up(match: MatchResult, score_cache, val_pool) -> list:
        """주어진 매치의 ref 와 같은 slot 내 다른 val 들을 점수 내림차순으로 (자기 자신 제외).

        _MatchRow 가 기본 4 장만 보여주고 ‘+더보기’ 로 늘릴 수 있도록 최대
        ``_MAX_RUNNERS`` 개까지 보관해서 돌려준다 (#16).
        """
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
        return scored[:_MAX_RUNNERS]

    def _on_toggle(self, match: MatchResult) -> None:
        key = match.key
        if key in self._unmatched_keys:
            self._unmatched_keys.remove(key)
            now_unmatched = False
        else:
            self._unmatched_keys.add(key)
            now_unmatched = True
        row = self._rows_by_key.get(key)
        if row is not None:
            row.set_unmatched(now_unmatched)
            # ‘매치 없음’ 처리된 행은 하단 ‘검토에서 삭제한 사진’ 섹션으로,
            # 되돌리면 다시 위쪽 active 섹션으로 이동 (#11).
            self._move_row(row, to_deleted=now_unmatched)
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
