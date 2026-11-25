"""'어느 Zone에서 왔니?' 사진 목록 썸네일이 왜 느린지 재는 측정 도구(읽기 전용, 이 파일 하나로 돈다).

현택님 사용법: VS Code 에서 이 파일을 열고 F5 → 사진이 든 폴더(LOT 이나 웨이퍼 폴더)를 고른다 →
5분쯤 기다린다 → 바탕 화면에 생긴 ``속도측정_썸네일_날짜_시각.zip`` 을 올린다.  고칠 곳은 없다.

* 고른 폴더에는 **아무것도 쓰지 않는다**(읽기만).  임시 파일은 PC 임시 폴더에 만들고 끝나면 지운다.
* zip 에는 **사진이 들어가지 않는다** — 파일 이름 · 크기 · 가로세로 · 걸린 시간만 담는다.
* 표준 파이썬만 쓴다(앱 코드 · 다른 dev 스크립트를 불러오지 않는다).  Pillow 가 있으면 디코드 · 앱 흉내
  측정까지 하고, 없으면 그 부분만 건너뛰고 요약에 적는다.
* 한 번 읽은 파일은 윈도우가 기억해 두 번째부터 빠르다.  그래서 '처음 읽기' 측정마다 **서로 다른 사진**을
  쓰고, 같은 사진을 다시 읽는 측정(T4)을 따로 둬 그 효과를 숫자로 남긴다.

머리 없이 돌리기(개발자용): ``python dev/measure_thumb_speed.py <폴더> [<폴더> …]``
"""

from __future__ import annotations

import csv
import io
import json
import os
import platform
import random
import shutil
import statistics
import struct
import sys
import tempfile
import threading
import time
import traceback
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

try:                                        # Pillow 는 선택 — 없으면 디코드 측정만 건너뛴다
    import PIL
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None           # 앱과 같게(큰 AOI 사진 허용)
    PIL_VERSION = PIL.__version__
except Exception:                           # noqa: BLE001 — 어떤 이유든 '없음' 으로 본다
    Image = None
    PIL_VERSION = None

# ── 앱과 맞춘 값(aoi_verification/app/config.py · image_io.py · zone_origin_dialog.py) ──────────
IMAGE_EXT = (".jpeg", ".jpg", ".png", ".bmp")
OVERVIEW_PREFIXES = ("cognexinsight",)      # 웨이퍼 전경 사진(앱이 목록에서 뺀다)
THUMB_PX, THUMB_Q = 120, 60                 # 목록 타일
MID_PX, MID_Q = 800, 88                     # 오른쪽 큰 사진
APP_WORKERS, PAGE_TILES = 3, 60
CACHE_DIRNAME = ".aoi_verification_cache"

# ── 측정 한도(전체 5분 안쪽) ────────────────────────────────────────────────────────────────────
TOTAL_BUDGET_S = 280
LIST_CAP_S, LIST_MAX_FILES, LIST_MAX_DEPTH = 30, 30000, 4
RAW_WORKERS = (1, 3, 6, 8, 12, 16)
PIPE_WORKERS = (3, 6, 8, 12, 16)
STEP_CAP_S = 10                             # 일꾼 수 하나당 최대 시간
MAX_READ_BYTES = 64 * 1024 * 1024           # 이보다 큰 파일은 읽지 않는다
CPU_SAMPLES = 12
CSV_FILE_ROWS = 30000

WANT = [("stat", 60), ("cold", 40)]
WANT += [(f"raw{w}", 48) for w in RAW_WORKERS]
WANT += [(f"pipe{w}", 40) for w in PIPE_WORKERS]
WANT += [("pipe_bytes3", 40), ("first60_w3", PAGE_TILES), ("first60_w8", PAGE_TILES)]

T0 = time.perf_counter()
END = T0 + TOTAL_BUDGET_S
timings: list[dict] = []                    # 파일별 측정 → timings.csv
errors: list[str] = []
_lock = threading.Lock()


def say(msg: str) -> None:
    print(f"[{time.perf_counter() - T0:5.0f}초] {msg}", flush=True)


def ms(sec: float) -> float:
    return round(sec * 1000, 2)


def stats(values) -> dict:
    v = sorted(x for x in values if x is not None)
    if not v:
        return {"n": 0}
    pick = lambda q: v[min(len(v) - 1, int(q * len(v)))]            # noqa: E731
    return {"n": len(v), "min": round(v[0], 2), "median": round(statistics.median(v), 2),
            "mean": round(statistics.fmean(v), 2), "p90": round(pick(0.9), 2), "max": round(v[-1], 2)}


def note_error(where: str, exc: BaseException) -> None:
    with _lock:
        errors.append(f"{where}: {type(exc).__name__}: {exc}")


def add_row(**row) -> None:
    with _lock:
        timings.append(row)


# ── 파일 이름 분류(앱의 slot.is_ignored_name 과 같은 규칙) ──────────────────────────────────────
def kind_of(name: str) -> str:
    low = name.lower()
    if not low.endswith(IMAGE_EXT):
        return "other"
    if low.startswith(OVERVIEW_PREFIXES):
        return "overview"
    if "t" in name.rsplit(".", 1)[0].split("."):
        return "scan"                       # `.t.` Scan 사진 — 앱 목록에는 안 나온다
    return "color"


