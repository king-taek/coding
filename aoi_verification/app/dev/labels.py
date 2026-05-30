"""정답 라벨(ground-truth) 만들기 — 헤드리스 코어.

벤치마크의 '실제 정확도'(각 모델의 유사도 점수가 아닌 매칭 정답 기준)를 재려면
기준 사진마다 '정답'인 검증 사진을 지정한 라벨이 필요하다.  이 모듈은 그 라벨을
만들고/불러오고/검증하는 **Qt 비의존** 코어다 (GUI 는 ``ui.widgets.label_maker_dialog``
가 이 모델을 그대로 소비한다).

라벨 형식 — 벤치마크(``benchmark._labels_to_gt``)와 동일::

    {slot: {기준사진경로: [정답 검증사진경로, ...]}}

- 정답은 **여러 개**일 수 있다 (리스트 길이 ≥ 2).
- 정답이 **없을** 수도 있다 (빈 리스트 ``[]`` = '정답 없음'으로 검토 완료).
- 라벨에 **없는** 기준 사진은 '미검토' 로 보고 정확도 집계에서 제외된다.

경로는 모두 문자열(절대경로)로 저장해 결과(Results)의 키와 정확히 일치시킨다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 기준 사진 한 장을 식별하는 키 — (slot, 기준사진경로 문자열).
RefKey = Tuple[str, str]


# ---------------------------------------------------------------------------
# 라벨 JSON 입출력 + 정규화 + 통계
# ---------------------------------------------------------------------------
def normalize(labels: dict) -> Dict[str, Dict[str, List[str]]]:
    """임의의 라벨 dict 를 ``{slot: {ref: [val,...]}}`` 표준형으로.

    값은 항상 리스트(중복 제거·정렬)이며, 단일 문자열/튜플/세트도 허용한다.
    빈 값은 ``[]`` (정답 없음) 으로 보존한다."""
    out: Dict[str, Dict[str, List[str]]] = {}
    for slot, refmap in (labels or {}).items():
        s = str(slot)
        inner: Dict[str, List[str]] = {}
        for rp, vps in (refmap or {}).items():
            if vps is None:
                vals: List[str] = []
            elif isinstance(vps, (list, tuple, set)):
                vals = sorted({str(x) for x in vps})
            else:
                vals = [str(vps)]
            inner[str(rp)] = vals
        out[s] = inner
    return out


def load(path) -> Dict[str, Dict[str, List[str]]]:
    """라벨 JSON 로드 → 표준형.  없거나 손상되면 빈 dict."""
    p = Path(path)
    try:
        if not p.exists() or p.stat().st_size == 0:
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return normalize(data)
    except Exception:
        return {}
    return {}


def save(path, labels: dict) -> Path:
    """표준형으로 정규화해 JSON(UTF-8, 들여쓰기) 으로 저장.  경로 반환."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(normalize(labels), ensure_ascii=False, indent=2)
    tmp = p.parent / (p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)
    return p


def stats(labels: dict) -> Dict[str, int]:
    """라벨 통계 — refs(전체), labeled(정답 ≥1), none(정답 0), multi(정답 ≥2)."""
    norm = normalize(labels)
    refs = labeled = none = multi = 0
    for refmap in norm.values():
        for vals in refmap.values():
            refs += 1
            if len(vals) == 0:
                none += 1
            else:
                labeled += 1
                if len(vals) >= 2:
                    multi += 1
    return {"refs": refs, "labeled": labeled, "none": none, "multi": multi}


def make_template(ref_root, val_root, *, max_slots: int = 0,
                  max_images_per_side: int = 0) -> Dict[str, Dict[str, List[str]]]:
    """기준/검증 폴더를 스캔해 **빈 정답** 템플릿을 만든다.

    모든 (slot, 기준사진) 을 키로 두고 값은 ``[]`` (채워 넣을 자리).  헤드리스에서
    이 템플릿을 저장한 뒤 사용자가 정답 검증사진 경로를 채우면 된다.  반환된
    템플릿은 그대로 ``[]`` 이므로 '정답 없음' 과 형식이 같다 — 채우는 것은 사용자 몫.
    """
    from . import benchmark as _bm
    ds = _bm.build_dataset(ref_root, val_root, max_slots=max_slots,
                           max_images_per_side=max_images_per_side)
    out: Dict[str, Dict[str, List[str]]] = {}
    for slot, refs, _vals in ds.tasks:
        inner: Dict[str, List[str]] = {}
        for r in refs:
            inner[str(r.path)] = []
        out[slot] = inner
    return out


def order_by_similarity(ref_item, val_items, *, cfg=None) -> list:
    """후보 검증사진을 기준과의 **고전(CPU) 유사도 내림차순**으로 정렬해 돌려준다.

    라벨 작업을 빠르게 하기 위한 **표시 순서일 뿐** 정답 판정과는 무관하다.
    torch/가속 장치가 없어도 동작한다(pHash+ORB+SSIM).  실패하면 파일명순 폴백."""
    vals = list(val_items)
    try:
        from ..workers.matcher import score_ref_classical
        cands = score_ref_classical(ref_item, vals, threshold=0.0, cfg=cfg)
        rank = {str(c.item.path): i for i, c in enumerate(cands)}
        # 점수 산출된 후보 먼저(점수순), 나머지는 파일명순으로 뒤에.
        vals.sort(key=lambda v: (rank.get(str(v.path), 10 ** 9), str(v.path)))
        return vals
    except Exception:
        return sorted(vals, key=lambda v: str(v.path))


