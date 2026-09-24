"""Wafer map 시트 — 두 입구, 한 화면.

* **셋업 단계**(``WaferMapDialog(parent)``): 폴더를 고른다.  **슬롯 폴더**(사진이 바로
  든 폴더)면 그 웨이퍼 하나, **LOT 폴더**(슬롯 폴더들이 든 폴더)면 전체 슬롯을 한 맵에
  합산하고 ‘슬롯 선택…’ 으로 일부만 본다(진행 범위의 슬롯 선택 팝업과 같은 창).
  **사진 1장을 창에 끌어다 놓으면** 그 결함만 볼 수 있다 — 놓은 사진의 폴더에서
  웨이퍼 기하를 읽으므로 원·격자는 그대로다.
  ‘Map 저장’ 은 지금 맵을 txt 로 쓰고(:mod:`coords.wafer_map_txt`), ‘Map 합치기’ 나
  **txt 끌어놓기**로 여러 맵을 한 맵에 합친다(합친 맵에 더 놓으면 계속 더해진다).
  매칭 전이라 점은 한 색(결함)이다.
* **결과 단계**(``WaferMapDialog(parent, result=...)``): 슬롯을 고르면 기준/검증 맵을
  나란히, '전체' 를 고르면 LOT 의 모든 슬롯을 한 맵에 합산한다.  점은 매치됨/미매치.

맵 만들기는 **폴더 판정·사진 목록부터** 워커 스레드(:class:`_MapBuild`)가 한다 — NAS
에서는 폴더 열거만으로도 초 단위라, 폴더를 고른 **그 순간** 오버레이가 떠서 지금 무슨
일을 하는지("폴더 탐색 중" → "좌표 읽는 중" → "사진 미리보기 준비 중") 말한다.
진행은 시그널로 :class:`LoadingOverlay` 에 전달한다(CLAUDE.md 로딩 계약).
썸네일 선로딩은 사진이 :data:`PREWARM_MAX` 장 이하일 때만 한다 — 그 이상이면
선로딩이 맵보다 오래 걸려 기다림이 목적을 잡아먹는다(그때는 마우스를 올릴 때 만든다).

점을 **더블클릭**하면 :class:`ImageInfoDialog` 로 그 사진의 상세 수치를 본다(같은
생산자 — 엑셀과 수치가 어긋나지 않는다).

보기 옵션은 **화면 전용**이다 — 엑셀에 들어가는 맵 그림은 기본값(노치 아래·점 표시)
으로 고정이라 결과 파일이 볼 때마다 달라지지 않는다(사용자 결정).

* ‘die 색칠’ — **셋업 단계에서만** 단다(사용자 결정).  점 대신 결함이 든 die 칸을
  **결함 점과 같은 색**으로 칠한다.  결과 단계는 매치됨/미매치를 점 색으로 읽는
  화면이라 칠하기를 두지 않는다.
* ‘노치: …’ — 두 단계 모두.  누를 때마다 90°씩 돌려 노치 방향을 맞춘다.  결과 단계
  에서는 기준·검증 두 맵에 **함께** 걸린다(같은 눈으로 봐야 비교가 된다).
* ‘전체화면’ — 두 단계 모두.  주변 표시(설명·버튼줄·제목·범례·시트 제목줄)를 감춰
  맵만 남기고, **메인 창**도 전체화면으로 바꾼다.  맵이 둘이면 둘 다 나란히 커진다
  (비교가 목적이므로 — 사용자 결정).  나가기는 ESC 와 떠 있는 버튼.
  ⚠ 시트 자신에게 ``showFullScreen()`` 을 걸어 봐야 소용없다 — 자식 위젯에는 통하지
  않는다(``widgets/window_controls`` 의 실측 기록).  그래서 '맵만 남기기'(여기)와
  '창 키우기'(메인 창)를 나눠서 한다.  내가 바꾼 창만 되돌린다 — 사용자가 이미 F11
  로 전체화면을 쓰고 있었다면 나갈 때 그 상태를 빼앗지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog, QFrame,
                             QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget)

from ... import i18n
from ...config import CONFIG
from ...coords import resolve_batch, wafer_map_txt
from ...coords.wafer_map import (ALL_SLOTS_KEY, MapData, SOURCE_ASSUMED,
                                 build_map, slot_maps)
from ...models.result import FinalResult
from ...models.slot import _list_images, list_slot_dirs
from ...utils import image_io
from .. import theme
from . import sheet_host as sheets
from .loading_overlay import LoadingOverlay
from .neon_button import NeonButton
from .wafer_map_view import WaferMapView
from .window_controls import set_window_fullscreen

# 페이지가 닫혀도 돌던 스레드가 수명을 다 살게 붙들어 둔다(setup_page 의 패턴).
_LIVE_BUILDS: set = set()
# 썸네일 선로딩 상한(장).  넘으면 선로딩을 건너뛴다 — 사용자 결정.
PREWARM_MAX = 500


def _coord_progress(report):
    """``resolve_batch`` 의 ``progress(done, total)`` → 오버레이 결정형 보고."""
    return lambda done, total: report(i18n.KO.WAFER_MAP_LOADING_COORDS, done, total)


class _MapBuild(QThread):
    """좌표 → MapData 계산 + 썸네일 선로딩.  ``token`` 으로 늦은 결과를 버린다."""

    class _Signals(QObject):
        progress = pyqtSignal(int, int, int, str)      # token, done, total, msg
        done = pyqtSignal(int, object, object, object)  # token, left, right|None, extra

    def __init__(self, token: int, job) -> None:
        """``job(report)`` 는 ``(left: MapData, right: MapData | None, extra)`` 를
        돌려준다(폴더 훑기 + 좌표 계산).  ``report(msg, done=0, total=0)`` 로 단계·진행을
        알린다(``total>0`` 이면 결정형).  썸네일은
        그 결과의 점 전부에 대해 여기서 만든다(:data:`PREWARM_MAX` 이하일 때)."""
        super().__init__()
        self._token = token
        self._job = job
        self.signals = self._Signals()
        _LIVE_BUILDS.add(self)
        self.finished.connect(lambda: _LIVE_BUILDS.discard(self))

    def run(self) -> None:      # type: ignore[override]
        t = self._token
        def report(msg, done=0, total=0):
            self.signals.progress.emit(t, done, total, msg)
        extra = None
        try:
            left, right, extra = self._job(report)
            paths = [p.path for p in left.points]
            if right is not None:
                paths += [p.path for p in right.points]
            total = len(paths)
            if total > PREWARM_MAX:
                paths = []
            for i, path in enumerate(paths, start=1):
                if self.isInterruptionRequested():
                    return
                try:
                    image_io.get_map_thumb_path(path)
                except Exception:
                    pass
                if i % 20 == 0 or i == total:
                    self.signals.progress.emit(
                        t, i, total, i18n.KO.WAFER_MAP_LOADING_THUMBS)
        except Exception:
            left, right = MapData(None, (), ()), None
        self.signals.done.emit(t, left, right, extra)


class _MapPanel(QWidget):
    """제목 + 맵 + 범례/카운트 한 벌."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.title = QLabel("", self)
        self.title.setProperty("role", "colHead")
        lay.addWidget(self.title)
        self.view = WaferMapView(self)
        self.view.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Expanding)
        lay.addWidget(self.view, stretch=1)
        self.legend = QLabel("", self)
        self.legend.setProperty("role", "mono")
        self.legend.setWordWrap(True)
        lay.addWidget(self.legend)

    def set_chrome_visible(self, visible: bool) -> None:
        """제목·범례를 감춘다/되돌린다 — 맵 전체화면에서 맵만 남기려고."""
        self.title.setVisible(visible)
        self.legend.setVisible(visible)

    def show_map(self, title: str, data: Optional[MapData]) -> None:
        self.title.setText(title)
        self.view.set_data(data)
        self.legend.setText(self.legend_text(data))

    @staticmethod
    def legend_text(data: Optional[MapData]) -> str:
        if data is None:
            return ""
        if data.frame is None:
            return i18n.KO.WAFER_MAP_NO_FRAME if data.unplaced else ""
        parts = []
        pts = data.points
        if any(p.matched is None for p in pts):
            parts.append(_chip(theme.ACCENT, i18n.KO.WAFER_MAP_LEGEND_DEFECT,
                               sum(p.matched is None for p in pts)))
        if any(p.matched is not None for p in pts):
            parts.append(_chip(theme.PASS, i18n.KO.WAFER_MAP_LEGEND_MATCHED,
                               sum(p.matched is True for p in pts)))
            parts.append(_chip(theme.DANGER, i18n.KO.WAFER_MAP_LEGEND_UNMATCHED,
                               sum(p.matched is False for p in pts)))
        if data.unplaced:
            parts.append(i18n.KO.WAFER_MAP_UNPLACED_FMT.format(n=len(data.unplaced)))
        if data.frame.center_source == SOURCE_ASSUMED:
            parts.append(i18n.KO.WAFER_MAP_CENTER_ASSUMED)
        if data.frame.pitch_assumed:
            parts.append(i18n.KO.WAFER_MAP_PITCH_ASSUMED)
        if data.frame.pitch_x and data.frame.pitch_y and not data.frame.die_cells:
            parts.append(i18n.KO.WAFER_MAP_GRID_COMPUTED)
        return " &nbsp;·&nbsp; ".join(parts)


