"""Slot / ImageItem 데이터 클래스 및 폴더 스캔 헬퍼."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .. import config


@dataclass(frozen=True)
class ImageItem:
    """한 장의 원본 이미지."""
    slot: str
    path: Path
    side: str  # "ref" or "val"

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def key(self) -> str:
        return f"{self.side}::{self.slot}::{self.path.name}"


@dataclass
class Slot:
    """한 슬롯에 대응하는 기준/검증 이미지 목록."""
    name: str
    ref_images: list[ImageItem] = field(default_factory=list)
    val_images: list[ImageItem] = field(default_factory=list)
    # KLA 장비처럼 폴더명이 slot 명이 아니고 하위 ``.001`` 파일명에서 slot 을 뽑은
    # 경우, 원본 폴더명을 보관(엑셀에 'slot명 / 폴더명' 두 줄로 표기).
    kla_folder: str | None = None

    @property
    def has_both(self) -> bool:
        return bool(self.ref_images) and bool(self.val_images)


@dataclass
class ScanResult:
    """루트 폴더 스캔 결과 + 한쪽에만 존재하는 Slot 목록."""
    slots: dict[str, Slot]
    ref_only: list[str]
    val_only: list[str]

    @property
    def common_slot_names(self) -> list[str]:
        return sorted(name for name, s in self.slots.items() if s.has_both)


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------
def _list_images(folder: Path) -> list[Path]:
    """폴더 내 이미지 파일 목록.

    ``os.scandir`` 의 캐시된 ``DirEntry.is_file()`` 를 써서 파일당 별도
    ``stat()`` 시스템콜을 피한다 — 폴더에 수만 장이 있어도 빠르게 열거 (#3).
    """
    if not folder.exists() or not folder.is_dir():
        return []
    out: list[Path] = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                try:
                    if entry.is_file() and config.CONFIG.is_image(entry.name):
                        out.append(Path(entry.path))
                except OSError:
                    continue
    except OSError:
        return []
    out.sort(key=lambda p: p.name.lower())
    return out


def _kla_slot_name(folder: Path) -> str | None:
    """KLA 폴더라면 하위 ``*.001`` 파일명에서 slot 명을 뽑아 돌려준다 (없으면 None).

    slot 명 = ``.001`` 파일 stem 의 **마지막 '_' 뒤** 토큰.  예)
    ``..._W6459079XYE1.001`` → ``W6459079XYE1``.  파일 **내용은 읽지 않고 파일명만**
    사용한다.  여러 개면 이름 오름차순 첫 파일을 쓴다(보통 동일 slot).
    """
    names: list[str] = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                try:
                    if entry.is_file() and entry.name.lower().endswith(".001"):
                        names.append(entry.name)
                except OSError:
                    continue
    except OSError:
        return None
    if not names:
        return None
    stem = Path(sorted(names, key=str.lower)[0]).stem
    token = stem.rsplit("_", 1)[-1].strip()
    return token or None


def _enum_slot_dirs(root: Path) -> dict[str, tuple[Path, str | None]]:
    """root/*/ 들 중 폴더만 골라 ``슬롯명 → (경로, kla_folder)`` 매핑.

    하위에 ``.001`` 파일이 있는 KLA 폴더는 그 파일명에서 뽑은 slot 명을 키로 쓰고
    원본 폴더명을 ``kla_folder`` 로 함께 보관한다.  ``.001`` 이 없으면 폴더명을
    그대로 slot 키로 쓴다(``kla_folder=None``).
    """
    if not root.exists():
        return {}
    out: dict[str, tuple[Path, str | None]] = {}
    try:
        with os.scandir(root) as it:
            for entry in it:
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                path = Path(entry.path)
                kla = _kla_slot_name(path)
                if kla:
                    out[kla] = (path, entry.name)
                else:
                    out[entry.name] = (path, None)
    except OSError:
        return {}
    return out


def scan(ref_root: Path, val_root: Path) -> ScanResult:
    """기준/검증 두 최상위 폴더를 스캔하여 Slot 매핑을 만든다."""
    ref_dirs = _enum_slot_dirs(Path(ref_root))
    val_dirs = _enum_slot_dirs(Path(val_root))

    all_names = sorted(set(ref_dirs.keys()) | set(val_dirs.keys()))
    slots: dict[str, Slot] = {}
    ref_only: list[str] = []
    val_only: list[str] = []

    for name in all_names:
        ref_e = ref_dirs.get(name)
        val_e = val_dirs.get(name)
        ref_d = ref_e[0] if ref_e else None
        val_d = val_e[0] if val_e else None
        ref_imgs: list[ImageItem] = []
        val_imgs: list[ImageItem] = []
        if ref_d:
            ref_imgs = [ImageItem(name, p, "ref") for p in _list_images(ref_d)]
        if val_d:
            val_imgs = [ImageItem(name, p, "val") for p in _list_images(val_d)]
        if ref_d and not val_d:
            ref_only.append(name)
        elif val_d and not ref_d:
            val_only.append(name)
        # 어느 쪽이든 KLA 폴더명이 있으면 보관(엑셀 두 줄 표기용).
        kla_folder = (ref_e[1] if ref_e else None) or (val_e[1] if val_e else None)
        slots[name] = Slot(name=name, ref_images=ref_imgs, val_images=val_imgs,
                           kla_folder=kla_folder)

    return ScanResult(slots=slots, ref_only=ref_only, val_only=val_only)


def drop_empty_unmatched(sr: ScanResult) -> None:
    """한쪽 전용(ref_only/val_only) 중 **사진이 한 장도 없는 폴더**를 목록에서 제거.

    매칭할 사진 자체가 없으므로 OCR/수동 매핑 대상에서 그냥 제외한다(사용자 요청).
    ``ScanResult`` 를 직접 수정한다.
    """
    sr.ref_only = [n for n in sr.ref_only
                   if n in sr.slots and sr.slots[n].ref_images]
    sr.val_only = [n for n in sr.val_only
                   if n in sr.slots and sr.slots[n].val_images]


def iter_in_order(slots: Iterable[Slot], side: str = "ref") -> list[ImageItem]:
    """Slot 명 오름차순 → 파일명 오름차순으로 펼친 ImageItem 리스트."""
    items: list[ImageItem] = []
    for slot in sorted(slots, key=lambda s: s.name):
        src = slot.ref_images if side == "ref" else slot.val_images
        items.extend(src)
    return items
