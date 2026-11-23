"""[멀티레시피수집-임시] 멀티 레시피 Defect 정보 수집 — 현택님이 Export 를 눌러 둔 웨이퍼 폴더만 모은다(2026-11-23).

읽기 전용 · 표준 파이썬만 쓴다(앱 코드를 불러오지 않는다).  앱의 개발자 모드 버튼
(``ui/multi_recipe_collect_tmp.py``)이 :func:`run` 을 부르고, 더블클릭으로도 돈다.

왜: 멀티 레시피(예: BS_3D · BS_3) 스캔에서 장비 화면의 Defect 정보(Recipe · Zone · Type · Region · Height ·
area/width/length)가 앱에 안 나온다.  어느 파일에 그 값이 있는지 찾으려면 장비 Export(``Defects.csv``)와
웨이퍼 폴더의 모든 결과 파일(``*.flt`` · ``*.flt.md`` · ini · dat)을 나란히 봐야 한다.

무엇을 모으나:
    1. :data:`WAFERS` 웨이퍼 폴더마다 — 사진이 아닌 파일 전부(하위 폴더 :data:`WAFER_DEPTH` 단계까지,
       ``Zones`` · ``Recipe2-Zones`` 등).  사진은 이름만 ``사진목록.txt``.
    2. 그 부모 폴더(LOT · 셋업 · Job, Scanresult 바로 아래까지)의 **파일만**(하위 폴더는 안 내려간다) —
       Region · 분류 이름 사전이 웨이퍼 폴더 밖에 있을 수 있어서.
무엇을 버리나: 압축 · 영상 파일, :data:`FILE_MB` MB 를 넘는 파일(이름 · 크기만 ``파일목록.txt``), 내용이 같은 파일.
결과: 바탕화면 ``멀티레시피수집_<시각>\\`` 폴더 안 zip.  :data:`PART_MB` MB 를 넘으면 나눈다.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import zipfile
from datetime import datetime
from pathlib import Path, PureWindowsPath

# 현택님이 장비에서 Export 를 눌러 둔 웨이퍼(2026-11-23).  첫 줄이 대표 사례
# (col14 row6 x1379 y1906 = BS_3D Height Defect, col14 row6 x12440 y15354 = BS_3 Surface Defect).
WAFERS = [
    r"V:\AOI-16\Scanresult\2D@R5-AS-T254-A01_0857122AY-0B\6324\CMG-1121D\GX57010487",
    r"V:\AOI-16\Scanresult\2D@R5-AS-T254-A01_0857122AY-0B\6324\CMG-1121D\GX57526603",
    r"V:\AOI-16\Scanresult\2D@R5-AS-T254-A01_0857122AY-0B\6324\CMY-1122N_RESCAN\GX57526737",
    r"V:\AOI-16\Scanresult\2D@R5-AS-T254-A01_0857122AY-0B\6324\FLU-DIA 1122N\GX57527979",
    r"V:\AOI-16\Scanresult\2D@R5-AS-T254-A01_0857122AY-0B\6324\FLU-DIA 1122N\GX57527982",
    r"V:\AOI-16\Scanresult\2D@R5-AS-T254-A01_0857122AY-0B\6324\FLU-1122N\GX57527982",
    r"V:\AOI-16\Scanresult\2D@R5-AS-T254-A01_0857122AY-0B\6324\FLU-1122N\GX57527979",
    r"V:\AOI-16\Scanresult\2D@R5-AS-T254-A01_0857122AY-0B\6324\CMY-11121D\GX57526745",
    r"V:\AOI-16\Scanresult\2D@R5-AS-T254-A01_0857122AY-0B\6324\CMY-1121D\GX57526745",
    r"V:\AOI-16\Scanresult\2D@R5-AS-T254-A01_0857122AY-0B\6324\CMY-1121S\GX57526744",
]
UNC_SHARES = [r"\\10.142.80.91\nas3-1"]
MINUTES = 30        # 전체 시간 상한
FILE_MB = 30        # 이보다 큰 파일은 이름만 남긴다(사용자 지시)
PART_MB = 28        # 30 MB 조각은 올라가지 않았다(사용자 2026-11-15)
WAFER_DEPTH = 3     # 웨이퍼 폴더 안 하위 폴더 깊이
MB = 2 ** 20
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif"}
SKIP_EXT = IMG_EXT | {".avi", ".mp4", ".wmv", ".zip", ".7z", ".rar", ".gz", ".cab"}
_SCANRESULT = re.compile(r"^scanresults?$", re.I)


def nas_roots():
    """이 PC 에서 보이는 ``<드라이브 또는 공유>\\*AOI-*\\Scanresult`` 전부 — 장비 이름(대문자) → 폴더."""
    bases = [Path(f"{c}:\\") for c in "DEFGHIJKLMNOPQRSTUVWXYZ"] + [Path(u) for u in UNC_SHARES]
    out = {}
    for b in bases:
        try:
            for d in sorted(b.glob("*AOI-*")):
                sr = next((p for p in (d / "Scanresult", d / "ScanResult", d / "ScanResults") if p.is_dir()), None)
                if sr is not None:
                    out.setdefault(d.name.upper(), sr)
        except OSError:
            continue
    return out


def _split(raw: str):
    """``V:\\AOI-16\\Scanresult\\Job\\...`` → ('AOI-16', ['Job', ...]).  Scanresult 가 없으면 None."""
    parts = PureWindowsPath(raw).parts
    for i, p in enumerate(parts):
        if _SCANRESULT.match(p) and i > 0:
            return parts[i - 1], list(parts[i + 1:])
    return None


def locate(raw: str, roots=None):
    """적힌 경로가 보이면 그것, 아니면 같은 장비의 다른 드라이브 · 공유에서 같은 상대 경로.  없으면 None."""
    try:
        p = Path(raw)
        if p.is_dir():
            return p
    except OSError:
        pass
    sp = _split(raw)
    if sp is None:
        return None
    aoi, rest = sp
    roots = nas_roots() if roots is None else roots
    sr = roots.get(aoi.upper())
    if sr is None:
        return None
    cand = sr.joinpath(*rest)
    try:
        return cand if cand.is_dir() else None
    except OSError:
        return None


def _entries(d: Path):
    """(폴더들, 파일들) — 각 항목 (이름, 경로, 크기, 수정 시각).  못 읽으면 빈 목록."""
    dirs, files = [], []
    try:
        with os.scandir(d) as it:
            for e in it:
                try:
                    st = e.stat()
                    (dirs if e.is_dir() else files).append((e.name, Path(e.path), st.st_size, st.st_mtime))
                except OSError:
                    continue
    except OSError:
        pass
    return sorted(dirs), sorted(files)


class Collector:
    """고른 파일을 작업용 zip 하나에 담는다(내용이 같은 파일은 한 번만)."""

    def __init__(self, staging: Path):
        self.zf = zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED)
        self.hashes = {}
        self.photos, self.listing, self.dups = [], [], []
        self.files = 0

    def take(self, folder: Path, arc: str, depth: int, expired=lambda: False):
        """``folder`` 의 파일을 담는다.  ``depth`` > 0 이면 하위 폴더도 그만큼 내려간다."""
        dirs, files = _entries(folder)
        for name, path, size, mtime in files:
            ext = Path(name).suffix.lower()
            if ext in IMG_EXT:
                self.photos.append(f"{arc}/{name}")
                continue
            stamp = f"{datetime.fromtimestamp(mtime):%Y-%m-%d %H:%M}"
            if ext in SKIP_EXT or size > FILE_MB * MB:
                self.listing.append(f"{arc}/{name}\t{size}\t뺌\t{stamp}")
                continue
            try:
                data = path.read_bytes()
            except OSError as e:
                self.listing.append(f"{arc}/{name}\t{size}\t못 읽음 {e}\t{stamp}")
                continue
            self.listing.append(f"{arc}/{name}\t{size}\t담음\t{stamp}")
            h = hashlib.sha1(data).hexdigest()
            if h in self.hashes:
                self.dups.append(f"{arc}/{name}\t= {self.hashes[h]}")
                continue
            self.hashes[h] = f"{arc}/{name}"
            self.zf.writestr(f"{arc}/{name}", data)
            self.files += 1
        if depth > 0:
            for name, path, _, _ in dirs:
                if expired():
                    return
                self.take(path, f"{arc}/{name}", depth - 1, expired)

    def zipped(self) -> int:
        return self.zf.fp.tell()

    def close(self, summary: str):
        for name, lines in (("사진목록.txt", self.photos), ("파일목록.txt", self.listing),
                            ("중복목록.txt", self.dups)):
            self.zf.writestr(name, "\n".join(lines) + "\n")
        self.zf.writestr("요약.txt", summary)
        self.zf.close()


def plan_parts(sizes, part=PART_MB * MB):
    """[(이름, 압축 크기)] → 조각별 이름 목록.  앞 조각부터 ``part`` 를 넘지 않게 들어가는 것을 골라 채운다.
    요약 파일은 첫 조각에 둔다."""
    first = [(n, s) for n, s in sizes if n == "요약.txt"]
    queue = [(n, s) for n, s in sizes if n != "요약.txt"]
    parts = []
    while queue or first:
        cur, used, rest = [n for n, _ in first], sum(s for _, s in first), []
        first = []
        for n, s in queue:
            if not cur or used + s <= part:
                cur.append(n)
                used += s
            else:
                rest.append((n, s))
        if rest and len(rest) == len(queue):         # 하나도 안 들어갔다 — 하나는 넣는다
            cur.append(rest.pop(0)[0])
        parts.append(cur)
        queue = rest
    return parts


def split_zip(staging: Path, out_dir: Path, stem: str):
    """작업용 zip 을 :data:`PART_MB` 조각으로 나눈다.  넘지 않으면 이름만 바꾼다."""
    if staging.stat().st_size <= PART_MB * MB:
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


def _arc_for(raw: str, k: int) -> str:
    """zip 안 경로 — ``AOI-16/Job/.../웨이퍼`` (같은 웨이퍼가 두 단계에 있어도 겹치지 않는다)."""
    sp = _split(raw)
    rel = [sp[0], *sp[1]] if sp else [f"웨이퍼{k}"]
    return "/".join(rel)


def collect(col, wafers, say, progress, expired, roots=None):
    """웨이퍼 폴더 · 부모 폴더 파일을 담는다.  (찾은 웨이퍼 수, Defects.csv 수)."""
    found = csv_n = 0
    parents_done = set()
    for k, raw in enumerate(wafers, 1):
        if expired():
            say(f"시간 제한 · 중지로 {k - 1}/{len(wafers)} 에서 멈춤")
            break
        progress(k - 1, len(wafers), f"웨이퍼 {k}/{len(wafers)} {Path(raw).name}")
        w = locate(raw, roots)
        if w is None:
            say(f"못 찾음: {raw}")
            continue
        arc = _arc_for(raw, k)
        before = len(col.listing)
        col.take(w, arc, WAFER_DEPTH, expired)
        names = [ln.split("\t")[0].rsplit("/", 1)[-1] for ln in col.listing[before:]]
        has_csv = any(n.lower() == "defects.csv" for n in names)
        flts = sorted(n for n in names if n.lower().endswith(".flt"))
        csv_n += has_csv
        found += 1
        say(f"담음: {w}  Defects.csv {'있음' if has_csv else '없음'} · flt {', '.join(flts) or '없음'}")
        # 부모(LOT · 셋업 · Job) 폴더의 파일만 — Scanresult 바로 아래(Job)까지.
        up, up_arc = w.parent, arc.rsplit("/", 1)[0]
        while "/" in up_arc and not _SCANRESULT.match(up.name):
            key = up_arc.lower()
            if key not in parents_done:
                parents_done.add(key)
                col.take(up, up_arc, 0)
            up, up_arc = up.parent, up_arc.rsplit("/", 1)[0]
    return found, csv_n


def run(out: Path, progress=None, stop=None, wafers=None, roots=None):
    """수집하고 ``(결과 폴더, 웨이퍼 수, 담은 파일 수)`` 를 돌려준다.  아무것도 못 찾으면 ``(None, 0, 0)``.

    ``progress(done, total, 문구)`` · ``stop()`` 이 참이면 그만 찾고 지금까지 모은 것을 남긴다."""
    progress = progress or (lambda *_: None)
    stop = stop or (lambda: False)
    wafers = WAFERS if wafers is None else wafers
    t0 = time.time()
    deadline = t0 + MINUTES * 60
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = out / f"멀티레시피수집_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    staging = out_dir / "_작업중.zip"
    col = Collector(staging)
    log = []

    def say(line):
        log.append(line)
        print(line)

    def expired():
        return stop() or time.time() > deadline

    try:
        found, csv_n = collect(col, wafers, say, progress, expired, roots)
    except Exception as e:                      # noqa: BLE001 — 수집 도구는 끝까지 zip 을 남긴다
        say(f"오류로 멈춤 — {type(e).__name__}: {e}")
        found, csv_n = 0, 0
    summary = "\n".join([
        f"멀티 레시피 Defect 정보 수집 {stamp}  ({time.time() - t0:.0f}초)",
        f"웨이퍼 {found}/{len(wafers)} · Defects.csv {csv_n} · 담은 파일 {col.files}"
        f" · zip {col.zipped() / MB:.1f} MB · 사진 이름 {len(col.photos)} · 중복 {len(col.dups)}",
        "", "기록:", *log, ""])
    col.close(summary)
    if not found:
        staging.unlink(missing_ok=True)
        try:
            out_dir.rmdir()
        except OSError:
            pass
        return None, 0, 0
    progress(0, 0, "zip 나누는 중")
    split_zip(staging, out_dir, f"멀티레시피수집_{stamp}")
    return out_dir, found, col.files


if __name__ == "__main__":
    desk = Path.home() / "Desktop"
    try:
        print(run(desk if desk.is_dir() else Path.home()))
    finally:
        input("끝났습니다. 바탕화면 멀티레시피수집_* 폴더 안 zip 을 올려 주세요. (Enter 로 닫기)")
