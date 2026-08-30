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
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPixmap
from PyQt6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout,
                             QLabel, QMenu, QScrollArea, QSizePolicy,
                             QVBoxLayout, QWidget)

from ... import i18n
from .. import theme
from ..score_fmt import fmt_score
from ...models.result import MatchResult, MissEntry
from ...models.slot import ImageItem
from ...utils import image_io
from ..widgets.neon_button import NeonButton
from ..widgets.app_logo import build_logo_label
from ..widgets.loading_overlay import LoadingOverlay
from ..widgets.no_wheel_slider import NoWheelSlider
from ..widgets.zoom_window import FullscreenViewer
from ..widgets import sheet_host as sheets
from ... import config as _config

# 허용 오차 폴백 — 값이 안 들어왔을 때만 쓴다.  **단일 출처는 config** 다
# (예전엔 리터럴 500 이 곳곳에 박혀 있어 기본값을 바꿔도 옛 값이 되살아났다).
_DFLT_TOL = _config.DEFAULT_COORD_TOLERANCE


_THUMB_PX = 140                             # 기준 썸네일 기본 크기 (#2)
_RUNNERUP_PX = int(_THUMB_PX * 0.8)         # 차순위는 20% 작게
_SIZE_MIN_PX = 100
_SIZE_MAX_PX = 360
# 후보 열 수는 가용 폭에 맞춰 동적으로 계산한다(가로 스크롤 방지, #3).
# (인라인 첫 줄 예약 폭은 _MatchRow._reserved_fixed_px 에서 현재 이미지 크기 기준으로
#  동적 계산 — 고정 상수 대신.)
# _lookup_runners_up 가 보관하는 차순위 후보 최대 개수 (#16).
_MAX_RUNNERS = 50
# 기준→검증 화살표 열 폭.  **행과 헤더가 공유**하는 상수다(둘이 다르면 헤더가 밀린다).
_ARROW_W = 24
# 슬롯 이름 열 폭.  화살표와 같은 이유로 상수다 — 행(`slot_host`)·예약폭 계산·헤더
# 세 곳이 **같은 값**을 봐야 한다.  예전엔 96 이 세 군데에 따로 적혀 있었다.
_SLOT_W = 96
# ✕/↩ 토글 열 폭.  헤더·행·예약폭 계산 세 곳이 공유한다.
# ★ 예전엔 `PROFILE.toggle_w`(스위치 트랙 크기 토큰)를 빌려 썼는데, 이 열은 스위치가
#   아니라 컴팩트 버튼이라 이름과 쓰임이 어긋났다 — 토큰을 스위치에 맞게 고치면 이
#   열 폭이 같이 흔들린다.  둘을 떼어 각자 이름을 갖게 했다.
# ★ 값이 52 → 68 로 커진 이유: 이 열에 헤더 이름을 주면서 낱말('매치 없음', colHead
#   12px/w700 실측 65px)이 52px 안에 안 들어가 잘렸다.  칸에 이름을 주는 것이 목적인데
#   이름이 잘리면 목적을 잃는다 — 낱말에 폭을 맞춘다(버튼 타깃도 함께 넓어진다).
_TOGGLE_COL_W_MIN = 68
_toggle_col_w_px: int | None = None       # 첫 호출에서 한 번 잰다


def _toggle_col_w() -> int:
    """토글 열 폭 — 헤더 낱말이 잘리지 않는 값을 **실제로 재서** 정한다.

    ★ 68 은 한 조합(colHead 12px/w700 + 동봉 폰트)의 실측값이다.  헤더는
      `setFixedWidth` 라 넘치면 **말줄임 없이 잘린다** — 폰트 프로필(`font_caption`)이
      바뀌거나 동봉 폰트가 없어 더 넓은 폴백으로 그려지면 "매치 없" 이 되어 칸에
      이름을 준 목적이 사라진다.  그래서 QSS `role="colHead"`(캡션 크기 · 700 ·
      자간 1px)와 같은 서체로 재고, 옛 실측값을 하한으로 둔다.
    ★ 세 곳(헤더·행 버튼·예약폭 계산)이 **같은 값**을 봐야 하므로 한 번만 재서 기억한다.
    """
    global _toggle_col_w_px
    if _toggle_col_w_px is None:
        f = QApplication.font()
        f.setPixelSize(theme.PROFILE.font_caption)
        f.setWeight(QFont.Weight.Bold)                       # QSS font-weight: 700
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        need = QFontMetrics(f).horizontalAdvance(i18n.KO.CHIP_NO_MATCH) + 6
        _toggle_col_w_px = max(_TOGGLE_COL_W_MIN, need)
    return _toggle_col_w_px


