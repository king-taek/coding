"""[고칠곳수집-임시] 고칠 곳 자료 수집 — '수집 자료 고칠 곳' 보고서가 기다리는 자료만 NAS 에서 모은다(2026-11-15).

읽기 전용 · 표준 파이썬만 쓴다(앱 코드를 불러오지 않는다).  앱의 개발자 모드 버튼
(``ui/data_mining_collect_tmp.py``)이 :func:`run` 을 부르고, 더블클릭으로도 돈다.
쓰레드마다 자기 수집 버튼을 둔다(사용자 지시 2026-11-15 — 한 버튼에 모두 모으니 오류가 났다).  그래서 Threshold
수집기(dev c45b585)에서 이 쓰레드 몫 단계와 헬퍼를 복사해 왔고, 그 파일은 불러오지 않는다.

무엇을 모으나 — 단계마다 시간 · zip 크기 상한이 있고, **한 단계가 실패해도 요약에 적고 다음 단계로 간다**:
    1. ``모든Job/`` — NAS 의 모든 Job 에서 최근 LOT 웨이퍼 1장의 :data:`WIDE_FILES` 와 ``Recipe<N>-ProductInfo.ini``
       (``Surface.flt`` 는 이름 · 크기만), 웨이퍼 폴더 · 부모 2단계의 ``Wafer2Table.ini``(블록 제품 중심).  장비마다 가장 최근 Job 하나는 꼭 보고(152 가 아닌 장비를 놓치지 않게),
       나머지는 같은 Job 이름을 장비가 달라도 한 번만 본다(제품 폭이 목적).
       쓰는 곳: 01 다중 레시피(Kendall 밖 제품) · 05 장비별 레코드 크기 · 맵 구멍과 장비 번호(QORVO 번호 쓰레드).
    2. ``구형flt/`` — 레코드가 152 가 아닌 장비(:data:`ODD_SEED` + 1단계에서 찾은 장비)의 Job 최대 :data:`ODD_JOBS` 개 ×
       최근 LOT 웨이퍼 1장의 :data:`ODD_FILES` 와 사진 이름.  쓰는 곳: 05(PR #131 검증).
    3. ``Conder옆폴더/`` — 같은 웨이퍼가 두 단계 이상에 나오는 Conder LOT 의 단계 폴더 파일(수정 시각과 함께)과
       사진 이름.  KLA 사진 폴더의 정보파일(``WaferID`` 헤더)은 본 LOT 도 담는다.  본 LOT 은 담지 않은 것까지 단계 폴더마다 맵 txt · 사진 폴더 이름을 ``Conder이름목록.txt`` 에 적는다.
       쓰는 곳: 06 옆 단계 맵 · 03 폴더 이름표.
무엇을 버리나: 사진 · 영상 · 압축 파일(사진은 이름만 ``사진목록.txt``), :data:`FILE_MB` 를 넘는 파일(이름 · 크기만
``파일목록.txt``), 내용이 같은 파일(한 번만 담고 ``중복목록.txt``).
결과: 바탕화면 ``고칠곳수집_<시각>\\`` 폴더 안 zip.  :data:`PART_MB` 를 넘으면 나누되,
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

UNC_SHARES = [r"\\10.142.80.91\nas3-1"]
CONDER_ROOTS = [r"\\k5cifsn2\k5tsvdata$\1. Conder Scan"]
WAFER_MARK = ("ColorImageGrabingInfo.ini", "Params_WaferInfo.ini")
MINUTES = 85        # 전체 시간 상한 — 단계 상한의 합(78분)보다 조금 길게
FILE_MB = 20        # 이보다 큰 파일은 이름만 남긴다
PART_MB = 28        # 올릴 수 있는 한도가 30 MB 보다 작다(30 MB 조각 2개가 안 올라감, 사용자 2026-11-15)
MIN_PART_MB = 23
MB = 2 ** 20
SKIP_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif",
            ".avi", ".mp4", ".wmv", ".zip", ".7z", ".rar", ".gz", ".cab"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif"}
_LOT_DEPTH = 4      # Job 아래 LOT 을 찾는 깊이(Setup → [구분] → LOT)
_WAFER_DEPTH = 2    # 웨이퍼 폴더 안 하위 폴더 깊이

# 1. 모든 Job — 웨이퍼 1장에서 이 파일들만.  zip 크기 상한은 압축한 크기로 잰다(올리는 양이 기준).
WIDE_FILES = ("s_DieLocation.dat", "s_DieLocation.dat.md", "ColorImageGrabingInfo.ini", "ProductInfo.ini",
              "Params_WaferInfo.ini", "WaferInfo.dat",
              "Surface.flt.md",       # 장비마다 레코드 크기(05, PR #131) — Surface.flt 는 이름 · 크기만
              "Defects.csv",
              "DieLut.dat",           # 다중 레시피 스캔에만 있다 — Recipe*-ProductInfo.ini 와 함께 01 을 다른 제품에서 확인
              "WaferMapRecipe.ini", "WaferInfo.ini",   # QORVO 번호 쓰레드 요청(2026-11-15)
              "Wafer2Table.ini",      # 웨이퍼 중심(블록 제품 — 앱이 웨이퍼 폴더 · 부모 2단계에서 찾는다)
              "EquipmentInfo.ini")    # 장비 번호(EagleTP_108150 같은) — 번호 상자가 넓은 스캔이 그 장비뿐인지(QORVO 번호 쓰레드)
_RECIPE_PI = re.compile(r"Recipe\d+-ProductInfo\.ini$", re.I)
WIDE_FILE_MB = 12        # 파일당 상한
_W2T_PARENTS = 2         # 앱(wafer_geometry._PARENT_LEVELS)이 Wafer2Table.ini 를 찾는 부모 단계 수
WIDE_MINUTES = 60        # Job 2,071 중 232 에서 15분이 끝났다(Job 약 15개/분) — 4배
WIDE_ZIP_MB = 120
# 2. 레코드가 152 바이트가 아닌(구형) 장비만 깊게 — 그 장비의 서로 다른 Job 최대 ODD_JOBS 개 × 최근 LOT 웨이퍼 1장.
ODD_SEED = ("AOI-1", "AOI-5", "AOI-10")          # 이미 확인한 구형(120 · 136 · 136)
ODD_FILES = ("Surface.flt", "Surface.flt.md", "RTP.txt", "Defects.csv")   # + 사진 이름(LIVE 이름의 크기 · 면적과 대조)
ODD_FIRST = ("PI", "LIVE")
ODD_JOBS = 8
ODD_MINUTES = 8
ODD_ZIP_MB = 30
# 3. 같은 웨이퍼가 두 단계 이상에 나오는 Conder LOT — QORVO 는 장치마다, 다른 고객은 고객마다 최근 SIB_LOTS 개.
QORVO_NAME = "QORVO"     # Conder 고객 폴더 이름(예: 575. QORVO US)
SIB_LOTS = 8
SIB_MINUTES = 10
SIB_ZIP_MB = 30


def nas_roots():
    """이 PC 에서 보이는 ``<드라이브 또는 공유>\\*AOI-*\\Scanresult`` 전부(같은 장비는 한 번만).
    ``4F-AOI-05`` 같은 4층 장비도 든다."""
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
    """웨이퍼 폴더인가.  못 읽으면 아니다 — Windows 의 ``is_file`` 은 권한 · 네트워크 오류를 그대로 올린다."""
    try:
        return any((d / m).is_file() for m in WAFER_MARK)
    except OSError:
        return False


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


def _record_size(w: Path):
    """웨이퍼 폴더 ``Surface.flt.md`` 의 RecordSize.  없으면 None."""
    try:
        m = re.search(r'RecordSize Size="(\d+)"', (w / "Surface.flt.md").read_text(errors="replace"))
    except OSError:
        return None
    return int(m.group(1)) if m else None


def _take_wafer2table(col, w: Path, job: Path, arc_job: str):
    """웨이퍼 폴더의 부모(앱이 보는 :data:`_W2T_PARENTS` 단계, Job 폴더까지만)에 있는 ``Wafer2Table.ini`` 를 담는다.
    웨이퍼 폴더 자신의 것은 :data:`WIDE_FILES` 가 담는다."""
    up = w
    for _ in range(_W2T_PARENTS):
        if up == job:
            break
        up = up.parent
        rel = up.relative_to(job).as_posix()
        col.take_files(up, arc_job if rel == "." else f"{arc_job}/{rel}", only={"Wafer2Table.ini"})


_KLA_HEAD = 65536
_KLA_ID = re.compile(rb'Wafer\s*ID\s+"', re.I)
_NON_INFO = (".jpg", ".jpeg", ".pass", ".ini", ".py", ".db")      # coords/kla_info.py 의 _NON_INFO_SUFFIXES 와 같게


def kla_info_names(folder: Path) -> set:
    """사진 폴더의 KLA 정보파일 이름들 — 비-사진 파일 중 헤더에 ``WaferID "..."`` 가 있는 것(확장자 ``.001`` 이거나 없음).
    내용이 0~1 바이트로 빈 후보도 든다(앱이 파일 **이름** 의 WaferID 를 쓰는 경우)."""
    out = set()
    for name, path, _, size in _entries(folder)[1]:
        if Path(name).suffix.lower() in _NON_INFO or name.startswith("."):
            continue
        if size <= 1:
            out.add(name)
            continue
        try:
            with path.open("rb") as fh:
                if _KLA_ID.search(fh.read(_KLA_HEAD)):
                    out.add(name)
        except OSError:
            continue
    return out


def _budget(col, stop, end, minutes: float, zip_mb: float):
    """단계 하나의 '그만' 판단 — 중지 · 단계 시간 · 전체 시간 · 이 단계가 늘린 zip 크기."""
    deadline = time.time() + minutes * 60
    if end is not None:
        deadline = min(deadline, end)
    start = col.zipped()
    return lambda: stop() or time.time() > deadline or col.zipped() - start > zip_mb * MB


def collect_all_jobs(col, say, progress, stop, end=None, odd=None):
    """NAS 의 모든 Job 에서 최근 LOT 중 하나(셋업이 여럿이면 가장 최근이 아닐 수 있다 — 제품 폭이 목적이라 NAS 읽기를
    아낀다)의 웨이퍼 1장에서 :data:`WIDE_FILES` 만 담는다.  장비마다 가장 최근 Job 은 꼭 보고, 나머지는 같은 Job 이름을
    한 번만 본다.  ``odd`` 에 레코드가 152 가 아닌 장비를 더한다.  웨이퍼 수를 돌려준다."""
    expired = _budget(col, stop, end, WIDE_MINUTES, WIDE_ZIP_MB)
    jobs = [(t, r, name, job) for r in nas_roots() for name, job, t, _ in _entries(r)[0]]
    jobs.sort(key=lambda j: -j[0])
    heads = {}
    for j in jobs:
        heads.setdefault(j[1], j)
    order = list(heads.values()) + [j for j in jobs if heads[j[1]] is not j]
    say(f"모든 Job: 장비 {len(heads)}곳 · Job {len(jobs)}개 (최근 것부터, 같은 이름은 한 번)")
    seen, wafers, looked = set(), 0, 0
    for k, (_, r, name, job) in enumerate(order):
        if expired():
            say(f"모든 Job: 시간 · 크기 한도나 중지로 {k}/{len(order)} 에서 멈춤")
            break
        if k >= len(heads) and name.upper() in seen:
            continue
        seen.add(name.upper())
        looked += 1
        progress(k, len(order), f"모든 Job {r.parent.name} {name}"[:70])
        for lot in find_lots(job, 1, expired):
            for w in pick_wafers(lot, 1):
                arc = f"모든Job/{r.parent.name}/{name}/{lot.relative_to(job).as_posix()}/{w.name}"
                col.take_files(w, arc, only=lambda n: n in WIDE_FILES or bool(_RECIPE_PI.match(n)),
                               max_mb=WIDE_FILE_MB, names_only=("Surface.flt",))
                _take_wafer2table(col, w, job, f"모든Job/{r.parent.name}/{name}")
                size = _record_size(w)
                if odd is not None and size not in (None, 152):
                    odd.add(_aoi_key(r.parent.name))
                wafers += 1
    say(f"모든 Job: Job {looked}개를 보고 웨이퍼 {wafers}장을 담음")
    return wafers


def collect_odd_flt(col, say, progress, stop, end=None, odd=()):
    """레코드 크기가 152 가 아닌 장비(``odd`` — 장비 이름)의 Job 을 깊게 모은다.  웨이퍼 수를 돌려준다."""
    expired = _budget(col, stop, end, ODD_MINUTES, ODD_ZIP_MB)
    wafers = 0
    for r in nas_roots():
        aoi = r.parent.name
        if _aoi_key(aoi) not in odd or expired():
            continue
        jobs = sorted(_entries(r)[0], key=lambda e: (not any(k in e[0].upper() for k in ODD_FIRST), -e[2]))
        taken = 0
        for name, job, _, _ in jobs:
            if taken >= ODD_JOBS or expired():
                break
            for lot in find_lots(job, 1, expired):
                for w in pick_wafers(lot, 1):
                    progress(0, 0, f"구형 Surface.flt {aoi} {name}"[:70])
                    col.take_files(w, f"구형flt/{aoi}/{name}/{lot.relative_to(job).as_posix()}/{w.name}",
                                   only=set(ODD_FILES), photos=True)
                    wafers += 1
                    taken += 1
        say(f"구형 Surface.flt {aoi}: Job {taken}개")
    return wafers


def _conder_steps(d: Path, k=0, out=None):
    """LOT 아래 단계 폴더 → (웨이퍼 폴더들, 그 단계의 웨이퍼 ID, 맵 txt 이름들).  사진이 바로 든 폴더 = 웨이퍼 폴더."""
    out = {} if out is None else out
    dirs, files = _entries(d)
    maps = sorted(n for n, _, _, _ in files if n.lower().endswith(".txt"))
    ids = {n[:-4].upper() for n in maps}
    wafer_dirs = []
    for name, p, _, _ in dirs:
        sub_dirs, sub_files = _entries(p)
        if any(Path(n).suffix.lower() in IMG_EXT for n, _, _, _ in sub_files) or name.upper() in ids:
            wafer_dirs.append((name, p))
            ids.add(name.upper())
        elif k < 3:
            _conder_steps(p, k + 1, out)
    if files or wafer_dirs:
        out[d] = (wafer_dirs, ids, maps)
    return out


def _take_conder_lot(col, lot: Path, arc_lot: str, steps):
    """단계 폴더 파일(수정 시각과 함께, Thumbs.db 빼고)과 사진 폴더의 사진 이름."""
    for step, (wafer_dirs, _, _) in steps.items():
        arc = arc_lot if step == lot else f"{arc_lot}/{step.relative_to(lot).as_posix()}"
        col.take_files(step, arc, depth=_WAFER_DEPTH, with_mtime=True, skip=("Thumbs.db",))
        for name, p in wafer_dirs:
            col.take_files(p, f"{arc}/{name}", depth=_WAFER_DEPTH, only=set(), photos=True)


def _note_conder_names(col, lot: Path, arc_lot: str, steps, taken: bool):
    """단계 폴더마다 맵 txt 이름 · 사진 폴더 이름 한 줄(03 폴더 이름표 위험 확인용, 내용은 담지 않는다)."""
    for step, (wafer_dirs, _, maps) in sorted(steps.items()):
        rel = arc_lot if step == lot else f"{arc_lot}/{step.relative_to(lot).as_posix()}"
        folders = sorted(n for n, _ in wafer_dirs)
        col.names.append(f"{'담음' if taken else '이름만'}\t{rel}\t맵 {len(maps)}: {', '.join(maps)}"
                         f"\t사진 폴더 {len(folders)}: {', '.join(folders)}")


def collect_conder_siblings(col, say, progress, stop, end=None):
    """같은 웨이퍼가 두 단계 이상에 나오는 Conder LOT 의 단계 폴더 파일 · 사진 이름.  담은 LOT 수를 돌려준다.
    본 LOT 은 담지 않아도 이름목록에 적는다."""
    expired = _budget(col, stop, end, SIB_MINUTES, SIB_ZIP_MB)

    def lots_of(d: Path):
        return [(t, p) for n, p, t, _ in _entries(d)[0] if re.search(r"\([^()]+\)\s*$", n)]

    lots = looked = 0
    for base in (Path(r) for r in CONDER_ROOTS):
        for cust_name, cust, _, _ in sorted(_entries(base)[0]):
            if expired():
                break
            devs = [p for _, p, _, _ in _entries(cust)[0]]
            groups = [lots_of(d) for d in devs] if QORVO_NAME in cust_name.upper() else [
                [lot for d in devs for lot in lots_of(d)]]
            for group in groups:
                picked = 0
                for _, lot in sorted(group, key=lambda t: -t[0]):
                    if picked >= SIB_LOTS or expired():
                        break
                    progress(0, 0, f"Conder 옆 폴더 {cust_name} {lot.name}"[:70])
                    steps = _conder_steps(lot)
                    seen = {}
                    for _, ids, _ in steps.values():
                        for i in ids:
                            seen[i] = seen.get(i, 0) + 1
                    arc_lot = f"Conder옆폴더/{lot.relative_to(base).as_posix()}"
                    sibling = len(steps) >= 2 and max(seen.values(), default=0) >= 2
                    _note_conder_names(col, lot, arc_lot, steps, sibling)
                    for step, (wafer_dirs, _, _) in steps.items():       # KLA 폴더의 정보파일(사진은 이름만)
                        sarc = arc_lot if step == lot else f"{arc_lot}/{step.relative_to(lot).as_posix()}"
                        for wname, wp in wafer_dirs:
                            names = kla_info_names(wp)
                            if names:
                                col.take_files(wp, f"{sarc}/{wname}", only=names, photos=not sibling)   # 옆 폴더 LOT 은 아래에서 사진 이름을 담는다
                    looked += 1
                    if not sibling:
                        continue
                    _take_conder_lot(col, lot, arc_lot, steps)
                    picked += 1
                    lots += 1
                    say(f"Conder 옆 폴더 LOT {lot}  단계 {len(steps)}")
    say(f"Conder: LOT {looked}개를 보고 옆 폴더 LOT {lots}개를 담음")
    return lots


class Collector:
    """고른 파일을 작업용 zip 하나에 담는다(내용이 같은 파일은 한 번만)."""

    def __init__(self, staging: Path):
        self.zf = zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED)
        self.hashes = {}
        self.photos, self.listing, self.dups, self.names = [], [], [], []
        self.raw = 0
        self.files = 0

    def zipped(self) -> int:
        """지금까지 zip 에 쓴 바이트(압축한 크기)."""
        return self.zf.fp.tell()

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
            if ext in SKIP_EXT or size > (max_mb or FILE_MB) * MB:
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

    def close(self, summary: str):
        for name, lines in (("사진목록.txt", self.photos), ("파일목록.txt", self.listing),
                            ("중복목록.txt", self.dups), ("Conder이름목록.txt", self.names)):
            self.zf.writestr(name, "\n".join(lines) + "\n")
        self.zf.writestr("요약.txt", summary)
        self.zf.close()


def plan_parts(sizes, part=PART_MB * MB):
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
    if staging.stat().st_size <= PART_MB * MB:
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
    """수집하고 ``(결과 폴더, 웨이퍼 수, 담은 파일 수)`` 를 돌려준다.  아무것도 못 찾으면 ``(None, 0, 0)``.

    ``progress(done, total, 문구)`` — total 0 이면 진행량 모름.  ``stop()`` 이 참이면 그만 찾고 지금까지 모은 것을 남긴다."""
    progress = progress or (lambda *_: None)
    stop = stop or (lambda: False)
    t0 = time.time()
    deadline = t0 + MINUTES * 60
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = out / f"고칠곳수집_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    staging = out_dir / "_작업중.zip"
    col = Collector(staging)
    log = []

    def say(line):
        log.append(line)
        print(line)
        progress(0, 0, line[:70])

    def guarded(title, call):
        """한 단계가 터져도 모은 것은 남기고 다음 단계로 간다(사용자 2026-11-15: 한 번에 모으니 오류)."""
        if stop() or time.time() > deadline:
            say(f"{title}: 시간 제한 · 중지로 건너뜀")
            return 0
        try:
            return call() or 0
        except Exception as e:                      # noqa: BLE001 — 수집 도구는 끝까지 zip 을 남긴다
            say(f"{title}: 오류로 이 단계를 멈추고 다음 단계로 — {type(e).__name__}: {e}")
            return 0

    odd = {_aoi_key(a) for a in ODD_SEED}
    wafers = guarded("모든 Job", lambda: collect_all_jobs(col, say, progress, stop, end=deadline, odd=odd))
    say(f"구형 Surface.flt 장비: {', '.join(sorted(odd))}")
    wafers += guarded("구형 Surface.flt", lambda: collect_odd_flt(col, say, progress, stop, end=deadline, odd=odd))
    lots = guarded("Conder 옆 폴더", lambda: collect_conder_siblings(col, say, progress, stop, end=deadline))
    summary = "\n".join([
        f"고칠 곳 자료 수집 {stamp}  ({time.time() - t0:.0f}초)",
        f"웨이퍼 {wafers} · Conder 옆 폴더 LOT {lots} · 담은 파일 {col.files} · 원본 {col.raw / MB:.1f} MB"
        f" · zip {col.zipped() / MB:.1f} MB · 사진 이름 {len(col.photos)} · 중복 {len(col.dups)}",
        "", "기록:", *log, ""])
    col.close(summary)
    if not (wafers or col.files or col.names or col.photos):
        staging.unlink(missing_ok=True)
        try:
            out_dir.rmdir()
        except OSError:
            pass
        return None, 0, 0
    progress(0, 0, "zip 나누는 중")
    split_zip(staging, out_dir, f"고칠곳수집_{stamp}")
    return out_dir, wafers, col.files


if __name__ == "__main__":
    desk = Path.home() / "Desktop"
    try:
        print(run(desk if desk.is_dir() else Path.home()))
    finally:
        input("끝났습니다. 바탕화면 고칠곳수집_* 폴더 안 zip 을 올려 주세요. (Enter 로 닫기)")
