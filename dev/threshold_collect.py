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
    · :data:`RICH_LOT` — 비교 웨이퍼의 ``s_RichSurface.dat`` 전체에서 :data:`RICH_COLS` 열만 CSV 로(Defect 후보별 Contrast, ``RichSurface/``).
    · :data:`EXPORT_WAFERS` — 현택님이 장비에서 [Export] 로 만든 Defects.csv(``Export/``).
    QORVO · 모든 Job · 구형 flt · Conder · KLA 단계는 각 쓰레드의 수집 버튼으로 옮겼다(현택님 지시 2026-11-15 '쓰레드마다 따로').
결과: 바탕화면 ``Threshold수집_<시각>\\`` 폴더 안 zip.  :data:`PART_MB` 를 넘으면 나누되,
나눈 조각은 :data:`MIN_PART_MB` 이상이 되게 채운다(마지막 조각만 작을 수 있다).
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
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
# 다 받은 단계 — :func:`run` 이 건너뛴다(코드는 남긴다).  현택님 지시(2026-11-15): 더 받을 필요 없는 건 수집기에서 계속 뺀다.
# pair 비교 스캔 · known Kendall/TB500 LOT — 20261115_1856 수집까지 · rich RichSurface 전체 — 20261115_2302 수집으로 다 받았다.
DONE = {"pair", "known", "rich"}
# 현택님이 장비에서 [Export] 를 눌러 Defects.csv 를 만들어 줄 웨이퍼(사용자 제안 2026-11-15) — (장비, Job, Setup, LOT, 웨이퍼, 폴더 전부?).
# Defects.csv = 장비가 화면에 보이는 Defect 별 Area · Width · Length · Classify.  Surface.flt 의 어느 열 · 어느 단위가
# 장비 필터의 '면적 · 폭' 인지 정해 미리보기의 면적 · 폭 기준을 정확히 하려는 자료다(RTP.txt 는 RDL 면적 38, Zone ini 는 63.96 로 다르다).
EXPORT_WAFERS = [
    ("4F-AOI-05", "R_TB500_LIVE_PI4 - Enhanced", "Setup1", "YDD-Contrast Avg24", "20234838EWG1", False),  # RDL Zone(폴더는 받음)
    ("4F-AOI-04", "R_TB500_LIVE_PI4 - Enhanced", "Setup1", "YDD-No Image", "20234838EWG1", False),       # PI_Opening + RDL, RichSurface 웨이퍼
    ("AOI-27", "TB500_RDL4-Multi", "Setup1", "With_EDC", "S3FAR079XMA7", True),                         # EDC Good 이 Export 에 나오나
    ("AOI-15", "R_KENDALL_A0_PI6_0223", "SETUP", "DDU-PIDS3", "E5025794EWF5", False),                   # 다른 제품(Kendall) 단위
    # 다른 제품 · 헷갈리는 기록(현택님 2026-11-15 '다른 디바이스도').  앞 둘은 이미 Export 함, 나머지는 폴더도 처음 받는다.
    ("AOI-12", "2D@RE-W97218SGB1K16_0860463PD-0A", "6412", "CTK", "SQ62G01-D7", False),                # QORVO: Contrast 0 · Zone 1 class −93
    ("AOI-11", "2D@RE-W97218SGB1K16_0860463PD-0A", "6412", "CTX", "SQ67P01-D1", False),                # QORVO: 136 B 구형 기록
    ("AOI-9", "2D@R2-15966TA0-BW2_0859654PD-0C", "6321", "TYW", "NELA26-01C1", True),                  # AVAGO: Contrast 가 Zone 1 에만
    ("AOI-12", "2D@R2-3L781987B17-WUP_0858675PD-0B", "6412", "CCG", "3LL641036-01-G1", True),          # TI: Zone 63 도 class 29, Contrast 0
    ("AOI-3", "R_T254 LIVE_0858711PD_FS UBM", "6324", "TSC-FS", "27125625EWC5", True),                 # T254: 화면 번호 시프트 −1 레시피
    ("AOI-16", "2D@R5-AS-T254-A01_0857122AY-0B", "6324", "WVU", "GX57007306", True),                   # T254: 부분 맵 · Zone 63 class −22
    ("AOI-1", "2D@RE-KENDALL_MEOL_0859275PD-0C", "REVERSEETCH", "2DG-1113S", "GX57527840", True),      # 120 B 구형 기록 · class 33
]
EXPORT_FILE = "Defects.csv"
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


