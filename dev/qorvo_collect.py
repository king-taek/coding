"""QORVO 번호 자료 수집 — QORVO 번호 쓰레드 전용(2026-11-15, 사용자 지시 '쓰레드마다 수집 버튼 따로').

읽기 전용 · 표준 파이썬만 쓴다(앱 코드를 불러오지 않는다).  앱의 개발자 모드 버튼
(``ui/qorvo_collect_tmp.py``)이 :func:`run` 을 부르고, 더블클릭으로도 돈다.
공통 수집기(``dev/threshold_collect.py``, dev c45b585)의 QORVO 단계와 도움 함수를 옮겨 왔다.

무엇을 모으나(앞 단계부터 — 작고 급한 것 먼저):
    1. 번호상자 — :data:`BOX_FIRST` 장비(AOI-11)의 **모든 Job** 에서 최근 LOT :data:`BOX_FIRST_LOTS` 개, 웨이퍼 1장의
       :data:`BOX_FILES` 만.  장비 번호 상자(``ProductInfo.ini [ExportData]``)가 고객 맵(``WaferMapRecipe.ini``
       RefPoint−CustPoint)보다 넓은 스캔이 그 장비(108150)에서 QORVO 말고도 나오는지 보려는 자료다.
       다른 장비의 '모든 Job 웨이퍼 1장' 은 [고칠 곳 자료 수집](``dev/data_mining_collect.py``)이 모은다(겹치지 않게 나눔).
    2. QORVO Scanresult — :data:`QORVO_KNOWN_LOTS`(스캔 기록에서 고른 LOT)와 :data:`W97218_KEY` Job 의 모든 셋업에서
       LOT 마다 웨이퍼 :data:`QORVO_INI_WAFERS` 장의 :data:`QORVO_INI` 만(:data:`QORVO_INI_PREFER` 웨이퍼 먼저).
    3. QORVO Conder — 고객 폴더 ``*QORVO*`` 아래 장치마다 최근 LOT :data:`CONDER_LOTS` 개와 :data:`CONDER_MUST`:
       단계 폴더의 맵 txt · csv · lot 과 웨이퍼 사진 **이름**(±2 시프트 탐색을 여러 LOT 에서 검산하는 자료).
무엇을 버리나: 사진 · 영상 · 압축 파일(사진은 **이름만** ``사진목록.txt``), :data:`FILE_MB` 를 넘는 파일(이름 · 크기만
``파일목록.txt``), 내용이 똑같은 파일(한 번만 담고 ``중복목록.txt``).
결과: 바탕화면 ``QORVO수집_<시각>\\`` 폴더 안 zip.  :data:`PART_MB` 를 넘으면 나눈다.
"""

from __future__ import annotations

import hashlib
import re
import os
import time
import zipfile
from datetime import datetime
from pathlib import Path

UNC_SHARES = [r"\\10.142.80.91\nas3-1"]
CONDER_ROOTS = [r"\\k5cifsn2\k5tsvdata$\1. Conder Scan"]
WAFER_MARK = ("ColorImageGrabingInfo.ini", "Params_WaferInfo.ini")
MINUTES = 40        # 전체 시간 상한 — 지나면 거기까지 모은 것으로 정리한다
FILE_MB = 20        # 이보다 큰 파일은 이름만 남긴다
TOTAL_MB = 300      # 담은 원본이 이만큼 되면 그만 찾는다
PART_MB = 28        # 올릴 수 있는 한도가 30 MB 보다 작다(30 MB 조각 2개가 안 올라감, 사용자 2026-11-15)
SKIP_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif",
            ".avi", ".mp4", ".wmv", ".zip", ".7z", ".rar", ".gz", ".cab"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif"}
_LOT_DEPTH = 4      # Job 아래 LOT 을 찾는 깊이(Setup → [구분] → LOT)
_WAFER_DEPTH = 2    # 웨이퍼 폴더 안 하위 폴더 깊이

