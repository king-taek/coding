"""Slot / ImageItem 데이터 클래스 및 폴더 스캔 헬퍼."""

from __future__ import annotations

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
    if not folder.exists() or not folder.is_dir():
        return []
    out: list[Path] = []
    for p in folder.iterdir():
        if p.is_file() and config.CONFIG.is_image(p.name):
            out.append(p)
    out.sort(key=lambda p: p.name.lower())
    return out


def _enum_slot_dirs(root: Path) -> dict[str, Path]:
    """root/*/ 들 중 폴더만 골라 슬롯명 → 경로 매핑."""
    if not root.exists():
        return {}
    out: dict[str, Path] = {}
    for child in root.iterdir():
        if child.is_dir():
            out[child.name] = child
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
        ref_d = ref_dirs.get(name)
        val_d = val_dirs.get(name)
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
        slots[name] = Slot(name=name, ref_images=ref_imgs, val_images=val_imgs)

    return ScanResult(slots=slots, ref_only=ref_only, val_only=val_only)


def iter_in_order(slots: Iterable[Slot], side: str = "ref") -> list[ImageItem]:
    """Slot 명 오름차순 → 파일명 오름차순으로 펼친 ImageItem 리스트."""
    items: list[ImageItem] = []
    for slot in sorted(slots, key=lambda s: s.name):
        src = slot.ref_images if side == "ref" else slot.val_images
        items.extend(src)
    return items