def find_lot(sr: Path, job: str, setup: str, lot_name: str):
    """``<Scanresult>\\<Job>\\<Setup>\\<LOT>`` — Setup 과 LOT 사이에 구분 폴더가 한 단 더 있어도 찾는다.  없으면 None."""
    lot = sr / job / setup / lot_name
    if not lot.is_dir():
        lot = next((d / lot_name for _, d, _, _ in _entries(sr / job / setup)[0] if (d / lot_name).is_dir()), lot)
    return lot if lot.is_dir() else None


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

    def take_files(self, folder: Path, arc: str, depth: int = 0, only=None, max_mb=None,
                   photos=False, names_only=(), with_mtime=False, skip=()):
        """``only``(이름 집합 또는 이름 → 참/거짓 함수)를 주면 그 파일만 담는다(하위 폴더는 내려가지 않고, 다른 파일은 목록에도 안 남긴다).
        ``photos`` 면 ``only`` 가 있어도 사진 이름은 남기고, ``names_only`` 의 파일은 이름 · 크기만 파일목록에 적는다.
        ``with_mtime`` 이면 파일목록에 수정 시각을 붙인다.  ``skip`` 의 이름은 아예 건너뛴다."""
        dirs, files = _entries(folder)
        for name, path, mtime, size in sorted(files):
            if name in skip:
                continue
            if Path(name).suffix.lower() in IMG_EXT and (only is None or photos):
                self.photos.append(f"{arc}/{name}")
                continue
            if only is not None and not (only(name) if callable(only) else name in only):
                if name in names_only:
                    self.listing.append(f"{arc}/{name}\t{size}\t이름만")
                continue
            ext = Path(name).suffix.lower()
            if ext in IMG_EXT:
                self.photos.append(f"{arc}/{name}")
                continue
            if ext in SKIP_EXT or size > (max_mb or FILE_MB) * 2 ** 20:
                self.listing.append(f"{arc}/{name}\t{size}\t뺌")
                continue
            try:
                data = path.read_bytes()
            except OSError as e:
                self.listing.append(f"{arc}/{name}\t{size}\t못 읽음 {e}")
                continue
            stamp = f"\t{datetime.fromtimestamp(mtime):%Y-%m-%d %H:%M}" if with_mtime else ""
            self.listing.append(f"{arc}/{name}\t{size}\t담음{stamp}")
            h = hashlib.sha1(data).hexdigest()
            if h in self.hashes:
                self.dups.append(f"{arc}/{name}\t= {self.hashes[h]}")
                continue
            self.hashes[h] = f"{arc}/{name}"
            self.zf.writestr(f"{arc}/{name}", data)
            self.raw += len(data)
            self.files += 1
        if depth < _WAFER_DEPTH and only is None:
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

    say(f"다 받아서 건너뛰는 단계: {', '.join(sorted(DONE)) or '없음'}")
    for k, raw in enumerate(PAIR_LOTS if "pair" not in DONE else []):   # 비교 스캔 LOT 은 먼저, 웨이퍼 전부
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
    if not stop() and "rich" not in DONE:
        collect_rich_surface(col, say, progress, stop)    # 비교 웨이퍼 RichSurface 전체 · 필요한 열만
    if not stop() and "export" not in DONE:
        wafers += collect_exports(col, say, progress, stop)
    pair_raw = col.raw                                     # 아래 용량 한도는 비교 LOT·RichSurface 를 뺀 양으로 잰다(시간은 전체 1시간)

    roots = {_aoi_key(r.parent.name): r for r in nas_roots()}
    say(f"Scanresult {len(roots)}곳: " + ", ".join(sorted(roots)))
    jobs = []
    for i, (aoi, job, setup, lot_name) in enumerate(KNOWN_LOTS if "known" not in DONE else []):
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