# ── 헤더만으로 가로세로 읽기(Pillow 없이) ───────────────────────────────────────────────────────
def image_dims(data: bytes):
    """(가로, 세로, 종류) — 종류는 'jpeg-baseline' · 'jpeg-progressive' · 'png' · 'bmp'.  모르면 None."""
    try:
        if data[:2] == b"\xff\xd8":
            i, n = 2, len(data)
            while i + 9 < n:
                if data[i] != 0xFF:
                    i += 1
                    continue
                m = data[i + 1]
                if m == 0xFF:                               # 채움 바이트
                    i += 1
                    continue
                if m in (0x01, 0xD8) or 0xD0 <= m <= 0xD7:  # 길이 없는 마커
                    i += 2
                    continue
                if m in (0xD9, 0xDA):
                    break
                seg = struct.unpack(">H", data[i + 2:i + 4])[0]
                if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h, "jpeg-progressive" if m in (0xC2, 0xC6, 0xCA, 0xCE) else "jpeg-baseline"
                i += 2 + seg
        elif data[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", data[16:24])
            return w, h, "png"
        elif data[:2] == b"BM":
            w, h = struct.unpack("<ii", data[18:26])
            return w, abs(h), "bmp"
    except Exception:                                       # noqa: BLE001 — 깨진 헤더
        pass
    return None


# ── 환경 ────────────────────────────────────────────────────────────────────────────────────────
def path_type(p: str) -> dict:
    info = {"path": p, "type": "local"}
    if p.startswith("\\\\") or p.startswith("//"):
        info["type"] = "UNC"
    elif os.name == "nt" and len(p) >= 2 and p[1] == ":":
        try:
            import ctypes
            if ctypes.windll.kernel32.GetDriveTypeW(p[:2] + "\\") == 4:      # DRIVE_REMOTE
                info["type"] = "mapped"
                buf, size = ctypes.create_unicode_buffer(1024), ctypes.c_ulong(1024)
                if ctypes.windll.mpr.WNetGetConnectionW(p[:2], buf, ctypes.byref(size)) == 0:
                    info["unc"] = buf.value
        except Exception as exc:                            # noqa: BLE001
            note_error("path_type", exc)
    return info


def environment(folders) -> dict:
    env = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "os": platform.platform(), "python": sys.version.split()[0], "executable": sys.executable,
        "cpu_count": os.cpu_count(), "machine": platform.machine(),
        "pillow": PIL_VERSION, "folders": [path_type(f) for f in folders],
        "AOI_DATA_HOME": os.environ.get("AOI_DATA_HOME"), "tempdir": tempfile.gettempdir(),
    }
    if Image is not None:
        try:
            from PIL import features
            env["libjpeg_turbo"] = bool(features.check_feature("libjpeg_turbo"))
            env["libjpeg"] = features.version("jpg")
        except Exception as exc:                            # noqa: BLE001
            note_error("pillow features", exc)
    return env


# ── T1 폴더 훑기 ────────────────────────────────────────────────────────────────────────────────
def list_folders(folders):
    """(파일 목록, 폴더별 기록, 요약).  파일 = dict(path, name, kind, size, dir).  읽기만 한다."""
    files, dir_rows = [], []
    deadline = min(END, time.perf_counter() + LIST_CAP_S)
    queue = [(os.path.abspath(f), 0) for f in folders]
    seen, truncated = set(), ""
    while queue:
        if time.perf_counter() > deadline:
            truncated = f"시간 한도 {LIST_CAP_S}초"
            break
        if len(files) >= LIST_MAX_FILES:
            truncated = f"파일 한도 {LIST_MAX_FILES}개"
            break
        d, depth = queue.pop(0)
        if d in seen:
            continue
        seen.add(d)
        t = time.perf_counter()
        n_entries, counts, subdirs = 0, {}, []
        try:
            with os.scandir(d) as it:
                for e in it:
                    n_entries += 1
                    try:
                        if e.is_dir():
                            subdirs.append(e.path)
                        elif e.is_file():
                            k = kind_of(e.name)
                            counts[k] = counts.get(k, 0) + 1
                            if k != "other":
                                # 윈도우는 목록에 크기가 딸려 와 추가 왕복이 없다
                                files.append({"path": e.path, "name": e.name, "kind": k,
                                              "size": e.stat().st_size, "dir": len(dir_rows)})
                    except OSError as exc:
                        note_error(f"list {e.name}", exc)
        except OSError as exc:
            note_error(f"scandir {d}", exc)
        took = time.perf_counter() - t
        dir_rows.append({"dir_id": len(dir_rows), "dir": d, "depth": depth, "entries": n_entries,
                         "scandir_ms": ms(took), **{k: counts.get(k, 0) for k in
                                                    ("color", "scan", "overview", "other")}})
        if len(dir_rows) % 20 == 0:
            say(f"  폴더 {len(dir_rows)}개 훑음, 사진 {len(files)}장")
        if depth < LIST_MAX_DEPTH:
            queue.extend((s, depth + 1) for s in sorted(subdirs))
    total_ms = sum(r["scandir_ms"] for r in dir_rows)
    n_ent = sum(r["entries"] for r in dir_rows)
    summary = {"dirs": len(dir_rows), "entries": n_ent, "total_ms": round(total_ms, 1),
               "ms_per_1000_entries": round(total_ms / n_ent * 1000, 1) if n_ent else None,
               "truncated": truncated, "slowest_dir_ms": max((r["scandir_ms"] for r in dir_rows), default=0)}
    for k in ("color", "scan", "overview"):
        sizes = [f["size"] for f in files if f["kind"] == k]
        summary[k] = {"count": len(sizes), "total_mb": round(sum(sizes) / 1e6, 1),
                      "size_kb": stats([s / 1024 for s in sizes])}
    return files, dir_rows, summary