# 1. 번호상자 — 작은 ini 셋(웨이퍼당 수십 KB).  EquipmentInfo · WaferInfo.ini 에 장비 번호(EagleTP_108150 꼴)가 든다.
BOX_FILES = ("ProductInfo.ini", "WaferMapRecipe.ini", "WaferInfo.ini", "EquipmentInfo.ini", "Params_WaferInfo.ini")
BOX_FIRST = ("AOI-11",)     # 번호 상자가 넓었던 장비(6412 CTX, EagleTP_108150) — 먼저, Job 마다 LOT 여러 개
BOX_FIRST_LOTS = 3
BOX_MINUTES = 12

# 2. QORVO Scanresult — 스캔 기록(AOI_capacity.csv)에서 고른 LOT (장비, Job, Setup, LOT).  공통 수집기에서 옮김.
QORVO_KNOWN_LOTS = [
    ('AOI-11', '2D@RE-W97253Z6B1K16_0860752PD-0A', '6412', 'TWT'),  # 25장 — 같은 장비 AOI-11 의 다른 Job(먼저)
    ('AOI-8', '2D@R2-W96721Z5BK16_0858867PD-0A', '6321', 'NPG-EDGE'),
    ('AOI-6', '2D@R2-W96760Z5BK16_0856722PD-0A', '6321', 'THV EDGE'),
    ('AOI-8', '2D@R2-W96798Z5BK16_0857146PD-0B', '6321', 'PHK-EDGE'),
    ('AOI-8', '2D@R2-W96816Z5BK16_0857248PD-0B', '6321', 'THW-EDGE'),
    ('AOI-8', '2D@R2-W96817Z5BK16_0857253PD-0A', '6321', 'UUT EDGE'),
    ('AOI-6', '2D@RE-W97115Z6B1K16_0858971PD-0A', '6412', 'TWN'),
    ('AOI-4', '2D@RE-W97135Z6BK16_0859176PD-0A', '6412', 'TWK'),
]
W97218_KEY = "W97218"       # 6H7ENK(CTX) · 6H7EMK(CTK) 의 Job — 모든 장비 · 모든 셋업
QORVO_INI = ("ProductInfo.ini", "WaferMapRecipe.ini", "Params_WaferInfo.ini", "WaferInfo.ini", "EquipmentInfo.ini",
             "Recipe.ini",
             "ColorImageGrabingInfo.ini",   # 사진별 X, Y — 두 스캔의 같은 die 짝 검산
             "Defects.csv")                 # 장비 Export 결과(Col · Row = 장비 화면 번호) — 있으면 정답지
QORVO_INI_PREFER = {"SQ67P01-D1", "SQ67P13-E6", "SQ67P25-G3"}   # 번호가 (1,2) 밀린 스캔에 찍힌 웨이퍼
QORVO_INI_LOTS = 40         # Job 마다 LOT 몇 개까지(최근 것부터)
QORVO_INI_WAFERS = 2        # LOT 마다 웨이퍼 몇 장
QORVO_MINUTES = 12

# 3. QORVO Conder
QORVO_NAME = "QORVO"        # Conder 고객 폴더 이름(예: 575. QORVO US)
CONDER_LOTS = 8             # 장치 폴더마다 최근 LOT 몇 개
CONDER_MUST = [r"575. QORVO US\44. W97218SGZ4BK16\75. 6H7ENK (CTX)"]   # 번호가 (1,2) 밀린 LOT — 늘 통째로
CONDER_MINUTES = 12


def nas_roots():
    """이 PC 에서 보이는 ``<드라이브 또는 공유>\\*AOI-*\\Scanresult`` 전부(같은 장비는 한 번만)."""
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


def find_lot(sr: Path, job: str, setup: str, lot_name: str):
    """``<Scanresult>\\<Job>\\<Setup>\\<LOT>`` — Setup 과 LOT 사이에 구분 폴더가 한 단 더 있어도 찾는다."""
    lot = sr / job / setup / lot_name
    if not lot.is_dir():
        lot = next((d / lot_name for _, d, _, _ in _entries(sr / job / setup)[0] if (d / lot_name).is_dir()), lot)
    return lot if lot.is_dir() else None


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


