"""QORVO CTX UBM 의 Camtek 원재료를 zip 으로 모은다(읽기 전용, 더블클릭).

2026-10-25 처음 준 Camtek 폴더(``…\\6412\\CTK``)에는 SQ62G 웨이퍼만 있어 Conder 의
SQ67P(LOT 6H7ENK) 와 짝이 안 맞았다 → 현택님이 준 SQ67P 경로(AOI-11 ``CTX``)로 다시 모은다.
거르는 규칙(앱이 안 읽는 파일 · die 맵 3장)은 :mod:`collect_devices` 를 그대로 쓴다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_avago  # noqa: E402
import collect_devices  # noqa: E402  (import 만으로 거르는 규칙이 걸린다)

FOLDERS = [
    collect_devices._CONDER + r"\575. QORVO US\44. W97218SGZ4BK16\75. 6H7ENK (CTX)\1. UBM",
    r"V:\AOI-11\Scanresult\2D@RE-W97218SGB1K16_0860463PD-0A\6412\CTX",
]


def main() -> int:
    out = Path.home() / "Desktop" / "좌표대조_디바이스별"
    out.mkdir(parents=True, exist_ok=True)
    print("QORVO CTX — UBM 모으는 중...")
    dst = collect_avago.collect([Path(f) for f in FOLDERS], out)
    collect_devices._size_report(dst)
    print(f"\n만들었습니다: {dst}  {dst.stat().st_size / 1e6:.1f} MB\nzip 을 대화에 올려 주세요.")
    try:
        os.startfile(str(out))                    # type: ignore[attr-defined]  (Windows)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    code = main()
    input("엔터를 누르면 닫힙니다.")
    sys.exit(code)