def make_plan(color):
    """측정마다 서로 다른 사진 묶음을 나눈다.  모자라면 같은 비율로 줄인다."""
    seed = int(time.time())                 # 돌릴 때마다 다른 사진 — 두 번째 실행이 '이미 읽은 사진' 이 되지 않게
    rnd = random.Random(seed)
    pool = [f for f in color if 0 < f["size"] <= MAX_READ_BYTES]
    rnd.shuffle(pool)
    want = [w for w in WANT if Image is not None or not w[0].startswith(("pipe", "first60"))]
    need = sum(n for _, n in want)
    scale = min(1.0, len(pool) / need) if need else 0
    plan, at = {}, 0
    for name, n in want:
        k = n if scale >= 1 else max(1, int(n * scale))
        plan[name] = pool[at:at + k]
        at += k
    info = {"seed": seed, "usable_photos": len(pool), "wanted": need, "scale": round(scale, 3),
            "set_sizes": {k: len(v) for k, v in plan.items()}}
    return plan, info


# ── 읽기 측정 조각 ──────────────────────────────────────────────────────────────────────────────
def read_file(f, test, workers=1, keep=None, t_start=None):
    """한 파일을 통째로 읽고 구간별 시간을 남긴다(열기 · 첫 64KB · 나머지)."""
    row = {"test": test, "workers": workers, "name": f["name"], "size": f["size"]}
    try:
        t0 = time.perf_counter()
        with open(f["path"], "rb", buffering=0) as fh:
            t1 = time.perf_counter()
            head = fh.read(65536)
            t2 = time.perf_counter()
            rest = fh.read()
        t3 = time.perf_counter()
        data = head + (rest or b"")
        dims = image_dims(data)
        row.update(open_ms=ms(t1 - t0), first_ms=ms(t2 - t1), read_ms=ms(t3 - t2), total_ms=ms(t3 - t0),
                   bytes=len(data), width=dims[0] if dims else "", height=dims[1] if dims else "",
                   format=dims[2] if dims else "")
        if keep is not None and len(keep) < CPU_SAMPLES and sum(len(b) for _, b in keep) < 150e6:
            keep.append((f, data))
    except Exception as exc:                                # noqa: BLE001 — 한 장 실패로 멈추지 않는다
        row["error"] = f"{type(exc).__name__}: {exc}"
    if t_start is not None:
        row["t_done_ms"] = ms(time.perf_counter() - t_start)
    add_row(**row)
    return row


def run_pool(fn, items, workers, cap_s):
    """FIFO 풀(앱과 같은 ThreadPoolExecutor)로 돌린다.  시간 한도를 넘기면 남은 것은 건너뛴다."""
    start = time.perf_counter()
    deadline = min(END, start + cap_s)

    def job(item):
        if time.perf_counter() > deadline:
            return None
        return fn(item, start)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = [r for r in pool.map(job, items) if r is not None]
    wall = time.perf_counter() - start
    ok = [r for r in rows if not r.get("error")]
    nbytes = sum(r.get("bytes", r.get("size", 0)) for r in ok)
    return {"workers": workers, "planned": len(items), "done": len(ok), "errors": len(rows) - len(ok),
            "skipped_by_time_cap": len(items) - len(rows), "wall_s": round(wall, 3),
            "files_per_s": round(len(ok) / wall, 2) if wall > 0 else None,
            "mb_per_s": round(nbytes / 1e6 / wall, 2) if wall > 0 else None,
            "mean_size_kb": round(nbytes / 1024 / len(ok), 1) if ok else None,
            "per_file_ms": stats([r.get("total_ms") for r in ok])}, rows


# ── Pillow 조각(앱의 image_io._ensure_resized 흉내) ─────────────────────────────────────────────
def _fit(img, long_edge):
    w, h = img.size
    if max(w, h) <= long_edge:
        return img.copy()
    if w >= h:
        size = (long_edge, int(round(h * long_edge / w)))
    else:
        size = (int(round(w * long_edge / h)), long_edge)
    return img.resize(size, Image.LANCZOS)


def _to_rgb(img):
    return img if img.mode == "RGB" else img.convert("RGB")


def pipeline(f, test, workers, tmpdir, t_start, long_edge=THUMB_PX, q=THUMB_Q, bytes_first=False):
    """stat → 열기+디코드(draft) → 줄이기 → JPEG 저장 → 이름 바꾸기.  bytes_first 면 통째로 읽은 뒤 디코드."""
    row = {"test": test, "workers": workers, "name": f["name"], "size": f["size"]}
    try:
        t0 = time.perf_counter()
        os.stat(f["path"])                                  # 앱의 cache.memo_mtime
        t1 = time.perf_counter()
        if bytes_first:
            with open(f["path"], "rb", buffering=0) as fh:
                src = io.BytesIO(fh.read())
        else:
            src = f["path"]
        img = Image.open(src)
        try:
            img.draft("RGB", (long_edge, long_edge))
        except Exception:                                   # noqa: BLE001 — 앱과 같다
            pass
        img.load()
        t2 = time.perf_counter()
        out_img = _to_rgb(_fit(img, long_edge))
        t3 = time.perf_counter()
        out = os.path.join(tmpdir, f"{test}_{threading.get_ident()}_{time.perf_counter_ns()}.jpg")
        out_img.save(out + ".part.jpg", format="JPEG", quality=q, optimize=True)
        os.replace(out + ".part.jpg", out)
        t4 = time.perf_counter()
        row.update(stat_ms=ms(t1 - t0), decode_ms=ms(t2 - t1), resize_ms=ms(t3 - t2), save_ms=ms(t4 - t3),
                   total_ms=ms(t4 - t0), width=img.size[0], height=img.size[1],
                   out_bytes=os.path.getsize(out))
    except Exception as exc:                                # noqa: BLE001
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["t_done_ms"] = ms(time.perf_counter() - t_start)
    add_row(**row)
    return row


