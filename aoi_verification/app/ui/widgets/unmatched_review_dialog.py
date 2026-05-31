"""매치 실패 사진 검토 다이얼로그 (#8).

엑셀 저장 전, ``FinalResult.unmatched_refs`` 의 사진들을 하나씩 다시 검토.
같은 슬롯의 검증 장비 후보를 ``SlotScoreCache`` 점수 내림차순으로 보여주고,
사용자가 클릭으로 매칭을 확정하면 새 ``MatchResult`` 가 누적된다.

- 다이얼로그가 닫힐 때 ``new_matches`` 와 ``resolved_refs`` 가 호출자에게 노출.
- 점수 캐시에 없는 (ref, val) 쌍은 그 자리에서 ``pipeline.score`` 로 계산
  (대부분 Stage 2 precompute 단계에서 이미 캐싱되어 있음).
- 이미 다른 매칭에 쓰인 val 은 후보에서 자동 제외 → 중복 매칭 방지.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QColor, QCursor, QIcon, QKeySequence, QPixmap,
                         QShortcut)
from PyQt6.QtWidgets import (QApplication, QDialog, QFrame, QGridLayout,
                              QHBoxLayout, QLabel, QListWidget,
                              QListWidgetItem, QMenu, QMessageBox,
                              QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ... import i18n
from ...models.result import MatchResult, MissEntry
from ...models.slot import ImageItem
from ...utils import image_io
from .loading_overlay import LoadingOverlay
from .neon_button import NeonButton
from .window_controls import add_fullscreen_shortcut, enable_window_controls


_LIST_THUMB_PX = 56     # 좌측 ‘실패 목록’ 항목 썸네일 한 변(px).
_REF_PX = 420           # 좌측 기준 사진 기본 크기 — 원본 화질에서 다운스케일.
_CAND_PX = 260          # 우측 후보 타일 기본 크기 — 썸네일 대신 원본을 lazy 로드.
_CAND_CAP_PX = 28       # 캡션 한 줄
# 크기 슬라이더 범위 (기준 사진 한 변, px) — 후보 타일은 비율로 파생 (#1).
_SIZE_MIN_PX = 250
_SIZE_MAX_PX = 700
_CAND_RATIO = _CAND_PX / _REF_PX        # ≈ 0.62


# ---------------------------------------------------------------------------
def _load_full_pixmap_scaled(path: Path, size: int) -> QPixmap:
    """원본 파일을 그대로 디코드한 뒤 ``size`` 박스에 맞춰 축소.

    캐시된 썸네일/mid 가 아닌 ‘원본 화질’ 을 그대로 보고 싶을 때 사용 — JPEG
    압축이 한 번만 적용된 결과를 사용자가 보게 된다.  full pixmap 은 함수
    스코프 안에서만 살아 있다가 GC 되므로 메모리는 축소된 사본만 유지.
    """
    fallback = QPixmap(size, size)
    fallback.fill(QColor(20, 28, 40))
    try:
        full = QPixmap(str(path))
        if full.isNull():
            return fallback
        return full.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    except Exception:
        return fallback


class _CandidateTile(QFrame):
    """후보 사진 타일.

    - 클릭 = 선택(파란 테두리)만, 즉시 매칭하지 않는다 (#1a).
    - 더블클릭 / 우클릭 = 좌우(기준·후보) 비교 크게보기 (#1e).
    - 이미지는 사전 생성된 mid 캐시를 소스로 빠르게 로드하고(#1c), 슬라이더로
      재디코드 없이 인플레이스 재스케일한다.
    """

    selected = pyqtSignal(object)          # ImageItem (클릭 선택)
    view_requested = pyqtSignal(object)    # ImageItem (크게보기)

    # objectName 스코프 셀렉터 — 최외곽 프레임에만 테두리. (QLabel 이 QFrame
    # 서브클래스라 ``QFrame {…}`` 는 내부 이미지/점수/캡션 라벨까지 번진다.)
    _SEL_STYLE = ("#candTile { border: 3px solid #00D4FF; border-radius: 8px;"
                  " background: rgba(0, 212, 255, 0.06); }")

    def __init__(self, item: ImageItem, score: float, parent=None,
                 *, size: int = _CAND_PX) -> None:
        super().__init__(parent)
        self.item = item
        self.score = float(score)
        self._size = int(size)
        self._image_loaded = False
        self._is_selected = False
        # 슬라이더 리사이즈를 재디코드 없이 처리하기 위한 소스(최대크기) 픽스맵.
        self._source_pix: Optional[QPixmap] = None
        self.setObjectName("candTile")
        self.setProperty("role", "card-soft")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self._size + 16, self._size + _CAND_CAP_PX + 32)
        # 우클릭 → 좌우 비교 크게보기 (#1e).
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)

        self._img_label = QLabel(self)
        self._img_label.setFixedSize(self._size, self._size)
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph = QPixmap(self._size, self._size)
        ph.fill(QColor(20, 28, 40))
        self._img_label.setPixmap(ph)
        lay.addWidget(self._img_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self._score_label = QLabel(f"유사도 {self.score * 100:.1f}%", self)
        self._score_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._score_label.setStyleSheet(
            "color: #00FFA3; font-weight: 700; padding: 2px;"
        )
        lay.addWidget(self._score_label)

        from PyQt6.QtGui import QFontMetrics
        cap = QLabel(self)
        cap.setFixedHeight(_CAND_CAP_PX)
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setProperty("role", "muted")
        cap.setWordWrap(False)
        fm = QFontMetrics(cap.font())
        cap.setText(fm.elidedText(
            item.filename, Qt.TextElideMode.ElideMiddle, self._size - 4,
        ))
        cap.setToolTip(item.filename)
        self._cap = cap
        lay.addWidget(cap)

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        if not self._image_loaded:
            self._image_loaded = True
            QTimer.singleShot(0, self._load_full)

    def _load_full(self) -> None:
        try:
            # 사전 생성된 mid 캐시(~800px)를 소스로 → 원본 디코드 없이 빠르게 (#1c).
            self._source_pix = image_io.load_thumb_qpixmap(
                Path(self.item.path), _SIZE_MAX_PX, kind="mid")
            self._apply_scaled()
        except Exception:
            pass

    def _apply_scaled(self) -> None:
        if self._source_pix is None or self._source_pix.isNull():
            return
        self._img_label.setPixmap(self._source_pix.scaled(
            self._size, self._size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def set_display_size(self, size: int) -> None:
        """슬라이더로 타일 크기 변경 (#1) — 재생성/재디코드 없이 보관 픽스맵 재스케일."""
        self._size = int(size)
        self.setFixedSize(self._size + 16, self._size + _CAND_CAP_PX + 32)
        self._img_label.setFixedSize(self._size, self._size)
        self._apply_scaled()
        from PyQt6.QtGui import QFontMetrics
        fm = QFontMetrics(self._cap.font())
        self._cap.setText(fm.elidedText(
            self.item.filename, Qt.TextElideMode.ElideMiddle, self._size - 4,
        ))

    def set_score(self, score: float) -> None:
        """같은 슬롯 재사용 시 새 기준 사진 기준으로 점수만 갱신 (#1b)."""
        self.score = float(score)
        self._score_label.setText(f"유사도 {self.score * 100:.1f}%")

    def set_selected(self, selected: bool) -> None:
        if selected == self._is_selected:
            return
        self._is_selected = bool(selected)
        self.setStyleSheet(self._SEL_STYLE if self._is_selected else "")

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.item)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.view_requested.emit(self.item)
        super().mouseDoubleClickEvent(event)

    def _on_context_menu(self, pos) -> None:
        menu = QMenu(self)
        act = menu.addAction(i18n.KO.CTX_VIEW_LARGER)
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen is act:
            self.view_requested.emit(self.item)


# ---------------------------------------------------------------------------
class UnmatchedReviewDialog(QDialog):
    """매치 실패한 ref 들을 하나씩 검토해 신규 매칭을 만든다."""

    def __init__(self,
                 unmatched: list[MissEntry],
                 val_pool,
                 already_used_vals: Iterable[Path] = (),
                 score_cache=None,
                 fast_results: dict | None = None,
                 parent=None) -> None:
        """``val_pool`` 키는 두 형태를 모두 지원:

        - ``(slot, side)`` → list[ImageItem]  : 후보 풀
        - ``slot``          → list[ImageItem]  : 단일 모드 호환 (side 무시)
        """
        super().__init__(parent)
        self._unmatched = list(unmatched)
        # (slot, side) 또는 slot 키 모두 받아들이도록 통일.
        self._val_pool_keyed: dict = {}
        for k, v in (val_pool or {}).items():
            self._val_pool_keyed[k] = list(v)
        self._used_vals: set[Path] = {Path(p) for p in already_used_vals}
        self._score_cache = score_cache
        # 효율 모드 선계산 top-K {(slot, ref_path): [(val_path, score)]} — 후보 풀이
        # 300장 이상이면 CPU 재계산 대신 이걸 재사용한다 (#1).
        self._fast_results = fast_results or {}
        self._idx = 0
        # 사진 크기 (#1) — 슬라이더로 조절. 후보 타일은 비율로 파생.
        self._ref_px = _REF_PX
        self._cand_px = _CAND_PX
        # 기준 사진 원본(최대크기) 픽스맵 — 슬라이더 변경 시 재디코드 없이 재스케일.
        self._ref_source: QPixmap | None = None
        # 현재 검토 중인 ref 원본 경로 — 우클릭 ‘크게보기’ 가 참조 (#13).
        self._cur_ref_path: Path | None = None
        # 결과: 호출자가 다이얼로그가 끝난 뒤 가져갈 데이터.
        self.new_matches: list[MatchResult] = []
        self.resolved_refs: list[MissEntry] = []     # 매칭 찾음
        self.skipped_refs: list[MissEntry] = []      # 사용자가 종료한 것
        # 선택(파란 테두리) 보류 상태 — ref 인덱스 → 선택한 후보 (확정 전, #1a).
        self._pending: dict[int, ImageItem] = {}
        # 현재 후보 타일들 + 후보 집합 키(같은 슬롯 재사용 판단, #1b).
        self._cand_tiles: list[_CandidateTile] = []
        self._last_cand_key: tuple | None = None
        self._close_prompted = False

        # 닫는 즉시 C++ 위젯 해제 — 매번 열 때마다 부모에 누적되지 않도록.
        # exec() 직후엔 Python 측 new_matches/resolved_refs 접근이 여전히 안전.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(
            i18n.KO.UNMATCHED_REVIEW_TITLE.format(n=len(self._unmatched))
        )
        self.setModal(True)
        scr = (self.parent().screen() if self.parent() is not None
               and hasattr(self.parent(), "screen") else None) \
            or QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.resize(min(1400, int(g.width() * 0.92)),
                        min(900, int(g.height() * 0.88)))
        else:
            self.resize(1400, 900)
        # 다이얼로그 창에 최소화/최대화 버튼 + F11 전체화면 토글 (#9).
        # 반드시 첫 show 이전에 플래그를 설정해야 창이 사라지지 않는다.
        enable_window_controls(self)
        add_fullscreen_shortcut(self)
        self._build()
        # 후보 풀이 작아(<300) 캐시 miss 를 그 자리에서 CPU 재계산할 때 띄우는 로딩 오버레이.
        # 다이얼로그 전체를 덮어 '계산 중'을 알린다(부모 위젯 size 추적).
        self._loading = LoadingOverlay(self)
        self._render_current()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # 상단 진행 + 안내
        head = QHBoxLayout()
        self.progress_label = QLabel("", self)
        self.progress_label.setStyleSheet(
            "color: #00D4FF; font-weight: 700; font-size: 15px;"
        )
        head.addWidget(self.progress_label)
        head.addStretch(1)
        # 네비게이션 버튼
        self.btn_prev = NeonButton(i18n.KO.BTN_UNMATCHED_PREV, role="ghost")
        self.btn_prev.clicked.connect(self._go_prev)
        head.addWidget(self.btn_prev)
        self.btn_skip = NeonButton(i18n.KO.BTN_UNMATCHED_NEXT, role="warn")
        self.btn_skip.clicked.connect(self._skip)
        head.addWidget(self.btn_skip)
        # 선택한 후보들을 실제 매칭으로 확정 (#1a) — 별도 액션.
        self.btn_confirm = NeonButton(i18n.KO.BTN_UNMATCHED_CONFIRM, role="primary")
        self.btn_confirm.clicked.connect(self._on_confirm)
        head.addWidget(self.btn_confirm)
        self.btn_close = NeonButton(i18n.KO.BTN_UNMATCHED_CLOSE, role="ghost")
        self.btn_close.clicked.connect(self.accept)
        head.addWidget(self.btn_close)
        root.addLayout(head)

        hint = QLabel(i18n.KO.UNMATCHED_REVIEW_HINT, self)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #7FB3D5; padding: 4px;")
        root.addWidget(hint)

        # 본문: 좌(실패 목록) + 중(기준 사진) + 우(후보 그리드)
        body = QHBoxLayout()
        body.setSpacing(16)

        # LIST: 매치 실패 ref 전체 목록 (#12) — 클릭하면 해당 항목으로 점프.
        list_panel = QFrame(self)
        list_panel.setProperty("role", "section")
        lpl = QVBoxLayout(list_panel)
        lpl.setContentsMargins(12, 12, 12, 12)
        lpl.setSpacing(6)
        list_title = QLabel("실패 목록", list_panel)   # 인라인 한글 (#12).
        list_title.setStyleSheet("color: #00D4FF; font-weight: 700;")
        lpl.addWidget(list_title)
        self.fail_list = QListWidget(list_panel)
        self.fail_list.setIconSize(QSize(_LIST_THUMB_PX, _LIST_THUMB_PX))
        self.fail_list.setStyleSheet(
            "QListWidget { background: #0A0F1C; border: 1px solid #1F2A3F; "
            "border-radius: 6px; color: #C8D6E5; }"
            "QListWidget::item { padding: 4px 6px; }"
            "QListWidget::item:selected { background: #123047; color: #00FFA3; }"
        )
        self.fail_list.itemClicked.connect(self._on_list_item_clicked)
        lpl.addWidget(self.fail_list, stretch=1)
        list_panel.setFixedWidth(260)
        body.addWidget(list_panel)
        # display-row → self._unmatched 인덱스 매핑 (#14 분리 정렬용).
        self._row_to_idx: dict[int, int] = {}
        self._idx_to_row: dict[int, int] = {}
        self._populate_list()

        # LEFT: 기준 사진
        left = QFrame(self)
        left.setProperty("role", "section")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(12, 12, 12, 12)
        ll.setSpacing(6)
        ref_title = QLabel(i18n.KO.PANEL_MATCH_REF, left)
        ref_title.setStyleSheet("color: #00D4FF; font-weight: 700;")
        ref_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ll.addWidget(ref_title)
        self.ref_filename = QLabel("", left)
        self.ref_filename.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ref_filename.setStyleSheet("color: #7FB3D5; padding: 2px;")
        self.ref_filename.setWordWrap(True)
        ll.addWidget(self.ref_filename)
        self.ref_img = QLabel(left)
        self.ref_img.setFixedSize(self._ref_px, self._ref_px)
        self.ref_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ref_img.setStyleSheet(
            "background: #050810; border: 1px solid #1F2A3F; border-radius: 6px;"
        )
        # 우클릭/더블클릭 ‘크게보기’ — 후보와 동일한 좌우 비교 창을 열되,
        # 기준 사진은 가장 유사도가 높은 후보부터(start=0) 보여준다 (#13).
        self.ref_img.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ref_img.customContextMenuRequested.connect(self._on_ref_context_menu)
        self.ref_img.mouseDoubleClickEvent = (  # type: ignore[assignment]
            lambda ev: self._open_compare(0)
            if ev.button() == Qt.MouseButton.LeftButton else None
        )
        ll.addWidget(self.ref_img, alignment=Qt.AlignmentFlag.AlignCenter)
        ll.addStretch(1)
        self._left_panel = left
        left.setFixedWidth(self._ref_px + 40)
        body.addWidget(left)

        # RIGHT: 후보 그리드 (스크롤)
        right = QFrame(self)
        right.setProperty("role", "section")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(12, 12, 12, 12)
        rl.setSpacing(6)
        cand_head = QHBoxLayout()
        cand_title = QLabel(i18n.KO.PANEL_MATCH_CANDIDATES, right)
        cand_title.setStyleSheet("color: #00D4FF; font-weight: 700;")
        cand_head.addWidget(cand_title)
        # '검증 장비 후보' 옆 '크게 보기' — 선택 후보(없으면 1순위)부터 좌우 비교.
        self.btn_zoom_cand = NeonButton(i18n.KO.BTN_VIEW_LARGER, role="ghost")
        self.btn_zoom_cand.clicked.connect(self._open_compare_selected)
        cand_head.addWidget(self.btn_zoom_cand)
        cand_head.addStretch(1)
        rl.addLayout(cand_head)
        self.candidates_summary = QLabel("", right)
        self.candidates_summary.setStyleSheet("color: #7FB3D5; padding: 2px;")
        rl.addWidget(self.candidates_summary)
        self._scroll = QScrollArea(right)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._host = QWidget()
        self._grid = QGridLayout(self._host)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setSpacing(10)
        self._scroll.setWidget(self._host)
        rl.addWidget(self._scroll, stretch=1)
        body.addWidget(right, stretch=1)

        root.addLayout(body, stretch=1)

    # ------------------------------------------------------------------
    def _clear_grid(self) -> None:
        while self._grid.count():
            it = self._grid.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

    # ------------------------------------------------------------------
    @staticmethod
    def _is_cancelled(entry: MissEntry) -> bool:
        """결과 검토 화면에서 ‘매칭 취소’ 로 발생한 실패인지 (#14)."""
        return "매칭 취소" in (getattr(entry, "note", "") or "")

    def _display_order(self) -> tuple[list[int], list[int]]:
        """리스트에 표시할 ``self._unmatched`` 인덱스 순서 (#14).

        ``(normal, cancelled)`` 두 인덱스 리스트를 돌려준다 — 일반 매치 실패가
        먼저, 그 다음 ‘매칭 취소’ 항목.  ``self._unmatched`` 자체는 재정렬하지
        않고(인덱싱 보존) 표시 순서만 만든다.  매치 확정된 항목은 목록에서
        제외한다(확정 시 사라지게).
        """
        normal = [i for i, e in enumerate(self._unmatched)
                  if not self._is_cancelled(e) and not self._entry_resolved(i)]
        cancelled = [i for i, e in enumerate(self._unmatched)
                     if self._is_cancelled(e) and not self._entry_resolved(i)]
        return normal, cancelled

    def _entry_resolved(self, idx: int) -> bool:
        """해당 인덱스의 ref 가 신규 매칭으로 확정됐는지 (#12 진행 표시)."""
        if idx < 0 or idx >= len(self._unmatched):
            return False
        e = self._unmatched[idx]
        ep = Path(e.path)
        for r in self.resolved_refs:
            if r.slot == e.slot and Path(r.path) == ep:
                return True
        return False

    def _list_label(self, idx: int) -> str:
        # 썸네일 표시이므로 파일명 대신 짧은 슬롯 태그만. (확정 항목은 목록에서
        # 제외되므로 ✓ 진행 표시는 더 이상 필요 없다.)
        return f"[{self._unmatched[idx].slot}]"

    def _populate_list(self) -> None:
        """전체 실패 목록을 채운다 — 일반 → 구분선 → 매칭 취소 (#12/#14)."""
        if not hasattr(self, "fail_list"):
            return
        self.fail_list.blockSignals(True)
        self.fail_list.clear()
        self._row_to_idx.clear()
        self._idx_to_row.clear()
        normal, cancelled = self._display_order()

        def _add_entry_row(idx: int) -> None:
            row = self.fail_list.count()
            it = QListWidgetItem(self._list_label(idx))
            # 파일명 텍스트 대신 작은 썸네일로 표시 — 파일명은 툴팁으로.
            path = Path(self._unmatched[idx].path)
            it.setIcon(QIcon(image_io.load_thumb_qpixmap(path, _LIST_THUMB_PX)))
            it.setToolTip(str(path))
            self.fail_list.addItem(it)
            self._row_to_idx[row] = idx
            self._idx_to_row[idx] = row

        for idx in normal:
            _add_entry_row(idx)

        if cancelled:
            # 구분선/헤더 — 선택 불가, 클릭해도 점프하지 않음.
            sep = QListWidgetItem("── 매칭 취소 목록 ──")
            sep.setFlags(Qt.ItemFlag.NoItemFlags)
            sep.setForeground(QColor("#FF8A65"))
            self.fail_list.addItem(sep)
            for idx in cancelled:
                _add_entry_row(idx)

        self.fail_list.blockSignals(False)

    def _sync_list_selection(self) -> None:
        """현재 ``self._idx`` 항목을 리스트에서 강조 + 라벨 갱신 (#12)."""
        if not hasattr(self, "fail_list"):
            return
        # 진행 상태(✓)가 바뀌었을 수 있으므로 라벨을 모두 새로 그린다.
        self.fail_list.blockSignals(True)
        for row in range(self.fail_list.count()):
            idx = self._row_to_idx.get(row)
            if idx is None:
                continue          # 구분선 행
            self.fail_list.item(row).setText(self._list_label(idx))
        row = self._idx_to_row.get(self._idx)
        if row is not None:
            self.fail_list.setCurrentRow(row)
        else:
            self.fail_list.clearSelection()
        self.fail_list.blockSignals(False)
        self._refresh_list_colors()

    def _refresh_list_colors(self) -> None:
        """후보를 선택(보류)한 ref 는 실패 목록에서 파일명을 파란색으로 표시 (#4)."""
        if not hasattr(self, "fail_list"):
            return
        for row in range(self.fail_list.count()):
            idx = self._row_to_idx.get(row)
            if idx is None:
                continue                              # 구분선 행.
            item = self.fail_list.item(row)
            if idx in self._pending:
                item.setForeground(QColor("#00D4FF"))
            else:
                item.setForeground(QColor("#C8D6E5"))

    def _on_list_item_clicked(self, item: QListWidgetItem) -> None:
        row = self.fail_list.row(item)
        idx = self._row_to_idx.get(row)
        if idx is None:
            return                # 구분선/헤더 클릭은 무시.
        self._idx = idx
        self._render_current()

    # ------------------------------------------------------------------
    def _current(self) -> Optional[MissEntry]:
        if self._idx < 0 or self._idx >= len(self._unmatched):
            return None
        return self._unmatched[self._idx]

    def _render_current(self) -> None:
        # 리스트 강조/진행 표시를 항상 현재 idx 와 동기화 (#12).
        self._sync_list_selection()
        cur = self._current()
        if cur is None:
            self._show_done()
            return

        total = len(self._unmatched)
        self.progress_label.setText(
            i18n.KO.UNMATCHED_REVIEW_PROGRESS_FMT.format(
                idx=self._idx + 1, total=total, slot=cur.slot,
            )
        )
        self.btn_prev.setEnabled(self._idx > 0)
        self.ref_filename.setText(Path(cur.path).name)
        self._cur_ref_path = Path(cur.path)

        # 현재 슬라이더 크기를 ref 패널에 반영 (#1).
        self.ref_img.setFixedSize(self._ref_px, self._ref_px)
        self._left_panel.setFixedWidth(self._ref_px + 40)
        # 기준 사진 — 원본을 최대 크기로 한 번만 디코드해 보관하고, 현재 크기로
        # 재스케일해 표시 (슬라이더 변경 시 재디코드 없이 재사용).
        self._ref_source = _load_full_pixmap_scaled(Path(cur.path), _SIZE_MAX_PX)
        self.ref_img.setPixmap(self._ref_source.scaled(
            self._ref_px, self._ref_px,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

        # 후보 = 같은 슬롯의 val_pool 중 (a) 이미 다른 매칭에 쓰이지 않은 항목.
        pool = (self._val_pool_keyed.get((cur.slot, cur.side))
                or self._val_pool_keyed.get(cur.slot)
                or [])
        candidates = [
            v for v in pool
            if Path(v.path) not in self._used_vals
        ]
        cand_key = (cur.slot, frozenset(Path(v.path) for v in candidates))
        scored: list[tuple[float, ImageItem]] = []
        if candidates:
            # 후보 풀이 300장 이상이면 효율 모드 선계산 점수를 재사용해 CPU 재계산을
            # 건너뛴다(즉시 표시). 미만이면 기존처럼 캐시 miss 를 그 자리 계산 (#1).
            allow_compute = len(candidates) < 300
            # 캐시에 없어 **실제로 다시 계산**해야 하는 후보 수를 먼저 센다 — 0 이면
            # 로딩을 띄우지 않고(즉시), >0 이면 로딩 오버레이로 진행을 보여준다 (#로딩).
            need = self._count_recompute(cur, candidates) if allow_compute else 0
            if need > 0:
                self._loading.show_overlay(i18n.KO.PHASE_SCORING)
                self._loading.set_progress(0, need, i18n.KO.PHASE_SCORING)
                self._recompute_done = 0

                def _on_recompute() -> None:
                    self._recompute_done += 1
                    self._loading.set_progress(
                        self._recompute_done, need,
                        i18n.KO.LOAD_SCORING_FMT.format(
                            done=self._recompute_done, total=need))
                    QApplication.processEvents()
                progress_cb = _on_recompute
            else:
                progress_cb = None
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
            try:
                scored = self._score_candidates(cur, candidates, allow_compute,
                                                on_computed=progress_cb)
            finally:
                QApplication.restoreOverrideCursor()
                if need > 0:
                    self._loading.hide_overlay()
            scored.sort(key=lambda x: x[0], reverse=True)

        if not scored:
            self._clear_grid()
            self._cand_tiles = []
            self._last_cand_key = None
            empty = QLabel(i18n.KO.UNMATCHED_REVIEW_NO_CANDIDATES, self._host)
            empty.setStyleSheet("color: #7FB3D5; padding: 20px;")
            self._grid.addWidget(empty, 0, 0)
            self.candidates_summary.setText("후보 0 장")
            return

        if cand_key == self._last_cand_key and self._cand_tiles:
            # 같은 슬롯 → 이미지 재로딩 없이 점수만 갱신 후 재정렬 (#1b).
            by_path = {t.item.path: t for t in self._cand_tiles}
            ordered: list[_CandidateTile] = []
            for s, v in scored:
                t = by_path.get(v.path)
                if t is None:
                    continue
                t.set_score(s)
                ordered.append(t)
            self._cand_tiles = ordered
        else:
            # 후보 집합이 달라졌으면 새로 빌드.
            self._clear_grid()
            self._cand_tiles = []
            for s, v in scored:
                tile = _CandidateTile(v, s, parent=self._host,
                                      size=self._cand_px)
                tile.selected.connect(self._on_tile_selected)
                tile.view_requested.connect(self._on_tile_view)
                self._cand_tiles.append(tile)
            self._last_cand_key = cand_key

        self.candidates_summary.setText(
            f"후보 {len(self._cand_tiles)} 장 (유사도 순)")
        # 현재 ref 의 선택(보류) 상태를 테두리로 반영 (#1a).
        sel = self._pending.get(self._idx)
        for t in self._cand_tiles:
            t.set_selected(sel is not None and t.item.path == sel.path)
        self._relayout_candidates()
        # 다음 사진으로 넘어오면 스크롤 최상단 복귀 (#1d).
        self._scroll.verticalScrollBar().setValue(0)

    # ------------------------------------------------------------------
    def _relayout_candidates(self) -> None:
        """viewport 폭에 맞춰 후보 열 수를 계산해 기존 타일을 재배치.

        **항상 가로 2개 이상**이 보이도록, 슬라이더가 설정한 ``_cand_px`` 가
        창에 비해 크면 2열이 들어갈 크기까지 자동 축소한다(#1). 타일 위젯은
        재사용(재생성/재디코드 없음)."""
        if not self._cand_tiles:
            return
        while self._grid.count():
            self._grid.takeAt(0)
        spacing = self._grid.spacing()
        margins = 8                      # 그리드 좌우 contentsMargins(4+4)
        frame = 16                       # 타일 1개의 chrome (set_display_size: size+16)
        vp = self._scroll.viewport().width() or self.width()
        # 2열이 들어갈 최대 타일 한 변 — 부족하면 슬라이더 값보다 축소.
        two_col_px = (vp - margins - spacing) // 2 - frame
        display_px = max(60, min(self._cand_px, two_col_px))
        tile_w = display_px + frame + spacing
        cols = max(2, max(1, vp // tile_w))
        for t in self._cand_tiles:
            if t._size != display_px:
                t.set_display_size(display_px)
            t.setVisible(True)
        for i, t in enumerate(self._cand_tiles):
            self._grid.addWidget(t, i // cols, i % cols)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._relayout_candidates()

    # ------------------------------------------------------------------
    def _count_recompute(self, cur: MissEntry, candidates: list) -> int:
        """캐시에 없어 그 자리에서 CPU 재계산해야 하는 후보 수(로딩 표시 여부 판단)."""
        if self._score_cache is None:
            return len(candidates)
        ref_path = Path(cur.path)
        n = 0
        for v in candidates:
            if self._score_cache.get_pair(cur.slot, ref_path, Path(v.path)) is None:
                n += 1
        return n

    def _score_candidates(self, cur: MissEntry, candidates: list,
                          allow_compute: bool,
                          on_computed=None) -> list[tuple[float, ImageItem]]:
        """후보들의 (score, item) 목록 — 내림차순 정렬 전.

        후보 풀이 300장 이상(``allow_compute=False``)이면 효율 모드 선계산
        top-K(``_fast_results``)를 그대로 재사용하고, 그게 없으면 점수 캐시 hit
        만 사용한다(둘 다 **CPU 재계산 없음**). 300장 미만이면 캐시 miss 를
        그 자리에서 계산한다 (#1)."""
        if not allow_compute:
            fres = self._fast_results.get((cur.slot, Path(cur.path)))
            if fres:
                by_path = {Path(v.path): v for v in candidates}
                out = []
                for vp, s in fres:
                    vi = by_path.get(Path(vp))
                    if vi is not None:
                        out.append((float(s), vi))
                return out
        out = []
        for v in candidates:
            cached = (self._score_cache is not None and
                      self._score_cache.get_pair(
                          cur.slot, Path(cur.path), Path(v.path)) is not None)
            s = self._lookup_or_compute_score(cur, v, allow_compute=allow_compute)
            if s is None:
                continue                     # ≥300 & 캐시 miss → 재계산 없이 제외.
            out.append((float(s), v))
            if on_computed is not None and not cached:
                on_computed()                # 실제 재계산한 후보만 진행 보고.
        return out

    # ------------------------------------------------------------------
    def _lookup_or_compute_score(self,
                                  ref: MissEntry,
                                  val: ImageItem,
                                  allow_compute: bool = True):
        """캐시 우선, 없으면(``allow_compute``) 즉석 계산. 재계산 불가 시 None."""
        ref_path = Path(ref.path)
        val_path = Path(val.path)
        if self._score_cache is not None:
            s = self._score_cache.get_pair(ref.slot, ref_path, val_path)
            if s is not None:
                return float(s)
        if not allow_compute:
            return None                    # ≥300: CPU 재계산 금지.
        # 캐시 miss — pipeline 으로 직접 계산. 캐시에 저장해서 재방문 시 빠르게.
        try:
            from ...similarity import pipeline as _pipeline
            rf = _pipeline.extract(ref_path)
            vf = _pipeline.extract(val_path)
            s = float(_pipeline.score(rf, vf))
        except Exception:
            s = 0.0
        if self._score_cache is not None:
            try:
                self._score_cache.put(ref.slot, ref_path, val_path, s)
            except Exception:
                pass
        return s

    # ------------------------------------------------------------------
    # 선택(보류) → 확정 흐름 (#1a)
    # ------------------------------------------------------------------
    def _on_tile_selected(self, val_item: ImageItem) -> None:
        """후보 클릭/‘이 후보로 선택’ — 현재 ref 의 보류 선택을 토글 (파란 테두리)."""
        cur = self._current()
        if cur is None:
            return
        prev = self._pending.get(self._idx)
        if prev is not None and Path(prev.path) == Path(val_item.path):
            # 같은 후보 재선택 → 해제.
            self._pending.pop(self._idx, None)
        else:
            self._pending[self._idx] = val_item
        sel = self._pending.get(self._idx)
        for t in self._cand_tiles:
            t.set_selected(sel is not None and t.item.path == sel.path)
        # 선택한 후보가 있으면 좌측 실패 목록에서 그 ref 를 파란색으로 (#4).
        self._refresh_list_colors()

    def _open_compare_selected(self) -> None:
        """'크게 보기' 버튼 — 선택한 후보(없으면 1순위)부터 좌우 비교 뷰어를 연다."""
        sel = self._pending.get(self._idx)
        start = 0
        if sel is not None:
            start = next((i for i, t in enumerate(self._cand_tiles)
                          if t.item.path == sel.path), 0)
        self._open_compare(start)

    def _open_compare(self, start_index: int) -> None:
        """좌(기준)·우(후보) 비교 크게보기 — 후보 더블클릭/우클릭 및 기준 우클릭
        공용.  ``self._cand_tiles`` 는 이미 유사도 내림차순이므로 start_index=0
        이면 가장 유사한 후보부터 보인다 (기준 우클릭용)."""
        from .side_by_side_viewer import SideBySideViewer
        cur = self._current()
        if cur is None or not self._cand_tiles:
            return
        candidates = [(t.item, f"유사도 {t.score * 100:.1f}%")
                      for t in self._cand_tiles]
        start = max(0, min(int(start_index), len(candidates) - 1))
        viewer = SideBySideViewer(
            Path(cur.path), candidates, start,
            ref_caption=f"기준 — {Path(cur.path).name}",
            action_label=i18n.KO.BTN_UNMATCHED_SELECT_THIS,
            parent=self,
        )
        viewer.action_requested.connect(self._on_tile_selected)
        viewer.exec()

    def _on_tile_view(self, val_item: ImageItem) -> None:
        """후보 크게보기 — 클릭한 후보 위치부터."""
        start = next((i for i, t in enumerate(self._cand_tiles)
                      if t.item.path == val_item.path), 0)
        self._open_compare(start)

    def _on_ref_context_menu(self, pos) -> None:
        """기준 사진 우클릭 → 유사도순 좌우 비교 크게보기."""
        menu = QMenu(self.ref_img)
        act = menu.addAction(i18n.KO.CTX_VIEW_LARGER)
        if menu.exec(self.ref_img.mapToGlobal(pos)) is act:
            self._open_compare(0)

    def _make_match(self, ref_entry: MissEntry, val_item: ImageItem) -> None:
        """선택된 (ref, 후보) 한 쌍을 MatchResult 로 확정 (side 별 ref/val 교환)."""
        cur_path = Path(ref_entry.path)
        cand_path = Path(val_item.path)
        score = self._lookup_or_compute_score(ref_entry, val_item)
        if ref_entry.side == "val":
            ref_path, val_path = cand_path, cur_path
        else:
            ref_path, val_path = cur_path, cand_path
        self.new_matches.append(MatchResult(
            slot=ref_entry.slot, ref_path=ref_path, val_path=val_path,
            score=float(score),
        ))
        self.resolved_refs.append(ref_entry)
        self._used_vals.add(cand_path)

    def _finalize_pending(self) -> int:
        """보류 선택을 모두 실제 매칭으로 확정. 확정한 건수를 돌려준다."""
        n = 0
        for idx in sorted(self._pending.keys()):
            if idx < 0 or idx >= len(self._unmatched):
                continue
            if self._entry_resolved(idx):
                continue
            val_item = self._pending[idx]
            if Path(val_item.path) in self._used_vals:
                continue                      # 이미 다른 ref 에 쓰인 후보.
            self._make_match(self._unmatched[idx], val_item)
            n += 1
        self._pending.clear()
        return n

    def _on_confirm(self) -> None:
        # 확정 직전 현재 항목의 표시 행 — 확정 후 그 자리로 올라온 다음
        # 미해결 항목으로 자연스럽게 이동하기 위해.
        prev_row = self._idx_to_row.get(self._idx, 0)
        n = self._finalize_pending()
        if n:
            QMessageBox.information(
                self, i18n.KO.APP_TITLE,
                i18n.KO.UNMATCHED_REVIEW_DONE_FMT.format(n=n),
            )
        # 확정으로 used_vals 가 바뀌어 후보 집합이 달라졌을 수 있으니 키 무효화.
        self._last_cand_key = None
        # 확정된 항목은 목록에서 사라진다(재생성) → 다음 미해결 항목으로 이동.
        self._populate_list()
        self._idx = self._next_idx_after(prev_row)
        self._render_current()

    def _next_idx_after(self, prev_row: int) -> int:
        """``_populate_list`` 재생성 후, ``prev_row`` 위치(또는 그 다음/이전)에
        남아 있는 첫 유효 항목의 ``self._unmatched`` 인덱스.  남은 게 없으면
        ``len(self._unmatched)`` 을 돌려준다(→ 완료 화면)."""
        count = self.fail_list.count()
        for row in range(prev_row, count):
            idx = self._row_to_idx.get(row)
            if idx is not None:
                return idx
        for row in range(min(prev_row, count) - 1, -1, -1):
            idx = self._row_to_idx.get(row)
            if idx is not None:
                return idx
        return len(self._unmatched)

    def _maybe_prompt_pending(self) -> None:
        """미확정(파란 테두리) 선택이 남은 채 창을 닫으면 매칭 여부를 묻는다 (#1a)."""
        if self._close_prompted or not self._pending:
            return
        self._close_prompted = True
        r = QMessageBox.question(
            self, i18n.KO.APP_TITLE, i18n.KO.UNMATCHED_CONFIRM_ON_CLOSE,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if r == QMessageBox.StandardButton.Yes:
            self._finalize_pending()

    def accept(self) -> None:  # noqa: D401
        self._maybe_prompt_pending()
        super().accept()

    def closeEvent(self, event):  # noqa: N802
        self._maybe_prompt_pending()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    def _skip(self) -> None:
        """다음 ref 로 이동 (확정하지 않음 — 보류 선택은 유지)."""
        self._idx += 1
        self._render_current()

    def _go_prev(self) -> None:
        if self._idx <= 0:
            return
        self._idx -= 1
        self._render_current()

    # ------------------------------------------------------------------
    def _show_done(self) -> None:
        self._clear_grid()
        self.progress_label.setText(
            i18n.KO.UNMATCHED_REVIEW_DONE_FMT.format(n=len(self.new_matches))
        )
        self.ref_filename.setText("")
        self.ref_img.clear()
        self._cur_ref_path = None
        self.candidates_summary.setText("")
        self.btn_prev.setEnabled(self._idx > 0)
        self.btn_skip.setEnabled(False)

    # ------------------------------------------------------------------
    @staticmethod
    def show_empty_message(parent) -> None:
        QMessageBox.information(
            parent, i18n.KO.APP_TITLE, i18n.KO.UNMATCHED_REVIEW_EMPTY,
        )