# RichSurface — Defect 후보마다 Contrast · 면적 · 폭이 든 유일한 파일(웨이퍼당 약 500 MB, 레코드 456 바이트).
# 앞 108 MB 를 받아 보니 RTP 필터(Contrast 평균 · 면적 · 폭)로 장비 Defect 2,291개가 오차 0 으로 나왔다(2026-11-15).
# 이제 **파일 전체**를 읽되 미리보기에 쓰는 열만 CSV 로 담는다(원본의 약 1/8, 압축하면 더 작다).
RICH_LOT = PAIR_LOTS[4]            # 4F-AOI-04 YDD-No Image — 비교 웨이퍼가 든 LOT
RICH_FILE = "s_RichSurface.dat"
RICH_COLS = ("reclassify", "Type", "x", "y", "zone", "recipe", "AlgIndex", "area", "Breadth", "Length",
             "AbsDeltaFromRef_Average", "AbsDeltaFromRef_Max", "SignedDeltaFromRef_Average", "GrayLevel_Average",
             "Polarity")
RICH_ROWS = 200_000                # CSV 하나에 담는 후보 수(약 11 MB, zip 조각 하나보다 작다)
_VARTYPE = {"2": "h", "3": "i", "4": "f", "5": "d", "17": "B"}     # .md 의 Vartype → struct


def find_rich_surface():
    """비교 웨이퍼(:data:`PAIR_WAFER`)의 ``s_RichSurface.dat``.  못 찾으면 None."""
    lot = resolve_pair(RICH_LOT)
    if lot is None:
        return None
    for _, d, _, _ in sorted(_entries(lot)[0]):
        f = d / RICH_FILE
        if PAIR_WAFER in d.name.upper() and f.is_file():
            return f
    return None


def collect_exports(col, say, progress, stop):
    """:data:`EXPORT_WAFERS` 의 Defects.csv(폴더 전부 표시면 웨이퍼 폴더 전체)를 ``Export/<장비>/<LOT>/<웨이퍼>`` 로 담는다.
    Export 를 안 눌러 Defects.csv 가 없으면 요약에 적는다.  담은 웨이퍼 수."""
    roots = {}
    try:
        roots = {_aoi_key(r.parent.name): r for r in nas_roots()}
    except OSError as e:
        say(f"Export: Scanresult 를 못 읽음 {e}")
    got, missing = 0, []
    for k, (aoi, job, setup, lot_name, wafer, full) in enumerate(EXPORT_WAFERS):
        if stop():
            break
        progress(k, len(EXPORT_WAFERS), f"Export {aoi} {lot_name} {wafer}"[:70])
        try:
            sr = roots.get(_aoi_key(aoi))
            lot = find_lot(sr, job, setup, lot_name) if sr else None
            w = next((d for _, d, _, _ in sorted(_entries(lot)[0]) if wafer in d.name.upper()), None) if lot else None
            if w is None:
                say(f"Export 웨이퍼 못 찾음: {aoi}\\{job}\\{setup}\\{lot_name}\\*{wafer}*")
                missing.append(f"{aoi} {lot_name} {wafer}")
                continue
            arc = f"Export/{aoi}/{lot_name}/{w.name}"
            if full:
                col.take_files(w, arc)
            else:
                col.take_files(w, arc, only={EXPORT_FILE})
            # 파일목록 줄로 판단한다 — 너무 크거나 못 읽은 파일은 take_files 가 조용히 건너뛰고 그 줄에만 적는다.
            line = next((s for s in reversed(col.listing) if s.startswith(f"{arc}/{EXPORT_FILE}\t")), None)
            if line is None:
                say(f"Defects.csv 없음(Export 전): {w}")
                missing.append(f"{aoi} {lot_name} {wafer}")
                continue
            if "\t담음" not in line:
                say(f"Defects.csv 못 담음({line.split(chr(9), 2)[-1]}): {w}")
                missing.append(f"{aoi} {lot_name} {wafer}")
                continue
            got += 1
            say(f"Export {w}")
        except OSError as e:                   # NAS 권한 · 연결 끊김 — 다음 웨이퍼로
            say(f"Export 못 읽음: {aoi} {lot_name} {wafer}  {e}")
            missing.append(f"{aoi} {lot_name} {wafer}")
    say(f"Export Defects.csv {got}/{len(EXPORT_WAFERS)}" + (f" — 없음: {', '.join(missing)}" if missing else ""))
    return got


