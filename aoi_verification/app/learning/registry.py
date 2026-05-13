"""모델 파일/메타 레지스트리.

- 디스크에서 모델 목록 조회
- ``active.txt`` 로 현재 사용 모델 추적 (``basic`` 또는 모델 이름)
- 신규 모델 이름 생성 (``YYYY-MM-DD`` / 동일 날짜는 ``_2`` …)
- 정확도 반영 리네임 (`{date}` → `{date}_HitAt5_{n}`)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from ..utils import paths


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASIC = "basic"             # 학습 모델 미사용 (기본 탐지 모드) 식별자
_ACTIVE_FILE = "active.txt"
_WEIGHTS_EXT = ".pt"
_META_EXT = ".json"
_EVAL_EXT = ".jsonl"

# 모델 이름 ↔ 표시 정확도 매핑 정규식
_ACC_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2}(?:_\d+)?)"
                     r"(?:_HitAt5_(?P<acc>\d{1,3}))?$")


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------
@dataclass
class ModelInfo:
    name: str
    weights_path: Path
    meta_path: Path
    eval_path: Path
    meta: dict = field(default_factory=dict)

    @property
    def base_date(self) -> str:
        """이름의 날짜(+ '_N') 부분만."""
        m = _ACC_RE.match(self.name)
        return m.group("date") if m else self.name

    @property
    def accuracy_pct(self) -> Optional[int]:
        """파일명에 표기된 Hit@5 백분율 (없으면 None)."""
        m = _ACC_RE.match(self.name)
        if not m or not m.group("acc"):
            return None
        try:
            return int(m.group("acc"))
        except ValueError:
            return None

    @property
    def num_evaluations(self) -> int:
        return int(self.meta.get("num_evaluations", 0))

    @property
    def num_train_pairs(self) -> int:
        return int(self.meta.get("num_train_pairs", 0))


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------
def list_models() -> list[ModelInfo]:
    out: list[ModelInfo] = []
    for pt in sorted(paths.models_dir().glob(f"*{_WEIGHTS_EXT}")):
        name = pt.stem
        meta_path = pt.with_suffix(_META_EXT)
        eval_path = paths.evaluations_dir() / f"{name}{_EVAL_EXT}"
        meta: dict = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        out.append(ModelInfo(
            name=name, weights_path=pt, meta_path=meta_path,
            eval_path=eval_path, meta=meta,
        ))
    # 최신 (이름이 큰 것) 이 위로
    out.sort(key=lambda m: m.name, reverse=True)
    return out


def find(name: str) -> Optional[ModelInfo]:
    for info in list_models():
        if info.name == name:
            return info
    return None


# ---------------------------------------------------------------------------
# Active model pointer
# ---------------------------------------------------------------------------
def _active_file() -> Path:
    return paths.models_dir() / _ACTIVE_FILE


def get_active() -> str:
    p = _active_file()
    if not p.exists():
        return BASIC
    try:
        v = p.read_text(encoding="utf-8").strip()
    except Exception:
        return BASIC
    if v == BASIC or not v:
        return BASIC
    # 파일 실제 존재 검증 — 없으면 basic 으로 fallback
    if find(v) is None:
        return BASIC
    return v


def set_active(name: str) -> None:
    if name != BASIC and find(name) is None:
        name = BASIC
    _active_file().write_text(name, encoding="utf-8")


# ---------------------------------------------------------------------------
# Name generation / rename
# ---------------------------------------------------------------------------
def make_new_name(today: Optional[datetime] = None) -> str:
    """오늘 날짜 기준의 새 모델 이름을 만든다 (동일 날짜는 ``_2`` …)."""
    today = today or datetime.now()
    base = today.strftime("%Y-%m-%d")
    existing = {info.base_date for info in list_models()}
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def _build_files(name: str) -> ModelInfo:
    return ModelInfo(
        name=name,
        weights_path=paths.models_dir() / f"{name}{_WEIGHTS_EXT}",
        meta_path=paths.models_dir() / f"{name}{_META_EXT}",
        eval_path=paths.evaluations_dir() / f"{name}{_EVAL_EXT}",
    )


def rename_with_accuracy(info: ModelInfo, hit_at_5_pct: int) -> ModelInfo:
    """모델 파일/메타/평가 로그/active.txt 를 정확도가 표기된 이름으로 일괄 변경.

    이미 동일한 정확도가 붙어 있으면 작업 없이 그대로 반환.
    """
    base = info.base_date
    new_name = f"{base}_HitAt5_{int(round(hit_at_5_pct))}"
    if new_name == info.name:
        return info

    target = _build_files(new_name)

    # 충돌 회피 — 동일 새 이름이 이미 있으면 _2/_3 처리
    suffix = 2
    while target.weights_path.exists() or target.meta_path.exists():
        target = _build_files(f"{new_name}_{suffix}")
        suffix += 1

    # 파일 이동
    try:
        info.weights_path.rename(target.weights_path)
    except FileNotFoundError:
        return info
    if info.meta_path.exists():
        info.meta_path.rename(target.meta_path)
    if info.eval_path.exists():
        info.eval_path.rename(target.eval_path)

    # meta 내부의 name 도 갱신
    meta = info.meta.copy()
    meta["name"] = target.name
    try:
        target.meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    # active 가 이 모델을 가리키고 있었다면 함께 갱신
    if get_active() == info.name:
        set_active(target.name)

    target.meta = meta
    return target


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
def delete_model(name: str) -> None:
    info = find(name)
    if info is None:
        return
    for p in (info.weights_path, info.meta_path, info.eval_path):
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass
    if get_active() == name:
        set_active(BASIC)


# ---------------------------------------------------------------------------
# Meta helpers
# ---------------------------------------------------------------------------
def write_meta(info: ModelInfo, meta: dict) -> None:
    meta = dict(meta)
    meta.setdefault("name", info.name)
    info.meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    info.meta = meta
