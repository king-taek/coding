"""'일부 슬롯만 진행' 선택 다이얼로그 — 검색 + 밀집 그리드 다중 선택.

기준 폴더에서 찾은 슬롯을 **작은 토글 타일 그리드**로 보여주고, 이번 검증에서 진행할
슬롯만 고른다.  결과는 :attr:`selected`(슬롯명 집합).

실제 작업 장면이 설계를 결정했다: 24개 슬롯 중 **급한 3개만** 먼저 돌리려는 사용자다.
그래서 (a) 이름을 알고 있으니 **검색**이 가장 빠른 길이고, (b) 한 화면에 최대한 많이
보여야 한다.

(c) 타일은 **마우스로만** 고른다 — 사용자 결정.  방향키 이동·Space 토글·Ctrl+A/D 를
모두 없앴고, 남은 키보드는 검색란 타이핑·Enter(확인)·Esc(닫기)뿐이다.  구현은
``_SlotTile`` 의 ``NoFocus`` 하나로 끝난다(받지 않게 하는 쪽이 가로채는 쪽보다
우회로가 없다).

이전 구현에서 실제로 있었던 결함 — 이 파일이 왜 다시 쓰였는지:

1. **죽은 네온 팔레트 하드코딩** — 선택 틴트가 ``rgba(57, 255, 20, 0.10)`` 였다.  지금
   어느 팔레트에도 없는 색이라 다크에서 배경만 초록으로 떴고, 라벨 대비는 측정된 적이
   없었다.  ``option_group`` 모듈 docstring 이 **이 타일을 이름까지 대며** "반복하지 말
   것"으로 적어 뒀던 그 실수다.  이제 선택 상태는 QSS(`role="slotTile"`)가 칠한다.
2. **포커스를 그리지 않았다** — ``StrongFocus`` 를 켜 두고 링을 그리지 않아, 키보드로
   이동해도 지금 어디인지 알 수 없었다(WCAG 2.4.7 실패).  이후 링을 그려 고쳤고,
   지금은 타일 키보드 자체가 없어져(``NoFocus``) 이 상태가 존재하지 않는다.
3. **press 에서 토글** — 터치 패널에서 타일에 손가락을 얹고 미는 **스크롤 제스처**가
   선택을 바꿨다.  ``ToggleSwitch`` 에서 이미 release 기준으로 고친 것과 같은 버그다.
   지금은 ``QPushButton`` 이라 release-inside 만 ``clicked`` 를 낸다(공짜로 해결).
4. **열 계산 off-by-one** — ``vp_w // (_TILE_W + spacing)`` 이 마지막 열에도 간격을
   물려 열을 하나 덜 줬다.  ``option_group.reflow_into_grid`` 가 이미 고쳐 놓은 버그를
   복제하고 있었다 — 이제 그 함수를 **재사용**한다.
5. **0개 선택으로 확인 가능** — 호출부는 빈 집합을 '전체 진행'으로 정규화한다
   (``setup_page._open_slot_select``).  즉 전부 해제하고 확인을 누르면 **의도와 정반대로
   40슬롯이 통째로 돌아간다.**  이제 [확인]을 막고 이유를 옆에 적는다.
6. 검색·키보드 이동 없음, 짧은 라벨 하나에 150×64 고정 타일.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from PyQt6.QtCore import QObject, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (QApplication, QDialog, QGridLayout, QHBoxLayout,
                             QLabel, QLineEdit, QScrollArea, QSizePolicy,
                             QVBoxLayout, QWidget)

from ... import i18n
from .. import theme
from .neon_button import NeonButton
from .option_group import reflow_into_grid

# 타일 최소 폭 — 실제 폭은 열 수 계산이 늘려 준다.  슬롯명은 'PI_Opening_A1' 처럼
# 길 수 있어 가운데 생략(…)으로 줄이고 전체 이름은 툴팁에 둔다.
_TILE_MIN_W = 168
_GRID_SPACING = 8

# 개수를 세는 워커가 도는 동안 파이썬 참조를 붙잡아 두는 곳 — 지역 변수로만 두면
# 함수가 끝나는 순간 GC 가 QThread 를 파괴한다(`pages/setup_page._LIVE_DIE_SCANS`
# 와 같은 패턴이고 같은 이유다: 실행 중인 QThread 가 파괴되면 프로세스가 죽는다).
_LIVE_COUNT_SCANS: set = set()


class _SlotCountScan(QThread):
    """슬롯마다 기준·검증 폴더의 사진 수를 **워커 스레드에서** 센다.

    폴더 열거는 NAS 에서 왕복이라 슬롯 25개면 50회다 — UI 스레드에서 하면 팝업이
    뜨는 순간 그만큼 멈춘다(직전에 고친 '폴더 선택 시 멈춤' 과 같은 종류).  그래서
    팝업을 **먼저 띄우고** 개수는 세어지는 대로 한 슬롯씩 올려 보낸다.

    ``token`` 은 늦게 도착한 옛 결과를 버리기 위한 세대 번호다."""

    class _Signals(QObject):
        counted = pyqtSignal(int, str, int, int)     # token, slot, ref, val
        # ★ `val` 이 음수면 '검증 폴더에 그 슬롯이 없다'는 뜻이다(0 장과 구분된다).

    def __init__(self, token: int, ref_dirs: dict, val_root) -> None:
        super().__init__()                  # 부모 없음(위 주석)
        self._token = token
        self._ref_dirs = dict(ref_dirs)
        self._val_root = Path(val_root) if val_root else None
        self.signals = self._Signals()

    def run(self) -> None:      # type: ignore[override]
        from ...models.slot import count_images

        for name, folder in sorted(self._ref_dirs.items()):
            if self.isInterruptionRequested():
                return
            try:
                ref_n = count_images(folder)
            except Exception:
                ref_n = 0
            val_n = -1
            if self._val_root is not None:
                try:
                    vd = self._val_root / name
                    val_n = count_images(vd) if vd.is_dir() else -1
                except Exception:
                    val_n = -1
            self.signals.counted.emit(self._token, name, ref_n, val_n)


class _SlotTile(NeonButton):
    """토글 가능한 슬롯 타일.  **선택 상태는 QSS 가 칠한다**(인라인 스타일시트 금지).

    ``QPushButton`` 을 쓰는 이유: (a) ``clicked`` 가 **release-inside** 에서만 나므로
    터치 스크롤이 선택을 바꾸지 않는다, (b) ``:checked``/``:hover``/``:disabled`` 를
    QSS 가 전부 칠해 준다.

    ★ 포커스는 ``NoFocus`` 다 — **이 창의 타일은 마우스로만 고른다**(사용자 결정).
    그래서 방향키 이동·Space 토글·포커스 링이 전부 사라진다.  Space 토글은
    ``QAbstractButton`` 의 기본 동작인데, 포커스를 받지 않으면 발생 자체가
    불가능하므로 따로 막을 코드가 필요 없다.
    """

    def __init__(self, name: str, *, selected: bool = False, parent=None) -> None:
        super().__init__("", role="slotTile", parent=parent)
        self.name = name
        self.setCheckable(True)
        self.setChecked(bool(selected))
        # ★ autoExclusive 를 끈다 — 부모를 공유하는 checkable 버튼은 Qt 가 라디오처럼
        #   묶어 **하나만** 켜지게 만든다.  다중 선택 위젯에서는 치명적이다.
        self.setAutoExclusive(False)
        self.setObjectName(f"slot_{name}")
        self.setAccessibleName(name)
        self.setToolTip(name)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # ── 둘째 줄: 그 슬롯의 사진 수 ──────────────────────────────────
        # ★ 버튼의 **텍스트에 합치지 않는다.**  `text()` 는 '가운데 생략된 슬롯명
        #   그 자체' 라는 계약이 있고(`_elide`, `test_long_name_stays_readable` 이
        #   첫 글자·끝 글자를 원본과 비교한다), 두 줄을 한 문자열로 만들면 그 계약이
        #   깨진다.  게다가 한 버튼의 텍스트에는 서체를 두 가지로 줄 수 없다.
        #   자식 라벨로 두면 QSS 가 작고 흐린 글씨를 따로 칠한다.
        self._count = QLabel(i18n.KO.SLOT_SELECT_TILE_COUNT_PENDING, self)
        self._count.setProperty("role", "slotTileCount")
        self._count.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay = QVBoxLayout(self)
        # 위쪽은 버튼 자기 텍스트(슬롯명)의 자리다 — 비워 두고 라벨만 아래에 앉힌다.
        lay.setContentsMargins(10, 0, 10, 5)
        lay.setSpacing(0)
        lay.addStretch(1)
        lay.addWidget(self._count)
        self._elide()

    def set_count_text(self, text: str) -> None:
        """둘째 줄 문구 교체 — 값이 바뀔 때만 적는다(리페인트를 아낀다)."""
        if self._count.text() != text:
            self._count.setText(text)

    def count_text(self) -> str:
        return self._count.text()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        """긴 슬롯명은 **가운데** 생략 — 접두(레시피)와 접미(다이 좌표)를 둘 다 남긴다.

        앞/뒤 생략을 쓰면 'PI_Opening_A1' 과 'PI_Opening_A2' 를 구분할 수 없다."""
        fm = QFontMetrics(self.font())
        avail = max(40, self.width() - 28)
        self.setText(fm.elidedText(self.name, Qt.TextElideMode.ElideMiddle, avail))


class SlotSelectDialog(QDialog):
    """검색 + 밀집 그리드로 진행할 슬롯을 고른다."""

    selection_changed = pyqtSignal(int)          # 선택 개수 (테스트·확장용)

    def __init__(self,
                 slot_names: Iterable[str],
                 *,
                 preselected: Optional[Iterable[str]] = None,
                 ref_dirs: Optional[dict] = None,
                 val_root=None,
                 parent=None) -> None:
        """``ref_dirs``·``val_root`` 를 주면 슬롯마다 사진 수를 **워커가** 채운다.

        주지 않으면 개수 줄은 '확인 중…' 그대로 남는다 — 개수는 곁들이는 정보이지
        이 창이 하는 일(슬롯 고르기)의 전제가 아니다."""
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.SLOT_SELECT_TITLE)
        self._slot_names = sorted(set(slot_names))
        self._preselected = (set(preselected) if preselected is not None
                             else set(self._slot_names))
        self._accepted = False
        self._tiles: dict[str, _SlotTile] = {}
        self._visible: list[str] = list(self._slot_names)
        # ★ 창 제어(최소화/최대화/F11) 헬퍼를 부르지 않는다 — 이 다이얼로그는 별도 OS
        #   창이 아니라 **메인 창 안의 시트**로 뜬다(widgets/sheet_host.py).  자식 위젯에는
        #   타이틀바가 없어 힌트가 무의미하고, F11 은 '전체화면'을 약속하면서 실제로는
        #   아무 변화도 만들지 못한다(실측: isFullScreen 만 True 가 되고 기하는 그대로).
        #   지키지 못할 약속을 하는 단축키는 없는 것이 낫다 — 전체화면은 메인 창의 F11.
        #
        #   ★ 크기는 **내용만큼**이다(아래 `_size_to_content`).  짧은 이름 24개를 고르는
        #   창이 27인치를 채우면 여백만 늘고 눈이 멀리 이동한다.  사진을 다루는 시트
        #   (일괄 선택·실패 검토)만 창 전체를 쓴다(`full_bleed=True`).
        self._counts: dict[str, tuple[int, int]] = {}
        self._count_token = 0
        self._count_scan: Optional[_SlotCountScan] = None
        self._build()
        self._size_to_content()
        if ref_dirs:
            self._start_counting(ref_dirs, val_root)

    # ── 슬롯별 사진 수 — 워커가 채운다 ────────────────────────────────
    def _start_counting(self, ref_dirs: dict, val_root) -> None:
        """개수 세기를 워커에 맡긴다 — 창은 이미 떠 있고 숫자만 나중에 들어온다."""
        self._count_token += 1
        scan = _SlotCountScan(self._count_token, ref_dirs, val_root)
        scan.signals.counted.connect(self._on_counted)
        _LIVE_COUNT_SCANS.add(scan)
        scan.finished.connect(lambda s=scan: _LIVE_COUNT_SCANS.discard(s))
        self._count_scan = scan
        scan.start()

    def _on_counted(self, token: int, slot: str, ref_n: int, val_n: int) -> None:
        """한 슬롯을 다 셌다 — **최신 세대만** 반영한다."""
        if token != self._count_token:
            return
        self._counts[slot] = (ref_n, val_n)
        self._apply_count(slot)

    def _apply_count(self, slot: str) -> None:
        """센 값 → 타일 둘째 줄.  순수 렌더링(파일을 읽지 않는다)."""
        tile = self._tiles.get(slot)
        if tile is None:
            return
        got = self._counts.get(slot)
        if got is None:
            tile.set_count_text(i18n.KO.SLOT_SELECT_TILE_COUNT_PENDING)
            return
        ref_n, val_n = got
        # 음수는 '검증 폴더에 그 슬롯이 없다' — 0 장과 구분해서 보여 준다.
        val_txt = (i18n.KO.SLOT_SELECT_TILE_COUNT_NONE if val_n < 0
                   else f"{val_n:,}")
        tile.set_count_text(i18n.KO.SLOT_SELECT_TILE_COUNT_FMT.format(
            ref=f"{ref_n:,}", val=val_txt))

    def set_counts(self, counts: dict) -> None:
        """개수를 직접 주입한다(테스트·미리 센 값이 있는 호출부용).

        값은 ``{슬롯명: (기준_장수, 검증_장수)}`` — 검증 장수가 음수면 '없음'."""
        for slot, pair in counts.items():
            self._counts[slot] = (int(pair[0]), int(pair[1]))
            self._apply_count(slot)

    def closeEvent(self, event):  # noqa: N802
        """창이 닫히면 세는 일도 멈춘다 — 답이 갈 곳이 없다.

        ★ 기다리지 않는다.  세대 번호가 늦게 온 결과를 버리고, 스레드는
        `_LIVE_COUNT_SCANS` 가 붙잡고 있으므로 자기 수명을 다 살고 조용히 사라진다
        (기다리면 그게 곧 멈춤이다 — 느린 NAS 에서 창이 안 닫힌다)."""
        self._count_token += 1
        scan = self._count_scan
        self._count_scan = None
        if scan is not None and scan.isRunning():
            scan.requestInterruption()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    @property
    def selected(self) -> set[str]:
        """선택된 슬롯명 집합.  **검색으로 숨은 타일도 포함**한다 — 필터는 보기이지
        선택이 아니다(숨었다고 선택이 풀리면 검색이 파괴적 동작이 된다)."""
        return {name for name, tile in self._tiles.items() if tile.isChecked()}

    @property
    def accepted_ok(self) -> bool:
        return self._accepted

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        m = theme.PROFILE.page_margin // 2
        root.setContentsMargins(m, m, m, m)
        root.setSpacing(10)

        # ── 머리: 지금 무엇이 진행되는지 **큰 글자로**.  이전엔 우측 상단의 작은
        #    '선택 3 / 24' 뿐이라, 확인을 누르는 순간 무슨 일이 일어나는지 흐렸다.
        self._head = QLabel("", self)
        self._head.setProperty("role", "title")
        root.addWidget(self._head)

        hint = QLabel(i18n.KO.SLOT_SELECT_HINT, self)
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # ── 검색 — 이름을 아는 사용자에게 가장 빠른 길.  열자마자 여기 포커스가 간다.
        self._search = QLineEdit(self)
        self._search.setPlaceholderText(i18n.KO.SLOT_SELECT_SEARCH_PLACEHOLDER)
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search)
        root.addWidget(self._search)

        # ── 일괄 동작 + 검색 결과 수.
        top = QHBoxLayout()
        top.setSpacing(8)
        self._btn_all = NeonButton(i18n.KO.SLOT_SELECT_ALL, role="ghost")
        self._btn_all.clicked.connect(lambda: self._set_all(True))
        self._btn_none = NeonButton(i18n.KO.SLOT_SELECT_NONE, role="ghost")
        self._btn_none.clicked.connect(lambda: self._set_all(False))
        # 검색 중일 때만 의미가 있는 동작 — 평소엔 숨긴다(빈 어포던스를 남기지 않는다).
        self._btn_visible = NeonButton(i18n.KO.SLOT_SELECT_PICK_VISIBLE, role="ghost")
        self._btn_visible.clicked.connect(self._select_visible)
        self._btn_visible.setVisible(False)
        self._filter_label = QLabel("", self)
        self._filter_label.setProperty("role", "muted")
        top.addWidget(self._btn_all)
        top.addWidget(self._btn_none)
        top.addWidget(self._btn_visible)
        top.addStretch(1)
        top.addWidget(self._filter_label)
        root.addLayout(top)

        # ── 타일 그리드(세로 스크롤만).
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 맨 QScrollArea/뷰포트는 전역 배경을 물려받아 면이 어긋난다 — 투명으로 못 박고
        # 자식에 새지 않게 objectName 으로 스코프한다(셋업 화면과 같은 함정).
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget#qt_scrollarea_viewport { background: transparent; }"
        )
        host = QWidget(self._scroll)
        host.setProperty("role", "rowHost")
        self._grid = QGridLayout(host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(_GRID_SPACING)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        for name in self._slot_names:
            tile = _SlotTile(name, selected=name in self._preselected, parent=host)
            tile.clicked.connect(self._on_tile_clicked)
            self._tiles[name] = tile
        self._scroll.setWidget(host)
        root.addWidget(self._scroll, stretch=1)

        # ── 빈 상태 — '검색 결과 없음' 을 그리드 자리에 말해 준다(빈 화면 금지).
        self._empty = QLabel("", self)
        self._empty.setProperty("role", "muted")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setVisible(False)
        root.addWidget(self._empty)

        # ── 차단 이유는 **자기 줄**에 둔다.  왜 확인할 수 없는지는 툴팁이 아니라 눈에
        #    보여야 하지만(셋업 액션바와 같은 규칙), 키 안내와 **같은 줄**에 두면 둘이 폭을
        #    다투다 키 안내가 'Ctrl·' 처럼 중간에서 잘려 뜻 없는 글자가 된다(캡처 실측).
        #    ★ 비어 있을 때도 자리를 남긴다 — 메시지가 뜰 때 바닥 버튼이 위로 튀지 않게.
        self._blocked = QLabel("", self)
        self._blocked.setProperty("role", "warn")
        self._blocked.setAlignment(Qt.AlignmentFlag.AlignRight
                                   | Qt.AlignmentFlag.AlignVCenter)
        self._blocked.setFixedHeight(self._blocked.sizeHint().height())
        root.addWidget(self._blocked)

        # ── 바닥: 취소·확인(우).
        # ★ 키 안내 문구는 없앴다 — 타일 키보드 이동을 없앴으므로 안내할 키가
        #   남지 않았다.  지키지 못할 약속을 화면에 적어 두지 않는다.
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addStretch(1)
        cancel = NeonButton(i18n.KO.BTN_CANCEL, role="ghost")
        cancel.setMinimumHeight(theme.PROFILE.control_h_lg)
        cancel.clicked.connect(self.reject)
        self._ok = NeonButton(i18n.KO.BTN_OK, role="primary")
        self._ok.setMinimumHeight(theme.PROFILE.control_h_lg)
        self._ok.clicked.connect(self._on_ok)
        bar.addWidget(cancel)
        bar.addWidget(self._ok)
        root.addLayout(bar)

        # ★ Ctrl+A / Ctrl+D 단축키는 없앴다(사용자 결정) — [전체]/[해제] 버튼이
        #   같은 일을 하고 화면에 보인다.  숨은 키는 남기지 않는다.
        # ★ Enter 를 QPushButton 의 autoDefault 에 맡기지 않는다 — 검색란에서 Enter 를
        #   누르면 '확인' 이 아니라 마지막으로 눌린 버튼이 다시 실행될 수 있다.
        self._ok.setDefault(True)
        for b in (cancel, self._btn_all, self._btn_none, self._btn_visible):
            b.setAutoDefault(False)

        self._refresh_counts()
        # 열자마자 검색란에 포커스 — 이름을 아는 사용자의 첫 동작이 타이핑이다.
        QTimer.singleShot(0, self._search.setFocus)
        QTimer.singleShot(0, self._relayout)

    # 4열이 들어가는 폭 + 바닥/머리 여백.  열 수는 창 폭에 따라 바뀌므로
    # '4열은 보장한다'는 하한만 정하고 나머지는 화면에 맡긴다.
    _WANT_COLS = 4
    # 화면 높이의 이만큼(상한 _WANT_H_MAX)을 쓴다.  슬롯 24~40개를 고르는 창이
    # 560px 이면 한 번에 대여섯 줄뿐이라 '리스트가 거의 안 보인다'는 말이 나온다.
    _WANT_H_RATIO = 0.78
    _WANT_H_MAX = 760

    def _size_to_content(self) -> None:
        """내용에 맞는 크기로 열되, **화면 가용 영역 안으로 클램프**한다.

        ★ 계산 결과를 ``sizeHint`` 로도 내보내는 것이 핵심이다.  이 창은 별도 OS
        창이 아니라 메인 창 안의 **시트**로 뜨는데(`widgets/sheet_host.py`),
        시트 호스트는 ``full_bleed`` 가 아닐 때 위젯의 ``resize()`` 를 **무시하고**
        ``sizeHint()``/``minimumSize``/하한(280×140)만 본다.  ``sizeHint`` 를
        오버라이드하지 않았던 탓에 여기서 계산한 크기가 통째로 버려지고 시트가
        380×340 근처로 쪼그라들어, 그리드가 두어 줄만 보였다.

        800×600 노트북에서 창이 화면을 넘으면 [확인]이 화면 밖으로 나가므로
        가용 영역 클램프는 그대로 유지한다."""
        want_w = (self._WANT_COLS * _TILE_MIN_W
                  + (self._WANT_COLS - 1) * _GRID_SPACING
                  + theme.PROFILE.page_margin + 24)      # 마진 + 스크롤바 여유
        want_h = self._WANT_H_MAX
        scr = self.screen() or QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            want_h = min(want_h, int(g.height() * self._WANT_H_RATIO))
            want_w = min(want_w, int(g.width() * 0.95))
            want_h = min(want_h, int(g.height() * 0.95))
        self._want_w = int(want_w)
        self._want_h = int(want_h)
        # ★ 최소값은 목표까지 올리지 **않는다**.  시트 호스트는
        #   `max(hint, minimumWidth, 하한)` 을 쓰므로 `sizeHint` 만으로 목표 크기가
        #   나온다.  최소값까지 목표로 올리면 좁은 화면에서 창을 줄일 수 없고
        #   열 수가 폭을 못 따라간다(가로 스크롤).  '2열 + 바닥 버튼' 선까지만.
        self.setMinimumSize(min(380, self._want_w), min(340, self._want_h))
        self.resize(self._want_w, self._want_h)

    def sizeHint(self) -> QSize:  # noqa: N802
        """시트 호스트가 **실제로 읽는** 크기.  `_size_to_content` 결과를 돌려준다."""
        return QSize(getattr(self, "_want_w", 640),
                     getattr(self, "_want_h", 560))

    # ── 검색(필터) ────────────────────────────────────────────────────
    def _on_search(self, text: str) -> None:
        q = (text or "").strip().lower()
        self._visible = [n for n in self._slot_names if q in n.lower()] if q \
            else list(self._slot_names)
        for name, tile in self._tiles.items():
            tile.setVisible(name in set(self._visible))
        if q:
            self._filter_label.setText(
                i18n.KO.SLOT_SELECT_FILTER_HIT_FMT.format(q=text, n=len(self._visible)))
            self._empty.setText(
                i18n.KO.SLOT_SELECT_FILTER_NONE_FMT.format(q=text))
        else:
            self._filter_label.setText("")
        # '검색 결과 선택' 은 검색 중이고 결과가 있을 때만 의미가 있다.
        self._btn_visible.setVisible(bool(q) and bool(self._visible))
        self._empty.setVisible(bool(q) and not self._visible)
        self._scroll.setVisible(bool(self._visible))
        self._relayout(force=True)

    def _select_visible(self) -> None:
        """검색 결과만 선택 — 나머지는 **해제한다**(‘이것만 돌린다’는 뜻)."""
        vis = set(self._visible)
        for name, tile in self._tiles.items():
            tile.setChecked(name in vis)
        self._refresh_counts()

    # ── 열 수 계산 — 가로 스크롤이 나지 않게 ───────────────────────────
    def _relayout(self, *, force: bool = False) -> None:
        """보이는 타일만 재배치.  ★ 열 수는 ``reflow_into_grid`` 가 계산한다.

        이전 구현은 ``vp_w // (_TILE_W + spacing)`` 으로 직접 셌는데, 마지막 열에도
        간격을 물려 열을 하나 덜 줬다 — ``reflow_into_grid`` 가 이미 고친 off-by-one 을
        복제한 셈이다.  같은 계산을 두 곳에 두지 않는다."""
        vis = [self._tiles[n] for n in self._visible]
        avail = self._scroll.viewport().width() or self.width()
        # 필터로 위젯 집합이 바뀌면 열 수가 같아도 재조립이 필요하다(force).
        if force:
            while self._grid.count():
                self._grid.takeAt(0)
        reflow_into_grid(self._grid, vis, avail, _TILE_MIN_W)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        QTimer.singleShot(0, self._relayout)

    # ★ 타일 키보드 조작(방향키 이동·Space 토글·검색란 ↓ 로 그리드 진입)은 전부
    #   제거했다 — **사용자 결정**.  타일은 마우스로만 고른다.  남아 있는 키보드는
    #   검색란 타이핑 · Enter(확인) · Esc(닫기) 뿐이다.
    #   구현은 `_SlotTile` 의 ``NoFocus`` 하나로 끝난다: 포커스를 못 받으면
    #   방향키도 Space 도 애초에 이 위젯으로 오지 않는다.  가로채는 코드를
    #   남겨 두는 것보다 **받지 않게 하는 쪽**이 우회로가 없다.

    def _grid_cols(self) -> int:
        """현재 열 수 — 레이아웃 검증(가로 스크롤 없음)용."""
        cols = 0
        for i in range(self._grid.count()):
            _r, c, _rs, _cs = self._grid.getItemPosition(i)
            cols = max(cols, c + 1)
        return cols

    # ── 선택 상태 ─────────────────────────────────────────────────────
    def _on_tile_clicked(self) -> None:
        self._refresh_counts()

    def _set_all(self, checked: bool) -> None:
        for tile in self._tiles.values():
            tile.setChecked(bool(checked))
        self._refresh_counts()

    def _refresh_counts(self) -> None:
        n = len(self.selected)
        total = len(self._slot_names)
        self._head.setText(
            i18n.KO.SLOT_SELECT_COUNT_HEAD_ALL.format(total=total) if n == total
            else i18n.KO.SLOT_SELECT_COUNT_HEAD_FMT.format(n=n, total=total))
        # ★ 0개는 막는다.  호출부(`setup_page._open_slot_select`)가 빈 집합을 '전체
        #   진행'으로 정규화하므로, 전부 해제하고 확인하면 **의도와 정반대로** 모든
        #   슬롯이 돌아간다.  버튼을 잠그고 이유를 옆에 적는다.
        self._ok.setEnabled(n > 0)
        self._blocked.setText("" if n > 0 else i18n.KO.SLOT_SELECT_NEED_ONE)
        self.selection_changed.emit(n)

    def _on_ok(self) -> None:
        if not self.selected:          # 단축키·Enter 로도 새지 않게 이중 가드
            return
        self._accepted = True
        self.accept()