class Collector:
    """고른 파일을 작업용 zip 하나에 담는다(내용이 같은 파일은 한 번만)."""

    def __init__(self, staging: Path):
        self.zf = zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED)
        self.hashes = {}
        self.photos, self.listing, self.dups = [], [], []
        self.raw = 0
        self.files = 0

    def take_files(self, folder: Path, arc: str, depth: int = 0, only=None, photos=False, skip=()):
        """``only`` 를 주면 그 이름의 파일만 담는다(하위 폴더는 내려가지 않는다).  ``photos`` 면 ``only`` 가 있어도
        사진 이름은 남긴다.  ``skip`` 의 이름은 아예 건너뛴다."""
        dirs, files = _entries(folder)
        for name, path, _, size in sorted(files):
            ext = Path(name).suffix.lower()
            if name in skip:
                continue
            if ext in IMG_EXT:
                if only is None or photos:
                    self.photos.append(f"{arc}/{name}")
                continue
            if only is not None and name not in only:
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
        if depth < _WAFER_DEPTH and only is None:
            for name, path, _, _ in sorted(dirs):
                self.take_files(path, f"{arc}/{name}", depth + 1, skip=skip)

    def take_top_files(self, folder: Path, arc: str, skip=()):
        """그 폴더 바로 아래 파일만(하위 폴더는 내려가지 않는다)."""
        self.take_files(folder, arc, depth=_WAFER_DEPTH, skip=skip)

    def close(self, summary: str):
        for name, lines in (("사진목록.txt", self.photos), ("파일목록.txt", self.listing),
                            ("중복목록.txt", self.dups)):
            self.zf.writestr(name, "\n".join(lines) + "\n")
        self.zf.writestr("요약.txt", summary)
        self.zf.close()


def _deadline(minutes: float, end: float) -> float:
    return min(time.time() + minutes * 60, end)


def collect_boxes(col, say, progress, stop, end):
    """1. 번호상자 — :data:`BOX_FIRST` 장비의 모든 Job, 최근 LOT 웨이퍼 1장에서 :data:`BOX_FILES` 만.  웨이퍼 수."""
    deadline = _deadline(BOX_MINUTES, end)

    def expired():
        return stop() or time.time() > deadline

    first = {_aoi_key(a) for a in BOX_FIRST}
    wafers = 0
    for r in [r for r in nas_roots() if _aoi_key(r.parent.name) in first]:
        aoi = r.parent.name
        for name, job, _, _ in sorted(_entries(r)[0], key=lambda t: -t[2]):
            if expired():
                say("번호상자: 시간 제한·중지로 여기서 멈춤")
                return wafers
            for lot in find_lots(job, BOX_FIRST_LOTS, expired):
                ws = pick_wafers(lot, 1)
                if not ws or lot == job:
                    continue
                progress(0, 0, f"번호상자 {aoi} {name}"[:70])
                col.take_files(ws[0], f"번호상자/{aoi}/{name}/{lot.relative_to(job).as_posix()}/{ws[0].name}",
                               only=set(BOX_FILES))
                wafers += 1
        say(f"번호상자 {aoi} 끝")
    return wafers


def _take_qorvo_lot(col, sr: Path, job: Path, lot: Path, n: int, expired, progress):
    arc_lot = f"QORVO/Scanresult/{sr.parent.name}/{job.name}/{lot.relative_to(job).as_posix()}"
    col.take_top_files(lot.parent, arc_lot.rsplit("/", 1)[0])    # 셋업 폴더 바로 아래 파일
    col.take_top_files(lot, arc_lot)
    ws = pick_wafers(lot, 1000)
    ws.sort(key=lambda p: p.name.upper() not in QORVO_INI_PREFER)
    for w in ws[:n]:
        if expired():
            break
        progress(0, 0, f"QORVO {job.name} {w.name}"[:70])
        col.take_files(w, f"{arc_lot}/{w.name}", only=set(QORVO_INI), photos=True)
    return min(n, len(ws))