def collect_rich_surface(col, say, progress, stop):
    """파일 전체에서 :data:`RICH_COLS` 열만 :data:`RICH_ROWS` 줄씩 CSV 로 담는다.  담은 CSV 수를 돌려준다."""
    src = None
    try:                                       # Windows 는 is_dir() 도 권한 · 연결 끊김(WinError 64/59)을 올린다(수집 자료 쓰레드 지적)
        src = find_rich_surface()
        if src is None:
            say(f"RichSurface 못 찾음: {RICH_LOT}\\*{PAIR_WAFER}*\\{RICH_FILE}")
            return 0
        return _take_rich_surface(col, src, say, progress, stop)
    except OSError as e:                       # NAS 권한 · 연결 끊김 — 나머지 수집은 계속한다
        say(f"RichSurface 못 읽음: {src or RICH_LOT}  {e}")
        return 0


def rich_layout(md_text: str):
    """``.md`` → (레코드 크기, [(열 이름, 오프셋, struct 문자)]) — :data:`RICH_COLS` 중 있는 것만, 오프셋 순."""
    size = int(re.search(r'RecordSize Size="(\d+)"', md_text).group(1))
    fields = {n: (int(o), _VARTYPE.get(v)) for n, o, v in
              re.findall(r'Name="([^"]+)" Id="\d+" Offset="(\d+)" Vartype="(\d+)"', md_text)}
    cols = sorted((fields[n][0], n, fields[n][1]) for n in RICH_COLS if n in fields and fields[n][1])
    return size, [(n, o, t) for o, n, t in cols]


def _take_rich_surface(col, src, say, progress, stop):
    size = src.stat().st_size
    arc = f"RichSurface/{src.parent.name}"
    md = src.with_name(src.name + ".md")
    if not md.is_file():
        say(f"RichSurface .md 없음(열 위치를 모름): {md}")
        return 0
    md_text = md.read_text(encoding="utf-8", errors="replace")
    col.zf.writestr(f"{arc}/{md.name}", md_text)
    rec, cols = rich_layout(md_text)
    fmt, pos = "<", 0
    for _, off, t in cols:
        fmt += f"{off - pos}x{t}" if off > pos else t
        pos = off + struct.calcsize("<" + t)
    unpack = struct.Struct(fmt + f"{rec - pos}x").iter_unpack
    header = ",".join(n for n, _, _ in cols)
    show = [(".2f" if n in ("x", "y") else ".9g") if t in "fd" else "d" for n, _, t in cols]   # .9g = float32 그대로(경계값이 안 바뀐다)
    rows, made, total, done = [], 0, size // rec, 0

    def flush():
        nonlocal rows, made
        made += 1
        part, rows = rows[:RICH_ROWS], rows[RICH_ROWS:]
        col.zf.writestr(f"{arc}/RichSurface_{made:02d}.csv", header + "\n" + "\n".join(part) + "\n")
        col.raw += sum(map(len, part))
        col.files += 1

    with src.open("rb") as f:
        while not stop():
            data = f.read(rec * 20_000)
            data = data[:len(data) // rec * rec]
            if not data:
                break
            for r in unpack(data):
                rows.append(",".join(format(v, s) for v, s in zip(r, show)))
            done += len(data) // rec
            progress(done, total, f"RichSurface 읽는 중 {done:,}/{total:,}")
            while len(rows) >= RICH_ROWS:
                flush()
    while rows:
        flush()
    col.zf.writestr(f"{arc}/정보.txt", f"{src}\n원래 크기 {size}\n레코드 {rec} 바이트 · {total}개 중 {done}개 읽음\n"
                                       f"열: {header}\n")
    say(f"RichSurface {src}  ({size / 1e6:.0f} MB, 후보 {done:,}/{total:,} · CSV {made}개)")
    return made


if __name__ == "__main__":
    desk = Path.home() / "Desktop"
    try:
        print(run(desk if desk.is_dir() else Path.home()))
    finally:
        input("끝났습니다. 바탕화면 Threshold수집_* 폴더 안 zip 을 올려 주세요. (Enter 로 닫기)")
