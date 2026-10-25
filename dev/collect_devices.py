"""여러 디바이스의 map·die·결함 좌표 원재료를 zip 하나로 모은다(읽기 전용, 더블클릭).

2026-10-25 현택님이 준 경로(TB500 2건 · T254 2건 · TI 2건 · QORVO 2건).  각 쌍은
Conder scan 폴더(LIVE 파일명 = 장비 정답) ↔ Camtek Scanresult 폴더다.  모으는 방식은
``collect_avago.collect`` 를 그대로 쓴다: 사진은 이름만, zip·30 MB 초과 파일은 뺀다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect_avago import collect  # noqa: E402

_CONDER = r"\\k5cifsn2\k5tsvdata$\1. Conder Scan"

FOLDERS = [
    # TB500 SGG — REVERSE ETCH
    _CONDER + r"\531. NVIDIA INT\143. ASTB500-SM822-8MAZ\1400. E4N789.S1V DCC20 (SGG)\1. REVERSE ETCH",
    r"V:\AOI-16\Scanresult\R_TB500 LIVE_0860154PD_R-ETCH\SETUP\SGG 1018S",
    # TB500 KUH — BS CUP
    _CONDER + r"\531. NVIDIA INT\134. ASTB500-SP822-8MAY\1347. E3P676.C0W DCC20 (KUH)\2. BS CUP",
    r"X:\AOI-3\Scanresult\R_ TB500_0860154PD_BS UBM\SETUP\KUH-1018N",
    # T254 TSC — RDL1 (KLA)
    _CONDER + r"\531. NVIDIA INT\113. 4DT-T254-H-M1\414. T254.INT406 (TSC)\4. RDL1_재리뷰",
    r"T:\2026-10-17\T254.INT406@6323",
    # T254 TSC — FS UBM
    _CONDER + r"\531. NVIDIA INT\113. 4DT-T254-H-M1\414. T254.INT406 (TSC)\5. FS UBM",
    r"X:\AOI-3\Scanresult\R_T254 LIVE_0858711PD_FS UBM\6324\TSC-FS",
    # TI CCG
    _CONDER + r"\672. TI DALLAS\78. 3L781987B17-WUP\106. 6501169AK5 (CCG)\1. FVI\1. OR",
    r"V:\AOI-12\Scanresult\2D@R2-3L781987B17-WUP_0858675PD-0B\6412\CCG",
    # TI CCB
    _CONDER + r"\672. TI DALLAS\78. 3L781987B17-WUP\100. 6501165AK5 (CCB)\1. FVI",
    r"X:\AOI-7\Scanresult\2D@R2-3L781987B17-WUP_0858675PD-0B\6412\CCB",
    # QORVO CTX — FVI (Camtek 레시피 3개)
    _CONDER + r"\575. QORVO US\44. W97218SGZ4BK16\75. 6H7ENK (CTX)\2. FVI",
    r"M:\AOI-9\Scanresult\2D@R2-W97218SGB1K16_0860463PD-0A\6321\CTK SRD",
    r"M:\AOI-9\Scanresult\2D@R2-W97218SGB1K16_0860463PD-0A\6321\CTK",
    r"M:\AOI-9\Scanresult\2D@R2-W97218SGB1K16_0860463PD-0A\6321\CTK EDGE",
    # QORVO CTX — UBM
    _CONDER + r"\575. QORVO US\44. W97218SGZ4BK16\75. 6H7ENK (CTX)\1. UBM",
    r"V:\AOI-12\Scanresult\2D@RE-W97218SGB1K16_0860463PD-0A\6412\CTK",
]


def main() -> int:
    out = Path.home() / "Desktop"
    print(f"폴더 {len(FOLDERS)}개를 모읍니다(네트워크라 몇 분 걸릴 수 있습니다)...")
    dst = collect([Path(f) for f in FOLDERS], out)
    print(f"만들었습니다: {dst}\n이 zip 을 대화에 올려 주세요.")
    try:
        os.startfile(str(out))                    # type: ignore[attr-defined]  (Windows)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    code = main()
    input("엔터를 누르면 닫힙니다.")
    sys.exit(code)
