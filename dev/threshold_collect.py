"""Threshold 자료 수집 — Kendall · TB500 레시피의 검출 조건(Width·Area·Contrast)과 결함 bin 이름이
어느 파일에 있는지 찾으려고 NAS 의 Scanresult 원재료를 모은다(2026-11-13, 사용자 지시).

읽기 전용 · 표준 파이썬만 쓴다(앱 코드를 불러오지 않는다).  앱의 개발자 모드 버튼
(``ui/threshold_collect_tmp.py``)이 :func:`run` 을 부르고, 더블클릭으로도 돈다.

무엇을 모으나:
    · 이 PC 에서 보이는 ``AOI-*\\Scanresult\\<Job>`` 가운데 Job 이름에 KENDALL 또는 TB500 이 든 것
    · Job · Setup 폴더 바로 아래 파일, Job 마다 최근 LOT :data:`LOTS` 개의 LOT 폴더 바로 아래 파일
    · LOT 마다 웨이퍼 :data:`WAFERS` 장의 폴더 안 파일(하위 폴더 2단계까지)
무엇을 버리나(확실히 필요 없는 것):
    · 사진 · 영상 · 압축 파일 — 사진은 **이름만** ``사진목록.txt`` 에 남긴다(LIVE 이름에 결함 이름이 든다)
    · :data:`FILE_MB` 를 넘는 파일 — 이름 · 크기만 ``파일목록.txt`` 에 남긴다
    · 내용이 똑같은 파일 — 한 번만 담고 ``중복목록.txt`` 에 적는다(레시피 사본이 웨이퍼마다 있다)
결과: 바탕화면 ``Threshold수집_<시각>\\`` 폴더 안 zip.  :data:`PART_MB` 를 넘으면 나누되,
나눈 조각은 :data:`MIN_PART_MB` 이상이 되게 채운다(마지막 조각만 작을 수 있다).
"""

from __future__ import annotations

import hashlib
import os
import time
import zipfile
from datetime import datetime
from pathlib import Path

PRODUCTS = ("KENDALL", "TB500")
UNC_SHARES = [r"\\10.142.80.91\nas3-1"]
WAFER_MARK = ("ColorImageGrabingInfo.ini", "Params_WaferInfo.ini")
LOTS = 3            # Job 마다 최근 LOT 몇 개
WAFERS = 2          # LOT 마다 웨이퍼 몇 장
MINUTES = 40        # 이 시간이 지나면 거기까지 모은 것으로 정리한다
FILE_MB = 20        # 이보다 큰 파일은 이름만 남긴다
TOTAL_MB = 400      # 담은 원본이 이만큼 되면 그만 찾는다
PART_MB = 30
MIN_PART_MB = 25
SKIP_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif",
            ".avi", ".mp4", ".wmv", ".zip", ".7z", ".rar", ".gz", ".cab"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif"}
_LOT_DEPTH = 4      # Job 아래 LOT 을 찾는 깊이(Setup → [구분] → LOT)
_WAFER_DEPTH = 2    # 웨이퍼 폴더 안 하위 폴더 깊이


def is_product_job(name: str) -> bool:
    up = name.upper()
    return any(p in up for p in PRODUCTS)


def nas_roots():
    """이 PC 에서 보이는 ``<드라이브 또는 공유>\\AOI-*\\Scanresult`` 전부(같은 장비는 한 번만)."""
    bases = [Path(f"{c}:\\") for c in "DEFGHIJKLMNOPQRSTUVWXYZ"] + [Path(u) for u in UNC_SHARES]
    out, seen = [], set()
    for b in bases:
        try:
            for d in sorted(b.glob("AOI-*")):
                sr = next((p for p in (d / "Scanresult", d / "ScanResult", d / "ScanResults") if p.is_dir()), None)
                if sr is not None and d.name.upper() not in seen:
                    seen.add(d.name.upper())
                    out.append(sr)
        except OSError:
            continue
    return out


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
    log, wafers = [], 0

    def expired():
        return stop() or time.time() > deadline or col.raw > TOTAL_MB * 2 ** 20

    def say(line):
        log.append(line)
        print(line)
        progress(0, 0, line[:70])

    roots = nas_roots()
    say(f"Scanresult {len(roots)}곳: " + ", ".join(str(r) for r in roots))
    jobs = []
    for r in roots:
        for name, path, _, _ in sorted(_entries(r)[0]):
            if is_product_job(name):
                jobs.append((r.parent.name, path))
    say(f"Kendall·TB500 Job {len(jobs)}개")
    for i, (aoi, job) in enumerate(jobs):
        if expired():
            say("시간 제한·중지·용량 한도로 여기서 멈춤")
            break
        progress(i, len(jobs), f"{aoi} {job.name}"[:70])
        arc_job = f"{aoi}/{job.name}"
        col.take_top_files(job, arc_job)
        setups = sorted(_entries(job)[0], key=lambda t: -t[2])[:10]
        for name, path, _, _ in setups:                    # Setup 폴더 바로 아래 파일(레시피 사본이 있을 수 있다)
            if not is_wafer(path):
                col.take_top_files(path, f"{arc_job}/{name}")
        for lot in find_lots(job, LOTS, expired):
            arc_lot = f"{arc_job}/{lot.relative_to(job).as_posix()}"
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
