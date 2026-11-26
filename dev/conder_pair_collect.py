"""[Conder짝수집-임시] Conder 짝 자료 수집 — 같은 웨이퍼의 KLA 결함과 Camtek 결함을 이을 자료를 모은다(2026-11-17).

읽기 전용 · 표준 파이썬만 쓴다(앱 코드를 불러오지 않는다).  앱의 개발자 모드 버튼
(``ui/conder_pair_collect_tmp.py``)이 :func:`run` 을 부르고, 더블클릭으로도 돈다.

무엇을 모으나 — 한 단계가 실패해도 요약에 적고 zip 은 남긴다:
    1. ``Conder장치목록.txt`` — Conder 의 ``고객\\디바이스`` 폴더 전부와 LOT 수 · 가장 최근 LOT 수정 시각.
       키워드로 아무것도 못 찾아도 항상 쓴다(다음에 맞는 이름을 찾으려고).
    2. 장치 이름에 :data:`KEYWORDS` 가 든 장치 **전부를 돌아가며**(장치 1 의 LOT 하나, 장치 2 의 LOT 하나, …) 훑는다.
       장치마다 LOT 를 최근 것 · 가장 오래된 것 · 가운데 · 4분점 … 순서(:func:`bisect_order`)로 :data:`LOOK_PER_DEVICE` 개까지,
       색인 시간 :data:`INDEX_MINUTES` 안에서 본다.  LOT 의 단계 폴더를 KLA · Camtek · 기타로 나누고 웨이퍼 폴더마다 기록한다.
    3. 같은 고객 안에서 KLA 웨이퍼 폴더와 Camtek 웨이퍼 폴더를 웨이퍼 ID(폴더 이름)로 짝짓는다 — 같거나, 한쪽이 다른 쪽에
       들어 있고 짧은 쪽이 8자 이상(폴더 이름이 줄었을 수 있다).  같은 LOT 이든 다른 LOT · 장치든 상관없다.
    4. 짝이 된 웨이퍼 폴더를 장치 → LOT 순으로 돌아가며 담는다(LOT 마다 :data:`MAX_WAFERS_PER_LOT` 개, 전체
       :data:`MAX_PAIRED_WAFERS` 개) — 웨이퍼 폴더의 사진 아닌 파일(수정 시각과 함께) · 사진 이름, 관련 단계 폴더의 맵 txt 등.
       훑은 단계마다 한 줄씩 ``LOT요약.tsv``, 짝마다 한 줄씩 ``짝목록.tsv`` 에 적는다(담지 않은 것도).
무엇을 버리나: 사진 · 영상 · 압축 파일(사진은 이름만), :data:`FILE_MB` 를 넘는 파일(이름 · 크기만 ``파일목록.txt``),
내용이 같은 파일(한 번만 담고 ``중복목록.txt``), ``Thumbs.db``.
결과: 바탕화면 ``프로그램 개발중\\분석용 자료 수집\\AOI 레시피 검증\\<날짜>\\`` 폴더 안 zip.  :data:`PART_MB` 를 넘으면
나누되, 나눈 조각은 :data:`MIN_PART_MB` 이상이 되게 채운다(마지막 조각만 작을 수 있다).
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import zipfile
from datetime import datetime
from pathlib import Path

CONDER_ROOTS = [r"\\k5cifsn2\k5tsvdata$\1. Conder Scan"]
KEYWORDS = ("TB500", "T254")
LOOK_PER_DEVICE = 80     # 장치마다 훑을 LOT 수
MINUTES = 60             # 전체 시간 상한(1차는 25분에 끝까지 못 돌았다)
INDEX_MINUTES = 35       # 그중 LOT 를 훑어 색인하는 시간(나머지는 담는 시간)
MAX_WAFERS_PER_LOT = 6   # LOT 하나에서 담는 짝 웨이퍼 폴더 수(1차는 두 웨이퍼에 짝의 35% 가 몰렸다)
MAX_PAIRED_WAFERS = 400  # 전체에서 담는 짝 웨이퍼 폴더 수
MIN_ID_LEN = 8           # 한쪽이 다른 쪽에 들어 있어 짝으로 볼 때 짧은 쪽의 최소 글자 수
DONE_LOTS = frozenset((   # 1차 수집(conder짝_1336)에서 이미 받은 LOT 폴더 — 다시 담지 않고 요약에만 '이미 받음'
    '1200. TB500.INT1183 (KAT)',
    '1202. TB500.INT1196 (LYS)',
    '1214. TB500.INT1208 (MAH)',
    '1230. TB500.INT1250 (NMT)',
    '2. N7G732.00 DCC01 (UGT)',
    '2. N7G733.00 DCC01 (UGU)',
    '430. T254.INT424 (FGB)',
    '436. T254.INT442 (KBP)',
    '437. T254.INT433 (KBA)',
    '438. T254.INT432 (FGM)',
    '439. T254.INT430 (FGK)',
    '447. T254.INT443 (KBS)',
    '501. TB500INT.544 (XNN)',
    '502. TB500INT.543 (XNM)',
    '503. TB500INT.545 (XNP)',
    '504. TB500INT.547 (XNT)',
    '506. TB500INT.548 (XNU)',
    '507. TB500INT.546 (XNS)',
    '6. HELLER TEST_T254 (GAA)',
    '78. E2C089.S00 DCC20 (MDG)',
    '892. TB500.INT1022 (BSX)',
    '920. TB500.INT955 (WFN)',
))
ZIP_MB = 80              # zip(압축한 크기) 상한 — 올리는 양이 기준
FILE_MB = 20             # 이보다 큰 파일은 이름만 남긴다
PART_MB = 28             # 올릴 수 있는 한도가 30 MB 보다 작다(30 MB 조각 2개가 안 올라감, 사용자 2026-11-15)
MIN_PART_MB = 23
MB = 2 ** 20
SKIP_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif",
            ".avi", ".mp4", ".wmv", ".zip", ".7z", ".rar", ".gz", ".cab"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif"}
_WAFER_DEPTH = 2    # 웨이퍼 폴더 안 하위 폴더 깊이
_LOT_RE = re.compile(r"\([^()]+\)\s*$")
_INT_RE = re.compile(r"-?\d+$")


def default_out_base() -> Path:
    """``<바탕화면>\\프로그램 개발중\\분석용 자료 수집\\AOI 레시피 검증`` (바탕화면이 없으면 홈 폴더 아래)."""
    desk = Path.home() / "Desktop"
    return (desk if desk.is_dir() else Path.home()) / "프로그램 개발중" / "분석용 자료 수집" / "AOI 레시피 검증"


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


def is_camtek_name(stem: str) -> bool:
    """LIVE 사진 이름인가 — ``_`` 토큰에서 처음 나오는 연속한 두 정수 앞에 식별자 토큰이 2개 이상
    (KLA 이미지 이름 ``{WaferID}_2_0_23_2`` 는 앞이 1개뿐이라 걸러진다.  ``coords/camtek_live`` 와 같은 규칙)."""
    toks = stem.split("_")
    for i in range(len(toks) - 1):
        if _INT_RE.match(toks[i]) and _INT_RE.match(toks[i + 1]):
            return i >= 2
    return False


def classify_wafers(wafer_dirs):
    """단계 하나 → (종류, 사진 수, 웨이퍼 폴더별 사진 수 목록).  종류는 KLA · Camtek · 기타.  KLA 정보파일이 있으면 KLA 가 먼저다."""
    photos, kla, camtek, per = 0, False, False, []
    for _, p in wafer_dirs:
        names = [n for n, _, _, _ in _entries(p)[1] if Path(n).suffix.lower() in IMG_EXT]
        photos += len(names)
        per.append(len(names))
        if not kla and kla_info_names(p):
            kla = True
        if not camtek and any(is_camtek_name(Path(n).stem) for n in names[:5]):
            camtek = True
    return ("KLA" if kla else "Camtek" if camtek else "기타"), photos, per


def classify_step(wafer_dirs):
    """단계 하나 → (종류, 사진 수)."""
    kind, photos, _ = classify_wafers(wafer_dirs)
    return kind, photos


def _budget(col, stop, end, minutes: float, zip_mb: float):
    """단계 하나의 '그만' 판단 — 중지 · 단계 시간 · 전체 시간 · 이 단계가 늘린 zip 크기."""
    deadline = time.time() + minutes * 60
    if end is not None:
        deadline = min(deadline, end)
    start = col.zipped()
    return lambda: stop() or time.time() > deadline or col.zipped() - start > zip_mb * MB


def list_devices(col, say, progress, stop, end=None):
    """모든 ``고객\\디바이스`` 를 ``Conder장치목록.txt`` 줄로 남기고 ``[(고객, 디바이스, 경로, [(수정 시각, LOT)], 루트)]`` 를 돌려준다."""
    expired = _budget(col, stop, end, MINUTES, ZIP_MB)
    found = []
    for base in (Path(r) for r in CONDER_ROOTS):
        for cust_name, cust, _, _ in sorted(_entries(base)[0]):
            if expired():
                break
            progress(0, 0, f"Conder 장치 목록 {cust_name}"[:70])
            for dev_name, dev, _, _ in sorted(_entries(cust)[0]):
                lots = [(t, p) for n, p, t, _ in _entries(dev)[0] if _LOT_RE.search(n)]
                newest = max((t for t, _ in lots), default=None)
                stamp = f"{datetime.fromtimestamp(newest):%Y-%m-%d %H:%M}" if newest else "-"
                col.devices.append(f"{cust_name}\t{dev_name}\t{len(lots)}\t{stamp}")
                found.append((cust_name, dev_name, dev, lots, base))
    say(f"장치 목록: 장치 {len(found)}개")
    return found


def bisect_order(n: int):
    """0..n-1 을 시간에 고르게 퍼지는 순서로 — 처음(최근) · 끝(가장 오래됨) · 가운데 · 4분점 … (구간 이등분을 넓게부터)."""
    if n <= 0:
        return []
    out = [0] if n == 1 else [0, n - 1]
    queue = [(0, n - 1)]
    while queue:
        nxt = []
        for lo, hi in queue:
            if hi - lo > 1:
                mid = (lo + hi) // 2
                out.append(mid)
                nxt += [(lo, mid), (mid, hi)]
        queue = nxt
    return out


def ids_match(a: str, b: str) -> bool:
    """웨이퍼 폴더 이름(대문자) 둘이 같은 웨이퍼인가 — 같거나, 짧은 쪽이 :data:`MIN_ID_LEN` 자 이상이고 긴 쪽에 들어 있다."""
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= MIN_ID_LEN and short in long_


class Wafer:
    """색인한 웨이퍼 폴더 하나."""
    __slots__ = ("cust", "label", "base", "lot", "step", "kind", "wid", "path", "photos", "row")

    def __init__(self, cust, label, base, lot, step, kind, wid, path, photos, row):
        self.cust, self.label, self.base, self.lot, self.step = cust, label, base, lot, step
        self.kind, self.wid, self.path, self.photos, self.row = kind, wid, path, photos, row

    def rel(self) -> str:
        return self.path.relative_to(self.base).as_posix()


def index_lots(col, devices, say, progress, expired, index_end):
    """키워드 장치를 돌아가며(장치마다 :func:`bisect_order` 순서로) LOT 를 훑어 ``(웨이퍼 목록, 요약 줄 목록)`` 을 돌려준다.
    요약 줄은 ``[고객 디바이스, LOT, 단계, 종류, 웨이퍼 폴더, 사진, 담음]`` 리스트(마지막 칸은 나중에 채운다)."""
    queues, rows, wafers = [], [], []
    for cust_name, dev_name, dev, lots, base in devices:
        label = f"{cust_name} {dev_name}"
        if not any(k.upper() in label.upper() for k in KEYWORDS):
            continue
        ordered = [p for _, p in sorted(lots, key=lambda t: -t[0])]
        todo = []
        for p in ordered:
            if p.name in DONE_LOTS:
                rows.append([label, p.relative_to(base).as_posix(), ".", "-", "0", "0", "이미 받음"])
            else:
                todo.append(p)
        todo = [todo[i] for i in bisect_order(len(todo))][:LOOK_PER_DEVICE]
        queues.append([cust_name, label, dev_name, base, list(reversed(todo))])
    visited = 0
    while any(q[4] for q in queues):
        for cust_name, label, dev_name, base, todo in queues:
            if not todo:
                continue
            if expired() or time.time() > index_end:
                return wafers, rows
            lot = todo.pop()
            progress(0, 0, f"{dev_name} {lot.name}"[:70])
            rel_lot = lot.relative_to(base).as_posix()
            for step, (wafer_dirs, _, _) in _conder_steps(lot).items():
                if not wafer_dirs:
                    continue
                kind, photos, per = classify_wafers(wafer_dirs)
                rel_step = "." if step == lot else step.relative_to(lot).as_posix()
                row = [label, rel_lot, rel_step, kind, str(len(wafer_dirs)), str(photos), "이름만"]
                rows.append(row)
                for (name, p), n in zip(wafer_dirs, per):
                    wafers.append(Wafer(cust_name, label, base, lot, step, kind, name.upper(), p, n, row))
            visited += 1
            if visited % 20 == 0:
                say(f"색인 LOT {visited}개")
    return wafers, rows


def find_pairs(wafers):
    """같은 고객 안에서 KLA 웨이퍼 폴더 ↔ Camtek 웨이퍼 폴더 짝 ``[(kla, camtek)]``."""
    by_cust = {}
    for w in wafers:
        if w.kind in ("KLA", "Camtek"):
            by_cust.setdefault(w.cust, ([], []))[0 if w.kind == "KLA" else 1].append(w)
    pairs = []
    for ks, cs in by_cust.values():
        for k in ks:
            for c in cs:
                if ids_match(k.wid, c.wid):
                    pairs.append((k, c))
    return pairs


def _spread(pairs):
    """한 LOT 의 짝을 단계가 다른 것부터 번갈아 나열한다."""
    groups = {}
    for pr in pairs:
        groups.setdefault((pr[0].step, pr[1].step), []).append(pr)
    lists = list(groups.values())
    out = []
    while any(lists):
        for g in lists:
            if g:
                out.append(g.pop(0))
    return out


def take_pairs(col, pairs, say, progress, expired):
    """짝 웨이퍼 폴더를 장치 → LOT 순으로 돌아가며 담는다.  ``(담은 LOT 수, 담은 웨이퍼 id 집합)``."""
    devs = {}                                     # 장치 → LOT → 짝(KLA 쪽 장치 · LOT 기준)
    for pr in pairs:
        devs.setdefault(pr[0].label, {}).setdefault(pr[0].lot, []).append(pr)
    rot = [[_cursor(lots)] for lots in devs.values()]
    taken, per_lot, steps_done, lots_done = set(), {}, set(), set()
    while any(r[0]["left"] for r in rot) and len(taken) < MAX_PAIRED_WAFERS:
        for (cur,) in rot:
            if expired():
                return len(lots_done), taken
            pr = cur_next(cur)
            if pr is None:
                continue
            new = [w for w in pr if id(w) not in taken]
            if len(taken) + len(new) > MAX_PAIRED_WAFERS:
                continue
            if any(per_lot.get(w.lot, 0) + sum(1 for x in new if x.lot == w.lot) > MAX_WAFERS_PER_LOT for w in new):
                continue
            for w in new:
                progress(0, 0, f"{w.label} {w.lot.name}"[:70])
                if (w.lot, w.step) not in steps_done:
                    steps_done.add((w.lot, w.step))
                    arc = f"Conder짝/{w.step.relative_to(w.base).as_posix()}"
                    col.take_files(w.step, arc, depth=_WAFER_DEPTH, with_mtime=True, skip=("Thumbs.db",))
                if w.lot not in lots_done:
                    arc = f"Conder짝/{w.lot.relative_to(w.base).as_posix()}"
                    if w.step != w.lot:
                        col.take_files(w.lot, arc, depth=_WAFER_DEPTH, with_mtime=True, skip=("Thumbs.db",))
                    lots_done.add(w.lot)
                col.take_files(w.path, f"Conder짝/{w.rel()}", with_mtime=True, skip=("Thumbs.db",))
                taken.add(id(w))
                per_lot[w.lot] = per_lot.get(w.lot, 0) + 1
                w.row[6] = "담음"
    say(f"짝 웨이퍼 폴더 {len(taken)}개 · LOT {len(lots_done)}개를 담음")
    return len(lots_done), taken


def _cursor(lots):
    """장치 하나의 LOT 별 짝 목록(단계 번갈아)과 LOT 순환 위치."""
    return {"lots": [_spread(prs) for prs in lots.values()], "pos": 0, "left": sum(len(p) for p in lots.values())}


def cur_next(cur):
    """장치의 다음 LOT 에서 짝 하나를 꺼낸다(LOT 을 돌아가며).  없으면 None."""
    n = len(cur["lots"])
    for _ in range(n):
        lot_pairs = cur["lots"][cur["pos"] % n]
        cur["pos"] += 1
        if lot_pairs:
            cur["left"] -= 1
            return lot_pairs.pop(0)
    return None


def collect_pairs(col, devices, say, progress, stop, end=None):
    """키워드 장치를 돌아가며 색인하고, 고객 안에서 KLA ↔ Camtek 웨이퍼 폴더 짝을 찾아 담는다.  담은 LOT 수를 돌려준다."""
    expired = _budget(col, stop, end, MINUTES, ZIP_MB)
    index_end = time.time() + INDEX_MINUTES * 60
    wafers, rows = [], []
    pairs = []
    try:
        wafers, rows = index_lots(col, devices, say, progress, expired, index_end)
        pairs = find_pairs(wafers)
        say(f"색인: 웨이퍼 폴더 {len(wafers)}개 · 짝 {len(pairs)}개")
        lots, taken = take_pairs(col, pairs, say, progress, expired)
        for k, c in pairs:
            same = 1 if k.lot == c.lot else 0
            col.pairs.append("\t".join((k.cust, k.rel(), c.rel(), str(same),
                                        "1" if id(k) in taken and id(c) in taken else "0")))
        return lots
    finally:
        for r in rows:
            col.rows.append("\t".join(r))


class Collector:
    """고른 파일을 작업용 zip 하나에 담는다(내용이 같은 파일은 한 번만)."""

    def __init__(self, staging: Path):
        self.zf = zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED)
        self.hashes = {}
        self.photos, self.listing, self.dups = [], [], []
        self.devices, self.rows, self.pairs = [], [], []
        self.raw = 0
        self.files = 0

    def zipped(self) -> int:
        """지금까지 zip 에 쓴 바이트(압축한 크기)."""
        return self.zf.fp.tell()

    def take_files(self, folder: Path, arc: str, depth: int = 0, only=None, max_mb=None,
                   photos=False, names_only=(), with_mtime=False, skip=()):
        """``only``(이름 집합 또는 이름 → 참/거짓 함수)를 주면 그 파일만 담는다(하위 폴더는 내려가지 않는다).
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
                self.take_files(path, f"{arc}/{name}", depth + 1, with_mtime=with_mtime, skip=skip)

    def close(self, summary: str):
        head = "고객\t디바이스\tLOT 수\t가장 최근 LOT 수정\n"
        rows_head = "디바이스\tLOT\t단계\t종류\t웨이퍼 폴더\t사진\t담음\n"
        pairs_head = "고객\tKLA 웨이퍼 폴더\tCamtek 웨이퍼 폴더\t같은 LOT\t담음\n"
        for name, text in (("사진목록.txt", "\n".join(self.photos) + "\n"),
                           ("파일목록.txt", "\n".join(self.listing) + "\n"),
                           ("중복목록.txt", "\n".join(self.dups) + "\n"),
                           ("Conder장치목록.txt", head + "\n".join(self.devices) + "\n"),
                           ("LOT요약.tsv", rows_head + "\n".join(self.rows) + "\n"),
                           ("짝목록.tsv", pairs_head + "\n".join(self.pairs) + "\n"),
                           ("요약.txt", summary)):
            self.zf.writestr(name, text)
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
        for k, names in enumerate(plan_parts(sizes, PART_MB * MB), 1):
            p = out_dir / f"{stem}_{k}.zip"
            with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as dst:
                for n in names:
                    dst.writestr(n, src.read(n))
            made.append(p)
    staging.unlink()
    return made


