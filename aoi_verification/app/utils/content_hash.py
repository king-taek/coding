"""이미지 컨텐츠 기반 해시.

학습 데이터 / 평가 로그 / 캐시 키가 ‘절대 경로 + mtime’ 만 쓰면 폴더를 옮기는
순간 데이터가 무력화된다. 컨텐츠 자체에서 빠른 해시를 뽑아 path-기반 키를
보완한다.

전략 — 파일을 모두 읽지 않고도 ‘유일성에 가까운’ 키를 얻기 위해:
  1) 파일 사이즈 ``(...)``
  2) 앞 64 KB
  3) 뒤 64 KB
세 가지를 SHA1 으로 합친다. JPEG 의 경우 헤더+EOI 표식이 끝에 있으므로 위/아래
모두 보는 게 안전. 64 KB 한도라 큰 원본 이미지도 ms 단위.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional


_CHUNK = 64 * 1024     # 64 KB


def content_hash(path: os.PathLike[str] | str) -> str:
    """이미지 파일의 컨텐츠 해시 (16 진수, 40 자). 실패 시 빈 문자열."""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return ""

    h = hashlib.sha1()
    h.update(str(size).encode("utf-8"))
    try:
        with p.open("rb") as f:
            head = f.read(_CHUNK)
            h.update(head)
            if size > _CHUNK:
                # 끝 _CHUNK 만큼 다시 읽기
                f.seek(max(0, size - _CHUNK))
                tail = f.read(_CHUNK)
                h.update(tail)
    except OSError:
        return ""
    return h.hexdigest()


def safe_content_hash(path: os.PathLike[str] | str) -> Optional[str]:
    """예외 안전 wrapper — 실패 시 ``None``."""
    try:
        h = content_hash(path)
        return h if h else None
    except Exception:
        return None
