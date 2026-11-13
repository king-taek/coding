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
    pitch 번호 pitch = DieStep ÷ ``ProductInfo.ini [Geometric] ColsInBlock/RowsInBlock`` (파일, NAS 1차 수집)
          ``.wlup.`` 정렬 사진은 결함으로 세지 않는다
          칸 안 die 는 ``[DieRectInfo]`` 표에서 가장 가까운 die — 균등 나눔은 근사다(AMD Sepio 45/357 틀림)
    5차   결과는 zip 하나로(20 MB 마다 나눔) · 이상 폴더는 작은 설정 · 글자 파일을 전부 담는다 ·
          Conder 사진 이름의 (col,row,x,y) 를 장비 결함과 한 장씩 대조(번호만 다름 · die 안 좌표만 다름 · 뒤집힘)
    4차   앞선 수집에서 본 LOT 은 건너뛴다(``--fresh`` 로 끔) · Conder 사진 폴더마다 바로 옆 맵 txt 와만 대조
          (레시피 섞임 · die 안 좌표가 맵 die 크기를 넘는 사진 · 한 칸 밀림 · 칸 수×크기) · 큰 맵을 자르지 않는다.
    반증  3차 수집부터: 칸 표 대 균등 나눔 · die 시작점을 빼는지 · Conder 맵을 뒤집은 네 방향 ·
          LIVE 번호가 맵의 die/불량 칸에 드는지 · 사진 종류별 어긋남 · 장비 방향 설정을 함께 적는다
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


def is_align_photo(name: str) -> bool:
    """결함이 아닌 정렬 사진 — ``….wlup.jpeg``(Type=18, Col=0·Row=0).  실물: TB500 EDC AOI-28(파일).
    결함으로 세면 ``Col == floor(X/pitch)`` 검산이 깨진다.  이름으로만 가린다(Type 값의 뜻은 미확인)."""
    return ".wlup." in name.lower()


def ini_entries(txt: str | None):
    """ColorImageGrabingInfo.ini → [(이름, X, Y, Col, Row, 레시피)].  정렬 사진도 들어 있다(:func:`is_align_photo`)."""
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


def die_rects(prod: str | None):
    """``ProductInfo.ini [DieRectInfo]`` → {(a, b): (폭, 높이, x0, y0)} — 큰 칸 안 작은 die 의 자리(파일).
    첫 값(0/1)의 뜻은 미확인이라 쓰지 않는다."""
    sec = re.search(r"(?ims)^\s*\[DieRectInfo\]\s*$(.*?)(?=^\s*\[|\Z)", prod or "")
    out = {}
    for a, b, w, h, x0, y0 in re.findall(
            r"(?m)^\s*(\d+)_(\d+)\s*=\s*-?\d+\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)",
            sec.group(1) if sec else ""):
        out[(int(a), int(b))] = (float(w), float(h), float(x0), float(y0))
    return out


