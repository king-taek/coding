"""더블클릭으로 바로 실행 — TYW 맵(Die 표시 · Conder 좌표 차이) 진단.

경로를 하드코딩했다.  결과 ``wafer_map_진단_<시각>.txt`` 는 바탕화면에 생긴다.
내용은 :mod:`diagnose_wafer_map_live` 와 같다(읽기 전용).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import diagnose_wafer_map_live as d     # noqa: E402

TARGET = r"M:\AOI-9\Scanresult\2D@R2-15966TA0-BW2_0859654PD-0C\6321\TYW"
OUT_DIR = str(Path.home() / "Desktop")

if __name__ == "__main__":
    try:
        d.main([TARGET, "--out", OUT_DIR])
    finally:
        input("끝났습니다. 바탕화면의 wafer_map_진단_*.txt 를 올려 주세요. (Enter 로 닫기)")
