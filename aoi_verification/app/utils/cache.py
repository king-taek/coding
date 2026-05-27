"""공용 디스크 캐시 — 절대경로/mtime/size_option 으로 키를 만든다.

원본 폴더(읽기 전용 네트워크 드라이브 가능성 있음) 에는 절대로 쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import paths

SizeOption = Literal["thumb", "mid", "feature"]

# 원본 mtime 메모이즈 — NAS(느린 원격 디스크)에서 같은 파일을 thumb/mid/feature/
# embedding 등 여러 캐시가 각각 stat() 하던 왕복을 세션당 1회로 줄인다(#5).
# 런(검증 1회) 동안 원본은 불변이라고 가정한다.  세션 시작 시 reset 한다.
_mtime_cache: dict[str, float] = {}


def reset_mtime_cache() -> None:
    """세션(검증) 시작 시 호출 — 원본 mtime 메모이즈를 비운다."""
    _mtime_cache.clear()


def _abspath(src) -> str:
    # ``Path.resolve()`` 는 심볼릭/마운트 해석으로 NAS 왕복이 생길 수 있어, FS 를
    # 건드리지 않는 순수 문자열 ``abspath`` 를 캐시 키에 쓴다.
    return os.path.abspath(str(src))


def memo_mtime(src) -> float:
    """원본 mtime(메모이즈).  같은 경로는 세션 내 1회만 stat() 한다."""
    ap = _abspath(src)
    v = _mtime_cache.get(ap)
    if v is None:
        try:
            v = os.stat(ap).st_mtime
        except OSError:
            v = 0.0
        _mtime_cache[ap] = v
    return v


def _hash_key(absolute_path: str, mtime: float, size_option: SizeOption,
              extra: str = "") -> str:
    h = hashlib.sha1()
    h.update(absolute_path.encode("utf-8", errors="replace"))
    h.update(f"|{int(mtime)}|{size_option}|{extra}".encode("utf-8"))
    return h.hexdigest()


def cache_path(src: Path, size_option: SizeOption, *,
               extra: str = "") -> Path:
    """원본 이미지 파일에 대응되는 캐시 파일 경로 (없을 수도 있음).

    ``extra`` 는 같은 이미지에 대해 서로 다른 화질 티어를 캐시할 때 키를
    분기하기 위한 문자열이다 (예: ``"t180q75"``). 기본값은 빈 문자열로,
    기존 호출 형태와 호환된다.
    """
    ap = _abspath(src)
    mtime = memo_mtime(src)
    key = _hash_key(ap, mtime, size_option, extra)

    if size_option == "thumb":
        return paths.thumb_cache_dir() / f"{key}.jpg"
    if size_option == "mid":
        return paths.mid_cache_dir() / f"{key}.jpg"
    return paths.feature_cache_dir() / f"{key}.npz"


@dataclass(frozen=True)
class CacheKey:
    """디버그·로그용으로 캐시 키 정보를 모아 보관."""
    src: Path
    size_option: SizeOption
    cache_file: Path

    @classmethod
    def for_(cls, src: Path, size_option: SizeOption) -> "CacheKey":
        return cls(src=src, size_option=size_option,
                   cache_file=cache_path(src, size_option))


# ---------------------------------------------------------------------------
# 오래된 사진 캐시 정리 (TTL)
# ---------------------------------------------------------------------------
def prune_old_cache(max_age_days: float = 1.0) -> int:
    """썸네일/중간이미지 캐시 중 ``max_age_days`` 보다 오래된 ``*.jpg`` 를 삭제한다.

    디스크가 무한정 커지는 것을 막기 위한 단순 TTL 청소.  features/scores 캐시는
    재계산 비용이 커서 대상에서 제외한다.  읽기는 mtime 을 갱신하지 않으므로 매일
    쓰는 썸네일도 만료되면 삭제→다음 조회 시 저렴하게 재생성된다.

    삭제한 파일 수를 돌려준다.  개별 파일 오류는 무시(다른 프로세스가 사용 중일 수
    있음).
    """
    cutoff = time.time() - max(0.0, float(max_age_days)) * 86400.0
    removed = 0
    for cache_dir in (paths.thumb_cache_dir(), paths.mid_cache_dir()):
        try:
            with os.scandir(cache_dir) as it:
                for entry in it:
                    try:
                        if not entry.name.lower().endswith(".jpg"):
                            continue
                        if entry.stat().st_mtime < cutoff:
                            os.unlink(entry.path)
                            removed += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return removed
