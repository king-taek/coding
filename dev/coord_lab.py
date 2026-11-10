"""좌표 가설 실험실 — "col/row·x/y·wafer map 을 하나의 공식으로" 가 되는지 **실물로** 가린다.

읽기 전용 · 표준 파이썬만 쓴다(앱 코드를 불러오지 않는다 — 앱 계산과 **독립인** 재계산이라
순환 검증이 아니다).  폴더를 주면 그 아래를 전부 훑어 Camtek 웨이퍼 폴더 · KLA 정보파일 ·
LIVE 파일명(사진 또는 ``사진목록*.txt``)을 찾고, 가설마다 답을 계산해 정답과 대조한다.

사용법::

    python dev/coord_lab.py                      # 저장소 샘플(dev/좌표 확인 · docs)
    python dev/coord_lab.py "<폴더>" ["<폴더>" ...] [--out "<출력 폴더>"]
    python dev/coord_lab.py --nas ["<폴더>" ...] [--minutes 30] [--per-lot 3] [--lots 5]

``--nas`` 는 NAS 의 Scanresult · Conder Scan 을 **가볍게** 돈다(더블클릭은 ``coord_lab_nas.py``):
LOT 마다 웨이퍼 몇 장만 뽑고, 사진은 열지 않고 이름만 읽고, 시간 제한에서 멈춘다.  가설끼리
답이 갈리거나 앱이 정답과 다른 폴더만 원재료(INI · die 목록 · 사진 이름)를 zip 하나로 모은다.

결과: ``좌표실험_<시각>.txt``(사람이 읽는 요약) · ``.json``(전체 수치) · ``.html``(색칠한 표).

하나의 모델(이 도구가 검증하는 것) — 장비·형식이 달라도 식은 하나이고 **재료만 다르다**::

    P        = 결함의 웨이퍼 평면 좌표(µm, 중심 기준)              ← 위치: 물리량
    칸(i, j) = floor((P − 위상) / pitch)                           ← 격자: 제품의 것
    col, row = (i, j) − 번호원점                                    ← 번호: 레시피가 정한 '이름'
    x, y     = (P − 위상) mod pitch

가설(출처 등급은 회고 R3 를 따른다):

    위치  A1  Camtek die 격자는 stage 원점에 고정이다 — ``Col == floor(X/pitch_x)`` (파일)
    번호  EXP 번호표 ``ProductInfo.ini [ExportData]`` (cMin, rMax)            (파일)
          LUT ``DieLut.dat`` 의 (min Col, max Row)                            (파일)
          MAP ``s_DieLocation.dat`` 의 (min x칸, max y칸)                     (파일)
          MMX ``WaferMapMatchMatrix`` 의 5·6번째 값 = (cMin, rMin) — col 만   (파일, 뜻은 가정)
          C1D 중심식: 원의 좌우·위아래 끝에서 온전히 드는 첫 열 · 마지막 행   (유도)
          G3  가장자리 3 mm 안쪽 원에 네 모서리가 드는 die                    (유도, 3 mm 는 가정)
          G0  웨이퍼 원에 네 모서리가 드는 die                                (유도)
          CEN 중심만 원 안에 드는 die                                         (유도)
          LOT 같은 LOT 형제의 '웨이퍼 중심이 번호 격자에서 놓인 자리' 를 빌린다(자기 제외) (유도)
          PRD 같은 pitch 의 다른 웨이퍼 전부에서 빌린다(자기 제외)            (유도)
          APP 지금 앱이 채택하는 값(저장소 안에서 돌릴 때만 — 앱이 어디서 틀리는지 보려고)
    정답  LIVE  Conder LIVE 파일명의 col/row(장비 화면 번호)와 INI 결함을 die 안쪽 좌표로 짝지어 표결 (관측)
          EXP   번호표(위와 같은 값 — 그래서 EXP 가설을 EXP 정답으로 채점하지 않는다)            (파일)
    KLA   K1  ``SampleTestPlan`` 범위 = 중심 규칙의 '온전히 드는 die' 범위
          K2  같은 웨이퍼의 KLA ↔ Camtek 은 평면 좌표에서 일정한 작은 잔차 B 만 남는다
"""

from __future__ import annotations

import json
import math
import os
import re
import struct
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOTS = [REPO / "dev" / "좌표 확인", REPO / "docs"]
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
EDGE_MM3 = 3000.0
NUM_HYP = ["EXP", "LUT", "MAP", "MMX", "C1D", "G3", "G0", "CEN", "LOT", "PRD", "APP"]


# ───────────────────────── 읽기 ─────────────────────────
def read_text(p: Path, limit: int | None = None) -> str | None:
    try:
        with open(p, "rb") as fh:
            b = fh.read() if limit is None else fh.read(limit)
    except OSError:
        return None
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return b.decode("utf-16", "replace")
    for enc in ("utf-8-sig", "cp949"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("latin-1", "replace")


def key_num(txt: str | None, key: str) -> float | None:
    """``key=값`` — 값 뒤에 다른 글자가 붙으면 읽지 않는다(``;`` 주석만 허용)."""
    if not txt:
        return None
    m = re.search(r"(?im)^\s*" + re.escape(key) + r"\s*=\s*([-+\d.eE]+)\s*(?:;.*)?$", txt)
    try:
        return float(m.group(1)) if m else None
    except ValueError:
        return None


def w2t_center(txt: str | None):
    if not txt:
        return None
    rows = {m.group(1).upper(): tuple(float(m.group(k)) for k in (2, 3, 4)) for m in re.finditer(
        r"(?im)^\s*Wafer2Table_([XY])\s*=\s*([-\d.eE]+)\s+([-\d.eE]+)\s+([-\d.eE]+)\s*$", txt)}
    if {"X", "Y"} - rows.keys():
        return None
    (a11, a12, t1), (a21, a22, t2) = rows["X"], rows["Y"]
    det = a11 * a22 - a12 * a21
    if abs(det) < 1e-9:
        return None
    return ((a22 * -t1 - a12 * -t2) / det, (-a21 * -t1 + a11 * -t2) / det)


def ini_entries(txt: str | None):
    """ColorImageGrabingInfo.ini → [(이름, X, Y, Col, Row, 레시피)]."""
    out = []
    if not txt:
        return out
    parts = re.split(r"(?m)^\[([^\]\r\n]+)\]\s*$", txt)
    it = iter(parts[1:])
    for name, body in zip(it, it):
        kv = {k.upper(): v.strip() for k, v in re.findall(r"(?m)^\s*(\w+)\s*=\s*(.*)$", body)}
        try:
            X = float(kv.get("X", kv.get("FAULTX")))
            Y = float(kv.get("Y", kv.get("FAULTY")))
            col, row = int(float(kv["COL"])), int(float(kv["ROW"]))
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(X) and math.isfinite(Y)):
            continue
        try:
            rec = int(kv.get("RECIPENUMBER", "0"))
        except ValueError:
            rec = 0
        out.append((name.strip(), X, Y, col, row, rec))
    return out


