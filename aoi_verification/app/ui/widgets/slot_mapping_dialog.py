"""Slot 불일치 수동 매핑 다이얼로그.

한쪽에만 있는 슬롯(폴더)을 사용자가 직접 짝지어 준다.  KLA 경우엔 각 폴더의
**판독된 slot명(파일명/OCR)** 과 **WaferID 헤더 크롭**을 함께 보여줘, 어떤 폴더인지
눈으로 확인하며 짝지을 수 있다.  사진이 없는 폴더는 ‘사진파일 없음’ 으로 표시(선택 불가).

상호작용:
  · 기준/검증에서 **각각 최대 1개** 선택(파란 테두리).  이미 선택한 걸 다시 누르면 해제.
  · ‘묶기’ → 선택한 두 항목이 리스트에서 빠져 **‘묶은 쌍’ 으로 사진째 이동**.
  · ‘선택 해제’ → 현재 선택(파란 테두리)을 해제.
  · 묶은 쌍을 더블클릭 → 다시 풀어 원래 리스트로 되돌림.

``ref_meta``/``val_meta`` (선택): ``{폴더명: {"slot","method","image"}}``.
  method: ``filename``/``ocr``/``none``(사진없음)/``unread``(판독실패)/``plain``(일반)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QLabel,
                             QListWidget, QListWidgetItem, QVBoxLayout, QWidget)

from ... import i18n
from .neon_button import NeonButton
from .window_controls import add_fullscreen_shortcut, enable_window_controls

# KLA 헤더(OCR 구간) 미리보기 — 좁게(가로·세로 축소).
_CROP_W = 180
_CROP_H = 60
_THUMB_PX = 60
_KLA_METHODS = ("ocr", "unread", "filename")
_GAP = 26                                   # 묶은 쌍 두 사진 사이 간격

# 선택(클릭) 시 파란 테두리가 또렷하게.  미선택 항목에도 투명 테두리를 미리 둬서
# 선택 시 레이아웃이 흔들리지 않게 한다.
_LIST_SEL_QSS = (
    "QListWidget::item { border: 2px solid transparent; border-radius: 4px;"
    " padding: 2px; margin: 1px; }"
    "QListWidget::item:selected { border: 2px solid #3DA5FF;"
    " background: rgba(61,165,255,0.22); color: #FFFFFF; }"
    "QListWidget::item:selected:active { border: 2px solid #3DA5FF; }"
)

_METHOD_LABEL = {
    "filename": "파일명",
    "ocr": "OCR",
    "none": "사진파일 없음",
    "unread": "판독 실패",
}


@dataclass
class SlotMapping:
    """슬롯 매핑 결과 — pairs[i] = (ref_only_slot, val_only_slot)."""
    pairs: list[tuple[str, str]]
    ref_skip: list[str]
    val_skip: list[str]


def _pil_to_qpixmap(pil) -> Optional[QPixmap]:
    try:
        pil = pil.convert("RGBA")
        data = pil.tobytes("raw", "RGBA")
        qimg = QImage(data, pil.width, pil.height, QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(qimg.copy())
    except Exception:
        return None


class SlotMappingDialog(QDialog):
    def __init__(self,
                 ref_only: Iterable[str],
                 val_only: Iterable[str],
                 *,
                 ref_meta: Optional[dict] = None,
                 val_meta: Optional[dict] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.SLOT_MAP_TITLE)
        self.resize(900, 640)
        self._ref_only = sorted(set(ref_only))
        self._val_only = sorted(set(val_only))
        self._ref_meta = ref_meta or {}
        self._val_meta = val_meta or {}
        self._pairs: list[tuple[str, str]] = []
        self._ref_sel: Optional[str] = None
        self._val_sel: Optional[str] = None
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
    # 썸네일 / 라벨
    # ------------------------------------------------------------------
    def _thumb_pix(self, name: str, meta: dict) -> Optional[QPixmap]:
        info = meta.get(name) or {}
        img = info.get("image")
        if not img:
            return None
        is_kla = info.get("method") in _KLA_METHODS
        pix: Optional[QPixmap] = None
        try:
            if is_kla:
                from ...utils import wafer_id
                crop = wafer_id.header_crop_image(Path(img))
                if crop is not None:
                    pix = _pil_to_qpixmap(crop)
            if pix is None:
                pix = QPixmap(str(img))
        except Exception:
            pix = None
        if pix is None or pix.isNull():
            return None
        w, h = (_CROP_W, _CROP_H) if is_kla else (_THUMB_PX, _THUMB_PX)
        return pix.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)

    def _icon_for(self, name: str, meta: dict) -> Optional[QIcon]:
        pix = self._thumb_pix(name, meta)
        return QIcon(pix) if pix is not None else None

    def _label_for(self, name: str, meta: dict) -> str:
        info = meta.get(name)
        if not info:
            return name
        method = info.get("method", "")
        slot = info.get("slot")
        if method == "none":
            return f"{name}\n⚠ 사진파일 없음"
        if slot:
            return f"{name}\n→ {slot}  ({_METHOD_LABEL.get(method, method)})"
        if method == "unread":
            return f"{name}\n→ 판독 실패 (수동 매핑)"
        return name

    def _make_item(self, name: str, meta: dict) -> QListWidgetItem:
        item = QListWidgetItem(self._label_for(name, meta))
        item.setData(Qt.ItemDataRole.UserRole, name)
        ic = self._icon_for(name, meta)
        if ic is not None:
            item.setIcon(ic)
        if (meta.get(name) or {}).get("method") == "none":
            # 사진 없는 폴더는 짝지을 수 없음 → 선택 불가 + 흐리게.
            item.setForeground(Qt.GlobalColor.gray)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        return item

    def _fill_list(self, lst: QListWidget, names: list[str], meta: dict) -> None:
        lst.setIconSize(QSize(_CROP_W, _CROP_H))
        lst.setStyleSheet(_LIST_SEL_QSS)
        lst.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for n in names:
            lst.addItem(self._make_item(n, meta))

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        hint = QLabel(i18n.KO.SLOT_MAP_HINT, self)
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        lists_row = QHBoxLayout()
        lists_row.setSpacing(10)
        # 기준 측
        ref_col = QVBoxLayout()
        ref_col.addWidget(QLabel(i18n.KO.SLOT_MAP_REF_LABEL, self))
        self._ref_list = QListWidget(self)
        self._fill_list(self._ref_list, self._ref_only, self._ref_meta)
        self._ref_list.itemClicked.connect(self._on_ref_clicked)
        ref_col.addWidget(self._ref_list, stretch=1)
        # 가운데 묶기 버튼
        mid_col = QVBoxLayout()
        mid_col.addStretch(1)
        mid_col.addWidget(QLabel("↔", self), alignment=Qt.AlignmentFlag.AlignHCenter)
        pair_btn = NeonButton(i18n.KO.SLOT_MAP_ADD, role="primary")
        pair_btn.clicked.connect(self._on_add)
        mid_col.addWidget(pair_btn)
        clear_btn = NeonButton(i18n.KO.SLOT_MAP_REMOVE, role="ghost")
        clear_btn.clicked.connect(self._on_clear_sel)
        mid_col.addWidget(clear_btn)
        mid_col.addStretch(1)
        # 검증 측
        val_col = QVBoxLayout()
        val_col.addWidget(QLabel(i18n.KO.SLOT_MAP_VAL_LABEL, self))
        self._val_list = QListWidget(self)
        self._fill_list(self._val_list, self._val_only, self._val_meta)
        self._val_list.itemClicked.connect(self._on_val_clicked)
        val_col.addWidget(self._val_list, stretch=1)

        ref_w = QWidget(self); ref_w.setLayout(ref_col)
        mid_w = QWidget(self); mid_w.setLayout(mid_col)
        val_w = QWidget(self); val_w.setLayout(val_col)
        lists_row.addWidget(ref_w, stretch=5)
        lists_row.addWidget(mid_w, stretch=1)
        lists_row.addWidget(val_w, stretch=5)
        root.addLayout(lists_row, stretch=3)

        root.addWidget(QLabel(i18n.KO.SLOT_MAP_PAIRS_LABEL, self))
        self._pairs_list = QListWidget(self)
        self._pairs_list.setIconSize(QSize(_CROP_W * 2 + _GAP, _CROP_H))
        self._pairs_list.setMaximumHeight(190)
        self._pairs_list.itemDoubleClicked.connect(self._on_pair_double_clicked)
        root.addWidget(self._pairs_list, stretch=1)

        bar = QHBoxLayout()
        bar.addStretch(1)
        cancel = NeonButton(i18n.KO.BTN_CANCEL, role="ghost")
        cancel.clicked.connect(self.reject)
        ok = NeonButton(i18n.KO.BTN_OK, role="primary")
        ok.clicked.connect(self.accept)
        bar.addWidget(cancel)
        bar.addWidget(ok)
        root.addLayout(bar)

        self._auto_suggest()

    # ------------------------------------------------------------------
    # 선택(클릭) — 단일 선택 + 재클릭 시 해제
    # ------------------------------------------------------------------
    def _on_ref_clicked(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        if self._ref_sel == name:
            self._ref_list.clearSelection()
            self._ref_sel = None
        else:
            self._ref_sel = name

    def _on_val_clicked(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        if self._val_sel == name:
            self._val_list.clearSelection()
            self._val_sel = None
        else:
            self._val_sel = name

    def _on_clear_sel(self) -> None:
        self._ref_list.clearSelection()
        self._val_list.clearSelection()
        self._ref_sel = None
        self._val_sel = None

    # ------------------------------------------------------------------
    # 묶기 / 풀기
    # ------------------------------------------------------------------
    def _take_item(self, lst: QListWidget, name: str) -> bool:
        for i in range(lst.count()):
            it = lst.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == name:
                lst.takeItem(i)
                return True
        return False

    def _combined_pixmap(self, a: str, b: str) -> QPixmap:
        pa = self._thumb_pix(a, self._ref_meta)
        pb = self._thumb_pix(b, self._val_meta)
        combo = QPixmap(_CROP_W * 2 + _GAP, _CROP_H)
        combo.fill(QColor(0, 0, 0, 0))
        p = QPainter(combo)
        if pa is not None:
            p.drawPixmap(max(0, (_CROP_W - pa.width()) // 2),
                         (_CROP_H - pa.height()) // 2, pa)
        p.setPen(QColor("#3DA5FF"))
        p.drawText(_CROP_W, 0, _GAP, _CROP_H,
                   Qt.AlignmentFlag.AlignCenter, "↔")
        if pb is not None:
            p.drawPixmap(_CROP_W + _GAP + max(0, (_CROP_W - pb.width()) // 2),
                         (_CROP_H - pb.height()) // 2, pb)
        p.end()
        return combo

    def _add_pair(self, a: str, b: str) -> None:
        if any(p[0] == a for p in self._pairs) or any(p[1] == b for p in self._pairs):
            return
        # 두 항목을 원래 리스트에서 빼서 '묶은 쌍' 으로 사진째 이동.
        self._take_item(self._ref_list, a)
        self._take_item(self._val_list, b)
        item = QListWidgetItem(f"{a}  ↔  {b}")
        item.setData(Qt.ItemDataRole.UserRole, (a, b))
        item.setIcon(QIcon(self._combined_pixmap(a, b)))
        self._pairs_list.addItem(item)
        self._pairs.append((a, b))

    def _on_add(self) -> None:
        a, b = self._ref_sel, self._val_sel
        if not a or not b:
            return
        self._add_pair(a, b)
        self._on_clear_sel()

    def _on_pair_double_clicked(self, item: QListWidgetItem) -> None:
        """묶은 쌍을 더블클릭 → 풀어서 원래 리스트로 사진째 되돌림."""
        pair = item.data(Qt.ItemDataRole.UserRole)
        if not pair:
            return
        a, b = pair
        row = self._pairs_list.row(item)
        self._pairs_list.takeItem(row)
        if (a, b) in self._pairs:
            self._pairs.remove((a, b))
        self._ref_list.addItem(self._make_item(a, self._ref_meta))
        self._val_list.addItem(self._make_item(b, self._val_meta))

    # ------------------------------------------------------------------
    def _auto_suggest(self) -> None:
        """판독 slot명/폴더명이 일치하는 leftover 를 자동으로 미리 묶어 둔다."""
        def key(name, meta):
            info = meta.get(name) or {}
            if info.get("method") == "none":
                return None
            return (info.get("slot") or name).strip().upper()
        val_by_key: dict[str, str] = {}
        for v in self._val_only:
            k = key(v, self._val_meta)
            if k:
                val_by_key.setdefault(k, v)
        for r in self._ref_only:
            k = key(r, self._ref_meta)
            v = val_by_key.get(k) if k else None
            if v and not any(p[0] == r or p[1] == v for p in self._pairs):
                self._add_pair(r, v)
