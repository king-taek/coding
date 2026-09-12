"""Wafer map 시트 — 두 입구, 한 화면.

* **셋업 단계**(``WaferMapDialog(parent)``): 폴더를 골라 그 웨이퍼의 결함 위치를 본다.
  매칭 전이라 점은 한 색(결함)이다.
* **결과 단계**(``WaferMapDialog(parent, result=...)``): 슬롯을 고르면 기준/검증 맵을
  나란히, '전체' 를 고르면 LOT 의 모든 슬롯을 한 맵에 합산한다.  점은 매치됨/미매치.

점을 클릭하면 :class:`ImageInfoDialog` 로 그 사진과 수치를 본다(같은 생산자 —
수치가 어긋나지 않는다).  좌표 조회는 폴더 단위 ``lru_cache`` 파서라 즉시 끝난다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
                             QLabel, QSizePolicy, QVBoxLayout, QWidget)

from ... import i18n
from ...coords import resolve_batch
from ...coords.wafer_map import (ALL_SLOTS_KEY, MapData, SOURCE_ASSUMED,
                                 build_map, slot_maps)
from ...models.result import FinalResult
from ...models.slot import _list_images
from .. import theme
from . import sheet_host as sheets
from .neon_button import NeonButton
from .wafer_map_view import WaferMapView

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


class WaferMapDialog(QDialog):
    def __init__(self, parent=None, *, result: Optional[FinalResult] = None,
                 folder: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.WAFER_MAP_TITLE)
        self._result = result
        self._folder: Optional[Path] = None
        self._build()
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
            panel.view.point_clicked.connect(self._on_point)
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
    # 셋업 단계 — 폴더
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
        paths = _list_images(folder)
        if not paths:
            self._render_empty(i18n.KO.WAFER_MAP_NO_IMAGES)
            return
        data = build_map(resolve_batch(paths))
        if data.frame is None:
            self._render_empty(i18n.KO.WAFER_MAP_NO_FRAME)
            return
        self._show_maps((folder.name, data), None)

    # ------------------------------------------------------------------
    # 결과 단계 — 슬롯
    # ------------------------------------------------------------------
    def current_slot(self) -> str:
        return self.slot_combo.currentData() or ALL_SLOTS_KEY

    def _on_slot_changed(self) -> None:
        assert self._result is not None
        slot = self.current_slot()
        ref, val = slot_maps(self._result, slot)
        ref_t = i18n.KO.WAFER_MAP_SIDE_REF_FMT.format(machine=self._result.ref_machine)
        val_t = i18n.KO.WAFER_MAP_SIDE_VAL_FMT.format(machine=self._result.val_machine)
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

    # ------------------------------------------------------------------
    def _on_point(self, path) -> None:
        from .image_info_dialog import ImageInfoDialog
        dlg = ImageInfoDialog(self, image_path=str(path))
        sheets.run(dlg, full_bleed=True)