def cpu_tests(kept):
    """NAS 를 빼고(메모리에 든 바이트로) 디코드 · 줄이기 · 저장만 잰다."""
    acc: dict[str, list] = {}

    def put(key, t):
        acc.setdefault(key, []).append(ms(t))

    for f, data in kept:
        if time.perf_counter() > END:
            break
        try:
            row = {"test": "cpu", "workers": 1, "name": f["name"], "size": f["size"]}
            for label, edge, q in (("thumb120", THUMB_PX, THUMB_Q), ("mid800", MID_PX, MID_Q)):
                t0 = time.perf_counter()
                img = Image.open(io.BytesIO(data))
                img.draft("RGB", (edge, edge))
                img.load()
                t1 = time.perf_counter()
                small = _to_rgb(_fit(img, edge))
                t2 = time.perf_counter()
                buf = io.BytesIO()
                small.save(buf, format="JPEG", quality=q, optimize=True)
                t3 = time.perf_counter()
                buf2 = io.BytesIO()
                small.save(buf2, format="JPEG", quality=q)
                t4 = time.perf_counter()
                put(f"{label}_decode_draft", t1 - t0)
                put(f"{label}_resize", t2 - t1)
                put(f"{label}_save_optimize", t3 - t2)
                put(f"{label}_save_plain", t4 - t3)
                put(f"{label}_total", t3 - t0)
                acc.setdefault(f"{label}_out_kb", []).append(round(buf.tell() / 1024, 2))
                if label == "thumb120":
                    row.update(decode_ms=ms(t1 - t0), resize_ms=ms(t2 - t1), save_ms=ms(t3 - t2),
                               total_ms=ms(t3 - t0), out_bytes=buf.tell(),
                               width=img.size[0], height=img.size[1])
            t0 = time.perf_counter()
            full = Image.open(io.BytesIO(data))
            full.load()
            t1 = time.perf_counter()
            _fit(full, THUMB_PX)
            t2 = time.perf_counter()
            put("full_decode_no_draft", t1 - t0)
            put("full_resize_to_120", t2 - t1)
            row.update(full_decode_ms=ms(t1 - t0))
            add_row(**row)
        except Exception as exc:                            # noqa: BLE001
            add_row(test="cpu", workers=1, name=f["name"], size=f["size"],
                    error=f"{type(exc).__name__}: {exc}")
    return {k: stats(v) for k, v in acc.items()}


def first60(files, workers, tmpdir, detail_in_queue):
    """첫 화면 60칸 흉내: 타일 60개를 FIFO 로 넣고, 오른쪽 큰 사진(800px, 첫 사진을 한 번 더 읽음)이 언제 뜨는지."""
    if not files:
        return {"skipped": "사진 없음"}
    test = f"first60_w{workers}"
    box = {}

    def detail(start):
        r = pipeline(files[0], test + "_detail", workers, tmpdir, start, MID_PX, MID_Q)
        box["detail_ms"] = r.get("t_done_ms")
        box["detail_error"] = r.get("error")

    start = time.perf_counter()
    deadline = min(END, start + STEP_CAP_S + 5)
    side = None
    if not detail_in_queue:                                 # 고침 B 흉내: 큰 사진은 줄 서지 않는다
        side = threading.Thread(target=detail, args=(start,))
        side.start()

    def job(f):
        if time.perf_counter() > deadline:
            return None
        return pipeline(f, test, workers, tmpdir, start)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(job, f) for f in files]
        if detail_in_queue:                                 # 지금 앱: 목록 뒤에 줄 선다
            futs.append(pool.submit(detail, start))
        rows = [x for x in (fu.result() for fu in futs[:len(files)]) if x]
    if side:
        side.join()
    done = sorted(r["t_done_ms"] for r in rows if not r.get("error"))
    return {"workers": workers, "tiles_planned": len(files), "tiles_done": len(done),
            "detail_in_queue": detail_in_queue,
            "first_tile_ms": done[0] if done else None,
            "tile10_ms": done[9] if len(done) >= 10 else None,
            "last_tile_ms": done[-1] if done else None,
            "detail_ms": box.get("detail_ms"), "detail_error": box.get("detail_error")}


# ── 로컬 디스크 · 앱 캐시 ───────────────────────────────────────────────────────────────────────
def local_write(tmpdir):
    payload, times = os.urandom(4096), []
    t_all = time.perf_counter()
    for i in range(200):
        if time.perf_counter() - t_all > 8:
            break
        p = os.path.join(tmpdir, f"w{i}.jpg")
        t = time.perf_counter()
        with open(p + ".part", "wb") as fh:
            fh.write(payload)
        os.replace(p + ".part", p)
        times.append(ms(time.perf_counter() - t))
    return {"file_bytes": 4096, "per_file_ms": stats(times), "tempdir": tmpdir}