def collect_qorvo_scan(col, say, progress, stop, end):
    """2. QORVO Scanresult — 스캔 기록 LOT 먼저, 그다음 :data:`W97218_KEY` Job 의 모든 셋업.  웨이퍼 수."""
    deadline = _deadline(QORVO_MINUTES, end)

    def expired():
        return stop() or time.time() > deadline

    roots = {_aoi_key(r.parent.name): r for r in nas_roots()}
    wafers, done = 0, set()
    for aoi, job, setup, lot_name in QORVO_KNOWN_LOTS:
        if expired():
            break
        sr = roots.get(_aoi_key(aoi))
        lot = find_lot(sr, job, setup, lot_name) if sr else None
        if lot is None:
            say(f"QORVO LOT 못 찾음: {aoi}\\{job}\\{setup}\\{lot_name}")
            continue
        wafers += _take_qorvo_lot(col, sr, sr / job, lot, 1, expired, progress)
        done.add(lot)
        say(f"QORVO Scanresult LOT {lot}")
    for sr in roots.values():
        for name, job, _, _ in sorted(_entries(sr)[0]):
            if expired() or W97218_KEY not in name.upper():
                continue
            col.take_top_files(job, f"QORVO/Scanresult/{sr.parent.name}/{name}")
            for lot in find_lots(job, QORVO_INI_LOTS, expired):
                if lot == job or lot in done:
                    continue
                wafers += _take_qorvo_lot(col, sr, job, lot, QORVO_INI_WAFERS, expired, progress)
                say(f"QORVO {W97218_KEY} LOT {lot}")
    if expired():
        say("QORVO Scanresult: 시간 제한·중지로 여기서 멈춤")
    return wafers


def _conder_steps(d: Path, k=0, out=None):
    """LOT 아래 단계 폴더 → (웨이퍼 폴더들).  사진이 바로 들었거나 같은 이름의 맵 txt 가 있는 폴더 = 웨이퍼 폴더."""
    out = {} if out is None else out
    dirs, files = _entries(d)
    ids = {n[:-4].upper() for n, _, _, _ in files if n.lower().endswith(".txt")}
    wafer_dirs = []
    for name, p, _, _ in dirs:
        sub_files = _entries(p)[1]
        if any(Path(n).suffix.lower() in IMG_EXT for n, _, _, _ in sub_files) or name.upper() in ids:
            wafer_dirs.append((name, p))
        elif k < 3:
            _conder_steps(p, k + 1, out)
    if files or wafer_dirs:
        out[d] = wafer_dirs
    return out


def _take_conder_lot(col, lot: Path, arc_lot: str):
    """단계 폴더 파일(맵 txt · csv · lot, Thumbs.db 빼고)과 웨이퍼 폴더의 사진 이름."""
    for step, wafer_dirs in _conder_steps(lot).items():
        arc = arc_lot if step == lot else f"{arc_lot}/{step.relative_to(lot).as_posix()}"
        col.take_top_files(step, arc, skip=("Thumbs.db",))
        for name, p in wafer_dirs:
            col.take_files(p, f"{arc}/{name}", only=set(), photos=True)


