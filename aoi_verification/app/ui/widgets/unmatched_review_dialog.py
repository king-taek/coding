"""매치 실패 사진 검토 다이얼로그 (#8).

엑셀 저장 전, ``FinalResult.unmatched_refs`` 의 사진들을 하나씩 다시 검토.
같은 슬롯의 검증 장비 후보를 ``SlotScoreCache`` 점수 내림차순으로 보여주고,
사용자가 클릭으로 매칭을 확정하면 새 ``MatchResult`` 가 누적된다.

- 다이얼로그가 닫힐 때 ``new_matches`` 와 ``resolved_refs`` 가 호출자에게 노출.
- 점수 캐시에 없는 (ref, val) 쌍은 그 자리에서 ``pipeline.score`` 로 계산
  (대부분 Stage 2 precompute 단계에서 이미 캐싱되어 있음).
- 이미 다른 매칭에 쓰인 val 은 후보에서 자동 제외 → 중복 매칭 방지.

★ 이 창이 **직접 계산한 점수는 공유 ``SlotScoreCache`` 에 넣지 않는다** (C-1).
  넣으면 ``match_page._launch_matcher`` 의 ``has_all_pairs`` 가 True 로 뒤집혀,
  같은 세션에서 매칭을 다시 돌릴 때 **Stage 2 를 건너뛰고 이 창이 남긴 값을
  정답으로 서빙**한다.  대신 ``MatchPage._review_scores``(같은 모양의 별도
  ``SlotScoreCache``)에 넣는다 — 읽기 순서는 공유 캐시 → 이 통이라 **표시
  점수는 종전 그대로**다.

  ※ 한때 이 자리에 "cfg 의 전처리 토글 때문에 특징 캐시 키가 갈라져 같은 쌍에
    다른 값이 나온다" 고 적혀 있었으나 **오늘 코드에서는 재현되지 않는다**:
    ``main_window._make_sim_cfg`` 가 만드는 두 cfg 모두 ``center_crop=False``·
    ``orb_nfeatures=0`` 이라 ``cache_extra()`` 가 빈 문자열이고, Stage 2 의
    ``SlotPrecomputeWorker`` 도 ``pipeline.score`` 를 cfg 없이 부른다(실측:
    특징 byte-identical, 점수 델타 0.000000).  확인한 해악만 위에 남긴다.

★ 보관처는 **창이 아니라 세션 수명**이어야 한다.  ``result_page`` 는 [실패 검토]
  를 누를 때마다 이 창을 **새로 만든다** — 창 인스턴스에 매달면 (a) 두 번째
  열기가 같은 쌍을 전부 다시 계산하고(실측 후보 40장 112 ms, 쌍당 2.8 ms),
  (b) 여기서 확정한 매치의 차순위 후보가 검토 화면에서 사라진다(실측 6 → 0 —
  좌표 모드에서 이웃을 못 찾은 ref 는 ``_fast_results`` 값이 빈 목록이라
  ``match_page.build_candidates_by_ref`` 가 점수 캐시로 내려오는데, 그 통에는
  아무것도 없기 때문).  그래서 ``review_scores`` 를 주입받는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from PyQt6.QtCore import QObject, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QIcon, QPixmap
from PyQt6.QtWidgets import (QApplication, QDialog, QFrame, QGridLayout,
                              QHBoxLayout, QLabel, QListWidget,
                              QListWidgetItem, QMenu, QMessageBox,
                              QScrollArea, QToolButton, QVBoxLayout, QWidget)

from ... import config, i18n
from .. import theme
from ..score_fmt import fmt_score
from ...models.result import MatchResult, MissEntry
from ...models.slot import ImageItem
from ...utils import image_io
from .loading_overlay import LoadingOverlay
from .neon_button import NeonButton
from . import sheet_host as sheets

# 허용 오차 폴백 — 값이 안 들어왔을 때만 쓴다.  **단일 출처는 config** 다
# (예전엔 리터럴 500 이 곳곳에 박혀 있어 기본값을 바꿔도 옛 값이 되살아났다).
_DFLT_TOL = config.DEFAULT_COORD_TOLERANCE
# 확정 토스트가 떠 있는 시간.  사진 정보 시트의 복사 토스트와 같은 값이다.
_TOAST_MS = 1800


_LIST_THUMB_PX = 56     # 좌측 ‘실패 목록’ 항목 썸네일 한 변(px).
_REF_PX = config.Sizing.DIALOG_REF_PX    # 좌측 기준 사진 기본 크기 (= 420)
_CAND_PX = config.Sizing.DIALOG_CAND_PX  # 우측 후보 타일 기본 크기 (= 260)
_CAND_CAP_PX = 28       # 캡션 한 줄
# 크기 슬라이더 범위 (기준 사진 한 변, px) — 후보 타일은 비율로 파생 (#1).
_SIZE_MIN_PX = 250
_SIZE_MAX_PX = 700


# 채점 워커가 살아 있는 동안 파이썬 참조를 붙잡아 두는 곳 — 지역 변수로만 두면
# 함수가 끝나는 순간 GC 가 QThread 를 파괴해 "QThread: Destroyed while thread is
# still running" 으로 죽는다 (`pages/setup_page.py` 의 `_LIVE_DIE_SCANS` 와 같은 패턴).
_LIVE_SCORINGS: set = set()


class _CandidateScoring(QThread):
    """후보 점수를 **워커 스레드에서** 계산한다 (P-03).

    이 계산은 캐시에 없는 쌍마다 특징 추출 + 채점이라 후보 299장이면 초 단위다.
    GUI 스레드에서 돌던 때는 그동안 창이 통째로 굳었고, 진행을 보여 주려고
    콜백마다 ``processEvents`` 를 불러 **채점 도중에 사용자 입력이 재진입**할 수
    있었다(다른 항목을 클릭하면 렌더가 겹쳐 들어왔다).

    ``fn`` 은 다이얼로그의 바인딩된 ``_score_candidates`` 다 — **Qt 위젯을 전혀
    건드리지 않으므로** 워커에서 그대로 부를 수 있고, 다이얼로그가 먼저 파괴돼도
    안전하다.  건드리는 공유 상태는 셋뿐이다:

    - ``SlotScoreCache``(공유 캐시·``review_scores``) — 전 메서드가 자체 락을 든다.
    - ``pipeline`` — 전역 가변 상태를 만지지 않는다(디스크 특징 캐시는 파일 단위).
    - ``_pair_memo`` — **락 없는 평범한 dict** 다.  개별 get/set 이 원자적이고
      키가 ``(slot, ref, val)`` 로 완전 한정돼 있어 어느 세대가 써도 값이 같다
      (아래 ``_pair_memo`` 주석 참고).  락을 들었다고 적어 두었던 적이 있는데
      사실이 아니었다 — 새 상태를 여기 추가한다면 이 목록도 함께 고쳐라.

    ``token`` 은 늦게 도착한 옛 세대를 버리기 위한 번호다 — 사용자가 항목을
    연달아 넘기면 채점이 겹치는데, 옛 결과가 새 후보 그리드를 덮으면 안 된다."""

    class _Signals(QObject):
        progress = pyqtSignal(int, int)      # token, done
        done = pyqtSignal(int, object)       # token, list[(score, item)]

    def __init__(self, token: int, fn, cur, candidates: list,
                 allow_compute: bool) -> None:
        super().__init__()                  # 부모 없음(위 주석)
        self._token = token
        self._fn = fn
        self._cur = cur
        self._candidates = candidates
        self._allow_compute = allow_compute
        self.signals = self._Signals()

    def run(self) -> None:      # type: ignore[override]
        done = 0

        def _tick() -> None:
            # 25건마다만 보고한다 — 신호를 매 건 보내면 큐가 넘쳐 오히려 느려진다
            # (`similarity/slot_features.py` 와 같은 스로틀).
            nonlocal done
            done += 1
            if done % 25 == 0:
                self.signals.progress.emit(self._token, done)

        try:
            scored = self._fn(self._cur, self._candidates,
                              self._allow_compute, on_computed=_tick)
        except Exception:
            # 워커에서 예외가 새면 오버레이가 영영 '계산 중' 으로 남는다.
            scored = []
        self.signals.done.emit(self._token, scored)


# ---------------------------------------------------------------------------


def _reuses_coord_scores(session_coord_mode: bool, allow_compute: bool,
                         fast_results, slot: str, ref_path: Path,
                         classical_refs=()) -> bool:
    """이 렌더가 **좌표 거리 점수를 그대로 재사용**하는가 (C-2).

    후보가 300장 이상이면 이 창은 CPU 재계산을 포기하고 선계산 결과
    (``_fast_results``)를 그대로 쓴다(:meth:`_score_candidates`).  좌표 매칭
    세션에서 그 값은 ``workers/coord_matcher`` 의 **거리 인코딩**이라
    (dist ≤ tol → 1-dist/tol, tol < dist ≤ 3tol → **음수** -(dist/tol))
    유사도 백분율로 찍으면 거짓 수치가 된다 — 실측 tol=200 µm 에서 480 µm 떨어진
    후보는 score −2.4 → 화면에 **"-240.0 %"** 로 나온다.

    ★ 그런데 **좌표 세션이라고 전부 거리 점수인 것은 아니다.**  좌표가 없는 ref 는
    ``coord_matcher`` 가 고전 유사도로 폴백 채점해 **같은** ``_fast_results`` 에
    넣는다(그 파일의 `_run` 폴백 구간).  그 값은 진짜 유사도라 %로 보여야 하는데
    한때 이 함수가 좌표 세션이면 무조건 True 를 내는 바람에 실측 0.83·0.61(=83%·
    61%)이 '계산 안 함' 으로 표시되고 요약에도 "좌표 거리 순" 이라고 거짓으로
    적혔다.  그래서 ``classical_refs``(= ``CoordScheduler.classical_refs``, 값이
    아니라 **출처 표식**)로 그 ref 들을 걸러낸다.

    Qt 없이 시험할 수 있도록 순수 함수로 분리한다(``_select_coord_candidates``
    ·``_match_neighbors`` 와 같은 처방)."""
    if not session_coord_mode or allow_compute:
        return False
    if (slot, Path(ref_path)) in (classical_refs or ()):
        return False                      # 고전 폴백이 매긴 진짜 유사도다.
    return bool((fast_results or {}).get((slot, Path(ref_path))))


def _load_full_pixmap_scaled(path: Path, size: int) -> QPixmap:
    """원본 파일을 그대로 디코드한 뒤 ``size`` 박스에 맞춰 축소.

    캐시된 썸네일/mid 가 아닌 ‘원본 화질’ 을 그대로 보고 싶을 때 사용 — JPEG
    압축이 한 번만 적용된 결과를 사용자가 보게 된다.  full pixmap 은 함수
    스코프 안에서만 살아 있다가 GC 되므로 메모리는 축소된 사본만 유지.
    """
    fallback = QPixmap(size, size)
    fallback.fill(QColor(theme.PANEL))
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
    - 우클릭 '크게 보기' = 좌우(기준·후보) 비교 크게보기 (#1e).
    - 이미지는 사전 생성된 mid 캐시를 소스로 빠르게 로드하고(#1c), 슬라이더로
      재디코드 없이 인플레이스 재스케일한다.
    """

    selected = pyqtSignal(object)          # ImageItem (클릭 선택)
    view_requested = pyqtSignal(object)    # ImageItem (크게보기)

    # objectName 스코프 셀렉터 — 최외곽 프레임에만 테두리. (QLabel 이 QFrame
    # 서브클래스라 ``QFrame {…}`` 는 내부 이미지/점수/캡션 라벨까지 번진다.)
    # ★ 색을 **클래스 본문에서 굽지 않는다** — 클래스 본문은 import 시점에 한 번만
    #   평가돼 그때의 팔레트(항상 라이트)가 영구히 박힌다(다크 모드가 안 먹는다).
    #   배경 틴트도 옛 네온 초록 리터럴 대신 팔레트의 강조 틴트를 쓴다.
    @staticmethod
    def _sel_style() -> str:
        return (f"#candTile {{ border: 2px solid {theme.ACCENT}; border-radius: 8px; "
                f"background: {theme.ACCENT_TINT}; }}")

    def __init__(self, item: ImageItem, score: float, parent=None,
                 *, size: int = _CAND_PX,
                 coord_mode: bool = False, tolerance: float = _DFLT_TOL,
                 score_unknown: bool = False) -> None:
        super().__init__(parent)
        self.item = item
        self.score = float(score)
        # 점수가 '유사도' 가 아닐 때(좌표 거리 재사용, C-2) — 수치 대신 계산 안 함을
        # 표시한다.  정렬에는 그대로 쓴다(거리 오름차순 = 이 값 내림차순).
        self._score_unknown = bool(score_unknown)
        self._coord_mode = bool(coord_mode)
        self._tolerance = float(tolerance) if tolerance > 0 else _DFLT_TOL
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
        ph.fill(QColor(theme.PANEL))
        self._img_label.setPixmap(ph)
        lay.addWidget(self._img_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self._score_label = QLabel(self.score_text(), self)
        self._score_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # ★ 후보 유사도는 '합격 판정' 이 아니다 — 매치 검토 화면과 같은 중립색을 쓴다
        #   (성공색은 판정 칩 전용).
        self._score_label.setProperty("role", "tileScore")
        lay.addWidget(self._score_label)

        from PyQt6.QtGui import QFontMetrics
        cap = QLabel(self)
        cap.setFixedHeight(_CAND_CAP_PX)
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 파일명 캡션 등급 — 썸네일 그리드와 같은 role 을 쓴다(대비·크기 한 자리에서).
        cap.setProperty("role", "fileCaption")
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

    def score_text(self) -> str:
        """타일·비교뷰가 함께 쓰는 점수 표기 — **한 곳에서만** 만든다.

        갈라 두면 타일은 '계산 안 함' 인데 크게보기 캡션만 거짓 백분율을
        띄우는 식으로 조용히 어긋난다 (C-2)."""
        if self._score_unknown:
            return i18n.KO.SCORE_NOT_COMPUTED
        return fmt_score(self.score, self._coord_mode, self._tolerance)

    def set_score(self, score: float, *, unknown: bool | None = None) -> None:
        """같은 슬롯 재사용 시 새 기준 사진 기준으로 점수만 갱신 (#1b)."""
        self.score = float(score)
        if unknown is not None:
            self._score_unknown = bool(unknown)
        self._score_label.setText(self.score_text())

    def set_selected(self, selected: bool) -> None:
        if selected == self._is_selected:
            return
        self._is_selected = bool(selected)
        self.setStyleSheet(self._sel_style() if self._is_selected else "")

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.item)
        super().mousePressEvent(event)

    # ★ 더블클릭 확대는 제거했다 — 후보를 연달아 누르다 보면 두 번째 클릭이 더블클릭으로
    #   붙어 원하지 않는 비교 창이 떴다(사용자 지적).  확대는 우클릭 메뉴로만 연다.
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
                 parent=None,
                 *,
                 coord_mode: bool = False,
                 tolerance: float = _DFLT_TOL,
                 review_scores=None,
                 coord_classical_refs=()) -> None:
        """``val_pool`` 키는 두 형태를 모두 지원:

        - ``(slot, side)`` → list[ImageItem]  : 후보 풀
        - ``slot``          → list[ImageItem]  : 단일 모드 호환 (side 무시)

        ``review_scores`` 는 이 창이 계산한 점수를 담을 **세션 수명 보관처**
        (``MatchPage._review_scores``).  주지 않으면 창 하나짜리 통을 만들어 쓰지만,
        그러면 창을 다시 열 때 전부 재계산하고 확정한 매치의 차순위가 사라진다
        (모듈 docstring 참고) — 실제 호출부(`result_page`)는 반드시 준다.
        ``coord_classical_refs`` 는 좌표 세션에서 **고전 유사도로 폴백 채점된**
        {(slot, ref_path)} 집합이다(C-2).
        """
        super().__init__(parent)
        self._unmatched = list(unmatched)
        # (slot, side) 또는 slot 키 모두 받아들이도록 통일.
        self._val_pool_keyed: dict = {}
        for k, v in (val_pool or {}).items():
            self._val_pool_keyed[k] = list(v)
        self._used_vals: set[Path] = {Path(p) for p in already_used_vals}
        self._score_cache = score_cache
        # 이 창이 직접 계산한 (slot, ref, val) → score 의 보관처.  공유 캐시와
        # **분리**하되 **세션 수명**이다(C-1, 모듈 docstring 참고).  읽기는 공유
        # 캐시 → 여기 순이라 표시되는 점수는 종전과 같다.
        if review_scores is None:
            # 주입이 없을 때만 만든다 — 여기서만 필요한 무거운 import 라 지연시킨다
            # (`_lookup_or_compute_score` 의 pipeline import 와 같은 처방).
            from ...similarity.slot_features import SlotScoreCache
            review_scores = SlotScoreCache()
        self._review_scores = review_scores
        # 좌표 세션에서 고전 유사도로 폴백 채점된 ref 들 — 점수 표기를 가른다 (C-2).
        self._coord_classical_refs = frozenset(coord_classical_refs or ())
        # 쌍당 캐시 조회 1회 (C-5) — `_count_recompute`·`_score_candidates`·
        # `_lookup_or_compute_score` 가 같은 쌍을 세 번 물어 `SlotScoreCache` 의
        # 락을 후보 1장당 3번 잡았다(후보 299장이면 897회).  렌더 세대마다 **새
        # dict 를 만들어** 메모가 한 화면 안에서만 살게 한다.
        # ★ 이것이 옛 세대의 쓰기를 막아 주지는 **않는다** — 워커는 호출 시점에
        #   `self._pair_memo` 를 다시 읽으므로 늦게 끝난 옛 세대도 새 dict 에 쓴다.
        #   키가 `(slot, ref, val)` 로 완전 한정돼 어느 세대가 써도 값이 같아
        #   무해하다(한때 "옛 세대가 덮지 않게" 라고 적혀 있었으나 사실이 아니다).
        self._pair_memo: dict[tuple, Optional[float]] = {}
        # 효율 모드 선계산 top-K {(slot, ref_path): [(val_path, score)]} — 후보 풀이
        # 300장 이상이면 CPU 재계산 대신 이걸 재사용한다 (#1).
        self._fast_results = fast_results or {}
        # 이 다이얼로그는 항상 이미지 유사도(pipeline.score)로 후보를 채점하므로
        # 좌표 거리가 아닌 유사도 백분율로 표시한다.
        self._coord_mode = False
        # ★ 위와 별개로 **세션이** 좌표로 매칭했는지는 기억해 둔다 — 그 경우에만
        #   "여기는 유사도 순" 이라고 알려 준다(U-18).  둘을 한 필드로 합치면
        #   점수 표기가 좌표 거리 형식으로 바뀌어 버린다.
        self._session_coord_mode = bool(coord_mode)
        # 채점 워커 (P-03) — 세대 번호로 늦게 온 옛 결과를 버린다.
        self._score_token = 0
        self._scoring: _CandidateScoring | None = None
        # 헤드리스(offscreen)에서는 동기로 채점한다 — 테스트가 `_render_current()`
        # 직후에 후보 타일을 검사하므로 한 틱 뒤로 미루면 성립하지 않는다.
        # (`widgets/zoom_window.py` 의 원본 로더가 쓰는 것과 같은 게이트.)
        import os
        self._sync_scoring = (
            os.environ.get("QT_QPA_PLATFORM", "") == "offscreen")
        self._tolerance = float(tolerance) if tolerance > 0 else _DFLT_TOL
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
        # 선택(파란 테두리) 보류 상태 — ref 인덱스 → 선택한 후보 (확정 전, #1a).
        self._pending: dict[int, ImageItem] = {}
        # 현재 후보 타일들 + 후보 집합 키(같은 슬롯 재사용 판단, #1b).
        self._cand_tiles: list[_CandidateTile] = []
        self._last_cand_key: tuple | None = None
        self._close_prompted = False
        # 확정 토스트를 지우는 타이머 — 첫 확정 때 만들어 재사용한다(`_show_toast`).
        self._toast_timer: QTimer | None = None

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
        # ★ 창 제어(최소화/최대화/F11) 헬퍼를 부르지 않는다 — 이 다이얼로그는
        #   별도 OS 창이 아니라 **메인 창 안의 시트**로 뜬다(widgets/sheet_host.py).
        #   최대화·전체화면은 메인 창이 담당한다.
        self._build()
        # 후보 풀이 작아(<300) 캐시 miss 를 그 자리에서 CPU 재계산할 때 띄우는 로딩 오버레이.
        # 다이얼로그 전체를 덮어 '계산 중'을 알린다(부모 위젯 size 추적).
        self._loading = LoadingOverlay(self)
        # ★ 첫 렌더를 여기서 부르지 **않는다.**  `_render_current` 는 원본 풀 디코드 +
        #   후보 점수 계산(캐시 miss 면 최대 299쌍의 extract+score)을 GUI 스레드에서
        #   동기로 수행하는데, 이 생성자는 `sheets.run(dlg)` 이 위젯을 show() 하기
        #   **전에** 끝나야 한다.  그래서 팝업이 뜨지도 않은 채 앱이 멈춘 것처럼
        #   보였고, 안에서 부르는 `show_overlay` 도 그려질 화면이 없어 무용지물이었다.
        #   표시 직후 한 틱 뒤로 미루면 시트가 먼저 뜨고 그 위에서 로딩이 보인다.
        self._first_render_done = False

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # 상단 진행 + 안내
        head = QHBoxLayout()
        self.progress_label = QLabel("", self)
        self.progress_label.setProperty("role", "slotHead")
        head.addWidget(self.progress_label)
        # 사용법 4줄은 기본 접힘 — 셋업 카드와 같은 '?'(helpToggle) 패턴이다.
        # 반복 검토하는 사용자에게 매번 세로 ~70px 를 내주면 본문(기준 사진·후보
        # 그리드)이 그만큼 아래로 밀린다.
        self._help_btn = QToolButton(self)
        self._help_btn.setText("?")
        self._help_btn.setObjectName("helpToggle")
        self._help_btn.setCheckable(True)
        self._help_btn.setToolTip(i18n.KO.HELP_TOGGLE_TOOLTIP)
        self._help_btn.toggled.connect(self._on_help_toggled)
        head.addWidget(self._help_btn)
        # 확정 결과를 알리는 자리 — 모달 대신 여기서 잠깐 뜬다.
        # ★ **늘어나는 여백보다 앞**에 둔다.  여백 뒤(버튼 바로 앞)에 두면 문구가
        #   붙는 순간 그 폭만큼 오른쪽 버튼 넷이 통째로 왼쪽으로 밀린다 — 150px 예약으로
        #   막을 수 없다(확정 문구는 250px 쯤 된다).  연속 확정을 하는 사용자에겐 커서
        #   아래에서 [확정]이 [닫기]로 바뀌는 셈이라 오클릭을 만든다.  여백 앞에 두면
        #   늘어나는 쪽은 여백이므로 버튼은 붙박이다(자리 예약이 따로 필요 없다).
        self._toast = QLabel("", self)
        self._toast.setProperty("role", "statusPass")
        head.addWidget(self._toast)
        head.addStretch(1)
        # 네비게이션 버튼
        self.btn_prev = NeonButton(i18n.KO.BTN_UNMATCHED_PREV, role="ghost")
        self.btn_prev.clicked.connect(self._go_prev)
        head.addWidget(self.btn_prev)
        # warn(주의색)은 예외 상태 경고 전용이다 — 다음 항목으로 넘어가는 단순 탐색에
        # 쓰면 '위험한 동작' 처럼 읽혀 클릭을 주저하게 한다([← 이전]과 짝을 맞춘다).
        self.btn_skip = NeonButton(i18n.KO.BTN_UNMATCHED_NEXT, role="ghost")
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

        self._hint = QLabel(i18n.KO.UNMATCHED_REVIEW_HINT, self)
        self._hint.setWordWrap(True)
        self._hint.setProperty("role", "muted")
        self._hint.setContentsMargins(4, 4, 4, 4)   # 옛 인라인 padding:4px 와 동일
        self._hint.setVisible(False)          # 기본 접힘 — '?' 로 편다
        root.addWidget(self._hint)

        # 좌표로 매칭한 세션에서만 — 후보 순서의 기준이 좌표가 아님을 밝힌다 (U-18).
        # ★ 이건 접지 않는다.  '사용법' 이 아니라 점수의 뜻을 바로잡는 고지라서,
        #   숨기면 후보 순서를 좌표 거리로 오해한 채 판단하게 된다.
        if self._session_coord_mode:
            self.coord_note = QLabel(i18n.KO.UNMATCHED_REVIEW_COORD_NOTE, self)
            self.coord_note.setWordWrap(True)
            self.coord_note.setProperty("role", "emptyHint")
            root.addWidget(self.coord_note)

        # 본문: 좌(실패 목록) + 중(기준 사진) + 우(후보 그리드)
        body = QHBoxLayout()
        body.setSpacing(16)

        # LIST: 매치 실패 ref 전체 목록 (#12) — 클릭하면 해당 항목으로 점프.
        list_panel = QFrame(self)
        list_panel.setProperty("role", "section")
        lpl = QVBoxLayout(list_panel)
        lpl.setContentsMargins(12, 12, 12, 12)
        lpl.setSpacing(6)
        list_title = QLabel(i18n.KO.UNMATCHED_FAIL_LIST_TITLE, list_panel)
        list_title.setProperty("role", "paneTitle")
        lpl.addWidget(list_title)
        self.fail_list = QListWidget(list_panel)
        self.fail_list.setIconSize(QSize(_LIST_THUMB_PX, _LIST_THUMB_PX))
        self.fail_list.setProperty("role", "pickList")
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
        ref_title.setProperty("role", "paneTitle")
        ref_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ll.addWidget(ref_title)
        self.ref_filename = QLabel("", left)
        self.ref_filename.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ref_filename.setProperty("role", "mutedPad2")
        self.ref_filename.setWordWrap(True)
        ll.addWidget(self.ref_filename)
        self.ref_img = QLabel(left)
        self.ref_img.setFixedSize(self._ref_px, self._ref_px)
        self.ref_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ref_img.setProperty("role", "photoFrame")
        # 우클릭 ‘크게보기’ — 후보와 동일한 좌우 비교 창을 열되, 기준 사진은 가장
        # 유사도가 높은 후보부터(start=0) 보여준다 (#13).
        # ★ 더블클릭 확대는 제거했다(사용자 지적).  여기에 걸려 있던 것은 인스턴스 메서드
        #   몽키패치라 더 나빴다 — QLabel 의 이벤트 핸들러를 람다로 갈아끼우면 위젯이
        #   자기 클래스 계약 밖에서 동작해 다음 사람이 찾을 수 없다.
        self.ref_img.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ref_img.customContextMenuRequested.connect(self._on_ref_context_menu)
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
        cand_title.setProperty("role", "paneTitle")
        cand_head.addWidget(cand_title)
        # '검증 장비 후보' 옆 '크게 보기' — 선택 후보(없으면 1순위)부터 좌우 비교.
        self.btn_zoom_cand = NeonButton(i18n.KO.BTN_VIEW_LARGER, role="ghost")
        self.btn_zoom_cand.clicked.connect(self._open_compare_selected)
        cand_head.addWidget(self.btn_zoom_cand)
        cand_head.addStretch(1)
        rl.addLayout(cand_head)
        self.candidates_summary = QLabel("", right)
        self.candidates_summary.setProperty("role", "mutedPad2")
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
        """‘매칭 취소’ 로 발생한 실패인지 (#14).

        문자열은 `i18n.KO.CANCELLED_NOTE_MARK` 한 곳에서만 정의한다(양쪽에 하드코딩하면
        조용히 갈라진다).

        ⚠ **지금 이 표시를 note 에 남기는 생산자는 없다.**  옛 주석은 `result_page` 를
        가리켰지만 `result_page._on_review_matches` 는 저장소에 존재하지 않고, note 를
        쓰는 곳은 NOTE_UNMATCHED / NOTE_UNMATCHED_BY_USER 둘뿐이라 둘 다 이 표식을 담지
        않는다 — 즉 이 판정은 항상 False 이고 아래 구분선도 그려지지 않는다.  소비자만
        살아 있는 **휴면 경로**다(근거·판단은 `i18n/ko.py` 의 CANCELLED_NOTE_MARK 주석,
        회귀 가드는 `dev/tests/test_unmatched_cancelled_order.py`)."""
        return i18n.KO.CANCELLED_NOTE_MARK in (getattr(entry, "note", "") or "")

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
        """`[슬롯]` + 파일명 2줄.  (확정 항목은 목록에서 빠지므로 ✓ 표시는 없다.)

        ★ 슬롯 태그만 적던 시절엔 같은 슬롯의 실패가 여러 장일 때 항목들이 **같은
          이름**으로 보여, 특정 사진으로 돌아가려면 썸네일을 육안 대조하거나 항목마다
          호버해 툴팁을 봐야 했다.  파일명은 앞뒤가 다 정보라 가운데를 줄인다(전체
          이름은 툴팁이 계속 보여 준다)."""
        entry = self._unmatched[idx]
        name = Path(entry.path).name
        fm = self.fail_list.fontMetrics()
        # 목록 고정폭(260) − 패널 마진 − 아이콘 − 항목 패딩/스크롤바 여유.
        avail = 260 - 24 - _LIST_THUMB_PX - 28
        short = fm.elidedText(name, Qt.TextElideMode.ElideMiddle, max(40, avail))
        return f"[{entry.slot}]\n{short}"

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
            sep = QListWidgetItem(i18n.KO.UNMATCHED_CANCELLED_SEPARATOR)
            sep.setFlags(Qt.ItemFlag.NoItemFlags)
            sep.setForeground(QColor(theme.DANGER))
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
        """후보를 선택(보류)한 ref 는 실패 목록에서 파일명을 강조색으로 표시 (#4)."""
        if not hasattr(self, "fail_list"):
            return
        for row in range(self.fail_list.count()):
            idx = self._row_to_idx.get(row)
            if idx is None:
                continue                              # 구분선 행.
            item = self.fail_list.item(row)
            if idx in self._pending:
                item.setForeground(QColor(theme.ACCENT))
            else:
                item.setForeground(QColor(theme.INK2))

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
        # 새 세대 — 아직 돌고 있는 옛 채점의 결과는 이 번호가 달라 버려진다.
        self._score_token += 1
        token = self._score_token
        # 쌍당 조회 1회(C-5)의 메모는 **세대마다 새로** 시작한다.
        self._pair_memo = {}
        scored: list[tuple[float, ImageItem]] = []
        coord_reuse = False
        if candidates:
            # 후보 풀이 300장 이상이면 효율 모드 선계산 점수를 재사용해 CPU 재계산을
            # 건너뛴다(즉시 표시). 미만이면 기존처럼 캐시 miss 를 그 자리 계산 (#1).
            allow_compute = len(candidates) < 300
            # 그 재사용이 **좌표 거리 점수**면 유사도 %로 찍지 않는다 (C-2).
            coord_reuse = _reuses_coord_scores(
                self._session_coord_mode, allow_compute, self._fast_results,
                cur.slot, Path(cur.path), self._coord_classical_refs)
            # 캐시에 없어 **실제로 다시 계산**해야 하는 후보 수를 먼저 센다 — 0 이면
            # 로딩을 띄우지 않고(즉시), >0 이면 로딩 오버레이로 진행을 보여준다 (#로딩).
            need = self._count_recompute(cur, candidates) if allow_compute else 0
            if need > 0 and not self._sync_scoring:
                # 무거운 쪽 — 워커에 넘기고 그리기는 결과가 올 때 이어서 한다 (P-03).
                # (이 경로는 allow_compute=True 뿐이라 점수는 항상 유사도다.)
                self._loading.show_overlay(i18n.KO.PHASE_SCORING)
                self._loading.set_progress(0, need, i18n.KO.PHASE_SCORING)
                self._start_scoring(token, cur, candidates, allow_compute,
                                    need, cand_key)
                return
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
            try:
                scored = self._score_candidates(cur, candidates, allow_compute)
            finally:
                QApplication.restoreOverrideCursor()
        self._apply_scored(token, cur, cand_key, scored,
                           score_unknown=coord_reuse)

    # ------------------------------------------------------------------
    def _start_scoring(self, token: int, cur: MissEntry, candidates: list,
                       allow_compute: bool, need: int, cand_key: tuple) -> None:
        """채점 워커를 띄운다 — 결과가 오면 `_on_scoring_done` 이 이어서 그린다."""
        worker = _CandidateScoring(token, self._score_candidates, cur,
                                   candidates, allow_compute)
        worker.signals.progress.connect(
            lambda tk, done, n=need: self._on_scoring_progress(tk, done, n))
        worker.signals.done.connect(
            lambda tk, scored, c=cur, k=cand_key:
                self._on_scoring_done(tk, c, k, scored))
        _LIVE_SCORINGS.add(worker)
        worker.finished.connect(lambda w=worker: _LIVE_SCORINGS.discard(w))
        self._scoring = worker
        worker.start()

    def _on_scoring_progress(self, token: int, done: int, need: int) -> None:
        if token != self._score_token:
            return
        self._loading.set_progress(done, need, i18n.KO.LOAD_SCORING)

    def _on_scoring_done(self, token: int, cur: MissEntry, cand_key: tuple,
                         scored: list) -> None:
        """워커가 끝났다 — **최신 세대만** 그린다(옛 결과는 조용히 버린다)."""
        if token != self._score_token:
            return
        self._scoring = None
        self._loading.hide_overlay()
        self._apply_scored(token, cur, cand_key, scored)

    # ------------------------------------------------------------------
    def _apply_scored(self, token: int, cur: MissEntry, cand_key: tuple,
                      scored: list, *, score_unknown: bool = False) -> None:
        """점수가 나온 뒤의 그리기 — 동기·워커 두 경로가 함께 쓴다.

        ``score_unknown`` 이면 점수가 유사도가 아니므로(좌표 거리 재사용, C-2)
        수치 대신 '계산 안 함' 을 보여 준다.  정렬은 그대로다."""
        if token != self._score_token:
            return
        scored = sorted(scored, key=lambda x: x[0], reverse=True)
        # 좌표 순으로 줄을 세운 화면에서는 U-18 안내('순서 기준은 유사도')가
        # 거짓이 된다 — 그 경우에만 감춘다.
        note = getattr(self, "coord_note", None)
        if note is not None:
            note.setVisible(not score_unknown)

        if not scored:
            self._clear_grid()
            self._cand_tiles = []
            self._last_cand_key = None
            empty = QLabel(i18n.KO.UNMATCHED_REVIEW_NO_CANDIDATES, self._host)
            empty.setProperty("role", "mutedPad")
            self._grid.addWidget(empty, 0, 0)
            self.candidates_summary.setText(i18n.KO.UNMATCHED_CAND_NONE)
            return

        if cand_key == self._last_cand_key and self._cand_tiles:
            # 같은 슬롯 → 이미지 재로딩 없이 점수만 갱신 후 재정렬 (#1b).
            by_path = {t.item.path: t for t in self._cand_tiles}
            ordered: list[_CandidateTile] = []
            for s, v in scored:
                t = by_path.get(v.path)
                if t is None:
                    continue
                t.set_score(s, unknown=score_unknown)
                ordered.append(t)
            self._cand_tiles = ordered
        else:
            # 후보 집합이 달라졌으면 새로 빌드.
            self._clear_grid()
            self._cand_tiles = []
            for s, v in scored:
                tile = _CandidateTile(v, s, parent=self._host,
                                      size=self._cand_px,
                                      coord_mode=self._coord_mode,
                                      tolerance=self._tolerance,
                                      score_unknown=score_unknown)
                tile.selected.connect(self._on_tile_selected)
                tile.view_requested.connect(self._on_tile_view)
                self._cand_tiles.append(tile)
            self._last_cand_key = cand_key

        fmt = (i18n.KO.UNMATCHED_CAND_COUNT_COORD_FMT if score_unknown
               else i18n.KO.UNMATCHED_CAND_COUNT_FMT)
        self.candidates_summary.setText(fmt.format(n=len(self._cand_tiles)))
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

    def showEvent(self, event):  # noqa: N802
        """시트가 **뜬 뒤에** 첫 사진을 렌더한다(생성자에서 하지 않는다).

        첫 렌더는 후보 점수 계산까지 포함해 수 초가 걸릴 수 있다.  생성자에서 하면
        `sheets.run` 이 show() 하기 전이라 팝업 없이 앱이 멈춘 것처럼 보였다.
        한 틱 뒤로 미뤄 시트를 먼저 그리고, 그 위에서 기존 LoadingOverlay 가
        정상적으로 보이게 한다."""
        super().showEvent(event)
        if self._first_render_done:
            return
        self._first_render_done = True
        QTimer.singleShot(0, self._render_first_if_alive)

    def _render_first_if_alive(self) -> None:
        """지연 첫 렌더 — 그 사이 창이 닫혔으면 조용히 포기한다."""
        try:
            if not self.isVisible():
                return
            self._render_current()
        except RuntimeError:
            pass                      # 이미 파괴된 C++ 객체

    # ------------------------------------------------------------------
    def _known_score(self, slot: str, ref_path: Path, val_path: Path):
        """이 쌍에 대해 **이미 아는 점수** (없으면 None).

        읽는 순서는 공유 ``SlotScoreCache``(Stage 2 가 채운 값) → 실패 검토가
        계산해 둔 값 — 종전에 둘이 한 통에 들어 있던 때와 **같은 값이 나온다**
        (C-1).  조회 결과는 렌더 세대 동안 기억해 쌍당 락을 한 번만 잡는다 (C-5)."""
        key = (slot, ref_path, val_path)
        memo = self._pair_memo
        if key in memo:
            return memo[key]
        s = None
        if self._score_cache is not None:
            s = self._score_cache.get_pair(slot, ref_path, val_path)
        if s is None:
            s = self._review_scores.get_pair(slot, ref_path, val_path)
        s = None if s is None else float(s)
        memo[key] = s
        return s

    def _remember_score(self, slot: str, ref_path: Path, val_path: Path,
                        score: float) -> None:
        """이 창이 계산한 점수를 보관 — **공유 캐시에는 넣지 않는다** (C-1).

        대신 세션 수명 보관처(``review_scores``)에 넣어, 창을 다시 열어도 재계산이
        없고 여기서 확정한 매치의 차순위 후보가 검토 화면에 그대로 남게 한다."""
        self._review_scores.put(slot, ref_path, val_path, float(score))
        self._pair_memo[(slot, ref_path, val_path)] = float(score)

    def _count_recompute(self, cur: MissEntry, candidates: list) -> int:
        """캐시에 없어 그 자리에서 CPU 재계산해야 하는 후보 수(로딩 표시 여부 판단).

        ★ 여기서 한 조회는 `_pair_memo` 에 남아 뒤따르는 채점이 그대로 쓴다 (C-5)."""
        ref_path = Path(cur.path)
        n = 0
        for v in candidates:
            if self._known_score(cur.slot, ref_path, Path(v.path)) is None:
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
            # ★ 조회는 **쌍당 한 번**이다 (C-5).  아는 값이면 그대로 쓰고, 그때만
            #   `_lookup_or_compute_score` 로 내려간다 — 그 함수도 첫 줄이 같은
            #   조회라 예전엔 같은 쌍을 두 번(+`_count_recompute` 까지 세 번) 물었다.
            known = self._known_score(cur.slot, Path(cur.path), Path(v.path))
            cached = known is not None
            s = (known if cached else
                 self._lookup_or_compute_score(cur, v,
                                               allow_compute=allow_compute))
            # ★ 진행 보고를 **점수 성공 여부와 분리한다.**  이전에는 `s is None` 이면
            #   `continue` 로 빠져 보고도 건너뛰었다 — 건너뛴 후보가 하나라도 있으면
            #   done 이 need 에 **영원히 못 닿아** 바가 98% 에서 멈춘 채 창이 내려간다.
            #   need 는 '캐시에 없던 개수'이므로, 계산을 시도했으면 결과와 무관하게 센다.
            if on_computed is not None and not cached:
                on_computed()
            if s is None:
                continue                     # ≥300 & 캐시 miss → 재계산 없이 제외.
            out.append((float(s), v))
        return out

    # ------------------------------------------------------------------
    def _lookup_or_compute_score(self,
                                  ref: MissEntry,
                                  val: ImageItem,
                                  allow_compute: bool = True):
        """캐시 우선, 없으면(``allow_compute``) 즉석 계산. 재계산 불가 시 None."""
        ref_path = Path(ref.path)
        val_path = Path(val.path)
        s = self._known_score(ref.slot, ref_path, val_path)
        if s is not None:
            return s
        if not allow_compute:
            return None                    # ≥300: CPU 재계산 금지.
        # 캐시 miss — pipeline 으로 직접 계산.  재방문 시 빠르도록 보관하되,
        # **공유 캐시가 아니라 이 창의 통**에 넣는다(C-1, 모듈 docstring 참고).
        try:
            from ...similarity import pipeline as _pipeline
            rf = _pipeline.extract(ref_path)
            vf = _pipeline.extract(val_path)
            s = float(_pipeline.score(rf, vf))
        except Exception:
            s = 0.0
        self._remember_score(ref.slot, ref_path, val_path, s)
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
        """좌(기준)·우(후보) 비교 크게보기 — 후보 우클릭 및 기준 우클릭
        공용.  ``self._cand_tiles`` 는 이미 유사도 내림차순이므로 start_index=0
        이면 가장 유사한 후보부터 보인다 (기준 우클릭용)."""
        from .side_by_side_viewer import SideBySideViewer
        cur = self._current()
        if cur is None or not self._cand_tiles:
            return
        candidates = [(t.item, t.score_text()) for t in self._cand_tiles]
        start = max(0, min(int(start_index), len(candidates) - 1))
        viewer = SideBySideViewer(
            Path(cur.path), candidates, start,
            ref_caption=i18n.KO.UNMATCHED_REF_PREFIX + Path(cur.path).name,
            action_label=i18n.KO.BTN_UNMATCHED_SELECT_THIS,
            parent=self,
        )
        viewer.action_requested.connect(self._on_tile_selected)
        sheets.run(viewer, full_bleed=True)

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

    def _on_help_toggled(self, on: bool) -> None:
        """'?' — 사용법 문단을 제자리에서 펼치고 접는다(스냅, 애니 없음)."""
        self._hint.setVisible(bool(on))

    def _show_toast(self, text: str) -> None:
        """확정 결과를 헤더에서 잠깐 알린다(자리는 예약돼 있어 레이아웃 불변).

        ★ 정적 `QTimer.singleShot` 을 쓰지 않는다 — 그 타이머는 시트가 먼저 닫혀도
          계속 살아 있어 죽은 위젯의 슬롯을 부른다.  **부모 있는** 타이머 하나를
          재사용하면 창과 함께 죽고, 연속 확정 때 타이머가 쌓이지도 않는다."""
        self._toast.setText(text)
        t = self._toast_timer
        if t is None:
            t = self._toast_timer = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(lambda: self._toast.setText(""))
        t.start(_TOAST_MS)

    def _on_confirm(self) -> None:
        # 확정 직전 현재 항목의 표시 행 — 확정 후 그 자리로 올라온 다음
        # 미해결 항목으로 자연스럽게 이동하기 위해.
        prev_row = self._idx_to_row.get(self._idx, 0)
        n = self._finalize_pending()
        if n:
            # ★ 모달을 띄우지 않는다.  확정 결과는 이미 화면이 말한다(그 항목이
            #   실패 목록에서 사라진다) — 반복 검토에 건당 [확인] 클릭 1회와 시선
            #   이동을 얹지 않기 위해 자리 예약 토스트로 알린다(U-14 원칙).
            self._show_toast(i18n.KO.UNMATCHED_REVIEW_DONE_FMT.format(n=n))
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
        r = sheets.ask(
            self, i18n.KO.APP_TITLE, i18n.KO.UNMATCHED_CONFIRM_ON_CLOSE,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if r == QMessageBox.StandardButton.Yes:
            self._finalize_pending()

    def accept(self) -> None:  # noqa: D401
        self._maybe_prompt_pending()
        self._detach_scoring()      # [닫기] 로 나가는 경로도 ✕ 와 똑같이 끊는다.
        super().accept()

    def closeEvent(self, event):  # noqa: N802
        self._maybe_prompt_pending()
        self._detach_scoring()
        super().closeEvent(event)

    def _detach_scoring(self) -> None:
        """죽어 가는 위젯으로 채점 결과가 들어오지 않게 연결만 끊는다 (P-03).

        스레드가 끝나기를 **기다리지 않는다** — 채점이 끝날 때까지 닫기를 붙잡아
        두면 그게 곧 이 변경이 없애려던 그 멈춤이다.  수명은 `_LIVE_SCORINGS` 가
        책임지고, 워커가 부르는 `_score_candidates` 는 Qt 를 만지지 않으므로
        C++ 객체가 먼저 사라져도 안전하다(`widgets/zoom_window.py` 와 같은 처방).
        ``_score_token`` 을 올려 두면 혹시 남은 연결이 있어도 옛 세대로 버려진다."""
        self._score_token += 1
        w = self._scoring
        if w is None:
            return
        self._scoring = None
        # 잠금이 걸린 채 오버레이가 파괴되면 앱 전역 이벤트 필터가 주인 없이 남는다
        # — 내려 두고 나간다(LoadingOverlay.hideEvent 가 잠금을 푼다).
        self._loading.hide()
        try:
            w.signals.progress.disconnect()
            w.signals.done.disconnect()
        except (TypeError, RuntimeError):
            pass

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
        sheets.info(
            parent, i18n.KO.APP_TITLE, i18n.KO.UNMATCHED_REVIEW_EMPTY,
        )
