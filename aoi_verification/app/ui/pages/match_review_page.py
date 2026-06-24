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

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu,
                              QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ... import i18n
from ...models.result import MatchResult, MissEntry
from ...models.slot import ImageItem
from ...utils import image_io
from ..widgets.neon_button import NeonButton
from ..widgets.no_wheel_slider import NoWheelSlider
from ..widgets.zoom_window import FullscreenViewer


_THUMB_PX = 140                             # 기준 썸네일 기본 크기 (#2)
_RUNNERUP_PX = int(_THUMB_PX * 0.8)         # 차순위는 20% 작게
_TILE_W = _RUNNERUP_PX + 12                 # 타일 1개 점유 폭(간격 포함)
_SIZE_MIN_PX = 100
_SIZE_MAX_PX = 360
# 후보 열 수는 가용 폭에 맞춰 동적으로 계산한다(가로 스크롤 방지, #3).
# (인라인 첫 줄 예약 폭은 _MatchRow._reserved_fixed_px 에서 현재 이미지 크기 기준으로
#  동적 계산 — 고정 상수 대신.)
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
                 subtle: bool = False, enable_context_menu: bool = True,
                 on_view=None, parent=None) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self._size = int(size)
        self._image_loaded = False
        # 우클릭 ‘크게보기’ 동작 — 콜백이 주어지면 단일 확대 대신 좌우 비교를 연다 (#5).
        self._on_view = on_view
        # 슬라이더 리사이즈를 재디코드 없이 처리하기 위한 원본(최대크기) 픽스맵.
        self._source_pix: QPixmap | None = None
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
        # 우클릭 컨텍스트 메뉴 (크게보기). 차순위 타일 내부 썸네일은 상위
        # _RunnerUpTile 이 좌우 비교 뷰어를 직접 열도록 비활성화한다 (#4).
        if enable_context_menu:
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.customContextMenuRequested.connect(self._on_context_menu)

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        if not self._image_loaded:
            self._image_loaded = True
            QTimer.singleShot(0, self._load)

    def _load(self) -> None:
        try:
            # mid 캐시(~800px)를 소스로 → 인라인 표시도 선명(고화질, #4). 슬라이더
            # 변경 시엔 재디코드 없이 이 보관 픽스맵을 재스케일.
            self._source_pix = image_io.load_thumb_qpixmap(
                self._path, _SIZE_MAX_PX, kind="mid")
            self._apply_scaled()
        except Exception:
            pass

    def _apply_scaled(self) -> None:
        if self._source_pix is None or self._source_pix.isNull():
            return
        self.setPixmap(self._source_pix.scaled(
            self._size, self._size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def set_size(self, size: int) -> None:
        """슬라이더로 크기를 바꿀 때 호출 (#2) — 재디코드 없이 보관 픽스맵 재스케일.
        아직 로드 전이면 다음 paint 에서 새 크기 기준으로 로드된다."""
        self._size = int(size)
        self.setFixedSize(self._size, self._size)
        self._apply_scaled()

    def _on_context_menu(self, pos) -> None:
        menu = QMenu(self)
        act = menu.addAction(i18n.KO.CTX_VIEW_LARGER)
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen is act:
            if self._on_view is not None:
                self._on_view()
            else:
                _open_fullscreen(self._path, self.window())


class _RunnerUpTile(QFrame):
    """클릭 가능한 차순위 후보 썸네일.  클릭 시 swap_requested(item, score).

    더블클릭/우클릭은 좌우(기준·후보) 비교 뷰어를 연다 (#4) — view_requested.
    """

    swap_requested = pyqtSignal(object, float)        # (ImageItem, score)
    view_requested = pyqtSignal(object)               # ImageItem (크게보기)

    def __init__(self, item: ImageItem, score: float, parent=None,
                 *, size: int = _RUNNERUP_PX) -> None:
        super().__init__(parent)
        self.item = item
        self.score = float(score)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(i18n.KO.RUNNERUP_TOOLTIP)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        # 지연 로드. 내부 썸네일의 단일 크게보기 메뉴는 끄고, 이 타일이 좌우
        # 비교 뷰어를 직접 연다 (#4).
        self._img = _LazyThumb(item.path, size=size, subtle=True,
                               enable_context_menu=False, parent=self)
        lay.addWidget(self._img, alignment=Qt.AlignmentFlag.AlignCenter)

        self._score_label = QLabel(f"{self.score * 100:.1f} %", self)
        self._score_label.setStyleSheet("color: #7FB3D5; font-size: 11px;")
        self._score_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._score_label)

    def set_size(self, size: int) -> None:
        """슬라이더 변경 시 썸네일을 그 자리에서 재스케일 (#2)."""
        self._img.set_size(size)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.swap_requested.emit(self.item, self.score)
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


class _MatchRow(QFrame):
    """한 매치 — 상단 한 줄(ref + 1위 매치 + 점수 + 토글) 아래에 차순위 후보
    그리드(20% 작게, 클릭 가능)를 줄바꿈으로 펼친다."""

    toggle_requested = pyqtSignal(object)                  # MatchResult
    swap_requested = pyqtSignal(object, object, float)     # (old_match, new_val_item, new_score)
    more_clicked = pyqtSignal(object)                      # self — ‘후보 한 줄 더 보기’ 후 스크롤 보정
    less_clicked = pyqtSignal(object)                      # self — ‘접기’ 후 스크롤 복귀

    def __init__(self,
                 match: MatchResult,
                 runners_up: list[tuple] | None = None,
                 parent=None,
                 *,
                 thumb_px: int = _THUMB_PX) -> None:
        super().__init__(parent)
        self.match = match
        self._is_unmatched = False
        # 썸네일 크기 (#2) — 차순위는 20% 작게 파생.
        # ``_requested_thumb_px`` 는 슬라이더 요청값, ``_thumb_px`` 는 행 폭에 맞춰
        # 클램프된 실제 적용값(가로 넘침 방지).  창 리사이즈 때 요청값으로 재클램프.
        self._requested_thumb_px = int(thumb_px)
        self._thumb_px = int(thumb_px)
        self._runnerup_px = max(40, int(thumb_px * 0.8))
        # 전체 차순위 후보 (정렬됨) 를 보관하고, 화면에는 일부 줄만 표시 (#5).
        self._runners_up = list(runners_up or [])     # [(ImageItem, score), ...]
        # 현재 화면에 만들어진 차순위 타일 — 슬라이더 인플레이스 재스케일용 (#2).
        self._runner_tiles: list["_RunnerUpTile"] = []
        # ‘후보 한 줄 더 보기’ 클릭마다 1 씩 늘어나는 표시 줄 수 (#5).
        self._visible_lines = 1
        self.setProperty("role", "card-soft")

        # 행 전체를 세로로 쌓는다: [상단 한 줄] → [차순위 후보 영역] (#4).
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)

        # ── 상단 한 줄 — slot · ref · → · 1위 매치 + 점수 · (stretch) · 토글 ──
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(12)

        # slot 라벨 + 그 아래 ‘크게 보기’ 버튼 (행당 1개, 좌우 비교 뷰어를 연다, #2).
        slot_host = QWidget(self)
        slot_lay = QVBoxLayout(slot_host)
        slot_lay.setContentsMargins(0, 0, 0, 0)
        slot_lay.setSpacing(4)
        self._slot_label = QLabel(match.slot, slot_host)
        self._slot_label.setStyleSheet(
            "color: #00D4FF; font-weight: 700; font-size: 14px;"
        )
        slot_lay.addWidget(self._slot_label)
        self.btn_view = NeonButton(i18n.KO.BTN_VIEW_LARGER, role="ghost")
        self.btn_view.clicked.connect(lambda: self._open_compare(0))
        slot_lay.addWidget(self.btn_view)
        slot_host.setMinimumWidth(110)
        top.addWidget(slot_host)

        # ref 이미지 — 우클릭 ‘크게보기’ 는 단일 확대 대신 좌우 비교로 (#5).
        self._ref_img = self._make_thumb(match.ref_path, size=self._thumb_px,
                                         on_view=lambda: self._open_compare(0))
        top.addWidget(self._ref_img)

        # 화살표
        arrow = QLabel("→", self)
        arrow.setStyleSheet("color: #7FB3D5; font-size: 28px;")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(arrow)

        # 1위 매치 이미지 + 점수 (수직 라벨링)
        primary_host = QWidget(self)
        primary_lay = QVBoxLayout(primary_host)
        primary_lay.setContentsMargins(0, 0, 0, 0)
        primary_lay.setSpacing(2)
        self._val_img = self._make_thumb(match.val_path, size=self._thumb_px,
                                         on_view=lambda: self._open_compare(0))
        primary_lay.addWidget(self._val_img,
                              alignment=Qt.AlignmentFlag.AlignCenter)
        score_label = QLabel(f"{match.score * 100:.1f} %", primary_host)
        score_label.setStyleSheet(
            "color: #FFD600; font-weight: 700; font-size: 14px;"
        )
        score_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        primary_lay.addWidget(score_label)
        top.addWidget(primary_host)

        # ── 첫 줄 차순위 후보 — 1위 매치 바로 옆(인라인)에 붙는다 (#3). ──
        # 이 컨테이너 안의 가로 레이아웃에 _first_cols() 개까지 채운다.
        self._first_line_host = QWidget(self)
        self._first_line_lay = QHBoxLayout(self._first_line_host)
        self._first_line_lay.setContentsMargins(0, 0, 0, 0)
        self._first_line_lay.setSpacing(8)
        self._first_line_lay.setAlignment(Qt.AlignmentFlag.AlignLeft)
        top.addWidget(self._first_line_host)

        top.addStretch(1)

        # ✕ 매치 없음 / ↩ 되돌리기 버튼
        self.btn_toggle = NeonButton(i18n.KO.BTN_MARK_NO_MATCH, role="danger")
        self.btn_toggle.clicked.connect(
            lambda: self.toggle_requested.emit(self.match)
        )
        top.addWidget(self.btn_toggle)

        outer.addLayout(top)

        # ── 차순위 후보 영역 — 첫 줄은 위 인라인, 추가 줄은 아래 그리드 (#3/#5). ──
        # 클릭하면 그 사진으로 매치 교체 (swap_requested).  처음엔 첫 줄만
        # 인라인으로 보이고 ‘후보 한 줄 더 보기’ 로 아래에 줄을 추가한다.
        # ‘매치 없음’ 처리 시 _candidate_host 전체(인라인 첫 줄 포함)를 숨긴다 (#1).
        if self._runners_up:
            self._runner_host = QWidget(self)
            host_lay = QVBoxLayout(self._runner_host)
            host_lay.setContentsMargins(0, 0, 0, 0)
            host_lay.setSpacing(6)

            # 추가 줄(2번째 줄부터)을 담는 그리드.
            self._runner_grid = QGridLayout()
            self._runner_grid.setContentsMargins(0, 0, 0, 0)
            self._runner_grid.setSpacing(8)
            self._runner_grid.setAlignment(Qt.AlignmentFlag.AlignLeft)
            host_lay.addLayout(self._runner_grid)

            # ‘후보 한 줄 더 보기’ / ‘접기’ 버튼 (#5/#4).
            self.btn_more = NeonButton(i18n.KO.RUNNERUP_MORE_ROW, role="ghost")
            self.btn_more.clicked.connect(self._on_more)
            self.btn_less = NeonButton(i18n.KO.RUNNERUP_LESS_ROW, role="ghost")
            self.btn_less.clicked.connect(self._on_less)
            self.btn_less.setVisible(False)
            more_bar = QHBoxLayout()
            more_bar.setContentsMargins(0, 0, 0, 0)
            more_bar.addWidget(self.btn_more)
            more_bar.addWidget(self.btn_less)
            more_bar.addStretch(1)
            host_lay.addLayout(more_bar)

            outer.addWidget(self._runner_host)
            self._layout_runner_tiles()
        else:
            self._runner_host = None
            self._runner_grid = None
            self.btn_more = None
            self.btn_less = None
            self._first_line_host.setVisible(False)

    def _row_width(self) -> int:
        """현재 행의 가용 너비 — 아직 표시 전이면 부모/페이지 너비로 추정."""
        w = self.width()
        if w <= 1:
            p = self.parentWidget()
            w = (p.width() if p is not None else 0) or 1280
        return w

    def _tile_w(self) -> int:
        """타일 1개 점유 폭 — 현재 차순위 썸네일 크기 + 간격 (#2)."""
        return self._runnerup_px + 12

    def _first_cols(self) -> int:
        """첫 줄(인라인) 후보 열 수 — 두 메인 이미지가 차지하고 남는 폭에만 채운다.

        예약 폭을 현재 이미지 크기 기준으로 동적 계산해, 이미지를 키우면 인라인
        후보가 줄거나 0 이 되어 가로 넘침이 생기지 않는다 (#3)."""
        reserved = self._reserved_fixed_px() + 2 * self._thumb_px
        avail = self._row_width() - reserved
        fit = avail // self._tile_w() if avail > 0 else 0
        return max(0, int(fit))

    def _grid_cols(self) -> int:
        """아래 추가 줄 후보 열 수 — 가용 폭에 맞게(가로 스크롤 방지, #3)."""
        avail = self._row_width() - 60
        fit = avail // self._tile_w() if avail > 0 else 0
        return max(1, int(fit))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        # 폭이 줄어 클램프 결과가 달라지면 이미지 크기를 다시 맞춘다 — 좁은 창에서도
        # 두 이미지가 행에 들어가 '매치 없음' 버튼이 잘리지 않게 (#2/#3).
        new_applied = max(_SIZE_MIN_PX,
                          min(self._requested_thumb_px, self._max_thumb()))
        if new_applied != self._thumb_px:
            # set_thumb_size 가 재클램프 + 후보 재배치까지 수행.
            self.set_thumb_size(self._requested_thumb_px)
            return
        # 창 크기가 바뀌어 열 수가 달라지면 후보를 다시 배치 (가로 넘침 방지/#3).
        if not self._runners_up:
            return
        cur = (self._first_cols(), self._grid_cols())
        if cur != getattr(self, "_last_cols", None):
            self._last_cols = cur
            self._layout_runner_tiles()

    def _make_tile(self, item: ImageItem, score: float, parent) -> "_RunnerUpTile":
        """후보 타일 하나를 만들고 swap/크게보기 시그널을 연결한다 (#3/#4)."""
        tile = _RunnerUpTile(item, score, parent=parent, size=self._runnerup_px)
        tile.swap_requested.connect(
            lambda it, s: self.swap_requested.emit(self.match, it, s)
        )
        tile.view_requested.connect(self._open_candidate_viewer)
        return tile

    def _open_candidate_viewer(self, item: ImageItem) -> None:
        """차순위 후보 크게보기 — 좌(기준)·우(후보) + 이전/다음 + 매치 버튼 (#4)."""
        from ..widgets.side_by_side_viewer import SideBySideViewer
        candidates = [(it, f"유사도 {s * 100:.1f}%")
                      for it, s in self._runners_up]
        start = 0
        for i, (it, _s) in enumerate(self._runners_up):
            if it.path == item.path:
                start = i
                break
        viewer = SideBySideViewer(
            self.match.ref_path, candidates, start,
            ref_caption=f"기준 — {self.match.ref_path.name}",
            action_label=i18n.KO.BTN_MATCH_THIS,
            parent=self.window(),
        )
        viewer.action_requested.connect(
            lambda it: self.swap_requested.emit(
                self.match, it, self._score_for(it))
        )
        viewer.exec()

    def _primary_val_item(self) -> ImageItem:
        """현재 1위 매치 val 의 ImageItem — runners_up 엔 없으므로 즉석 생성 (#5)."""
        return ImageItem(slot=self.match.slot, path=self.match.val_path,
                         side="val")

    def _open_compare(self, start: int = 0) -> None:
        """slot 아래 ‘크게 보기’ 버튼 / 기준·1위 썸네일 우클릭 — 좌(기준)·우(후보)
        비교 뷰어. 1위 매치를 후보 맨 앞에 포함해 실제 비교가 되도록 한다 (#2/#5)."""
        from ..widgets.side_by_side_viewer import SideBySideViewer
        candidates = [(self._primary_val_item(),
                       f"매치 {self.match.score * 100:.1f}%")]
        candidates += [(it, f"유사도 {s * 100:.1f}%")
                       for it, s in self._runners_up]
        viewer = SideBySideViewer(
            self.match.ref_path, candidates, max(0, int(start)),
            ref_caption=f"기준 — {self.match.ref_path.name}",
            action_label=i18n.KO.BTN_MATCH_THIS,
            parent=self.window(),
        )
        viewer.action_requested.connect(
            lambda it: self.swap_requested.emit(
                self.match, it, self._score_for(it))
        )
        viewer.exec()

    def _score_for(self, item: ImageItem) -> float:
        for it, s in self._runners_up:
            if it.path == item.path:
                return float(s)
        if Path(item.path) == Path(self.match.val_path):
            return float(self.match.score)
        return 0.0

    def _reserved_fixed_px(self) -> int:
        """행에서 두 메인 이미지를 제외한 고정 점유 폭 — slot·화살표·버튼·여백.

        이 폭을 뺀 나머지를 두 이미지가 나눠 가져야 가로로 넘치지 않는다."""
        try:
            btn = max(140, self.btn_toggle.sizeHint().width())
        except Exception:
            btn = 140
        # slot_host(min 110) + 화살표(~50) + 버튼 + 행 여백/스페이싱(~110).
        return 110 + 50 + btn + 110

    def _max_thumb(self) -> int:
        """현재 행 폭에서 가로 넘침 없이 허용되는 메인 이미지 한 변의 최대값."""
        avail = self._row_width() - self._reserved_fixed_px()
        return max(_SIZE_MIN_PX, avail // 2)

    def set_thumb_size(self, thumb_px: int) -> None:
        """슬라이더로 썸네일 크기 변경 (#2) — 타일을 재생성하지 않고 보유 픽스맵을
        그 자리에서 재스케일하고, 열 수를 다시 계산해 가로 넘침 없이 재배치 (#3).

        요청 크기가 행 폭을 넘기면 두 이미지가 행에 들어가도록 클램프해, 우측
        '매치 없음' 버튼이 잘리거나 가로 스크롤이 생기지 않게 한다."""
        self._requested_thumb_px = int(thumb_px)
        applied = max(_SIZE_MIN_PX, min(int(thumb_px), self._max_thumb()))
        self._thumb_px = applied
        self._runnerup_px = max(40, int(applied * 0.8))
        self._ref_img.set_size(applied)
        self._val_img.set_size(applied)
        for tile in self._runner_tiles:
            tile.set_size(self._runnerup_px)
        self._layout_runner_tiles()

    # ------------------------------------------------------------------
    def _ensure_runner_tiles(self, count: int) -> None:
        """필요한 개수만큼 차순위 타일을 (재사용 가능하게) 생성해 둔다."""
        count = min(count, len(self._runners_up))
        host = self._runner_host or self
        while len(self._runner_tiles) < count:
            item, score = self._runners_up[len(self._runner_tiles)]
            self._runner_tiles.append(self._make_tile(item, score, host))

    def _visible_runner_count(self) -> int:
        fc = self._first_cols()
        gc = self._grid_cols()
        if fc == 0:
            # 인라인 자리가 없으면(큰 이미지) 후보를 사라지게 두지 말고 아래
            # 그리드에 첫 줄부터 배치 (#3).
            return min(gc * max(1, self._visible_lines), len(self._runners_up))
        extra = max(0, self._visible_lines - 1)
        return min(fc + extra * gc, len(self._runners_up))

    def _layout_runner_tiles(self) -> None:
        """첫 줄(인라인) + 아래 그리드에 기존 타일을 재배치 (재생성 없음).

        열 수는 가용 폭에 맞춰 계산해 가로 스크롤이 생기지 않게 한다 (#3).
        ``_visible_lines`` 로 보이는 줄 수를 조절(더 보기/접기, #5).
        """
        if not self._runners_up:
            return
        fc = self._first_cols()
        gc = self._grid_cols()
        need = self._visible_runner_count()
        self._ensure_runner_tiles(need)
        # 두 레이아웃에서 기존 위젯을 떼어낸다(삭제하지 않고 재사용).
        for lay in (self._first_line_lay, self._runner_grid):
            if lay is None:
                continue
            while lay.count():
                lay.takeAt(0)
        for i, tile in enumerate(self._runner_tiles):
            if i >= need:
                tile.setVisible(False)
                continue
            tile.setVisible(True)
            if i < fc:
                self._first_line_lay.addWidget(tile)
            elif self._runner_grid is not None:
                j = i - fc
                self._runner_grid.addWidget(tile, j // gc, j % gc)
        if self.btn_more is not None:
            self.btn_more.setVisible(need < len(self._runners_up))
        if self.btn_less is not None:
            self.btn_less.setVisible(self._visible_lines > 1)

    def _on_more(self) -> None:
        """‘후보 한 줄 더 보기’ — 표시 줄 수를 1 늘린다 (#5).
        높이 변화로 스크롤이 튀지 않게 페이지가 위치를 보정한다 (#6)."""
        self._visible_lines += 1
        self._layout_runner_tiles()
        self.more_clicked.emit(self)

    def _on_less(self) -> None:
        """‘접기’ — 펼친 줄을 전부 한 번에 접고(첫 줄만 남김), 페이지가 이 행을
        최상단으로 스크롤 복귀한다 (#1/#6)."""
        self._visible_lines = 1
        self._layout_runner_tiles()
        self.less_clicked.emit(self)

    def _make_thumb(self, path: Path, *, size: int = _THUMB_PX,
                    subtle: bool = False, on_view=None) -> QLabel:
        # 지연 로드 + 우클릭 ‘크게보기’ 지원 (#6-4/#13).
        return _LazyThumb(path, size=size, subtle=subtle, on_view=on_view,
                          parent=self)

    def set_unmatched(self, unmatched: bool) -> None:
        self._is_unmatched = unmatched
        if unmatched:
            # 빨간 강조는 가장 바깥 프레임 테두리에만 둔다 (배경 틴트/내부 위젯
            # 테두리 없음) (#1). 자식 위젯에 번지지 않도록 셀렉터를 self 로 한정.
            self.setStyleSheet(
                "_MatchRow { border: 2px solid #FF2D55; border-radius: 6px; }"
            )
            self.btn_toggle.setText(i18n.KO.BTN_RESTORE_MATCH)
            self.btn_toggle.setRole("ghost")
            # 후보 영역(인라인 첫 줄 + 아래 그리드/‘더 보기’)을 모두 숨긴다 (#1/#3).
            self._set_candidates_visible(False)
        else:
            self.setStyleSheet("")
            self.btn_toggle.setText(i18n.KO.BTN_MARK_NO_MATCH)
            self.btn_toggle.setRole("danger")
            # 후보 영역을 이전 표시 상태로 복원한다 (#1).
            self._set_candidates_visible(True)

    def _set_candidates_visible(self, visible: bool) -> None:
        """인라인 첫 줄 + 아래 후보 호스트의 표시 여부를 한꺼번에 토글 (#1/#3).

        후보가 아예 없는 행이면 첫 줄 컨테이너는 계속 숨김 상태로 둔다.
        """
        if self._runners_up:
            self._first_line_host.setVisible(visible)
        if self._runner_host is not None:
            self._runner_host.setVisible(visible)


class MatchReviewPage(QWidget):
    """자동 매치 결과 검토 — 잘못된 매치를 ‘매치 없음’ 으로 표시."""

    finished = pyqtSignal(list, list)        # (kept_matches, unmatched_refs)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._matches: list[MatchResult] = []
        self._unmatched_keys: set[tuple] = set()    # MatchResult.key set
        self._rows: list[_MatchRow] = []
        self._rows_by_key: dict[tuple, _MatchRow] = {}
        self._score_cache = None
        self._val_pool: dict | None = None
        self._candidates_by_ref: dict | None = None
        self._thumb_px = _THUMB_PX                  # 사진 크기 (#2)
        self._resize_timer = QTimer(self)           # 슬라이더 드래그 디바운스
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._apply_thumb_size)
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

        # 요약 라벨 + 사진 크기 슬라이더 (#2)
        summary_row = QHBoxLayout()
        self._summary_label = QLabel("", self)
        self._summary_label.setStyleSheet("color: #00FFA3; font-weight: 700;")
        summary_row.addWidget(self._summary_label)
        summary_row.addStretch(1)
        size_label = QLabel(i18n.KO.IMAGE_SIZE_LABEL, self)
        size_label.setStyleSheet("color: #7FB3D5;")
        summary_row.addWidget(size_label)
        # 마우스 휠로는 조절 불가 (NoWheelSlider).
        self.size_slider = NoWheelSlider(Qt.Orientation.Horizontal, self)
        self.size_slider.setRange(_SIZE_MIN_PX, _SIZE_MAX_PX)
        self.size_slider.setValue(self._thumb_px)
        self.size_slider.setSingleStep(20)
        self.size_slider.setPageStep(80)
        self.size_slider.setFixedWidth(180)
        self.size_slider.valueChanged.connect(self._on_size_changed)
        summary_row.addWidget(self.size_slider)
        self.size_value = QLabel(f"{self._thumb_px} px", self)
        self.size_value.setStyleSheet("color: #7FB3D5;")
        self.size_value.setFixedWidth(56)
        summary_row.addWidget(self.size_value)
        root.addLayout(summary_row)

        # 매치 리스트 (세로 스크롤만). 가로 스크롤은 끄고 창 너비에 맞춰
        # 후보 타일이 줄바꿈 되도록 한다 (#4).
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        self._scroll = scroll                       # 더 보기/접기 스크롤 보정용 (#1/#6).
        host = QWidget()
        host.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.MinimumExpanding)
        scroll.setWidget(host)
        self._scroll_host = host
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        # 매치 행 영역. ‘매치 없음’ 처리해도 이 자리에 그대로 두고 빨간
        # 테두리로만 표시한다 (#1).
        self._list_layout = QVBoxLayout()
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        outer.addLayout(self._list_layout)

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
                   val_pool: dict | None = None,
                   candidates_by_ref: dict | None = None) -> None:
        """매치 검토 화면 초기화.

        ``score_cache`` 와 ``val_pool`` 이 함께 주어지면 각 매치 행에 차순위
        후보를 클릭 가능한 형태로 보여주고, 클릭 시 그 후보로 매치를 교체한다.

        ``candidates_by_ref`` 가 주어지면 (fast 모드, #7) ``(slot, ref_path.name)``
        키로 미리 점수 내림차순 정렬된 ``[(ImageItem, score), ...]`` 후보 목록을
        직접 사용한다.  score_cache 가 비어있는 fast 모드에서도 후보가 보인다.
        """
        self._matches = list(matches)
        self._unmatched_keys.clear()
        # 차순위 swap / 재계산용으로 score_cache + val_pool 참조 보관.
        self._score_cache = score_cache
        self._val_pool = val_pool
        # fast 모드용 미리 계산된 후보 목록 (#7).
        self._candidates_by_ref = candidates_by_ref

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
        self._update_summary()
        # 검토 화면이 새로 열릴 때마다 스크롤을 항상 최상단으로 (이전 세션의
        # 스크롤 위치가 남지 않도록). 레이아웃 확정 후 적용.
        QTimer.singleShot(
            0, lambda: self._scroll.verticalScrollBar().setValue(0))

    def _on_size_changed(self, value: int) -> None:
        self._thumb_px = int(value)
        self.size_value.setText(f"{value} px")
        self._resize_timer.start(150)

    def _apply_thumb_size(self) -> None:
        """슬라이더 변경 적용 (#2) — 행 상태를 보존한 채 썸네일 크기만 갱신."""
        for row in self._rows:
            row.set_thumb_size(self._thumb_px)

    # ------------------------------------------------------------------
    def _row_top(self, row) -> int:
        """스크롤 콘텐츠 좌표계에서 행 상단의 y (스크롤바 값과 같은 단위)."""
        return row.mapTo(self._scroll_host, QPoint(0, 0)).y()

    def _on_row_more(self, row) -> None:
        """‘후보 한 줄 더 보기’ 후 — 행이 화면에서 같은 자리에 있도록 스크롤 보정 (#6)."""
        sb = self._scroll.verticalScrollBar()
        delta = self._row_top(row) - sb.value()
        QTimer.singleShot(
            0, lambda: sb.setValue(max(0, self._row_top(row) - delta)))

    def _on_row_less(self, row) -> None:
        """‘접기’ 후 — 접은 행의 사진들이 최상단에 오도록 스크롤 복귀 (#1/#6)."""
        sb = self._scroll.verticalScrollBar()
        QTimer.singleShot(0, lambda: sb.setValue(self._row_top(row)))

    def _append_row(self, match: MatchResult) -> "_MatchRow":
        runners = self._lookup_runners_up(match, self._score_cache, self._val_pool)
        row = _MatchRow(match, runners_up=runners, parent=self,
                        thumb_px=self._thumb_px)
        row.toggle_requested.connect(self._on_toggle)
        row.swap_requested.connect(self._on_swap)
        row.more_clicked.connect(self._on_row_more)
        row.less_clicked.connect(self._on_row_less)
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
        )
        # matches 리스트에서 old → new 교체
        for i, m in enumerate(self._matches):
            if m.key == old_match.key:
                self._matches[i] = new_match
                break
        # 새 매치를 고른 것이므로 unmatched 표시는 자동 해제 (빨간 테두리 제거).
        self._unmatched_keys.discard(old_match.key)
        # 행 위젯 제거 후 같은 자리에 새 행 삽입 (행은 옮기지 않는다, #1).
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
                thumb_px=self._thumb_px,
            )
            new_row.toggle_requested.connect(self._on_toggle)
            new_row.swap_requested.connect(self._on_swap)
            new_row.more_clicked.connect(self._on_row_more)
            new_row.less_clicked.connect(self._on_row_less)
            if layout_idx >= 0:
                self._list_layout.insertWidget(layout_idx, new_row)
            else:
                self._list_layout.addWidget(new_row)
            self._rows.append(new_row)
            self._rows_by_key[new_match.key] = new_row
        self._update_summary()

    def _lookup_runners_up(self, match: MatchResult, score_cache, val_pool) -> list:
        """주어진 매치의 ref 와 같은 slot 내 다른 val 들을 점수 내림차순으로 (자기 자신 제외).

        fast 모드 (#7): ``self._candidates_by_ref`` 에 ``(slot, ref_path.name)``
        키가 있으면 미리 정렬된 후보 목록에서 1위(현재 val) 를 제외하고 사용한다.
        그렇지 않으면 기존 score_cache + val_pool 로직으로 폴백한다 (basic 모드).

        _MatchRow 가 처음엔 한 줄만 보여주고 ‘후보 한 줄 더 보기’ 로 늘릴 수
        있도록 최대 ``_MAX_RUNNERS`` 개까지 보관해서 돌려준다 (#5/#16).
        """
        cbr = self._candidates_by_ref
        if cbr is not None:
            key = (match.slot, match.ref_path.name)
            if key in cbr:
                scored = [
                    (item, float(s))
                    for item, s in (cbr.get(key) or [])
                    if item.path != match.val_path
                ]
                return scored[:_MAX_RUNNERS]
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
            # 행은 제자리에 두고 빨간 테두리 강조만 토글한다 (#1).
            row.set_unmatched(now_unmatched)
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
