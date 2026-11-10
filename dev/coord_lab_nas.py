"""더블클릭으로 바로 실행 — NAS 의 Scanresult · Conder Scan 을 돌며 좌표 가설을 채점하고 자료를 모은다.

내용은 :mod:`coord_lab` 의 ``--nas`` 와 같다(읽기 전용 · 사진은 이름만).  결과는 바탕화면에
``좌표실험_<시각>.txt/.json/.html`` 과, 이상 폴더가 있으면 ``…_수집.zip`` 으로 생긴다.

돌 곳을 직접 정하려면 :data:`ROOTS` 에 경로를 적는다(비워 두면 이 PC 에서 보이는
``AOI-*\\Scanresult`` 전부와 Conder Scan 을 찾는다).  경로에 'Conder' 가 들면 정답 쪽으로 쓴다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coord_lab  # noqa: E402

ROOTS: list[str] = [
    # r"V:\AOI-16\Scanresult",
    # r"\\k5cifsn2\k5tsvdata$\1. Conder Scan\531. NVIDIA INT",
]
MINUTES = 30        # 이 시간이 지나면 거기까지 돈 것으로 정리한다
PER_LOT = 3         # LOT 마다 뽑는 웨이퍼 수
LOTS = 5            # 한 폴더 아래 최근 LOT 몇 개까지

if __name__ == "__main__":
    out = Path.home() / "Desktop"
    try:
        coord_lab.main(["--nas", *ROOTS, "--minutes", str(MINUTES), "--per-lot", str(PER_LOT),
                        "--lots", str(LOTS), "--out", str(out)])
    finally:
        input("끝났습니다. 바탕화면의 좌표실험_* 파일(과 _수집.zip)을 올려 주세요. (Enter 로 닫기)")