def cache_state():
    """앱 캐시 폴더 상태(읽기만, 없으면 만들지 않는다)."""
    roots = []
    if os.environ.get("AOI_DATA_HOME"):
        roots.append(os.path.join(os.environ["AOI_DATA_HOME"], "cache"))
    roots.append(os.path.join(os.path.expanduser("~"), CACHE_DIRNAME))
    now, out = time.time(), []
    edges = (("1시간 안", 3600), ("1일 안", 86400), ("7일 안", 7 * 86400), ("7일 넘음", float("inf")))
    for root in roots:
        info = {"root": root, "exists": os.path.isdir(root), "subdirs": {}}
        out.append(info)
        if not info["exists"]:
            continue
        deadline = min(END, time.perf_counter() + 8)
        try:
            subs = sorted(e.name for e in os.scandir(root) if e.is_dir())
        except OSError as exc:
            note_error(f"cache {root}", exc)
            continue
        for sub in subs:
            d = {"files": 0, "total_mb": 0.0, "age": {k: 0 for k, _ in edges}, "truncated": False}
            info["subdirs"][sub] = d
            total, sample, stack = 0, [], [os.path.join(root, sub)]
            t = time.perf_counter()
            while stack and not d["truncated"]:
                try:
                    with os.scandir(stack.pop()) as it:
                        for e in it:
                            if time.perf_counter() > deadline or d["files"] >= 200000:
                                d["truncated"] = True
                                break
                            if e.is_dir():
                                stack.append(e.path)
                                continue
                            st = e.stat()
                            d["files"] += 1
                            total += st.st_size
                            age = now - st.st_mtime
                            d["age"][next(k for k, lim in edges if age <= lim)] += 1
                            if len(sample) < 60 and st.st_size > 0:
                                sample.append(e.path)
                except OSError as exc:
                    note_error(f"cache {sub}", exc)
            d["total_mb"] = round(total / 1e6, 2)
            d["mean_kb"] = round(total / 1024 / d["files"], 2) if d["files"] else None
            d["scan_ms"] = ms(time.perf_counter() - t)
            if sub in ("thumbs", "mid") and sample:         # 캐시 적중이면 드는 시간
                reads = []
                for p in sample:
                    try:
                        t = time.perf_counter()
                        with open(p, "rb") as fh:
                            fh.read()
                        reads.append(ms(time.perf_counter() - t))
                    except OSError as exc:
                        note_error("cache read", exc)
                d["hit_read_ms"] = stats(reads)
    return out


# ── 요약 ────────────────────────────────────────────────────────────────────────────────────────
def best_workers(sweep):
    """가장 빠른 속도의 90% 를 처음 넘는 일꾼 수(더 늘려도 이득이 작은 지점)."""
    ok = [s for s in sweep if s.get("files_per_s") and s["done"] >= 3]
    if not ok:
        return None
    top = max(s["files_per_s"] for s in ok)
    return next(s["workers"] for s in sorted(ok, key=lambda s: s["workers"]) if s["files_per_s"] >= 0.9 * top)


def med(d, *keys):
    for k in keys:
        d = (d or {}).get(k)
    return d


