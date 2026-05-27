"""Slot 불일치 수동 매핑 다이얼로그.

한쪽에만 있는 슬롯(폴더)을 사용자가 직접 짝지어 준다.  KLA 경우엔 각 폴더의
**판독된 slot명(파일명/OCR)** 과 **대표 사진 썸네일**(OCR 폴더는 OCR 헤더 크롭)을
함께 보여줘, 어떤 폴더인지 눈으로 확인하며 짝지을 수 있다.  사진이 없는 폴더는
‘사진파일 없음’ 으로 표시한다.

자동으로 짝지어진(WaferID/폴더명 일치) 쌍은 이 화면이 열리기 전에 이미 병합되며,
여기서는 **남은 항목만** 수동 처리한다.

``ref_meta``/``val_meta`` (선택): ``{폴더명: {"slot","method","image"}}``.
  - method: ``"filename"``(파일명) / ``"ocr"`` / ``"none"``(사진 없음) / ``"unread"``(판독 실패)
  - image: 미리보기용 대표 이미지 경로(없으면 None)
메타가 없으면(일반 슬롯 불일치) 폴더명만 표시한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QImage, QPixmap
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QVBoxLayout, QWidget)

from ... import i18n
from .neon_button import NeonButton
from .window_controls import add_fullscreen_shortcut, enable_window_controls

_THUMB_PX = 72
# KLA 쪽 항목은 WaferID 가 찍힌 '헤더(OCR 구간)' 를 가로로 넓게 보여줘 사용자가 직접
# 읽고 매핑할 수 있게 한다(가로:세로 ≈ 4:1 띠 영역).
_CROP_W = 260
_CROP_H = 72
_KLA_METHODS = ("ocr", "unread", "filename")

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
        self.resize(900, 620)
        self._ref_only = sorted(set(ref_only))
        self._val_only = sorted(set(val_only))
        self._ref_meta = ref_meta or {}
        self._val_meta = val_meta or {}
        self._pairs: list[tuple[str, str]] = []
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
    def _icon_for(self, name: str, meta: dict) -> Optional[QIcon]:
        info = meta.get(name) or {}
        img = info.get("image")
        if not img:
            return None
        # KLA 쪽(파일명/OCR/판독실패) 은 WaferID 헤더(OCR 구간)를 보여줘 사용자가
        # 직접 읽고 매핑하게 한다.  비-KLA(plain) 은 일반 썸네일.
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
        pix = pix.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        return QIcon(pix)

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

    def _fill_list(self, lst: QListWidget, names: list[str], meta: dict) -> None:
        # 아이콘 박스는 넓은 헤더(OCR 구간)가 보이도록 가로로 크게.
        lst.setIconSize(QSize(_CROP_W, _CROP_H))
        for n in names:
            item = QListWidgetItem(self._label_for(n, meta))
            item.setData(Qt.ItemDataRole.UserRole, n)
            ic = self._icon_for(n, meta)
            if ic is not None:
                item.setIcon(ic)
            # 사진 없는 폴더는 짝지을 수 없음 → 비활성 표시(스킵으로 기록됨).
            if (meta.get(n) or {}).get("method") == "none":
                item.setForeground(Qt.GlobalColor.gray)
            lst.addItem(item)

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
        ref_col.addWidget(self._ref_list, stretch=1)
        # 가운데 묶기 버튼
        mid_col = QVBoxLayout()
        mid_col.addStretch(1)
        pair_btn = NeonButton(i18n.KO.SLOT_MAP_ADD, role="primary")
        pair_btn.clicked.connect(self._on_add)
        mid_col.addWidget(QLabel("↔", self), alignment=Qt.AlignmentFlag.AlignHCenter)
        mid_col.addWidget(pair_btn)
        mid_col.addStretch(1)
        # 검증 측
        val_col = QVBoxLayout()
        val_col.addWidget(QLabel(i18n.KO.SLOT_MAP_VAL_LABEL, self))
        self._val_list = QListWidget(self)
        self._fill_list(self._val_list, self._val_only, self._val_meta)
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
        self._pairs_list.setMaximumHeight(150)
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

        self._auto_suggest()

    # ------------------------------------------------------------------
    def _auto_suggest(self) -> None:
        """판독 slot명/폴더명이 일치하는 leftover 를 자동으로 미리 짝지어 둔다."""
        def key(name, meta):
            info = meta.get(name) or {}
            return (info.get("slot") or name).strip().upper()
        val_by_key: dict[str, str] = {}
        for v in self._val_only:
            val_by_key.setdefault(key(v, self._val_meta), v)
        for r in self._ref_only:
            v = val_by_key.get(key(r, self._ref_meta))
            if v and not any(p[0] == r or p[1] == v for p in self._pairs):
                self._add_pair(r, v)

    def _selected(self, lst: QListWidget) -> Optional[str]:
        it = lst.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it is not None else None

    def _add_pair(self, a: str, b: str) -> None:
        if any(p == (a, b) for p in self._pairs):
            return
        if any(p[0] == a for p in self._pairs) or any(p[1] == b for p in self._pairs):
            return
        self._pairs.append((a, b))
        self._pairs_list.addItem(f"{a}  ↔  {b}")

    def _on_add(self) -> None:
        a = self._selected(self._ref_list)
        b = self._selected(self._val_list)
        if not a or not b:
            return
        # 사진 없는 폴더는 짝지을 수 없음.
        if (self._ref_meta.get(a) or {}).get("method") == "none":
            return
        if (self._val_meta.get(b) or {}).get("method") == "none":
            return
        self._add_pair(a, b)

    def _on_remove(self) -> None:
        cur = self._pairs_list.currentRow()
        if cur < 0 or cur >= len(self._pairs):
            return
        self._pairs.pop(cur)
        self._pairs_list.takeItem(cur)