# 행(`QFrame[role="row"]`)의 QSS 좌측 보더 두께.  행은 보더 안쪽에서 내용을 시작하므로
# 헤더(보더 없음)보다 내용이 이만큼 오른쪽으로 밀린다 — 실측 1.0px 어긋남의 정체다.
# 헤더 좌측 마진에 더해 상쇄한다.  style.qss 의 `QFrame[role="row"]` 보더와 같은 값이다.
_ROW_BORDER_W = 1


def _open_fullscreen(path: Path, parent=None) -> None:
    """기존 풀스크린 뷰어로 원본 이미지를 크게 보여준다 (#13)."""
    try:
        viewer = FullscreenViewer(Path(path), parent)
        sheets.run(viewer, full_bleed=True)
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
        # ★ 인스턴스별 setStyleSheet 를 쓰지 않는다(#렉).  한 화면에 썸네일이 수백 개
        #   생기는데, 위젯마다 자기 스타일시트를 가지면 그만큼 규칙 집합이 따로 만들어져
        #   폴리시·재계산 비용이 개수에 비례해 커진다.  role 속성 + 전역 규칙 한 줄이면
        #   같은 그림을 그리면서 비용은 1회다.  덤으로 색이 생성 시점에 박히지 않아
        #   다크 모드 전환에도 자동으로 따라간다.
        self.setProperty("role", "thumbFrame")
        self.setProperty("subtle", "true" if subtle else "false")
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

    우클릭 '크게 보기' 가 좌우(기준·후보) 비교 뷰어를 연다 (#4) — view_requested.
    """

    swap_requested = pyqtSignal(object, float)        # (ImageItem, score)
    view_requested = pyqtSignal(object)               # ImageItem (크게보기)

    def __init__(self, item: ImageItem, score: float, parent=None,
                 *, size: int = _RUNNERUP_PX,
                 coord_mode: bool = False, tolerance: float = _DFLT_TOL) -> None:
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
        self._score_label = QLabel(
            fmt_score(score, coord_mode, tolerance, verbose=False), self)
        # µm 신호 통일(C21): '허용 초과'는 어디서나 위험색(빨강), 정상은 중립 보조색.
        color = theme.DANGER if over else theme.INK2
        self._score_label.setStyleSheet(
            f"color: {color}; font-size: 12px; font-family: {theme.FONT_MONO};")
        self._score_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if over:
            self._score_label.setToolTip(
                fmt_score(score, coord_mode, tolerance, verbose=True))
        lay.addWidget(self._score_label)

    def set_size(self, size: int) -> None:
        """슬라이더 변경 시 썸네일을 그 자리에서 재스케일 (#2)."""
        self._img.set_size(size)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.swap_requested.emit(self.item, self.score)
        super().mousePressEvent(event)

    # ★ 더블클릭 확대는 제거했다 — 후보를 연달아 눌러 교체하다 보면 두 번째 클릭이
    #   더블클릭으로 붙어 원하지 않는 비교 창이 떴다(사용자 지적).  프레스는 '교체'라는
    #   잦은 동작이고 확대는 드문 동작이라, 같은 버튼의 연타에 둘을 겹치면 잦은 쪽이
    #   드문 쪽을 오발한다.  확대는 아래 우클릭 메뉴로만 연다.
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
                 tolerance: float = _DFLT_TOL) -> None:
        super().__init__(parent)
        self.match = match
        self._is_unmatched = False
        self._pulse = 0.0                # 상태 전환 펄스(0=없음) — paintEvent 가 읽음
        self._prev_state = None          # 초기 로드 시 펄스 억제용
        self._coord_mode = bool(coord_mode)
        self._tolerance = float(tolerance) if tolerance > 0 else _DFLT_TOL
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
        self._slot_label.setProperty("role", "slotHead")
        # ★ 좁은 열(96px)이라 웨이퍼 ID 는 대부분 넘친다.  평범한 QLabel 은 말줄임 없이
        #   **그냥 잘라** 버려서 어느 슬롯인지 알 수 없었다(툴팁도 없었다).
        #   **가운데** 생략인 이유: 실제 ID 는 앞이 공통이다(W75483·04XYG4 / W75483·03XYC2).
        #   뒤를 자르면 서로 다른 웨이퍼가 똑같이 렌더된다 — `_SlotTile._elide` 와 같은 판단.
        self._slot_label.setWordWrap(False)       # 공백 없는 ID 는 줄바꿈이 안 듣고
        self._slot_label.setToolTip(match.slot)   # 최소 폭만 부풀린다(가로 넘침).
        self._elide_slot()
        slot_lay.addWidget(self._slot_label)
        # ★ 조용한 **테두리 버튼**이다(옛 결정: 링크형 — 반복 8행에 테두리가 쌓이지
        #   않게).  결정을 바꾼 근거: 사진 더블클릭 확대를 없앤 뒤로 이것이 좌우 비교
        #   뷰어로 가는 **유일하게 눈에 보이는 경로**가 됐다(썸네일 우클릭 메뉴는 발견되지
        #   않는다).  링크는 '눌러도 되는 것'으로 읽히지 않아 기능이 없는 것과 같았다.
        #   무게는 role="rowAction"(액션 등급이 아닌 중간 등급)으로 눌러 둔다.
        self.btn_view = NeonButton(i18n.KO.BTN_VIEW_LARGER, role="rowAction")
        self.btn_view.setMinimumHeight(36)   # ≥편안한 터치 타깃(C19)
        # 96px 슬롯 컬럼을 가로로 꽉 채워 클릭 면적을 키운다(컬럼 밖으로 나가지 않으므로
        # 가로 스크롤 위험 없음).
        self.btn_view.setSizePolicy(QSizePolicy.Policy.Expanding,
                                    QSizePolicy.Policy.Fixed)
        self.btn_view.clicked.connect(lambda: self._open_compare(0))
        slot_lay.addWidget(self.btn_view)
        slot_lay.addStretch(1)             # 같은 베이스라인(점수는 우측 VCenter)으로.
        slot_host.setFixedWidth(_SLOT_W)
        top.addWidget(slot_host)

        # ref 이미지 — 우클릭 ‘크게보기’ 는 단일 확대 대신 좌우 비교로 (#5).
        self._ref_img = self._make_thumb(match.ref_path, size=self._thumb_px,
                                         on_view=lambda: self._open_compare(0))
        top.addWidget(self._ref_img)

        # 화살표 — ★ 폭을 **상수로 못 박는다**.  sizeHint 에 맡기면 서체·DPI 에 따라
        #   흔들리고, 상단 헤더는 그 값을 알 방법이 없어 '기준/검증' 라벨 정렬이 어긋난다.
        #   행과 헤더가 같은 `_ARROW_W` 를 쓰게 해 정렬이 우연이 아니게 한다.
        arrow = QLabel("→", self)
        arrow.setProperty("role", "mutedArrow")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow.setFixedWidth(_ARROW_W)
        top.addWidget(arrow)

        # 1위 매치 이미지 + **사진 바로 밑 점수**.  차순위 타일(_RunnerUpTile)은
        # 처음부터 사진 밑에 점수를 달았는데 1위만 우측 metric 컬럼에 있어, 같은
        # 종류의 값을 두 군데서 다르게 읽어야 했다(사용자 지적).  우측 컬럼은
        # 목록 훑기용으로 그대로 두고, 사진 옆에도 같은 값을 붙여 짝을 맞춘다.
        self._val_host = QWidget(self)
        self._val_host.setProperty("role", "rowHost")
        self._val_host.setFixedWidth(self._thumb_px)
        _val_col = QVBoxLayout(self._val_host)
        _val_col.setContentsMargins(0, 0, 0, 0)
        _val_col.setSpacing(2)
        self._val_img = self._make_thumb(match.val_path, size=self._thumb_px,
                                         on_view=lambda: self._open_compare(0))
        _val_col.addWidget(self._val_img, alignment=Qt.AlignmentFlag.AlignCenter)
        self._val_score_label = QLabel(
            self._format_score(match.score, verbose=False), self._val_host)
        self._val_score_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _val_col.addWidget(self._val_score_label)
        self._style_val_score(match.score)
        top.addWidget(self._val_host)

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
        self.btn_toggle.setFixedSize(_toggle_col_w(), 44)
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
            # ★ 여기서 넣는 숫자는 **총 개수라 잔여가 아니다** — 문구가 뜻하는 값과
            #   다르므로 자리만 잡고(빈 문구), 실제 잔여 개수는 바로 뒤
            #   `_layout_runner_tiles` 가 채운다(보이게 만드는 것도 그쪽이다).
            self.btn_more = NeonButton("", role="link")
            self.btn_more.setVisible(False)
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

    def _style_val_score(self, score: float) -> None:
        """사진 밑 점수의 색 — 차순위 타일과 같은 규칙(초과=위험색, 정상=보조색).

        µm 신호 통일(C21): '허용 초과'는 어디서나 위험색으로 읽힌다.
        """
        over = self._coord_mode and score < 0
        color = theme.DANGER if over else theme.INK2
        self._val_score_label.setStyleSheet(
            f"color: {color}; font-size: 12px; font-family: {theme.FONT_MONO};")
        self._val_score_label.setToolTip(
            self._format_score(score, verbose=True) if over else "")

    def _format_score(self, score: float, *, verbose: bool = True) -> str:
        """score 값을 표시 문자열로 변환.

        좌표 모드: 거리(µm)로 역산 (score ≥ 0 → 허용 내, score < 0 → 허용 초과).
        일반 모드: 0~100% 백분율.
        ``verbose=False`` 면 '허용범위 초과' 접미어를 빼고 거리만 (metric 컬럼용 —
        초과 여부는 옆 칩이 전담).  실제 변환은 공용 포맷터가 하고, 여기서는 이 행의
        상태(좌표 모드·허용 오차)만 실어 보낸다.
        """
        return fmt_score(score, self._coord_mode, self._tolerance,
                         verbose=verbose)

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

    def _elide_slot(self) -> None:
        """슬롯 이름을 열 폭에 맞춰 **가운데** 생략.  전체 이름은 툴팁이 갖는다.

        ``_SLOT_W`` 가 고정이라 한 번만 계산해도 되지만, 폭이 바뀔 때를 대비해
        ``resizeEvent`` 에서도 다시 부른다(`_SlotTile._elide` 와 같은 형태).
        """
        avail = max(40, (self._slot_label.width() or _SLOT_W) - 4)
        fm = QFontMetrics(self._slot_label.font())
        self._slot_label.setText(fm.elidedText(
            self.match.slot, Qt.TextElideMode.ElideMiddle, avail))

    def _grid_cols(self) -> int:
        """아래 추가 줄 후보 열 수 — 가용 폭에 맞게(가로 스크롤 방지, #3)."""
        avail = self._row_width() - 60
        fit = avail // self._tile_w() if avail > 0 else 0
        return max(1, int(fit))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._elide_slot()
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
        sheets.run(viewer, full_bleed=True)

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
        sheets.run(viewer, full_bleed=True)

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
        # slot_host + 화살표 + metric(96) + 칩(chip_w) + 토글(_toggle_col_w())
        # + 행 여백/스페이싱(96).  변형이 커져도 두 이미지가 클램프되어 안 넘침.
        return _SLOT_W + _ARROW_W + 6 + 96 + p.chip_w + _toggle_col_w() + 96

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
        # 사진 밑 점수를 담은 컨테이너도 같은 폭이어야 헤더 정렬이 유지된다.
        self._val_host.setFixedWidth(applied)
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
            remaining = len(self._runners_up) - need
            self.btn_more.setVisible(remaining > 0)
            # 남은 개수를 라벨에 싣는다 — 3개 남았는지 40개 남았는지 모른 채
            # 반복 클릭하지 않게.  ★ 남은 게 없을 때는 **적지 않는다**: '(+0)'·'(+-2)'
            #   같은 문구가 숨겨진 버튼에 남아, setVisible 한 번이면 화면에 나온다.
            if remaining > 0:
                self.btn_more.setText(
                    i18n.KO.RUNNERUP_MORE_ROW_FMT.format(n=remaining))
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
        # 실패 검토 창이 계산한 점수의 세션 보관처 — 공유 캐시 다음으로 본다.
        self._review_scores = None
        self._val_pool: dict | None = None
        self._candidates_by_ref: dict | None = None
        self._thumb_px = theme.PROFILE.thumb_default_px   # 사진 크기 (#2) — 변형별
        self._resize_timer = QTimer(self)           # 슬라이더 드래그 디바운스
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._apply_thumb_size)
        # 행 배치 생성(P-01) — 세대 토큰 + 대기 목록 + 진행 오버레이.
        self._row_batch_timer: QTimer | None = None
        self._row_batch_gen = 0
        self._pending_rows: list = []
        self._row_total = 0
        self._load_gen = 0
        self._loading = LoadingOverlay(self)
        # 좌표 매칭 모드 관련
        self._coord_mode: bool = False
        self._tolerance: float = _DFLT_TOL
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
        # ★ 로고는 스크롤 **밖 최상단**에 둔다.  예전엔 스크롤 안 첫 위젯이라
        #   고정 컬럼 헤더와 첫 행 사이에 밴드가 끼어 표가 머리와 몸통으로 갈라졌다.
        root.addWidget(build_logo_label(self))

        # ★ 이 화면에는 표제가 없었다 — 로고를 스크롤 안에 두고 컬럼 헤더만 있어서,
        #   '지금 어느 단계인지' 를 화면이 말해 주지 않았다(다른 페이지는 전부 표제가 있다).
        self.title = QLabel(i18n.KO.MATCH_REVIEW_TITLE, self)
        self.title.setProperty("role", "title")
        root.addWidget(self.title)


        bar = QHBoxLayout()
        bar.setSpacing(12)
        self._tally_label = QLabel("", self)
        self._tally_label.setTextFormat(Qt.TextFormat.RichText)
        # 낱말만 봐서는 '매치 없음' 과 '매치 실패' 가 같은 말로 읽힌다 — 넷의
        # 차이를 화면에서 바로 확인할 수 있게 한다 (U-13).
        self._tally_label.setToolTip(i18n.KO.TALLY_TOOLTIP)
        bar.addWidget(self._tally_label)
        bar.addStretch(1)
        # ※ '확인 필요만' 필터는 제거했다 — 상단 탤리(일치·허용 초과·매치 없음)가 이미
        #   '무엇을 확인해야 하는지'를 말한다.  행을 숨기는 필터는 그 위에 상태를 하나
        #   더 얹고, '지금 보이는 게 전부인가'를 매번 되묻게 만들었다.
        size_label = QLabel(i18n.KO.IMAGE_SIZE_LABEL, self)
        size_label.setProperty("role", "muted")
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
        # 값 라벨 규약은 '사진 크기' 슬라이더 네 곳이 공유한다 — 모노 보조색 ·
        # 폭 64 · 우측 정렬(자릿수가 바뀌어도 숫자 끝이 흔들리지 않게).
        self.size_value = QLabel(f"{self._thumb_px} px", self)
        self.size_value.setProperty("role", "monoMuted")
        self.size_value.setFixedWidth(64)
        self.size_value.setAlignment(Qt.AlignmentFlag.AlignRight
                                     | Qt.AlignmentFlag.AlignVCenter)
        bar.addWidget(self.size_value)
        # [검토 완료] — 하단에서 상단 바로 이동 (동작 동일, 유지 카운트 표시).
        self.btn_done = NeonButton(i18n.KO.BTN_FINISH_REVIEW, role="primary")
        self.btn_done.setMinimumHeight(38)
        self.btn_done.clicked.connect(self._on_done)
        bar.addWidget(self.btn_done)
        root.addLayout(bar)

        # 상단 고정 컬럼 헤더(타이틀블록) — 행이 '떠 있는 카드' 가 아니라
        # '눈금 잡힌 제도 시트' 로 읽히게 한다.
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

        outer.addStretch(1)
        root.addWidget(scroll, stretch=1)

    def _build_list_header(self) -> QWidget:
        """검토 리스트 컬럼 헤더 — 행의 **모든** 컬럼에 정렬해 표/시트로 읽히게.

        ★ '기준 · 검증 · 후보' 를 **한 라벨**로 이미지 영역 전체에 얹어 두었더니 셋 중
        어느 것도 자기 사진 위에 없었다.  셋으로 쪼개 각각 대응 썸네일과 **같은 폭**을
        갖게 하고, 그 사이에 행의 화살표와 같은 폭의 빈 칸을 둔다.

        행의 상단 줄 구성과 **한 글자씩 대응**한다(간격·마진도 같아야 얹힌다):
          슬롯(96) │ 기준(thumb) │ →(_ARROW_W) │ 검증(thumb) │ 후보(가변)
                   │ 눈금 │ 거리(96) │ 판정(chip_w) │ 토글(toggle_w)

        ★ 썸네일 폭은 슬라이더로 100~360px 사이에서 바뀐다 — `_sync_header_widths` 가
        `_apply_thumb_size` 에서 헤더를 함께 다시 잡는다.  이걸 빼면 크기를 바꾼 순간
        정렬이 깨진다."""
        p = theme.PROFILE
        host = QFrame(self)
        host.setProperty("role", "listHeader")
        # 행과 **같은** 좌우 마진·간격 — 다르면 한 칸씩 밀린다.
        # 좌측만 행의 보더 두께를 더한다(위 `_ROW_BORDER_W` 주석 참조).
        lay = QHBoxLayout(host)
        lay.setContentsMargins(10 + _ROW_BORDER_W, 5, 10 + _ROW_BORDER_W, 5)
        lay.setSpacing(12)

        def head(text, *, width=None, align=Qt.AlignmentFlag.AlignLeft):
            lb = QLabel(text, host)
            lb.setProperty("role", "colHead")
            if width is not None:
                lb.setFixedWidth(width)
            lb.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
            return lb

        lay.addWidget(head(i18n.KO.COL_SLOT, width=_SLOT_W))
        # 기준·검증은 사진 위 **가운데**, 후보는 후보 스트립 위 **좌측**.
        self._hdr_ref = head(i18n.KO.COL_REF, width=self._thumb_px,
                             align=Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self._hdr_ref)
        self._hdr_arrow_gap = QWidget(host)            # 행의 '→' 자리
        self._hdr_arrow_gap.setFixedWidth(_ARROW_W)
        lay.addWidget(self._hdr_arrow_gap)
        self._hdr_val = head(i18n.KO.COL_VAL, width=self._thumb_px,
                             align=Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self._hdr_val)
        lay.addWidget(head(i18n.KO.COL_CANDIDATES), 1)  # 후보 영역(가변)
        rule = QFrame(host)
        rule.setProperty("role", "vrule")
        rule.setFixedWidth(1)
        lay.addWidget(rule)
        # ★ 이 열의 이름은 엔진에 따라 다르다 — 좌표는 '거리(µm)', 구형(유사도)은
        #   '유사도(%)'.  모드는 `load_state` 에서야 정해지므로 핸들을 들고 있다가
        #   그때 갈아 끼운다(헤더는 생성자에서 만들어진다).
        self._hdr_metric = head(i18n.KO.COL_DISTANCE, width=96,
                                align=Qt.AlignmentFlag.AlignRight)
        lay.addWidget(self._hdr_metric)
        lay.addWidget(head(i18n.KO.COL_VERDICT, width=p.chip_w,
                           align=Qt.AlignmentFlag.AlignCenter))
        # ★ 표(타이틀블록)에서 유일하게 이름 없던 칸이다 — ✕ 가 무슨 토글인지 헤더도
        #   버튼도 말하지 않아 호버해 툴팁을 봐야 알 수 있었다.  칩·버튼과 **같은
        #   낱말**을 쓴다(새 용어를 만들지 않는다).  폭 계약은 그대로.
        lay.addWidget(head(i18n.KO.CHIP_NO_MATCH, width=_toggle_col_w(),
                           align=Qt.AlignmentFlag.AlignCenter))
        return host

    def _sync_header_widths(self) -> None:
        """썸네일 크기가 바뀌면 헤더 컬럼 폭도 따라간다(정렬 유지)."""
        for lbl in (getattr(self, "_hdr_ref", None), getattr(self, "_hdr_val", None)):
            if lbl is not None:
                lbl.setFixedWidth(self._thumb_px)

    # ------------------------------------------------------------------
    def load_state(self,
                   matches: list[MatchResult],
                   *,
                   score_cache=None,
                   review_scores=None,
                   val_pool: dict | None = None,
                   candidates_by_ref: dict | None = None,
                   coord_mode: bool = False,
                   tolerance: float = _DFLT_TOL,
                   coord_failed_count: int = 0,
                   unmatched_keys: set | None = None) -> None:
        """매치 검토 화면 초기화.

        ``score_cache`` 와 ``val_pool`` 이 함께 주어지면 각 매치 행에 차순위
        후보를 클릭 가능한 형태로 보여주고, 클릭 시 그 후보로 매치를 교체한다.
        ``review_scores`` 는 실패 검토 창이 계산한 점수의 세션 보관처로,
        공유 캐시에 없는 쌍을 여기서 한 번 더 찾는다(없으면 종전과 같다).

        ``candidates_by_ref`` 가 주어지면 (fast 모드, #7) ``(slot, ref_path.name)``
        키로 미리 점수 내림차순 정렬된 ``[(ImageItem, score), ...]`` 후보 목록을
        직접 사용한다.  score_cache 가 비어있는 fast 모드에서도 후보가 보인다.

        ``coord_mode=True`` 면 score 를 µm 거리로 역산해 표시하고,
        ``coord_failed_count`` 를 요약에 "매치 실패 N쌍" 으로 포함한다.
        """
        # ★ 세대 토큰 — 배치 생성 도중 다시 들어오면(결과에서 검토로 복귀 등) 옛 배치가
        #   새 목록에 행을 섞어 넣는다.  토큰이 바뀐 배치는 스스로 멈춘다.
        self._load_gen = getattr(self, "_load_gen", 0) + 1
        self._pending_rows = []
        if getattr(self, "_row_batch_timer", None) is not None:
            self._row_batch_timer.stop()
        self._matches = list(matches)
        # ★ 결과 화면에서 되돌아올 때(U-10) 사용자가 표시한 '매치 없음' 을 되살린다.
        #   무조건 비우면 돌아온 화면에 빨간 행이 하나도 없다 — 표시를 다시 해야 한다.
        self._unmatched_keys.clear()
        if unmatched_keys:
            valid = {m.key for m in self._matches}
            self._unmatched_keys.update(k for k in unmatched_keys if k in valid)
        self._coord_mode = bool(coord_mode)
        self._tolerance = float(tolerance) if tolerance and tolerance > 0 else _DFLT_TOL
        # 점수 열 이름을 엔진에 맞춘다 — 구형(유사도)에서 '거리(µm)' 로 보이던 오표기.
        hdr = getattr(self, "_hdr_metric", None)
        if hdr is not None:
            hdr.setText(i18n.KO.COL_DISTANCE if self._coord_mode
                        else i18n.KO.COL_SIMILARITY)
        self._coord_failed_count = int(coord_failed_count)
        # 차순위 swap / 재계산용으로 score_cache + val_pool 참조 보관.
        self._score_cache = score_cache
        self._review_scores = review_scores
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
        self._current_row = None

        if not self._matches:
            empty = QLabel(i18n.KO.REVIEW_EMPTY_HINT)
            empty.setProperty("role", "mutedPad")
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
            self._start_row_batches(ordered)
        self._update_summary()
        # 검토 화면이 새로 열릴 때마다 스크롤을 항상 최상단으로 (이전 세션의
        # 스크롤 위치가 남지 않도록). 레이아웃 확정 후 적용.
        QTimer.singleShot(
            0, lambda: self._scroll.verticalScrollBar().setValue(0))

    # ------------------------------------------------------------------
    # 행 생성 — 한 번에 다 만들지 않는다.
    # ------------------------------------------------------------------
    _ROW_BATCH = 40

    def _start_row_batches(self, ordered: list) -> None:
        """행을 40개씩 나눠 만든다.

        ★ 예전엔 `load_state` 가 전 행을 한 루프로 만들었다.  행 하나가 위젯 ~15개
        (라벨·버튼·지연 썸네일 2장·차순위 타일)라 600행이면 진입에 4.0초 동안 창이
        통째로 굳었고, 그 사이 로딩 표시조차 없어 '죽은 것처럼' 보였다(실측).
        첫 배치만 즉시 만들어 화면을 띄우고 나머지는 이벤트 루프 틈에 채운다.

        ★ 생성 중에는 오버레이가 입력을 잠근다 — 아직 만들어지지 않은 행을 두고
        [검토 완료] 를 누르면 사용자가 보지 못한 매치가 전부 '유지' 로 확정된다."""
        self._pending_rows = list(ordered)
        first = self._pending_rows[:self._ROW_BATCH]
        del self._pending_rows[:self._ROW_BATCH]
        for m in first:
            self._append_row(m)
        if not self._pending_rows:
            self._arm_entrance()
            return
        total = len(ordered)
        self._row_total = total
        self._loading.show_overlay(i18n.KO.LOAD_REVIEW_ROWS)
        self._loading.set_progress(len(first), total, i18n.KO.LOAD_REVIEW_ROWS)
        if self._row_batch_timer is None:
            self._row_batch_timer = QTimer(self)
            self._row_batch_timer.setSingleShot(True)
            self._row_batch_timer.timeout.connect(self._make_next_rows)
        self._row_batch_gen = self._load_gen
        self._row_batch_timer.start(0)

    def _make_next_rows(self) -> None:
        if self._row_batch_gen != self._load_gen:
            return                                  # 옛 세대 — 조용히 멈춘다
        chunk = self._pending_rows[:self._ROW_BATCH]
        del self._pending_rows[:self._ROW_BATCH]
        for m in chunk:
            self._append_row(m)
        done = self._row_total - len(self._pending_rows)
        if self._pending_rows:
            self._loading.set_progress(done, self._row_total,
                                       i18n.KO.LOAD_REVIEW_ROWS)
            self._row_batch_timer.start(0)
        else:
            # ★ `hide_overlay` 는 동기 종료가 아니다(최소표시 래치 + 페이드아웃).
            #   바로 다음 줄에서 재생하면 아직 덮개 아래라 아무도 못 본다 —
            #   덮개가 **실제로 걷힌 뒤** 를 콜백으로 받는다.
            self._loading.hide_overlay(then=self._arm_entrance)

    #: 진입 스태거를 거는 행 수 — 시안이 '화면에 보이는 첫 8행' 으로 정했다.
    _ENTRANCE_ROWS = 8
    #: 행 사이 지연(ms) — 8행 × 60 + 220 = 640ms 안에 전부 끝난다.
    _ENTRANCE_STEP_MS = 60

    def _arm_entrance(self) -> None:
        """스태거를 예약한다 — 화면에 **앉은 뒤** 재생한다.

        ★ 행 생성은 `load_state` 안에서 끝나는데, 창은 그 뒤에 `_show_page` 로
        이 페이지를 올린다.  그래서 생성 직후에 재생하면 아직 스택의 current 가
        아니라 아무도 못 본다 — 진입 모션이 '진입할 때 안 보이는' 꼴이었다.
        창이 페이지를 실제로 앉히면 `on_shown()` 이 이걸 깨운다."""
        self._entrance_pending = True
        self._maybe_play_entrance()

    def on_shown(self) -> None:
        """창이 이 페이지를 화면에 앉혔다(`main_window._show_page` 의 커밋 지점)."""
        self._maybe_play_entrance()

    def _maybe_play_entrance(self) -> None:
        if not getattr(self, "_entrance_pending", False):
            return
        if not self.isVisible():
            return                      # 아직 화면 밖 — 예약만 남겨 둔다
        self._entrance_pending = False
        self._play_entrance()

    def _play_entrance(self) -> None:
        """목록이 준비된 순간, 위쪽 8행이 **하나씩** 자리에 안착한다 (25안).

        ★ 왜 스태거인가.  매칭이 끝나면 행 수백 개가 **한 프레임에 통째로** 나타나
        어디부터 봐야 할지 시선의 출발점이 없었다.  첫 행이 가장 먼저 안착하면
        그 자리가 출발점이 된다.
        ★ 왜 8행뿐인가.  보이는 만큼만이면 충분하고(스크롤로 생기는 행은 애니 없음),
        600행 전체에 걸면 화면 밖 행까지 비용을 내고 마지막 행이 수십 초 뒤에
        나타난다.  이 앱의 'per-item 애니 금지' 예산은 '가시 8행 1회' 로 지킨다.
        ★ 이펙트는 재생이 끝나면 `motion.rise_in` 이 스스로 떼어 낸다 — 붙여 두면
        Qt 가 그 행을 계속 오프스크린으로 다시 그려 스크롤이 무거워진다."""
        from .. import motion
        rows = [r for r in self._rows if not r.isHidden()][:self._ENTRANCE_ROWS]
        for i, row in enumerate(rows):
            motion.rise_in(row, delay_ms=i * self._ENTRANCE_STEP_MS)

    def unmatched_keys(self) -> set:
        """사용자가 '매치 없음' 으로 표시한 매치 키들(결과↔검토 왕복 보존용)."""
        return set(self._unmatched_keys)

    def all_matches(self) -> list:
        """검토 대상 **전부** — '매치 없음' 으로 표시한 것까지 포함한다.

        ★ `finished` 가 싣는 ``kept`` 와 다르다.  결과 화면에서 검토로 되돌아올 때
        ``kept`` 를 기반으로 다시 들어가면 '매치 없음' 행이 애초에 없으므로 그 표시를
        복원할 수 없고, 다음 [검토 완료] 에서 그 사진들이 매치에도 미매칭에도 없는
        상태가 돼 **결과에서 통째로 사라진다.**  왕복의 기반은 이쪽이어야 한다."""
        return list(self._matches)

    def _on_size_changed(self, value: int) -> None:
        self._thumb_px = int(value)
        self.size_value.setText(f"{value} px")
        self._resize_timer.start(150)

    def _apply_thumb_size(self) -> None:
        """슬라이더 변경 적용 (#2) — 행 상태를 보존한 채 썸네일 크기만 갱신."""
        for row in self._rows:
            row.set_thumb_size(self._thumb_px)
        self._sync_header_widths()      # 헤더가 따라가지 않으면 정렬이 깨진다

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

    # ── 현재 행 표시 (스왑 뒤 그 행을 보이게 스크롤하는 용도) ────────────────
    def _visible_rows(self) -> list["_MatchRow"]:
        """레이아웃 순서 기준 행 목록.

        ※ '확인 필요만' 필터를 지운 뒤로 모든 행이 항상 보이므로 사실상 ``_rows`` 와
        같다.  그래도 레이아웃 순서로 정렬해 주는 역할이 남아 있다(스왑은 행을 새로
        만들어 append 하므로 `_rows` 순서가 화면 순서와 어긋난다)."""
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

    # ※ 키보드 상호작용(↑↓ 행 이동 · R 매치 없음 · Enter 검토 완료)은 제거했다.
    #   기능 손실은 없다 — '매치 없음'은 행마다 있는 ✕ 토글, '검토 완료'는 상단
    #   [검토 완료] 버튼이 한다.  Enter 가 페이지 전체를 완료시키던 것은 특히 위험했다:
    #   포커스가 페이지에 있는 동안 무심코 누른 Enter 한 번이 검토를 끝냈다.

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
            # ★ 22안-B — 교체 직후 **행 전체가 한 번 펄스**한다.  교체는 행을 통째로
            #   다시 만들기 때문에 새 행의 `_prev_state` 가 None 이고, 그래서
            #   `_apply_state` 의 상태 전환 펄스가 **터지지 않는다** — 눌렀는데
            #   스냅으로 바뀌어 무엇이 달라졌는지 눈이 다시 찾아야 했다.
            #   시안이 고른 안은 '기존 헬퍼 그대로' 라 신규 코드가 0 이다.
            from .. import motion
            motion.pulse(new_row)
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
            if s is None and self._review_scores is not None:
                # 실패 검토 창에서 확정한 매치는 그 창이 계산한 점수가 유일한
                # 출처다 — 안 보면 그 행만 차순위가 통째로 비어 보인다.
                s = self._review_scores.get_pair(
                    match.slot, match.ref_path, v.path)
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
                    note=i18n.KO.NOTE_UNMATCHED_BY_USER,
                ))
            else:
                kept.append(m)
        self.finished.emit(kept, unmatched_refs)
