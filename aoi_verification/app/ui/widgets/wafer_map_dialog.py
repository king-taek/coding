"""Wafer map 시트 — 두 입구, 한 화면.

* **셋업 단계**(``WaferMapDialog(parent)``): 폴더를 고른다.  **슬롯 폴더**(사진이 바로
  든 폴더)면 그 웨이퍼 하나, **LOT 폴더**(슬롯 폴더들이 든 폴더)면 전체 슬롯을 한 맵에
  합산하고 ‘슬롯 선택…’ 으로 일부만 본다(진행 범위의 슬롯 선택 팝업과 같은 창).
  매칭 전이라 점은 한 색(결함)이다.
* **결과 단계**(``WaferMapDialog(parent, result=...)``): 슬롯을 고르면 기준/검증 맵을
  나란히, '전체' 를 고르면 LOT 의 모든 슬롯을 한 맵에 합산한다.  점은 매치됨/미매치.

맵 만들기는 **워커 스레드**(:class:`_MapBuild`)가 한다 — 좌표 파싱은 폴더 단위 캐시라
빠르지만 NAS 의 사진 수천 장 **썸네일 선로딩**은 I/O 라 UI 스레드에서 돌리면 창이
멈춘다.  진행은 시그널로 :class:`LoadingOverlay` 에 전달한다(CLAUDE.md 로딩 계약).
썸네일을 미리 만들어 두므로 점에 마우스를 올리면 사진이 바로 뜬다.

점을 **더블클릭**하면 :class:`ImageInfoDialog` 로 그 사진의 상세 수치를 본다(같은
생산자 — 엑셀과 수치가 어긋나지 않는다).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
                             QLabel, QSizePolicy, QVBoxLayout, QWidget)

from ... import i18n
from ...coords import resolve_batch
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

# 페이지가 닫혀도 돌던 스레드가 수명을 다 살게 붙들어 둔다(setup_page 의 패턴).
_LIVE_BUILDS: set = set()


class _MapBuild(QThread):
    """좌표 → MapData 계산 + 썸네일 선로딩.  ``token`` 으로 늦은 결과를 버린다."""

    class _Signals(QObject):
        progress = pyqtSignal(int, int, int, str)      # token, done, total, msg
        done = pyqtSignal(int, object, object)         # token, left MapData, right|None

    def __init__(self, token: int, job) -> None:
        """``job()`` 은 ``(left: MapData, right: MapData | None)`` 을 돌려주는 순수
        호출체(좌표 계산).  썸네일은 그 결과의 점 전부에 대해 여기서 만든다."""
        super().__init__()
        self._token = token
        self._job = job
        self.signals = self._Signals()
        _LIVE_BUILDS.add(self)
        self.finished.connect(lambda: _LIVE_BUILDS.discard(self))

    def run(self) -> None:      # type: ignore[override]
        t = self._token
        try:
            self.signals.progress.emit(t, 0, 0, i18n.KO.WAFER_MAP_LOADING_COORDS)
            left, right = self._job()
            paths = [p.path for p in left.points]
            if right is not None:
                paths += [p.path for p in right.points]
            total = len(paths)
            for i, path in enumerate(paths, start=1):
                if self.isInterruptionRequested():
                    return
                try:
                    image_io.get_thumb_path(path)
                except Exception:
                    pass
                if i % 20 == 0 or i == total:
                    self.signals.progress.emit(
                        t, i, total, i18n.KO.WAFER_MAP_LOADING_THUMBS)
        except Exception:
            left, right = MapData(None, (), ()), None
        self.signals.done.emit(t, left, right)


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
        self._lot_slots: dict[str, Path] = {}         # LOT 폴더일 때만
        self._selected: Optional[set[str]] = None     # None = 전체
        self._token = 0
        self._build_thread: Optional[_MapBuild] = None
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

        sub = QLabel(i18n.KO.WAFER_MAP_SUBTITLE, self)
        sub.setProperty("role", "subtitle")
        sub.setWordWrap(True)
        root.addWidget(sub)

        top = QHBoxLayout()
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
            self.folder_label = QLabel("", self)
            self.folder_label.setProperty("role", "monoMuted")
            top.addWidget(self.folder_label, stretch=1)
        top.addStretch(1)
        root.addLayout(top)

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
        self.resize(1100, 720)

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
    def _start_build(self, job, on_done) -> None:
        """``job`` 을 워커에서 돌리고 결과를 ``on_done(left, right)`` 로 받는다."""
        self._token += 1
        token = self._token
        if self._build_thread is not None and self._build_thread.isRunning():
            self._build_thread.requestInterruption()
        self._loading.show_overlay(i18n.KO.WAFER_MAP_LOADING)
        th = _MapBuild(token, job)
        th.signals.progress.connect(self._on_progress)

        def _done(t, left, right):
            if t != self._token:
                return                      # 늦게 온 옛 결과 — 새 폴더의 맵을 덮지 않는다
            self._loading.hide_overlay()
            on_done(left, right)

        th.signals.done.connect(_done)
        self._build_thread = th
        th.start()

    def _on_progress(self, token: int, done: int, total: int, msg: str) -> None:
        if token == self._token:
            self._loading.set_progress(done, total, msg)

    def is_building(self) -> bool:
        return self._build_thread is not None and self._build_thread.isRunning()

    def closeEvent(self, event):        # noqa: N802
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
        self._folder = folder
        self.folder_label.setText(str(folder))
        self.pick_btn.setText(i18n.KO.WAFER_MAP_PICK_FOLDER_ANOTHER)
        kind, slots = classify_folder(folder)
        self._lot_slots = slots
        self._selected = None
        self.slots_btn.setVisible(kind == "lot")
        if kind == "empty":
            self._render_empty(i18n.KO.WAFER_MAP_NO_IMAGES)
            return
        self._rebuild_folder_map()

    def _current_paths(self) -> list[Path]:
        if not self._lot_slots:
            return _list_images(self._folder) if self._folder else []
        names = sorted(self._lot_slots if self._selected is None else self._selected)
        out: list[Path] = []
        for n in names:
            out += _list_images(self._lot_slots[n])
        return out

    def _folder_title(self) -> str:
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
        paths = self._current_paths()
        title = self._folder_title()

        def job():
            return build_map(resolve_batch(paths)), None

        def done(data, _right):
            if data.frame is None:
                self._render_empty(i18n.KO.WAFER_MAP_NO_FRAME)
            else:
                self._show_maps((title, data), None)

        self._start_build(job, done)

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

        def job():
            return slot_maps(result, slot)

        def done(ref, val):
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

        self._start_build(job, done)

    # ------------------------------------------------------------------
    def _on_point(self, path) -> None:
        from .image_info_dialog import ImageInfoDialog
        dlg = ImageInfoDialog(self, image_path=str(path))
        sheets.run(dlg, full_bleed=True)
