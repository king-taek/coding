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
_LATEST_FILE = "latest.txt"
_WEIGHTS_EXT = ".pt"
_META_EXT = ".json"
_EVAL_EXT = ".jsonl"

# 모델 이름 ↔ 표시 정확도 매핑 정규식 (옛 날짜 이름 형식)
_ACC_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2}(?:_\d+)?)"
                     r"(?:_HitAt5_(?P<acc>\d{1,3}))?$")

# 신규 timestamp 이름: ``model_YYYY-MM-DD_HHMMSS`` (스펙 §8.2-c).
# 동일 초에 2 회 학습 시 ``_2``, ``_3`` 등이 붙을 수 있음.
_TIMESTAMP_RE = re.compile(
    r"^model_\d{4}-\d{2}-\d{2}_\d{6}(?:_\d+)?$"
)


def is_timestamp_name(name: str) -> bool:
    """신규 timestamp 형식의 모델 이름인지."""
    return bool(_TIMESTAMP_RE.match(name))


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
# Latest pointer — 마지막으로 학습된 모델 이름 (스펙 §8.2-c).
# ---------------------------------------------------------------------------
def _latest_file() -> Path:
    return paths.models_dir() / _LATEST_FILE


def get_latest() -> Optional[str]:
    """가장 최근 학습된 모델 이름. 없으면 None."""
    p = _latest_file()
    if not p.exists():
        return None
    try:
        v = p.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if not v or find(v) is None:
        return None
    return v


def set_latest(name: str) -> None:
    """학습 성공 시 호출. 첫 실행 시 ``active`` 가 비어있다면 자동 적용."""
    _latest_file().parent.mkdir(parents=True, exist_ok=True)
    _latest_file().write_text(name, encoding="utf-8")


def apply_latest_if_active_unset() -> None:
    """active.txt 가 비어있거나 basic 이면 latest 로 활성 모델을 설정."""
    if get_active() == BASIC and get_latest() is not None:
        set_active(get_latest() or BASIC)


# ---------------------------------------------------------------------------
# Name generation / rename
# ---------------------------------------------------------------------------
def make_new_name(today: Optional[datetime] = None) -> str:
    """신규 timestamp 형식의 모델 이름을 만든다 (스펙 §8.2-c).

    형식: ``model_YYYY-MM-DD_HHMMSS``. 같은 초에 두 번 학습되면 ``_2``, ``_3``
    으로 disambiguate. 옛 ``YYYY-MM-DD`` 모델은 그대로 보존되며 충돌하지 않는다.
    """
    today = today or datetime.now()
    base = "model_" + today.strftime("%Y-%m-%d_%H%M%S")
    existing = {info.name for info in list_models()}
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
    신규 timestamp 이름(``model_YYYY-MM-DD_HHMMSS``) 은 자동 리네임 대상에서
    제외 — 스펙 §8.3 ‘모든 학습 버전을 보존’ 원칙과 맞춤.
    """
    # 신규 timestamp 형식은 절대 이름을 바꾸지 않는다.
    if is_timestamp_name(info.name):
        return info

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

    # active 가 이 모델을 가리키고 있었는지 ‘이동 전에’ 미리 캡처
    was_active = (get_active() == info.name)

    # 파일 이동 — 셋 중 하나라도 실패하면 이미 이동된 것을 원복한다.
    # 그렇지 않으면 `list_models()` 가 새 이름으로 모델을 찾아오는데 meta/eval
    # 은 옛 이름에 남아 있는 분열 상태가 발생한다.
    moved: list[tuple[Path, Path]] = []
    try:
        info.weights_path.rename(target.weights_path)
        moved.append((info.weights_path, target.weights_path))
        if info.meta_path.exists():
            info.meta_path.rename(target.meta_path)
            moved.append((info.meta_path, target.meta_path))
        if info.eval_path.exists():
            info.eval_path.rename(target.eval_path)
            moved.append((info.eval_path, target.eval_path))
    except FileNotFoundError:
        if not moved:
            return info
        for src, dst in reversed(moved):
            try:
                dst.rename(src)
            except OSError:
                pass
        return info
    except OSError:
        for src, dst in reversed(moved):
            try:
                dst.rename(src)
            except OSError:
                pass
        return info

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

    if was_active:
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


# ---------------------------------------------------------------------------
# Export / import (zip 패키지) — #22
# ---------------------------------------------------------------------------
def export_model(name: str, dst_zip: Path) -> Path:
    """모델 1개 (.pt + .json + .jsonl) 를 zip 으로 묶어 ``dst_zip`` 에 저장."""
    import zipfile
    info = find(name)
    if info is None:
        raise FileNotFoundError(f"모델을 찾을 수 없습니다: {name}")
    dst_zip = Path(dst_zip)
    dst_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(dst_zip), "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(info.weights_path, arcname=info.weights_path.name)
        if info.meta_path.exists():
            zf.write(info.meta_path, arcname=info.meta_path.name)
        if info.eval_path.exists():
            zf.write(info.eval_path, arcname=info.eval_path.name)
    return dst_zip


def import_model(src_zip: Path) -> str:
    """``src_zip`` 의 모델 패키지를 현재 ``models_dir()`` 에 풀어넣는다.

    동일 이름이 이미 있으면 ``_2``, ``_3`` 으로 자동 부여.
    반환 = 실제로 들어간 모델 이름.
    """
    import zipfile
    src_zip = Path(src_zip)
    if not src_zip.exists():
        raise FileNotFoundError(str(src_zip))

    with zipfile.ZipFile(str(src_zip), "r") as zf:
        names = zf.namelist()
        pt = next((n for n in names if n.endswith(_WEIGHTS_EXT)), None)
        if pt is None:
            raise ValueError("zip 안에 가중치(.pt) 파일이 없습니다")

        base = Path(pt).stem
        # 충돌 회피
        existing = {info.name for info in list_models()}
        target_name = base
        n = 2
        while target_name in existing:
            target_name = f"{base}_{n}"
            n += 1

        target = _build_files(target_name)

        with zf.open(pt) as f:
            target.weights_path.write_bytes(f.read())
        meta_name = next((n for n in names if n.endswith(_META_EXT)), None)
        if meta_name is not None:
            data = json.loads(zf.read(meta_name).decode("utf-8"))
            data["name"] = target_name
            target.meta_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        eval_name = next((n for n in names if n.endswith(_EVAL_EXT)), None)
        if eval_name is not None:
            target.eval_path.parent.mkdir(parents=True, exist_ok=True)
            target.eval_path.write_bytes(zf.read(eval_name))

    return target_name
