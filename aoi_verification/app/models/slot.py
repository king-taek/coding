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
def is_ignored_name(name: str) -> bool:
    """파일명에 점으로 구분된 't' 토큰이 있으면 무시 대상.

    예: ``-86955.68631.t.1.jpg`` → 확장자 제거한 stem ``-86955.68631.t.1`` 을
    ``.`` 으로 나눈 토큰 ``[-86955, 68631, t, 1]`` 에 정확히 ``t`` 가 있으므로
    무시한다 (썸네일 생성·매칭 등 모든 단계에서 처음부터 배제).
    """
    stem = name.rsplit(".", 1)[0]        # 확장자 한 단계 제거
    return "t" in stem.split(".")


def _list_images(folder: Path) -> list[Path]:
    """폴더 내 이미지 파일 목록.

    ``os.scandir`` 의 캐시된 ``DirEntry.is_file()`` 를 써서 파일당 별도
    ``stat()`` 시스템콜을 피한다 — 폴더에 수만 장이 있어도 빠르게 열거 (#3).

    점 토큰 't' 가 포함된 파일 (예: ``*.t.1.jpg``) 은 ``is_ignored_name`` 으로
    처음부터 건너뛴다 — 열거가 유일한 소스라 썸네일도 만들어지지 않는다.
    """
    if not folder.exists() or not folder.is_dir():
        return []
    out: list[Path] = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                try:
                    if (entry.is_file()
                            and config.CONFIG.is_image(entry.name)
                            and not is_ignored_name(entry.name)):
                        out.append(Path(entry.path))
                except OSError:
                    continue
    except OSError:
        return []
    out.sort(key=lambda p: p.name.lower())
    return out


def list_slot_dirs(root: Path) -> dict[str, Path]:
    """root/*/ 들 중 폴더만 골라 슬롯명 → 경로 매핑 (공개 래퍼).

    Setup 단계의 '일부 슬롯만 진행' 옵션이 폴더를 스캔하기 위해 사용한다.
    """
    return _enum_slot_dirs(Path(root))


def _enum_slot_dirs(root: Path) -> dict[str, Path]:
    """root/*/ 들 중 폴더만 골라 슬롯명 → 경로 매핑."""
    if not root.exists():
        return {}
    out: dict[str, Path] = {}
    try:
        with os.scandir(root) as it:
            for entry in it:
                try:
                    if entry.is_dir():
                        out[entry.name] = Path(entry.path)
                except OSError:
                    continue
    except OSError:
        return {}
    return out


def scan(ref_root: Path, val_root: Path, progress=None) -> ScanResult:
    """기준/검증 두 최상위 폴더를 스캔하여 Slot 매핑을 만든다.

    ``progress(done, total)`` 콜백이 주어지면 폴더(slot) 하나를 열거할 때마다 호출해,
    NAS 처럼 느린 원격에서 폴더가 많아도 진행 개수를 실시간 표시할 수 있다."""
    ref_dirs = _enum_slot_dirs(Path(ref_root))
    val_dirs = _enum_slot_dirs(Path(val_root))

    all_names = sorted(set(ref_dirs.keys()) | set(val_dirs.keys()))
    total = len(all_names)
    slots: dict[str, Slot] = {}
    ref_only: list[str] = []
    val_only: list[str] = []

    for idx, name in enumerate(all_names, start=1):
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
        if progress is not None:
            try:
                progress(idx, total)
            except Exception:
                pass

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