def _chip(color: str, label: str, n: int) -> str:
    return (f"<span style='color:{color}'>●</span> {label} "
            f"{i18n.KO.WAFER_MAP_COUNT_FMT.format(n=n)}")


def classify_folder(folder: Path) -> tuple[str, dict[str, Path]]:
    """``("slot", {})`` — 사진이 바로 든 폴더, ``("lot", {슬롯명: 경로})`` — 슬롯
    폴더들이 든 폴더, ``("empty", {})`` — 둘 다 아님.  순수 — 헤드리스."""
    if _list_images(folder):
        return "slot", {}
    slots = {n: d for n, d in list_slot_dirs(folder).items() if _list_images(d)}
    return ("lot", slots) if slots else ("empty", {})


class WaferMapDialog(QDialog):
    def __init__(self, parent=None, *, result: Optional[FinalResult] = None,
                 folder: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.WAFER_MAP_TITLE)
        self._result = result
        self._folder: Optional[Path] = None
        self._single: Optional[Path] = None           # 사진 1장만 볼 때
        self._lot_slots: dict[str, Path] = {}         # LOT 폴더일 때만
        self._selected: Optional[set[str]] = None     # None = 전체
        self._rot = 0                                 # 노치 방향(90° 단위)
        self._fullscreen = False
        self._win_state: dict = {}                    # 창을 되돌릴 때 쓸 원래 상태
        self._win_changed = False                     # 창을 **내가** 바꿨나
        self._token = 0
        self._build_thread: Optional[_MapBuild] = None
        self._current: Optional[MapData] = None       # 셋업 단계에서 지금 보이는 맵
        self._merged: list[Path] = []                 # 합친 맵이면 그 txt 들
        self._build()
        self._loading = LoadingOverlay(self)
        if result is not None:
            self._on_slot_changed()
        elif folder:
            self.show_folder(Path(folder))
        else:
            self._render_empty()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(theme.PROFILE.section_gap // 2)

        self.sub = QLabel(i18n.KO.WAFER_MAP_SUBTITLE, self)
        self.sub.setProperty("role", "subtitle")
        self.sub.setWordWrap(True)
        root.addWidget(self.sub)

        # ★ 상단줄을 **위젯으로 싼다** — 전체화면에서 줄 하나를 통째로 감추려면
        #   레이아웃이 아니라 위젯이어야 한다(QHBoxLayout 에는 setVisible 이 없다).
        self._top_host = QWidget(self)
        top = QHBoxLayout(self._top_host)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(12)
        if self._result is not None:
            cap = QLabel(i18n.KO.WAFER_MAP_SLOT_LABEL, self)
            cap.setProperty("role", "colHead")
            top.addWidget(cap)
            self.slot_combo = QComboBox(self)
            self.slot_combo.addItem(i18n.KO.WAFER_MAP_ALL_SLOTS, ALL_SLOTS_KEY)
            for name in sorted(self._result.slot_images):
                self.slot_combo.addItem(name, name)
            self.slot_combo.setMinimumWidth(220)
            self.slot_combo.currentIndexChanged.connect(self._on_slot_changed)
            top.addWidget(self.slot_combo)
        else:
            self.pick_btn = NeonButton(i18n.KO.WAFER_MAP_PICK_FOLDER, role="primary")
            self.pick_btn.setMinimumHeight(theme.PROFILE.control_h_lg)
            self.pick_btn.clicked.connect(self._on_pick)
            self.pick_btn.setAutoDefault(False)
            top.addWidget(self.pick_btn)
            # LOT 폴더일 때만 보인다 — '일부 슬롯만 진행' 과 같은 팝업.
            self.slots_btn = NeonButton(i18n.KO.WAFER_MAP_PICK_SLOTS, role="ghost")
            self.slots_btn.clicked.connect(self._on_pick_slots)
            self.slots_btn.setAutoDefault(False)
            self.slots_btn.hide()
            top.addWidget(self.slots_btn)
            # 사진 1장은 버튼이 아니라 **끌어놓기**다(사용자 요청) — dropEvent.
            self.export_btn = NeonButton(i18n.KO.WAFER_MAP_EXPORT, role="ghost")
            self.export_btn.clicked.connect(self._on_export)
            self.export_btn.setAutoDefault(False)
            self.export_btn.setEnabled(False)
            top.addWidget(self.export_btn)
            self.merge_btn = NeonButton(i18n.KO.WAFER_MAP_MERGE, role="ghost")
            self.merge_btn.setToolTip(i18n.KO.WAFER_MAP_MERGE_TIP)
            self.merge_btn.clicked.connect(self._on_merge)
            self.merge_btn.setAutoDefault(False)
            top.addWidget(self.merge_btn)
            self.setAcceptDrops(True)
            self.folder_label = QLabel("", self)
            self.folder_label.setProperty("role", "monoMuted")
            top.addWidget(self.folder_label, stretch=1)
        top.addStretch(1)
        self._add_view_options(top)
        root.addWidget(self._top_host)

        self.empty = QLabel(i18n.KO.WAFER_MAP_NO_FOLDER, self)
        self.empty.setProperty("role", "muted")
        self.empty.setWordWrap(True)
        root.addWidget(self.empty)

        maps = QHBoxLayout()
        maps.setSpacing(theme.PROFILE.card_pad)
        self.left = _MapPanel(self)
        self.right = _MapPanel(self)
        maps.addWidget(self.left, stretch=1)
        self.rule = QFrame(self)
        self.rule.setProperty("role", "vrule")
        maps.addWidget(self.rule)
        maps.addWidget(self.right, stretch=1)
        root.addLayout(maps, stretch=1)
        for panel in (self.left, self.right):
            panel.view.point_activated.connect(self._on_point)

        # 전체화면에서 유일하게 남는 나가기 버튼 — 레이아웃 밖에 **떠 있다**(맵 넓이를
        # 한 픽셀도 먹지 않게).  시트 제목줄의 ✕ 가 그때 감춰지므로 ESC 와 함께 이
        # 버튼이 나가기 경로다(sheet_host.set_chrome_visible 의 경고).
        self.exit_btn = NeonButton(i18n.KO.WAFER_MAP_FULLSCREEN_EXIT, role="ghost",
                                   parent=self)
        self.exit_btn.setAutoDefault(False)
        self.exit_btn.clicked.connect(lambda: self.set_fullscreen(False))
        self.exit_btn.hide()
        self.resize(1100, 720)

    def _add_view_options(self, top) -> None:
        """보기 옵션 — die 색칠(셋업 단계만) · 노치 방향(두 단계 모두).

        ``fill_btn`` 은 셋업 단계에서만 만든다 — 결과 단계는 점 색이 곧 매치됨/미매치
        라 칸을 한 색으로 칠하면 그 정보가 사라진다(사용자 결정).  없는 단계에서
        참조하다 조용히 깨지지 않게 :attr:`fill_btn` 은 그때 ``None`` 이다."""
        self.fill_btn: Optional[NeonButton] = None
        if self._result is None:
            self.fill_btn = NeonButton(i18n.KO.WAFER_MAP_FILL_DIES, role="ghost")
            self.fill_btn.setCheckable(True)
            self.fill_btn.setToolTip(i18n.KO.WAFER_MAP_FILL_DIES_TIP)
            self.fill_btn.setAutoDefault(False)
            self.fill_btn.toggled.connect(self._on_fill_toggled)
            top.addWidget(self.fill_btn)
        self.notch_btn = NeonButton("", role="ghost")
        self.notch_btn.setToolTip(i18n.KO.WAFER_MAP_NOTCH_TIP)
        self.notch_btn.setAutoDefault(False)
        self.notch_btn.clicked.connect(self._on_rotate)
        self._update_notch_text()
        top.addWidget(self.notch_btn)
        # ★ 토글 버튼(checkable)이 아니다 — 전체화면에서는 이 줄이 통째로 감춰져
        #   ESC·떠 있는 버튼으로 나오므로, checked 상태를 따로 맞춰 줄 일이 없게 한다.
        self.full_btn = NeonButton(i18n.KO.WAFER_MAP_FULLSCREEN, role="ghost")
        self.full_btn.setToolTip(i18n.KO.WAFER_MAP_FULLSCREEN_TIP)
        self.full_btn.setAutoDefault(False)
        self.full_btn.clicked.connect(lambda: self.set_fullscreen(True))
        top.addWidget(self.full_btn)

    def _views(self):
        return (self.left.view, self.right.view)

    def _on_fill_toggled(self, on: bool) -> None:
        for view in self._views():
            view.set_fill_dies(on)

    def _on_rotate(self) -> None:
        """누를 때마다 시계 방향 90° — 노치가 아래→왼쪽→위→오른쪽으로 돈다."""
        self._rot = (self._rot + 1) % len(i18n.KO.WAFER_MAP_NOTCH_DIRS)
        for view in self._views():
            view.set_rotation(self._rot)
        self._update_notch_text()

    def _update_notch_text(self) -> None:
        self.notch_btn.setText(i18n.KO.WAFER_MAP_NOTCH_FMT.format(
            dir=i18n.KO.WAFER_MAP_NOTCH_DIRS[self._rot]))

    # ------------------------------------------------------------------
    # 전체화면 — 맵만 남기고, 앱 창도 같이 키운다
    # ------------------------------------------------------------------
    def set_fullscreen(self, on: bool) -> None:
        """맵만 남기는 보기.  주변 표시를 감추고 **메인 창**도 전체화면으로 바꾼다.

        ★ 시트 자신에게 ``showFullScreen()`` 을 걸지 않는다 — 자식 위젯에는 통하지
        않는다(``widgets/window_controls`` 의 실측 기록).  그래서 '맵만 남기기'(이
        위젯이 하는 일)와 '창 키우기'(메인 창이 하는 일)를 나눠서 한다.

        ★ 창은 **내가 바꿨을 때만** 되돌린다.  사용자가 이미 F11 로 전체화면을 쓰고
        있었다면 나갈 때 그 상태를 빼앗지 않는다."""
        on = bool(on)
        if on == self._fullscreen:
            return
        self._fullscreen = on
        self.sub.setVisible(not on)
        self._top_host.setVisible(not on)
        for panel in (self.left, self.right):
            panel.set_chrome_visible(not on)
        m = 0 if on else 16
        self.layout().setContentsMargins(m, m, m, m)
        sheets.set_chrome_visible(self, not on)     # 시트 제목줄(+ ✕)
        self._sync_window_fullscreen(on)
        self.exit_btn.setVisible(on)
        if on:
            self.exit_btn.raise_()
            self._place_exit_btn()

    def is_fullscreen(self) -> bool:
        return self._fullscreen

    def _sync_window_fullscreen(self, on: bool) -> None:
        win = self.window()
        # 보이지 않는 창은 건드리지 않는다 — 시트로 뜨기 전(헤드리스 테스트 포함)에
        # showFullScreen 을 걸면 뜰 생각이 없던 창이 떠 버린다.
        if win is None or not win.isVisible():
            return
        if on:
            if not win.isFullScreen():
                set_window_fullscreen(win, True, self._win_state)
                self._win_changed = True
        elif self._win_changed:
            set_window_fullscreen(win, False, self._win_state)
            self._win_changed = False

    def _place_exit_btn(self) -> None:
        """맵 오른쪽 위 구석 — 웨이퍼는 원이라 네 구석은 비어 있다."""
        self.exit_btn.adjustSize()
        self.exit_btn.move(max(0, self.width() - self.exit_btn.width() - 12), 12)

    def resizeEvent(self, event):       # noqa: N802
        super().resizeEvent(event)
        if self._fullscreen:
            self._place_exit_btn()

    def keyPressEvent(self, event):     # noqa: N802
        """Esc — 전체화면이면 **거기서만** 빠져나온다(창은 닫지 않는다)."""
        if event.key() == Qt.Key.Key_Escape and self._fullscreen:
            self.set_fullscreen(False)
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    def _render_empty(self, text: str = "") -> None:
        self.empty.setText(text or i18n.KO.WAFER_MAP_NO_FOLDER)
        self.empty.show()
        self.left.hide()
        self.rule.hide()
        self.right.hide()

    def _show_maps(self, left: tuple[str, MapData],
                   right: Optional[tuple[str, MapData]]) -> None:
        self.empty.hide()
        self.left.show_map(*left)
        self.left.show()
        if right is None:
            self.rule.hide()
            self.right.hide()
        else:
            self.right.show_map(*right)
            self.rule.show()
            self.right.show()

    # ------------------------------------------------------------------
    # 워커 — 좌표 + 썸네일 선로딩, 진행은 오버레이로
    # ------------------------------------------------------------------
    def _start_build(self, job, on_done, first_msg: str) -> None:
        """``job(report)`` 를 워커에서 돌리고 결과를 ``on_done(left, right, extra)`` 로
        받는다.  오버레이는 **여기서 즉시** ``first_msg`` 로 뜬다 — 워커가 첫 보고를
        하기 전에도 사용자는 무엇을 기다리는지 안다."""
        self._token += 1
        token = self._token
        if self._build_thread is not None and self._build_thread.isRunning():
            self._build_thread.requestInterruption()
        self._loading.show_overlay(first_msg)
        # ★ 워커를 띄우기 **전에** 오버레이를 실제로 한 번 그린다.  `show()` 는 페인트를
        #   예약만 하는데, 곧바로 시작한 워커가 순수 파이썬 파싱(INI 수천 건)으로 GIL
        #   을 쥐면 UI 스레드의 그 첫 페인트가 밀려 '폴더를 골랐는데 한참 아무것도
        #   없다' 가 된다(setup_page `_DieGeometryScan` 과 같은 현상).  여기서 이벤트를
        #   한 바퀴 돌려 덮개가 눈에 보이는 상태로 워커에 들어간다.
        self._loading.repaint()
        QApplication.processEvents()
        th = _MapBuild(token, job)
        th.signals.progress.connect(self._on_progress)

        def _done(t, left, right, extra):
            if t != self._token:
                return                      # 늦게 온 옛 결과 — 새 폴더의 맵을 덮지 않는다
            self._loading.hide_overlay()
            on_done(left, right, extra)

        th.signals.done.connect(_done)
        self._build_thread = th
        th.start()

    def _on_progress(self, token: int, done: int, total: int, msg: str) -> None:
        if token == self._token:
            self._loading.set_progress(done, total, msg)

    def is_building(self) -> bool:
        return self._build_thread is not None and self._build_thread.isRunning()

    def closeEvent(self, event):        # noqa: N802
        # 전체화면인 채 닫으면 내가 키운 창이 전체화면으로 남는다 — 먼저 되돌린다.
        self.set_fullscreen(False)
        if self.is_building():
            self._build_thread.requestInterruption()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # 셋업 단계 — 폴더(슬롯 또는 LOT)
    # ------------------------------------------------------------------
    def _on_pick(self) -> None:
        start = str(self._folder) if self._folder else ""
        path = QFileDialog.getExistingDirectory(
            self, i18n.KO.WAFER_MAP_PICK_FOLDER_TITLE, start)
        if path:
            self.show_folder(Path(path))

    def show_folder(self, folder: Path) -> None:
        """폴더 판정(슬롯/LOT)부터 워커 — 고른 순간 '폴더 탐색 중' 이 뜬다."""
        self._folder = folder
        self._single = None
        self._merged = []
        self._set_current(None)
        self.folder_label.setText(str(folder))
        self.pick_btn.setText(i18n.KO.WAFER_MAP_PICK_FOLDER_ANOTHER)
        self._lot_slots = {}
        self._selected = None
        self.slots_btn.hide()

        def job(report):
            report(i18n.KO.WAFER_MAP_LOADING_SCAN)
            kind, slots = classify_folder(folder)
            if kind == "empty":
                return MapData(None, (), ()), None, (kind, slots)
            paths = (_list_images(folder) if kind == "slot"
                     else [p for n in sorted(slots) for p in _list_images(slots[n])])
            report(i18n.KO.WAFER_MAP_LOADING_COORDS)
            coords = resolve_batch(paths, _coord_progress(report))
            return build_map(coords), None, (kind, slots)

        def done(data, _right, extra):
            kind, slots = extra or ("empty", {})
            self._lot_slots = slots
            self.slots_btn.setVisible(kind == "lot")
            if kind == "empty":
                self._render_empty(i18n.KO.WAFER_MAP_NO_IMAGES)
            else:
                self._show_folder_map(data)

        self._start_build(job, done, i18n.KO.WAFER_MAP_LOADING_SCAN)

    def _show_folder_map(self, data: MapData, empty_msg: str = "") -> None:
        if data.frame is None:
            self._set_current(None)
            self._render_empty(empty_msg or i18n.KO.WAFER_MAP_NO_FRAME)
        else:
            self._set_current(data)
            self._show_maps((self._folder_title(), data), None)

    def _set_current(self, data: Optional[MapData]) -> None:
        self._current = data
        self.export_btn.setEnabled(data is not None)

    def current_map(self) -> Optional[MapData]:
        return self._current

    # ------------------------------------------------------------------
    # txt 저장 / 합치기 / 끌어놓기
    # ------------------------------------------------------------------
    def _on_export(self) -> None:
        if self._current is None:
            return
        base = self._folder or Path.home()
        name = (self._single.stem if self._single is not None
                else (base.name if not self._merged else "merged"))
        path, _ = QFileDialog.getSaveFileName(
            self, i18n.KO.WAFER_MAP_EXPORT_TITLE, str(base / f"{name}_wafer_map.txt"),
            i18n.KO.WAFER_MAP_TXT_FILTER)
        if path:
            self.export_to(Path(path))

    def export_to(self, path: Path) -> bool:
        try:
            wafer_map_txt.save(self._current, path)
        except (OSError, TypeError) as exc:
            sheets.error(self, i18n.KO.WAFER_MAP_EXPORT,
                         i18n.KO.WAFER_MAP_EXPORT_FAIL_FMT.format(err=exc))
            return False
        self.folder_label.setText(i18n.KO.WAFER_MAP_EXPORT_DONE_FMT.format(path=path))
        return True

    def _on_merge(self) -> None:
        start = str(self._merged[-1].parent if self._merged else (self._folder or ""))
        paths, _ = QFileDialog.getOpenFileNames(
            self, i18n.KO.WAFER_MAP_MERGE_TITLE, start, i18n.KO.WAFER_MAP_TXT_FILTER)
        if paths:
            self.merge_files([Path(p) for p in paths], add=False)

    def merge_files(self, paths: list[Path], *, add: bool = True) -> None:
        """txt 맵들을 한 맵으로.  ``add`` 면 이미 합친 맵에 더한다(끌어놓기)."""
        files = list(self._merged) if add else []
        files += [p for p in paths if p not in files]
        maps, bad = [], []
        for f in files:
            try:
                maps.append(wafer_map_txt.load(f))
            except (OSError, ValueError, IndexError, UnicodeDecodeError):
                bad.append(f)
        if bad:
            sheets.warn(self, i18n.KO.WAFER_MAP_MERGE,
                        i18n.KO.WAFER_MAP_MERGE_BAD_FMT.format(
                            names="\n".join(b.name for b in bad)))
        good = [f for f in files if f not in bad]
        if not good:
            return
        self._merged = good
        self._single = None
        self._lot_slots = {}
        self._selected = None
        self.slots_btn.hide()
        self.folder_label.setText(" + ".join(f.name for f in good))
        data = wafer_map_txt.merge(maps)
        if data.frame is None:
            self._set_current(None)
            self._render_empty(i18n.KO.WAFER_MAP_NO_FRAME)
            return
        self._set_current(data)
        self._show_maps((i18n.KO.WAFER_MAP_MERGED_FMT.format(n=len(good)), data), None)

    def merged_files(self) -> list[Path]:
        return list(self._merged)

    @staticmethod
    def _dropped(event) -> tuple[list[Path], list[Path]]:
        """끌어놓은 것 → (txt 목록, 사진 목록).  로컬 파일만."""
        txts, imgs = [], []
        exts = {e.lower() for e in CONFIG.image_extensions}
        md = event.mimeData()
        for url in (md.urls() if md is not None and md.hasUrls() else []):
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            if p.suffix.lower() == ".txt":
                txts.append(p)
            elif p.suffix.lower() in exts:
                imgs.append(p)
        return txts, imgs

    def dragEnterEvent(self, event):    # noqa: N802
        txts, imgs = self._dropped(event)
        if txts or imgs:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):     # noqa: N802
        self.dragEnterEvent(event)

    def dropEvent(self, event):         # noqa: N802
        """txt → 맵 합치기(합친 맵이면 더하기) / 사진 → 그 1장만.  txt 가 우선."""
        txts, imgs = self._dropped(event)
        if txts:
            self.merge_files(txts, add=bool(self._merged))
        elif imgs:
            self.show_image(imgs[0])
        else:
            event.ignore()
            return
        event.acceptProposedAction()

    def _folder_title(self) -> str:
        if self._single is not None:
            return i18n.KO.WAFER_MAP_ONE_IMAGE_FMT.format(name=self._single.name)
        assert self._folder is not None
        if not self._lot_slots:
            return self._folder.name
        total = len(self._lot_slots)
        if self._selected is None:
            return i18n.KO.WAFER_MAP_LOT_ALL_FMT.format(lot=self._folder.name,
                                                       total=total)
        return i18n.KO.WAFER_MAP_LOT_SUBSET_FMT.format(
            lot=self._folder.name, n=len(self._selected), total=total)

    def _rebuild_folder_map(self) -> None:
        """LOT 의 선택 슬롯만 다시 — 사진 목록 수집도 워커에서."""
        slots = dict(self._lot_slots)
        names = sorted(slots if self._selected is None else self._selected)

        def job(report):
            report(i18n.KO.WAFER_MAP_LOADING_SCAN)
            paths = [p for n in names for p in _list_images(slots[n])]
            report(i18n.KO.WAFER_MAP_LOADING_COORDS)
            return build_map(resolve_batch(paths, _coord_progress(report))), None, None

        self._start_build(job, lambda d, _r, _e: self._show_folder_map(d),
                          i18n.KO.WAFER_MAP_LOADING_SCAN)

    def show_image(self, path: Path) -> None:
        """사진 1장만 맵에 — 원·격자는 **그 사진이 든 폴더**의 기하 그대로다.

        LOT/웨이퍼 단위로는 점이 수천 개라 한 결함을 짚기 어렵다(사용자 요청).  폴더를
        훑지 않으므로 좌표 1건만 읽는다 — NAS 에서도 즉시 뜬다."""
        self._single = path
        self._folder = path.parent
        self._merged = []
        self._set_current(None)
        self._lot_slots = {}
        self._selected = None
        self.slots_btn.hide()
        self.folder_label.setText(str(path))

        def job(report):
            report(i18n.KO.WAFER_MAP_LOADING_COORDS)
            return build_map(resolve_batch([path], _coord_progress(report))), None, None

        self._start_build(
            job,
            lambda d, _r, _e: self._show_folder_map(d, i18n.KO.WAFER_MAP_NO_COORD),
            i18n.KO.WAFER_MAP_LOADING_COORDS)

    def single_image(self) -> Optional[Path]:
        return self._single

    def _on_pick_slots(self) -> None:
        """LOT 의 일부 슬롯만 — 진행 범위의 슬롯 선택 팝업을 그대로 쓴다."""
        from .slot_select_dialog import SlotSelectDialog
        names = sorted(self._lot_slots)
        dlg = SlotSelectDialog(names, preselected=self._selected, parent=self)
        if not (sheets.run(dlg) and dlg.accepted_ok):
            return
        chosen = dlg.selected
        self._selected = None if (not chosen or chosen == set(names)) else set(chosen)
        self._rebuild_folder_map()

    def selected_slots(self) -> Optional[set[str]]:
        return None if self._selected is None else set(self._selected)

    # ------------------------------------------------------------------
    # 결과 단계 — 슬롯
    # ------------------------------------------------------------------
    def current_slot(self) -> str:
        return self.slot_combo.currentData() or ALL_SLOTS_KEY

    def _on_slot_changed(self) -> None:
        assert self._result is not None
        result, slot = self._result, self.current_slot()
        ref_t = i18n.KO.WAFER_MAP_SIDE_REF_FMT.format(machine=result.ref_machine)
        val_t = i18n.KO.WAFER_MAP_SIDE_VAL_FMT.format(machine=result.val_machine)

        def job(report):
            report(i18n.KO.WAFER_MAP_LOADING_COORDS)
            return (*slot_maps(result, slot, _coord_progress(report)), None)

        def done(ref, val, _extra):
            has_ref = bool(ref.points or ref.unplaced)
            has_val = bool(val.points or val.unplaced)
            if not has_ref and not has_val:
                self._render_empty(i18n.KO.WAFER_MAP_NO_IMAGES)
            elif has_ref and has_val:
                self._show_maps((ref_t, ref), (val_t, val))
            elif has_ref:
                self._show_maps((ref_t, ref), None)
            else:
                self._show_maps((val_t, val), None)

        self._start_build(job, done, i18n.KO.WAFER_MAP_LOADING_COORDS)

    # ------------------------------------------------------------------
    def _on_point(self, path) -> None:
        from .image_info_dialog import ImageInfoDialog
        dlg = ImageInfoDialog(self, image_path=str(path))
        sheets.run(dlg, full_bleed=True)