# ---------------------------------------------------------------------------
# 라벨 만들기 모델 — GUI/CLI 가 공유하는 선택 상태기계 (Qt 비의존)
# ---------------------------------------------------------------------------
class LabelMakerModel:
    """기준 사진을 하나씩 순회하며 정답 검증사진을 토글하는 상태기계.

    ``tasks`` = ``[(slot, refs[ImageItem], vals[ImageItem]), ...]``.  슬롯별 후보는
    필요할 때 ``order_by_similarity`` 로 정렬(메모이즈)한다.  선택 상태는 메모리에만
    있고, ``to_labels()`` 로 라벨 dict 를 만들어 저장한다.
    """

    def __init__(self, tasks, *, cfg=None) -> None:
        self._cfg = cfg
        self._refs: List[Tuple[str, object]] = []
        self._vals_by_slot: Dict[str, list] = {}
        for slot, refs, vals in tasks:
            self._vals_by_slot[slot] = list(vals)
            for r in refs:
                self._refs.append((slot, r))
        self._idx = 0
        self._answers: Dict[RefKey, set] = {}
        self._reviewed: set = set()
        self._ordered_cache: Dict[str, list] = {}
        # 후보 표시 순서 — "name"(즉시) 기본, "sim"(고전 유사도순, 느릴 수 있음).
        self._ordering = "name"
        self.dirty = False

    def set_ordering(self, mode: str) -> None:
        self._ordering = "sim" if str(mode) == "sim" else "name"

    # -- 순회 -----------------------------------------------------------
    def count(self) -> int:
        return len(self._refs)

    def index(self) -> int:
        return self._idx

    def goto(self, i: int) -> None:
        if self._refs:
            self._idx = max(0, min(int(i), len(self._refs) - 1))

    def next(self) -> None:
        self.goto(self._idx + 1)

    def prev(self) -> None:
        self.goto(self._idx - 1)

    def current(self):
        """(slot, ref_item) 또는 None(빈 데이터셋)."""
        if not self._refs:
            return None
        return self._refs[self._idx]

    def current_key(self) -> Optional[RefKey]:
        cur = self.current()
        if cur is None:
            return None
        slot, ref = cur
        return (slot, str(ref.path))

    def current_vals(self) -> list:
        """현재 기준의 슬롯 후보 — 유사도순 정렬(메모이즈)."""
        cur = self.current()
        if cur is None:
            return []
        slot, ref = cur
        base = self._vals_by_slot.get(slot, [])
        if self._ordering != "sim":
            return sorted(base, key=lambda v: str(v.path))
        cache_key = f"{slot}|{str(ref.path)}"
        cached = self._ordered_cache.get(cache_key)
        if cached is None:
            cached = order_by_similarity(ref, base, cfg=self._cfg)
            self._ordered_cache[cache_key] = cached
        return cached

    # -- 선택 -----------------------------------------------------------
    def toggle(self, val_path: str) -> bool:
        """현재 기준의 정답 후보를 토글.  반환 = 토글 후 선택 여부."""
        key = self.current_key()
        if key is None:
            return False
        vp = str(val_path)
        s = self._answers.setdefault(key, set())
        if vp in s:
            s.discard(vp)
            on = False
        else:
            s.add(vp)
            on = True
        self._reviewed.add(key)
        self.dirty = True
        return on

    def set_none(self) -> None:
        """현재 기준을 '정답 없음' 으로 검토 완료(선택 비우기)."""
        key = self.current_key()
        if key is None:
            return
        self._answers[key] = set()
        self._reviewed.add(key)
        self.dirty = True

    def is_selected(self, val_path: str) -> bool:
        key = self.current_key()
        return key is not None and str(val_path) in self._answers.get(key, set())

    def selected(self) -> set:
        key = self.current_key()
        return set(self._answers.get(key, set())) if key is not None else set()

    def is_reviewed(self) -> bool:
        key = self.current_key()
        return key is not None and key in self._reviewed

    # -- 라벨 입출력 ----------------------------------------------------
    def load_labels(self, labels: dict) -> int:
        """기존 라벨을 현재 데이터셋의 기준에 매칭해 적재.  적재된 ref 수 반환."""
        norm = normalize(labels)
        known = {(slot, str(r.path)) for slot, r in self._refs}
        n = 0
        for slot, refmap in norm.items():
            for rp, vals in refmap.items():
                key = (slot, rp)
                if key in known:
                    self._answers[key] = set(vals)
                    self._reviewed.add(key)
                    n += 1
        self.dirty = False
        return n

    def to_labels(self) -> Dict[str, Dict[str, List[str]]]:
        """검토 완료된 기준만 라벨 dict 로.  '정답 없음' 은 ``[]`` 로 기록."""
        out: Dict[str, Dict[str, List[str]]] = {}
        for (slot, rp) in sorted(self._reviewed):
            out.setdefault(slot, {})[rp] = sorted(self._answers.get((slot, rp), set()))
        return out

    def stats(self) -> Dict[str, int]:
        st = stats(self.to_labels())
        st["total"] = self.count()
        st["unreviewed"] = self.count() - len(self._reviewed)
        return st
