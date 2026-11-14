"""Threshold 자료 수집 — Kendall · TB500 레시피의 검출 조건(Width·Area·Contrast)과 결함 bin 이름이
어느 파일에 있는지 찾으려고 NAS 의 Scanresult 원재료를 모은다(2026-11-13, 사용자 지시).

읽기 전용 · 표준 파이썬만 쓴다(앱 코드를 불러오지 않는다).  앱의 개발자 모드 버튼
(``ui/threshold_collect_tmp.py``)이 :func:`run` 을 부르고, 더블클릭으로도 돈다.

무엇을 모으나:
    · :data:`KNOWN_LOTS` — 장비 스캔 기록(AOI_capacity.csv, 2026-11-15)에서 고른 최근 Kendall · TB500 LOT.
      NAS 를 훑지 않고 ``<장비>\\Scanresult\\<Job>\\<Setup>\\<LOT>`` 로 바로 찾아간다
    · 그 Job · Setup · LOT 폴더 바로 아래 파일
    · LOT 마다 웨이퍼 :data:`WAFERS` 장의 폴더 안 파일(하위 폴더 2단계까지)
    · :data:`PAIR_LOTS` — 같은 웨이퍼(:data:`PAIR_WAFER`)를 Contrast 기준만 바꿔 스캔한 LOT 에서 그 웨이퍼를 먼저 모은다
      (미리보기의 '새로 빠짐' 예측을 실제 스캔과 맞춰 보는 자료, 2026-11-14 사용자 지정)
무엇을 버리나(확실히 필요 없는 것):
    · 사진 · 영상 · 압축 파일 — 사진은 **이름만** ``사진목록.txt`` 에 남긴다(LIVE 이름에 결함 이름이 든다)
    · :data:`FILE_MB` 를 넘는 파일 — 이름 · 크기만 ``파일목록.txt`` 에 남긴다
    · 내용이 똑같은 파일 — 한 번만 담고 ``중복목록.txt`` 에 적는다(레시피 사본이 웨이퍼마다 있다)
    · QORVO(2026-11-14 사용자 요청) — Conder 와 Scanresult 가 맵을 다르게 그리는 문제의 자료.
      Conder ``*QORVO*`` 아래 LOT 폴더의 맵 txt · csv · lot 와 웨이퍼 사진 **이름**, 그 Conder 맵의
      DEVICE 앞 :data:`QORVO_KEY_LEN` 글자가 Job 이름에 든 Scanresult LOT 의 **웨이퍼 전부**.
      스캔 기록에서 고른 QORVO Scanresult LOT(:data:`QORVO_KNOWN_LOTS`)은 Conder 보다 먼저 모은다.
    · :data:`CONDER_ROW_LOTS` — Conder LIVE 파일명 번호로 앱 row 를 확인할 Kendall LOT(맵 txt · lot · csv · 사진 이름).
    · :data:`KLA_PAIRS` — 같은 웨이퍼를 KLA 와 Camtek 이 같은 층에서 찍은 것(스캔 기록, 2026-11-15 사용자 요청).
      Camtek 웨이퍼 폴더와 KLA 웨이퍼 폴더(정보파일 · 사진 이름)를 ``KLA비교/<KLA LOT>/`` 아래 나란히 담는다.
결과: 바탕화면 ``Threshold수집_<시각>\\`` 폴더 안 zip.  :data:`PART_MB` 를 넘으면 나누되,
나눈 조각은 :data:`MIN_PART_MB` 이상이 되게 채운다(마지막 조각만 작을 수 있다).
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import zipfile
from datetime import datetime
from pathlib import Path

PRODUCTS = ("KENDALL", "TB500")
UNC_SHARES = [r"\\10.142.80.91\nas3-1"]
WAFER_MARK = ("ColorImageGrabingInfo.ini", "Params_WaferInfo.ini")
WAFERS = 2          # LOT 마다 웨이퍼 몇 장
MINUTES = 60        # 전체 수집 시간 상한 — 지나면 거기까지 모은 것으로 정리한다(2026-11-14 사용자 지정: 최대 1시간)
FILE_MB = 20        # 이보다 큰 파일은 이름만 남긴다
TOTAL_MB = 400      # 담은 원본이 이만큼 되면 그만 찾는다
PART_MB = 28          # 올릴 수 있는 한도가 30 MB 보다 작다(30 MB 조각 2개가 안 올라감, 사용자 2026-11-15)
MIN_PART_MB = 23
SKIP_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif",
            ".avi", ".mp4", ".wmv", ".zip", ".7z", ".rar", ".gz", ".cab"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif"}
# 같은 웨이퍼를 Contrast 기준(Avg24~28)만 바꿔 스캔한 LOT(2026-11-14 사용자 지정, 2026-11-15 스캔 기록으로 고침).
# 스캔 기록상 전부 웨이퍼 20234838EWG1(슬롯 18): Avg24 2309 → Avg26 644 → Avg27 353 → Avg28 164 개.
# 사용자가 준 AOI-04 'YDD' 에는 다른 웨이퍼(35174140EWD4 …)만 있어 같은 웨이퍼가 든 'YDD-No Image'(43211 개)로
# 바꾸고, 빠져 있던 AOI-03 Avg28 을 더했다.  이 드라이브가 없으면 UNC_SHARES 아래 같은 경로를 본다.
PAIR_WAFER = "20234838EWG1"
PAIR_LOTS = [
    r"I:\4F-AOI-05\Scanresult\R_TB500_LIVE_PI4 - Enhanced\Setup1\YDD_Contrast Avg28_AOI-5",
    r"I:\4F-AOI-05\Scanresult\R_TB500_LIVE_PI4 - Enhanced\Setup1\YDD-Contrast Avg24",
    r"I:\4F-AOI-05\Scanresult\R_TB500_LIVE_PI4 - Enhanced\Setup1\YDD-Contrast Avg26",
    r"I:\4F-AOI-05\Scanresult\R_TB500_LIVE_PI4 - Enhanced\Setup1\YDD-Contrast Avg27",
    r"I:\4F-AOI-04\Scanresult\R_TB500_LIVE_PI4 - Enhanced\Setup1\YDD-No Image",
    r"I:\4F-AOI-04\Scanresult\R_TB500_LIVE_PI4 - Enhanced\Setup1\YDD_Contrast Avg28_AOI-4",
    r"I:\4F-AOI-03\Scanresult\R_TB500_LIVE_PI4 - Enhanced\Setup1\YDD_Contrast Avg28_AOI-3",
]
# 장비 스캔 기록(AOI_capacity.csv, 2026-11-15 사용자 제공)에서 고른 LOT — (장비, Job, Setup, LOT).
# Camtek 장비 · Pass · 웨이퍼 5장 이상 · 이름에 TEST 없는 LOT 중 Job 마다 가장 최근 것, 최근 Job 순으로
# Kendall 20 · TB500 10(TB500 은 이미 모은 자료가 많다).  NAS 에서 지워졌으면 '못 찾음' 으로 남긴다.
KNOWN_LOTS = [
    # Kendall
    ('AOI-16', 'R_KENDALL_A0_PI5', 'SETUP', 'BAD-PIDS5'),  # 2026-11-13 25장
    ('AOI-1', '2D@RE-KENDALL_MEOL_0859275PD-0C', 'REVERSEETCH', '2DG-1113S'),  # 2026-11-13 12장
    ('AOI-13', 'R_KENDALL_A0_PI6_0126', 'SETUP', 'GVS-PIDS3'),  # 2026-11-13 27장
    ('AOI-15', 'R_KENDALL_A0_PI6_0223', 'SETUP', 'DDU-PIDS3'),  # 2026-11-13 25장
    ('AOI-16', 'R_KENDALL_A0_PI6_0126', 'SETUP', 'PAD-PIDS3'),  # 2026-11-13 25장
    ('AOI-21', 'R_Kendall A0_PI3', 'Setup1', 'CUV-PIDS9'),  # 2026-11-13 25장
    ('AOI-1', '2D@R2-KENDALL_MEOL_0859275PD_BS_CAMTEK', '6311', 'HNG 1112N'),  # 2026-11-13 6장
    ('AOI-7', '2D@R2-KENDALL_MEOL_0859275PD-0C', '6321', '2HG 1112N'),  # 2026-11-13 5장
    ('AOI-21', 'R_Kendall A0_PI4', 'Setup1', 'CUL-PIDS7'),  # 2026-11-12 48장
    ('AOI-15', 'R_KENDALL_A0_PI5', 'SETUP', 'AYV-PIDS5'),  # 2026-11-12 24장
    ('AOI-10', '2D@R2-KENDALL_MEOL_0859275PD-0C', '6321', '2CB 1111N'),  # 2026-11-11 7장
    ('AOI-13', 'R_KENDALL_A0_FS', 'SETUP', 'AWB-FS-DIA'),  # 2026-11-11 5장
    ('AOI-5', '2D@RE-KENDALL MEOL_0859275PD', '6412', 'ACF 1111D'),  # 2026-11-11 10장
    ('AOI-3', 'R_Kendall LIVE_0859275PD_R-Etch', 'MSFT Kendall', '2TL 1111D'),  # 2026-11-11 10장
    ('AOI-15', 'R_Kendall A0_0859275PD_REVERSE', 'SETUP', 'MCX-1110S'),  # 2026-11-10 6장
    ('AOI-5', 'R_MSFT KENDALL_0859275PD_REVERSE', 'R-Etch', 'L7G'),  # 2026-11-10 5장
    ('AOI-13', 'R_KENDALL_A0_PI5', 'SETUP', 'AYY-REWORK_1110D'),  # 2026-11-10 25장
    ('AOI-15', 'R_KENDALL_A0_PI3_0317', '6412', 'CUU-PIDS9'),  # 2026-11-10 25장
    ('AOI-13', 'R_KENDALL_A0_PI2', '6412', 'CTY-PIDS11'),  # 2026-11-09 25장
    ('AOI-15', 'R_MSFT_Kendall_0859275PD_BS-CAMTEK', 'SET UP', 'MBC-1107S 3D RE'),  # 2026-11-07 6장
    # TB500
    ('4F-AOI-05', 'TB500_RDL2 - Multi', 'Setup1', 'TNY'),  # 2026-11-13 25장
    ('AOI-13', 'R_TB500 LIVE_FS', '6324', 'LMW-DIA'),  # 2026-11-13 6장
    ('4F-AOI-01', '2D@RE-ASTB500-SP822-8MAY_0857984AY', '6412', 'GAV'),  # 2026-11-13 8장
    ('AOI-3', 'R_TB500 LIVE_FS', '6324', 'WGH-FS DIA'),  # 2026-11-13 6장
    ('4F-AOI-01', '2D@R2-ASTB500-SP822-8MAY_0857984AY', '6321', 'BPK'),  # 2026-11-13 8장
    ('4F-AOI-02', '2D@R2-ASTB500-SP822-8MAY_0857984AY', '6321', 'BPC'),  # 2026-11-13 7장
    ('AOI-17', 'TB500_RDL3 - Multi', 'Setup1', 'BTA-RDL3'),  # 2026-11-13 23장
    ('AOI-10', '2D@R2-ASTB500-SM822-8MAY_0860154PD_BS UBM', 'SETUP', 'YPH_1113S'),  # 2026-11-13 8장
    ('AOI-1', 'R_TB500 LIVE_0860154PD_BS UBM', 'SET UP', 'BTM-1113S'),  # 2026-11-13 8장
    ('4F-AOI-03', 'TB500_RDL2 - Multi', 'Setup1', 'HYP'),  # 2026-11-13 25장
]
# QORVO — Conder 맵 txt 가 사진 이름과 (col 1, row 2) 어긋나는지 다른 LOT 에서도 보려는 자료.
CONDER_ROOTS = [r"\\k5cifsn2\k5tsvdata$\1. Conder Scan"]
QORVO_NAME = "QORVO"     # Conder 고객 폴더 이름(예: 575. QORVO US)
QORVO_LOTS = 4           # Conder 장치 폴더마다 최근 LOT 몇 개
QORVO_JOB_LOTS = 3       # Scanresult Job 마다 최근 LOT 몇 개(웨이퍼는 전부)
QORVO_KEY_LEN = 8        # Conder 맵 DEVICE 앞 몇 글자로 Scanresult Job 을 찾나(W97218SGZ4BK16 → W97218SG)  — Job 이름에 장치 이름이 그대로 든다(INCI-W97218SGZ4BK16_0860463PD-0A, AOI Capacity 답 2026-11-15)
QORVO_MINUTES = 12
QORVO_MB = 150          # QORVO 로 담는 원본 상한(die 맵이 웨이퍼당 약 10 MB)
# 스캔 기록(AOI_capacity.csv)의 QORVO Scanresult LOT — (장비, Job, Setup, LOT).
# Conder 를 먼저 훑으면 시간 한도에 걸려 Scanresult 가 0개였다(2026-11-15) — 이 목록을 Conder 보다 먼저 모은다.
# 앞 8개: Conder 장치(W9…K16)와 **같은 웨이퍼 ID** 가 든 LOT(장치마다 겹치는 웨이퍼가 가장 많은 것).  Conder 웨이퍼 ID
#   (GWL4R01-D6 꼴)는 Scanresult 와 같은 체계라 표 없이 이어진다 — 수집한 Conder 400장 중 328장이 스캔 기록에 있었다.
# 뒤 7개: XP2100(0861004PD 등) — Conder 폴더가 없는 다른 제품(7L8WW057SOG3 같은 ID 는 이 제품의 OCR 값).
QORVO_KNOWN_LOTS = [
    ('AOI-8', '2D@R2-W96721Z5BK16_0858867PD-0A', '6321', 'NPG-EDGE'),  # Conder 와 같은 웨이퍼 25장
    ('AOI-6', '2D@R2-W96760Z5BK16_0856722PD-0A', '6321', 'THV EDGE'),  # 25장
    ('AOI-8', '2D@R2-W96798Z5BK16_0857146PD-0B', '6321', 'PHK-EDGE'),  # 25장 (GWL4R01-D6 …)
    ('AOI-8', '2D@R2-W96816Z5BK16_0857248PD-0B', '6321', 'THW-EDGE'),  # 25장
    ('AOI-8', '2D@R2-W96817Z5BK16_0857253PD-0A', '6321', 'UUT EDGE'),  # 25장
    ('AOI-6', '2D@RE-W97115Z6B1K16_0858971PD-0A', '6412', 'TWN'),  # 22장
    ('AOI-4', '2D@RE-W97135Z6BK16_0859176PD-0A', '6412', 'TWK'),  # 24장
    ('AOI-11', '2D@RE-W97253Z6B1K16_0860752PD-0A', '6412', 'TWT'),  # 25장
    ('AOI-14', 'R_Qorvo XP2100_0861004PD_RDL1', 'SETUP', 'MMG'),  # 2026-11-10 7장
    ('AOI-14', 'R_Qorvo XP2100_0861004PD_UBM', 'SETUP', '1110_Qorvo XP2100 FRB4 Lot4-5'),  # 2026-11-10 8장
    ('AOI-14', 'R_Qorvo XP2100_0861004PD_PI4_NEW_M', 'SETUP', '1030_Qorvo XP2100_FRB5'),  # 2026-10-30 4장
    ('AOI-14', 'R_Qorvo XP2100_0861004PD_RDL2', 'SETUP', '1019_Qorvo XP2100 FRB4_Lot4-5'),  # 2026-10-19 8장
    ('AOI-14', 'R_Qorvo XP2100_0861004PD_TMI', 'SETUP', 'N37'),  # 2026-10-19 9장
    ('AOI-12', 'R_Qorvo XP2100_0860070PD_RDL2', 'SETUP', '1015_Qorvo XP2100 FRB3_Lot2'),  # 2026-10-15 2장
    ('4F-AOI-02', 'R_Qorvo XP2100_0861004PD_INCI', 'SETUP', 'MMG_SRD'),  # 2026-09-29 6장
]
QORVO_KNOWN_WAFERS = 3   # QORVO_KNOWN_LOTS 의 LOT 마다 웨이퍼 몇 장
# 같은 웨이퍼를 KLA 와 Camtek 이 같은 층(RDL1~6 · FS …)에서 7일 안에 찍은 것(스캔 기록, 2026-11-15 사용자 요청).
# (KLA 장비, KLA LOT, [KLA 웨이퍼 시작 시각], Camtek 장비, Job, Setup, LOT, [웨이퍼 ID], [KLA 슬롯]).
# KLA 폴더 = <드라이브>\<날짜>\<KLA LOT>\<웨이퍼 시작 YYYY-MM-DD-HH-MM>_<슬롯>  (AOI Capacity 답, 2026-11-15).
#   웨이퍼 시작 시각 = 스캔 기록 wafer_start_time.  report 의 시각은 LOT 실행 시작이라 폴더 이름과 다르다.
#   자정을 넘긴 LOT 은 다음 날 폴더에 LOT 전체가 다시 있어, 웨이퍼 시작 날짜 폴더를 보면 늘 있다.
# 드라이브는 PC 마다 연결이 달라(4F-K1 = H: 또는 L:, K3 = W: …) 드라이브를 전부 보고 <날짜>\<LOT> 가 있는 곳을 쓴다.
KLA_PAIRS = [
    ('4F-K1', 'TB500.INT1133@6326', ['2026-11-10-02-08', '2026-11-10-01-39'], '4F-AOI-05', 'TB500_RDL1_SAO', 'Setup1', 'HKS_4F_AOI05', ['S3G9S016XMF6', 'S3GHK016XMD4'], [25, 22]),
    ('K5', 'TB500.INT585@6324 RE', ['2026-11-09-13-09', '2026-11-09-12-51'], 'AOI-19', 'TB500_RDL2 - Multi', 'Setup1', '0', ['54265740EWB2', '54265742EWC3'], [23, 21]),
    ('4F-K1', 'TB500.INT1431@6412', ['2026-11-08-11-45', '2026-11-08-11-32'], '4F-AOI-03', 'TB500_RDL4-Multi-Align', 'Setup1', 'VHC', ['33296733EWH2', '33296734EWE1'], [25, 24]),
    ('4F-K1', 'TB500.INT1046@6324', ['2026-11-08-05-02', '2026-11-08-04-38'], '4F-AOI-03', 'TB500_RDL2 - Multi', 'Setup1', 'CNA-RDL2_AOI-3', ['43206497EWG6', '43206498EWD5'], [2, 1]),
    ('4F-K1', 'TB500.INT1185@6322', ['2026-11-07-07-05', '2026-11-07-06-55'], '4F-AOI-04', 'TB500_RDL3 - Multi', 'Setup1', 'KAV-RDL3-LIFT', ['S3M9N191XMG7', 'S3M9N187XMF6'], [25, 24]),
    ('K5', 'TB500.INT1099@6324', ['2026-11-06-22-28', '2026-11-07-02-53'], 'AOI-22', 'TB500_RDL2 - Multi - Swelling', 'Setup1', 'DGM-RDL2 SWELLING', ['49126697EWD2', '49126662EWF3'], [4, 14]),
    ('K3', 'TB500.INT1279@6412 RE', ['2026-11-06-18-07', '2026-11-06-17-50'], 'AOI-27', 'TB500_RDL4-Multi', 'Setup1', 'With_EDC', ['S3FAR128XMC6', 'S3FAR079XMA7'], [25, 22]),
    ('K3', 'TB500.INT1057@6324', ['2026-11-06-09-53', '2026-11-06-08-24'], 'AOI-22', 'TB500_RDL2 - Multi - Swelling', 'Setup1', 'CNS-RDL2 SWELLING', ['61JV6G04EWD4', '61JV6G08EWF6'], [20, 16]),
    ('K2', 'INTP-KENDALL-80-2604@6342', ['2026-11-11-15-06', '2026-11-11-15-03'], 'AOI-13', 'R_KENDALL_A0_FS', 'SETUP', 'AWB-FS', ['W6960194XYA0', 'W6960195XYE2'], [8, 7]),
    ('K2', 'INTP-KENDALL-85-2604@6342', ['2026-11-10-04-46', '2026-11-10-04-28'], 'AOI-13', 'R_KENDALL_A0_FS', 'SETUP', 'CSY-FS DIA', ['00NSP126XYG3', '00NSP131XYE3'], [25, 20]),
    ('K4', 'INTP-KENDALL-118-2609@6324 RE', ['2026-10-25-09-26', '2026-10-25-11-04'], 'AOI-23', 'R_Kendall RDL4', 'Setup1', 'CUT- WPH test', ['00NVP095XYG5', '00NVP078XYD0'], [4, 20]),
    ('K2', 'INTP-KENDALL-92-2608@6342', ['2026-10-05-20-45', '2026-10-05-20-41'], 'AOI-3', 'R_MSFT_KENDALL A0_FS', '6324', 'CTH-FS DIA', ['00NKT225XYA7', '00NKT224XYE0'], [25, 24]),
    ('K2', 'INTP-KENDALL-82-2604@6342', ['2026-08-24-16-20', '2026-08-24-16-16'], 'AOI-3', 'R_MSFT_KENDALL A0_FS', '6324', 'CSV-FS DIA', ['W6960048XYC6', 'W6960176XYG7'], [25, 23]),
]
KLA_MINUTES = 12
# Conder LIVE 파일명 col/row(장비 화면 번호)로 앱 row 를 확인할 자료(2026-11-15, 정보 찾기 쓰레드 요청).
# (Conder LOT 코드, [웨이퍼 ID]) — `1. Conder Scan` 아래 LOT 폴더 이름이 `(코드)` 로 끝나고 그 안에 웨이퍼 폴더가 있는 곳.
# 공정 단계 폴더마다 맵 txt · .lot · .csv 를 담고, 대상 웨이퍼 폴더는 사진 이름 전부를 남긴다.
CONDER_ROW_LOTS = [
    ('DDU', ['E5025794EWF5', 'E5025779EWD1']),   # 확인 대상(번호표 없음) AOI-15 R_KENDALL_A0_PI6_0223/SETUP/DDU-PIDS3
    ('PAD', ['07075E39EWF3', '54085438EWG1']),   # 대조군(번호표 있음) AOI-16 R_KENDALL_A0_PI6_0126
    ('GVS', ['07295277EWE3', '08235229EWC1']),   # 대조군 AOI-13 R_KENDALL_A0_PI6_0126
    ('CTY', ['00NUR030XYA3', '00NUR042XYG0']),   # 대조군 AOI-13 R_KENDALL_A0_PI2/6412
]
CONDER_ROW_MINUTES = 10
CONDER_FIRST = ("KENDALL", "MSFT", "MICROSOFT")   # 이 이름이 든 고객 폴더부터 본다
_LOT_DEPTH = 4      # Job 아래 LOT 을 찾는 깊이(Setup → [구분] → LOT)
_WAFER_DEPTH = 2    # 웨이퍼 폴더 안 하위 폴더 깊이


def nas_roots():
    """이 PC 에서 보이는 ``<드라이브 또는 공유>\\*AOI-*\\Scanresult`` 전부(같은 장비는 한 번만).
    ``4F-AOI-05`` 같은 4층 장비도 든다(예전 ``AOI-*`` 는 놓쳤다)."""
    bases = [Path(f"{c}:\\") for c in "DEFGHIJKLMNOPQRSTUVWXYZ"] + [Path(u) for u in UNC_SHARES]
    out, seen = [], set()
    for b in bases:
        try:
            for d in sorted(b.glob("*AOI-*")):
                sr = next((p for p in (d / "Scanresult", d / "ScanResult", d / "ScanResults") if p.is_dir()), None)
                if sr is not None and d.name.upper() not in seen:
                    seen.add(d.name.upper())
                    out.append(sr)
        except OSError:
            continue
    return out


def _aoi_key(name: str) -> str:
    """장비 이름 비교용 — 대소문자와 번호 앞 0 을 무시한다(기록의 'AOI-1' = 폴더 'AOI-01')."""
    return re.sub(r"-0*(\d+)$", r"-\1", name.strip().upper())


def resolve_pair(raw: str):
    """주소 그대로 있으면 그것, 없으면 드라이브 대신 :data:`UNC_SHARES` 아래 같은 경로.  못 찾으면 None."""
    p = Path(raw)
    if p.is_dir():
        return p
    rest = p.parts[1:]
    for u in UNC_SHARES:
        q = Path(u).joinpath(*rest)
        if rest and q.is_dir():
            return q
    return None


def _entries(d: Path):
    """(폴더들, 파일들) — 각 항목 (이름, 경로, 수정 시각, 크기).  못 읽으면 빈 목록."""
    dirs, files = [], []
    try:
        with os.scandir(d) as it:
            for e in it:
                try:
                    st = e.stat()
                    (dirs if e.is_dir() else files).append((e.name, Path(e.path), st.st_mtime, st.st_size))
                except OSError:
                    continue
    except OSError:
        pass
    return dirs, files


def is_wafer(d: Path) -> bool:
    return any((d / m).is_file() for m in WAFER_MARK)


def is_conder_lot(d: Path) -> bool:
    """Conder LOT 폴더 — 하위 폴더(웨이퍼)와 같은 이름의 맵 ``.txt`` 가 하나라도 있다."""
    dirs, files = _entries(d)
    names = {n for n, _, _, _ in dirs}
    return any(n.lower().endswith(".txt") and n[:-4] in names for n, _, _, _ in files)


def conder_device(lot: Path):
    """LOT 폴더의 맵 txt 첫 ``DEVICE:`` 값.  없으면 None."""
    for n, p, _, size in _entries(lot)[1]:
        if not n.lower().endswith(".txt") or size > 2 * 2 ** 20:
            continue
        try:
            head = p.read_bytes()[:400].decode("latin-1")
        except OSError:
            continue
        for line in head.splitlines():
            if line.upper().startswith("DEVICE:") and line[7:].strip():
                return line[7:].strip()
    return None


def find_conder_lots(root: Path, limit: int, expired, depth: int = 4):
    """``root`` 아래 Conder LOT 폴더를 최근 것부터 ``limit`` 개."""
    found = []

    def visit(d: Path, k: int):
        if len(found) >= limit or k > depth or expired():
            return
        if is_conder_lot(d):
            found.append(d)
            return
        for _, p, _, _ in sorted(_entries(d)[0], key=lambda t: -t[2]):
            visit(p, k + 1)
            if len(found) >= limit or expired():
                return

    visit(root, 0)
    return found


def find_lot(sr: Path, job: str, setup: str, lot_name: str):
    """``<Scanresult>\\<Job>\\<Setup>\\<LOT>`` — Setup 과 LOT 사이에 구분 폴더가 한 단 더 있어도 찾는다.  없으면 None."""
    lot = sr / job / setup / lot_name
    if not lot.is_dir():
        lot = next((d / lot_name for _, d, _, _ in _entries(sr / job / setup)[0] if (d / lot_name).is_dir()), lot)
    return lot if lot.is_dir() else None


def kla_bases():
    """KLA 결과가 있을 수 있는 드라이브 · 공유 루트."""
    return [Path(f"{c}:\\") for c in "DEFGHIJKLMNOPQRSTUVWXYZ"] + [Path(u) for u in UNC_SHARES]


def find_kla_folder(lot: str, start: str, slot: int):
    """KLA 웨이퍼 폴더 ``<루트>\\<날짜>\\<LOT>\\<start>_<슬롯>`` (start = 웨이퍼 시작 ``YYYY-MM-DD-HH-MM``).
    딱 맞는 폴더가 없으면 그 날짜 LOT 폴더에서 ``_<슬롯>`` 으로 끝나는 것 중 시각이 가장 가까운 것.  못 찾으면 None."""
    day, want = start[:10], f"{start}_{slot}"
    for b in kla_bases():
        d = b / day / lot
        try:
            if not d.is_dir():
                continue
        except OSError:
            continue
        if (d / want).is_dir():
            return d / want
        cands = [p for n, p, _, _ in _entries(d)[0] if n.endswith(f"_{slot}") and n[:10] == day]
        if cands:
            return min(cands, key=lambda p: abs(_minutes(p.name[:16]) - _minutes(start)))
    return None


def _minutes(stamp: str) -> int:
    """``YYYY-MM-DD-HH-MM`` → 분(못 읽으면 큰 값)."""
    try:
        y, mo, d, h, mi = (int(x) for x in stamp.split("-"))
        return (((y * 12 + mo) * 31 + d) * 24 + h) * 60 + mi
    except ValueError:
        return 10 ** 9


def collect_kla_pairs(col, say, progress, stop, end=None):
    """:data:`KLA_PAIRS` — 같은 웨이퍼의 Camtek 웨이퍼 폴더와 KLA 웨이퍼 폴더를 나란히 담는다.  담은 웨이퍼 수."""
    deadline = time.time() + KLA_MINUTES * 60
    if end is not None:
        deadline = min(deadline, end)
    roots = {_aoi_key(r.parent.name): r for r in nas_roots()} if KLA_PAIRS else {}
    n = 0
    for i, (kdev, klot, starts, aoi, job, setup, lot_name, wids, slots) in enumerate(KLA_PAIRS):
        if stop() or time.time() > deadline:
            say("KLA: 시간 제한·중지로 여기서 멈춤")
            break
        progress(i, len(KLA_PAIRS), f"KLA·Camtek {klot}"[:70])
        arc = f"KLA비교/{klot}"
        sr = roots.get(_aoi_key(aoi))
        lot = find_lot(sr, job, setup, lot_name) if sr else None
        if lot is None:
            say(f"KLA 짝 Camtek LOT 못 찾음: {aoi}\\{job}\\{setup}\\{lot_name}")
        for wid, start, slot in zip(wids, starts, slots):
            if stop() or time.time() > deadline:
                break
            if lot is not None:
                w = next((p for _, p, _, _ in _entries(lot)[0] if wid.upper() in p.name.upper() and is_wafer(p)), None)
                if w is None:
                    say(f"KLA 짝 Camtek 웨이퍼 못 찾음: {lot}\\{wid}")
                else:
                    col.take_files(w, f"{arc}/Camtek/{aoi}/{w.name}")
                    n += 1
            k = find_kla_folder(klot, start, slot)
            if k is None:
                say(f"KLA 폴더 못 찾음: {kdev} {start[:10]}\\{klot}\\{start}_{slot} ({wid})")
            else:
                col.take_files(k, f"{arc}/KLA/{k.name}")
                say(f"KLA {kdev} {k}  ↔  {wid}")
    return n


def collect_conder_rows(col, say, progress, stop, end=None):
    """:data:`CONDER_ROW_LOTS` — Conder LOT(이름 끝 ``(코드)``)의 단계 폴더 파일과 대상 웨이퍼 사진 이름.  담은 웨이퍼 수."""
    deadline = time.time() + CONDER_ROW_MINUTES * 60
    if end is not None:
        deadline = min(deadline, end)

    def expired():
        return stop() or time.time() > deadline

    want = {code.upper(): {w.upper() for w in ws} for code, ws in CONDER_ROW_LOTS}
    left, n = dict(want), 0

    def has_wafer(d: Path, ws, k=0):
        dirs = _entries(d)[0]
        if any(any(w in name.upper() for w in ws) for name, _, _, _ in dirs):
            return True
        return k < 2 and any(has_wafer(p, ws, k + 1) for _, p, _, _ in dirs)

    def take_lot(d: Path, arc: str, ws, k=0):
        nonlocal n
        dirs, files = _entries(d)
        maps = {name[:-4].upper() for name, _, _, _ in files if name.lower().endswith(".txt")}
        col.take_top_files(d, arc)                               # 맵 txt · .lot · .csv
        for name, p, _, _ in sorted(dirs):
            if expired():
                return
            if any(w in name.upper() for w in ws):
                col.take_files(p, f"{arc}/{name}", depth=_WAFER_DEPTH)   # 사진 이름 전부
                n += 1
            elif name.upper() not in maps and k < 3:              # 다른 웨이퍼 폴더는 건너뛰고 단계 폴더만 내려간다
                take_lot(p, f"{arc}/{name}", ws, k + 1)

    for base in (Path(r) for r in CONDER_ROOTS):
        customers = sorted(_entries(base)[0], key=lambda e: (not any(s in e[0].upper() for s in CONDER_FIRST), e[0]))
        for cust_name, cust, _, _ in customers:
            for dev_name, dev, _, _ in _entries(cust)[0]:
                if not left or expired():
                    break
                progress(0, 0, f"Conder 번호 확인 {cust_name}/{dev_name}"[:70])
                for lot_name, lot, _, _ in _entries(dev)[0]:
                    m = re.search(r"\(([^()]+)\)\s*$", lot_name)
                    code = m.group(1).strip().upper() if m else None
                    if code not in left or not has_wafer(lot, left[code]):
                        continue
                    take_lot(lot, f"Conder번호확인/{cust_name}/{dev_name}/{lot_name}", want[code])
                    say(f"Conder 번호 확인 LOT {lot}")
                    left.pop(code)
    for code in left:
        say(f"Conder 번호 확인 LOT 못 찾음: ({code})")
    return n


def collect_qorvo(col, say, progress, stop, end=None):
    """QORVO Scanresult LOT(:data:`QORVO_KNOWN_LOTS` 먼저) · Conder LOT(맵 txt · 사진 이름) · 같은 장치의
    Scanresult LOT 을 모은다.  웨이퍼 수를 돌려준다."""
    deadline = time.time() + QORVO_MINUTES * 60
    if end is not None:
        deadline = min(deadline, end)              # 전체 1시간 상한을 넘지 않게
    start = col.raw

    def expired():
        return stop() or time.time() > deadline or col.raw - start > QORVO_MB * 2 ** 20

    keys, wafers = {QORVO_NAME}, 0              # Job 이름에 Qorvo 가 든 것(R_Qorvo XP2100_…)도 찾는다
    roots = {_aoi_key(r.parent.name): r for r in nas_roots()} if QORVO_KNOWN_LOTS else {}
    done = set()
    for aoi, job, setup, lot_name in QORVO_KNOWN_LOTS:   # 스캔 기록에서 고른 LOT — Conder 보다 먼저(시간 한도에 밀리지 않게)
        if expired():
            break
        sr = roots.get(_aoi_key(aoi))
        lot = find_lot(sr, job, setup, lot_name) if sr else None
        if lot is None:
            say(f"QORVO LOT 못 찾음: {aoi}\\{job}\\{setup}\\{lot_name}")
            continue
        arc_lot = f"QORVO/Scanresult/{sr.parent.name}/{job}/{lot.relative_to(sr / job).as_posix()}"
        col.take_top_files(sr / job, f"QORVO/Scanresult/{sr.parent.name}/{job}")
        col.take_top_files(lot, arc_lot)
        for w in pick_wafers(lot, QORVO_KNOWN_WAFERS):
            if expired():
                break
            progress(0, 0, f"QORVO {job} {w.name}"[:70])
            col.take_files(w, f"{arc_lot}/{w.name}")
            wafers += 1
        done.add(lot)
        say(f"QORVO Scanresult LOT {lot}")
    for base in (Path(r) for r in CONDER_ROOTS):
        customers = [p for n, p, _, _ in _entries(base)[0] if QORVO_NAME in n.upper()]
        if not customers:
            say(f"QORVO Conder 폴더 못 찾음: {base}")
        for cust in customers:
            for _, dev, _, _ in sorted(_entries(cust)[0], key=lambda t: -t[2]):
                if expired():
                    break
                for lot in find_conder_lots(dev, QORVO_LOTS, expired):
                    progress(0, 0, f"QORVO Conder {lot.name}"[:70])
                    arc = f"QORVO/Conder/{lot.relative_to(cust).as_posix()}"
                    col.take_top_files(lot, arc)
                    for _, w, _, _ in sorted(_entries(lot)[0]):
                        col.take_files(w, f"{arc}/{w.name}", depth=_WAFER_DEPTH)   # 사진 이름만(바로 아래)
                    device = conder_device(lot)
                    if device:
                        keys.add(device[:QORVO_KEY_LEN].upper())
                    say(f"QORVO Conder LOT {lot}  DEVICE {device}")
    say(f"QORVO Scanresult Job 열쇠: {', '.join(sorted(keys)) or '없음'}")
    for r in (nas_roots() if keys else []):
        for name, job, _, _ in sorted(_entries(r)[0]):
            if expired() or not any(k in name.upper() for k in keys):
                continue
            arc_job = f"QORVO/Scanresult/{r.parent.name}/{name}"
            col.take_top_files(job, arc_job)
            for lot in find_lots(job, QORVO_JOB_LOTS, expired):
                if lot in done:
                    continue
                arc_lot = f"{arc_job}/{lot.relative_to(job).as_posix()}"
                col.take_top_files(lot, arc_lot)
                for w in pick_wafers(lot, 50):
                    if expired():
                        break
                    progress(0, 0, f"QORVO {name} {w.name}"[:70])
                    col.take_files(w, f"{arc_lot}/{w.name}")
                    wafers += 1
                say(f"QORVO Scanresult LOT {lot}")
    if expired():
        say("QORVO: 시간 제한·중지·용량 한도로 여기서 멈춤")
    return wafers


def find_lots(job: Path, limit: int, expired):
    """Job 아래에서 웨이퍼 폴더를 담은 LOT 폴더를 최근 것부터 ``limit`` 개 찾는다."""
    found = []

    def visit(d: Path, depth: int):
        if len(found) >= limit or depth > _LOT_DEPTH or expired():
            return
        subs = sorted(_entries(d)[0], key=lambda t: -t[2])
        if not subs:
            return
        probe = [subs[0], subs[len(subs) // 2], subs[-1]]
        if any(is_wafer(p) for _, p, _, _ in probe):
            found.append(d)
            return
        for _, p, _, _ in subs:
            visit(p, depth + 1)
            if len(found) >= limit or expired():
                return

    visit(job, 0)
    return found


def pick_wafers(lot: Path, n: int):
    wafers = sorted((p for _, p, _, _ in _entries(lot)[0] if is_wafer(p)), key=lambda p: p.name)
    if len(wafers) <= n:
        return wafers
    step = len(wafers) / n
    return [wafers[int(i * step)] for i in range(n)]


class Collector:
    """고른 파일을 작업용 zip 하나에 담는다(내용이 같은 파일은 한 번만)."""

    def __init__(self, staging: Path):
        self.zf = zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED)
        self.hashes = {}
        self.photos, self.listing, self.dups = [], [], []
        self.raw = 0
        self.files = 0

    def take_files(self, folder: Path, arc: str, depth: int = 0):
        dirs, files = _entries(folder)
        for name, path, _, size in sorted(files):
            ext = Path(name).suffix.lower()
            if ext in IMG_EXT:
                self.photos.append(f"{arc}/{name}")
                continue
            if ext in SKIP_EXT or size > FILE_MB * 2 ** 20:
                self.listing.append(f"{arc}/{name}\t{size}\t뺌")
                continue
            try:
                data = path.read_bytes()
            except OSError as e:
                self.listing.append(f"{arc}/{name}\t{size}\t못 읽음 {e}")
                continue
            self.listing.append(f"{arc}/{name}\t{size}\t담음")
            h = hashlib.sha1(data).hexdigest()
            if h in self.hashes:
                self.dups.append(f"{arc}/{name}\t= {self.hashes[h]}")
                continue
            self.hashes[h] = f"{arc}/{name}"
            self.zf.writestr(f"{arc}/{name}", data)
            self.raw += len(data)
            self.files += 1
        if depth < _WAFER_DEPTH:
            for name, path, _, _ in sorted(dirs):
                self.take_files(path, f"{arc}/{name}", depth + 1)

    def take_top_files(self, folder: Path, arc: str):
        """그 폴더 바로 아래 파일만(하위 폴더는 내려가지 않는다)."""
        self.take_files(folder, arc, depth=_WAFER_DEPTH)

    def close(self, summary: str):
        for name, lines in (("사진목록.txt", self.photos), ("파일목록.txt", self.listing),
                            ("중복목록.txt", self.dups)):
            self.zf.writestr(name, "\n".join(lines) + "\n")
        self.zf.writestr("요약.txt", summary)
        self.zf.close()


def plan_parts(sizes, part=PART_MB * 2 ** 20):
    """[(이름, 압축 크기)] → 조각별 이름 목록.  앞 조각부터 ``part`` 를 넘지 않게 **들어가는 것을 골라**
    채운다(큰 파일 하나 때문에 조각이 작게 끊기지 않게).  요약 파일은 첫 조각에 둔다."""
    first = [(n, s) for n, s in sizes if n == "요약.txt"]
    queue = [(n, s) for n, s in sizes if n != "요약.txt"]
    parts = []
    while queue or first:
        cur, used, rest = [n for n, _ in first], sum(s for _, s in first), []
        first = []
        for n, s in queue:
            if len(cur) == 0 or used + s <= part:
                cur.append(n)
                used += s
            else:
                rest.append((n, s))
        if rest and len(rest) == len(queue):         # 하나도 안 들어갔다(요약만 있는 첫 조각) — 하나는 넣는다
            cur.append(rest.pop(0)[0])
        parts.append(cur)
        queue = rest
    return parts


def split_zip(staging: Path, out_dir: Path, stem: str):
    """작업용 zip 을 :data:`PART_MB` 조각으로 나눈다.  넘지 않으면 이름만 바꾼다."""
    if staging.stat().st_size <= PART_MB * 2 ** 20:
        final = out_dir / f"{stem}.zip"
        staging.replace(final)
        return [final]
    with zipfile.ZipFile(staging) as src:
        infos = src.infolist()
        sizes = [(i.filename, i.compress_size + 76 + 2 * len(i.filename.encode())) for i in infos]
        made = []
        for k, names in enumerate(plan_parts(sizes), 1):
            p = out_dir / f"{stem}_{k}.zip"
            with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as dst:
                for n in names:
                    dst.writestr(n, src.read(n))
            made.append(p)
    staging.unlink()
    return made


def run(out: Path, progress=None, stop=None):
    """수집하고 ``(결과 폴더, 웨이퍼 수, 담은 파일 수)`` 를 돌려준다.  웨이퍼가 없으면 ``(None, 0, 0)``.

    ``progress(done, total, 문구)`` — total 0 이면 진행량 모름.  ``stop()`` 이 참이면 그만 찾고 지금까지 모은 것을 남긴다."""
    progress = progress or (lambda *_: None)
    stop = stop or (lambda: False)
    t0 = time.time()
    deadline = t0 + MINUTES * 60
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = out / f"Threshold수집_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    staging = out_dir / "_작업중.zip"
    col = Collector(staging)
    log, wafers, pair_raw = [], 0, 0

    def expired():
        return stop() or time.time() > deadline or col.raw - pair_raw > TOTAL_MB * 2 ** 20

    def say(line):
        log.append(line)
        print(line)
        progress(0, 0, line[:70])

    for k, raw in enumerate(PAIR_LOTS):                    # 비교 스캔 LOT 은 먼저, 웨이퍼 전부
        if stop() or time.time() > deadline:
            say("시간 제한·중지로 비교 LOT 을 여기서 멈춤")
            break
        lot = resolve_pair(raw)
        if lot is None:
            say(f"비교 LOT 못 찾음: {raw}")
            continue
        aoi = lot.parents[3].name if len(lot.parents) > 3 else ""
        arc_lot = f"비교스캔/{aoi}/{lot.name}"
        col.take_top_files(lot, arc_lot)
        ws = sorted((p for _, p, _, _ in _entries(lot)[0] if is_wafer(p)), key=lambda p: p.name)
        mine = [p for p in ws if PAIR_WAFER.upper() in p.name.upper()]
        if not mine:
            say(f"비교 LOT 에 {PAIR_WAFER} 없음 — 웨이퍼 전부 담음: {lot}")
        ws = mine or ws
        for j, w in enumerate(ws):
            if stop() or time.time() > deadline:
                break
            progress(j, len(ws), f"비교 LOT {k + 1}/{len(PAIR_LOTS)} {lot.name}"[:70])
            col.take_files(w, f"{arc_lot}/{w.name}")
            wafers += 1
        say(f"비교 LOT {lot}  웨이퍼 {len(ws)}")
    if not stop():
        wafers += collect_conder_rows(col, say, progress, stop, end=deadline)
    if not stop():
        wafers += collect_kla_pairs(col, say, progress, stop, end=deadline)
    if not stop():
        wafers += collect_qorvo(col, say, progress, stop, end=deadline)
    pair_raw = col.raw                                     # 아래 용량 한도는 비교 LOT·QORVO 를 뺀 양으로 잰다(시간은 전체 1시간)

    roots = {_aoi_key(r.parent.name): r for r in nas_roots()}
    say(f"Scanresult {len(roots)}곳: " + ", ".join(sorted(roots)))
    jobs = []
    for i, (aoi, job, setup, lot_name) in enumerate(KNOWN_LOTS):
        if expired():
            say("시간 제한·중지·용량 한도로 여기서 멈춤")
            break
        progress(i, len(KNOWN_LOTS), f"{aoi} {lot_name}"[:70])
        sr = roots.get(_aoi_key(aoi))
        lot = find_lot(sr, job, setup, lot_name) if sr else None
        if lot is None:
            say(f"LOT 못 찾음: {aoi}\\{job}\\{setup}\\{lot_name}")
            continue
        jobs.append((aoi, sr / job))
        arc_job = f"{aoi}/{job}"
        col.take_top_files(sr / job, arc_job)
        col.take_top_files(sr / job / setup, f"{arc_job}/{setup}")
        arc_lot = f"{arc_job}/{lot.relative_to(sr / job).as_posix()}"
        col.take_top_files(lot, arc_lot)
        for w in pick_wafers(lot, WAFERS):
            if expired():
                break
            col.take_files(w, f"{arc_lot}/{w.name}")
            wafers += 1
            say(f"  웨이퍼 {wafers}  {w}")
    summary = "\n".join([
        f"Threshold 자료 수집 {stamp}  ({time.time() - t0:.0f}초)",
        f"제품: {', '.join(PRODUCTS)} · Job {len(jobs)} · 웨이퍼 {wafers} · 담은 파일 {col.files}"
        f" · 원본 {col.raw / 2 ** 20:.1f} MB · 사진 이름 {len(col.photos)} · 중복 {len(col.dups)}",
        "", "Job 목록:", *[f"  {a}\t{j}" for a, j in jobs], "", "기록:", *log, ""])
    col.close(summary)
    if not wafers and not col.files:
        staging.unlink(missing_ok=True)
        try:
            out_dir.rmdir()
        except OSError:
            pass
        return None, 0, 0
    progress(0, 0, "zip 나누는 중")
    split_zip(staging, out_dir, f"Threshold수집_{stamp}")
    return out_dir, wafers, col.files


if __name__ == "__main__":
    desk = Path.home() / "Desktop"
    try:
        print(run(desk if desk.is_dir() else Path.home()))
    finally:
        input("끝났습니다. 바탕화면 Threshold수집_* 폴더 안 zip 을 올려 주세요. (Enter 로 닫기)")
