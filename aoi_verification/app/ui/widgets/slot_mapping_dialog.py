"""Slot 불일치 수동 매핑 다이얼로그 (#23).

폴더 이름이 미묘하게 다른 호기 (예: ``Slot_01`` vs ``S01``) 의 슬롯을 사용자가
직접 짝지어 줄 수 있다. 한쪽에만 있는 슬롯 목록을 좌/우 콤보박스로 보여주고,
‘이 둘은 같은 슬롯’ 으로 묶거나 ‘제외’ 할 수 있다.

결과는 다이얼로그 닫힐 때 ``mapping`` 속성으로 가져갈 수 있고, 호출자가
ScanResult 를 재구성해서 사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel,
                              QListWidget, QListWidgetItem, QVBoxLayout,
                              QWidget)

from ... import i18n
from .neon_button import NeonButton
from .window_controls import add_fullscreen_shortcut, enable_window_controls


@dataclass
class SlotMapping:
    """슬롯 매핑 결과 — pairs[i] = (ref_only_slot, val_only_slot)."""
    pairs: list[tuple[str, str]]
    ref_skip: list[str]            # ref 쪽 무시할 슬롯
    val_skip: list[str]            # val 쪽 무시할 슬롯


class SlotMappingDialog(QDialog):
    def __init__(self,
                 ref_only: Iterable[str],
                 val_only: Iterable[str],
                 parent=None,
                 header_pixmaps: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.SLOT_MAP_TITLE)
        self.resize(720, 520)
        self._ref_only = sorted(set(ref_only))
        self._val_only = sorted(set(val_only))
        self._pairs: list[tuple[str, str]] = []
        # 폴더명 → 헤더(‘OCR 부분’) 미리보기 QPixmap.  주어지면 콤보 선택 시 표시.
        self._header_pixmaps = dict(header_pixmaps or {})
        # 창에 최소화/최대화 버튼 + F11 전체화면 토글 (#9). 첫 show 이전에 설정.
        enable_window_controls(self)
        add_fullscreen_shortcut(self)
        self._build()

    @property
    def mapping(self) -> SlotMapping:
        used_ref = {a for a, _ in self._pairs}
        used_val = {b for _, b in self._pairs}
        return SlotMapping(
            pairs=list(self._pairs),
            ref_skip=[s for s in self._ref_only if s not in used_ref],
            val_skip=[s for s in self._val_only if s not in used_val],
        )

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        hint = QLabel(i18n.KO.SLOT_MAP_HINT, self)
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        select_row = QHBoxLayout()
        self._ref_combo = QComboBox(self)
        self._val_combo = QComboBox(self)
        for s in self._ref_only:
            self._ref_combo.addItem(s)
        for s in self._val_only:
            self._val_combo.addItem(s)
        select_row.addWidget(QLabel(i18n.KO.SLOT_MAP_REF_LABEL, self))
        select_row.addWidget(self._ref_combo, stretch=1)
        select_row.addWidget(QLabel("↔", self))
        select_row.addWidget(QLabel(i18n.KO.SLOT_MAP_VAL_LABEL, self))
        select_row.addWidget(self._val_combo, stretch=1)
        add_btn = NeonButton(i18n.KO.SLOT_MAP_ADD, role="primary")
        add_btn.clicked.connect(self._on_add)
        select_row.addWidget(add_btn)
        root.addLayout(select_row)

        # 헤더(‘OCR 부분’) 미리보기 — OCR 자동 인식이 끝까지 실패했을 때, 선택한
        # 폴더의 좌상단(WaferID)을 직접 보고 짝지을 수 있게 한다.
        if self._header_pixmaps:
            prev_hint = QLabel(i18n.KO.SLOT_MAP_PREVIEW_HINT, self)
            prev_hint.setProperty("role", "muted")
            prev_hint.setWordWrap(True)
            root.addWidget(prev_hint)
            prev_row = QHBoxLayout()
            self._ref_preview = QLabel(self)
            self._val_preview = QLabel(self)
            for lab in (self._ref_preview, self._val_preview):
                lab.setMinimumHeight(72)
                lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lab.setStyleSheet(
                    "background: #050810; border: 1px solid #1F2A3F;")
            prev_row.addWidget(self._ref_preview, stretch=1)
            prev_row.addWidget(self._val_preview, stretch=1)
            root.addLayout(prev_row)
            self._ref_combo.currentTextChanged.connect(self._update_ref_preview)
            self._val_combo.currentTextChanged.connect(self._update_val_preview)
            self._update_ref_preview(self._ref_combo.currentText())
            self._update_val_preview(self._val_combo.currentText())

        self._pairs_list = QListWidget(self)
        self._pairs_list.setMinimumHeight(220)
        root.addWidget(self._pairs_list, stretch=1)

        del_row = QHBoxLayout()
        del_btn = NeonButton(i18n.KO.SLOT_MAP_REMOVE, role="danger")
        del_btn.clicked.connect(self._on_remove)
        del_row.addWidget(del_btn)
        del_row.addStretch(1)
        root.addLayout(del_row)

        bar = QHBoxLayout()
        bar.addStretch(1)
        cancel = NeonButton(i18n.KO.BTN_CANCEL, role="ghost")
        cancel.clicked.connect(self.reject)
        ok = NeonButton(i18n.KO.BTN_OK, role="primary")
        ok.clicked.connect(self.accept)
        bar.addWidget(cancel)
        bar.addWidget(ok)
        root.addLayout(bar)

    # ------------------------------------------------------------------
    def _update_ref_preview(self, name: str) -> None:
        self._set_preview(self._ref_preview, name)

    def _update_val_preview(self, name: str) -> None:
        self._set_preview(self._val_preview, name)

    def _set_preview(self, label: QLabel, name: str) -> None:
        pm = self._header_pixmaps.get(name)
        if pm is None or pm.isNull():
            label.clear()
            label.setText(i18n.KO.SLOT_MAP_NO_PREVIEW)
            return
        w = min(360, pm.width())
        label.setPixmap(pm.scaledToWidth(
            w, Qt.TransformationMode.SmoothTransformation))

    # ------------------------------------------------------------------
    def _on_add(self) -> None:
        if self._ref_combo.count() == 0 or self._val_combo.count() == 0:
            return
        a = self._ref_combo.currentText()
        b = self._val_combo.currentText()
        if not a or not b:
            return
        # 중복 추가 방지
        for existing in self._pairs:
            if existing == (a, b):
                return
        self._pairs.append((a, b))
        self._pairs_list.addItem(f"{a}  ↔  {b}")
        # 사용된 슬롯은 콤보에서 제거
        idx_a = self._ref_combo.findText(a)
        if idx_a >= 0:
            self._ref_combo.removeItem(idx_a)
        idx_b = self._val_combo.findText(b)
        if idx_b >= 0:
            self._val_combo.removeItem(idx_b)

    def _on_remove(self) -> None:
        cur = self._pairs_list.currentRow()
        if cur < 0 or cur >= len(self._pairs):
            return
        a, b = self._pairs.pop(cur)
        self._pairs_list.takeItem(cur)
        # 콤보에 다시 추가
        self._ref_combo.addItem(a)
        self._val_combo.addItem(b)
