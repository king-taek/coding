QORVO W97218SGZ4BK16 · LOT 6H7ENK@6412 · 셋업 CTX(AOI-11, 장비 EagleTP_108150) · Conder 1. UBM 단계 (2026-11-15 수집)

이 스캔은 장비 번호 상자([ExportData] 5..323 × 5..319)가 고객 맵(stage 6..322 × 6..317)보다 넓어
LIVE 파일명 번호가 LOT 맵 txt 와 (col 1, row 2) 어긋난다(관측):
  · 장비 화면 2장 · 장비 Export 파일(Defects.csv) 17/17 이 파일명 번호와 같다
  · 파일명 − (1, 2) 칸이 55장 모두 불량 bin(093 특수 die 52 · 031 3), 시프트 0 에서는 0장
  · 같은 웨이퍼 SQ67P01-D1 을 6321 단계(AOI-8, EagleTP_106840)에서 찍은 스캔은 상자 = 고객 맵, 어긋남 0
Conder_UBM/ — 맵 txt 는 원본을 gzip + base64 로 담았다(<WaferID>.txt.gz.b64, 테스트가 그대로 푼다), 사진은 이름만(사진목록.txt).  골든: dev/tests/test_display_shift.py

Camtek_CTX_6321/SQ67P01-D1 — 같은 웨이퍼의 6321 단계 스캔(AOI-8) 작은 ini 4개(사진은 ColorImageGrabingInfo.ini 의 이름만).
6412 쪽은 Camtek_CTX/SQ67P01-D1 (WaferMapRecipe.ini 포함).
두 스캔에 같이 찍힌 Defect 5개가 장비 번호로 (1, 2) 어긋난다(die 안 x·y 3 µm 안, 관측).  골든: dev/tests/test_scan_start.py