def collect_conder(col, say, progress, stop, end):
    """3. QORVO Conder — :data:`CONDER_MUST` 먼저, 그다음 장치마다 최근 LOT.  LOT 수."""
    deadline = _deadline(CONDER_MINUTES, end)

    def expired():
        return stop() or time.time() > deadline

    n, done = 0, set()
    for rel in CONDER_MUST:
        parts = Path(rel.replace("\\", "/")).parts
        lot = next((Path(b).joinpath(*parts) for b in CONDER_ROOTS if Path(b).joinpath(*parts).is_dir()), None)
        if lot is None:
            say(f"Conder LOT 못 찾음: {rel}")
            continue
        progress(0, 0, f"Conder {lot.name}"[:70])
        _take_conder_lot(col, lot, "QORVO/Conder/" + "/".join(parts))
        done.add(lot)
        n += 1
    for base in (Path(r) for r in CONDER_ROOTS):
        customers = [p for nm, p, _, _ in _entries(base)[0] if QORVO_NAME in nm.upper()]
        if not customers:
            say(f"QORVO Conder 폴더 못 찾음: {base}")
        for cust in customers:
            for _, dev, _, _ in sorted(_entries(cust)[0], key=lambda t: -t[2]):
                if expired():
                    break
                for lot in find_conder_lots(dev, CONDER_LOTS, expired):
                    if lot in done:
                        continue
                    progress(0, 0, f"QORVO Conder {lot.name}"[:70])
                    _take_conder_lot(col, lot, f"QORVO/Conder/{cust.name}/{lot.relative_to(cust).as_posix()}")
                    n += 1
                    say(f"QORVO Conder LOT {lot}")
    if expired():
        say("QORVO Conder: 시간 제한·중지로 여기서 멈춤")
    return n


def plan_parts(sizes, part=PART_MB * 2 ** 20):
    """[(이름, 압축 크기)] → 조각별 이름 목록.  앞 조각부터 ``part`` 를 넘지 않게 들어가는 것을 골라 채운다.
    요약 파일은 첫 조각에 둔다."""
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
        sizes = [(i.filename, i.compress_size + 76 + 2 * len(i.filename.encode())) for i in src.infolist()]
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
    """수집하고 ``(결과 폴더, 웨이퍼 · LOT 수, 담은 파일 수)`` 를 돌려준다.  아무것도 없으면 ``(None, 0, 0)``.

    ``progress(done, total, 문구)`` — total 0 이면 진행량 모름.  ``stop()`` 이 참이면 그만 찾고 지금까지 모은 것을 남긴다."""
    progress = progress or (lambda *_: None)
    stop = stop or (lambda: False)
    t0 = time.time()
    end = t0 + MINUTES * 60
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = out / f"QORVO수집_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    staging = out_dir / "_작업중.zip"
    col = Collector(staging)
    log = []

    def say(line):
        log.append(line)
        print(line)
        progress(0, 0, line[:70])

    def halt():
        return stop() or col.raw > TOTAL_MB * 2 ** 20

    roots = nas_roots()
    say(f"Scanresult {len(roots)}곳: " + ", ".join(sorted(r.parent.name for r in roots)))
    boxes = collect_boxes(col, say, progress, halt, end)
    scans = 0 if halt() else collect_qorvo_scan(col, say, progress, halt, end)
    lots = 0 if halt() else collect_conder(col, say, progress, halt, end)
    summary = "\n".join([
        f"QORVO 번호 자료 수집 {stamp}  ({time.time() - t0:.0f}초)",
        f"번호상자 웨이퍼 {boxes} · QORVO Scanresult 웨이퍼 {scans} · QORVO Conder LOT {lots} · 담은 파일 {col.files}"
        f" · 원본 {col.raw / 2 ** 20:.1f} MB · 사진 이름 {len(col.photos)} · 중복 {len(col.dups)}",
        "", "기록:", *log, ""])
    col.close(summary)
    if not col.files and not col.photos:
        staging.unlink(missing_ok=True)
        try:
            out_dir.rmdir()
        except OSError:
            pass
        return None, 0, 0
    progress(0, 0, "zip 나누는 중")
    split_zip(staging, out_dir, f"QORVO수집_{stamp}")
    return out_dir, boxes + scans + lots, col.files


if __name__ == "__main__":
    desk = Path.home() / "Desktop"
    folder, n, files = run(desk if desk.is_dir() else Path.home())
    print(f"\n끝: {folder}  (웨이퍼 · LOT {n}, 파일 {files})")
    input("Enter 를 누르면 닫힙니다")
