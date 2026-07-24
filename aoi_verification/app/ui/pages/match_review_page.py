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
from .. import theme
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


def classify_row(score: float, coord_mode: bool, unmatched: bool) -> str:
    """행 상태 분류 — **기존 데이터만** 사용, 새 판정 로직 없음 (A2 표시용).

    - ``unmatched`` (사용자가 '매치 없음' 표시) 가 항상 우선 → ``"unmatched"``
    - 좌표 모드에서 ``score < 0`` (허용범위 초과 인코딩) → ``"over"``
    - 그 외 → ``"ok"``
    """
    if unmatched:
        return "unmatched"
    if coord_mode and score < 0:
        return "over"
    return "ok"


def tally(matches, unmatched_keys, coord_mode: bool) -> tuple[int, int, int]:
    """(일치, 허용 초과, 매치 없음) 개수 — 상단 집계 바 표시용 순수 함수."""
    ok = over = none = 0
    for m in matches:
        state = classify_row(m.score, coord_mode, m.key in unmatched_keys)
        if state == "ok":
            ok += 1
        elif state == "over":
            over += 1
        else:
            none += 1
    return ok, over, none


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
        # 프레임을 패널보다 밝은 thumb_frame 으로 — 어두운 다이가 어두운 패널에
        # 묻히지 않게 경계를 분명히(C1). 차순위는 점선으로 '후보' 성격 유지.
        if subtle:
            self.setStyleSheet(
                f"border: 1px dashed {theme.THUMB_FRAME}; border-radius: 6px;")
        else:
            self.setStyleSheet(
                f"border: 1px solid {theme.THUMB_FRAME}; border-radius: 6px;")
        # placeholder — 첫 paint 후 실제 이미지로 교체(패널보다 살짝 밝은 elev 바탕).
        ph = QPixmap(self._size, self._size)
        ph.fill(QColor(theme.ELEV))
        self.setPixmap(ph)
        # 우클릭 컨텍스트 메뉴 (크게보기). 차순위 타일 내부 썸네일은 상위
        # _RunnerUpTile 이 좌우 비교 뷰어를 직접 열도록 비활성화한다 (#4).
        if enable_context_menu:
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.customContextMenuRequested.connect(self._on_context_menu)

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        # 계측기 변형 — 모서리 레티클 틱(검사 부스 성격, C22 차별성). 절제된 강조.
        if theme.PROFILE.thumb_reticle:
            self._paint_reticle()
        if not self._image_loaded:
            self._image_loaded = True
            QTimer.singleShot(0, self._load)

    def _paint_reticle(self) -> None:
        from PyQt6.QtGui import QPainter, QPen
        w, h = self.width(), self.height()
        m, ln = 5, max(7, self._size // 12)     # 여백·틱 길이(크기 비례)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(QColor(theme.ACCENT))
        pen.setWidth(1)
        p.setPen(pen)
        for cx, sx in ((m, 1), (w - m, -1)):
            for cy, sy in ((m, 1), (h - m, -1)):
                p.drawLine(cx, cy, cx + sx * ln, cy)
                p.drawLine(cx, cy, cx, cy + sy * ln)
        p.end()

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
                 *, size: int = _RUNNERUP_PX,
                 coord_mode: bool = False, tolerance: float = 500.0) -> None:
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

        # 타일 아래 점수는 간결하게 — '허용범위 초과' 같은 긴 접미어는 빼고
        # 거리만, 초과 여부는 색으로 신호한다(허용 내=밝게, 초과=주의색).
        over = coord_mode and score < 0
        if coord_mode:
            tol = float(tolerance) if tolerance > 0 else 500.0
            dist = (1.0 - score) * tol if score >= 0 else (-score) * tol
            score_text = i18n.KO.SCORE_DIST_FMT.format(dist=dist)
        else:
            score_text = f"{score * 100:.1f} %"
        self._score_label = QLabel(score_text, self)
        # µm 신호 통일(C21): '허용 초과'는 어디서나 위험색(빨강), 정상은 중립 보조색.
        color = theme.DANGER if over else theme.INK2
        self._score_label.setStyleSheet(
            f"color: {color}; font-size: 12px; font-family: {theme.FONT_MONO};")
        self._score_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if over:
            self._score_label.setToolTip(i18n.KO.SCORE_DIST_OVER_FMT.format(dist=dist))
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
                 thumb_px: int = _THUMB_PX,
                 coord_mode: bool = False,
                 tolerance: float = 500.0) -> None:
        super().__init__(parent)
        self.match = match
        self._is_unmatched = False
        self._pulse = 0.0                # 상태 전환 펄스(0=없음) — paintEvent 가 읽음
        self._prev_state = None          # 초기 로드 시 펄스 억제용
        self._coord_mode = bool(coord_mode)
        self._tolerance = float(tolerance) if tolerance > 0 else 500.0
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
        self.setProperty("role", "row")   # 변형별 card/hairline (QSS role=row)

        # 행 전체를 세로로 쌓는다: [상단 한 줄] → [차순위 후보 영역] (#4).
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, theme.PROFILE.row_pad_v, 10, theme.PROFILE.row_pad_v)
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
        slot_lay.addStretch(1)             # 라벨+링크를 세로 중앙에 — 우측 점수와
        self._slot_label = QLabel(match.slot, slot_host)
        # 슬롯 라벨은 행 ID(보조) — 최중량은 결정값(점수)이 전담(C4 위계).
        self._slot_label.setStyleSheet(
            f"color: {theme.INK}; font-weight: 600; font-size: 14px;"
        )
        slot_lay.addWidget(self._slot_label)
        # 2차 액션은 조용한 링크형으로 (반복 8행에 테두리 버튼이 쌓이지 않게).
        self.btn_view = NeonButton(i18n.KO.BTN_VIEW_LARGER, role="link")
        self.btn_view.setMinimumHeight(36)   # ≥편안한 터치 타깃(C19)
        self.btn_view.clicked.connect(lambda: self._open_compare(0))
        slot_lay.addWidget(self.btn_view)
        slot_lay.addStretch(1)             # 같은 베이스라인(점수는 우측 VCenter)으로.
        slot_host.setFixedWidth(96)
        top.addWidget(slot_host)

        # ref 이미지 — 우클릭 ‘크게보기’ 는 단일 확대 대신 좌우 비교로 (#5).
        self._ref_img = self._make_thumb(match.ref_path, size=self._thumb_px,
                                         on_view=lambda: self._open_compare(0))
        top.addWidget(self._ref_img)

        # 화살표
        arrow = QLabel("→", self)
        arrow.setStyleSheet(f"color: {theme.MUTE}; font-size: 20px;")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(arrow)

        # 1위 매치 이미지 — 점수는 우측 metric 컬럼으로 분리 (A2 밀집 리스트).
        self._val_img = self._make_thumb(match.val_path, size=self._thumb_px,
                                         on_view=lambda: self._open_compare(0))
        top.addWidget(self._val_img)

        # ── 첫 줄 차순위 후보 — 1위 매치 바로 옆(인라인)에 붙는다 (#3). ──
        # 이 컨테이너 안의 가로 레이아웃에 _first_cols() 개까지 채운다.
        self._first_line_host = QWidget(self)
        self._first_line_lay = QHBoxLayout(self._first_line_host)
        self._first_line_lay.setContentsMargins(0, 0, 0, 0)
        self._first_line_lay.setSpacing(8)
        self._first_line_lay.setAlignment(Qt.AlignmentFlag.AlignLeft)
        top.addWidget(self._first_line_host)

        top.addStretch(1)

        # 이미지 영역과 우측 점수 컬럼을 1px 헤어라인으로 분리 — 점수가 '떠 있지'
        # 않고 눈금 컬럼으로 읽히게(C7). 변형별 색은 $row_divider.
        rule = QFrame(self)
        rule.setProperty("role", "vrule")
        rule.setFixedWidth(1)
        top.addWidget(rule)

        # ── 우측 고정 컬럼: 거리·점수(mono) → 판정 칩 → 컴팩트 토글 (A2). ──
        # metric 은 한 줄 — '허용범위 초과' 반복은 칩이 전담(삼중 중복 제거).
        self._metric_label = QLabel(self._format_score(match.score, verbose=False), self)
        self._metric_label.setProperty("role", "mono")
        self._metric_label.setFixedWidth(96)
        self._metric_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                        | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self._metric_label)

        # 판정 칩 — 정상(일치)은 배지 없이 조용히, 예외(초과/매치 없음)만 표시해
        # 시선이 예외로 가게 한다. 폭은 유지해 토글 열이 정렬되도록.
        self._chip = QLabel("", self)
        self._chip.setFixedSize(theme.PROFILE.chip_w, theme.PROFILE.chip_h)
        self._chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(self._chip)

        # ✕ 매치 없음 / ↩ 되돌리기 — 컴팩트 토글. 평소 중립, hover 에서만 위험색.
        self.btn_toggle = NeonButton(i18n.KO.BTN_NO_MATCH_COMPACT, role="ghost")
        self.btn_toggle.setProperty("compact", True)
        self.btn_toggle.setProperty("intent", "reject")
        # 오탭=오검증 — 세로 히트영역은 최소 44px 보장(행 높이가 썸네일이라 여유).
        self.btn_toggle.setFixedSize(theme.PROFILE.toggle_w,
                                     max(44, theme.PROFILE.toggle_h))
        self.btn_toggle.setToolTip(i18n.KO.BTN_MARK_NO_MATCH)
        self.btn_toggle.clicked.connect(
            lambda: self.toggle_requested.emit(self.match)
        )
        top.addWidget(self.btn_toggle)

        outer.addLayout(top)
        self._apply_state()

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
            self.btn_more = NeonButton(i18n.KO.RUNNERUP_MORE_ROW, role="link")
            self.btn_more.clicked.connect(self._on_more)
            self.btn_less = NeonButton(i18n.KO.RUNNERUP_LESS_ROW, role="link")
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
            # 허용 초과(실패) 행은 대안 후보를 처음부터 모두 펼친다 — 정밀 확인이
            # 필요한 행에서 후보를 '더 보기' 뒤로 숨기지 않는다(현장 지적, C4).
            # 일치(ok) 행은 그대로 첫 줄만 → 빠른 배치 확인 흐름 유지.
            if self.state() == "over":
                self._visible_lines = 99
                self._layout_runner_tiles()
        else:
            self._runner_host = None
            self._runner_grid = None
            self.btn_more = None
            self.btn_less = None
            self._first_line_host.setVisible(False)

    def _format_score(self, score: float, *, verbose: bool = True) -> str:
        """score 값을 표시 문자열로 변환.

        좌표 모드: 거리(µm)로 역산 (score ≥ 0 → 허용 내, score < 0 → 허용 초과).
        일반 모드: 0~100% 백분율.
        ``verbose=False`` 면 '허용범위 초과' 접미어를 빼고 거리만 (metric 컬럼용 —
        초과 여부는 옆 칩이 전담).
        """
        if self._coord_mode:
            from ... import i18n as _i18n
            if score >= 0:
                dist = (1.0 - score) * self._tolerance
                return _i18n.KO.SCORE_DIST_FMT.format(dist=dist)
            else:
                dist = (-score) * self._tolerance
                fmt = (_i18n.KO.SCORE_DIST_OVER_FMT if verbose
                       else _i18n.KO.SCORE_DIST_FMT)
                return fmt.format(dist=dist)
        return f"{score * 100:.1f} %"

    def _row_width(self) -> int:
        """현재 행의 가용 너비.

        스크롤 영역 안에서는 **뷰포트 폭**이 진실이다 — 큰 썸네일이 행의
        최소폭을 키우면 self.width() 는 뷰포트보다 넓게 남아(가로 스크롤),
        그 값을 믿으면 재클램프가 영영 안 일어난다.  조상에서 QScrollArea
        를 찾아 뷰포트 폭을 쓰고, 없으면(단독 생성/테스트) 기존 추정 폭."""
        p = self.parentWidget()
        while p is not None:
            if isinstance(p, QScrollArea):
                vw = p.viewport().width()
                if vw > 1:
                    return vw
                break
            p = p.parentWidget()
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
        tile = _RunnerUpTile(item, score, parent=parent, size=self._runnerup_px,
                             coord_mode=self._coord_mode, tolerance=self._tolerance)
        tile.swap_requested.connect(
            lambda it, s: self.swap_requested.emit(self.match, it, s)
        )
        tile.view_requested.connect(self._open_candidate_viewer)
        return tile

    def _open_candidate_viewer(self, item: ImageItem) -> None:
        """차순위 후보 크게보기 — 좌(기준)·우(후보) + 이전/다음 + 매치 버튼 (#4)."""
        from ..widgets.side_by_side_viewer import SideBySideViewer
        candidates = [(it, self._format_score(s))
                      for it, s in self._runners_up]
        start = 0
        for i, (it, _s) in enumerate(self._runners_up):
            if it.path == item.path:
                start = i
                break
        viewer = SideBySideViewer(
            self.match.ref_path, candidates, start,
            ref_caption=i18n.KO.COMPARE_REF_CAPTION_FMT.format(
                name=self.match.ref_path.name),
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
                       self._format_score(self.match.score))]
        candidates += [(it, self._format_score(s))
                       for it, s in self._runners_up]
        viewer = SideBySideViewer(
            self.match.ref_path, candidates, max(0, int(start)),
            ref_caption=i18n.KO.COMPARE_REF_CAPTION_FMT.format(
                name=self.match.ref_path.name),
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
        """행에서 두 메인 이미지를 제외한 고정 점유 폭 (A2 우측 컬럼 포함).

        slot·화살표·metric·칩·컴팩트 토글·여백/스페이싱.  이 폭을 뺀 나머지를
        두 이미지가 나눠 가져야 가로로 넘치지 않는다 (800×600 창 기준 검증)."""
        p = theme.PROFILE
        # slot_host(96) + 화살표(30) + metric(96) + 칩(chip_w) + 토글(toggle_w)
        # + 행 여백/스페이싱(96).  변형이 커져도 두 이미지가 클램프되어 안 넘침.
        return 96 + 30 + 96 + p.chip_w + p.toggle_w + 96

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

    def state(self) -> str:
        """현재 행 상태 — ``classify_row`` 위임 (표시 전용, 데이터 불변)."""
        return classify_row(self.match.score, self._coord_mode,
                            self._is_unmatched)

    def _apply_state(self) -> None:
        """상태(chip/rowState 프로퍼티·metric 색)를 한곳에서 적용 + repolish.

        인라인 setStyleSheet 대신 QSS 동적 프로퍼티 셀렉터를 쓴다
        (rowState="over"/"unmatched", chip="ok"/"over"/"none").
        """
        st = self.state()
        # 정상(일치)은 배지 없이 — 예외(초과/매치 없음)만 칩을 띄워 시선을 모은다.
        if st == "ok":
            self._chip.setText("")
            self._chip.setProperty("chip", "")
        else:
            self._chip.setText(i18n.KO.CHIP_OVER if st == "over"
                               else i18n.KO.CHIP_NO_MATCH)
            self._chip.setProperty("chip", "over" if st == "over" else "none")
        self.setProperty("rowState", st if st != "ok" else "")
        metric_color = theme.DANGER if st == "over" else theme.INK
        self._metric_label.setStyleSheet(
            f"color: {metric_color}; font-weight: 700; font-size: 13px;"
        )
        for w in (self._chip, self):
            w.style().unpolish(w)
            w.style().polish(w)
        # 예외 상태로 '전환'될 때만 한 번 배경 틴트 펄스(초기 로드엔 안 함).
        if (self._prev_state is not None and st != self._prev_state
                and st in ("over", "unmatched")):
            from .. import motion
            motion.pulse(self)
        self._prev_state = st

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        pulse = getattr(self, "_pulse", 0.0)
        if pulse <= 0.0:
            return
        from PyQt6.QtGui import QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(theme.DANGER)
        c.setAlpha(int(64 * pulse))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        r = theme.PROFILE.row_radius
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), r, r)

    def set_unmatched(self, unmatched: bool) -> None:
        self._is_unmatched = unmatched
        if unmatched:
            # 되돌리기(↩) 상태 — reject 의도 해제(호버에서 위험색 안 뜨게).
            self.btn_toggle.setText(i18n.KO.BTN_RESTORE_COMPACT)
            self.btn_toggle.setToolTip(i18n.KO.BTN_RESTORE_MATCH)
            self.btn_toggle.setProperty("intent", "")
            # 후보 영역(인라인 첫 줄 + 아래 그리드/‘더 보기’)을 모두 숨긴다 (#1/#3).
            self._set_candidates_visible(False)
        else:
            self.btn_toggle.setText(i18n.KO.BTN_NO_MATCH_COMPACT)
            self.btn_toggle.setToolTip(i18n.KO.BTN_MARK_NO_MATCH)
            self.btn_toggle.setProperty("intent", "reject")
            # 후보 영역을 이전 표시 상태로 복원한다 (#1).
            self._set_candidates_visible(True)
        self.btn_toggle.style().unpolish(self.btn_toggle)
        self.btn_toggle.style().polish(self.btn_toggle)
        # 빨간 테두리 등 상태 스타일은 rowState 프로퍼티로 일괄 적용 (#1).
        self._apply_state()

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
        self._thumb_px = theme.PROFILE.thumb_default_px   # 사진 크기 (#2) — 변형별
        self._resize_timer = QTimer(self)           # 슬라이더 드래그 디바운스
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._apply_thumb_size)
        # 좌표 매칭 모드 관련
        self._coord_mode: bool = False
        self._tolerance: float = 500.0
        self._coord_failed_count: int = 0
        # 키보드 탐색 — 현재 행 (↑↓ 로 이동, R 토글). 표시 전용 상태.
        self._current_row: "_MatchRow | None" = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 12)
        root.setSpacing(10)

        # ── 상단 집계/액션 바 (A2) — 탤리 · (stretch) · 사진 크기 · 검토 완료 ──
        bar = QHBoxLayout()
        bar.setSpacing(12)
        self._tally_label = QLabel("", self)
        self._tally_label.setTextFormat(Qt.TextFormat.RichText)
        bar.addWidget(self._tally_label)
        bar.addStretch(1)
        # '확인 필요만' 표시 필터 — 행 setVisible 만 바꾼다 (완료 동작 불변).
        self.btn_filter = NeonButton(i18n.KO.BTN_FILTER_NEEDS_CHECK, role="ghost")
        self.btn_filter.setCheckable(True)
        self.btn_filter.toggled.connect(lambda _c: self._apply_filter())
        bar.addWidget(self.btn_filter)
        size_label = QLabel(i18n.KO.IMAGE_SIZE_LABEL, self)
        size_label.setStyleSheet(f"color: {theme.MUTE};")
        bar.addWidget(size_label)
        # 마우스 휠로는 조절 불가 (NoWheelSlider).
        self.size_slider = NoWheelSlider(Qt.Orientation.Horizontal, self)
        self.size_slider.setRange(_SIZE_MIN_PX, _SIZE_MAX_PX)
        self.size_slider.setValue(self._thumb_px)
        self.size_slider.setSingleStep(20)
        self.size_slider.setPageStep(80)
        self.size_slider.setFixedWidth(150)
        self.size_slider.valueChanged.connect(self._on_size_changed)
        bar.addWidget(self.size_slider)
        self.size_value = QLabel(f"{self._thumb_px} px", self)
        self.size_value.setProperty("role", "mono")
        self.size_value.setStyleSheet(f"color: {theme.MUTE};")
        self.size_value.setFixedWidth(56)
        bar.addWidget(self.size_value)
        # [검토 완료] — 하단에서 상단 바로 이동 (동작 동일, 유지 카운트 표시).
        self.btn_done = NeonButton(i18n.KO.BTN_FINISH_REVIEW, role="primary")
        self.btn_done.setMinimumHeight(38)
        self.btn_done.clicked.connect(self._on_done)
        bar.addWidget(self.btn_done)
        root.addLayout(bar)

        # 키보드 힌트 — 단축키를 kbd 칩으로 (프로즈 대신 '키'로 읽히게).
        root.addWidget(self._build_key_hint())

        # 표/제도 시트 성격 변형(cleanroom·datum)만 상단 고정 컬럼 헤더를 얹어
        # 행이 '떠 있는 카드' 가 아니라 '눈금 잡힌 표/시트' 로 읽히게 한다(C22 차별성).
        if theme.PROFILE.list_header:
            root.addWidget(self._build_list_header())

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
        self._list_layout.setSpacing(theme.PROFILE.row_gap)
        outer.addLayout(self._list_layout)

        # '확인 필요만' 필터가 0건일 때의 빈 상태 — '멈춘 건지 다 끝난 건지'
        # 헷갈리지 않게 명시(A3/C23).  전체 보기로 돌아가는 안내를 함께.
        self._filter_empty = QLabel(i18n.KO.REVIEW_FILTER_EMPTY, host)
        self._filter_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._filter_empty.setStyleSheet(f"color: {theme.MUTE}; padding: 36px;")
        self._filter_empty.setVisible(False)
        outer.addWidget(self._filter_empty)

        outer.addStretch(1)
        root.addWidget(scroll, stretch=1)

    def _build_key_hint(self) -> QWidget:
        """단축키 힌트 줄 — 각 키를 kbd 칩으로, 설명은 흐린 텍스트로."""
        host = QWidget(self)
        lay = QHBoxLayout(host)
        lay.setContentsMargins(2, 0, 0, 0)
        lay.setSpacing(6)
        pairs = [("↑ ↓", i18n.KO.KEY_HINT_MOVE),
                 ("R", i18n.KO.KEY_HINT_REJECT),
                 ("Enter", i18n.KO.KEY_HINT_DONE)]
        for i, (key, desc) in enumerate(pairs):
            if i:
                lay.addSpacing(8)
            cap = QLabel(key, host)
            cap.setProperty("role", "kbd")
            lay.addWidget(cap)
            lbl = QLabel(desc, host)
            lbl.setStyleSheet(f"color: {theme.MUTE}; font-size: 11px;")
            lay.addWidget(lbl)
        lay.addStretch(1)
        return host

    def _build_list_header(self) -> QWidget:
        """검토 리스트 컬럼 헤더 — 행의 고정 컬럼(슬롯·거리·판정)에 정렬해 표/시트로
        읽히게(cleanroom 데이터테이블 / datum 제도 타이틀블록). 우측 클러스터는 행과
        동일한 고정 폭(metric 96 · 칩 · 토글)으로 맞춰 헤더가 그 위에 정확히 얹힌다."""
        p = theme.PROFILE
        host = QFrame(self)
        host.setProperty("role", "listHeader")
        lay = QHBoxLayout(host)
        lay.setContentsMargins(10, 5, 10, 5)
        lay.setSpacing(12)

        def head(text, *, width=None, align=Qt.AlignmentFlag.AlignLeft):
            lb = QLabel(text, host)
            lb.setProperty("role", "colHead")
            if width is not None:
                lb.setFixedWidth(width)
            lb.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
            return lb

        lay.addWidget(head(i18n.KO.COL_SLOT, width=96))
        lay.addWidget(head(i18n.KO.COL_IMAGES), 1)         # 이미지·후보 영역(가변)
        rule = QFrame(host)
        rule.setProperty("role", "vrule")
        rule.setFixedWidth(1)
        lay.addWidget(rule)
        lay.addWidget(head(i18n.KO.COL_DISTANCE, width=96,
                           align=Qt.AlignmentFlag.AlignRight))
        lay.addWidget(head(i18n.KO.COL_VERDICT, width=p.chip_w,
                           align=Qt.AlignmentFlag.AlignCenter))
        spacer = QWidget(host)                             # 토글 열 위 빈 칸
        spacer.setFixedWidth(p.toggle_w)
        lay.addWidget(spacer)
        return host

    # ------------------------------------------------------------------
    def load_state(self,
                   matches: list[MatchResult],
                   *,
                   score_cache=None,
                   val_pool: dict | None = None,
                   candidates_by_ref: dict | None = None,
                   coord_mode: bool = False,
                   tolerance: float = 500.0,
                   coord_failed_count: int = 0) -> None:
        """매치 검토 화면 초기화.

        ``score_cache`` 와 ``val_pool`` 이 함께 주어지면 각 매치 행에 차순위
        후보를 클릭 가능한 형태로 보여주고, 클릭 시 그 후보로 매치를 교체한다.

        ``candidates_by_ref`` 가 주어지면 (fast 모드, #7) ``(slot, ref_path.name)``
        키로 미리 점수 내림차순 정렬된 ``[(ImageItem, score), ...]`` 후보 목록을
        직접 사용한다.  score_cache 가 비어있는 fast 모드에서도 후보가 보인다.

        ``coord_mode=True`` 면 score 를 µm 거리로 역산해 표시하고,
        ``coord_failed_count`` 를 요약에 "매치 실패 N쌍" 으로 포함한다.
        """
        self._matches = list(matches)
        self._unmatched_keys.clear()
        self._coord_mode = bool(coord_mode)
        self._tolerance = float(tolerance) if tolerance and tolerance > 0 else 500.0
        self._coord_failed_count = int(coord_failed_count)
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
        # 표시 필터/현재 행 초기화 (새 검토 세션마다 전체 보기에서 시작).
        self._current_row = None
        self.btn_filter.blockSignals(True)
        self.btn_filter.setChecked(False)
        self.btn_filter.blockSignals(False)

        if not self._matches:
            empty = QLabel(i18n.KO.REVIEW_EMPTY_HINT)
            empty.setStyleSheet(f"color: {theme.MUTE}; padding: 20px;")
            self._list_layout.addWidget(empty)
        else:
            if self._coord_mode:
                ordered = sorted(
                    self._matches,
                    key=lambda m: (m.score, m.slot),
                )
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
        """‘접기’ 후 — 접은 행의 사진들이 최상단에 오도록 부드럽게 복귀 (#1/#6)."""
        from .. import motion
        sb = self._scroll.verticalScrollBar()
        QTimer.singleShot(0, lambda: motion.animate_scroll(sb, self._row_top(row)))

    # ── 표시 필터 / 키보드 탐색 (표시·입력 계층 — 흐름/데이터 불변) ─────────
    def _apply_filter(self) -> None:
        """'확인 필요만' — 일치(ok) 행만 숨긴다.  ``_on_done`` 은 ``_matches``
        를 순회하므로 완료 결과는 표시 여부와 무관하다."""
        checked = self.btn_filter.isChecked()
        for row in self._rows:
            row.setVisible((not checked) or row.state() != "ok")
        # 필터가 켜졌는데 확인 필요 행이 0건이면 빈 상태를 노출(A3/C23).
        visible = [r for r in self._rows if not r.isHidden()]
        if hasattr(self, "_filter_empty"):
            self._filter_empty.setVisible(checked and not visible and
                                          bool(self._matches))
        # 현재 행이 숨겨졌으면 가장 가까운 보이는 행으로 이동.
        if (self._current_row is not None
                and self._current_row.isHidden()):
            rows = self._visible_rows()
            self._set_current(rows[0] if rows else None)

    def _visible_rows(self) -> list["_MatchRow"]:
        """레이아웃 순서 기준 보이는 행 목록 (스왑 후 append 순서와 무관)."""
        rows = [r for r in self._rows if not r.isHidden()]
        rows.sort(key=lambda r: self._list_layout.indexOf(r))
        return rows

    def _set_current(self, row: "_MatchRow | None") -> None:
        old = self._current_row
        if old is not None and old is not row:
            old.setProperty("current", "")
            old.style().unpolish(old)
            old.style().polish(old)
        self._current_row = row
        if row is not None:
            row.setProperty("current", "true")
            row.style().unpolish(row)
            row.style().polish(row)
            from .. import motion
            motion.ensure_visible_animated(self._scroll, self._scroll_host, row,
                                           margin=40)

    def _move_current(self, delta: int) -> None:
        rows = self._visible_rows()
        if not rows:
            return
        if self._current_row not in rows:
            self._set_current(rows[0] if delta >= 0 else rows[-1])
            return
        idx = rows.index(self._current_row) + delta
        idx = max(0, min(idx, len(rows) - 1))
        self._set_current(rows[idx])

    def keyPressEvent(self, event):  # noqa: N802
        """↑↓ 행 이동 · R 매치 없음 토글 · Enter 검토 완료.

        QShortcut 을 쓰지 않아 포커스된 버튼의 Enter 등 기존 Qt 규칙이
        그대로 우선한다 (페이지가 포커스를 가질 때만 동작)."""
        key = event.key()
        if key == Qt.Key.Key_Down:
            self._move_current(+1)
        elif key == Qt.Key.Key_Up:
            self._move_current(-1)
        elif key == Qt.Key.Key_R:
            if self._current_row is not None:
                self._on_toggle(self._current_row.match)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._on_done()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        # 페이지 진입 즉시 키보드 탐색 가능하게.
        self.setFocus()

    def resizeEvent(self, event):  # noqa: N802
        """창 크기 변경 시 각 행을 뷰포트 기준으로 재클램프 (가로 넘침 방지).

        큰 썸네일 상태에서 창을 줄이면 행 최소폭이 뷰포트보다 커져 행
        resizeEvent 만으로는 재클램프가 안 걸린다 — 페이지가 직접 구동한다.
        드래그 중 과호출은 슬라이더와 같은 debounce 타이머로 흡수."""
        super().resizeEvent(event)
        self._resize_timer.start(80)

    def _append_row(self, match: MatchResult) -> "_MatchRow":
        runners = self._lookup_runners_up(match, self._score_cache, self._val_pool)
        row = _MatchRow(match, runners_up=runners, parent=self,
                        thumb_px=self._thumb_px,
                        coord_mode=self._coord_mode,
                        tolerance=self._tolerance)
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
                coord_mode=self._coord_mode,
                tolerance=self._tolerance,
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
            # 교체된 행이 현재 행이었으면 새 행으로 이어받는다.
            if self._current_row is old_row:
                self._current_row = None
                self._set_current(new_row)
        self._update_summary()
        self._apply_filter()

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
        # 필터가 켜져 있으면 상태 변화에 따라 표시 여부 재적용.
        self._apply_filter()

    def _update_summary(self) -> None:
        """상단 탤리 갱신 — 일치는 기준선으로 항상, 예외(초과/매치 없음/실패)는
        0 이 아닐 때만 노출해 시선을 예외로 모은다. 숫자는 mono·tabular."""
        ok, over, none = tally(self._matches, self._unmatched_keys,
                               self._coord_mode)

        def seg(label_fmt, n, color):
            # 라벨은 색, 숫자는 mono tabular 로 자릿수 흔들림 없이.
            txt = label_fmt.format(n="")  # '일치 ' 처럼 숫자 자리 비움
            return (f"<span style='color:{color}'>{txt.strip()} "
                    f"<span style='font-family:{theme.FONT_MONO}'>{n}</span></span>")

        sep = f"<span style='color:{theme.LINE2}'>&nbsp;&nbsp;</span>"
        parts = [seg(i18n.KO.TALLY_OK_FMT, ok, theme.PASS)]
        if over > 0:
            parts.append(seg(i18n.KO.TALLY_OVER_FMT, over, theme.DANGER))
        if none > 0:
            parts.append(seg(i18n.KO.TALLY_NO_MATCH_FMT, none, theme.INK2))
        if self._coord_failed_count > 0:
            # 매치 실패(좌표 미검출)는 이 화면에서 조치 불가 → 정보성 mute.
            parts.append(seg(i18n.KO.TALLY_COORD_FAILED_FMT,
                             self._coord_failed_count, theme.MUTE))
        self._tally_label.setText(sep.join(parts))
        kept = len(self._matches) - len(self._unmatched_keys)
        self.btn_done.setText(i18n.KO.BTN_FINISH_REVIEW_KEPT_FMT.format(n=kept))

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
