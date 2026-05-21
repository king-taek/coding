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

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QColor, QCursor, QKeySequence, QPixmap, QShortcut)
from PyQt6.QtWidgets import (QApplication, QDialog, QFrame, QGridLayout,
                              QHBoxLayout, QLabel, QListWidget,
                              QListWidgetItem, QMenu, QMessageBox,
                              QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ... import i18n
from ...models.result import MatchResult, MissEntry
from ...models.slot import ImageItem
from .neon_button import NeonButton
from .no_wheel_slider import NoWheelSlider
from .window_controls import add_fullscreen_shortcut, enable_window_controls


class _OriginalImageViewer(QDialog):
    """원본 이미지를 화면 대부분에 KeepAspectRatio 로 크게 보여주는 모달 (#4).

    ``FullscreenViewer`` 는 mid(다운스케일) 이미지를 쓰지만, 여기서는 사용자가
    요청한 대로 ``QPixmap(str(path))`` 로 ‘원본’ 파일을 직접 디코드해 보여준다.
    """

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(Path(path).name)
        self.setModal(True)
        self.setStyleSheet("background-color: #000;")
        scr = QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.resize(int(g.width() * 0.9), int(g.height() * 0.9))
        else:
            self.resize(1280, 800)

        self._pix = QPixmap(str(path))
        if self._pix.isNull():
            self._pix = QPixmap(800, 600)
            self._pix.fill(QColor(20, 28, 40))

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("background-color: #000;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._label)

        QShortcut(QKeySequence("Esc"), self, activated=self.close)
        self._redraw()

    def resizeEvent(self, e):  # noqa: N802
        self._redraw()
        super().resizeEvent(e)

    def _redraw(self) -> None:
        if self._pix.isNull():
            return
        target = self._label.size()
        if target.width() <= 0 or target.height() <= 0:
            target = self.size()
        scaled = self._pix.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(scaled)


def _open_fullscreen(path: Path, parent=None) -> None:
    """원본 이미지를 크게 보여준다 (#4 — 반드시 원본 화질)."""
    try:
        viewer = _OriginalImageViewer(Path(path), parent)
        viewer.exec()
    except Exception:
        pass


def _attach_view_larger(label: QLabel, path_getter) -> None:
    """라벨에 ‘크게 보기’ 동작을 붙인다 (#4).

    - 우클릭 → 컨텍스트 메뉴 ‘크게 보기’
    - 더블 클릭 → 동일한 원본 큰 화면

    ``path_getter`` 는 호출 시점의 원본 경로를 돌려주는 콜러블 (현재 ref 가
    바뀌는 좌측 패널에서도 항상 최신 경로를 가리키도록).
    """
    label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def _show_large() -> None:
        path = path_getter()
        if not path:
            return
        _open_fullscreen(Path(path), label.window())

    def _on_menu(pos) -> None:
        path = path_getter()
        if not path:
            return
        menu = QMenu(label)
        # 인라인 한글 리터럴 (i18n.KO 미수정 정책 — #4).
        act = menu.addAction("크게 보기")
        chosen = menu.exec(label.mapToGlobal(pos))
        if chosen is act:
            _show_large()

    label.customContextMenuRequested.connect(_on_menu)

    # 더블 클릭으로도 같은 큰 화면을 연다 (#4). 이벤트 필터 대신 메서드 대체로
    # 가볍게 처리 — 라벨은 이 다이얼로그 안에서만 쓰이므로 안전.
    orig_dbl = label.mouseDoubleClickEvent

    def _on_dbl(ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            _show_large()
        try:
            orig_dbl(ev)
        except Exception:
            pass

    label.mouseDoubleClickEvent = _on_dbl  # type: ignore[assignment]
    # 외부에서 동일 동작을 호출할 수 있도록 콜러블 노출 (스모크 테스트 등).
    label.view_larger = _show_large  # type: ignore[attr-defined]

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
    """후보 사진 — 클릭하면 매칭 확정. 원본 화질 lazy 로드 (paintEvent 트리거)."""

    picked = pyqtSignal(object)            # ImageItem

    def __init__(self, item: ImageItem, score: float, parent=None,
                 *, size: int = _CAND_PX) -> None:
        super().__init__(parent)
        self.item = item
        self.score = float(score)
        self._size = int(size)
        self._image_loaded = False
        # 슬라이더 리사이즈를 재디코드 없이 처리하기 위한 원본(최대크기) 픽스맵.
        self._source_pix: Optional[QPixmap] = None
        self.setProperty("role", "card-soft")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self._size + 16, self._size + _CAND_CAP_PX + 32)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)

        self._img_label = QLabel(self)
        self._img_label.setFixedSize(self._size, self._size)
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 우선 placeholder — paintEvent 첫 발생 시 원본을 비동기로 로드.
        ph = QPixmap(self._size, self._size)
        ph.fill(QColor(20, 28, 40))
        self._img_label.setPixmap(ph)
        # 우클릭 ‘크게보기’ (#13) — 후보 원본 경로를 그대로 연다.
        _attach_view_larger(self._img_label, lambda: self.item.path)
        lay.addWidget(self._img_label, alignment=Qt.AlignmentFlag.AlignCenter)

        score_text = f"유사도 {self.score * 100:.1f}%"
        sc = QLabel(score_text, self)
        sc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sc.setStyleSheet(
            "color: #00FFA3; font-weight: 700; padding: 2px;"
        )
        lay.addWidget(sc)

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
            # 첫 paint 이벤트 시점 = 위젯이 실제 viewport 에 들어온 시점.
            # 무거운 디코드를 paintEvent 안에서 동기로 돌리면 스크롤이 끊기므로
            # 다음 이벤트 루프 tick 에 지연 실행.
            QTimer.singleShot(0, self._load_full)

    def _load_full(self) -> None:
        try:
            # 원본을 최대 크기로 한 번만 디코드해 보관 → 슬라이더 변경 시
            # 재디코드 없이 그 자리에서 재스케일.
            self._source_pix = _load_full_pixmap_scaled(
                Path(self.item.path), _SIZE_MAX_PX,
            )
            self._img_label.setPixmap(self._source_pix.scaled(
                self._size, self._size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        except Exception:
            pass

    def set_display_size(self, size: int) -> None:
        """슬라이더로 타일 크기 변경 (#1) — 재생성/재디코드 없이 보관 픽스맵 재스케일."""
        self._size = int(size)
        self.setFixedSize(self._size + 16, self._size + _CAND_CAP_PX + 32)
        self._img_label.setFixedSize(self._size, self._size)
        if self._source_pix is not None and not self._source_pix.isNull():
            self._img_label.setPixmap(self._source_pix.scaled(
                self._size, self._size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        from PyQt6.QtGui import QFontMetrics
        fm = QFontMetrics(self._cap.font())
        self._cap.setText(fm.elidedText(
            self.item.filename, Qt.TextElideMode.ElideMiddle, self._size - 4,
        ))

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.picked.emit(self.item)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
class UnmatchedReviewDialog(QDialog):
    """매치 실패한 ref 들을 하나씩 검토해 신규 매칭을 만든다."""

    def __init__(self,
                 unmatched: list[MissEntry],
                 val_pool,
                 already_used_vals: Iterable[Path] = (),
                 score_cache=None,
                 parent=None) -> None:
        """``val_pool`` 키는 두 형태를 모두 지원:

        - ``(slot, side)`` → list[ImageItem]  : cross 모드 양방향 후보
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
        head.addSpacing(20)
        # 사진 크기 슬라이더 (#1) — 마우스 휠로는 조절 불가 (NoWheelSlider).
        size_label = QLabel(i18n.KO.IMAGE_SIZE_LABEL, self)
        size_label.setStyleSheet("color: #7FB3D5;")
        head.addWidget(size_label)
        self.size_slider = NoWheelSlider(Qt.Orientation.Horizontal, self)
        self.size_slider.setRange(_SIZE_MIN_PX, _SIZE_MAX_PX)
        self.size_slider.setValue(self._ref_px)
        self.size_slider.setSingleStep(20)
        self.size_slider.setPageStep(80)
        self.size_slider.setFixedWidth(160)
        self.size_slider.valueChanged.connect(self._on_size_changed)
        head.addWidget(self.size_slider)
        self.size_value = QLabel(f"{self._ref_px} px", self)
        self.size_value.setStyleSheet("color: #7FB3D5;")
        self.size_value.setFixedWidth(56)
        head.addWidget(self.size_value)
        head.addStretch(1)
        # 네비게이션 버튼
        self.btn_prev = NeonButton(i18n.KO.BTN_UNMATCHED_PREV, role="ghost")
        self.btn_prev.clicked.connect(self._go_prev)
        head.addWidget(self.btn_prev)
        self.btn_skip = NeonButton(i18n.KO.BTN_UNMATCHED_NEXT, role="warn")
        self.btn_skip.clicked.connect(self._skip)
        head.addWidget(self.btn_skip)
        self.btn_close = NeonButton(i18n.KO.BTN_UNMATCHED_CLOSE, role="primary")
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
        # 우클릭 ‘크게보기’ (#13) — 현재 검토 중인 ref 원본을 연다.
        _attach_view_larger(self.ref_img, lambda: self._cur_ref_path)
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
        cand_title = QLabel(i18n.KO.PANEL_MATCH_CANDIDATES, right)
        cand_title.setStyleSheet("color: #00D4FF; font-weight: 700;")
        rl.addWidget(cand_title)
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
        않고(인덱싱 보존) 표시 순서만 만든다.
        """
        normal = [i for i, e in enumerate(self._unmatched)
                  if not self._is_cancelled(e)]
        cancelled = [i for i, e in enumerate(self._unmatched)
                     if self._is_cancelled(e)]
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
        e = self._unmatched[idx]
        prefix = "✓ " if self._entry_resolved(idx) else ""
        return f"{prefix}[{e.slot}] {Path(e.path).name}"

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
            it.setToolTip(str(self._unmatched[idx].path))
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
        scored: list[tuple[float, ImageItem]] = []
        if candidates:
            # 점수 캐시 hit 이 대부분이지만, miss 시 pipeline.score 가 무거워
            # UI 가 잠깐 굳을 수 있다. 모래시계 커서로 사용자에게 알린다.
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
            try:
                for v in candidates:
                    s = self._lookup_or_compute_score(cur, v)
                    scored.append((s, v))
            finally:
                QApplication.restoreOverrideCursor()
            scored.sort(key=lambda x: x[0], reverse=True)

        self._clear_grid()
        if not scored:
            empty = QLabel(i18n.KO.UNMATCHED_REVIEW_NO_CANDIDATES, self._host)
            empty.setStyleSheet("color: #7FB3D5; padding: 20px;")
            self._grid.addWidget(empty, 0, 0)
            self.candidates_summary.setText("후보 0 장")
            return

        self.candidates_summary.setText(f"후보 {len(scored)} 장 (유사도 순)")
        # #4 — 한 줄에 최소 3개, 최대 5개. 작은 창에서도 3개를 강제(가로 스크롤
        # 허용)해 사용자가 한 번에 3개 이상 비교할 수 있게 한다.
        spacing = self._grid.spacing()
        viewport_width = self._scroll.viewport().width()
        fit = viewport_width // (self._cand_px + spacing)
        cols = max(3, min(5, fit))
        for i, (score, v) in enumerate(scored):
            tile = _CandidateTile(v, score, parent=self._host,
                                  size=self._cand_px)
            tile.picked.connect(self._on_pick)
            self._grid.addWidget(tile, i // cols, i % cols)

    # ------------------------------------------------------------------
    def _on_size_changed(self, value: int) -> None:
        """사진 크기 슬라이더 변경 (#1) — 행/타일을 재생성하지 않고 그 자리에서
        보관 픽스맵을 재스케일한다 (재빌드/재디코드 없음 → 대량 후보에서도 즉시)."""
        self._ref_px = int(value)
        self._cand_px = max(60, int(value * _CAND_RATIO))
        self.size_value.setText(f"{value} px")
        # 기준 사진 — 보관된 원본에서 재스케일.
        self.ref_img.setFixedSize(self._ref_px, self._ref_px)
        self._left_panel.setFixedWidth(self._ref_px + 40)
        if self._ref_source is not None and not self._ref_source.isNull():
            self.ref_img.setPixmap(self._ref_source.scaled(
                self._ref_px, self._ref_px,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        # 후보 타일 — 기존 타일을 그 자리에서 재스케일.
        for i in range(self._grid.count()):
            w = self._grid.itemAt(i).widget()
            if isinstance(w, _CandidateTile):
                w.set_display_size(self._cand_px)

    # ------------------------------------------------------------------
    def _lookup_or_compute_score(self,
                                  ref: MissEntry,
                                  val: ImageItem) -> float:
        """캐시 우선, 없으면 즉석 계산."""
        ref_path = Path(ref.path)
        val_path = Path(val.path)
        if self._score_cache is not None:
            s = self._score_cache.get_pair(ref.slot, ref_path, val_path)
            if s is not None:
                return float(s)
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
    def _on_pick(self, val_item: ImageItem) -> None:
        cur = self._current()
        if cur is None:
            return
        cur_path = Path(cur.path)
        cand_path = Path(val_item.path)
        score = self._lookup_or_compute_score(cur, val_item)
        # MatchResult 컨벤션 (main_window._merge_matches 와 일치):
        #   ref_path = ‘낮은 호기 (또는 ref 측)’ 경로,
        #   val_path = ‘높은 호기 (또는 val 측)’ 경로.
        # Phase A 미매칭(side="ref")은 cur 가 ref 측 → 그대로 둔다.
        # Phase B 미매칭(side="val")은 cur 가 val 측(높은 호기), candidate 가
        # ref 측(낮은 호기) → ref/val 을 교환해서 엑셀의 C/D 컬럼이 호기 라벨
        # 과 일치하도록.
        if cur.side == "val":
            ref_path, val_path = cand_path, cur_path
            direction = "B→A"
        else:
            ref_path, val_path = cur_path, cand_path
            direction = "A→B"
        self.new_matches.append(MatchResult(
            slot=cur.slot,
            ref_path=ref_path,
            val_path=val_path,
            score=float(score),
            direction=direction,
        ))
        self.resolved_refs.append(cur)
        self._used_vals.add(cand_path)
        # 같은 ref 가 다른 곳에서 다시 나오지 않도록 idx 만 전진.
        self._idx += 1
        self._render_current()

    def _skip(self) -> None:
        cur = self._current()
        if cur is not None:
            self.skipped_refs.append(cur)
        self._idx += 1
        self._render_current()

    def _go_prev(self) -> None:
        if self._idx <= 0:
            return
        # 이전 ref 로 가면서, 그 ref 가 (a) 이전에 매칭으로 확정됐다면 그 매칭을
        # 되돌리고 val 을 다시 사용 가능으로 풀어준다.
        self._idx -= 1
        cur = self._current()
        if cur is None:
            return
        # 되돌릴 신규 매칭이 있으면 제거. side="val" 매칭은 ref/val 을 교환해
        # 저장하므로 cur 가 m.ref_path / m.val_path 중 어디에 있는지 양쪽 모두 검사.
        for i in range(len(self.new_matches) - 1, -1, -1):
            m = self.new_matches[i]
            if m.slot != cur.slot:
                continue
            mr = Path(m.ref_path)
            mv = Path(m.val_path)
            cp = Path(cur.path)
            if cp == mr:
                self._used_vals.discard(mv)
            elif cp == mv:
                self._used_vals.discard(mr)
            else:
                continue
            self.new_matches.pop(i)
            # resolved_refs 에서도 동일 ref 한 건 제거
            for j, r in enumerate(self.resolved_refs):
                if r.slot == cur.slot and Path(r.path) == cp:
                    self.resolved_refs.pop(j)
                    break
            break
        # skip 으로 마크된 경우엔 그 항목만 풀어준다.
        for i in range(len(self.skipped_refs) - 1, -1, -1):
            r = self.skipped_refs[i]
            if (r.slot == cur.slot
                    and Path(r.path) == Path(cur.path)):
                self.skipped_refs.pop(i)
                break
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
