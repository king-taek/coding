r"""[Surface없음수집-임시] Surface.flt 없는 폴더 수집 — Overkill 튜닝이 못 여는 폴더를 모은다(2026-11-23).

읽기 전용 · 표준 파이썬만 쓴다(앱 코드를 불러오지 않는다).  앱의 개발자 모드 버튼
(``ui/noflt_collect_tmp.py``)이 :func:`run` 을 부르고, VS Code 에서 바로 돌려도 된다.

왜: ``R_TB500_LIVE_PI4 - Enhanced\Setup1\YDD-PIDS3 RE`` 의 5 웨이퍼 중 3 곳은 사진만 있고 결과 파일
(ini · flt · Defects.csv)이 없었다(1차 수집 2026-11-23).  현택님이 장비 화면에서 그 3 웨이퍼를 열고 Export 를
누른 뒤(2026-11-23 13:00 KST), 무엇이 새로 생겼는지(특히 ``Defects.csv``) 본다.

무엇을 모으나:
    1. :data:`TARGETS` 웨이퍼 폴더 — 사진이 아닌 파일 전부(하위 폴더 :data:`TARGET_DEPTH` 단계까지).
       사진은 이름만 ``사진목록.txt``(이름 끝 숫자 = 레시피 번호 확인용).
    2. 그 LOT 폴더의 **파일만**(Export 가 LOT 쪽에 썼을 때를 대비).
    1차 수집에서 담은 부모(셋업 · Job) 파일 · 옆 LOT 목록 · 비교용 웨이퍼는 뺐다(이미 받았다).
무엇을 버리나: 압축 · 영상 파일, :data:`FILE_MB` MB 를 넘는 파일(이름 · 크기 · 시각만 ``파일목록.txt``), 내용이 같은 파일.
결과: 바탕화면 ``Surface없음수집_<시각>\`` 폴더 안 zip.  :data:`PART_MB` MB 를 넘으면 나눈다.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import zipfile
import zlib
from datetime import datetime
from pathlib import Path, PureWindowsPath

# 사진만 있던 웨이퍼 3 곳 — 현택님이 장비 화면에서 열고 Export 를 누른다(2026-11-23 12:59 KST).
_LOT = r"I:\4F-AOI-04\Scanresult\R_TB500_LIVE_PI4 - Enhanced\Setup1\YDD-PIDS3 RE"
TARGETS = [_LOT + "\\" + w for w in ("35174142EWE5", "35174140EWD4", "35174664EWD0")]
UNC_SHARES = [r"\\10.142.80.91\nas3-1"]
MINUTES = 20        # 전체 시간 상한
FILE_MB = 30        # 이보다 큰 파일은 이름만 남긴다(사용자 지시)
PART_MB = 28        # 30 MB 조각은 올라가지 않았다(사용자 2026-11-15)
TARGET_DEPTH = 3    # 웨이퍼 폴더 안 하위 폴더 깊이(웨이퍼 → Zones → …)
MB = 2 ** 20
_ENTRY_OVERHEAD = 76 + 1024     # zip 머리 · 목록 칸(이름 길이 제외) — 조각 크기를 잴 때와 같게 넉넉히
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
            if expired():
                return
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
            info = zipfile.ZipInfo(f"{arc}/{name}", time.localtime(mtime)[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            packed = len(zlib.compress(data, 6))
            if packed + _ENTRY_OVERHEAD + 2 * len(info.filename.encode()) > PART_MB * MB:
                self.listing[-1] = f"{arc}/{name}\t{size}\t뺌(압축해도 {PART_MB} MB 초과)\t{stamp}"
                continue
            self.hashes[h] = f"{arc}/{name}"
            self.zf.writestr(info, data)
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
        sizes = [(i.filename, i.compress_size + _ENTRY_OVERHEAD + 2 * len(i.filename.encode())) for i in src.infolist()]
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
    """zip 안 경로 — ``4F-AOI-04/Job/.../폴더``."""
    sp = _split(raw)
    rel = [sp[0], *sp[1]] if sp else [f"폴더{k}"]
    return "/".join(rel)


def collect(col, targets, say, progress, expired, roots=None):
    """웨이퍼 폴더와 그 LOT 폴더의 파일을 담는다.  찾은 웨이퍼 수."""
    found = 0
    lots_done = set()
    for k, raw in enumerate(targets, 1):
        if expired():
            say(f"시간 제한 · 중지로 {k - 1}/{len(targets)} 에서 멈춤")
            break
        progress(0, 0, f"웨이퍼 {k}/{len(targets)} {PureWindowsPath(raw).name}")
        t = locate(raw, roots)
        if t is None:
            say(f"못 찾음: {raw}")
            continue
        arc = _arc_for(raw, k)
        before, photos_before = len(col.listing), len(col.photos)
        col.take(t, arc, TARGET_DEPTH, expired)
        rows = [ln.split("\t") for ln in col.listing[before:]]
        names = [r[0][len(arc) + 1:] for r in rows]
        csv = [n for n in names if n.rsplit("/", 1)[-1].lower() == "defects.csv"]
        flts = sorted({n.rsplit("/", 1)[-1] for n in names if n.lower().endswith(".flt")})
        newest = max((r[-1] for r in rows), default="없음")
        found += 1
        say(f"담음: {t}  파일 {len(names)} · 사진 {len(col.photos) - photos_before} · Defects.csv {len(csv)}"
            f" · flt 종류 {', '.join(flts) or '없음'} · 가장 최근 파일 {newest}")
        lot, lot_arc = t.parent, arc.rsplit("/", 1)[0]
        if lot_arc.lower() not in lots_done:
            lots_done.add(lot_arc.lower())
            col.take(lot, lot_arc, 0)
    return found


def run(out: Path, progress=None, stop=None, targets=None, roots=None):
    """수집하고 ``(결과 폴더, 폴더 수, 담은 파일 수)`` 를 돌려준다.  아무것도 못 찾으면 ``(None, 0, 0)``.

    ``progress(done, total, 문구)`` · ``stop()`` 이 참이면 그만 찾고 지금까지 모은 것을 남긴다."""
    progress = progress or (lambda *_: None)
    stop = stop or (lambda: False)
    targets = TARGETS if targets is None else targets
    t0 = time.time()
    deadline = t0 + MINUTES * 60
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = out / f"Surface없음수집_{stamp}"
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
        found = collect(col, targets, say, progress, expired, roots)
    except Exception as e:                      # noqa: BLE001 — 수집 도구는 끝까지 zip 을 남긴다
        say(f"오류로 멈춤 — {type(e).__name__}: {e}")
        found = 0
    summary = "\n".join([
        f"Surface.flt 없는 폴더 수집 {stamp}  ({time.time() - t0:.0f}초)",
        f"웨이퍼 {found}/{len(targets)} · 담은 파일 {col.files}"
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
    split_zip(staging, out_dir, f"Surface없음수집_{stamp}")
    return out_dir, found, col.files


if __name__ == "__main__":
    desk = Path.home() / "Desktop"
    try:
        print(run(desk if desk.is_dir() else Path.home()))
    finally:
        input("끝났습니다. 바탕화면 Surface없음수집_* 폴더 안 zip 을 올려 주세요. (Enter 로 닫기)")
