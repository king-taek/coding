"""장비가 쓴 INI/텍스트 파일을 **인코딩에 관계없이** 읽는다.

``read_text(encoding="utf-8", errors="replace")`` 로만 읽으면 UTF-16 파일이
**예외 없이** 깨진 문자열이 되어 정규식이 하나도 안 맞는다.  그러면 호출부는
'파일이 없다/내용이 없다' 와 구분할 수 없고, 실제로 그 때문에
``ColorImageGrabingInfo.ini`` 가 멀쩡히 있는데 좌표가 0건으로 나오는 사고가 났다.

(UTF-16LE 의 ``X=1`` 은 ``X\\x00=\\x001`` 로 디코드되는데, ``\\x00`` 은 유효한 UTF-8 이라
치환문자조차 안 남고 ``^(\\w+)\\s*=`` 같은 패턴이 조용히 빗나간다.)

BOM 으로 UTF-16 을 판별하고, 아니면 UTF-8(BOM 허용) → 그래도 안 되면 관대하게 읽는다.
전 구간 fail-safe — 못 읽으면 ``None``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

__all__ = ["read_ini_text", "decode_ini_bytes"]


def decode_ini_bytes(data: bytes) -> str:
    """INI 바이트 → 텍스트.  UTF-16(BOM) → UTF-8(BOM 허용) → 관대하게."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except ValueError:
            pass
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def read_ini_text(path: Path) -> Optional[str]:
    """INI 파일 → 텍스트.  못 읽으면 ``None``."""
    try:
        return decode_ini_bytes(path.read_bytes())
    except OSError:
        return None
