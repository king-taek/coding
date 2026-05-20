"""오류 로그 기록 헬퍼.

사용자에게 실패 상세를 모두 보여주는 대신, 타임스탬프가 찍힌 ``.txt`` 파일을
캐시 루트 아래 ``오류 목록`` 폴더에 남긴다.  UI 는 단순히 "오류가 기록되었습니다"
만 안내하면 되도록 한다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import paths


def _error_dir() -> Path:
    """``<cache_root>/오류 목록`` 폴더 (없으면 생성)."""
    try:
        root = paths.cache_root()
    except Exception:
        root = Path.home() / ".aoi_verification_cache"
    d = root / "오류 목록"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_error(context: str, error: str) -> Path:
    """타임스탬프가 찍힌 오류 로그 파일을 작성하고 그 경로를 돌려준다.

    파일명은 ``YYYYMMDD_HHMMSS_<context>.txt`` 형태이며, 내용에는 타임스탬프와
    컨텍스트, 오류 텍스트가 들어간다.
    """
    now = datetime.now()
    ts_name = now.strftime("%Y%m%d_%H%M%S")
    ts_human = now.strftime("%Y-%m-%d %H:%M:%S")
    # 파일명에 쓸 수 없는 문자를 제거한 안전한 컨텍스트 토큰.
    safe = "".join(
        c if (c.isalnum() or c in ("-", "_")) else "_"
        for c in (context or "error")
    ).strip("_") or "error"
    path = _error_dir() / f"{ts_name}_{safe}.txt"
    body = (
        f"[시각] {ts_human}\n"
        f"[컨텍스트] {context}\n"
        f"[오류]\n{error}\n"
    )
    path.write_text(body, encoding="utf-8")
    return path