def run(out: Path, progress=None, stop=None):
    """수집하고 ``(결과 폴더, 담은 LOT 수, 담은 파일 수)`` 를 돌려준다.  아무것도 못 찾으면 ``(None, 0, 0)``.

    ``out`` 아래 ``<날짜>`` 폴더에 zip 을 둔다.  ``progress(done, total, 문구)`` — total 0 이면 진행량 모름.
    ``stop()`` 이 참이면 그만 찾고 지금까지 모은 것을 남긴다."""
    progress = progress or (lambda *_: None)
    stop = stop or (lambda: False)
    t0 = time.time()
    deadline = t0 + MINUTES * 60
    now = datetime.now()
    out_dir = out / f"{now:%Y%m%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    staging = out_dir / "_작업중.zip"
    col = Collector(staging)
    log = []

    def say(line):
        log.append(line)
        print(line)
        progress(0, 0, line[:70])

    def guarded(title, call):
        """한 단계가 터져도 모은 것은 남기고 다음 단계로 간다."""
        if stop() or time.time() > deadline:
            say(f"{title}: 시간 제한 · 중지로 건너뜀")
            return None
        try:
            return call()
        except Exception as e:                      # noqa: BLE001 — 수집 도구는 끝까지 zip 을 남긴다
            say(f"{title}: 오류로 이 단계를 멈추고 다음 단계로 — {type(e).__name__}: {e}")
            return None

    devices = guarded("장치 목록", lambda: list_devices(col, say, progress, stop, end=deadline)) or []
    lots = guarded("짝 LOT", lambda: collect_pairs(col, devices, say, progress, stop, end=deadline)) or 0
    summary = "\n".join([
        f"Conder 짝 자료 수집 {now:%Y%m%d_%H%M}  ({time.time() - t0:.0f}초)",
        f"장치 {len(devices)} · 담은 LOT {lots} · 담은 파일 {col.files} · 원본 {col.raw / MB:.1f} MB"
        f" · zip {col.zipped() / MB:.1f} MB · 사진 이름 {len(col.photos)} · 중복 {len(col.dups)} · 짝 {len(col.pairs)}",
        "", "기록:", *log, ""])
    col.close(summary)
    if not (col.devices or col.files or col.photos or col.rows):
        staging.unlink(missing_ok=True)
        try:
            out_dir.rmdir()
        except OSError:
            pass
        return None, 0, 0
    progress(0, 0, "zip 나누는 중")
    split_zip(staging, out_dir, f"conder짝_{now:%H%M}")
    return out_dir, lots, col.files


if __name__ == "__main__":
    try:
        print(run(default_out_base()))
    finally:
        input("끝났습니다. 바탕화면 프로그램 개발중 폴더의 오늘 날짜 폴더 안 zip 을 올려 주세요. (Enter 로 닫기)")
