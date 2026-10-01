"""매칭 결과 데이터 클래스."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# `FinalResult.mode` 값 — Defect 추출(매칭 없이 한쪽 폴더에서 고른 사진만 엑셀로).
# 이때 ``unmatched_refs`` 가 고른 사진 목록이고 ``matches`` 는 비어 있다.
EXTRACT_MODE = "extract"
# AVAGO 재리뷰 — 1차 리뷰 맵의 Reject die 사진을 빼고 다시 본 결과.  ``unmatched_refs`` 가
# 재리뷰한 사진 전부(``note`` = :data:`VERDICT_GOOD`/:data:`VERDICT_REJECT`)이고
# ``rereview`` 가 웨이퍼별 계획(:class:`~..coords.rereview.WaferPlan`)이다.
REREVIEW_MODE = "rereview"
VERDICT_GOOD = "Good"
VERDICT_REJECT = "Reject"


@dataclass
class MatchResult:
    """기준 사진 ↔ 검증 사진 1:1 매칭 한 줄."""
    slot: str
    ref_path: Path        # 기준 쪽 사진
    val_path: Path        # 검증 쪽 사진
    score: float

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.slot, self.ref_path.name, self.val_path.name)


@dataclass
class MissEntry:
    """매칭되지 못해 상대 장비의 미탐(놓침)으로 기록되는 항목."""
    slot: str
    side: str            # "ref" or "val" (어느 장비에서 봤는데 상대가 못 봤는지)
    path: Path
    note: str = ""


@dataclass
class FinalResult:
    """엑셀 저장으로 전달되는 최종 결과 묶음."""
    mode: str                                            # "single" | "cross" | EXTRACT_MODE
    ref_machine: str                                     # 예) "1호기"
    val_machine: str                                     # 예) "3호기"
    matches: list[MatchResult] = field(default_factory=list)
    slot_only_ref: list[str] = field(default_factory=list)
    slot_only_val: list[str] = field(default_factory=list)
    # Stage 2 에서 매칭을 찾지 못한 (Skip + No-match) 기준 사진들 — 엑셀에
    # ‘기준 이미지 + 빨간 파일명’ 행으로 함께 표기 (#7).
    unmatched_refs: list[MissEntry] = field(default_factory=list)
    # KLA 장비 사용 시: {slot명(WaferID) → KLA 하위폴더명}.  엑셀 B열에 slot명 아래
    # 회색 글씨로 KLA 폴더명을 함께 표기한다.
    kla_folders: dict[str, str] = field(default_factory=dict)
    # {slot명 → 카세트 슬롯 번호(`WaferInfo.ini` 의 `ActiveSlot`)}.  엑셀 B열에 slot명
    # 아래 `(#6)` 으로 함께 표기한다.  못 읽은 슬롯은 아예 들어오지 않는다(표기 생략).
    slot_numbers: dict[str, str] = field(default_factory=dict)
    # Wafer map 용 — {slot명 → (기준 사진 경로들, 검증 사진 경로들)}.  스캔 결과에서
    # 옮겨 담는다(결과 화면·엑셀 시트가 같은 목록을 본다).  매치 여부는 ``matches`` 로.
    slot_images: dict[str, tuple[list[Path], list[Path]]] = field(default_factory=dict)
    # AVAGO 재리뷰 전용 — {slot명 → WaferPlan}.  다른 모드에서는 비어 있다.
    rereview: dict = field(default_factory=dict)

    def rereview_rejects(self, slot: str) -> list[Path]:
        """재리뷰에서 Reject 로 고른 사진 — 결과 화면·엑셀이 같은 목록을 센다."""
        return [u.path for u in self.unmatched_refs
                if u.slot == slot and u.note == VERDICT_REJECT]


def extract_result(machine: str, picked: dict, *, kla_folders=None,
                   slot_numbers=None) -> FinalResult:
    """Defect 추출의 엑셀 입력 — ``picked`` : {slot명 → 고른 사진 경로들}.

    고른 사진은 ``unmatched_refs`` 로 싣는다.  exporter 가 미매칭 행과 **같은 모양**
    (C=사진, D=파일명·계측·좌표 글자)으로 적게 하려는 것이고(사용자 결정), '미매칭'
    표시는 ``mode`` 를 보고 exporter 가 뺀다.  Wafer map 에는 **고른 사진만** 찍힌다."""
    rows: list[MissEntry] = []
    images: dict[str, tuple[list[Path], list[Path]]] = {}
    for slot in sorted(picked):
        paths = [Path(p) for p in picked[slot]]
        if not paths:
            continue
        rows += [MissEntry(slot=slot, side="ref", path=p) for p in paths]
        images[slot] = (paths, [])
    return FinalResult(mode=EXTRACT_MODE, ref_machine=machine, val_machine="",
                       unmatched_refs=rows,
                       kla_folders=dict(kla_folders or {}),
                       slot_numbers=dict(slot_numbers or {}),
                       slot_images=images)


def rereview_result(machine: str, plans: dict, rejects: dict, goods: dict, *,
                    slot_numbers=None) -> FinalResult:
    """AVAGO 재리뷰의 결과 — ``rejects``/``goods`` : {slot명 → 사진 경로들}.

    재리뷰한 사진은 **전부** 행으로 싣는다(사용자 결정 — 엑셀에 판정 열).  Reject 를
    먼저, 그 안에서 파일명 순."""
    rows: list[MissEntry] = []
    images: dict[str, tuple[list[Path], list[Path]]] = {}
    for slot in sorted(plans):
        rej = sorted((Path(p) for p in rejects.get(slot, ())), key=lambda p: p.name.lower())
        good = sorted((Path(p) for p in goods.get(slot, ())), key=lambda p: p.name.lower())
        rows += [MissEntry(slot=slot, side="ref", path=p, note=VERDICT_REJECT) for p in rej]
        rows += [MissEntry(slot=slot, side="ref", path=p, note=VERDICT_GOOD) for p in good]
        images[slot] = (rej + good, [])
    return FinalResult(mode=REREVIEW_MODE, ref_machine=machine, val_machine="",
                       unmatched_refs=rows, slot_numbers=dict(slot_numbers or {}),
                       slot_images=images, rereview=dict(plans))
