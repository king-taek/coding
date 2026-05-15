"""공용 디스크 캐시 — 절대경로/mtime/size_option 으로 키를 만든다.

원본 폴더(읽기 전용 네트워크 드라이브 가능성 있음) 에는 절대로 쓰지 않는다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import paths

SizeOption = Literal["thumb", "mid", "feature"]


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
    try:
        mtime = src.stat().st_mtime
    except OSError:
        mtime = 0.0
    key = _hash_key(str(src.resolve()), mtime, size_option, extra)

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