def dielut_cells(p: Path):
    txt = read_text(p)
    if not txt:
        return None
    cells = set()
    for m in re.finditer(r"<DieLutData\b([^>]*?)/?>", txt):
        kv = dict(re.findall(r'\b(Col|Row)="(-?\d+)"', m.group(1)))
        if len(kv) == 2:
            cells.add((int(kv["Col"]), int(kv["Row"])))
    return cells or None


def diemap_cells(dat: Path, px: float, py: float):
    md = read_text(Path(str(dat) + ".md"))
    if not md:
        return None
    m = re.search(r'RecordSize\s+Size="(\d+)"', md)
    f = {n: (int(o), int(v)) for n, o, v in
         re.findall(r'Name="([^"]+)"[^>]*?Offset="(\d+)"[^>]*?Vartype="(\d+)"', md)}
    if not m or "x" not in f or "y" not in f or f["x"][1] != 5 or f["y"][1] != 5:
        return None
    rec = int(m.group(1))
    try:
        data = dat.read_bytes()
    except OSError:
        return None
    if rec <= 0 or not data or len(data) % rec:
        return None
    cells = set()
    try:
        for i in range(len(data) // rec):
            x = struct.unpack_from("<d", data, i * rec + f["x"][0])[0]
            y = struct.unpack_from("<d", data, i * rec + f["y"][0])[0]
            cells.add((math.floor(x / px), math.floor(y / py)))
    except (struct.error, ValueError, OverflowError):
        return None
    return cells or None


_INT = re.compile(r"-?\d+$")
_NUM = re.compile(r"-?\d+(?:\.\d+)?$")


def parse_live(name: str):
    """LIVE 파일명 → (col, row, x, y).  앱 규칙과 같다: 식별자 토큰 2개 이상 뒤의
    처음 연속한 두 정수 = col,row · 그 뒤 처음 두 수치 = x,y."""
    tok = Path(name).stem.split("_")
    for i in range(2, len(tok) - 1):
        if _INT.match(tok[i]) and _INT.match(tok[i + 1]):
            nums = [float(t) for t in tok[i + 2:] if _NUM.match(t)]
            if len(nums) < 2:
                return None
            col, row = int(tok[i]), int(tok[i + 1])
            return None if col < 0 or row < 0 else (col, row, nums[0], nums[1])
        if _INT.match(tok[i]):
            continue
    return None


def kla_header(txt: str):
    def two(pat):
        m = re.search(pat + r"\s+([-+\d.eE]+)\s+([-+\d.eE]+)", txt, re.I)
        return (float(m.group(1)), float(m.group(2))) if m else None
    pitch = two(r"DiePitch")
    if pitch is None:
        return None
    wid = re.search(r'WaferID\s+"([^"]*)"', txt)
    size = re.search(r"SampleSize\s+\d+\s+([-+\d.eE]+)", txt, re.I)
    plan = re.search(r"SampleTestPlan\s+\d+(.*?);", txt, re.I | re.S)
    pairs = [(int(a), int(b)) for a, b in re.findall(r"(-?\d+)\s+(-?\d+)", plan.group(1))] if plan else []
    notch = re.search(r"OrientationMarkLocation\s+(\w+)", txt, re.I)
    return {"pitch": pitch, "center": two(r"SampleCenterLocation"),
            "dia": float(size.group(1)) * 1000.0 if size else 300000.0,
            "wafer": wid.group(1).strip() if wid else "", "plan": pairs,
            "notch": notch.group(1) if notch else "",
            "eof": "EndOfFile" in txt}


def kla_defects(txt: str):
    """결함 행 → [(XREL, YREL, XINDEX, YINDEX)].  두 수가 붙은 행 등 모양이 다른 행은 버린다."""
    spec = re.search(r"DefectRecordSpec\s+\d+\s+([A-Z0-9_ \t]+?)\s*;", txt)
    body = txt.split("DefectList", 1)
    if not spec or len(body) < 2:
        return []
    cols = spec.group(1).split()
    try:
        ix = [cols.index(k) for k in ("XREL", "YREL", "XINDEX", "YINDEX")]
    except ValueError:
        return []
    out = []
    for line in body[1].splitlines():
        t = line.split()
        if len(t) <= max(ix) or not _INT.match(t[0]):
            continue
        try:
            xr, yr = float(t[ix[0]]), float(t[ix[1]])
            if not (_INT.match(t[ix[2]]) and _INT.match(t[ix[3]])):
                continue
            out.append((xr, yr, int(t[ix[2]]), int(t[ix[3]])))
        except ValueError:
            continue
    return out


# ───────────────────────── 가설 ─────────────────────────
def h_c1d(cx, cy, dia, px, py):
    return (math.ceil((cx - dia / 2) / px), math.floor((cy + dia / 2) / py) - 1)


def h_grid(cx, cy, dia, px, py, edge=0.0, center_only=False):
    r = dia / 2 - edge
    cells = []
    for i in range(math.floor((cx - r) / px), math.ceil((cx + r) / px) + 1):
        for j in range(math.floor((cy - r) / py), math.ceil((cy + r) / py) + 1):
            if center_only:
                ok = math.hypot((i + .5) * px - cx, (j + .5) * py - cy) <= r
            else:
                ok = all(math.hypot(x - cx, y - cy) <= r for x in (i * px, (i + 1) * px)
                         for y in (j * py, (j + 1) * py))
            if ok:
                cells.append((i, j))
    return (min(i for i, _ in cells), max(j for _, j in cells)) if cells else None


def flip_margin(cx, cy, dia, px, py):
    """중심이 몇 µm 움직이면 중심식(C1D)의 답이 1 바뀌는가 — 축마다 가까운 쪽 경계까지."""
    fx = ((cx - dia / 2) / px) % 1.0          # 0 에 가까우면 왼쪽으로, 1 에 가까우면 오른쪽으로 뒤집힌다
    fy = ((cy + dia / 2) / py) % 1.0
    return (min(fx, 1 - fx) * px, min(fy, 1 - fy) * py)


def live_vote(entries, lives, px, py, tol=2.5):
    """LIVE (col,row,x,y) 와 INI 결함을 die 안쪽 좌표로 짝지어 (col_origin, row_total) 을 표결한다."""
    inner = [(math.floor(X / px), math.floor(Y / py), X % px, Y % py) for _, X, Y, *_ in entries]
    votes = Counter()
    for col, row, x, y in lives:
        hit = {(i - col, j + row) for i, j, xi, yi in inner
               if abs(xi - x) <= tol and abs(yi - y) <= tol}
        if len(hit) == 1:                       # 한 사진이 두 답을 가리키면 그 표는 버린다
            votes[next(iter(hit))] += 1
    if not votes:
        return None
    (best, n), total = votes.most_common(1)[0], sum(votes.values())
    # 한 표도 die 안쪽 x·y 가 둘 다 2.5 µm 안에서 맞아야 나온다 — 만장일치 2표 이상이면 정답으로 쓴다.
    return {"value": best, "votes": n, "total": total,
            "ok": (n >= 2 and n == total) or (n >= 3 and n >= 0.8 * total)}


# ───────────────────────── 훑기 ─────────────────────────
def _find_up(folder: Path, name: str, levels: int = 2):
    cur = folder
    for _ in range(levels + 1):
        p = cur / name
        if p.is_file():
            return p
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def scan(roots):
    camtek, klas, live_by_dir = [], [], {}
    for root in roots:
        for d, _dirs, files in os.walk(root):
            dp = Path(d)
            low = {f.lower(): f for f in files}
            names = [f for f in files if Path(f).suffix.lower() in IMG_EXT]
            for f in files:
                if f.startswith("사진목록") and f.lower().endswith(".txt"):
                    names += [ln.strip() for ln in (read_text(dp / f) or "").splitlines()
                              if ln.strip() and not ln.startswith("#")]
            lv = [(n, parse_live(n)) for n in names]
            lv = [(n, p) for n, p in lv if p]
            if lv:
                live_by_dir[dp] = lv
            if "colorimagegrabinginfo.ini" in low or "params_waferinfo.ini" in low:
                camtek.append(dp)
            for f in files:
                if re.search(r"\.\d{3}$", f) or ("." not in f and not names):
                    head = read_text(dp / f, 65536) or ""
                    if "DiePitch" in head and "SampleCenterLocation" in head:
                        klas.append(dp / f)
    return camtek, klas, live_by_dir


# ───────────────────────── NAS 순회 ─────────────────────────
import time
import zipfile

CONDER_ROOT = r"\\k5cifsn2\k5tsvdata$\1. Conder Scan"
UNC_SHARES = [r"\\10.142.80.91\nas3-1"]
_WAFER_MARK = ("ColorImageGrabingInfo.ini", "Params_WaferInfo.ini")
_LIVE_CAP = 400          # 폴더마다 읽는 사진 이름 수(정답 표결에는 몇 장이면 된다)
_DEPTH = 7


def nas_default_roots():
    """이 PC 에서 보이는 ``<드라이브 또는 공유>\\AOI-*\\Scanresult`` 전부 + Conder Scan."""
    bases = [Path(f"{c}:\\") for c in "DEFGHIJKLMNOPQRSTUVWXYZ"] + [Path(u) for u in UNC_SHARES]
    cam, seen = [], set()
    for b in bases:
        try:
            for d in sorted(b.glob("AOI-*")):
                sr = next((p for p in (d / "Scanresult", d / "ScanResult", d / "ScanResults")
                           if p.is_dir()), None)
                if sr is not None and d.name.upper() not in seen:   # 같은 공유가 두 글자로 물려 있다
                    seen.add(d.name.upper())
                    cam.append(sr)
        except OSError:
            continue
    conder = [Path(CONDER_ROOT)] if os.path.isdir(CONDER_ROOT) else []
    return cam, conder


def _subdirs(d: Path):
    """(이름, 경로, 수정 시각) — 못 읽는 폴더는 빈 목록(NAS 권한 · 끊김)."""
    out = []
    try:
        with os.scandir(d) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        out.append((e.name, Path(e.path), e.stat().st_mtime))
                except OSError:
                    continue
    except OSError:
        pass
    return out


def _is_wafer(d: Path) -> bool:
    return any((d / m).is_file() for m in _WAFER_MARK)


def _expired(deadline) -> bool:
    """마감 — 시각(초) 또는 '멈출까' 를 답하는 함수."""
    return deadline() if callable(deadline) else time.time() > deadline


def nas_walk(roots, deadline, per_lot=3, lots=5, log=print):
    """웨이퍼 폴더(= INI 가 든 폴더)를 찾는다.  LOT(웨이퍼들의 부모)마다 ``per_lot`` 장,
    한 폴더 아래 LOT 은 최근 ``lots`` 개만 — 번호 기준은 레시피의 것이라 몇 장이면 된다."""
    found, state = [], {"lots": 0, "cut": False}

    def visit(d: Path, depth: int) -> bool:
        """d 가 LOT 이었으면 True."""
        if _expired(deadline):
            state["cut"] = True
            return False
        subs = sorted(_subdirs(d), key=lambda t: -t[2])           # 최근 것부터
        if not subs or depth > _DEPTH:
            return False
        names = sorted(subs)
        if any(_is_wafer(p) for _, p, _ in (names[0], names[len(names) // 2], names[-1])):
            step = max(1, len(names) // per_lot)
            picked = [p for _, p, _ in names[::step] if _is_wafer(p)][:per_lot]
            found.extend(picked)
            state["lots"] += 1
            log(f"  LOT {state['lots']:>4}  웨이퍼 {len(names):>3}장 중 {len(picked)}장  {d}")
            return True
        n_lot = 0
        for _, p, _ in subs:
            if visit(p, depth + 1):
                n_lot += 1
                if n_lot >= lots:
                    break
            if state["cut"]:
                break
        return False

    for r in roots:
        r = Path(r)
        if _is_wafer(r):
            found.append(r)
        elif not visit(r, 0) and not r.is_dir():
            log(f"  (못 엶) {r}")
    return found, state["cut"]


def nas_conder(roots, wanted, deadline, log=print):
    """Conder Scan 에서 **이름이 웨이퍼 ID 인 폴더**만 열어 LIVE 파일명을 읽는다(사진은 안 연다)."""
    live, cut = {}, False

    def visit(d: Path, depth: int):
        nonlocal cut
        if _expired(deadline):
            cut = True
            return
        for name, p, _ in _subdirs(d):
            if cut:
                return
            if name in wanted:
                names = []
                try:
                    with os.scandir(p) as it:
                        for e in it:
                            if Path(e.name).suffix.lower() in IMG_EXT:
                                names.append(e.name)
                                if len(names) >= _LIVE_CAP:
                                    break
                except OSError:
                    continue
                lv = [(n, q) for n, q in ((n, parse_live(n)) for n in names) if q]
                if lv:
                    live[p] = lv
                    log(f"  Conder {name}: LIVE 이름 {len(lv)}  {p}")
            elif depth < _DEPTH:
                visit(p, depth + 1)

    for r in roots:
        visit(Path(r), 0)
    return live, cut


def flags(r, no_map=()):
    """원재료를 모을 만한 폴더인가 — 이유 목록(없으면 정상)."""
    out = list(r.get("notes", []))
    h = r.get("hyp")
    if h is None:
        return out or ["기하 못 읽음"]
    t = (r["truth_live"]["value"] if r.get("truth_live", {}).get("ok") else None) or h.get("EXP")
    same = lambda a, b: a[0] == b[0] and (None in (a[1], b[1]) or a[1] == b[1])
    if t and h.get("APP") and not same(h["APP"], t):
        out.append(f"앱≠정답 {tuple(h['APP'])}≠{tuple(t)}")
    if h.get("EXP") and r.get("truth_live", {}).get("ok") and not same(h["EXP"], r["truth_live"]["value"]):
        out.append("번호표≠LIVE")
    if h.get("EXP") and h.get("LUT") and not same(h["EXP"], h["LUT"]):
        out.append("번호표≠die목록")
    if h.get("PRD") and h.get("APP") and not same(h["PRD"], h["APP"]):
        out.append(f"빌리기≠앱 {tuple(h['PRD'])}≠{tuple(h['APP'])}")
    if t and h.get("PRD") and not same(h["PRD"], t):
        out.append("빌리기≠정답")
    if not (h.get("EXP") or h.get("LUT")) and h.get("C1D") and h.get("G3") and h["C1D"] != h["G3"]:
        out.append("표 없음+계산식 갈림")
    if not r.get("cx"):
        out.append("중심 없음")
    if r.get("A1", {}).get("x_bad"):
        out.append("Col≠floor(X/pitch)")
    if r["folder"] in no_map:
        out.append("앱이 맵을 못 그림")
    return out


_COLLECT = ("Params_WaferInfo.ini", "ProductInfo.ini", "Wafer2Table.ini", "Recipe.ini", "MultiRecipe.ini",
            "WaferInfo.ini", "DieLut.dat", "ColorImageGrabingInfo.ini", "s_DieLocation.dat.md",
            "s_DieLocation.dat")


def collect_zip(recs, live_by_dir, base: Path, no_map=(), part_mb=30, file_mb=12):
    """이상 폴더의 원재료만 zip 으로.  사진은 이름만(``사진목록.txt``).

    한 파일이 ``part_mb`` 를 넘지 않게 ``_수집.zip`` · ``_수집_2.zip`` … 으로 나눈다(올리기 쉬운 크기).
    각 조각은 따로 풀리는 온전한 zip 이다.  담기 전 크기로 넘길지 정하므로 조각은 한도보다 작다.
    """
    flagged = [(r, flags(r, no_map)) for r in recs]
    flagged = [(r, f) for r, f in flagged if f]
    if not flagged:
        return [], 0
    limit, per_product = part_mb * 2 ** 20, Counter()
    paths = [base.with_name(base.name + "_수집.zip")]
    z = zipfile.ZipFile(paths[0], "w", zipfile.ZIP_DEFLATED)

    def room(size):
        """``size`` 바이트를 더 담으면 한도를 넘을 때 다음 조각으로 넘어간다."""
        nonlocal z
        if z.namelist() and z.fp.tell() + size > limit:
            z.close()
            paths.append(base.with_name(f"{base.name}_수집_{len(paths) + 1}.zip"))
            z = zipfile.ZipFile(paths[-1], "w", zipfile.ZIP_DEFLATED)

    def text(arcname, body):
        room(len(body.encode("utf-8")) + 1024)
        z.writestr(arcname, body)

    try:
        text("이유.txt", "\n".join(f"{r['folder']}\t{'; '.join(f)}" for r, f in flagged))
        for i, (r, _f) in enumerate(flagged):
            key = product_key(r) if "px" in r else "?"
            per_product[key] += 1
            if per_product[key] > 4:                 # 같은 제품은 4폴더면 충분하다
                continue
            src = Path(r["folder"])
            arc = f"{i:03d}_{src.parent.name}_{src.name}"
            for name in _COLLECT:
                p = src / name
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                if size > file_mb * 2 ** 20:
                    continue
                room(size + 1024)                    # 압축 전 크기 기준 → 조각은 늘 한도 안
                try:
                    z.write(p, f"{arc}/{name}")
                except OSError:
                    continue
            try:
                imgs = sorted(e.name for e in os.scandir(src) if Path(e.name).suffix.lower() in IMG_EXT)
            except OSError:
                imgs = []
            if imgs:
                text(f"{arc}/사진목록.txt", "# 사진 파일명(사진 자체는 담지 않음)\n" + "\n".join(imgs))
            w = r["wafer"]
            lv = [n for d, names in live_by_dir.items() for n, _ in names
                  if w in Path(n).stem.split("_")]
            if lv:
                text(f"{arc}/사진목록_Conder.txt", "# Conder LIVE 사진 파일명\n" + "\n".join(lv))
    finally:
        z.close()
    return paths, len(flagged)


def run_nas(argv, out: Path, progress=None, stop=None):
    """``progress(done, total, 문구)`` — total 0 이면 진행량 모름(busy).  ``stop()`` 이 참이면
    그 단계에서 멈추고 거기까지 돈 것으로 정리한다(앱의 수집 버튼이 쓴다)."""
    progress = progress or (lambda *_: None)
    stop = stop or (lambda: False)

    def log(line):
        print(line)
        progress(0, 0, str(line).strip()[:70])

    def until(t):                             # 마감 시각 또는 중지 요청
        return lambda: time.time() > t or stop()

    def opt(name, default):
        if name in argv:
            i = argv.index(name)
            v = argv[i + 1]
            del argv[i:i + 2]
            return float(v)
        return default
    minutes, per_lot, lots = opt("--minutes", 30), int(opt("--per-lot", 3)), int(opt("--lots", 5))
    cam_roots, conder_roots = [Path(a) for a in argv], []
    if not cam_roots:
        cam_roots, conder_roots = nas_default_roots()
    else:                                    # 준 폴더 가운데 'Conder' 가 든 경로는 정답 쪽으로 쓴다
        conder_roots = [p for p in cam_roots if "conder" in str(p).lower()]
        cam_roots = [p for p in cam_roots if p not in conder_roots]
    print("Camtek 쪽:", *[f"\n  {p}" for p in cam_roots] or ["없음"])
    print("Conder 쪽:", *[f"\n  {p}" for p in conder_roots] or ["없음"])
    t0 = time.time()
    folders, cut1 = nas_walk(cam_roots, until(t0 + minutes * 60 * 0.6), per_lot, lots, log)
    klas_files = []
    wanted = {wafer_name(f) for f in folders}
    live_by_dir, cut2 = nas_conder(conder_roots, wanted, until(t0 + minutes * 60 * 0.85), log)
    for f in folders:                        # 폴더 안에 LIVE 이름 사진이 같이 있는 경우
        try:
            names = [e.name for e in os.scandir(f) if Path(e.name).suffix.lower() in IMG_EXT][:_LIVE_CAP]
        except OSError:
            continue
        lv = [(n, q) for n, q in ((n, parse_live(n)) for n in names) if q]
        if lv:
            live_by_dir[f] = lv
    print(f"\n웨이퍼 폴더 {len(folders)} · Conder 폴더 {len(live_by_dir)} · {time.time() - t0:.0f}초"
          + (" · 시간 제한으로 일부만 돌았습니다" if cut1 or cut2 else ""))
    recs = []
    for i, f in enumerate(sorted(folders)):
        if stop():
            break
        progress(i, len(folders), f"계산 {i + 1}/{len(folders)}")
        try:
            recs.append(analyze_camtek(f, live_by_dir))
        except Exception as e:                                  # noqa: BLE001 — 한 폴더가 전체를 못 멈춘다
            recs.append({"folder": str(f), "wafer": wafer_name(f), "lot": str(f.parent),
                         "notes": [f"읽다 실패 {type(e).__name__}: {e}"]})
        if i % 20 == 19:
            print(f"  계산 {i + 1}/{len(folders)}")
    for scope in ("LOT", "PRD"):
        borrow(recs, scope)
    no_map = app_check(recs, live_by_dir)
    base = report(recs, [], [], out, no_map)
    zips, n = collect_zip(recs, live_by_dir, base, no_map if isinstance(no_map, list) else ())
    print(f"\n이상 폴더 {n}" + (" — 원재료를 모았습니다: " + ", ".join(z.name for z in zips) if zips
                              else " — 모을 것이 없습니다"))
    return base, len(recs), n


def wafer_name(folder: Path) -> str:
    return re.sub(r"\(.*\)$", "", folder.name).strip()


def analyze_camtek(folder: Path, live_by_dir):
    r = {"folder": str(folder), "wafer": wafer_name(folder), "lot": str(folder.parent), "notes": []}
    params = read_text(_find_up(folder, "Params_WaferInfo.ini") or folder / "-")
    prod_own = read_text(folder / "ProductInfo.ini")
    prod = prod_own or read_text(_find_up(folder, "ProductInfo.ini") or folder / "-")
    px, py, psrc = key_num(params, "DieStep_X"), key_num(params, "DieStep_Y"), "Params DieStep"
    if not px or not py:
        px, py, psrc = key_num(prod, "XDieIndex"), key_num(prod, "YDieIndex"), "ProductInfo XDieIndex"
    if not px or not py:
        r["notes"].append("pitch 없음")
        return r
    dia = key_num(params, "Diameter") or key_num(prod, "Diameter") or 300000.0
    cx, cy, csrc = key_num(params, "Center_X"), key_num(params, "Center_Y"), "Params Center"
    if not cx or not cy:
        w = w2t_center(read_text(_find_up(folder, "Wafer2Table.ini") or folder / "-"))
        cx, cy, csrc = (w[0], w[1], "Wafer2Table") if w else (None, None, "없음")
    r.update(px=px, py=py, pitch_src=psrc, dia=dia, cx=cx, cy=cy, center_src=csrc)

    entries = ini_entries(read_text(folder / "ColorImageGrabingInfo.ini"))
    r["entries"] = len(entries)
    if entries:                                             # A1
        bad = sum(1 for _, X, _Y, c, *_ in entries if math.floor(X / px) != c)
        ydiff = defaultdict(set)
        for _, _X, Y, _c, row, rec in entries:
            ydiff[rec].add(math.floor(Y / py) - row)
        r["A1"] = {"n": len(entries), "x_bad": bad,
                   "y_diff": {str(k): sorted(v) for k, v in ydiff.items()}}

    hyp = {}
    sec = re.search(r"(?ims)^\s*\[ExportData\]\s*$(.*?)(?=^\s*\[|\Z)", prod_own or "")
    if sec:
        kv = dict(re.findall(r"(?m)^\s*(\w+)\s*=\s*(-?\d+)\s*(?:;.*)?$", sec.group(1)))
        if all(k in kv for k in ("cMin", "cMax", "rMin", "rMax")):
            hyp["EXP"] = (int(kv["cMin"]), int(kv["rMax"]))
            r["export"] = {k: int(kv[k]) for k in ("cMin", "cMax", "rMin", "rMax")}
    lut = dielut_cells(folder / "DieLut.dat")
    if lut:
        hyp["LUT"] = (min(i for i, _ in lut), max(j for _, j in lut))
        r["lut_n"] = len(lut)
    dat = _find_up(folder, "s_DieLocation.dat")
    dm = diemap_cells(dat, px, py) if dat else None
    if dm:
        hyp["MAP"] = (min(i for i, _ in dm), max(j for _, j in dm))
        r["map_n"] = len(dm)
    mm = re.search(r"(?im)^\s*WaferMapMatchMatrix\s*=\s*Ver\d+((?:\s*,\s*-?\d+)+)", prod_own or "")
    if mm:
        v = [int(t) for t in mm.group(1).replace(" ", "").strip(",").split(",")]
        if len(v) >= 6:
            hyp["MMX"] = (v[4], None)
            r["matchmatrix"] = v
    if cx and cy:
        hyp["C1D"] = h_c1d(cx, cy, dia, px, py)
        hyp["G3"] = h_grid(cx, cy, dia, px, py, EDGE_MM3)
        hyp["G0"] = h_grid(cx, cy, dia, px, py)
        hyp["CEN"] = h_grid(cx, cy, dia, px, py, center_only=True)
        r["flip_margin"] = flip_margin(cx, cy, dia, px, py)
        if dm:
            full = sum(1 for i in range(math.floor((cx - dia / 2) / px), math.ceil((cx + dia / 2) / px))
                       for j in range(math.floor((cy - dia / 2) / py), math.ceil((cy + dia / 2) / py))
                       if all(math.hypot(x - cx, y - cy) <= dia / 2 for x in (i * px, (i + 1) * px)
                              for y in (j * py, (j + 1) * py)))
            r["map_cover"] = round(len(dm) / full, 3) if full else None
    r["hyp"] = hyp

    recipe = read_text(folder / "Recipe.ini")
    sc, sr = key_num(recipe, "DisplayColShift"), key_num(recipe, "DisplayRowShift")
    r["shift"] = (int(sc or 0), int(sr or 0))
    r["shift_known"] = recipe is not None and sc is not None

    w = r["wafer"]                                           # 정답: LIVE 표결
    lives = [p for d, lv in live_by_dir.items() for n, p in lv
             if d == folder or w in Path(n).stem.split("_")]
    r["live_n"] = len(lives)
    if lives and entries:
        v = live_vote(entries, lives, px, py)
        if v:
            # LIVE 는 화면 번호(시프트 포함)다 → 실제 die 번호 기준으로 되돌린다.
            co, rt = v["value"]
            v["value"] = (co + r["shift"][0], rt - r["shift"][1])
            r["truth_live"] = v
    return r


def borrow(recs, scope):
    """LOT/PRD 가설 — 다른 웨이퍼의 '웨이퍼 중심이 번호 격자에서 놓인 자리'(u, v)를 빌린다.

    u = cx/px − col_origin · v = row_total + 1 − cy/py 는 die 가 웨이퍼에 인쇄된 위치라
    웨이퍼가 stage 어디 놓였든 같은 제품이면 같아야 한다(가설).  자기 자신은 빼고 빌린다."""
    for r in recs:
        if not r.get("cx") or "hyp" not in r:
            continue
        us, vs = [], []
        for o in recs:
            # 같은 웨이퍼의 다른 사본(샘플 폴더가 여러 곳에 있다)에서 빌리면 자기 답을 베끼는 것이다.
            if o is r or o["wafer"] == r["wafer"] or not o.get("cx") or "hyp" not in o:
                continue
            if abs(o["px"] - r["px"]) > 0.01 or abs(o["py"] - r["py"]) > 0.01:
                continue
            if scope == "LOT" and o["lot"] != r["lot"]:
                continue
            ref = o["hyp"].get("EXP") or o["hyp"].get("LUT")
            if ref:
                us.append(o["cx"] / o["px"] - ref[0])
                vs.append(ref[1] + 1 - o["cy"] / o["py"])
        if us:
            r["hyp"][scope] = (round(r["cx"] / r["px"] - median(us)),
                               round(median(vs) + r["cy"] / r["py"] - 1))


def analyze_kla(path: Path):
    txt = read_text(path) or ""
    h = kla_header(txt[:65536])
    if h is None:
        return None
    r = {"file": str(path), "wafer": h["wafer"], "pitch": h["pitch"], "center": h["center"],
         "dia": h["dia"], "notch": h["notch"], "eof": h["eof"]}
    (px, py), (cx, cy), R = h["pitch"], h["center"], h["dia"] / 2
    pred = [(math.ceil((c - R) / p), math.floor((c + R) / p) - 1) for c, p in ((cx, px), (cy, py))]
    r["pred_range"] = pred
    if h["plan"]:
        plan = [(min(a for a, _ in h["plan"]), max(a for a, _ in h["plan"])),
                (min(b for _, b in h["plan"]), max(b for _, b in h["plan"]))]
        r["plan_range"] = plan
        r["K1"] = [plan[0] == pred[0], plan[1] == pred[1]]
    r["_def"] = [(i * px + xr - cx, j * py + yr - cy) for xr, yr, i, j in kla_defects(txt)]
    r["defects"] = len(r["_def"])
    return r


def pair_kla_camtek(k, c, entries, window=400.0, tight=40.0):
    """같은 웨이퍼의 KLA ↔ Camtek 을 평면 좌표에서 짝지어 잔차 B(Camtek − KLA)를 잰다."""
    cam = [(X - c["cx"], c["cy"] - Y) for _, X, Y, *_ in entries]
    d = [(a - x, b - y) for x, y in k["_def"] for a, b in cam
         if abs(a - x) <= window and abs(b - y) <= window]
    if len(d) < 3:
        return None
    for _ in range(3):                                     # 중앙값 주변으로 좁힌다
        bx, by = median(v[0] for v in d), median(v[1] for v in d)
        near = [v for v in d if abs(v[0] - bx) <= tight and abs(v[1] - by) <= tight]
        if len(near) < 3 or len(near) == len(d):
            break
        d = near
    bx, by = median(v[0] for v in d), median(v[1] for v in d)
    px, py = c["px"], c["py"]
    wrap = lambda v, p: (v + p / 2) % p - p / 2
    scx, scy = k["center"]
    return {"wafer": c["wafer"], "pairs": len(d), "B": (round(bx, 1), round(by, 1)),
            "spread": (round(max(v[0] for v in d) - min(v[0] for v in d), 1),
                       round(max(v[1] for v in d) - min(v[1] for v in d), 1)),
            # die 안쪽 좌표로 비교할 때 보이는 어긋남 = B + 두 장비의 격자 위상 차(파일에서 계산)
            "phase": (round(wrap(c["cx"] - scx, px), 1), round(wrap(c["cy"] + scy, py), 1))}


def app_check(recs, live_by_dir):
    """(선택) 지금 앱이 채택하는 값과 맵을 그릴 수 있는지 — 저장소 안에서 돌릴 때만.

    앱 코드를 못 불러오면 조용히 건너뛴다(가설 채점은 앱과 독립이라 영향 없다)."""
    try:
        sys.path.insert(0, str(REPO))
        from aoi_verification.app.coords import wafer_geometry as wg, wafer_map as wm
    except Exception as e:                                  # noqa: BLE001
        return f"앱 코드를 불러오지 못해 APP 대조는 건너뜀({type(e).__name__})"
    no_map = []
    for r in recs:
        f = Path(r["folder"])
        try:
            g = wg.camtek_geometry(f)
            frame = wm.frame_for_folder(f, "camtek")
        except Exception as e:                              # noqa: BLE001
            r["notes"].append(f"앱 예외 {type(e).__name__}")
            continue
        if g is not None and "hyp" in r:
            r["hyp"]["APP"] = (g.col_origin, g.row_total)
            r["app_source"] = g.source
        elif r.get("entries"):
            r["notes"].append("앱: die 기하 없음(절대좌표)")
        if frame is None and (r.get("entries") or r.get("live_n")):
            no_map.append(r["folder"])
    known = {r["folder"] for r in recs}
    for d in live_by_dir:
        if str(d) not in known:
            try:
                if wm.frame_for_folder(d, "camtek") is None:
                    no_map.append(str(d))
            except Exception:                               # noqa: BLE001
                no_map.append(str(d))
    return no_map


# ───────────────────────── 채점 · 출력 ─────────────────────────
def score(recs):
    table = {}
    for truth in ("LIVE", "EXP"):
        for h in NUM_HYP:
            if truth == h:
                continue
            n = col_ok = row_ok = both = 0
            wrong, uniq = [], set()
            for r in recs:
                t = (r.get("truth_live", {}).get("value") if truth == "LIVE" and
                     r.get("truth_live", {}).get("ok") else r.get("hyp", {}).get("EXP")
                     if truth == "EXP" else None)
                v = r.get("hyp", {}).get(h)
                if not t or not v:
                    continue
                n += 1
                uniq.add(r["wafer"])
                c_ok, r_ok = v[0] == t[0], v[1] is None or v[1] == t[1]
                col_ok += c_ok
                row_ok += r_ok
                both += c_ok and r_ok
                if not (c_ok and r_ok):
                    wrong.append(f'{r["wafer"]}({v[0]},{v[1]}≠{t[0]},{t[1]})')
            table[f"{h}|{truth}"] = {"n": n, "col": col_ok, "row": row_ok, "both": both, "wrong": wrong,
                                     "wafers": len(uniq),
                                     "products": len({product_key(r) for r in recs if r["wafer"] in uniq
                                                      and "px" in r})}
    return table


def discrim(recs):
    """R1 구분표 — 두 가설이 **다른 답을 낸** 웨이퍼 수.  0 이면 이 표본은 둘을 구분하지 못한다."""
    out = {}
    for i, a in enumerate(NUM_HYP):
        for b in NUM_HYP[i + 1:]:
            both = [(r["hyp"][a], r["hyp"][b]) for r in recs
                    if r.get("hyp", {}).get(a) and r.get("hyp", {}).get(b)]
            diff = sum(1 for x, y in both if x[0] != y[0] or (None not in (x[1], y[1]) and x[1] != y[1]))
            out[f"{a}~{b}"] = (diff, len(both))
    return out


def product_key(r):
    return f'{r["px"]:.1f}×{r["py"]:.1f}'


def report(recs, klas, pairs, out_dir: Path, no_map=None):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sc, dc = score(recs), discrim(recs)
    L = []
    geo = [r for r in recs if "hyp" in r]
    L.append(f"좌표 가설 실험 {stamp} — Camtek 웨이퍼 폴더 {len(recs)} (기하 읽음 {len(geo)}) · "
             f"KLA 파일 {len(klas)} · KLA↔Camtek 짝 {len(pairs)}")
    prods = Counter(product_key(r) for r in geo)
    L.append("제품(pitch) " + " · ".join(f"{k}: {n}장" for k, n in prods.most_common()))
    L.append("")
    a1 = [r["A1"] for r in geo if "A1" in r]
    L.append(f"[A1] die 격자가 stage 원점에 고정 — 폴더 {len(a1)} · 결함 {sum(a['n'] for a in a1)} 중 "
             f"Col≠floor(X/pitch) {sum(a['x_bad'] for a in a1)}건 · "
             f"Row 가 레시피별로 어긋난 폴더 {sum(1 for a in a1 if any(v != [0] for v in a['y_diff'].values()))}")
    L.append("")
    L.append("[번호] 가설별 채점 — 정답 LIVE(관측) / 정답 EXP(파일).  '둘다' = col·row 모두 일치")
    for truth in ("LIVE", "EXP"):
        for h in NUM_HYP:
            s = sc.get(f"{h}|{truth}")
            if s and s["n"]:
                L.append(f"  {h:>3} vs {truth:<4} 둘다 {s['both']:>3}/{s['n']:<3} "
                         f"(col {s['col']} · row {s['row']} · 고유 웨이퍼 {s['wafers']} · 제품 {s['products']})"
                         + (f"   틀림: {', '.join(s['wrong'][:6])}" + (" …" if len(s['wrong']) > 6 else "")
                            if s["wrong"] else ""))
    L.append("")
    L.append("[R1 구분표] 두 가설의 답이 갈린 웨이퍼 / 둘 다 계산된 웨이퍼 — 0 이면 이 표본으로는 구분 불가")
    L.append("  " + " · ".join(f"{k} {d}/{n}" for k, (d, n) in dc.items() if n))
    L.append("")
    L.append("[불변량] 웨이퍼 중심이 번호 격자에서 놓인 자리 u=cx/px−col원점, v=row기준+1−cy/py (정답이 있는 웨이퍼)")
    by = defaultdict(list)
    for r in geo:
        t = r["hyp"].get("EXP") or r["hyp"].get("LUT") or (
            r["truth_live"]["value"] if r.get("truth_live", {}).get("ok") else None)
        if t and r.get("cx"):
            by[product_key(r)].append((r["cx"] / r["px"] - t[0], t[1] + 1 - r["cy"] / r["py"]))
    inv = {}
    for k, v in by.items():
        du = (max(a for a, _ in v) - min(a for a, _ in v))
        dv = (max(b for _, b in v) - min(b for _, b in v))
        inv[k] = {"n": len(v), "u": round(median(a for a, _ in v), 4), "v": round(median(b for _, b in v), 4),
                  "du": round(du, 4), "dv": round(dv, 4)}
        L.append(f"  {k}: {len(v)}장 u={inv[k]['u']:.3f} (폭 {du:.4f} die) v={inv[k]['v']:.3f} (폭 {dv:.4f} die)")
    L.append("")
    fm = sorted((min(r["flip_margin"]), r["wafer"]) for r in geo if "flip_margin" in r)
    L.append("[경계] 중심이 이만큼(µm) 움직이면 중심식 답이 1 바뀐다 — 가까운 순 5장: "
             + ", ".join(f"{w} {m:.0f}" for m, w in fm[:5]))
    L.append("")
    k1 = [k for k in klas if "K1" in k]
    L.append(f"[K1] KLA 검사 목록 범위 = 중심 규칙 범위 — X {sum(k['K1'][0] for k in k1)}/{len(k1)} · "
             f"Y {sum(k['K1'][1] for k in k1)}/{len(k1)}"
             + ("   다름: " + ", ".join(f"{k['wafer']} 목록{k['plan_range']} 규칙{k['pred_range']}"
                                       for k in k1 if not all(k["K1"]))[:400] if any(not all(k["K1"]) for k in k1) else ""))
    if pairs:
        bx, by_ = [p["B"][0] for p in pairs], [p["B"][1] for p in pairs]
        # die 안쪽 y 는 두 장비 모두 위에서 아래로 재므로 평면(+Y 위) 잔차의 부호가 뒤집힌다.
        fx, fy = [p["B"][0] + p["phase"][0] for p in pairs], [p["phase"][1] - p["B"][1] for p in pairs]
        L.append(f"[K2] KLA↔Camtek 평면 잔차 B — 웨이퍼 {len(pairs)}장 · 짝 {sum(p['pairs'] for p in pairs)} · "
                 f"중앙값 ({median(bx):+.1f}, {median(by_):+.1f}) µm · 웨이퍼 간 폭 "
                 f"({max(bx) - min(bx):.1f}, {max(by_) - min(by_):.1f})")
        L.append(f"     die 안쪽 좌표로 보면 (B+위상차) 중앙값 ({median(fx):+.1f}, {median(fy):+.1f}) · "
                 f"웨이퍼 간 폭 ({max(fx) - min(fx):.1f}, {max(fy) - min(fy):.1f})")
    L.append("")
    if isinstance(no_map, str):
        L.append(f"[맵] {no_map}")
    elif no_map is not None:
        L.append(f"[맵] 앱이 Wafer map 틀을 못 만드는 폴더 {len(no_map)}"
                 + "".join(f"\n     {p}" for p in no_map[:30]))
    L.append("")
    L.append("[웨이퍼별] 정답 · 가설값 (col원점,row기준) — ✗ 는 정답과 다름")
    for r in sorted(geo, key=lambda r: (product_key(r), r["folder"])):
        t = (r["truth_live"]["value"] if r.get("truth_live", {}).get("ok") else None) or r["hyp"].get("EXP")
        cells = []
        for h in NUM_HYP:
            v = r["hyp"].get(h)
            if v:
                bad = t and (v[0] != t[0] or (v[1] is not None and v[1] != t[1]))
                cells.append(f"{h}={v[0]},{'·' if v[1] is None else v[1]}{'✗' if bad else ''}")
        tl = r.get("truth_live")
        L.append(f"  {product_key(r):>17} {r['wafer']:<16} 정답 {str(t) if t else '없음':<10} "
                 f"LIVE표 {str(tl['votes']) + '/' + str(tl['total']) if tl else '-':<7} " + " ".join(cells)
                 + (f"  [{'; '.join(r['notes'])}]" if r["notes"] else ""))
    for r in recs:
        if "hyp" not in r:
            L.append(f"  (기하 못 읽음) {r['folder']}: {'; '.join(r['notes'])}")

    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"좌표실험_{stamp}"
    text = "\n".join(L)
    base.with_suffix(".txt").write_text(text, encoding="utf-8")
    for k in klas:
        k.pop("_def", None)
    base.with_suffix(".json").write_text(json.dumps(
        {"camtek": recs, "kla": klas, "pairs": pairs, "score": sc, "discrim": dc, "invariant": inv},
        ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    base.with_suffix(".html").write_text(html_table(geo, sc, text), encoding="utf-8")
    print(text)
    print(f"\n저장: {base}.txt / .json / .html")
    return base


def html_table(geo, sc, text):
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;")
    rows = []
    for r in sorted(geo, key=lambda r: (product_key(r), r["folder"])):
        t = (r["truth_live"]["value"] if r.get("truth_live", {}).get("ok") else None) or r["hyp"].get("EXP")
        tds = []
        for h in NUM_HYP:
            v = r["hyp"].get(h)
            if not v:
                tds.append("<td class=n>—</td>")
                continue
            cls = "u" if not t else ("x" if v[0] != t[0] or (v[1] is not None and v[1] != t[1]) else "o")
            tds.append(f"<td class={cls}>{v[0]}, {'·' if v[1] is None else v[1]}</td>")
        rows.append(f"<tr><td>{product_key(r)}</td><td>{esc(r['wafer'])}</td>"
                    f"<td>{esc(t) if t else '없음'}</td>{''.join(tds)}</tr>")
    bars = "".join(
        f"<tr><td>{k.replace('|', ' vs ')}</td><td>{s['both']}/{s['n']}</td><td><div class=bar>"
        f"<i style='width:{100 * s['both'] / s['n']:.0f}%'></i></div></td></tr>"
        for k, s in sc.items() if s["n"])
    return f"""<!doctype html><meta charset=utf-8><title>좌표 가설 실험</title>
<style>body{{font:14px/1.5 'Malgun Gothic',sans-serif;margin:24px;color:#222}}table{{border-collapse:collapse;margin:12px 0}}
td,th{{border:1px solid #ccc;padding:3px 8px;white-space:nowrap}}th{{background:#f1f1f1}}
.o{{background:#d9f0dd}}.x{{background:#f6c9c4;font-weight:700}}.u{{background:#f4f4f4}}.n{{color:#aaa}}
.bar{{width:260px;height:12px;background:#f6c9c4}}.bar i{{display:block;height:12px;background:#4a9d5b}}
pre{{background:#f7f7f7;padding:12px;overflow:auto}}</style>
<h2>가설별 채점 (둘다 맞은 웨이퍼 / 채점한 웨이퍼)</h2><table>{bars}</table>
<h2>웨이퍼별 (초록 = 정답과 같음 · 빨강 = 다름 · 회색 = 정답 없음)</h2>
<table><tr><th>pitch</th><th>웨이퍼</th><th>정답</th>{''.join(f'<th>{h}</th>' for h in NUM_HYP)}</tr>
{''.join(rows)}</table><h2>요약</h2><pre>{esc(text)}</pre>"""


def main(argv):
    out = Path.home() / "Desktop"
    if "--out" in argv:
        i = argv.index("--out")
        out = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if "--nas" in argv:
        argv.remove("--nas")
        return run_nas(argv, out if out.is_dir() else Path.cwd())[0]
    roots = [Path(a) for a in argv] or [r for r in DEFAULT_ROOTS if r.is_dir()]
    asked = not roots
    while not roots:                       # 저장소 밖에서 더블클릭 — 폴더를 묻는다
        got = input("훑을 폴더 경로(여러 개면 | 로 구분): ").strip()
        roots = [Path(t.strip().strip('"')) for t in got.split("|") if Path(t.strip().strip('"')).is_dir()]
    if not out.is_dir():
        out = Path.cwd()
    camtek, kla_files, live_by_dir = scan(roots)
    recs = [analyze_camtek(f, live_by_dir) for f in sorted(camtek)]
    for scope in ("LOT", "PRD"):
        borrow(recs, scope)
    klas = [k for k in (analyze_kla(p) for p in sorted(kla_files)) if k]
    by_wafer = {r["wafer"]: r for r in recs if r.get("cx")}
    pairs = []
    for k in klas:
        c = by_wafer.get(k["wafer"])
        if c and k["_def"]:
            p = pair_kla_camtek(k, c, ini_entries(read_text(Path(c["folder"]) / "ColorImageGrabingInfo.ini")))
            if p:
                pairs.append(p)
    base = report(recs, klas, pairs, out, app_check(recs, live_by_dir))
    if asked:
        input("끝났습니다. 위 세 파일을 올려 주세요. (Enter 로 닫기)")
    return base


if __name__ == "__main__":
    main(sys.argv[1:])
