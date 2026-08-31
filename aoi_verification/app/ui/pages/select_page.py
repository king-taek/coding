"""Stage 1 — 후보 선별 화면.

레이아웃: 상단 컨트롤 바 (검증 제외 사진 보기 버튼 포함)
        / 좌 (남은 후보) · 중앙 (결정 대상) · 우 (검증 대상).
검증에서 제외한 사진들은 화면을 차지하지 않고, 상단 버튼을 누르면 팝업으로
모아 볼 수 있다.

키보드 단축키 — **화면 배치와 같은 방향**이다:
  →  → 검증 (오른쪽 '검증 대상' 패널로 보낸다)
  ←  → 제외
  Z  → 되돌리기

★ 예전에는 ←가 검증이었다.  그런데 '검증 대상' 패널은 **오른쪽**이라, 왼쪽을 누르면
  사진이 오른쪽으로 가는 모순이 있었다.  버튼도 [검증][제외] 순이라 화면과 어긋났다.
★ 숫자 키 1·2 는 없앴다.  방향키가 배치를 그대로 따르므로 외울 것이 하나면 충분하다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from PyQt6.QtCore import (QByteArray, QPoint, QPropertyAnimation, QRect,
                          Qt, pyqtSignal)
from PyQt6.QtGui import QFontMetrics, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                             QLabel, QProgressBar, QScrollArea, QSizePolicy,
                             QSlider, QSplitter, QVBoxLayout, QWidget)

from ... import config, i18n
from .. import theme
from ...models.slot import ImageItem
from ...utils import prefs as _prefs
from .progress_row import ProgressRowMixin
from ..widgets.app_logo import build_logo_label
from ..widgets.neon_button import NeonButton
from ..widgets.no_wheel_slider import NoWheelSlider
from ..widgets.neon_card import NeonCard
from ..widgets.scalable_image import ScalableImage
from ..widgets.slot_section import SlotSection
from ..widgets.thumb_grid import ThumbEntry
from ..widgets.zoom_window import (ZoomWindow, SOURCE_TARGET, SOURCE_EXCLUDED,
                                   SOURCE_CANDIDATES)
from ..widgets import sheet_host as sheets


# ---------------------------------------------------------------------------
@dataclass
class Stage1State:
    """페이지가 들고 있는 상태 (외부에서 주입/회수)."""
    queue: list[ImageItem]                       # 남은 후보 (앞에서 pop)
    targets: dict[str, list[ImageItem]] = field(default_factory=lambda: defaultdict(list))
    excluded: dict[str, list[ImageItem]] = field(default_factory=lambda: defaultdict(list))
    history: list[tuple[str, ImageItem]] = field(default_factory=list)
    # history: ("verify"|"exclude", item)


class _SidePanel(QFrame):
    """Slot 별 누적 표시 패널 (좌/우/하단 공용).

    [선택 모드] 버튼 클릭 시 inline 체크박스가 아니라 큰 팝업 다이얼로그가
    뜬다 — 사진을 가리지 않고 시원하게 다중 선택 가능.
    """

    selection_action = pyqtSignal(str, str, list)
    # (panel_name, action_id, [ImageItem])

    tile_clicked = pyqtSignal(str, str, object)        # (panel_name, slot, ImageItem)
    plus_clicked = pyqtSignal(str, str)                # (panel_name, slot)
    expand_requested = pyqtSignal(str, str, object)    # (panel_name, slot, ImageItem)

    def __init__(self, name: str, title: str,
                 *, vertical_scroll: bool = True,
                 title_tooltip: str = "",
                 actions: Optional[list[tuple[str, str, str]]] = None,
                 columns: int = 4,
                 tile_px: Optional[int] = None,
                 inline_select: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self._name = name
        self._title = title
        self._actions = list(actions or [])
        self._tile_px = tile_px
        self._inline_select = bool(inline_select)
        self._sections: dict[str, SlotSection] = {}
        self._cached: dict[str, list[ImageItem]] = {}

        self.setProperty("role", "section")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # 헤더 — 제목 + (인라인 선택 도구) + ‘선택 모드’ 버튼
        head = QHBoxLayout()
        ttl = QLabel(title, self)
        ttl.setProperty("role", "paneTitle")
        # ★ `role=paneTitle` 은 wordWrap 도 elide 도 없어 **말줄임표 없이 하드 클립**된다 —
        #   1024 폭에서 옛 제목("검증 대상 (검증하기로 한 사진들)")이 189px 을 요구하는데
        #   자리는 135px 이라 "검증 대상 (검증하기로 " 까지만 보였다.  제목을 짧게 줄이고
        #   (i18n) 부연은 툴팁으로 내렸다.  **여기에 elide 를 넣어 덮지 않는다** — 넣으면
        #   실측 하네스도 테스트도 '잘림 없음' 으로 읽혀 다음에 제목이 길어져도 아무도
        #   모른다.  대신 회귀 가드가 1024·1280 에서 **온전한 제목**이 들어가는지 잰다
        #   (`dev/tests/test_pane_title_fits.py`).
        if title_tooltip:
            ttl.setToolTip(title_tooltip)
        head.addWidget(ttl)
        head.addStretch(1)

        if self._actions:
            self._select_btn = NeonButton(i18n.KO.BTN_SELECT_MODE, role="ghost")
            self._select_btn.clicked.connect(self._open_bulk_select)
            head.addWidget(self._select_btn)
        outer.addLayout(head)

        # ★ 타일 클릭 선택(inline_select)은 오랫동안 **아무 데도 연결되지 않은** 죽은
        #   기능이었다 — 테두리만 생기고 그 선택으로 할 수 있는 일이 없었다.  선택이
        #   1장 이상일 때만 일괄 액션을 띄워, 팝업을 열지 않고도 처리하게 한다.
        #
        #   ★ 액션은 **제목 줄이 아니라 그 아래 제 줄**에 둔다.  이 패널은 폭이
        #   330px 남짓이라 제목 + 액션 2개 + [선택 모드] 를 한 줄에 넣으면 서로를
        #   밀어 라벨이 잘린다("선택 1 장 검증" → "1 장", "선택 모드" → "택 모").
        #   실측: 필요 113px, 실제 71px.
        #   ★ 두 액션을 **세로로 쌓는다.**  가로로 나란히 두면 1280x800(패널 263px)에서
        #   장수가 두 자리만 돼도 다시 잘린다(실측 필요 122px / 실제 118px).  세로로
        #   쌓으면 각 버튼이 패널 폭을 통째로 써서 세 자리 장수까지 여유가 있고,
        #   클릭 대상도 커진다(하우스 관습: 클릭 대상은 크고 명확하게).
        self._inline_buttons: list[NeonButton] = []
        self._inline_row = QVBoxLayout()
        self._inline_row.setContentsMargins(0, 0, 0, 0)
        self._inline_row.setSpacing(6)
        if self._inline_select and self._actions:
            for action_id, label_fmt, role in (
                    ("batch_exclude", i18n.KO.BTN_INLINE_EXCLUDE_FMT, "danger"),
                    ("batch_verify", i18n.KO.BTN_INLINE_VERIFY_FMT, "primary")):
                btn = NeonButton(label_fmt.format(n=0), role=role)
                btn.setProperty("labelFmt", label_fmt)
                btn.clicked.connect(
                    lambda _c=False, a=action_id: self._fire_inline_action(a))
                btn.hide()
                # 줄을 통째로 나눠 갖는다 — 글자가 늘어도(세 자리 장수) 잘리지 않는다.
                self._inline_row.addWidget(btn, 1)
                self._inline_buttons.append(btn)
        outer.addLayout(self._inline_row)

        # 스크롤 영역 ---------------------------------------------------
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        host = QWidget()
        self._scroll.setWidget(host)
        self._host_layout = QVBoxLayout(host)
        self._host_layout.setContentsMargins(4, 4, 4, 4)
        self._host_layout.setSpacing(10)
        self._host_layout.addStretch(1)

        # 후보 영역은 가로 스크롤이 절대 생기지 않도록 — 타일은 ThumbGrid 가
        # 패널 폭에 맞춰 열 수를 자동 reflow 한다.
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        if not vertical_scroll:
            self._scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            )

        outer.addWidget(self._scroll, stretch=1)
        self._columns = columns

    # ------------------------------------------------------------------
    def update_data(self, data: dict[str, list[ImageItem]]) -> None:
        """Slot → ImageItem 리스트 매핑으로 패널 갱신."""
        self._cached = {k: list(v) for k, v in data.items() if v}
        self._sections = {}

        while self._host_layout.count():
            item = self._host_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        for slot in sorted(self._cached.keys()):
            sec = SlotSection(slot, columns=self._columns,
                              select_mode=False,
                              inline_select=self._inline_select,
                              truncate=not self._inline_select,
                              tile_px=self._tile_px, parent=self)
            entries = [ThumbEntry(item=it) for it in self._cached[slot]]
            sec.set_entries(entries)
            sec.tile_clicked.connect(
                lambda ent, s=slot: self.tile_clicked.emit(self._name, s, ent.item)
            )
            sec.plus_clicked.connect(
                lambda s: self.plus_clicked.emit(self._name, s)
            )
            sec.expand_requested.connect(
                lambda ent, s=slot: self.expand_requested.emit(
                    self._name, s, ent.item)
            )
            sec.inline_changed.connect(self._refresh_inline_buttons)
            self._sections[slot] = sec
            self._host_layout.addWidget(sec)
        self._host_layout.addStretch(1)
        self._refresh_empty_hint()
        self._refresh_inline_buttons()

    # ------------------------------------------------------------------
    def set_slot(self, slot: str, items: list[ImageItem]) -> None:
        """한 슬롯의 섹션만 갱신/생성/제거 — 전체 재생성 없이 증분 갱신(#렉).

        - items 가 비면 그 슬롯 섹션을 제거.
        - 섹션이 있으면 그 섹션의 타일만 다시 그린다(``set_entries``).
        - 없으면 슬롯명 정렬 위치에 새 섹션을 삽입(끝 stretch 앞).
        """
        items = list(items)
        if not items:
            self._cached.pop(slot, None)
            sec = self._sections.pop(slot, None)
            if sec is not None:
                self._host_layout.removeWidget(sec)
                sec.deleteLater()
            self._refresh_empty_hint()
            return

        self._cached[slot] = items
        entries = [ThumbEntry(item=it) for it in items]
        sec = self._sections.get(slot)
        if sec is not None:
            sec.set_entries(entries)
            return

        sec = SlotSection(slot, columns=self._columns,
                          select_mode=False,
                          inline_select=self._inline_select,
                          truncate=not self._inline_select,
                          tile_px=self._tile_px, parent=self)
        sec.set_entries(entries)
        sec.tile_clicked.connect(
            lambda ent, s=slot: self.tile_clicked.emit(self._name, s, ent.item)
        )
        sec.plus_clicked.connect(
            lambda s: self.plus_clicked.emit(self._name, s)
        )
        sec.expand_requested.connect(
            lambda ent, s=slot: self.expand_requested.emit(
                self._name, s, ent.item)
        )
        sec.inline_changed.connect(self._refresh_inline_buttons)
        self._sections[slot] = sec
        # 슬롯명 정렬 위치에 삽입 — 끝 stretch 보다 앞.
        ordered = sorted(self._sections.keys())
        idx = ordered.index(slot)
        self._host_layout.insertWidget(idx, sec)
        self._refresh_empty_hint()

    def cached(self) -> dict[str, list[ImageItem]]:
        return {k: list(v) for k, v in self._cached.items()}

    # ------------------------------------------------------------------
    def _refresh_empty_hint(self) -> None:
        """비어 있을 때 이 칸의 용도를 한 줄로 알려 준다.

        ★ 라벨을 멤버로 들고 있으면 `update_data` 가 `_host_layout` 을 통째로 비울 때
        함께 파괴돼 죽은 참조가 남는다 — 매번 찾아 쓰고, 없으면 새로 만든다."""
        hint_text = getattr(i18n.KO, f"PANEL_{self._name.upper()}_EMPTY", "")
        if not hint_text:
            return
        # ★ `deleteLater` 로 지운 위젯은 이벤트 루프가 돌기 전까지 `findChildren` 에
        #   계속 잡힌다 — 안내가 두 장 겹쳐 보였다.  즉시 부모에서 떼어 낸다.
        for w in list(self._host_layout.parentWidget().findChildren(QLabel)):
            if w.property("role") == "emptyHint":
                self._host_layout.removeWidget(w)
                w.setParent(None)
                w.deleteLater()
        if self._sections:
            return
        lab = QLabel(hint_text, self._host_layout.parentWidget())
        lab.setProperty("role", "emptyHint")
        lab.setWordWrap(True)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._host_layout.insertWidget(0, lab)

    def remove_item(self, slot: str, item: ImageItem) -> bool:
        """사진 **한 장**만 패널에서 지운다 — 슬롯 전체를 다시 그리지 않는다.

        ★ 결정 1건마다 슬롯 타일을 통째로 재생성하던 경로(300장 슬롯에서 49ms/건 실측)를
        대체한다.  `+N` 트렁케이션이 켜진 패널에서는 숨은 사진이 올라와야 하므로
        `ThumbGrid.remove_entry` 가 False 를 돌려주고, 호출부는 기존 전체 갱신으로 넘어간다."""
        sec = self._sections.get(slot)
        if sec is None:
            return False
        entry = next((e for e in sec.grid._entries if e.item is item), None)
        if entry is None or not sec.remove_entry(entry):
            return False
        rest = [it for it in self._cached.get(slot, []) if it is not item]
        if rest:
            self._cached[slot] = rest
        else:                      # 마지막 한 장 → 섹션째 제거
            self._cached.pop(slot, None)
            self._sections.pop(slot, None)
            self._host_layout.removeWidget(sec)
            sec.deleteLater()
            self._refresh_empty_hint()
        self._refresh_inline_buttons()
        return True

    # ------------------------------------------------------------------
    # 인라인 선택 — 타일 클릭=선택 / 더블클릭=해제.  일괄작업·드래그는
    # ‘선택 모드’ 팝업으로 일원화했으므로 여기엔 전체선택(Ctrl+A) 헬퍼만 둔다.
    # ------------------------------------------------------------------
    def _set_all_inline(self, selected: bool) -> None:
        for sec in self._sections.values():
            sec.grid.set_all_inline_selected(selected)
        self._refresh_inline_buttons()

    def inline_selected_items(self) -> list[ImageItem]:
        """전 슬롯에서 인라인으로 고른 사진들(슬롯 순서 유지)."""
        out: list[ImageItem] = []
        for slot in sorted(self._sections.keys()):
            out.extend(e.item for e in self._sections[slot].inline_selected())
        return out

    def clear_inline_selection(self) -> None:
        """선택 표시를 지운다 — 액션 실행 뒤/화면을 떠날 때 스테일 테두리 방지."""
        self._set_all_inline(False)

    def _refresh_inline_buttons(self) -> None:
        n = len(self.inline_selected_items())
        for btn in getattr(self, "_inline_buttons", ()):
            fmt = btn.property("labelFmt") or "{n}"
            btn.setText(fmt.format(n=n))
            btn.setVisible(n > 0)

    def _fire_inline_action(self, action_id: str) -> None:
        items = self.inline_selected_items()
        if not items:
            return
        self.clear_inline_selection()
        self.selection_action.emit(self._name, action_id, items)

    # ------------------------------------------------------------------
    def _open_bulk_select(self) -> None:
        """[선택 모드] 클릭 → 큰 팝업 다이얼로그 띄움."""
        from ..widgets.bulk_select_dialog import BulkSelectDialog
        if not self._cached:
            return
        dlg = BulkSelectDialog(
            title=i18n.KO.BULK_SELECT_TITLE_FMT.format(panel=self._title),
            data=self._cached,
            actions=self._actions,
            parent=self,
        )
        dlg.selection_action.connect(
            lambda action_id, items: self.selection_action.emit(
                self._name, action_id, items,
            )
        )
        sheets.run(dlg, full_bleed=True)


# ---------------------------------------------------------------------------
class SelectPage(ProgressRowMixin, QWidget):
    """Stage 1 메인 위젯."""

    # 외부로 전달되는 시그널
    decision_made = pyqtSignal(str, object)            # ("verify"|"exclude", ImageItem)
    finished = pyqtSignal()                             # 큐가 모두 비었을 때
    state_changed = pyqtSignal()                        # 자동 저장 트리거
    # ★ Stage 2 의 [← 설정으로] 와 대칭.  이게 없어서 폴더를 잘못 고르고 들어오면
    #   되돌아갈 길이 없었다(선택 종료 → 매칭 → 검토 → 결과 → 새 검증, 5단계 우회).
    cancelled = pyqtSignal()

    PANEL_LEFT = "left"
    PANEL_RIGHT = "right"
    PANEL_BOTTOM = "bottom"

    # 좁은 창 (≤ THRESH_LO) 에선 좌/중/우 3-pane 을 위→아래 세로 스택으로
    # 자동 전환 → 가로 스크롤 회피.  넓은 창 (≥ THRESH_HI) 에선 원래 가로
    # 배치.  hysteresis 갭으로 임계 근처에서 flicker 방지 (#2).
    _RESPONSIVE_THRESH_LO = 960
    _RESPONSIVE_THRESH_HI = 1080

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._state: Stage1State | None = None
        self._current: Optional[ImageItem] = None
        self._phase_b_already_matched: dict[str, list[ImageItem]] = {}
        # 스플리터 방향을 첫 showEvent 에서 한 번만 확정했는지 (#cold-start).
        self._orientation_seeded = False
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # 상단 로고 — 이 화면은 전체 스크롤이 없어 맨 위에 그대로 둔다
        # (보이는 결과는 예전과 같다).
        root.addWidget(build_logo_label(self))

        # 상단 바 -------------------------------------------------------
        top = QHBoxLayout()
        self.title = QLabel(i18n.KO.STAGE1_TITLE, self)
        self.title.setProperty("role", "title")
        top.addWidget(self.title)
        # ── 진행 상태는 **표제 바로 옆**이다 (구조개편 2안-B) ────────────────────
        # ★ 예전엔 이 두 라벨이 액션 버튼 **뒤**, 줄의 오른쪽 끝에 있었다.  진행률은
        #   이 화면의 핵심 상태인데 위계가 최하위였고, 화면마다 자리가 달라 눈의
        #   이동이 학습되지 않았다.  표제 옆으로 옮기면 '무슨 화면 · 어디까지' 가
        #   한 시선에 읽히고, 다섯 화면이 같은 규약을 쓴다.
        #   (하단 상태바는 그대로 둔다 — 크레딧·메모리는 계속 거기 산다.)
        top.addSpacing(14)
        self.progress_label = QLabel("", self)
        self.progress_label.setProperty("role", "muted")
        top.addWidget(self.progress_label)
        # 수치는 한 등급 위로 — 모노 본문 잉크(자릿수가 바뀌어도 흔들리지 않는다).
        self.progress_count = QLabel("", self)
        self.progress_count.setProperty("role", "progressCountLg")
        top.addWidget(self.progress_count)
        top.addStretch(1)
        # [검증 제외 사진 보기 (n)] — 제외된 사진은 화면에서 숨기고,
        # 이 버튼으로 팝업에서 모아 본다. 0 장이면 비활성.
        self.btn_view_excluded = NeonButton(
            i18n.KO.BTN_VIEW_EXCLUDED_FMT.format(n=0), role="ghost",
        )
        self.btn_view_excluded.clicked.connect(self._open_excluded_dialog)
        self.btn_view_excluded.setEnabled(False)
        top.addWidget(self.btn_view_excluded)
        # [선택 종료] — 남은 미결정 사진을 모두 ‘검증 제외’ 로 처리하고
        # Stage 2 로 진행 (사용자 결정).  큐가 비어 있으면 자동 비활성.
        # ★ 조회용 [검증 제외 사진 보기] 와 물리적으로 떼어 놓는다 — 8px 간격으로
        #   붙어 있으면 한 칸 옆을 잘못 눌러 파괴 흐름의 확인창으로 들어간다.
        top.addSpacing(24)
        self.btn_end_selection = NeonButton(
            i18n.KO.BTN_END_SELECTION, role="warn",
        )
        self.btn_end_selection.clicked.connect(self._end_selection_now)
        self.btn_end_selection.setEnabled(False)
        top.addWidget(self.btn_end_selection)
        root.addLayout(top)

        # 제목 줄 바로 아래 폭 전체 진행 눈금 — 표시 전용(텍스트 없음, 채움 스냅).
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setProperty("role", "pageProgress")
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

        # LOT(slot)별 전체 장수 — 참고용으로 작게 한 줄 (#2).  세션 내 불변이라
        # load_state 에서 1회만 채운다.  슬롯이 많으면 elide + 전체는 툴팁.
        self.lot_counts_label = QLabel("", self)
        self.lot_counts_label.setProperty("role", "mutedSmall")
        self.lot_counts_label.setWordWrap(False)
        root.addWidget(self.lot_counts_label)

        # 중앙 3-pane — QSplitter 로 사용자 조절 + 상태 영속 -------------
        self._h_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._h_splitter.setHandleWidth(6)
        self._h_splitter.setChildrenCollapsible(False)

        # LEFT --------------------------------------------------------
        # 측면 패널 타일 — 기본(240px)의 50% (사용자 요청).  같은 패널 폭에서
        # 한 줄에 더 많은 사진이 들어가고 한눈에 더 많은 후보를 비교할 수 있다.
        side_tile = config.Sizing.SIDE_TILE_PX      # 사이드 패널 타일(=120, D2)
        self.left_panel = _SidePanel(
            self.PANEL_LEFT, i18n.KO.PANEL_LEFT_CANDIDATES,
            title_tooltip=i18n.KO.PANEL_LEFT_CANDIDATES_TOOLTIP,
            actions=[
                # 가운데 버튼 줄과 **같은 순서**(제외 왼쪽 · 검증 오른쪽)로 둔다 —
                # 한 화면에서 두 곳의 순서가 다르면 손이 헷갈린다.
                ("batch_exclude", i18n.KO.BTN_BATCH_EXCLUDE, "danger"),
                ("batch_verify", i18n.KO.BTN_BATCH_VERIFY, "primary"),
            ],
            # 타일 절반 크기 → 같은 폭에 3 열 그리드 깔리도록.
            columns=3,
            tile_px=side_tile,
            inline_select=True,        # 타일 클릭=선택 / 더블클릭=해제 (Ctrl+A=전체)
        )
        self.left_panel.selection_action.connect(self._on_batch_action)
        self.left_panel.tile_clicked.connect(self._on_tile_click)
        self.left_panel.plus_clicked.connect(self._on_plus_click)
        # 후보 패널은 확대(줌) 모드를 두지 않는다 — 더블클릭은 선택 해제용.
        # 3 col × (120 thumb + 14 padding) + spacing + 패널 padding 을 담을 최소
        # 너비.  좁은 창에선 세로 스택으로 reflow 되어 무관.
        self.left_panel.setMinimumWidth(220)
        self._h_splitter.addWidget(self.left_panel)

        # CENTER ------------------------------------------------------
        center_card = NeonCard(role="card", parent=self)
        cl = center_card.body()
        center_title = QLabel(i18n.KO.PANEL_CENTER_DECIDE, center_card)
        center_title.setProperty("role", "paneTitle")
        center_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(center_title)

        # Slot 명 (파일명은 표시하지 않음) -----------------------------
        self.slot_label = QLabel("", center_card)
        self.slot_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.slot_label.setProperty("role", "slotLabel")
        cl.addWidget(self.slot_label)

        # 사진 크기 슬라이더 -------------------------------------------
        # ★ 이 줄은 **사진 아래**에 붙인다(아래 `cl.addLayout(size_row)`).  한 장씩
        #   판단하는 카드의 시선 축은 슬롯 → 사진 → 판단 버튼인데, 세션에 한 번쯤
        #   만지는 설정 컨트롤이 그 한가운데를 가로지르면 매 판단마다 사진 시작점이
        #   ~34px 내려간다.  검토 화면이 이미 '크기 조절은 가장자리' 선례를 세웠다.
        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        size_label = QLabel(i18n.KO.IMAGE_SIZE_LABEL, center_card)
        size_label.setProperty("role", "muted")
        self.size_slider = NoWheelSlider(Qt.Orientation.Horizontal, center_card)
        self.size_slider.setRange(ScalableImage.MIN_LONG_EDGE,
                                   ScalableImage.MAX_LONG_EDGE)
        # 모니터 크기에 맞춰 자동 시작값. 사용자가 바꾸면 세션 동안만 유지되고
        # 프로그램 재시작 시 다시 자동 맞춤으로 초기화 (prefs 저장 안 함).
        self.size_slider.setValue(ScalableImage.auto_fit_long_edge())
        self.size_slider.setSingleStep(20)
        self.size_slider.setPageStep(80)
        self.size_value = QLabel(f"{self.size_slider.value()} px", center_card)
        self.size_value.setProperty("role", "monoMuted")
        self.size_value.setFixedWidth(64)
        self.size_value.setAlignment(Qt.AlignmentFlag.AlignRight
                                     | Qt.AlignmentFlag.AlignVCenter)
        self.size_slider.valueChanged.connect(self._on_size_changed)
        size_row.addWidget(size_label)
        size_row.addWidget(self.size_slider, stretch=1)
        size_row.addWidget(self.size_value)

        # 이미지 (스크롤 영역) -----------------------------------------
        self.center_img = ScalableImage(center_card)
        self._img_scroll = QScrollArea(center_card)
        self._img_scroll.setWidgetResizable(False)
        self._img_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_scroll.setWidget(self.center_img)
        # 색은 QSS 가 준다 — 구우면 색 모드 전환 때 이 영역만 옛 색으로 남는다.
        self._img_scroll.setProperty("role", "imageViewport")
        self._img_scroll.setMinimumHeight(300)
        self._img_scroll.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Expanding)
        cl.addWidget(self._img_scroll, stretch=1)
        cl.addLayout(size_row)          # 설정은 시선 축 밖(사진 아래) — 위 주석 참조

        # 버튼 줄 (사진 밑에 명확히 분리) -------------------------------
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.setContentsMargins(0, 6, 0, 0)
        self.btn_verify = NeonButton("✓  " + i18n.KO.BTN_VERIFY, role="primary")
        self.btn_exclude = NeonButton("✕  " + i18n.KO.BTN_EXCLUDE, role="danger")
        self.btn_undo = NeonButton(i18n.KO.BTN_UNDO, role="ghost")
        self.btn_verify.setToolTip(i18n.KO.SHORTCUT_TOOLTIP)
        self.btn_exclude.setToolTip(i18n.KO.SHORTCUT_TOOLTIP)
        self.btn_undo.setToolTip(i18n.KO.SHORTCUT_TOOLTIP)
        self.btn_verify.clicked.connect(lambda: self._decide("verify"))
        self.btn_exclude.clicked.connect(lambda: self._decide("exclude"))
        self.btn_undo.clicked.connect(self._undo)
        btn_row.addWidget(self.btn_undo)
        btn_row.addStretch(1)
        # ★ 순서가 곧 방향이다 — [제외]가 왼쪽, [검증]이 오른쪽.  오른쪽 패널이
        #   '검증 대상' 이므로 "오른쪽으로 보내면 오른쪽에 쌓인다" 가 성립한다.
        #   방향키(→ 검증 / ← 제외)도 이 배치를 그대로 따른다.
        btn_row.addWidget(self.btn_exclude)
        btn_row.addWidget(self.btn_verify)
        self._btn_row = btn_row          # 배치 계약 테스트가 잡을 수 있게.
        cl.addLayout(btn_row)

        center_card.setMinimumWidth(360)
        self._h_splitter.addWidget(center_card)

        # RIGHT — 좌측과 동일한 절반 타일 크기 + 3열 그리드 (사용자 요청).
        self.right_panel = _SidePanel(
            self.PANEL_RIGHT, i18n.KO.PANEL_RIGHT_TARGETS,
            title_tooltip=i18n.KO.PANEL_RIGHT_TARGETS_TOOLTIP,
            actions=[
                ("to_exclude", i18n.KO.BTN_MOVE_TO_EXCLUDE, "warn"),
                ("recenter", i18n.KO.BTN_BACK_TO_CENTER, "ghost"),
            ],
            columns=3,
            tile_px=side_tile,
        )
        self.right_panel.selection_action.connect(self._on_batch_action)
        self.right_panel.tile_clicked.connect(self._on_tile_click)
        self.right_panel.plus_clicked.connect(self._on_plus_click)
        self.right_panel.setMinimumWidth(220)
        self._h_splitter.addWidget(self.right_panel)

        self._h_splitter.setStretchFactor(0, 2)
        self._h_splitter.setStretchFactor(1, 4)
        self._h_splitter.setStretchFactor(2, 2)

        root.addWidget(self._h_splitter, stretch=1)

        # 저장된 분할 비율 복원 + 변경 시 영속화 -------------------------
        _p2 = _prefs.load()
        if _p2.splitter_state_select_h:
            self._h_splitter.restoreState(
                QByteArray.fromBase64(_p2.splitter_state_select_h.encode("ascii"))
            )
        self._h_splitter.splitterMoved.connect(self._save_splitter_state)

        # 단축키 — 화면 배치와 같은 방향(오른쪽 = 검증) ------------------
        QShortcut(QKeySequence("Right"), self,
                  activated=lambda: self._decide("verify"))
        QShortcut(QKeySequence("Left"), self,
                  activated=lambda: self._decide("exclude"))
        QShortcut(QKeySequence("Z"), self, activated=self._undo)
        # Ctrl+A — 좌측 후보 패널 전체 선택 (#2).
        QShortcut(QKeySequence.StandardKey.SelectAll, self,
                  activated=self._select_all_candidates)

    def _select_all_candidates(self) -> None:
        if self.isVisible():
            self.left_panel._set_all_inline(True)

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def resizeEvent(self, event):                       # noqa: N802
        super().resizeEvent(event)
        self._update_splitter_orientation()
        self._elide_lot_counts()

    def showEvent(self, event):                         # noqa: N802
        super().showEvent(event)
        self._elide_lot_counts()
        # 실제로 보여질 때의 너비로 방향을 한 번 확정한다. 구성 중 잠깐 좁아졌다
        # 다시 넓어지는 과도기 때문에 세로로 굳는 버그를 방지 — 히스테리시스는
        # 그 이후의 사용자 리사이즈에만 적용된다.
        if not self._orientation_seeded:
            self._seed_splitter_orientation()

    def _seed_splitter_orientation(self) -> None:
        """히스테리시스 없이 중점 기준으로 초기 방향을 확정 (#cold-start)."""
        if not hasattr(self, "_h_splitter"):
            return
        w = self.width()
        mid = (self._RESPONSIVE_THRESH_LO + self._RESPONSIVE_THRESH_HI) // 2
        target = (Qt.Orientation.Horizontal if w >= mid
                  else Qt.Orientation.Vertical)
        if self._h_splitter.orientation() != target:
            self._h_splitter.setOrientation(target)
            self._h_splitter.setSizes([300, 600, 300]
                                      if target == Qt.Orientation.Horizontal
                                      else [200, 500, 200])
        self._orientation_seeded = True

    def _update_splitter_orientation(self) -> None:
        """창 폭에 따라 H ↔ V splitter 전환 — 가로 스크롤 없이 reflow."""
        if not hasattr(self, "_h_splitter"):
            return
        # 첫 표시(showEvent)로 방향이 확정되기 전의 구성 중 리사이즈는 무시 —
        # 과도기 너비로 방향이 잘못 굳는 것을 막는다.
        if not self._orientation_seeded:
            return
        cur = self._h_splitter.orientation()
        w = self.width()
        # hysteresis — 임계 근처에서 토글이 깜빡이지 않도록.
        if cur == Qt.Orientation.Horizontal and w < self._RESPONSIVE_THRESH_LO:
            self._h_splitter.setOrientation(Qt.Orientation.Vertical)
            self._h_splitter.setSizes([200, 500, 200])
        elif cur == Qt.Orientation.Vertical and w > self._RESPONSIVE_THRESH_HI:
            self._h_splitter.setOrientation(Qt.Orientation.Horizontal)
            self._h_splitter.setSizes([300, 600, 300])

    # ------------------------------------------------------------------
    def _save_splitter_state(self, *args) -> None:
        try:
            _prefs.patch(
                splitter_state_select_h=bytes(
                    self._h_splitter.saveState().toBase64()
                ).decode("ascii"),
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load_state(self,
                   queue: list[ImageItem],
                   targets: dict[str, list[ImageItem]] | None = None,
                   excluded: dict[str, list[ImageItem]] | None = None,
                   history: list[tuple[str, ImageItem]] | None = None,
                   phase_b_already_matched: dict[str, list[ImageItem]] | None = None,
                   ) -> None:
        self._state = Stage1State(
            queue=list(queue),
            targets=defaultdict(list, {k: list(v) for k, v in (targets or {}).items()}),
            excluded=defaultdict(list, {k: list(v) for k, v in (excluded or {}).items()}),
            history=list(history or []),
        )
        self._phase_b_already_matched = phase_b_already_matched or {}
        self._update_lot_counts()
        self._refresh_all()
        self._advance_to_next()

    def _update_lot_counts(self) -> None:
        """LOT(slot)별 전체 장수 라벨 갱신 (#2) — 세션 내 불변이라 1회만."""
        if self._state is None:
            self._lot_counts_full = ""
            self.lot_counts_label.setText("")
            return
        totals: dict[str, int] = defaultdict(int)
        for it in self._state.queue:
            totals[it.slot] += 1
        for pool in (self._state.targets, self._state.excluded):
            for slot, items in pool.items():
                totals[slot] += len(items)
        if not totals:
            self._lot_counts_full = ""
            self.lot_counts_label.setText("")
            return
        parts = [f"{slot} {totals[slot]}" for slot in sorted(totals)]
        self._lot_counts_full = i18n.KO.LOT_COUNTS_PREFIX + "  ·  ".join(parts)
        self.lot_counts_label.setToolTip(self._lot_counts_full)
        self._elide_lot_counts()

    def _elide_lot_counts(self) -> None:
        """전체 문자열을 지금 폭에 맞춰 **가운데** 생략.  원본은 툴팁이 갖는다.

        ★ 폭을 잴 수 있을 때마다 **다시** 부른다.  `load_state` 시점에는 이 페이지가 아직
        레이아웃되지 않아 라벨 폭이 QLabel 기본값(100px)이다 — 그때 한 번만 자르면 슬롯이
        한두 개인 평범한 세션에서도 라벨이 통째로 잘린 채 **영원히** 복구되지 않는다
        (창을 키워도 그대로다).  `_MatchRow._elide_slot` 이 `resizeEvent` 에서 다시 부르는
        것과 같은 이유다.
        """
        full = getattr(self, "_lot_counts_full", "")
        if not full:
            return
        avail = self.lot_counts_label.contentsRect().width()
        if avail <= 0:
            avail = self.lot_counts_label.width()
        fm = QFontMetrics(self.lot_counts_label.font())
        self.lot_counts_label.setText(
            fm.elidedText(full, Qt.TextElideMode.ElideMiddle, max(80, avail)))

    def get_state(self) -> Stage1State | None:
        return self._state

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 표시 모드 / 슬롯별 표시 항목 (one-slot 모드 반영, #렉)
    # ------------------------------------------------------------------
    def _is_single_slot_mode(self) -> bool:
        """측당 총 사진이 임계 이상이면 현재 슬롯만 표시(위젯 수 최소화)."""
        if self._state is None:
            return False
        return self._total_count() >= config.SELECT_SINGLE_SLOT_THRESHOLD

    def _left_items_for_slot(self, slot: str) -> list[ImageItem]:
        """좌(후보) 패널에 그 슬롯이 보여줄 항목 = 큐 항목 − 현재 결정중 사진."""
        if self._state is None:
            return []
        if self._is_single_slot_mode() and (
                self._current is None or slot != self._current.slot):
            return []
        items = [it for it in self._state.queue if it.slot == slot]
        if self._current is not None and self._current.slot == slot:
            items = [it for it in items if it is not self._current]
        return items

    def _right_items_for_slot(self, slot: str) -> list[ImageItem]:
        """우(검증 대상) 패널에 그 슬롯이 보여줄 항목 — **모든 슬롯을 보여준다.**

        ★ one-slot 모드(`_is_single_slot_mode`)를 여기에는 걸지 않는다.  예전에는
        좌·우 양쪽에 걸려 있어서, 슬롯이 넘어가는 순간 이 패널의 `SlotSection` 이
        통째로 파괴됐다 사용자가 되돌아올 때 다시 만들어졌다 — 사용자가 신고한
        "후보 선별에서 slot별로 처리될 때 검증 대상 쪽 사진의 썸네일이 초기화된다"
        가 그것이다.  게다가 지금까지 고른 것이 화면에서 사라져 무엇을 골랐는지
        확인할 수도 없었다.

        위젯 수가 터지는 곳은 **후보(좌) 패널**이다(큐 전체가 들어온다).  이쪽은
        사용자가 직접 고른 것만 들어오고, `SlotSection(truncate=True)` 이라
        (우측은 `inline_select=False`) `+N` 으로 잘려 위젯 수가 이미 묶여 있다."""
        if self._state is None:
            return []
        return list(self._state.targets.get(slot, []))

    def _update_slots_incremental(self, slots, *, dropped_left=()) -> None:
        """주어진 슬롯들만 좌·우 패널에서 증분 갱신(전체 재생성 없음, #렉).

        ``dropped_left`` 는 좌측 패널에서 **딱 그 사진만** 빠진 경우다(결정 1건 →
        결정된 사진 + 새 현재 사진).  그때는 슬롯 전체를 다시 그리지 않고 타일 한 장만
        떼어 낸다 — 300장 슬롯에서 결정 1건당 49ms(3프레임) 걸리던 재생성을 없앤다.
        `+N` 트렁케이션 패널이면 `remove_item` 이 False 를 돌려주므로 그때만 전체 갱신."""
        fast_ok = True
        for item in dropped_left:
            if item is None or not self.left_panel.remove_item(item.slot, item):
                fast_ok = False
                break
        for s in slots:
            if not s:
                continue
            if not fast_ok:
                self.left_panel.set_slot(s, self._left_items_for_slot(s))
            self.right_panel.set_slot(s, self._right_items_for_slot(s))

    def _refresh_all(self) -> None:
        if self._state is None:
            return
        if self._is_single_slot_mode():
            cur = self._current.slot if self._current is not None else None
            left = {cur: self._left_items_for_slot(cur)} if cur else {}
            self.left_panel.update_data(left)
            # ★ 우측은 슬롯을 좁히지 않는다 — 지금까지 고른 것을 계속 보여 준다
            #   (`_right_items_for_slot` 주석 참조).
            self.right_panel.update_data(
                {k: list(v) for k, v in self._state.targets.items()})
        else:
            # left = 남은 큐를 Slot 별로 그룹화
            left_groups: dict[str, list[ImageItem]] = defaultdict(list)
            for it in self._state.queue:
                left_groups[it.slot].append(it)
            # 현재 결정 중인 사진은 left 에서 제외
            if self._current is not None and self._current in left_groups[self._current.slot]:
                left_groups[self._current.slot].remove(self._current)
                if not left_groups[self._current.slot]:
                    left_groups.pop(self._current.slot, None)
            self.left_panel.update_data(left_groups)
            self.right_panel.update_data(
                {k: list(v) for k, v in self._state.targets.items()})
        self._refresh_excluded_button()
        self._refresh_end_selection_button()

    def _refresh_end_selection_button(self) -> None:
        """큐에 미결정 사진이 남아 있으면 활성, 비면 비활성."""
        n_remaining = len(self._state.queue) if self._state else 0
        self.btn_end_selection.setEnabled(n_remaining > 0)

    def _refresh_excluded_button(self) -> None:
        if self._state is None:
            self.btn_view_excluded.setText(
                i18n.KO.BTN_VIEW_EXCLUDED_FMT.format(n=0)
            )
            self.btn_view_excluded.setEnabled(False)
            return
        n = sum(len(v) for v in self._state.excluded.values())
        self.btn_view_excluded.setText(
            i18n.KO.BTN_VIEW_EXCLUDED_FMT.format(n=n)
        )
        self.btn_view_excluded.setEnabled(n > 0)

    def _advance_to_next(self) -> None:
        if self._state is None:
            return
        if not self._state.queue:
            self._current = None
            self.center_img.clear_image()
            self.slot_label.setText("")
            self._clear_progress()
            self.finished.emit()
            return
        self._current = self._state.queue[0]
        self._show_center(self._current)
        self._set_progress(self._current.slot,
                           self._already_decided_count(), self._total_count())
        self._refresh_all()

    def _advance_incremental(self, prev_slot: str) -> None:
        """결정 1건 후 — 바뀐 슬롯만 증분 갱신(전체 재생성 금지, #렉).

        ``_advance_to_next`` 과 달리 ``_refresh_all`` (전 패널 재생성) 을 호출하지
        않고, 영향받은 슬롯(직전 슬롯 + 새 현재 슬롯) 섹션만 갱신한다.
        """
        if self._state is None:
            return
        if not self._state.queue:
            self._current = None
            self.center_img.clear_image()
            self.slot_label.setText("")
            self._clear_progress()
            # 큐가 비었다 — 좌측에서 새로 빠지는 사진은 없다(결정된 사진은 중앙에 있었다).
            self._update_slots_incremental({prev_slot})
            self._refresh_excluded_button()
            self._refresh_end_selection_button()
            self.finished.emit()
            return
        self._current = self._state.queue[0]
        self._show_center(self._current)
        self._set_progress(self._current.slot,
                           self._already_decided_count(), self._total_count())
        # ★ 좌측에서 빠지는 것은 **새로 중앙으로 올라온 사진 한 장뿐**이다 —
        #   방금 결정한 사진은 결정 전부터 중앙에 있었으므로 좌측 패널에 없었다.
        #   (여기를 두 장으로 잡아 두면 첫 장에서 실패해 매번 전체 재생성으로 떨어진다.)
        self._update_slots_incremental({prev_slot, self._current.slot},
                                       dropped_left=(self._current,))
        self._refresh_excluded_button()
        self._refresh_end_selection_button()

    def _already_decided_count(self) -> int:
        if self._state is None:
            return 0
        n = 0
        for v in self._state.targets.values():
            n += len(v)
        for v in self._state.excluded.values():
            n += len(v)
        return n

    def _total_count(self) -> int:
        if self._state is None:
            return 0
        return self._already_decided_count() + len(self._state.queue)

    def _show_center(self, item: ImageItem) -> None:
        self.center_img.set_image(item.path)
        # 파일명은 표시하지 않고 Slot 명만 노출한다 (요청 사항).
        self.slot_label.setText(i18n.KO.SLOT_LABEL_FMT.format(slot=item.slot))

    def _on_size_changed(self, value: int) -> None:
        self.size_value.setText(f"{value} px")
        self.center_img.set_target_size(value)
        # 사용자 변경은 세션 동안만 유지 — 재시작 시 자동 맞춤으로 초기화.

    # ------------------------------------------------------------------
    def request_back_to_setup(self) -> None:
        """창(여정 레일)이 부르는 복귀 진입점.

        ★ 화면 안의 [← 설정으로] 버튼은 없앴다(구조개편 1안-A) — 뒤로가기의 의미가
        화면마다 다른 것이 문제였고, 이제 레일이 '어디로' 를 통일해서 말한다.
        하지만 **무엇이 사라지는지** 는 이 화면만 아는 사실이라 확인은 여기 남는다."""
        self._on_back_to_setup()

    def _on_back_to_setup(self) -> None:
        """설정 화면으로 돌아간다 — 진행한 결정이 있으면 한 번 확인한다."""
        decided = 0
        if self._state is not None:
            decided = sum(len(v) for v in self._state.targets.values())
            decided += sum(len(v) for v in self._state.excluded.values())
        if decided:
            from PyQt6.QtWidgets import QMessageBox
            r = sheets.ask(
                self, i18n.KO.SELECT_BACK_CONFIRM_TITLE,
                i18n.KO.SELECT_BACK_CONFIRM_FMT.format(n=decided),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if r != QMessageBox.StandardButton.Yes:
                return
        self.clear_inline_selection_all()
        self.cancelled.emit()

    def clear_inline_selection_all(self) -> None:
        """화면을 떠날 때 선택 테두리를 지운다 — 다시 들어왔을 때 스테일 표시 방지."""
        self.left_panel.clear_inline_selection()

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------
    def _decide(self, action: str) -> None:
        # QShortcut 의 기본 context 가 WindowShortcut 이라 다른 페이지가
        # 보이는 상태에서도 →/← 가 여기로 전달된다. 보이지 않을 땐 무시.
        if not self.isVisible():
            return
        if self._state is None or self._current is None:
            return
        item = self._current
        # 큐에서 제거
        try:
            self._state.queue.remove(item)
        except ValueError:
            pass
        target_pool = self._state.targets if action == "verify" else self._state.excluded
        target_pool[item.slot].append(item)
        self._state.history.append((action, item))
        self.decision_made.emit(action, item)
        self.state_changed.emit()
        # ★ 26안 — 결정한 사진이 **결정의 방향**으로 밀려나며 사라진다.
        #   결정 자체는 위에서 이미 커밋됐다(모션은 잔상일 뿐) — 연타해도 애니를
        #   기다리지 않는다.  떠나는 그림의 사본을 먼저 떠 둬야 하므로 아래
        #   `_advance_incremental`(다음 사진을 같은 위젯에 넣는다)보다 **먼저**다.
        moved = self._swipe_out_decided(action)
        # 핫 경로 — 전체 재생성 대신 영향 슬롯만 증분 갱신(#렉).
        self._advance_incremental(item.slot)
        # 앞 사진이 다 빠진 **뒤** 다음 사진이 들어온다(총 300ms 한 동작).
        # ★ 연타로 스와이프를 생략했으면 페이드인도 생략한다 — 짝이 맞아야
        #   '떠나고 들어온다' 가 하나의 동작으로 읽히고, 비용도 함께 사라진다.
        if moved:
            self._fade_in_next()

    #: 스와이프 이동 거리(px) — 시안값.  화면 밖까지 보내지 않는다: 카드 안에서
    #: 벗어나는 정도면 방향이 읽히고, 긴 이동은 다음 사진을 기다리게 만든다.
    _SWIPE_PX = 64

    def _swipe_out_decided(self, action: str) -> bool:
        """방금 결정한 사진이 그 결정이 향한 쪽으로 밀려나며 사라진다 (26안).

        ★ 방향은 화면이 이미 정해 둔 것을 그대로 따른다: 오른쪽 패널이 '검증',
        왼쪽이 '제외' 이고 단축키도 →/← 다.  그래서 이 모션은 새 규칙을 만들지
        않고 **이미 있는 공간 규칙을 몸이 기억하게** 한다.
        ★ **떠나는 그림의 사본에만** 건다(시안 명시).  바로 뒤에서
        `_advance_incremental` 이 같은 위젯에 다음 사진을 넣으므로 살아 있는
        `center_img` 에 걸면 새 사진이 밀려나고, 그 위젯에 그래픽스 이펙트를
        얹으면 사진이 다시 그려질 때마다 오프스크린 렌더가 따라붙는다.
        ★ **연타 중에는 생략한다**(시안 명시).  이게 없으면 결정마다 애니가 쌓여
        수백 장을 넘길 때 그냥 버벅이는 것으로만 남는다 — 실측(900px 사진 39회
        연타, 결정당 평균): 모션 OFF 28.3ms · 가드 없음 33.7ms(최악 78.7) ·
        가드 있음 28.2ms(최악 49.8).  즉 가드가 있으면 **공짜**다.
        ★ 이동은 `pos`, 사라짐은 불투명도다.  고스트는 레이아웃이 자리를 정하지
        않는 뷰포트 위 절대배치라 `move` 가 되돌려지지 않는다(리플로 0).
        """
        from .. import motion
        if not motion.enabled():
            return False
        prev = getattr(self, "_swipe_ghost", None)
        if prev is not None:
            # 연타 — 앞 잔상을 **즉시** 치우고 새 애니는 걸지 않는다.  남겨 두면
            # 지지난 사진이 지금 사진 위에 떠 있게 된다(화면이 거짓말을 한다).
            self._drop_swipe_ghost()
            return False
        viewport = self._img_scroll.viewport()
        img = self.center_img
        pm = img.pixmap()
        if pm is None or pm.isNull():
            return False
        try:
            shot = img.grab()
        except RuntimeError:
            return False
        if shot.isNull():
            return False
        ghost = QLabel(viewport)
        ghost.setPixmap(shot)
        ghost.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        start = img.mapTo(viewport, QPoint(0, 0))
        ghost.setGeometry(QRect(start, img.size()))
        ghost.show()
        ghost.raise_()
        self._swipe_ghost = ghost

        eff = QGraphicsOpacityEffect(ghost)
        eff.setOpacity(1.0)
        ghost.setGraphicsEffect(eff)
        fade = QPropertyAnimation(eff, b"opacity", ghost)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setDuration(motion.DUR_SWIPE_OUT)
        fade.setEasingCurve(motion.EASE_PRIMARY)

        dx = self._SWIPE_PX if action == "verify" else -self._SWIPE_PX
        move = QPropertyAnimation(ghost, b"pos", ghost)
        move.setStartValue(start)
        move.setEndValue(QPoint(start.x() + dx, start.y()))
        move.setDuration(motion.DUR_SWIPE_OUT)
        move.setEasingCurve(motion.EASE_PRIMARY)
        # ★ 정리는 **한쪽에서만** 건다 — 두 애니에 각각 걸면 두 번 불린다.
        move.finished.connect(self._drop_swipe_ghost)
        fade.start()
        move.start()
        return True

    def _fade_in_next(self) -> None:
        """다음 사진이 제자리에서 떠오른다 — 스와이프가 끝난 뒤 120ms (26안).

        ★ 지연이 곧 순서다.  같은 틱에 겹쳐 재생하면 떠나는 사진과 들어오는
        사진이 동시에 반투명해져 한순간 화면이 비어 보인다 — 시안이 CSS 로
        `.12s linear .18s backwards` 라 적은 것이 이 순서다."""
        from .. import motion
        if self._current is None:
            return
        motion.fade_in(self.center_img, delay_ms=motion.DUR_SWIPE_OUT)

    def _drop_swipe_ghost(self) -> None:
        """잔상을 치운다 — 애니가 끝났을 때와 연타로 잘렸을 때 모두 여기로 온다."""
        ghost = getattr(self, "_swipe_ghost", None)
        self._swipe_ghost = None
        if ghost is None:
            return
        try:
            ghost.hide()
            ghost.deleteLater()
        except RuntimeError:
            pass                       # 이미 사라졌다

    def _undo(self) -> None:
        # Z 가 MatchPage 가 보일 때도 SelectPage 로 전달되는 것을 차단.
        if not self.isVisible():
            return
        if self._state is None or not self._state.history:
            return
        action, item = self._state.history.pop()
        pool = self._state.targets if action == "verify" else self._state.excluded
        try:
            pool[item.slot].remove(item)
        except ValueError:
            pass
        self._state.queue.insert(0, item)
        self.state_changed.emit()
        self._advance_to_next()

    # ------------------------------------------------------------------
    # Batch actions from panels
    # ------------------------------------------------------------------
    def _on_batch_action(self, panel: str, action_id: str,
                          items: list[ImageItem]) -> None:
        if self._state is None:
            return
        if panel == self.PANEL_RIGHT:
            for it in items:
                if it in self._state.targets[it.slot]:
                    self._state.targets[it.slot].remove(it)
                if action_id == "to_exclude":
                    self._state.excluded[it.slot].append(it)
                elif action_id == "recenter":
                    self._state.queue.insert(0, it)
                # remove → nothing additional
        elif panel == self.PANEL_BOTTOM:
            for it in items:
                if it in self._state.excluded[it.slot]:
                    self._state.excluded[it.slot].remove(it)
                if action_id == "to_target":
                    self._state.targets[it.slot].append(it)
                elif action_id == "recenter":
                    self._state.queue.insert(0, it)
        elif panel == self.PANEL_LEFT:
            # ★ 큐에서 곧바로 내리는 이 두 액션은 가운데에서 한 장씩 내리는 결정과
            #   **같은 일**이다 — 그러므로 `_decide` 와 똑같이 되돌리기 기록을 남긴다.
            #   남기지 않으면 `Z` 가 이 일괄 처리를 건너뛰고 **그 이전의 한 장짜리
            #   결정**을 대신 취소한다: 사용자는 방금 보낸 3장이 돌아올 줄 알았는데
            #   손대지 않은 다른 사진이 조용히 큐로 되돌아온다(실측 확인).
            #   기록 단위는 `_end_selection_now` 와 같은 **장당 1건**이다 — `Z` 는
            #   "직전 한 장" 이라는 화면·설명서의 약속을 그대로 지킨다.
            for it in items:
                if it in self._state.queue:
                    self._state.queue.remove(it)
                if action_id == "batch_verify":
                    self._state.targets[it.slot].append(it)
                    self._state.history.append(("verify", it))
                    self.decision_made.emit("verify", it)
                elif action_id == "batch_exclude":
                    self._state.excluded[it.slot].append(it)
                    self._state.history.append(("exclude", it))
                    self.decision_made.emit("exclude", it)
        self.state_changed.emit()
        self._advance_to_next()

    # ------------------------------------------------------------------
    # Zoom-view window
    # ------------------------------------------------------------------
    def _on_tile_click(self, panel: str, slot: str, _item: ImageItem) -> None:
        self._open_zoom(panel, slot)

    def _on_plus_click(self, panel: str, slot: str) -> None:
        self._open_zoom(panel, slot)

    def _open_zoom(self, panel: str, slot: str) -> None:
        if self._state is None:
            return
        view_only = False
        if panel == self.PANEL_RIGHT:
            items = list(self._state.targets.get(slot, []))
            source = SOURCE_TARGET
            already = self._phase_b_already_matched.get(slot, [])
        elif panel == self.PANEL_BOTTOM:
            items = list(self._state.excluded.get(slot, []))
            source = SOURCE_EXCLUDED
            already = []
        else:
            # Stage 1 의 검증 후보 — 단순 확대 뷰어로만 동작 (액션 없음).
            items = [it for it in self._state.queue if it.slot == slot]
            source = SOURCE_CANDIDATES
            already = []
            view_only = True
        if not items and not already:
            return
        win = ZoomWindow(slot, items, source,
                         already_matched_items=already,
                         view_only=view_only, parent=self)
        win.action_requested.connect(
            lambda act, sel: self._apply_zoom_action(panel, act, sel)
        )
        sheets.run(win, full_bleed=True)

    def _apply_zoom_action(self, panel: str, action: str,
                            items: list[ImageItem]) -> None:
        if self._state is None:
            return
        if panel == self.PANEL_RIGHT:
            for it in items:
                if it in self._state.targets[it.slot]:
                    self._state.targets[it.slot].remove(it)
                if action == "exclude":
                    self._state.excluded[it.slot].append(it)
                elif action == "recenter":
                    self._state.queue.insert(0, it)
        elif panel == self.PANEL_BOTTOM:
            for it in items:
                if it in self._state.excluded[it.slot]:
                    self._state.excluded[it.slot].remove(it)
                if action == "verify":
                    self._state.targets[it.slot].append(it)
                elif action == "recenter":
                    self._state.queue.insert(0, it)
        self.state_changed.emit()
        self._advance_to_next()

    # ------------------------------------------------------------------
    # 선택 종료 — 남은 미결정 사진을 모두 ‘검증 제외’ 로 처리하고 진행
    # ------------------------------------------------------------------
    def _end_selection_now(self) -> None:
        """[선택 종료] — 남은 큐를 모두 excluded 로 옮기고 Stage 2 로."""
        if self._state is None:
            return
        n_remaining = len(self._state.queue)
        if n_remaining == 0:
            return
        from PyQt6.QtWidgets import QMessageBox
        ret = sheets.ask(
            self, i18n.KO.END_SELECTION_CONFIRM_TITLE,
            i18n.KO.END_SELECTION_CONFIRM_FMT.format(n=n_remaining),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            # ★ 기본은 '아니오' 다 — 남은 수백 장을 일괄 제외하는 파괴 흐름이라,
            #   조회 버튼을 노리다 한 칸 옆을 누른 손이 Enter 습관으로 그대로
            #   통과해서는 안 된다.  SELECT_BACK·NEW_SESSION 확인과 같은 규약.
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        # 큐의 모든 항목을 슬롯별 excluded 에 추가 + history 기록.
        # 큐의 사본을 만든 뒤 비운다 (반복 중 mutate 방지).
        for it in list(self._state.queue):
            self._state.excluded[it.slot].append(it)
            self._state.history.append(("exclude", it))
            self.decision_made.emit("exclude", it)
        self._state.queue.clear()
        # 현재 결정 중인 사진은 _advance_to_next 에서 자연스럽게 None 으로
        # 떨어지면서 finished 시그널이 emit 된다.
        self._current = None
        self.state_changed.emit()
        self._advance_to_next()

    # ------------------------------------------------------------------
    # 검증 제외 사진 팝업 다이얼로그
    # ------------------------------------------------------------------
    def _open_excluded_dialog(self) -> None:
        """[검증 제외 사진 보기] 클릭 → 큰 팝업으로 표시 + 다중 액션."""
        from ..widgets.bulk_select_dialog import BulkSelectDialog
        if self._state is None:
            return
        data = {k: list(v) for k, v in self._state.excluded.items() if v}
        if not data:
            return
        dlg = BulkSelectDialog(
            title=i18n.KO.BULK_SELECT_EXCLUDED_TITLE,
            data=data,
            actions=[
                ("to_target", i18n.KO.BTN_MOVE_TO_TARGET, "primary"),
                ("recenter", i18n.KO.BTN_BACK_TO_CENTER, "ghost"),
            ],
            parent=self,
        )
        dlg.selection_action.connect(
            lambda action_id, items: self._on_batch_action(
                self.PANEL_BOTTOM, action_id, items,
            )
        )
        sheets.run(dlg, full_bleed=True)
