"""Slot / ImageItem 데이터 클래스 및 폴더 스캔 헬퍼."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

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
    """한 슬롯에 대응하는 기준/검증 이미지 목록.

    ``ref_dir``/``val_dir`` 는 원본 폴더 경로다 — **사진이 한 장도 없는 폴더**는 이미지
    경로에서 폴더를 역산할 수 없으므로, KLA 정보파일에서 WaferID 를 읽으려면 이 경로가 필요하다.
    """
    name: str
    ref_images: list[ImageItem] = field(default_factory=list)
    val_images: list[ImageItem] = field(default_factory=list)
    ref_dir: Path | None = None
    val_dir: Path | None = None

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
# 결함 사진이 아닌 웨이퍼 전경(매크로) 카메라 사진 — 슬롯마다 1장씩 들어 있고
# ColorImageGrabingInfo.ini 에 항목이 없어 좌표를 만들 수 없다.
# 예: ``CognexInSight17xx_Bottom_OnPal_Station1_Slot21.jpg``
_NON_DEFECT_PREFIXES = ("cognexinsight",)


def is_ignored_name(name: str) -> bool:
    """무시 대상 파일명인가 (썸네일 생성·매칭 등 모든 단계에서 처음부터 배제).

    거르는 것은 **결함 사진이 아닌 웨이퍼 전경 사진**(:data:`_NON_DEFECT_PREFIXES`)
    하나뿐이다 — 좌표가 없어 매칭에 쓸 수 없다.

    ⚠ 점으로 구분된 ``t`` 토큰(예: ``-86955.68631.t.1.jpg``)은 예전에 여기서 걸렀지만
    **지금은 거르지 않는다**(사용자 요청).  그 사진도 검증해야 할 결함 사진이다.
    좌표(INI 항목)가 없으면 매칭이 고전 유사도로 폴백하므로 열어 둬도 안전하다.
    """
    return name.lower().startswith(_NON_DEFECT_PREFIXES)


def _list_images(folder: Path) -> list[Path]:
    """폴더 내 이미지 파일 목록.

    ``os.scandir`` 의 캐시된 ``DirEntry.is_file()`` 를 써서 파일당 별도
    ``stat()`` 시스템콜을 피한다 — 폴더에 수만 장이 있어도 빠르게 열거 (#3).

    웨이퍼 전경 사진은 ``is_ignored_name`` 으로 처음부터 건너뛴다 — 열거가 유일한
    소스라 썸네일도 만들어지지 않는다.
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


def count_images(folder: Path) -> int:
    """폴더의 결함 사진 수 — **앱이 실제로 처리하는 수와 같다.**

    슬롯 선택 팝업이 'slot별로 몇 장인지' 를 보여주는 데 쓴다(사용자 요청).
    :func:`_list_images` 를 그대로 쓰므로 웨이퍼 전경 사진(:data:`_NON_DEFECT_PREFIXES`)
    은 빠진다 — 화면의 숫자와 검증이 실제로 도는 장수가 어긋나면 안 된다.

    ⚠ 이 함수는 폴더를 통째로 열거한다.  NAS 에서는 왕복이므로 **UI 스레드에서
    부르지 마라**(호출부는 워커에서 부른다: `widgets/slot_select_dialog._SlotCountScan`).
    """
    return len(_list_images(Path(folder)))


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
        slots[name] = Slot(name=name, ref_images=ref_imgs, val_images=val_imgs,
                           ref_dir=ref_d, val_dir=val_d)
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


def push_one_sided_to_unmatched(sr: ScanResult) -> None:
    """사진이 **한쪽에만** 있는 슬롯을 ``ref_only``/``val_only`` 로 옮겨 결과에 남긴다.

    KLA 정보파일로 짝은 찾았지만 한쪽 폴더에 사진이 0장이면 ``has_both`` 가 False 라
    ``common_slot_names`` 에도, ``ref_only``/``val_only`` 에도 없어 **결과에서 통째로
    사라진다**.  이를 ‘기준 전용/검증 전용’ 으로 되돌려 (병합된) slot명으로 보고되게 한다.

    양쪽 다 사진이 없는 슬롯은 검증할 것이 없으므로 그대로 둔다.
    ``ScanResult`` 를 직접 수정한다.
    """
    known = set(sr.ref_only) | set(sr.val_only)
    for name, slot in sr.slots.items():
        if name in known or slot.has_both:
            continue
        if slot.ref_images:
            sr.ref_only.append(name)
        elif slot.val_images:
            sr.val_only.append(name)


def _norm_key(s) -> str:
    return str(s).strip().upper()


def merge_unmatched_by_wafer_id(sr: ScanResult, wid_by_ref: dict,
                                wid_by_val: dict) -> list[tuple[str, str]]:
    """``ref_only``/``val_only`` 를 ‘폴더명 또는 WaferID’ 키 교집합으로 병합.

    각 폴더 키 = {폴더명, (있으면) WaferID}.  ref·val 키가 겹치면 같은 slot.
    병합 **slot명 = 교집합 키(WaferID 우선)** — KLA 가 기준/검증 어느 쪽이든 깔끔한
    WaferID 가 slot명이 된다(KLA 의 임의 폴더명이 slot명이 되는 것 방지).
    ``sr`` 를 직접 수정하고 짝지은 ``(ref, val)`` 목록 반환.  ``wid_by_*`` : {폴더명 → WaferID}."""
    wid_by_ref = wid_by_ref or {}
    wid_by_val = wid_by_val or {}

    def keyset(name, wid):
        ks = {_norm_key(name)}
        if wid:
            ks.add(_norm_key(wid))
        return ks

    val_keys = {v: keyset(v, wid_by_val.get(v)) for v in sr.val_only}
    paired: list[tuple[str, str]] = []
    used_val: set = set()
    for ref_name in list(sr.ref_only):
        rk = keyset(ref_name, wid_by_ref.get(ref_name))
        match_val = None
        slot_name = None
        for val_name in list(sr.val_only):
            if val_name in used_val:
                continue
            inter = rk & val_keys.get(val_name, set())
            if not inter:
                continue
            match_val = val_name
            # slot명 = 교집합 키 중 WaferID 우선(없으면 임의의 교집합 키).
            wids = {_norm_key(w) for w in
                    (wid_by_ref.get(ref_name), wid_by_val.get(val_name)) if w}
            pref = inter & wids
            slot_name = sorted(pref)[0] if pref else sorted(inter)[0]
            break
        if match_val is None:
            continue
        ref_slot = sr.slots.get(ref_name)
        vs = sr.slots.get(match_val)
        if ref_slot is None or vs is None:
            continue
        # slot명이 **무관한 기존 슬롯**과 겹치면 병합하지 않는다 — 겹친 채로 진행하면
        # 그 슬롯의 사진을 덮어써 웨이퍼가 조용히 사라진다.  두 폴더는 ref_only/val_only
        # 에 남아 수동 매핑으로 넘어간다(정확도 우선).
        existing = sr.slots.get(slot_name)
        if existing is not None and slot_name not in (ref_name, match_val):
            continue
        merged = existing or Slot(name=slot_name)
        merged.ref_images = [ImageItem(slot=slot_name, path=it.path, side="ref")
                             for it in ref_slot.ref_images]
        merged.val_images = [ImageItem(slot=slot_name, path=it.path, side="val")
                             for it in vs.val_images]
        # 원본 폴더 경로를 옮겨 둔다 — 사진 0장 폴더의 정보파일을 나중에도 찾을 수 있게.
        merged.ref_dir = ref_slot.ref_dir
        merged.val_dir = vs.val_dir
        if ref_name != slot_name:
            sr.slots.pop(ref_name, None)
        if match_val != slot_name:
            sr.slots.pop(match_val, None)
        sr.slots[slot_name] = merged
        used_val.add(match_val)
        for n in (ref_name, match_val, slot_name):
            if n in sr.ref_only:
                sr.ref_only.remove(n)
            if n in sr.val_only:
                sr.val_only.remove(n)
        paired.append((ref_name, match_val))
    return paired
