"""QORVO 번호 자료 수집 — QORVO 번호 쓰레드 전용(2026-11-15, 사용자 지시 '쓰레드마다 수집 버튼 따로').

읽기 전용 · 표준 파이썬만 쓴다(앱 코드를 불러오지 않는다).  앱의 개발자 모드 버튼
(``ui/qorvo_collect_tmp.py``)이 :func:`run` 을 부르고, 더블클릭으로도 돈다.

무엇을 모으나(2026-11-15 22:15 수집 결과로 좁힘, 사용자 '필요한 것만'):
    AOI-11 의 W97218 LOT 5개(:data:`TARGET_LOTS`)는 기준점이 다른 LOT 과 달라 번호가 (0, 3) 어긋났을 수 있다(계산, 미확인).
    그것을 확인할 자료만 모은다.
    1. Scanresult — 모든 장비의 :data:`W97218_KEY` Job 에서 그 이름의 LOT 폴더(``<Job>\\<Setup>\\<LOT>``).
       AOI-11 과 **다른 장비가 같이 스캔한 웨이퍼**를 :data:`PAIR_WAFERS` 장까지 양쪽 모두, 같이 스캔한 웨이퍼가 없으면
       LOT 마다 1장.  파일은 :data:`WAFER_FILES` 와 사진 **이름**만.
    2. Conder — QORVO 고객 폴더의 W97218 장치 아래, 이름에 ``(<그 LOT>)`` 가 붙은 LOT 폴더의 맵 txt · csv · lot 과 사진 **이름**.
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
MINUTES = 15        # 전체 시간 상한 — 지나면 거기까지 모은 것으로 정리한다
SCAN_MINUTES = 10
CONDER_MINUTES = 5
FILE_MB = 20        # 이보다 큰 파일은 이름만 남긴다
TOTAL_MB = 300      # 담은 원본이 이만큼 되면 그만 찾는다
PART_MB = 28        # 올릴 수 있는 한도가 30 MB 보다 작다(30 MB 조각 2개가 안 올라감, 사용자 2026-11-15)
SKIP_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif",
            ".avi", ".mp4", ".wmv", ".zip", ".7z", ".rar", ".gz", ".cab"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif"}
_WAFER_DEPTH = 2    # 웨이퍼 폴더 안 하위 폴더 깊이

W97218_KEY = "W97218"
TARGET_LOTS = ("APH", "UAP", "AVY", "TKG", "XLW")   # AOI-11 에서 CustPoint (159,157) · 출발점 (0, 314) 이던 LOT(관측 2026-11-15)
FIRST_AOI = "AOI-11"
PAIR_WAFERS = 4     # AOI-11 과 다른 장비가 같이 스캔한 웨이퍼 — LOT 이름마다 이만큼
WAFER_FILES = ("ProductInfo.ini", "WaferMapRecipe.ini", "Params_WaferInfo.ini", "WaferInfo.ini", "EquipmentInfo.ini",
               "Recipe.ini",
               "Wafer2Table.ini",             # 웨이퍼 중심(Params 에 없을 때) — 22:15 수집에 빠져 앱이 중심을 못 찾았다
               "ColorImageGrabingInfo.ini",   # 사진별 X, Y — 두 스캔의 같은 Defect 짝 검산
               "Defects.csv")                 # 장비 Export 결과(Col · Row = 장비 화면 번호) — 있으면 정답지
QORVO_NAME = "QORVO"        # Conder 고객 폴더 이름(예: 575. QORVO US)


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


def target_lot(name: str):
    """LOT 폴더 이름이 :data:`TARGET_LOTS` 중 하나로 시작하면 그 이름(``APH-EDGE`` · ``UAP SRD`` 도 같은 LOT), 아니면 None."""
    tok = re.split(r"[\s\-_]+", name.strip().upper())[0]
    return tok if tok in TARGET_LOTS else None


def find_target_lots(roots, expired):
    """``[(장비 Scanresult, Job, LOT 폴더, LOT 이름, {웨이퍼 이름: 폴더})]`` — ``<Job>\\<Setup>\\<LOT>`` 만 본다
    (2026-11-15 수집의 W97218 웨이퍼 441장이 모두 이 깊이였다)."""
    out = []
    for sr in roots:
        for jname, job, _, _ in sorted(_entries(sr)[0]):
            if expired():
                return out
            if W97218_KEY not in jname.upper():
                continue
            for _, setup, _, _ in sorted(_entries(job)[0]):
                for lname, lot, _, _ in sorted(_entries(setup)[0]):
                    key = target_lot(lname)
                    if key:
                        wafers = {n.upper(): p for n, p, _, _ in _entries(lot)[0] if is_wafer(p)}
                        out.append((sr, job, lot, key, wafers))
    return out


def plan_wafers(found):
    """LOT 폴더마다 담을 웨이퍼.  AOI-11 과 다른 장비에 같이 있는 웨이퍼를 LOT 이름마다 :data:`PAIR_WAFERS` 장까지
    (양쪽 다), 그런 웨이퍼가 없는 LOT 폴더는 1장.  ``(계획, {LOT 이름: 같이 스캔한 웨이퍼})``."""
    first = _aoi_key(FIRST_AOI)
    on_first, elsewhere = {}, {}
    for sr, _, _, key, wafers in found:
        side = on_first if _aoi_key(sr.parent.name) == first else elsewhere
        side.setdefault(key, set()).update(wafers)
    shared = {k: sorted(on_first.get(k, set()) & elsewhere.get(k, set()))[:PAIR_WAFERS] for k in TARGET_LOTS}
    plan = []
    for sr, job, lot, key, wafers in found:
        names = [n for n in shared[key] if n in wafers] or sorted(wafers)[:1]
        plan.append((sr, job, lot, [wafers[n] for n in names]))
    return plan, shared


def collect_scan(col, say, progress, stop, end):
    """1. Scanresult — :data:`TARGET_LOTS` LOT 의 웨이퍼.  웨이퍼 수."""
    deadline = _deadline(SCAN_MINUTES, end)

    def expired():
        return stop() or time.time() > deadline

    progress(0, 0, "Scanresult 에서 LOT 찾는 중")
    plan, shared = plan_wafers(find_target_lots(nas_roots(), expired))
    say("같이 스캔한 웨이퍼: " + ", ".join(f"{k} {len(v)}장" for k, v in shared.items()))
    n = 0
    for k, (sr, job, lot, wafers) in enumerate(plan, 1):
        if expired():
            say("Scanresult: 시간 제한·중지로 여기서 멈춤")
            break
        arc = f"QORVO/Scanresult/{sr.parent.name}/{job.name}/{lot.relative_to(job).as_posix()}"
        progress(k, len(plan), f"Scanresult {sr.parent.name} {lot.name}"[:70])
        col.take_top_files(lot.parent, arc.rsplit("/", 1)[0])    # Setup 폴더 바로 아래 파일
        col.take_top_files(lot, arc)
        for w in wafers:
            col.take_files(w, f"{arc}/{w.name}", only=set(WAFER_FILES), photos=True)
            n += 1
        say(f"Scanresult LOT {lot} 웨이퍼 {len(wafers)}")
    if not plan:
        say("Scanresult: 대상 LOT 없음")
    return n


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
    """2. Conder — QORVO 고객 폴더의 W97218 장치에서 이름에 ``(<대상 LOT>)`` 가 든 LOT(예: ``75. 6H7ENK (CTX)`` 꼴).  LOT 수."""
    deadline = _deadline(CONDER_MINUTES, end)

    def expired():
        return stop() or time.time() > deadline

    pat = re.compile(r"\((%s)\)" % "|".join(TARGET_LOTS), re.I)
    n = 0
    for base in (Path(r) for r in CONDER_ROOTS):
        customers = [p for nm, p, _, _ in _entries(base)[0] if QORVO_NAME in nm.upper()]
        if not customers:
            say(f"QORVO Conder 폴더 못 찾음: {base}")
        for cust in customers:
            for dname, dev, _, _ in sorted(_entries(cust)[0]):
                if W97218_KEY not in dname.upper():
                    continue
                for lname, lot, _, _ in sorted(_entries(dev)[0]):
                    if expired():
                        say("Conder: 시간 제한·중지로 여기서 멈춤")
                        return n
                    if not pat.search(lname):
                        continue
                    progress(0, 0, f"Conder {lname}"[:70])
                    _take_conder_lot(col, lot, f"QORVO/Conder/{cust.name}/{dev.name}/{lname}")
                    n += 1
                    say(f"Conder LOT {lot}")
    if not n:
        say("Conder: 대상 LOT 없음")
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
    scans = collect_scan(col, say, progress, halt, end)
    lots = 0 if halt() else collect_conder(col, say, progress, halt, end)
    summary = "\n".join([
        f"QORVO 번호 자료 수집 {stamp}  ({time.time() - t0:.0f}초)",
        f"Scanresult 웨이퍼 {scans} · Conder LOT {lots} · 담은 파일 {col.files}"
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
    return out_dir, scans + lots, col.files


if __name__ == "__main__":
    desk = Path.home() / "Desktop"
    folder, n, files = run(desk if desk.is_dir() else Path.home())
    print(f"\n끝: {folder}  (웨이퍼 · LOT {n}, 파일 {files})")
    input("Enter 를 누르면 닫힙니다")