def write_summary(R) -> str:
    L = ["썸네일 속도 측정 요약", f"측정 시각: {R['env']['when']}  (걸린 시간 {R.get('elapsed_s', '?')}초)", ""]
    add = L.append
    if R.get("interrupted"):
        add("※ 중간에 멈춰서 일부 측정만 들어 있습니다.")
    env = R["env"]
    add(f"[환경] {env['os']} / Python {env['python']} / CPU {env['cpu_count']}개 / "
        f"Pillow {env['pillow'] or '없음'}")
    for f in env["folders"]:
        add(f"  폴더({f['type']}): {f['path']}" + (f"  → {f['unc']}" if f.get("unc") else ""))
    if not env["pillow"]:
        add("  ※ Pillow 가 없어 디코드(T6) · 앱 흉내(T7~T8) 측정은 건너뛰었습니다. 읽기 측정은 정상입니다.")
    ls = R.get("listing")
    if ls:
        add("")
        add(f"[T1 폴더 훑기] 폴더 {ls['dirs']}개, 항목 {ls['entries']}개, {ls['total_ms']} ms "
            f"(1000개당 {ls['ms_per_1000_entries']} ms)" + (f"  ※ {ls['truncated']}에서 멈춤" if ls["truncated"] else ""))
        for k, label in (("color", "Color 사진(목록에 나오는 것)"), ("scan", "Scan 사진(.t.)"), ("overview", "전경 사진")):
            s = ls[k]
            add(f"  {label}: {s['count']}장, 합계 {s['total_mb']} MB, 한 장 중앙값 {s['size_kb'].get('median', '-')} KB"
                f" (최대 {s['size_kb'].get('max', '-')} KB)")
    if R.get("dims"):
        add(f"  가로세로(읽은 사진 기준): {R['dims']['top']}  / 형식: {R['dims']['formats']}")
    pl = R.get("plan")
    if pl and pl["scale"] < 1:
        add(f"  ※ 사진이 {pl['usable_photos']}장뿐이라 측정 묶음을 {pl['scale']:.0%} 로 줄였습니다(필요 {pl['wanted']}장).")
    if R.get("stat"):
        add(f"[T2 파일 정보 1회(stat)] 중앙값 {R['stat'].get('median')} ms, 90% {R['stat'].get('p90')} ms")
    c = R.get("cold")
    if c:
        add(f"[T3 처음 읽기, 한 번에 한 장] 한 장 {med(c, 'total_ms', 'median')} ms "
            f"= 열기 {med(c, 'open_ms', 'median')} + 첫 64KB {med(c, 'first_ms', 'median')} + 나머지 {med(c, 'read_ms', 'median')}"
            f"  / {c.get('mb_per_s')} MB/s")
    w = R.get("warm")
    if w:
        add(f"[T4 같은 사진 다시 읽기] 한 장 {med(w, 'total_ms', 'median')} ms (처음 읽기 ÷ 다시 읽기 = {w.get('speedup', '?')}배)")
    if R.get("raw_sweep"):
        add("[T5 동시에 읽는 수를 바꿔 가며 읽기만] 일꾼 수: 초당 장수 (MB/s)")
        add("  " + " | ".join(f"{s['workers']}: {s['files_per_s']} ({s['mb_per_s']})" for s in R["raw_sweep"]))
    cpu = R.get("cpu")
    if cpu:
        add(f"[T6 PC 계산만(NAS 제외)] 120px 타일 한 장 {med(cpu, 'thumb120_total', 'median')} ms "
            f"= 디코드 {med(cpu, 'thumb120_decode_draft', 'median')} + 줄이기 {med(cpu, 'thumb120_resize', 'median')}"
            f" + 저장 {med(cpu, 'thumb120_save_optimize', 'median')}"
            f" / 800px {med(cpu, 'mid800_total', 'median')} ms / 통째 디코드 {med(cpu, 'full_decode_no_draft', 'median')} ms")
    if R.get("pipe_sweep"):
        add("[T7 앱과 같은 방식(읽기+디코드+저장)] 일꾼 수: 초당 장수 → 60칸 예상")
        add("  " + " | ".join(f"{s['workers']}: {s['files_per_s']} → {round(PAGE_TILES / s['files_per_s'], 1) if s['files_per_s'] else '?'}초"
                              for s in R["pipe_sweep"]))
    pb = R.get("pipe_bytes3")
    if pb:
        add(f"  통째로 읽은 뒤 디코드(일꾼 3): 초당 {pb['files_per_s']}장 (위 3과 비교)")
    for key in ("first60_w3", "first60_w8"):
        s = R.get(key)
        if s and "skipped" not in s:
            how = "목록 뒤에 줄 섬(지금 앱)" if s["detail_in_queue"] else "줄 서지 않음"
            add(f"[T8 첫 화면 흉내, 일꾼 {s['workers']}] 첫 타일 {s['first_tile_ms']} ms, "
                f"{s['tiles_done']}칸 다 뜸 {s['last_tile_ms']} ms, 오른쪽 큰 사진 {s['detail_ms']} ms ({how})")
    lw = R.get("local_write")
    if lw:
        add(f"[T9 PC 디스크에 작은 파일 쓰기] 한 장 {med(lw, 'per_file_ms', 'median')} ms (90% {med(lw, 'per_file_ms', 'p90')} ms)")
    for cst in R.get("cache", []):
        if not cst["exists"]:
            add(f"[T10 앱 캐시] {cst['root']} — 없음")
            continue
        add(f"[T10 앱 캐시] {cst['root']}")
        for sub, d in cst["subdirs"].items():
            add(f"  {sub}: {d['files']}개, {d['total_mb']} MB, 나이 {d['age']}"
                + (f", 캐시에서 읽기 {d['hit_read_ms'].get('median')} ms" if d.get("hit_read_ms") else ""))

    add("")
    add("[판단]")
    nas = med(c, "total_ms", "median")
    cpu_ms = med(cpu, "thumb120_total", "median")
    if nas is not None and cpu_ms is not None:
        share = nas / (nas + cpu_ms)
        if nas > 2 * cpu_ms:
            add(f"- 가장 큰 원인: NAS 에서 사진을 읽어 오는 시간 (한 장 {nas} ms 중 PC 계산은 {cpu_ms} ms, NAS 비중 {share:.0%})")
        elif cpu_ms > 2 * nas:
            add(f"- 가장 큰 원인: PC 계산(디코드 · 저장) (한 장 계산 {cpu_ms} ms, NAS 읽기 {nas} ms)")
        else:
            add(f"- NAS 읽기({nas} ms)와 PC 계산({cpu_ms} ms)이 비슷하게 든다")
    elif nas is not None:
        add(f"- NAS 에서 한 장 읽기 {nas} ms (Pillow 가 없어 PC 계산과 비교하지 못함)")
    if c and nas and nas >= 2:
        fixed = (med(c, "open_ms", "median") or 0) + (med(c, "first_ms", "median") or 0)
        if fixed >= 0.5 * nas:
            add(f"- NAS 시간의 대부분이 '왕복 대기'({fixed:.0f} ms)다 → 동시에 읽는 수를 늘리면 잘 듣는다")
        else:
            add(f"- NAS 시간의 대부분이 '내려받는 양'이다(왕복 대기는 {fixed:.0f} ms) → 일꾼을 늘려도 회선 속도에서 막힐 수 있다")
    rec_raw, rec_pipe = best_workers(R.get("raw_sweep", [])), best_workers(R.get("pipe_sweep", []))
    R["recommended_workers"] = {"raw": rec_raw, "pipeline": rec_pipe}
    if rec_pipe or rec_raw:
        add(f"- 권장 일꾼 수: {rec_pipe or rec_raw}  (앱 방식 기준 {rec_pipe or '측정 없음'}, 읽기만 기준 {rec_raw or '측정 없음'}; 지금 앱은 {APP_WORKERS})")
    if w and w.get("speedup"):
        if w["speedup"] >= 1.5:
            add(f"- 다시 읽으면 {w['speedup']}배 빠르다(윈도우가 기억한 덕) → 처음 읽기 숫자가 실제 첫 화면 속도다")
        else:
            add("- 다시 읽어도 빨라지지 않았다 → 이 사진들을 방금 이미 읽었거나(앱에서 본 직후) NAS 가 아니다. "
                "'처음 읽기' 숫자가 실제보다 좋게 나왔을 수 있다")
    a, b = R.get("first60_w3") or {}, R.get("first60_w8") or {}
    if a.get("detail_ms") and b.get("detail_ms"):
        add(f"- 오른쪽 큰 사진: 줄 서면 {a['detail_ms']} ms, 줄 안 서면 {b['detail_ms']} ms")
    if errors:
        add("")
        add(f"[오류 {len(errors)}건] (측정은 계속됨, 처음 10건)")
        L.extend("  " + e for e in errors[:10])
    add("")
    add("※ 이 zip 에는 사진이 들어 있지 않습니다(이름 · 크기 · 시간만).")
    return "\n".join(L) + "\n"