def rect_locator(rects, bc, br):
    """칸 안 좌표 (bx, by) → (a, b, die 안 x, die 안 y).  가장 가까운 die 를 고른다(틈에 찍힌 결함도 한 die 에 든다)."""
    mid = lambda v: sorted(v)[len(v) // 2]
    xc = [mid([rects[(a, b)][2] + rects[(a, b)][0] / 2 for b in range(br)]) for a in range(bc)]
    yc = [mid([rects[(a, b)][3] + rects[(a, b)][1] / 2 for a in range(bc)]) for b in range(br)]

    def locate(bx, by):
        a = min(range(bc), key=lambda k: abs(bx - xc[k]))
        b = min(range(br), key=lambda k: abs(by - yc[k]))
        return a, b, bx - rects[(a, b)][2], by - rects[(a, b)][3]
    return locate


def _spread(items):
    """맨 앞(가장 최근)부터 시작해 전체에 고르게 퍼지는 순서 — 0, n/2, n/4, 3n/4 …
    최근 것만 보면 시간 제한 안에서 늘 같은 폴더만 본다(1·2차 수집)."""
    n = len(items)
    bits = max(1, (n - 1).bit_length())
    order = sorted(range(1 << bits), key=lambda k: int(format(k, f"0{bits}b")[::-1], 2))
    return [items[k] for k in order if k < n]


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


def live_vote(entries, lives, px, py, tol=2.5, inner=None):
    """LIVE (col,row,x,y) 와 INI 결함을 die 안쪽 좌표로 짝지어 (col_origin, row_total) 을 표결한다.
    ``inner`` = [(i, j, die 안 x, die 안 y)] 를 주면 그것을 쓴다(칸 표로 계산한 값)."""
    if inner is None:
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


def same_scan_prefix(folder: Path) -> str:
    """이 장비 폴더의 스캔으로 찍은 Conder 사진 이름의 앞머리 — ``레시피_셋업_웨이퍼_``.

    5차 수집은 웨이퍼 ID 만 보고 견줘서, 다른 공정 사진(결함이 다르다)을 '좌표가 다르다' 로 셌다
    (어긋난 폴더 382 중 370 이 다른 스캔이었다 — 관측 2026-11-12)."""
    return f"{folder.parents[2].name}_{folder.parent.name}_{folder.name}_"


def conder_xy_check(inner, origin, named, px, py, tol=2.5):
    """Conder 사진 이름의 (col,row,x,y) 가 **장비가 적은 결함**과 같은가 — 사진마다 갈라 센다(5차 수집).

    ``inner`` = 장비 결함 [(i, j, die 안 x, die 안 y)], ``origin`` = 번호표 원점(col_origin, row_total) 또는 None.
    same  번호도 die 안 좌표도 같다 · moved  die 안 좌표는 같은데 번호가 다르다(어긋난 칸 수를 센다) ·
    inner 같은 die 인데 die 안 좌표가 다르다(가장 가까운 결함과의 차이) · none  장비 결함에 짝이 없다
    (다른 스캔의 사진일 수 있다).  die 안 좌표를 좌우 · 상하로 뒤집어야 맞는 사진도 따로 센다.
    원점을 모르면 번호는 못 가리고 die 안 좌표가 맞는 사진 수만 적는다(``xy_only``)."""
    out = {"n": len(named), "same": 0, "moved": {}, "inner": 0, "none": 0, "flip_x": 0, "flip_y": 0,
           "xy_only": 0, "examples": []}
    near = lambda a, b: abs(a - b) <= tol
    cam = [((i - origin[0], origin[1] - j) if origin else None, x, y) for i, j, x, y in inner]
    by_die = {}
    for c, x, y in cam:
        by_die.setdefault(c, []).append((x, y))
    diffs = []
    for name, (col, row, x, y) in named:
        hits = [c for c, xi, yi in cam if near(xi, x) and near(yi, y)]
        if not origin:
            out["xy_only"] += bool(hits)
            kind = "xy" if hits else "none"
        elif (col, row) in hits:
            kind = "same"
        elif hits:
            kind = "moved"
            k = f"{col - hits[0][0]},{row - hits[0][1]}"
            out["moved"][k] = out["moved"].get(k, 0) + 1
        elif (col, row) in by_die:
            kind = "inner"
            dx, dy = min(((x - xi, y - yi) for xi, yi in by_die[(col, row)]), key=lambda d: abs(d[0]) + abs(d[1]))
            diffs.append((dx, dy))
        else:
            kind = "none"
        if kind in ("same", "inner", "none"):
            out[kind] += 1
        if kind in ("inner", "none"):                       # 뒤집힌 표기인가
            out["flip_x"] += any(near(px - xi, x) and near(yi, y) for _c, xi, yi in cam)
            out["flip_y"] += any(near(xi, x) and near(py - yi, y) for _c, xi, yi in cam)
        if kind in ("moved", "inner") and len(out["examples"]) < 8:
            out["examples"].append([kind, name])
    if diffs:
        out["inner_d"] = [round(median(d[0] for d in diffs), 1), round(median(d[1] for d in diffs), 1)]
    return out


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


def recipe_shift(folder: Path):
    """``Recipe.ini [Display]`` 화면 번호 시프트 ``((col, row), 찾은 곳)`` — 레시피가 없으면 ``(None, None)``.

    앱(``camtek_ini.display_shift``)은 웨이퍼 폴더의 Recipe.ini 만 본다.  시프트 조사(2026-11-13)
    에서는 위 폴더에 있는 레시피도 찾아 '앱이 놓치는 시프트' 가 있는지까지 본다."""
    p = _find_up(folder, "Recipe.ini")
    if p is None:
        return None, None
    t = read_text(p)
    sc, sr = key_num(t, "DisplayColShift"), key_num(t, "DisplayRowShift")
    return (int(sc or 0), int(sr or 0)), ("폴더" if p.parent == folder else "상위")


def has_shift(picked, census=None) -> bool:
    """LOT 에서 고른 웨이퍼 가운데 화면 시프트가 0 이 아닌 레시피가 있나 — 시프트 조사 모드의 거르개.

    ``census`` 리스트를 주면 확인한 LOT 마다 ``(셋업 폴더, 시프트, 레시피 위치, 번호표 유무)`` 를 남긴다 —
    시프트가 없는 셋업까지 적어야 '몇 개 중 몇 개' 를 말할 수 있다(2차 시프트 수집, 2026-11-14)."""
    hit = None
    for f in picked:
        shift, where = recipe_shift(f)
        if (shift or (0, 0)) != (0, 0):
            hit = (f, shift, where)
            break
    if census is not None and picked:
        f, shift, where = hit or (picked[0], *recipe_shift(picked[0]))
        prod = read_text(_find_up(f, "ProductInfo.ini") or f / "-")
        census.append((str(f.parent.parent), shift, where, bool(prod and "[ExportData]" in prod)))
    return hit is not None


def write_census(census, path: Path) -> Path:
    """확인한 셋업 목록 — 셋업마다 한 줄(첫 LOT 기준), 시프트 있는 것부터."""
    rows = {}
    for setup, shift, where, table in census:
        rows.setdefault(setup, (shift, where, table))
    order = sorted(rows.items(), key=lambda kv: ((kv[1][0] or (0, 0)) == (0, 0), kv[0]))
    n_shift = sum(1 for _, (sh, _w, _t) in order if (sh or (0, 0)) != (0, 0))
    lines = [f"# 확인한 셋업 {len(order)}개 · 화면 시프트 있음 {n_shift}개",
             "# 셋업 폴더\t시프트(col,row)\t레시피 위치\t번호표([ExportData])"]
    for setup, (shift, where, table) in order:
        sh = "레시피 없음" if shift is None else f"{shift[0]},{shift[1]}"
        lines.append(f"{setup}\t{sh}\t{where or '-'}\t{'있음' if table else '없음'}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


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
_SCAN_CAP = 5000         # Conder 폴더 하나에서 훑는 항목 수 — 파일 보관 폴더는 수십만 개라 끝이 없다
_CONDER_SKIP_TOP = ("0. 전산",)   # 고객 폴더가 아닌 파일 보관함(사용자 확인 2026-11-10)


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


def _subdirs(d: Path, deadline=None):
    """(이름, 경로, 수정 시각) — 못 읽는 폴더는 빈 목록(NAS 권한 · 끊김).
    ``deadline`` 을 주면 항목마다 확인해 큰 폴더 한가운데서도 멈춘다."""
    out = []
    try:
        with os.scandir(d) as it:
            for e in it:
                if deadline is not None and _expired(deadline):
                    break
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


_SKIP_SIBLINGS = object()


def nas_walk(roots, deadline, per_lot=3, lots=5, log=print, on_lot=None, skip=frozenset(), want=None):
    """웨이퍼 폴더(= INI 가 든 폴더)를 찾는다.  LOT(웨이퍼들의 부모)마다 ``per_lot`` 장,
    한 폴더 아래 LOT 은 최근 ``lots`` 개만 — 번호 기준은 레시피의 것이라 몇 장이면 된다.
    ``want(고른 웨이퍼들)`` 이 거짓이면 그 LOT 은 세지 않고, **같은 폴더의 다른 LOT 도 건너뛴다**
    (시프트 조사 모드) — 레시피는 셋업 폴더 단위라 형제 LOT 도 같다.  예전에는 시프트 없는 셋업의
    LOT 을 전부 열어 보느라 30분 동안 웨이퍼 3장만 찾았다(1차 시프트 수집, 2026-11-14)."""
    found, state = [], {"lots": 0, "cut": False}

    def visit(d: Path, depth: int):
        """d 가 LOT 이었으면 True, 원하지 않는 LOT 이면 ``_SKIP_SIBLINGS``."""
        if _expired(deadline):
            state["cut"] = True
            return False
        subs = _spread(sorted(_subdirs(d, deadline), key=lambda t: -t[2]))  # 최근 것부터, 오래된 것까지 고르게
        if not subs or depth > _DEPTH:
            return False
        names = sorted(subs)
        if any(_is_wafer(p) for _, p, _ in (names[0], names[len(names) // 2], names[-1])):
            if str(d) in skip:                   # 앞선 수집에서 본 LOT — 세지 않고 지나가 새 LOT 을 찾는다
                state["skipped"] = state.get("skipped", 0) + 1
                return False
            step = max(1, len(names) // per_lot)
            picked = [p for _, p, _ in names[::step] if _is_wafer(p)][:per_lot]
            if want is not None and not want(picked):
                state["unwanted"] = state.get("unwanted", 0) + 1
                return _SKIP_SIBLINGS
            found.extend(picked)
            state["lots"] += 1
            log(f"  LOT {state['lots']:>4}  웨이퍼 {len(names):>3}장 중 {len(picked)}장  {d}")
            if on_lot is not None:               # 찾는 즉시 계산한다 — 중간에 멈춰도 거기까지 남는다
                on_lot(picked)
            return True
        n_lot = 0
        for _, p, _ in subs:
            got = visit(p, depth + 1)
            if got is _SKIP_SIBLINGS:
                break
            if got:
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


def strong_id(name: str) -> bool:
    """폴더 이름만으로 웨이퍼를 가릴 수 있나 — ``9`` · ``01`` 같은 이름은 아무 폴더와나 맞는다."""
    return len(name) >= 5 and not name.isdigit()


_LOT_MARK = re.compile(r"\(([A-Za-z0-9]{3})\)\s*$")      # Conder LOT 폴더 끝의 S/M: ``106. 6501169AK5 (CCG)``
_INPUT_LOT = re.compile(r"(?im)^\s*InputLot\s*=[ \t]*([A-Za-z0-9]{3})-")
_CONDER_BELOW_LOT = 4       # LOT 폴더 아래로 내려가는 깊이(공정 → [구분] → 웨이퍼)


def strip_mark_of(folder: Path):
    """Camtek 웨이퍼 폴더의 S/M(스트립 마크, 3글자).  ``WaferInfo.ini`` 의 ``InputLot=GFW-RDL4`` 첫 하이픈 앞
    (파일), 없으면 LOT 폴더 이름의 첫 토큰(``CCG`` · ``TSC-FS`` · ``SGG 1018S`` — 가정, 관측 5건).
    틀려도 정답이 오염되지는 않는다 — Conder 쪽에서 파일명에 웨이퍼 ID 가 든 사진만 센다."""
    m = _INPUT_LOT.search(read_text(_find_up(folder, "WaferInfo.ini") or folder / "-") or "")
    if not m:
        m = re.match(r"([A-Za-z0-9]{3})(?:$|[\s\-_])", folder.parent.name)
    return m.group(1).upper() if m else None


def conder_index(roots, deadline, log=print):
    """S/M → Conder LOT 폴더들.  구조는 ``고객 \\ 디바이스 \\ N. LOT (S/M) \\ 공정 …`` 이라(실측 2026-11-10:
    고객 80 · 디바이스 902 · LOT 28,783 · 표식 7,674종) 위 두 단만 열면 LOT 전부가 나온다.
    트리 전체는 수십만 폴더라 훑지 않는다."""
    index, cut = {}, False
    for r in roots:
        for cust, cp, _ in sorted(_subdirs(Path(r), deadline)):
            if cust.startswith(_CONDER_SKIP_TOP):
                continue
            log(f"  Conder 목록: {cust}")
            for _dev, dp, _ in _subdirs(cp, deadline):
                for lot, lp, _ in _subdirs(dp, deadline):
                    m = _LOT_MARK.search(lot)
                    if m:
                        index.setdefault(m.group(1).upper(), []).append(lp)
            if _expired(deadline):
                return index, True
    return index, cut


_MAP_CAP = 4 * 2 ** 20      # 맵 txt 하나의 크기 상한.  300 KB 였을 때 302×273 맵(330 KB)이 잘려 방향을 오판했다(3차 수집)
_FILES_CAP = 200            # 폴더마다 적어 두는 파일 이름 수


def conder_map_info(txt: str):
    """Conder LOT 폴더의 ``<WaferID>.txt`` 요약 — 칸 수 · die 크기 · 노치 · die/불량 칸 수(파일)."""
    kv = dict(re.findall(r"(?m)^\s*([A-Za-z]\w*)\s*:\s*([^\r\n]*?)\s*$", txt))
    rows = [ln.split(":", 1)[1].split() for ln in txt.splitlines() if ln.startswith("RowData:")]
    num = lambda k, f: (f(float(kv[k])) if re.fullmatch(r"-?\d+(\.\d+)?", kv.get(k, "")) else None)
    cells = [c for row in rows for c in row if not re.fullmatch(r"[_.\-]+", c)]
    good = kv.get("BCEQU", "000")
    return {"FNLOC": num("FNLOC", float), "ROWCT": num("ROWCT", int), "COLCT": num("COLCT", int),
            "XDIES": num("XDIES", float), "YDIES": num("YDIES", float),
            "rows": len(rows), "cols": max(map(len, rows), default=0),
            "dies": len(cells), "bad": sum(1 for c in cells if c != good)}


def conder_cells(txt: str):
    """맵 txt → (die 칸, 불량 칸, 열 수, 행 수).  칸 = (왼쪽부터 열, **아래부터** 행) — 앱 규칙(관측 9 LOT)."""
    kv = dict(re.findall(r"(?m)^\s*([A-Za-z]\w*)\s*:\s*([^\r\n]*?)\s*$", txt))
    rows = [ln.split(":", 1)[1].split() for ln in txt.splitlines() if ln.startswith("RowData:")]
    n, good = len(rows), kv.get("BCEQU", "000")
    if re.fullmatch(r"\d+", kv.get("ROWCT", "")):           # 행 번호는 머리글 행 수 기준 — 본문이 잘려도 안 밀린다
        n = max(n, int(kv["ROWCT"]))
    dies = {(c, n - 1 - i) for i, row in enumerate(rows) for c, v in enumerate(row)
            if not re.fullmatch(r"[_.\-]+", v)}
    bad = {(c, n - 1 - i) for i, row in enumerate(rows) for c, v in enumerate(row)
           if (c, n - 1 - i) in dies and v != good}
    return dies, bad, max(map(len, rows), default=0), n


def attach_conder_maps(r, extra):
    """웨이퍼 기록에 그 웨이퍼의 Conder 맵 요약을 붙이고 Camtek 값과 나란히 둔다(Conder 만으로 맵을 그릴 근거).

    반증용: 맵을 그대로 · 좌우 · 상하 · 둘 다 뒤집었을 때 Camtek die 목록과 겹치는 칸 수를 다 적는다
    ('왼쪽부터 · 아래부터' 가 틀렸다면 다른 쪽이 더 크다).  LIVE 사진 번호가 die 칸 · 불량 칸에 드는지도 센다."""
    out = []
    mine = _LAST if _LAST.get("folder") == r["folder"] else {}
    for p, txt in (extra or {}).get("maps", {}).items():
        if r["wafer"] not in re.split(r"[_\s]", Path(p).stem):
            continue
        m = dict(conder_map_info(txt), path=str(p))
        e = r.get("export")
        if e:
            m.update(exp_cols=e["cMax"] - e["cMin"] + 1, exp_rows=e["rMax"] - e["rMin"] + 1)
        if r.get("px") and m["XDIES"] and m["YDIES"]:        # XDIES 는 mm · die 크기(간격 아님, T254 관측)
            m["pitch_ratio"] = (m["XDIES"] * 1000 / r["px"], m["YDIES"] * 1000 / r["py"])
        S, bad, C, Rn = conder_cells(txt)
        D = mine.get("dies")
        if D:
            m["cam_dies"] = len(D)
            m["orient"] = {"그대로": len(D & S), "좌우": len(D & {(C - 1 - c, y) for c, y in S}),
                           "상하": len(D & {(c, Rn - 1 - y) for c, y in S}),
                           "둘다": len(D & {(C - 1 - c, Rn - 1 - y) for c, y in S})}
        lv = mine.get("lives") or []
        if lv:
            m["live_die"] = [sum(1 for q in lv if q in S), len(lv)]
            m["live_bad"] = [sum(1 for q in lv if q in bad), len(lv)]
        out.append(m)
    if out:
        r["conder_map"] = out


def conder_folder_checks(live, extra):
    """Conder 사진 폴더마다 **그 폴더 사진과 바로 옆 맵 txt 만** 대조한다(4차 수집).

    3차까지는 한 웨이퍼의 모든 공정 사진을 모든 맵에 대 봐서, 공정마다 맵이 다른 제품(36열/24열)과
    레시피가 섞인 폴더를 가려내지 못했다.  앱이 쓰는 자리(사진 폴더의 부모 ``<폴더명>.txt``)만 본다.
    적는 것: 레시피(이름 앞부분) 종류 · 레시피별 die 안 x/y 최댓값 · 맵 유무 · die 칸/불량 칸에 든 사진 ·
    불량 칸이 가장 잘 맞는 한 칸 이동 · ``칸 수 × XDIES``(지름과 맞는가) · die 안 좌표가 XDIES 보다 큰 사진."""
    maps = (extra or {}).get("maps", {})
    out = []
    for d, lv in live.items():
        d = Path(d)
        rec, inner = Counter(), {}
        for n, q in lv:
            key = Path(n).stem.split("_" + d.name + "_")[0] if "_" + d.name + "_" in n else "_".join(n.split("_")[:2])
            rec[key] += 1
            mx = inner.setdefault(key, [0.0, 0.0])
            mx[0], mx[1] = max(mx[0], q[2]), max(mx[1], q[3])
        r = {"dir": str(d), "n": len(lv), "recipes": dict(rec), "inner_max": inner, "map": False}
        txt = maps.get(d.parent / f"{d.name}.txt")
        if txt:
            info = conder_map_info(txt)
            S, bad, _C, _R = conder_cells(txt)
            cells = [(q[0], q[1]) for _n, q in lv]
            hit = {(dx, dy): sum(1 for c, y in cells if (c + dx, y + dy) in bad)
                   for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
            best = max(hit, key=lambda k: (hit[k], k == (0, 0)))
            r.update(map=True, cols=info["COLCT"], rows=info["ROWCT"], XDIES=info["XDIES"], YDIES=info["YDIES"],
                     dies=len(S), bad=len(bad), in_die=sum(1 for q in cells if q in S), on_bad=hit[(0, 0)],
                     shift=list(best), shift_hit=hit[best])
            if info["XDIES"] and info["YDIES"] and info["COLCT"] and info["ROWCT"]:
                r["span"] = [info["COLCT"] * info["XDIES"], info["ROWCT"] * info["YDIES"]]     # mm 라면 ≈ 지름
                r["spill"] = sum(1 for _n, q in lv if q[2] > info["XDIES"] * 1000 or q[3] > info["YDIES"] * 1000)
        out.append(r)
    return out


def known_lots(out_dir: Path):
    """앞선 수집에서 이미 본 LOT 폴더 — 다시 돌지 않고 새 LOT 으로 넘어간다.
    ``coord_lab_seen.txt``(1~3차) + 결과 폴더에 남아 있는 ``좌표실험_*.json``(그 뒤 수집)."""
    seen = set()
    listed = read_text(Path(__file__).with_name("coord_lab_seen.txt")) or ""       # 1~3차에 본 LOT(저장소에 둔 목록)
    seen.update(ln.strip() for ln in listed.splitlines() if ln.strip() and not ln.startswith("#"))
    texts = []
    for p in sorted(Path(out_dir).glob("좌표실험_*")):
        try:
            if p.suffix == ".json":
                texts.append(p.read_text(encoding="utf-8"))
            elif p.suffix == ".zip":                 # 5차부터 결과는 zip 안에 있다
                with zipfile.ZipFile(p) as z:
                    texts += [z.read(n).decode("utf-8") for n in z.namelist() if "/" not in n and n.endswith(".json")]
        except (OSError, ValueError, zipfile.BadZipFile):
            continue
    for t in texts:
        try:
            seen.update(r["lot"] for r in json.loads(t).get("camtek", []) if r.get("lot"))
        except (ValueError, AttributeError, TypeError):
            continue
    return seen


def nas_conder(roots, wanted, deadline, log=print, index=None, extra=None):
    """Conder Scan 에서 웨이퍼의 LIVE 파일명을 읽는다(사진은 안 연다).

    ``wanted`` = {웨이퍼 ID: S/M}.  S/M 이 같은 LOT 폴더 아래만 보고, **파일명에 그 웨이퍼 ID 토큰이 든
    사진**만 센다.  짧거나 숫자뿐인 ID(``strong_id`` 아님)와 S/M 을 모르는 웨이퍼는 찾지 않는다 —
    이름이 ``9`` 인 폴더를 보관함에서 찾아 웨이퍼로 짚은 적이 있다(실측)."""
    live = {}
    by_mark = {}
    for w, mark in wanted.items():
        if mark and strong_id(w):
            by_mark.setdefault(mark, set()).add(w)
    if not by_mark:
        return live, False
    cut = False
    if index is None:
        index, cut = conder_index(roots, deadline, log)

    def visit(d: Path, depth: int, ids):
        nonlocal cut
        if cut or _expired(deadline):
            cut = True
            return
        names, others, maps = [], [], []
        try:
            with os.scandir(d) as it:
                for i, e in enumerate(it):
                    if i >= _SCAN_CAP or len(names) >= _LIVE_CAP or _expired(deadline):
                        break
                    stem = Path(e.name)
                    if stem.suffix.lower() in IMG_EXT:
                        if ids.intersection(stem.stem.split("_")):
                            names.append(e.name)
                        continue
                    if extra is None or not e.is_file():
                        continue
                    if len(others) < _FILES_CAP:
                        others.append(e.name)
                    if stem.suffix.lower() == ".txt" and ids.intersection(re.split(r"[_\s]", stem.stem)):
                        maps.append(d / e.name)
        except OSError:
            return
        if extra is not None and (maps or (others and depth == 0)):
            extra.setdefault("files", {})[d] = others        # 맵 말고 장비가 남기는 파일이 있는지 보려고
            for p in maps:
                txt = read_text(p, _MAP_CAP)
                if txt and "RowData" in txt:
                    extra.setdefault("maps", {})[p] = txt
        lv = [(n, q) for n, q in ((n, parse_live(n)) for n in names) if q]
        if lv:
            live[d] = lv
            log(f"  Conder LIVE 이름 {len(lv)}  {d}")
        if depth < _CONDER_BELOW_LOT:
            for _name, p, _ in _subdirs(d, deadline):
                visit(p, depth + 1, ids)

    for mark, ids in sorted(by_mark.items()):
        lots = index.get(mark, [])
        log(f"  Conder S/M {mark}: LOT 폴더 {len(lots)}개 · 웨이퍼 {len(ids)}장")
        for lp in lots:
            visit(lp, 0, ids)
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
    if "block" in r:
        out.append("칸 안 die 제품")                       # 칸 표 규칙을 원재료로 다시 확인하려고 모은다
    if tuple(r.get("shift", (0, 0))) != (0, 0):
        out.append("화면 시프트 있음")
    if r.get("rect0"):
        out.append("die 시작점 있음")
    if any(n and k not in ("c", "wlup") and b for k, (n, b) in r.get("kinds", {}).items()):
        out.append("결함 아닌 사진 종류 의심")
    if r.get("env", {}).get("IsPanel") or r.get("env", {}).get("IsMultiIndex") or (r.get("dia") and abs(r["dia"] - 300000) > 1000):
        out.append("300 mm 원형이 아닌 형태")
    for m in r.get("conder_map", []):
        o = m.get("orient")
        if o and o["그대로"] < max(o.values()):
            out.append("Conder 맵 방향 의심")
            break
    if any(c["moved"] or c["inner"] or c["flip_x"] or c["flip_y"] for c in r.get("conder_xy", [])):
        out.append("Conder 좌표≠장비 좌표")
    if r.get("conder_odd"):
        out.append("Conder 사진·맵 어긋남: " + ", ".join(r["conder_odd"]))
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


_RAW_EXT = {".ini", ".dat", ".md", ".xml", ".txt", ".csv", ".lot", ".cfg", ".log", ".json", ".wlup", ".001"}
_RAW_FILES = 60             # 폴더 하나에서 담는 파일 수
_RAW_SMALL = 100 * 1024     # 정해 둔 이름이 아닌 파일은 이 크기까지만 — 큰 .dat 가 zip 의 85% 였다(5차: zip 25개)
_DIELOC = "s_DieLocation.dat"


def dieloc_text(data: bytes, md: str | None):
    """``s_DieLocation.dat`` (수 MB) 대신 담는 글 — die 위치(x, y µm)만.  배치를 못 읽으면 None.

    die 가 격자에 놓여 있으면 x 값 줄 · y 값 줄 · y 마다 0/1 한 줄(그 x 에 die 가 있나)로 적는다."""
    m = re.search(r'RecordSize\s+Size="(\d+)"', md or "")
    f = {n: (int(o), int(v)) for n, o, v in
         re.findall(r'Name="([^"]+)"[^>]*?Offset="(\d+)"[^>]*?Vartype="(\d+)"', md or "")}
    if not m or "x" not in f or "y" not in f or f["x"][1] != 5 or f["y"][1] != 5:
        return None
    rec = int(m.group(1))
    if rec <= 0 or not data or len(data) % rec:
        return None
    try:
        pts = {(round(struct.unpack_from("<d", data, i * rec + f["x"][0])[0], 1),
                round(struct.unpack_from("<d", data, i * rec + f["y"][0])[0], 1)) for i in range(len(data) // rec)}
    except struct.error:
        return None
    xs, ys = sorted({p[0] for p in pts}), sorted({p[1] for p in pts})
    head = f"# {_DIELOC} 의 die 위치(µm) {len(pts)}개 — 원본 대신 담았다\n"
    if len(xs) * len(ys) > 20 * len(pts):                  # 격자가 아니다 → 점 목록
        return head + "점\n" + "\n".join(f"{x}\t{y}" for x, y in sorted(pts))
    return (head + "격자\nx\t" + "\t".join(map(str, xs)) + "\ny\t" + "\t".join(map(str, ys)) + "\n"
            + "\n".join("".join("1" if (x, y) in pts else "0" for x in xs) for y in ys))


def keep_raw(name: str, size: int) -> bool:
    """원재료로 담을 파일인가 — 정해 둔 이름은 늘, 그 밖의 글자 · 설정 파일은 작은 것만."""
    ext = Path(name).suffix.lower()
    if ext in IMG_EXT:
        return False
    return name in _COLLECT or ((ext in _RAW_EXT or not ext) and size <= _RAW_SMALL)
_UP_NAMES = ("Recipe.ini", "MultiRecipe.ini", "ProductInfo.ini", "WaferInfo.ini", "Params_WaferInfo.ini")


def _raw_files(src: Path, file_mb):
    """원인 조사용으로 담을 파일 — 정해 둔 이름 + 작은 글자·설정 파일 전부(사진 제외).  ``(이름, 크기, 전체 목록 글)``."""
    picked, listing = [], []
    try:
        ents = sorted(os.scandir(src), key=lambda e: e.name)
    except OSError:
        return picked, ""
    for e in ents:
        try:
            size = e.stat().st_size if e.is_file() else -1
        except OSError:
            continue
        ext = Path(e.name).suffix.lower()
        if ext not in IMG_EXT:
            listing.append(f"{size}\t{e.name}" + ("\\" if size < 0 else ""))
        if size < 0 or size > file_mb * 2 ** 20 or ext in IMG_EXT:
            continue
        if keep_raw(e.name, size) and (e.name in _COLLECT or len(picked) < _RAW_FILES):
            picked.append((e.name, size))
    return picked, "# 크기(바이트)\t이름 — 사진은 뺐다(사진목록.txt)\n" + "\n".join(listing)


def collect_zip(recs, live_by_dir, base: Path, no_map=(), part_mb=20, file_mb=12, progress=None, conder=None,
                reports=()):
    """이상 폴더의 원재료만 zip 으로.  사진은 이름만(``사진목록.txt``).

    한 파일이 ``part_mb`` 를 넘지 않게 ``_수집.zip`` · ``_수집_2.zip`` … 으로 나눈다(올리기 쉬운 크기).
    각 조각은 따로 풀리는 온전한 zip 이다.  담기 전 크기로 넘길지 정하므로 조각은 한도보다 작다.
    """
    flagged = [(r, flags(r, no_map)) for r in recs]
    flagged = [(r, f) for r, f in flagged if f]
    conder = conder or {}
    reports = [p for p in reports if Path(p).is_file()]
    if not flagged and not conder.get("maps") and not conder.get("files") and not live_by_dir and not reports:
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
        for p in reports:                            # 결과 요약도 zip 안에 — 올릴 것은 zip 뿐이다(사용자 요청)
            room(Path(p).stat().st_size + 1024)
            z.write(p, Path(p).name)
        text("이유.txt", "\n".join(f"{r['folder']}\t{'; '.join(f)}" for r, f in flagged))
        for i, (r, _f) in enumerate(flagged):
            if progress is not None:
                progress(i, len(flagged), f"이상 폴더 원재료 모으는 중 {i + 1}/{len(flagged)}")
            key = product_key(r) if "px" in r else "?"
            per_product[key] += 1
            if per_product[key] > 8:                 # 같은 제품은 8폴더까지(원인을 가리려면 견줄 폴더가 있어야 한다)
                continue
            src = Path(r["folder"])
            arc = f"{i:03d}_{src.parent.name}_{src.name}"
            files, listing = _raw_files(src, file_mb)
            for name, size in files:
                try:
                    if name == _DIELOC:              # 수 MB 짜리 → die 위치만 글로
                        body = dieloc_text((src / name).read_bytes(), read_text(src / (name + ".md")))
                        if body:
                            text(f"{arc}/s_DieLocation.txt", body)
                            continue
                    room(size + 1024)                # 압축 전 크기 기준 → 조각은 늘 한도 안
                    z.write(src / name, f"{arc}/{name}")
                except OSError:
                    continue
            if listing:
                text(f"{arc}/폴더목록.txt", listing)
            for name in _UP_NAMES:                   # 레시피 · 제품 설정은 LOT · 셋업 폴더에 있기도 하다
                up = _find_up(src, name)
                if up is None or Path(up).parent == src:
                    continue
                try:
                    if Path(up).stat().st_size <= file_mb * 2 ** 20:
                        room(Path(up).stat().st_size + 1024)
                        z.write(up, f"{arc}/_상위/{Path(up).parent.name}/{name}")
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
        # Conder 쪽 맵 txt 는 이상 여부와 상관없이 담는다 — Conder 만으로 맵을 그릴 근거(작은 글자 파일).
        dirs = {}
        for p, txt in conder.get("maps", {}).items():
            n = dirs.setdefault(Path(p).parent, len(dirs))
            text(f"Conder/{n:03d}_{Path(p).parent.name}/{Path(p).name}", txt)
        per_dir = [f"{d}\t{n}" for d, names in live_by_dir.items() if not _is_wafer(Path(d)) for n, _ in names]
        if per_dir:                                  # 폴더를 가려 다시 대조할 수 있게 — 3차까지는 웨이퍼별로 뭉쳐 담았다
            text("Conder/사진목록_폴더별.txt", "# Conder 사진 폴더\t파일명\n" + "\n".join(per_dir))
        if conder.get("files"):
            text("Conder/파일목록.txt", "# Conder 폴더에 사진 말고 있는 파일(이름만)\n" + "\n".join(
                f"{d}\t{n}" for d, names in conder["files"].items() for n in sorted(names)))
    finally:
        z.close()
    return paths, len(flagged)


def collected_runs(folder: Path):
    """``folder`` 에 있는 수집 zip 을 수집 회차별로 — ``{좌표실험_<시각>: [zip …]}``.  이미 줄인 회차는 뺀다."""
    runs = {}
    try:
        for p in sorted(folder.glob("좌표실험_*_수집*.zip")):
            runs.setdefault(p.name.split("_수집")[0], []).append(p)
    except OSError:
        return {}
    return {k: v for k, v in runs.items() if not (folder / f"{k}_줄임.zip").is_file()}


def find_collected(desktop: Path):
    """바탕화면과 그 바로 아래 폴더에서 수집 zip 이 여러 개(올리기 벅찬 수) 있는 폴더를 찾는다."""
    try:
        dirs = [desktop] + sorted(Path(e.path) for e in os.scandir(desktop) if e.is_dir())
    except OSError:
        return None
    for d in dirs:
        if any(len(v) > 2 for v in collected_runs(d).values()):
            return d
    return None


def slim_zips(folder: Path, part_mb=20, progress=None):
    """이미 모은 ``_수집*.zip`` 을 **다시 수집하지 않고** 올리기 쉬운 크기로 다시 묶는다 → ``_줄임.zip``.

    뺄 것은 ``keep_raw`` 가 정한다(정해 둔 이름이 아닌 큰 파일 — 대부분 장비의 큰 .dat).
    ``s_DieLocation.dat`` 는 die 위치만 글로 바꿔 담는다.  원래 zip 은 건드리지 않는다.
    ``([만든 zip], 원래 바이트, 줄인 바이트)``."""
    progress = progress or (lambda *_: None)
    out, before, limit = [], 0, part_mb * 2 ** 20
    for stem, parts in collected_runs(folder).items():
        before += sum(p.stat().st_size for p in parts)
        srcs = [zipfile.ZipFile(p) for p in parts]
        try:
            index = {i.filename: (z, i) for z in srcs for i in z.infolist() if not i.is_dir()}
            paths = [folder / f"{stem}_줄임.zip"]
            dst = zipfile.ZipFile(paths[0], "w", zipfile.ZIP_DEFLATED)
            try:
                for n, (name, (z, info)) in enumerate(sorted(index.items())):
                    if n % 200 == 0:
                        progress(n, len(index), f"줄여 묶는 중 {n}/{len(index)}")
                    base, arc = Path(name).name, name
                    top = "/" not in name                # 맨 위 = 결과 요약 · 이유.txt
                    ours = top or name.startswith("Conder/") or base in ("사진목록.txt", "사진목록_Conder.txt", "폴더목록.txt")
                    if not ours and not keep_raw(base, info.file_size):
                        continue
                    data = z.read(info)
                    if base == _DIELOC:
                        md = index.get(name + ".md")
                        body = dieloc_text(data, md[0].read(md[1]).decode("utf-8", "replace") if md else None)
                        if body:
                            data, arc = body.encode("utf-8"), name[:-len(_DIELOC)] + "s_DieLocation.txt"
                    if dst.namelist() and dst.fp.tell() + len(data) + 1024 > limit:
                        dst.close()
                        paths.append(folder / f"{stem}_줄임_{len(paths) + 1}.zip")
                        dst = zipfile.ZipFile(paths[-1], "w", zipfile.ZIP_DEFLATED)
                    dst.writestr(arc, data)
            finally:
                dst.close()
        finally:
            for z in srcs:
                z.close()
        out += paths
    return out, before, sum(p.stat().st_size for p in out)


def run_nas(argv, out: Path, progress=None, stop=None):
    """NAS 를 돌며 **LOT 을 찾는 즉시 계산**하고, 끝나거나 멈추면 그때까지 것으로 결과를 쓴다.

    ``progress(done, total, 문구)`` — total 0 이면 진행량 모름(busy).  ``stop()`` 이 참이면 찾기를 그만두고
    **지금까지 계산한 웨이퍼로** 결과를 남긴다(앱의 수집 버튼이 쓴다).  예전에는 다 찾은 뒤에 계산해서
    LOT 3000개를 찾고 멈추면 아무것도 남지 않았다(실측 2026-11-10).  웨이퍼가 하나도 없으면 ``(None, 0, 0)``."""
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
    fresh = "--fresh" in argv                 # 앞선 수집에서 본 LOT 도 다시 돈다
    if fresh:
        argv.remove("--fresh")
    shift_only = "--shift-only" in argv       # 화면 시프트가 0 이 아닌 레시피의 LOT 만 모은다(2026-11-13)
    if shift_only:
        argv.remove("--shift-only")
        print("시프트 조사 모드 — Recipe.ini 화면 시프트가 0 이 아닌 LOT 만 모읍니다")
    seen = frozenset() if fresh else frozenset(known_lots(out))
    print(f"앞선 수집에서 본 LOT {len(seen)}개는 건너뜁니다" if seen else "앞선 수집 결과 없음 — 전부 새로 돕니다")
    cam_roots, conder_roots = [Path(a) for a in argv], []
    if not cam_roots:
        cam_roots, conder_roots = nas_default_roots()
    else:                                    # 준 폴더 가운데 'Conder' 가 든 경로는 정답 쪽으로 쓴다
        conder_roots = [p for p in cam_roots if "conder" in str(p).lower()]
        cam_roots = [p for p in cam_roots if p not in conder_roots]
    print("Camtek 쪽:", *[f"\n  {p}" for p in cam_roots] or ["없음"])
    print("Conder 쪽:", *[f"\n  {p}" for p in conder_roots] or ["없음"])
    t0 = time.time()
    t_walk = t0 + minutes * 60 * 0.85        # 남은 15% 는 결과 쓰기 · 원재료 모으기
    index, _ = conder_index(conder_roots, until(t0 + minutes * 60 * 0.15), log) if conder_roots else ({}, False)
    print(f"Conder S/M {len(index)}종 · LOT 폴더 {sum(map(len, index.values()))} · {time.time() - t0:.0f}초")
    recs, live_by_dir, no_map, app_note, conder = [], {}, set(), [], {}
    quiet = lambda *_: None

    def on_lot(picked):
        wanted = {wafer_name(f): strip_mark_of(f) for f in picked}
        new = nas_conder(conder_roots, wanted, until(t_walk), quiet, index, conder)[0]
        live_by_dir.update(new)
        res = app_check([], new)              # LIVE 파일명뿐인 Conder 폴더에서 앱이 맵을 그릴 수 있나
        if isinstance(res, list):
            no_map.update(res)
        for f in picked:
            if stop():
                return
            try:                              # 폴더 안에 LIVE 이름 사진이 같이 있는 경우
                names = [e.name for e in os.scandir(f) if Path(e.name).suffix.lower() in IMG_EXT][:_LIVE_CAP]
                lv = [(n, q) for n, q in ((n, parse_live(n)) for n in names) if q]
                if lv:
                    live_by_dir[f] = lv
                r = analyze_camtek(f, live_by_dir)
            except Exception as e:            # noqa: BLE001 — 한 폴더가 전체를 못 멈춘다
                r = {"folder": str(f), "wafer": wafer_name(f), "lot": str(f.parent),
                     "notes": [f"읽다 실패 {type(e).__name__}: {e}"]}
            attach_conder_maps(r, conder)
            recs.append(r)
            res = app_check([r], {})          # 앱 대조도 그 자리에서 — 끝에 몰면 NAS 를 한 번 더 돈다
            (no_map.update if isinstance(res, list) else app_note.append)(res)
        left = max(0, int((t_walk - time.time()) / 60))
        progress(0, 0, f"웨이퍼 {len(recs)}장 계산 · 남은 시간 약 {left}분 · {picked[0].parent.name}"[:70])

    census = []                               # 시프트 조사: 확인한 셋업 전부(시프트 없는 것도)
    cut = False
    for i, root in enumerate(cam_roots):      # 장비마다 남은 시간을 고르게 — 첫 장비가 시간을 다 쓰지 않게
        if stop() or time.time() > t_walk:
            cut = True
            break
        share = time.time() + (t_walk - time.time()) / (len(cam_roots) - i)
        cut = nas_walk([root], until(share), per_lot, lots, print, on_lot, seen,
                       (lambda picked: has_shift(picked, census)) if shift_only else None)[1] or cut
    print(f"\n웨이퍼 폴더 {len(recs)} · Conder 폴더 {len(live_by_dir)} · {time.time() - t0:.0f}초"
          + (" · 중지했습니다" if stop() else " · 시간 제한으로 일부만 돌았습니다" if cut else ""))
    census_file = None
    if shift_only:
        census_file = write_census(census, out / f"좌표실험_{datetime.now():%Y%m%d_%H%M%S}_시프트목록.txt")
        print(f"확인한 셋업 목록: {census_file}")
    if not recs:
        return census_file, 0, 0
    progress(0, 0, "결과 정리 중")
    for scope in ("LOT", "PRD"):
        borrow(recs, scope)
    dirs = conder_folder_checks({d: lv for d, lv in live_by_dir.items() if not _is_wafer(Path(d))}, conder)
    odd = {}
    for c in dirs:                            # 사진과 옆 맵이 어긋난 폴더 → 그 웨이퍼의 장비 원재료도 모은다
        why = [t for t, bad in (("die 칸 밖", c["map"] and c["in_die"] < c["n"]), ("크기 넘침", c.get("spill")),
                                ("한 칸 밀림", c["map"] and c["bad"] and c["shift"] != [0, 0]),
                                ("레시피 섞임", len(c["recipes"]) > 1)) if bad]
        if why:
            odd.setdefault(Path(c["dir"]).name, set()).update(why)
    for r in recs:
        if r["wafer"] in odd:
            r["conder_odd"] = sorted(odd[r["wafer"]])
    base = report(recs, [], [], out, sorted(no_map) if not app_note else app_note[0], conder_dirs=dirs)
    loose = [base.with_suffix(e) for e in (".txt", ".json", ".html")] + ([census_file] if census_file else [])
    zips, n = collect_zip(recs, live_by_dir, base, sorted(no_map), progress=progress, conder=conder, reports=loose)
    if zips:                                  # 결과물은 zip 뿐 — 낱개 파일은 zip 에 담았으니 지운다
        for p in loose:
            p.unlink(missing_ok=True)
    print(f"\n이상 폴더 {n}" + (" — 원재료를 모았습니다: " + ", ".join(z.name for z in zips) if zips
                              else " — 모을 것이 없습니다"))
    return base, len(recs), n


def wafer_name(folder: Path) -> str:
    return re.sub(r"\(.*\)$", "", folder.name).strip()


_LAST = {}      # 방금 분석한 폴더의 LIVE 번호 · die 목록(Conder 맵 대조에 한 번 쓰고 버린다 — JSON 에 넣기엔 크다)


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
    entries = ini_entries(read_text(folder / "ColorImageGrabingInfo.ini"))
    step_x, step_y = px, py
    kinds = defaultdict(lambda: [0, 0])            # 사진 종류(이름의 셋째 토막: c · t · wlup …)별 [장수, Col 어긋남]
    for name, X, _Y, c, *_ in entries:
        tok = name.split(".")
        k = kinds[tok[2].lower() if len(tok) >= 4 else "?"]
        k[0] += 1
        k[1] += math.floor(X / px) != c
    r["kinds"] = dict(kinds)
    r["skipped_align"] = sum(1 for e in entries if is_align_photo(e[0]))
    entries = [e for e in entries if not is_align_photo(e[0])]
    # 번호 die 는 스캔 칸(DieStep)보다 작을 수 있다 — ProductInfo [Geometric] ColsInBlock/RowsInBlock 가
    # 칸 하나에 든 번호 die 수다(파일: Qorvo XP2100 18×23 · QORVO W97320 19×13 · INCI N7G751 1×2).
    bc, br = int(key_num(prod, "ColsInBlock") or 1), int(key_num(prod, "RowsInBlock") or 1)
    if bc >= 1 and br >= 1 and (bc, br) != (1, 1):
        bad = lambda qx: sum(1 for _, X, _Y, c, *_ in entries if math.floor(X / qx) != c)
        if bad(px / bc) <= bad(px):               # 나눈 쪽이 더 틀릴 때만 나누지 않는다(그런 실물은 없다)
            r.update(step=(px, py), block=(bc, br))
            px, py, psrc = px / bc, py / br, psrc + f" ÷ 칸 안 die {bc}×{br}"
        else:
            r["notes"].append(f"칸 안 die {bc}×{br} 로 나누면 더 어긋남")
    dia = key_num(params, "Diameter") or key_num(prod, "Diameter") or 300000.0
    cx, cy, csrc = key_num(params, "Center_X"), key_num(params, "Center_Y"), "Params Center"
    if not cx or not cy:
        w = w2t_center(read_text(_find_up(folder, "Wafer2Table.ini") or folder / "-"))
        cx, cy, csrc = (w[0], w[1], "Wafer2Table") if w else (None, None, "없음")
    r.update(px=px, py=py, pitch_src=psrc, dia=dia, cx=cx, cy=cy, center_src=csrc)
    r["entries"] = len(entries)
    # 장비 설정 가운데 방향 · 형태에 걸리는 값 — 지금까지 본 것과 다른 제품이 있는지 보려고 적어 둔다.
    recipe = read_text(folder / "Recipe.ini")
    r["env"] = {k: key_num(t, k) for t, k in ((recipe, "WaferDisplayOrientation"), (prod, "NotchFlat"),
                                              (prod, "Flat Pos"), (prod, "IsPanel"), (prod, "IsMultiIndex"))}
    # die 번호와 die 안 좌표 — 큰 칸 안 die 는 균등하지 않아 [DieRectInfo] 표로 찾는다(AMD Sepio: 균등은 45/357 틀림).
    rects = die_rects(prod)
    uniform = [(math.floor(X / px), math.floor(Y / py), X % px, Y % py) for _, X, Y, *_ in entries]
    inner, alt = uniform, None
    if "block" in r and all((a, b) in rects for a in range(bc) for b in range(br)):
        loc = rect_locator(rects, bc, br)
        inner = []
        for _, X, Y, *_ in entries:
            a, b, x, y = loc(X % step_x, Y % step_y)
            inner.append((math.floor(X / step_x) * bc + a, math.floor(Y / step_y) * br + b, x, y))
        r["sub_rule"] = "칸 표"
    elif "block" in r:
        r["sub_rule"] = "균등(칸 표 없음)"
    elif (0, 0) in rects and rects[(0, 0)][2:] != (0.0, 0.0):
        r["rect0"] = rects[(0, 0)][2:]                       # 칸 안 die 가 하나여도 시작점이 적힌 제품
        alt = [(i, j, x - r["rect0"][0], y - r["rect0"][1]) for i, j, x, y in uniform]
    if entries:                                             # A1
        ydiff = defaultdict(set)
        for (i, j, *_), (_, _X, _Y, _c, row, rec) in zip(inner, entries):
            ydiff[rec].add(j - row)
        r["A1"] = {"n": len(entries), "x_bad": sum(1 for (i, *_), e in zip(inner, entries) if i != e[3]),
                   "y_diff": {str(k): sorted(v) for k, v in ydiff.items()}}
        if inner is not uniform:
            r["A1"]["uniform_bad"] = sum(1 for (i, j, *_), e in zip(uniform, entries) if (i, j) != (e[3], e[4]))

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

    shift, where = recipe_shift(folder)
    r["shift"] = shift or (0, 0)
    r["shift_known"] = shift is not None
    r["shift_where"] = where                                 # 폴더(앱이 읽는 곳) · 상위(앱이 못 읽음)

    w = r["wafer"]                                           # 정답: LIVE 표결
    lives = [p for d, lv in live_by_dir.items() for n, p in lv
             if d == folder or w in Path(n).stem.split("_")]
    r["live_n"] = len(lives)
    origin = hyp.get("EXP") or hyp.get("LUT") or hyp.get("MAP")
    cam = lut or dm                                           # Camtek die 목록을 화면 번호로 — Conder 맵과 대조용
    _LAST.update(folder=str(folder), lives=[(q[0], q[1]) for q in lives],
                 dies={(i - origin[0], origin[1] - j) for i, j in cam} if cam and origin else None)
    if entries:                                              # Conder 사진 폴더마다 장비 결함과 한 장씩 대조
        o = hyp.get("EXP") or hyp.get("LUT")
        scan_tag = same_scan_prefix(folder)                  # 다른 공정 · 다른 레시피 사진은 결함이 달라 견줄 수 없다
        mine = [(d, [(n, q) for n, q in lv if w in Path(n).stem.split("_")])
                for d, lv in live_by_dir.items() if d != folder]
        xy = [dict(conder_xy_check(inner, o, named, px, py, 45.0 if "block" in r else 2.5), dir=str(d))
              for d, named in ((d, [t for t in named if t[0].startswith(scan_tag)]) for d, named in mine) if named]
        if xy:
            r["conder_xy"] = xy
        other = sum(1 for _d, named in mine for t in named if not t[0].startswith(scan_tag))
        if other:
            r["conder_other_scan"] = other
    if lives and entries:
        v = live_vote(entries, lives, px, py, inner=inner)
        if alt is not None:                                  # 시작점을 빼야 맞는가(가설) — 두 쪽 표 수를 다 적는다
            va = live_vote(entries, lives, px, py, inner=alt)
            r["live_x0"] = {"plain": v["votes"] if v else 0, "minus_x0": va["votes"] if va else 0}
            if va and (not v or va["votes"] > v["votes"]):
                v = va
        if v is None and "block" in r and inner is uniform:
            # 칸 안 die 제품은 LIVE 의 die 안쪽 좌표가 격자와 수십 µm 어긋난다(관측 3건: 24~38 µm) → 느슨하게 한 번 더.
            v = live_vote(entries, lives, px, py, tol=45.0)
            if v:
                v["loose"] = True
                v["ok"] = v["votes"] >= 3 and v["votes"] == v["total"]
        if v:
            # LIVE 이름이 화면 시프트를 더한 번호인지는 레시피마다 달랐다(관측): T254 FS UBM 은 더했고
            # KENDALL A0 PI3 는 번호표 번호 그대로였다.  번호표가 있으면 그것으로 가리고, 없으면 예전처럼
            # 되돌리되 '미구분' 으로 적는다.
            raw = v["value"]
            back = (raw[0] + r["shift"][0], raw[1] - r["shift"][1])
            exp = hyp.get("EXP")
            if r["shift"] == (0, 0):
                mode = "없음"
            elif exp == raw:
                mode = "안 더함"
            elif exp == back:
                mode = "더함"
            else:
                mode = "미구분"
            v.update(raw=raw, value=raw if mode == "안 더함" else back, shift_mode=mode)
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


def report(recs, klas, pairs, out_dir: Path, no_map=None, conder_dirs=None):
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
    blk = Counter(f"{r['block'][0]}×{r['block'][1]}" for r in geo if "block" in r)
    L.append(f"     칸 안 die 로 pitch 를 나눈 폴더 {sum(blk.values())}"
             + (" (" + " · ".join(f"{k}: {n}" for k, n in blk.most_common()) + ")" if blk else "")
             + f" · 뺀 정렬 사진 {sum(r.get('skipped_align', 0) for r in geo)}장")
    tab = [r["A1"] for r in geo if r.get("sub_rule") == "칸 표" and "A1" in r]
    if tab:
        L.append(f"     칸 표로 계산한 결함 {sum(a['n'] for a in tab)} 중 어긋남 {sum(a['x_bad'] for a in tab)} "
                 f"(균등 나눔이면 {sum(a['uniform_bad'] for a in tab)}) · 칸 표가 없는 칸 안 die 폴더 "
                 f"{sum(1 for r in geo if str(r.get('sub_rule', '')).startswith('균등'))}")
    x0 = [r["live_x0"] for r in geo if "live_x0" in r]
    if x0:
        L.append(f"[die 시작점] 칸 안 die 가 하나인데 시작점이 적힌 폴더 {sum(1 for r in geo if r.get('rect0'))} · "
                 f"LIVE 표: 그대로 {sum(v['plain'] for v in x0)} / 시작점을 빼면 {sum(v['minus_x0'] for v in x0)}")
    kd = defaultdict(lambda: [0, 0])
    for r in geo:
        for k, (n, b) in r.get("kinds", {}).items():
            kd[k][0] += n
            kd[k][1] += b
    if kd:
        L.append("[사진 종류] 장수(스캔 칸 기준 Col 어긋남) — " + " · ".join(f"{k}: {n}({b})" for k, (n, b) in sorted(kd.items())))
    env = Counter(f"{k}={v:g}" for r in geo for k, v in r.get("env", {}).items() if v is not None)
    if env:
        L.append("[장비 설정] " + " · ".join(f"{k}: {n}" for k, n in sorted(env.items())))
    sm = Counter(r["truth_live"]["shift_mode"] for r in geo if r.get("truth_live", {}).get("shift_mode", "없음") != "없음")
    if sm:
        L.append("[시프트] 화면 시프트가 있는 레시피의 LIVE 이름 — " + " · ".join(f"{k} {n}장" for k, n in sm.most_common()))
    cm = [m for r in geo for m in r.get("conder_map", [])]
    if cm:
        same = sum(1 for m in cm if "exp_cols" in m and (m["COLCT"], m["ROWCT"]) == (m["exp_cols"], m["exp_rows"]))
        have = sum(1 for m in cm if "exp_cols" in m)
        pr = sorted(m["pitch_ratio"][0] for m in cm if "pitch_ratio" in m)
        L.append(f"[Conder 맵] txt {len(cm)}개 · 칸 수 = 번호표 범위 {same}/{have} · FNLOC≠180 "
                 f"{sum(1 for m in cm if m['FNLOC'] not in (None, 180.0))} · 불량 칸 표시 없음 "
                 f"{sum(1 for m in cm if not m['bad'])} · die 크기 없음 {sum(1 for m in cm if not m['XDIES'])}"
                 + (f" · XDIES÷pitch {pr[0]:.3f}~{pr[-1]:.3f}" if pr else ""))
        ori = [m["orient"] for m in cm if "orient" in m]
        if ori:
            L.append(f"     방향: Camtek die 목록과 가장 잘 겹치는 쪽이 '그대로' {sum(1 for o in ori if o['그대로'] == max(o.values()))}"
                     f"/{len(ori)} · 완전히 같은 맵 {sum(1 for m in cm if 'orient' in m and m['orient']['그대로'] == m['cam_dies'] == m['dies'])}")
        ld = [m for m in cm if "live_die" in m]
        if ld:
            L.append(f"     LIVE 사진 번호가 die 칸에 {sum(m['live_die'][0] for m in ld)}/{sum(m['live_die'][1] for m in ld)}"
                     f" · 불량 표시가 있는 맵에서 불량 칸에 {sum(m['live_bad'][0] for m in ld if m['bad'])}"
                     f"/{sum(m['live_bad'][1] for m in ld if m['bad'])}")
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
    xy = [c for r in recs for c in r.get("conder_xy", [])]
    if xy:
        mv = Counter()
        for c in xy:
            mv.update(c["moved"])
        tot = sum(c["n"] for c in xy)
        L.append("")
        L.append(f"[Conder 좌표 대 장비 좌표] 사진 폴더 {len(xy)} · 사진 {tot} · 번호와 die 안 좌표 모두 같음 "
                 f"{sum(c['same'] for c in xy)} · 번호만 다름 {sum(mv.values())} · die 안 좌표만 다름 "
                 f"{sum(c['inner'] for c in xy)} · 짝 없음 {sum(c['none'] for c in xy)} · 번호표 없어 좌표만 맞춤 "
                 f"{sum(c['xy_only'] for c in xy)} · 다른 스캔 사진이라 견주지 않음 "
                 f"{sum(r.get('conder_other_scan', 0) for r in recs)}")
        L.append(f"     번호가 어긋난 칸 수(col,row): {dict(mv.most_common(8)) or '없음'} · 좌우로 뒤집어야 맞음 "
                 f"{sum(c['flip_x'] for c in xy)} · 상하로 뒤집어야 맞음 {sum(c['flip_y'] for c in xy)}")
    if conder_dirs:
        wm = [c for c in conder_dirs if c["map"]]
        n_in, n_all = sum(c["in_die"] for c in wm), sum(c["n"] for c in wm)
        marked = [c for c in wm if c["bad"]]
        L.append("")
        L.append(f"[Conder 폴더별] 사진 폴더 {len(conder_dirs)} · 옆에 맵 txt 있음 {len(wm)} · "
                 f"레시피가 2종 이상 섞인 폴더 {sum(1 for c in conder_dirs if len(c['recipes']) > 1)}")
        L.append(f"     사진 번호가 그 폴더 맵의 die 칸 안 {n_in}/{n_all} · 불량 칸 "
                 f"{sum(c['on_bad'] for c in marked)}/{sum(c['n'] for c in marked)}(불량 표시 있는 맵 {len(marked)})")
        L.append(f"     한 칸 옮겨야 불량 칸이 더 맞는 폴더 {sum(1 for c in marked if c['shift'] != [0, 0])} · "
                 f"die 안 좌표가 맵의 die 크기보다 큰 사진 {sum(c.get('spill', 0) for c in wm)}장"
                 f"(폴더 {sum(1 for c in wm if c.get('spill'))}) · 칸 수×크기가 270~330 mm 인 맵 "
                 f"{sum(1 for c in wm if 'span' in c and all(270 <= v <= 330 for v in c['span']))}/"
                 f"{sum(1 for c in wm if 'span' in c)}")

    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"좌표실험_{stamp}"
    text = "\n".join(L)
    base.with_suffix(".txt").write_text(text, encoding="utf-8")
    for k in klas:
        k.pop("_def", None)
    base.with_suffix(".json").write_text(json.dumps(
        {"camtek": recs, "kla": klas, "pairs": pairs, "score": sc, "discrim": dc, "invariant": inv,
         "conder_dirs": conder_dirs or []},
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
    if "--slim" in argv:                   # 이미 모은 zip 을 줄여 묶는다: --slim <폴더>
        made, a, b = slim_zips(Path(argv[argv.index("--slim") + 1]))
        print(f"{a / 2 ** 20:.0f} MB → {b / 2 ** 20:.1f} MB", *[f"\n  {p}" for p in made])
        return made
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