# ── 본체 ────────────────────────────────────────────────────────────────────────────────────────
def measure(folders, R, tmpdir, out_csv):
    R["env"] = environment(folders)
    say("T1 폴더를 훑는 중…")
    files, dir_rows, R["listing"] = list_folders(folders)
    out_csv["folders"] = dir_rows
    out_csv["files"] = files
    color = [f for f in files if f["kind"] == "color"]
    say(f"  Color 사진 {len(color)}장, Scan(.t.) {R['listing']['scan']['count']}장")
    if not color:
        R["fatal"] = "고른 폴더에 Color 사진이 없습니다."
        return
    plan, R["plan"] = make_plan(color)

    say("T2 파일 정보(stat) 속도…")
    st = []
    for f in plan["stat"]:
        try:
            t = time.perf_counter()
            os.stat(f["path"])
            st.append(ms(time.perf_counter() - t))
            add_row(test="stat", workers=1, name=f["name"], size=f["size"], stat_ms=st[-1])
        except OSError as exc:
            add_row(test="stat", workers=1, name=f["name"], size=f["size"], error=str(exc))
    R["stat"] = stats(st)

    say("T3 처음 읽기(한 장씩)…")
    kept: list = []

    def fold(rows, wall):
        ok = [r for r in rows if not r.get("error")]
        d = {k: stats([r[k] for r in ok]) for k in ("open_ms", "first_ms", "read_ms", "total_ms")}
        nb = sum(r["bytes"] for r in ok)
        d.update(files=len(ok), errors=len(rows) - len(ok), wall_s=round(wall, 3),
                 mb_per_s=round(nb / 1e6 / wall, 2) if wall > 0 else None,
                 mean_size_kb=round(nb / 1024 / len(ok), 1) if ok else None)
        return d

    t, rows, read_set = time.perf_counter(), [], []
    cap = min(END, t + 25)
    for f in plan["cold"]:
        if time.perf_counter() > cap:
            break
        rows.append(read_file(f, "cold", keep=kept))
        read_set.append(f)
    R["cold"] = fold(rows, time.perf_counter() - t)

    say("T4 같은 사진 다시 읽기…")
    t = time.perf_counter()
    rows = [read_file(f, "warm") for f in read_set]
    R["warm"] = fold(rows, time.perf_counter() - t)
    c_med, w_med = med(R["cold"], "total_ms", "median"), med(R["warm"], "total_ms", "median")
    if c_med and w_med:
        R["warm"]["speedup"] = round(c_med / w_med, 1)

    R["raw_sweep"] = []
    for w in RAW_WORKERS:
        say(f"T5 읽기만, 일꾼 {w}개…")
        s, _ = run_pool(lambda f, start, w=w: read_file(f, f"raw{w}", w, t_start=start),
                        plan[f"raw{w}"], w, STEP_CAP_S)
        R["raw_sweep"].append(s)

    seen = [r for r in timings if r.get("width")]
    if seen:
        from collections import Counter
        dims = Counter(f"{r['width']}x{r['height']}" for r in seen)
        R["dims"] = {"top": ", ".join(f"{k} ({n}장)" for k, n in dims.most_common(4)),
                     "all": dict(dims), "formats": dict(Counter(r.get("format", "") for r in seen))}

    if Image is None:
        say("Pillow 가 없어 T6~T8 은 건너뜁니다.")
    else:
        say("T6 PC 계산만(디코드 · 줄이기 · 저장)…")
        R["cpu"] = cpu_tests(kept)
        kept.clear()
        R["pipe_sweep"] = []
        for w in PIPE_WORKERS:
            say(f"T7 앱과 같은 방식, 일꾼 {w}개…")
            s, _ = run_pool(lambda f, start, w=w: pipeline(f, f"pipe{w}", w, tmpdir, start),
                            plan[f"pipe{w}"], w, STEP_CAP_S)
            R["pipe_sweep"].append(s)
        say("T7 통째로 읽은 뒤 디코드, 일꾼 3개…")
        R["pipe_bytes3"], _ = run_pool(
            lambda f, start: pipeline(f, "pipe_bytes3", 3, tmpdir, start, bytes_first=True),
            plan["pipe_bytes3"], 3, STEP_CAP_S)
        say("T8 첫 화면 60칸 흉내(일꾼 3, 지금 앱)…")
        R["first60_w3"] = first60(plan["first60_w3"], APP_WORKERS, tmpdir, detail_in_queue=True)
        say("T8 첫 화면 60칸 흉내(일꾼 8, 큰 사진 따로)…")
        R["first60_w8"] = first60(plan["first60_w8"], 8, tmpdir, detail_in_queue=False)

    say("T9 PC 디스크 쓰기 속도…")
    R["local_write"] = local_write(tmpdir)
    say("T10 앱 캐시 폴더 상태…")
    R["cache"] = cache_state()


def desktop_dir() -> Path:
    cands = []
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0:
                cands.append(Path(buf.value))
        except Exception:                                   # noqa: BLE001
            pass
    cands += [Path.home() / "Desktop", Path.home() / "바탕 화면", Path.home()]
    for c in cands:
        if c.is_dir() and os.access(c, os.W_OK):
            return c
    return Path(tempfile.gettempdir())


def write_zip(R, out_csv) -> Path:
    R["elapsed_s"] = round(time.perf_counter() - T0, 1)
    R["errors"] = errors[:500]
    try:
        summary = write_summary(R)
    except Exception:                                       # noqa: BLE001 — 요약이 깨져도 숫자는 보낸다
        summary = "요약을 만들다 오류가 났습니다. result.json 을 봐 주세요.\n" + traceback.format_exc()

    def to_csv(rows, fields=None):
        if not fields:
            fields = []
            for r in rows:
                fields += [k for k in r if k not in fields]
        buf = io.StringIO()
        wr = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)
        return "﻿" + buf.getvalue()                    # 엑셀에서 한글이 깨지지 않게 BOM

    out = desktop_dir() / f"속도측정_썸네일_{datetime.now():%Y%m%d_%H%M}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("요약.txt", summary)
        z.writestr("result.json", json.dumps(R, ensure_ascii=False, indent=1, default=str))
        z.writestr("timings.csv", to_csv(timings))
        z.writestr("folders.csv", to_csv(out_csv.get("folders", [])))
        z.writestr("files.csv", to_csv(out_csv.get("files", [])[:CSV_FILE_ROWS],
                                       ["dir", "name", "kind", "size"]))
    print()
    print(summary)
    return out


def pick_folders():
    """(폴더 목록, tk root 또는 None)."""
    args = [a for a in sys.argv[1:] if os.path.isdir(a)]
    if args:
        return args, None
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
    except Exception:                                       # noqa: BLE001 — 창을 못 띄우는 환경
        p = input("사진이 든 폴더 경로를 붙여 넣고 Enter: ").strip().strip('"')
        return ([p] if os.path.isdir(p) else []), None
    messagebox.showinfo("썸네일 속도 측정", "현택님, 사진이 든 폴더를 골라 주세요.\n"
                        "(LOT 폴더나 웨이퍼 폴더 하나면 됩니다. 폴더는 읽기만 하고 아무것도 바꾸지 않습니다.)",
                        parent=root)
    folders = []
    while True:
        d = filedialog.askdirectory(title="사진이 든 폴더 고르기", parent=root)
        if d:
            d = os.path.normpath(d)
            if d not in folders:
                folders.append(d)
        if not folders:
            return [], root
        if not messagebox.askyesno("썸네일 속도 측정", f"고른 폴더 {len(folders)}개:\n" + "\n".join(folders)
                                   + "\n\n폴더를 더 추가할까요?", parent=root):
            return folders, root


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")            # 콘솔이 못 찍는 글자로 멈추지 않게
    except Exception:                                       # noqa: BLE001
        pass
    folders, root = pick_folders()
    if not folders:
        print("폴더를 고르지 않아 끝냅니다.")
        return 1
    say(f"측정을 시작합니다(최대 5분쯤). 폴더 {len(folders)}개 — 읽기만 합니다.")
    R: dict = {"tool": "measure_thumb_speed", "version": 1}
    out_csv: dict = {}
    tmpdir = tempfile.mkdtemp(prefix="aoi_thumb_speed_")
    try:
        measure(folders, R, tmpdir, out_csv)
    except KeyboardInterrupt:
        R["interrupted"] = True
        say("멈춤 요청을 받았습니다. 지금까지 잰 것만 저장합니다.")
    except Exception as exc:                                # noqa: BLE001
        R["crash"] = traceback.format_exc()
        note_error("measure", exc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    R.setdefault("env", environment(folders))
    out = write_zip(R, out_csv)
    msg = f"끝났습니다. 이 파일을 올려 주세요:\n{out}"
    if R.get("fatal"):
        msg = f"{R['fatal']}\n다른 폴더로 다시 돌려 주세요.\n(기록: {out})"
    print(msg)
    if os.name == "nt":
        try:
            os.startfile(str(out.parent))                   # noqa: S606 — 결과 폴더 열기
        except OSError:
            pass
    if root is not None:
        try:
            from tkinter import messagebox
            messagebox.showinfo("썸네일 속도 측정", msg, parent=root)
            root.destroy()
        except Exception:                                   # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
