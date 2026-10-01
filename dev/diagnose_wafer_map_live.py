"""Wafer map 이 LIVE 파일명 슬롯을 **이상하게 그리는** 원인을 한 번에 짚는 진단 도구.

신고 증상(Camtek LIVE 파일명 폴더):
  1. slot 을 바꾸면 같아야 할 die 배치(격자)가 달라진다.
  2. die 가 없는 위치에 점이 찍힌다.
  3. 파일명은 (3,3) 인데 맵에서는 (9,3) 자리에 찍힌다.

앱이 맵을 그리는 경로(`coords.wafer_map.build_map` → `to_plane` → `frame_for_folder`)를
**앱 코드 그대로** 다시 돌리면서, 그 사이의 모든 중간값과 원재료(INI·die 맵·파일명 토큰)를
**한 파일**에 남긴다.  두 번 진단하지 않도록 경쟁 가설(좌표 뒤집힘·축 교환·1-based·
절대좌표·pitch 오판·부모 폴더 파일 공유·화면 회전)을 전부 수치로 같이 찍는다.

사용법::

    python dev/diagnose_wafer_map_live.py                       # 기본 경로·기본 출력 폴더
    python dev/diagnose_wafer_map_live.py "<폴더>" [<폴더> ...]
    python dev/diagnose_wafer_map_live.py "<폴더>" --out "<출력 폴더>" --focus 3,3 --focus 9,3

폴더는 슬롯 폴더(사진이 바로 든 폴더)·LOT 폴더(슬롯 폴더들이 든 폴더)·그 상위 어느
것이든 된다.  **읽기 전용**이며 아무것도 바꾸지 않는다.  결과는 출력 폴더에
``wafer_map_진단_<시각>.txt`` 1개로 쓴다(중간에 죽어도 거기까지는 남는다).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import logging
import math
import os
import platform
import re
import struct
import sys
import traceback
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_TARGET = (r"\\k5cifsn2\k5tsvdata$\1. Conder Scan\531. NVIDIA INT"
                  r"\145. AST254-AG6S2-6CAA\22. E1X000.C03 DCC20 (WVU)\2. BS CUP")
DEFAULT_OUT_DIR = r"C:\Users\304236\Desktop\종료된 업무"
DEFAULT_FOCUS = ["3,3", "9,3"]

_IMG_EXT = (".jpeg", ".jpg", ".png", ".bmp")
_TEXT_EXT = {"", ".ini", ".txt", ".md", ".xml", ".csv", ".001", ".log", ".dat.md",
             ".json", ".cfg", ".klarf", ".000", ".002"}
_MAX_TEXT_DUMP = 256 * 1024      # 이보다 큰 텍스트는 앞부분만
_HEAD_LINES = 120                # 큰 파일/알 수 없는 파일의 앞부분 줄 수
_MAX_ASCII_COLS = 220            # 이보다 넓은 die 맵은 ASCII 로 안 그린다
_MAX_WALK_DEPTH = 4              # 슬롯 폴더를 찾아 내려가는 깊이

# ---------------------------------------------------------------------------
# 앱 코드 import — 실패해도 원재료 덤프는 계속한다
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve()
_APP_IMPORT_ERR = ""
_MARK = Path("aoi_verification") / "app" / "coords" / "wafer_map.py"
_SKIP_DIRS = {"windows", "$recycle.bin", "appdata", "node_modules", ".git", "python",
              "__pycache__", ".pytest_cache", "system volume information", "programdata"}
_APP_SEARCH_LOG: list[str] = []


def _walk_for_app(root: Path, depth: int):
    """root 아래 depth 단계까지 앱 코드(aoi_verification) 폴더를 찾는다."""
    try:
        if (root / _MARK).is_file():
            yield root
        if depth <= 0:
            return
        with os.scandir(root) as it:
            subs = [Path(e.path) for e in it
                    if e.is_dir() and e.name.lower() not in _SKIP_DIRS
                    and not e.name.startswith(".")]
    except OSError:
        return
    for d in subs:
        yield from _walk_for_app(d, depth - 1)


def _find_app_root():
    """앱 코드 위치 — ``--app <폴더>`` · 환경변수 ``AOI_APP_ROOT`` · 스크립트/현재 폴더의
    부모 · 사용자 폴더(바탕화면·문서·다운로드) · C:/D: 드라이브 얕은 곳 순으로 찾는다.
    바탕화면에 스크립트만 복사해 돌려도 설치된 앱(포터블/exe 의 app 폴더)을 찾게 한다."""
    explicit = []
    if "--app" in sys.argv:
        i = sys.argv.index("--app")
        if i + 1 < len(sys.argv):
            explicit.append(Path(sys.argv[i + 1]))
    if os.environ.get("AOI_APP_ROOT"):
        explicit.append(Path(os.environ["AOI_APP_ROOT"]))
    for c in explicit:
        for cand in (c, c / "app"):
            if (cand / _MARK).is_file():
                return cand
        _APP_SEARCH_LOG.append(f"지정한 경로에 앱 코드 없음: {c}")
    for c in (*_HERE.parents, Path.cwd(), *Path.cwd().parents):
        if (c / _MARK).is_file():
            return c
    home = Path.home()
    roots = [(home / n, 4) for n in ("Desktop", "바탕 화면", "OneDrive", "Documents",
                                     "Downloads")]
    roots += [(home, 2), (Path("C:/"), 3), (Path("D:/"), 3)]
    found = []
    for r, depth in roots:
        if not r.exists():
            continue
        found += list(_walk_for_app(r, depth))
        if found:
            break
    if found:
        # 여러 개면 wafer_map.py 가 가장 최근인 것
        found.sort(key=lambda c: (c / _MARK).stat().st_mtime, reverse=True)
        _APP_SEARCH_LOG.append("찾은 앱 코드 후보: " + ", ".join(str(c) for c in found[:8]))
        return found[0]
    _APP_SEARCH_LOG.append("탐색한 곳: " + ", ".join(str(r) for r, _ in roots))
    return None


_REPO = _find_app_root()
if _REPO is not None:
    sys.path.insert(0, str(_REPO))

try:
    from aoi_verification.app import coords as C                       # noqa: E402
    from aoi_verification.app.coords import camtek_ini, camtek_live    # noqa: E402
    from aoi_verification.app.coords import wafer_geometry as wg       # noqa: E402
    from aoi_verification.app.coords import wafer_map as wm            # noqa: E402
    from aoi_verification.app.coords.ini_text import read_ini_text     # noqa: E402
    try:
        from aoi_verification.app.models.slot import (_list_images,   # noqa: E402
                                                      is_ignored_name)
    except Exception:                                                   # noqa: BLE001
        _list_images = None
        is_ignored_name = None
    APP = True
except Exception:                                                       # noqa: BLE001
    APP = False
    _APP_IMPORT_ERR = traceback.format_exc()
    C = camtek_ini = camtek_live = wg = wm = None
    read_ini_text = None
    _list_images = None
    is_ignored_name = None


# ---------------------------------------------------------------------------
# 앱 로그 포착 — 앱이 남기는 경고(부분 맵 거부·폴백 등)를 보고서에 그대로 싣는다
# ---------------------------------------------------------------------------
class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(f"[{record.levelname}] {record.name}: {record.getMessage()}")
        except Exception:                                               # noqa: BLE001
            self.records.append(f"[{record.levelname}] {record.name}: (메시지 포맷 실패)")


_LOGS = _ListHandler()
for _name in ("aoi", "aoi.coords", "aoi.coords.wafer_map"):
    _lg = logging.getLogger(_name)
    _lg.setLevel(logging.DEBUG)
    _lg.addHandler(_LOGS)
    _lg.propagate = False


def _take_logs() -> list[str]:
    out = list(_LOGS.records)
    _LOGS.records.clear()
    return out


def _clear_app_caches() -> None:
    """lru_cache 를 비워 슬롯마다 앱 로그가 다시 나오게 한다(결과값에는 영향 없음)."""
    if not APP:
        return
    import aoi_verification.app.coords as pkg
    for mod_name in ("wafer_geometry", "wafer_map", "camtek_ini", "camtek_live",
                     "kla_info", "ini_text"):
        mod = getattr(pkg, mod_name, None)
        if mod is None:
            try:
                mod = __import__(f"aoi_verification.app.coords.{mod_name}",
                                 fromlist=["_"])
            except Exception:                                           # noqa: BLE001
                continue
        for v in vars(mod).values():
            fn = getattr(v, "cache_clear", None)
            if callable(fn):
                try:
                    fn()
                except Exception:                                       # noqa: BLE001
                    pass


# ---------------------------------------------------------------------------
# 보고서 — 섹션마다 flush 해서 중간에 죽어도 남긴다
# ---------------------------------------------------------------------------
class Report:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fh = path.open("w", encoding="utf-8-sig", newline="\r\n")
        self.findings: list[str] = []

    def w(self, *lines: str) -> None:
        for ln in lines:
            self.fh.write(str(ln) + "\n")

    def h1(self, title: str) -> None:
        self.w("", "=" * 100, f"■ {title}", "=" * 100)
        self.fh.flush()

    def h2(self, title: str) -> None:
        self.w("", f"── {title} " + "─" * max(0, 90 - len(title)))

    def kv(self, key: str, val) -> None:
        self.w(f"  {key:<34}: {val}")

    def find(self, msg: str) -> None:
        """자동 판정 — 끝의 '요약' 에 모은다."""
        self.findings.append(msg)
        self.w(f"  ⚑ {msg}")

    def close(self) -> None:
        self.fh.flush()
        self.fh.close()


def _guard(rep: Report, what: str, fn, *a, **kw):
    """섹션 하나가 죽어도 나머지는 계속 — 트레이스백을 보고서에 남긴다."""
    try:
        return fn(*a, **kw)
    except Exception:                                                   # noqa: BLE001
        rep.w(f"  !! [{what}] 진단 중 예외 — 아래 트레이스백:")
        rep.w(*["     " + ln for ln in traceback.format_exc().splitlines()])
        rep.fh.flush()
        return None


# ---------------------------------------------------------------------------
# 파일 유틸
# ---------------------------------------------------------------------------
def _stat(p: Path) -> str:
    try:
        st = p.stat()
        t = _dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        return f"{st.st_size:,} B, 수정 {t}"
    except OSError as e:
        return f"stat 실패({e})"


def _encoding(raw: bytes) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "UTF-16(BOM)"
    if raw[:3] == b"\xef\xbb\xbf":
        return "UTF-8(BOM)"
    if len(raw) > 4 and raw[1:2] == b"\x00" and raw[3:4] == b"\x00":
        return "UTF-16LE(BOM 없음 추정)"
    return "UTF-8/ANSI"


def _decode(raw: bytes) -> str:
    enc = _encoding(raw)
    if enc.startswith("UTF-16"):
        try:
            return raw.decode("utf-16" if "BOM)" in enc else "utf-16-le", errors="replace")
        except Exception:                                               # noqa: BLE001
            pass
    for e in ("utf-8-sig", "cp949", "latin-1"):
        try:
            return raw.decode(e)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _read_text(p: Path, limit: int = _MAX_TEXT_DUMP) -> tuple[str, str, bool]:
    """(텍스트, 인코딩, 잘렸나)."""
    with p.open("rb") as fh:
        raw = fh.read(limit + 1)
    cut = len(raw) > limit
    raw = raw[:limit]
    return _decode(raw), _encoding(raw), cut


def _looks_text(raw: bytes) -> bool:
    if not raw:
        return True
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return True
    sample = raw[:4096]
    bad = sum(1 for b in sample if b < 9 or (13 < b < 32))
    return bad / len(sample) < 0.05


def _sha(p: Path) -> str:
    try:
        return hashlib.sha1(p.read_bytes()).hexdigest()[:12]
    except OSError:
        return "?"


def _is_image(name: str) -> bool:
    return name.lower().endswith(_IMG_EXT)


def _list_imgs(folder: Path) -> tuple[list[Path], list[Path]]:
    """(앱이 쓰는 사진, 앱이 무시하는 사진)."""
    all_imgs: list[Path] = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                try:
                    if e.is_file() and _is_image(e.name):
                        all_imgs.append(Path(e.path))
                except OSError:
                    continue
    except OSError:
        return [], []
    all_imgs.sort(key=lambda p: p.name.lower())
    if _list_images is not None:
        used = _list_images(folder)
    else:
        used = all_imgs
    used_set = set(used)
    return used, [p for p in all_imgs if p not in used_set]


def _search_dirs(folder: Path) -> list[Path]:
    if APP:
        return wg._search_dirs(folder)
    out, cur = [folder], folder
    for _ in range(2):
        if cur.parent == cur:
            break
        cur = cur.parent
        out.append(cur)
    return out


# ---------------------------------------------------------------------------
# LIVE 파일명 토큰 — 앱 파서와 독립으로 한 번 더 본다
# ---------------------------------------------------------------------------
_INT = re.compile(r"^-?\d+$")
_NUM = re.compile(r"^-?\d+(?:\.\d+)?$")
_NUM_ANY = re.compile(r"^[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?$")


def _token_shape(stem: str) -> str:
    """토큰 배치 모양 — I=정수, F=실수, S=문자열.  배치가 여러 개 섞였는지 본다."""
    out = []
    for t in stem.split("_"):
        if _INT.match(t):
            out.append("I")
        elif _NUM.match(t):
            out.append("F")
        elif _NUM_ANY.match(t):
            out.append("f")          # 지수표기/쉼표 소수 — 앱 파서는 수치로 안 본다
        else:
            out.append("S")
    return "_".join(out)


def _int_pairs(stem: str) -> list[tuple[int, int, int]]:
    """연속한 두 정수 토큰 전부 — (위치, a, b).  앱은 그중 첫 번째를 col/row 로 쓴다."""
    toks = stem.split("_")
    return [(i, int(toks[i]), int(toks[i + 1])) for i in range(len(toks) - 1)
            if _INT.match(toks[i]) and _INT.match(toks[i + 1])]


# ---------------------------------------------------------------------------
# die 맵 원시 읽기 — 앱(die_map_cells)과 같은 규칙이지만 '왜 거부했는지' 를 다 남긴다
# ---------------------------------------------------------------------------
_RECSIZE_PAT = re.compile(r'RecordSize\s+Size="(\d+)"')
_FIELD_PAT = re.compile(r'Name="([^"]+)"[^>]*?Offset="(\d+)"[^>]*?Vartype="(\d+)"')


def _raw_die_map(dat: Path) -> dict:
    info: dict = {"path": dat, "exists": dat.exists()}
    md = Path(str(dat) + ".md")
    info["md_exists"] = md.exists()
    if not info["exists"]:
        return info
    info["stat"] = _stat(dat)
    info["sha"] = _sha(dat)
    if not info["md_exists"]:
        info["reject"] = ".md 사이드카 없음 → 앱은 이 맵을 안 씀"
        return info
    txt, _, _ = _read_text(md)
    m = _RECSIZE_PAT.search(txt)
    fields = {n: (int(o), int(v)) for n, o, v in _FIELD_PAT.findall(txt)}
    info["recsize"] = int(m.group(1)) if m else None
    info["fields"] = fields
    try:
        data = dat.read_bytes()
    except OSError as e:
        info["reject"] = f"읽기 실패 {e}"
        return info
    info["bytes"] = len(data)
    rec = info["recsize"]
    if not rec:
        info["reject"] = "RecordSize 없음"
        return info
    if len(data) % rec:
        info["reject"] = f"파일 크기 {len(data)} 가 레코드 {rec} 의 배수가 아님"
    n = len(data) // rec
    info["count"] = n
    recs: list[dict] = []
    for i in range(n):
        b = i * rec
        row = {}
        for name, (off, vt) in fields.items():
            try:
                if vt == 5:
                    row[name] = struct.unpack_from("<d", data, b + off)[0]
                elif vt == 4:
                    row[name] = struct.unpack_from("<f", data, b + off)[0]
                elif vt in (3, 22, 19):
                    row[name] = struct.unpack_from("<i", data, b + off)[0]
                elif vt in (2, 18):
                    row[name] = struct.unpack_from("<h", data, b + off)[0]
                elif vt == 11:
                    row[name] = struct.unpack_from("<h", data, b + off)[0] != 0
                else:
                    row[name] = f"vt{vt}"
            except struct.error:
                row[name] = "?"
        recs.append(row)
    info["records"] = recs
    return info


# ---------------------------------------------------------------------------
# 표시 좌표 계산 — 앱 화면이 보여 주는 (col,row) 로 되돌린다
# ---------------------------------------------------------------------------
def _disp_of_cell(kx: int, ky: int, col_origin: int, row_total: int) -> tuple[int, int]:
    """평면 칸 (kx,ky) → 장비 표시 (col,row).

    wafer_map: stage 칸 j 의 평면 ky = −(j+1),  표시 row = row_total − j,
    표시 col = kx − col_origin (stage x 칸 = kx)."""
    j = -ky - 1
    return kx - col_origin, row_total - j


def _drawn_cells(frame) -> tuple[set, str]:
    """앱이 격자로 그리는 칸 집합(평면 칸 인덱스)과 그 출처."""
    if frame is None or not frame.pitch_x or not frame.pitch_y:
        return set(), "격자 없음(pitch 모름)"
    if frame.die_cells:
        return set(frame.die_cells), "die 맵(s_DieLocation.dat)"
    r2 = frame.radius ** 2
    px, py, x0, y0 = frame.pitch_x, frame.pitch_y, frame.grid_x0, frame.grid_y0
    k_lo = math.floor((-frame.radius - x0) / px) - 1
    k_hi = math.ceil((frame.radius - x0) / px) + 1
    j_lo = math.floor((-frame.radius - y0) / py) - 1
    j_hi = math.ceil((frame.radius - y0) / py) + 1
    out = set()
    for kx in range(k_lo, k_hi + 1):
        for ky in range(j_lo, j_hi + 1):
            xa, xb = x0 + kx * px, x0 + (kx + 1) * px
            ya, yb = y0 + ky * py, y0 + (ky + 1) * py
            if all(x * x + y * y <= r2 for x in (xa, xb) for y in (ya, yb)):
                out.add((kx, ky))
    return out, "계산(원 안에 온전히 드는 칸)"


def _ascii_map(dies: set, marks: dict, title: str) -> list[str]:
    """dies = {(col,row)} 표시 좌표, marks = {(col,row): 개수}.

    '.' die(결함 없음)  '#' die + 결함  '!' die 없는 칸에 결함  ' ' 빈칸.
    맨 위가 큰 row(장비 화면처럼 row 0 이 아래)."""
    pts = set(dies) | set(marks)
    if not pts:
        return [f"  ({title}: 그릴 칸 없음)"]
    c_lo = min(c for c, _ in pts)
    c_hi = max(c for c, _ in pts)
    r_lo = min(r for _, r in pts)
    r_hi = max(r for _, r in pts)
    width = c_hi - c_lo + 1
    if width > _MAX_ASCII_COLS or (r_hi - r_lo + 1) > 400:
        return [f"  ({title}: {width}열 × {r_hi - r_lo + 1}행 — 너무 커서 생략)"]
    out = [f"  [{title}]  col {c_lo}..{c_hi}, row {r_lo}..{r_hi}   "
           f"'.'=die  '#'=die+결함  '!'=die 없는 칸에 결함"]

    def hdr(fn) -> str:
        return "        " + "".join(fn(c) for c in range(c_lo, c_hi + 1))
    if c_lo < 0 or c_hi >= 100:
        out.append(hdr(lambda c: "-" if c < 0 else str(abs(c) // 100 % 10)))
    out.append(hdr(lambda c: str(abs(c) // 10 % 10)))
    out.append(hdr(lambda c: str(abs(c) % 10)))
    for r in range(r_hi, r_lo - 1, -1):
        line = []
        for c in range(c_lo, c_hi + 1):
            if (c, r) in marks:
                line.append("#" if (c, r) in dies else "!")
            elif (c, r) in dies:
                line.append(".")
            else:
                line.append(" ")
        out.append(f"  {r:>4}  " + "".join(line))
    return out


def _corr(a: list[float], b: list[float]) -> str:
    n = len(a)
    if n < 3:
        return "n<3"
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return "분산 0"
    return f"{sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb):+.3f}"


def _fmt(v, nd=1) -> str:
    if v is None:
        return "None"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


# ---------------------------------------------------------------------------
# 폴더 분류 — 앱(wafer_map_dialog.classify_folder)과 같은 규칙 + 더 깊이 탐색
# ---------------------------------------------------------------------------
def _classify(rep: Report, target: Path) -> tuple[str, dict[str, Path]]:
    used, _ = _list_imgs(target)
    if used:
        return "slot", {target.name: target}
    slots = {}
    try:
        for e in sorted(os.scandir(target), key=lambda e: e.name.lower()):
            if e.is_dir() and _list_imgs(Path(e.path))[0]:
                slots[e.name] = Path(e.path)
    except OSError as e:
        rep.w(f"  !! 폴더 열거 실패: {e}")
    if slots:
        return "lot", slots
    # 앱은 여기서 '비어 있음' 이지만, 진단은 더 깊이 내려가 사진 폴더를 찾는다.
    found: dict[str, Path] = {}
    for root, dirs, files in os.walk(target):
        depth = len(Path(root).relative_to(target).parts)
        if depth > _MAX_WALK_DEPTH:
            dirs[:] = []
            continue
        if any(_is_image(f) for f in files):
            key = str(Path(root).relative_to(target))
            found[key] = Path(root)
    return "deep", found


def _tree(rep: Report, target: Path) -> None:
    rep.h2("폴더 트리(깊이 3) — 사진 수 / 그 외 파일")
    for root, dirs, files in os.walk(target):
        rel = Path(root).relative_to(target)
        depth = len(rel.parts)
        if depth > 3:
            dirs[:] = []
            continue
        dirs.sort(key=str.lower)
        imgs = [f for f in files if _is_image(f)]
        other = sorted(f for f in files if not _is_image(f))
        rep.w(f"  {'    ' * depth}{rel.as_posix() if depth else '.'}/  "
              f"사진 {len(imgs)}  기타 {len(other)}"
              + (f"  → {other[:15]}{' …' if len(other) > 15 else ''}" if other else ""))


# ---------------------------------------------------------------------------
# 슬롯 진단
# ---------------------------------------------------------------------------
_DUMPED: dict[Path, str] = {}     # 부록에 실을 텍스트 파일 → 내용 요약


def _context_files(rep: Report, folder: Path) -> dict:
    """앱이 기하를 찾을 때 보는 파일들을 폴더+부모 2단계에서 전부 확인."""
    rep.h2("기하 원재료 파일 — 폴더 + 부모 2단계 (앱 탐색 순서)")
    names = ["Params_WaferInfo.ini", "ProductInfo.ini", "Wafer2Table.ini",
             "s_DieLocation.dat", "s_DieLocation.dat.md",
             "ColorImageGrabingInfo.ini", "ColorImageGrabinginfo.ini"]
    hit: dict = {}
    for lvl, base in enumerate(_search_dirs(folder)):
        where = "슬롯 폴더 자신" if lvl == 0 else f"부모 {lvl}단계"
        for n in names:
            p = base / n
            if p.exists():
                rep.w(f"  ✓ [{where}] {p}  ({_stat(p)}, sha {_sha(p)})")
                hit.setdefault(n, []).append((lvl, p))
                if not n.endswith(".dat"):
                    _DUMPED.setdefault(p, n)
    for n in names:
        if n not in hit:
            rep.w(f"  ✗ {n} — 어디에도 없음")
    for n, lst in hit.items():
        if lst[0][0] > 0 and n in ("Params_WaferInfo.ini", "ProductInfo.ini",
                                   "Wafer2Table.ini", "s_DieLocation.dat"):
            rep.find(f"{folder.name}: {n} 를 슬롯 폴더가 아니라 **부모 {lst[0][0]}단계**"
                     f"({lst[0][1].parent})에서 읽는다 — 다른 웨이퍼/슬롯과 공유되는 값일 수 있음")
    # 슬롯 폴더 안의 그 밖의 비-사진 파일
    try:
        others = sorted(p for p in folder.iterdir()
                        if p.is_file() and not _is_image(p.name))
    except OSError:
        others = []
    if others:
        rep.w(f"  슬롯 폴더의 비-사진 파일 {len(others)}개:")
        for p in others:
            rep.w(f"     - {p.name}  ({_stat(p)})")
            if p.name not in names:
                _DUMPED.setdefault(p, "기타")
    return hit


def _ini_values(rep: Report, folder: Path) -> None:
    rep.h2("INI 핵심 키 (앱 _read_key 로 읽은 값 · 원문 줄)")
    keys = ("DieStep_X", "DieStep_Y", "DieSize_X", "DieSize_Y", "XDieIndex", "YDieIndex",
            "CustomerDiePitch_X", "CustomerDiePitch_Y", "XDieSize", "YDieSize",
            "Diameter", "MeasuredDiameter", "Center_X", "Center_Y", "FlatNotchVal",
            "Notch", "NotchAngle", "FlatAngle", "EBR", "Rows", "Cols", "NumOfDies")
    for base in _search_dirs(folder):
        for rel in ("Params_WaferInfo.ini", "ProductInfo.ini"):
            p = base / rel
            if not p.exists():
                continue
            txt = read_ini_text(p) if APP else _read_text(p)[0]
            txt = txt or ""
            rep.w(f"  · {p}")
            for k in keys:
                lines = [ln.strip() for ln in txt.splitlines()
                         if re.match(r"\s*" + re.escape(k) + r"\s*=", ln, re.I)]
                if not lines:
                    continue
                parsed = wg._read_key(p, k, -1e12, 1e12) if APP else "?"
                rep.w(f"      {k:<20} 원문 {lines}  → 앱 파싱 {parsed}")
            # 키 이름에 die/step/pitch/center/notch 가 들어간 줄 전부(이름이 다른 장비 대비)
            extra = [ln.strip() for ln in txt.splitlines()
                     if re.search(r"die|step|pitch|center|notch|flat|index|orient|angle",
                                  ln, re.I) and "=" in ln]
            if extra:
                rep.w(f"      (관련 줄 전체 {len(extra)}개) " + " | ".join(extra[:60]))


def _die_map_section(rep: Report, folder: Path, px, py) -> None:
    rep.h2("s_DieLocation.dat — 원시 읽기(앱 규칙 + 거부 사유)")
    any_found = False
    for lvl, base in enumerate(_search_dirs(folder)):
        dat = base / "s_DieLocation.dat"
        if not dat.exists():
            continue
        any_found = True
        info = _raw_die_map(dat)
        rep.w(f"  · [{('자신' if lvl == 0 else f'부모 {lvl}단계')}] {dat}  {info.get('stat')}"
              f"  sha {info.get('sha')}")
        rep.kv("    .md 사이드카", info.get("md_exists"))
        rep.kv("    RecordSize / 필드", f"{info.get('recsize')} / {info.get('fields')}")
        rep.kv("    레코드 수", info.get("count"))
        if info.get("reject"):
            rep.kv("    거부 사유", info["reject"])
        recs = info.get("records") or []
        if recs:
            for fname in sorted(recs[0]):
                vals = [r[fname] for r in recs if isinstance(r.get(fname), (int, float))]
                if vals:
                    rep.w(f"      필드 {fname:<14} min {min(vals):.3f}  max {max(vals):.3f}  "
                          f"고유값 {len(set(vals))}")
            rep.w("      처음 5 레코드: " + " | ".join(str(r) for r in recs[:5]))
            if px and py and "x" in recs[0] and "y" in recs[0]:
                cells = {(math.floor(r["x"] / px), math.floor(r["y"] / py)) for r in recs
                         if isinstance(r.get("x"), float) and isinstance(r.get("y"), float)}
                rep.kv("    floor(x/px),floor(y/py) 칸 수", f"{len(cells)} (레코드 {len(recs)})")
                if len(cells) != len(recs):
                    rep.w(f"      (참고) 레코드 {len(recs)}개 → 칸 {len(cells)}개: die 하나에 "
                          f"레코드가 여러 개({len(recs) / max(1, len(cells)):.2f}배)이거나 "
                          f"pitch 가 die 맵 간격과 다름 — 아래 간격 최빈값과 pitch 비교")
                xs = sorted({r["x"] for r in recs})
                ys = sorted({r["y"] for r in recs})
                dx = Counter(round(b - a, 3) for a, b in zip(xs, xs[1:]))
                dy = Counter(round(b - a, 3) for a, b in zip(ys, ys[1:]))
                rep.kv("    die 맵 x 간격(최빈 5)", dx.most_common(5))
                rep.kv("    die 맵 y 간격(최빈 5)", dy.most_common(5))
                if cells:
                    rep.kv("    stage 인덱스 범위",
                           f"x {min(i for i, _ in cells)}..{max(i for i, _ in cells)}, "
                           f"y {min(j for _, j in cells)}..{max(j for _, j in cells)}")
                # x/y 가 die 왼아래 모서리인지 중심인지 — 칸 안 상대 위치
                fr = [((r["x"] / px) % 1, (r["y"] / py) % 1) for r in recs[:200]
                      if isinstance(r.get("x"), float)]
                if fr:
                    rep.kv("    레코드 좌표의 칸 안 상대위치(평균)",
                           f"x {sum(a for a, _ in fr) / len(fr):.3f}, "
                           f"y {sum(b for _, b in fr) / len(fr):.3f}  "
                           f"(0≈모서리, 0.5≈중심 — 0.99/0.01 근처면 경계 걸침 위험)")
    if not any_found:
        rep.w("  ✗ 폴더·부모 어디에도 없음 → 격자는 '계산' 으로 그려진다")


def _diagnose_slot(rep: Report, name: str, folder: Path, focus: list[tuple[int, int]],
                   summary: dict) -> None:
    rep.h1(f"슬롯 [{name}]  {folder}")
    _clear_app_caches()
    _take_logs()
    used, ignored = _list_imgs(folder)
    rep.kv("앱이 쓰는 사진 수", len(used))
    rep.kv("앱이 무시하는 사진 수", f"{len(ignored)}  {[p.name for p in ignored[:5]]}")
    S: dict = {"name": name, "folder": folder, "n": len(used)}
    summary[name] = S

    _guard(rep, "원재료 파일", _context_files, rep, folder)
    if APP:
        _guard(rep, "INI 핵심 키", _ini_values, rep, folder)

    # ── 파일명 토큰 ──────────────────────────────────────────────────────
    rep.h2("LIVE 파일명 토큰 배치 (I=정수 F=실수 f=지수/쉼표수 S=문자)")
    shapes = Counter(_token_shape(p.stem) for p in used)
    for sh, n in shapes.most_common(15):
        ex = next(p.name for p in used if _token_shape(p.stem) == sh)
        rep.w(f"  {n:>6} 장  {sh}")
        rep.w(f"           예) {ex}")
    if len(shapes) > 1:
        rep.find(f"{name}: 파일명 토큰 배치가 {len(shapes)}종 섞여 있다")
    multi = [p for p in used if len(_int_pairs(p.stem)) > 1]
    if multi:
        rep.w(f"  연속 정수쌍이 2개 이상인 파일 {len(multi)}장 — 앱은 **첫 쌍**을 col/row 로 씀:")
        for p in multi[:8]:
            rep.w(f"     {p.name}  쌍={[(a, b) for _, a, b in _int_pairs(p.stem)]}")
        rep.find(f"{name}: 정수쌍이 여러 개인 파일명 {len(multi)}장 — col/row 를 잘못 집을 수 있음")
    if any("f" in sh.split("_") for sh in shapes):
        rep.find(f"{name}: 지수표기/쉼표 소수 토큰이 있다 — 앱 파서는 수치로 안 읽어 x/y 가 밀릴 수 있음")

    if not APP:
        rep.w("  (앱 코드 import 실패 — 이하 앱 경로 재현 생략)")
        return

    # ── 앱 좌표 해석 ─────────────────────────────────────────────────────
    rep.h2("앱 좌표 해석(coords.resolve) — 소스별 장수")
    coords = {p: C.resolve(p) for p in used}
    src = Counter((c.source if c else "None(좌표 없음)") for c in coords.values())
    rep.kv("소스 분포", dict(src))
    live = {p: camtek_live.parse_live_name(p.stem) for p in used}
    n_live = sum(v is not None for v in live.values())
    rep.kv("parse_live_name 성공", f"{n_live}/{len(used)}")
    fails = [p.name for p, v in live.items() if v is None]
    if fails:
        rep.w("  LIVE 파싱 실패 예: " + " | ".join(fails[:10]))
    rep.kv("INI 항목 있음(has_camtek_entries)", wg.has_camtek_entries(folder))
    try:
        rep.kv("ColorImageGrabingInfo 항목 수", len(camtek_ini.load_raw_folder(folder)))
    except Exception as e:                                              # noqa: BLE001
        rep.kv("ColorImageGrabingInfo 항목 수", f"예외 {e}")

    # ── 기하 단계별 ──────────────────────────────────────────────────────
    rep.h2("die 기하 — 앱이 고르는 경로를 단계별로")
    cg = wg.camtek_geometry(folder)
    rep.kv("camtek_geometry (INI 검산 경로)", cg)
    lg = wg.live_geometry(folder)
    rep.kv("live_geometry (파일 pitch 경로)", lg)
    rep.kv("peek_die_pitch", wg.peek_die_pitch(folder))
    est = wm._live_pitch_estimate(folder)
    rep.kv("_live_pitch_estimate (사진 최댓값×1.05)", est)
    dia = wg._read_diameter(folder)
    rep.kv("Diameter", dia)
    rep.kv("Center_X / Center_Y (Params)",
           f"{wg._read_center(folder, 'Center_X')} / {wg._read_center(folder, 'Center_Y')}")
    rep.kv("Wafer2Table 중심", wg._read_wafer2table(folder))
    cx_, cy_ = wg._wafer_center(folder)
    rep.kv("_wafer_center (최종)", (cx_, cy_))
    cam = wm._camtek(folder)
    geom = cam.geom
    rep.kv("wafer_map._camtek.geom (맵이 쓰는 기하)", geom)
    rep.kv("  pitch 추정 여부(pitch_assumed)", cam.pitch_assumed)
    rep.kv("  중심(cx, cy) / 출처", f"({_fmt(cam.cx)}, {_fmt(cam.cy)}) / {cam.source}")
    rep.kv("  die 맵 칸 수(cells)", None if cam.cells is None else len(cam.cells))
    if geom is not None:
        rep.kv("  유도 col_origin (중심식)", wg._col_origin(cx_, dia, geom.pitch_x))
        rep.kv("  유도 row_total (중심식)", wg._row_total(cy_, dia, geom.pitch_y))
        rep.kv("  die 맵 (col_origin,row_total)",
               wg._die_map_origins(folder, geom.pitch_x, geom.pitch_y))
        if cam.pitch_assumed:
            rep.find(f"{name}: pitch 를 파일이 아니라 **이 슬롯 사진들의 최댓값**으로 추정 "
                     f"({geom.pitch_x:.1f}, {geom.pitch_y:.1f}) — 슬롯마다 사진이 달라 "
                     f"격자가 슬롯마다 달라진다(증상 1의 유력 원인)")
        if cam.source == wm.SOURCE_ASSUMED:
            rep.find(f"{name}: 웨이퍼 중심이 파일에 없어 **가정**(col_origin/row_total 로 역산) "
                     f"— 최대 ±반 pitch 어긋남, 격자 계산값에 의존")
    frame = wm.frame_for_folder(folder, "camtek")
    rep.kv("frame_for_folder", frame and {
        k: getattr(frame, k) for k in ("diameter", "pitch_x", "pitch_y", "grid_x0", "grid_y0",
                                      "center_source", "kind", "pitch_assumed")})
    rep.kv("frame.die_cells 수", None if frame is None or frame.die_cells is None
           else len(frame.die_cells))
    S.update(geom=geom, cam=cam, frame=frame, est=est, dia=dia)
    _guard(rep, "die 맵", _die_map_section, rep, folder,
           geom.pitch_x if geom else None, geom.pitch_y if geom else None)

    logs = _take_logs()
    rep.h2(f"앱 로그(이 슬롯 처리 중) {len(logs)}줄")
    rep.w(*[f"  {ln}" for ln in logs] or ["  (없음)"])

    # ── 파일명 x/y 통계 — die 내부 좌표가 맞는가 ─────────────────────────
    rep.h2("파일명 col/row/x/y 통계 — x/y 가 정말 die 내부 좌표인가")
    rows = [(p, v) for p, v in live.items() if v is not None]
    if rows:
        cols = [v.col for _, v in rows]
        rws = [v.row for _, v in rows]
        xs = [v.x for _, v in rows]
        ys = [v.y for _, v in rows]
        rep.kv("col 범위 / 분포(상위)", f"{min(cols)}..{max(cols)} / {Counter(cols).most_common(12)}")
        rep.kv("row 범위 / 분포(상위)", f"{min(rws)}..{max(rws)} / {Counter(rws).most_common(12)}")
        rep.kv("x 범위", f"{min(xs):.1f} .. {max(xs):.1f}")
        rep.kv("y 범위", f"{min(ys):.1f} .. {max(ys):.1f}")
        rep.kv("x 음수 / y 음수 장수", f"{sum(x < 0 for x in xs)} / {sum(y < 0 for y in ys)}")
        rep.kv("extra(크기·면적) 있는 장수", sum(bool(v.extra) for _, v in rows))
        rep.kv("상관 col↔x / row↔y", f"{_corr(cols, xs)} / {_corr(rws, ys)}  "
               f"(+1 에 가까우면 x/y 가 die 내부가 아니라 **절대좌표**일 가능성)")
        rep.kv("상관 col↔y / row↔x", f"{_corr(cols, ys)} / {_corr(rws, xs)}  (축 교환 의심)")
        if geom:
            over_x = sum(x >= geom.pitch_x for x in xs)
            over_y = sum(y >= geom.pitch_y for y in ys)
            rep.kv("x ≥ pitch_x / y ≥ pitch_y 장수", f"{over_x} / {over_y}")
            rep.kv("floor(x/pitch_x) 분포", Counter(math.floor(x / geom.pitch_x) for x in xs)
                   .most_common(10))
            rep.kv("floor(y/pitch_y) 분포", Counter(math.floor(y / geom.pitch_y) for y in ys)
                   .most_common(10))
            if over_x or over_y:
                rep.find(f"{name}: 파일명 x/y 가 pitch 를 넘는 사진 x {over_x}장 / y {over_y}장 "
                         f"— 그 점은 **옆 die 로 밀려** 찍힌다(증상 2·3 후보)")
            # pitch 를 바꿔 봤을 때 — x/y 가 다른 단위(mm·px)인지
            for label, sx in (("x/1000(mm?)", 1000.0), ("x×1000", 0.001)):
                rep.kv(f"  참고 max x 를 {label} 로 보면", f"{max(xs) / sx:.3f}")

    # ── 앱 경로로 평면에 놓고 표시 좌표로 되돌림 ─────────────────────────
    rep.h2("앱 build_map 재현 — 파일명 (col,row) vs 맵에서 실제로 찍힌 칸")
    md = wm.build_map({p: coords[p] for p in used})
    rep.kv("MapData.points / unplaced", f"{len(md.points)} / {len(md.unplaced)}")
    if md.unplaced:
        rep.w("  좌표 못 놓은 사진 예: " + " | ".join(p.name for p in md.unplaced[:10]))
    fr = md.frame
    drawn, drawn_src = _drawn_cells(fr)
    rep.kv("격자 출처 / 칸 수", f"{drawn_src} / {len(drawn)}")
    S.update(drawn=drawn, drawn_src=drawn_src, md=md)
    if fr is None or geom is None:
        rep.w("  (frame/geom 없음 — 표시 좌표 비교 생략)")
        return
    co, rt = geom.col_origin, geom.row_total
    drawn_disp = {_disp_of_cell(kx, ky, co, rt) for kx, ky in drawn}
    S["drawn_disp"] = drawn_disp
    if drawn_disp:
        rep.kv("격자 표시 범위", f"col {min(c for c, _ in drawn_disp)}..{max(c for c, _ in drawn_disp)}, "
               f"row {min(r for _, r in drawn_disp)}..{max(r for _, r in drawn_disp)}")
    table = []
    by_file: dict = {}
    mismatch = outside_die = outside_circle = 0
    shift = Counter()
    for pt in md.points:
        v = live.get(pt.path)
        cell = wm.cell_of(fr, pt.x, pt.y)
        dc, dr = _disp_of_cell(*cell, co, rt) if cell else (None, None)
        fc, frw = (v.col, v.row) if v else (pt.col, pt.row)
        ok = (dc, dr) == (fc, frw)
        in_die = cell in drawn if cell else None
        rr = math.hypot(pt.x, pt.y)
        in_circ = rr <= fr.radius
        mismatch += not ok
        outside_die += in_die is False
        outside_circle += not in_circ
        if not ok and dc is not None:
            shift[(dc - fc, dr - frw)] += 1
        table.append((pt.path.name, coords[pt.path].source if coords.get(pt.path) else "-",
                      fc, frw, v.x if v else None, v.y if v else None,
                      pt.x, pt.y, cell, dc, dr, ok, in_die, in_circ, rr))
        by_file[pt.path] = (fc, frw, dc, dr, cell, pt.x, pt.y)
    S.update(by_file=by_file, live=live, co=co, rt=rt)
    rep.kv("파일명≠찍힌 칸", f"{mismatch}/{len(table)}  어긋남(Δcol,Δrow) 분포 {shift.most_common(8)}")
    rep.kv("격자에 없는 칸에 찍힘", f"{outside_die}/{len(table)}")
    rep.kv("웨이퍼 원 밖에 찍힘", f"{outside_circle}/{len(table)}")
    if mismatch:
        rep.find(f"{name}: 파일명 (col,row) 와 맵에 찍힌 칸이 다른 점 {mismatch}장 — "
                 f"분포 {shift.most_common(3)}")
    if outside_die:
        rep.find(f"{name}: die 가 없는 칸에 찍힌 점 {outside_die}장 (증상 2)")

    # 파일명 (col,row) 자체가 격자의 어디에 해당하는가 — 해석 가설 적합도
    rep.h2("해석 가설 적합도 — 파일명 (col,row) 를 이렇게 읽으면 격자 안에 몇 장 드나")
    fn_cr = [(v.col, v.row) for v in live.values() if v is not None]
    if drawn_disp and fn_cr:
        cmax = max(c for c, _ in drawn_disp)
        rmax = max(r for _, r in drawn_disp)
        cmin = min(c for c, _ in drawn_disp)
        rmin = min(r for _, r in drawn_disp)
        hyps = {
            "그대로 (col,row)": lambda c, r: (c, r),
            "축 교환 (row,col)": lambda c, r: (r, c),
            "row 뒤집기 (col, rmax+rmin−row)": lambda c, r: (c, rmax + rmin - r),
            "col 뒤집기 (cmax+cmin−col, row)": lambda c, r: (cmax + cmin - c, r),
            "180° (둘 다 뒤집기)": lambda c, r: (cmax + cmin - c, rmax + rmin - r),
            "1-based (col−1,row−1)": lambda c, r: (c - 1, r - 1),
            "교환+row 뒤집기": lambda c, r: (r, rmax + rmin - c),
            "교환+col 뒤집기": lambda c, r: (cmax + cmin - r, c),
        }
        for label, f in hyps.items():
            n_in = sum(f(c, r) in drawn_disp for c, r in fn_cr)
            rep.w(f"  {label:<36} {n_in:>6}/{len(fn_cr)}  ({100 * n_in / len(fn_cr):5.1f}%)")
        best = []
        for dc in range(-20, 21):
            for dr in range(-20, 21):
                n_in = sum((c + dc, r + dr) in drawn_disp for c, r in fn_cr)
                best.append((n_in, dc, dr))
        best.sort(reverse=True)
        rep.w("  평행이동 (col+Δc,row+Δr) 상위 6: " + ", ".join(
            f"Δ({dc:+d},{dr:+d})→{n}" for n, dc, dr in best[:6]))
        S["fit"] = {label: sum(f(c, r) in drawn_disp for c, r in fn_cr)
                    for label, f in hyps.items()}

    # ASCII 맵 두 장
    rep.h2("ASCII 맵 — (A) 앱이 찍은 칸  (B) 파일명 (col,row) 그대로")
    marks_a = Counter((t[9], t[10]) for t in table if t[9] is not None)
    marks_b = Counter(fn_cr)
    rep.w(*_ascii_map(drawn_disp, marks_a, f"A: {name} 앱 화면 재현(격자={drawn_src})"))
    rep.w("")
    rep.w(*_ascii_map(drawn_disp, marks_b, f"B: {name} 파일명 col/row 그대로"))

    # 포커스 칸
    rep.h2(f"포커스 칸 {focus} — 관련 사진 전부 추적")
    for fc_, fr_ in focus:
        hits = [t for t in table if (t[2], t[3]) == (fc_, fr_)]
        there = [t for t in table if (t[9], t[10]) == (fc_, fr_)]
        rep.w(f"  ▶ 파일명이 ({fc_},{fr_}) 인 사진 {len(hits)}장 / 맵에서 ({fc_},{fr_}) 칸에 "
              f"찍힌 사진 {len(there)}장 / 그 칸이 격자에 있나: {(fc_, fr_) in drawn_disp}")
        for t in (hits + [t for t in there if t not in hits])[:15]:
            nm = t[0]
            rep.w(f"     {nm}")
            rep.w(f"        토큰={nm.rsplit('.', 1)[0].split('_')}")
            rep.w(f"        파일명 col,row=({t[2]},{t[3]}) x,y=({_fmt(t[4])},{_fmt(t[5])})  "
                  f"평면=({t[6]:.1f},{t[7]:.1f}) r={t[14] / 1000:.2f}mm  칸={t[8]}  "
                  f"→ 표시=({t[9]},{t[10]})  일치={t[11]}  격자안={t[12]}")
            if geom:
                sx = (t[2] + co) * geom.pitch_x + (t[4] or 0)
                sy = (rt - t[3]) * geom.pitch_y + (t[5] or 0)
                rep.w(f"        stage sx=({t[2]}+{co})·{geom.pitch_x:.3f}+{_fmt(t[4])}={sx:.1f}  "
                      f"sy=({rt}−{t[3]})·{geom.pitch_y:.3f}+{_fmt(t[5])}={sy:.1f}  "
                      f"cx,cy=({_fmt(cam.cx)},{_fmt(cam.cy)})")
            if t[9] is not None:
                same_row = sorted(c for c, r in drawn_disp if r == t[10])
                rank = sum(c < t[9] for c in same_row)
                rep.w(f"        그 행(row {t[10]})의 격자: col {same_row[:1]}..{same_row[-1:]} "
                      f"— 행 왼쪽 끝 die 부터 세면 {rank}번째(0부터)")
            # 화면 회전(노치 방향)별로 '눈으로 센' 칸 — 보기 상태라 저장 안 됨
            if drawn and fr.pitch_x:
                rep.w("        화면 회전별 '왼쪽 아래 기준 몇 번째 칸' 으로 보이나:")
                for rot in range(4):
                    vis = _visual_cell(fr, drawn, t[6], t[7], rot)
                    rep.w(f"          rot {rot} (노치 {['아래', '왼쪽', '위', '오른쪽'][rot]}): "
                          f"왼쪽에서 {vis[0]}, 아래에서 {vis[1]}  (0부터)")

    # 사진별 전체 표
    rep.h2("사진별 전체 표 (탭 구분 — 엑셀에 붙여 넣기 가능)")
    rep.w("  파일\t소스\tfn_col\tfn_row\tfn_x\tfn_y\tplane_x\tplane_y\tcell\tmap_col\tmap_row"
          "\t일치\t격자안\t원안\tr_um")
    for t in table:
        rep.w("  " + "\t".join(_fmt(v) if isinstance(v, float) else str(v) for v in t))


def _rot(x: float, y: float, rot: int) -> tuple[float, float]:
    rot %= 4
    if rot == 1:
        return y, -x
    if rot == 2:
        return -x, -y
    if rot == 3:
        return -y, x
    return x, y


def _visual_cell(fr, drawn: set, x: float, y: float, rot: int) -> tuple:
    """회전한 화면에서 그 점이 격자 왼쪽 아래로부터 몇 번째 칸으로 보이나."""
    px, py = fr.pitch_x, fr.pitch_y
    if rot % 2:
        px, py = py, px
    centers = []
    for kx, ky in drawn:
        cxp = fr.grid_x0 + (kx + 0.5) * fr.pitch_x
        cyp = fr.grid_y0 + (ky + 0.5) * fr.pitch_y
        centers.append(_rot(cxp, cyp, rot))
    rx, ry = _rot(x, y, rot)
    minx = min(c[0] for c in centers) - px / 2
    miny = min(c[1] for c in centers) - py / 2
    return math.floor((rx - minx) / px), math.floor((ry - miny) / py)


# ---------------------------------------------------------------------------
# 슬롯 간 비교 / LOT 합산
# ---------------------------------------------------------------------------
def _compare_slots(rep: Report, summary: dict, focus) -> None:
    rep.h1("슬롯 간 비교 — 증상 1(slot 을 바꾸면 격자가 달라진다)")
    S = [s for s in summary.values() if s.get("geom") is not None]
    if not S:
        rep.w("  (기하를 가진 슬롯 없음)")
        return
    hdr = ("슬롯", "사진", "pitch_x", "pitch_y", "pitch추정", "col_origin", "row_total",
           "cx", "cy", "중심출처", "직경", "격자출처", "격자칸", "기하출처")
    rep.w("  " + "\t".join(hdr))
    cols = defaultdict(set)
    for s in S:
        g, c = s["geom"], s["cam"]
        row = (s["name"], s["n"], f"{g.pitch_x:.3f}", f"{g.pitch_y:.3f}", c.pitch_assumed,
               g.col_origin, g.row_total, _fmt(c.cx), _fmt(c.cy), c.source, s["dia"],
               s.get("drawn_src"), len(s.get("drawn") or ()), g.source)
        rep.w("  " + "\t".join(str(v) for v in row))
        for h, v in zip(hdr[2:], row[2:]):
            cols[h].add(str(v))
    diff = [h for h, vs in cols.items() if len(vs) > 1]
    if diff:
        rep.find("슬롯마다 다른 값: " + ", ".join(f"{h}({len(cols[h])}종)" for h in diff)
                 + " — 같은 LOT 이면 die 격자는 같아야 한다")
    # 격자 칸 집합(표시 좌표) 비교
    sets = {s["name"]: frozenset(s.get("drawn_disp") or ()) for s in S}
    uniq = Counter(sets.values())
    rep.kv("서로 다른 격자(표시좌표) 종류", len(uniq))
    if len(uniq) > 1:
        base_name = S[0]["name"]
        for nm, st in sets.items():
            a, b = sets[base_name], st
            rep.w(f"  {nm:<30} 칸 {len(b):>5}  vs [{base_name}]: 이쪽에만 {len(b - a)}, "
                  f"저쪽에만 {len(a - b)}")
    # 같은 표시 die 가 슬롯마다 평면 어디에 놓이나
    rep.h2("같은 die (col,row) 가 슬롯마다 평면 어디에 그려지나 (die 왼아래 모서리, µm)")
    for fc, frw in focus:
        for s in S:
            g, c = s["geom"], s["cam"]
            if c.cx is None:
                continue
            sx = (fc + g.col_origin) * g.pitch_x
            sy = (g.row_total - frw) * g.pitch_y
            rep.w(f"  ({fc},{frw}) [{s['name']}] 평면 x={sx - c.cx:.1f}  y={c.cy - sy:.1f}")
    # 적합도 비교
    fits = [(s["name"], s.get("fit")) for s in S if s.get("fit")]
    if fits:
        rep.h2("해석 가설 적합도 — 슬롯별 (격자 안에 드는 장수)")
        labels = list(fits[0][1])
        rep.w("  슬롯\t" + "\t".join(labels))
        for nm, f in fits:
            rep.w(f"  {nm}\t" + "\t".join(str(f[k]) for k in labels))


def _lot_combined(rep: Report, slots: dict[str, Path]) -> None:
    rep.h1("LOT 합산(설정 화면 '전체' · 결과 화면 '전체(LOT 합산)') 재현")
    paths = []
    for n in sorted(slots):
        paths += _list_imgs(slots[n])[0]
    coords = C.resolve_batch(paths)
    md = wm.build_map(coords)
    rep.kv("점 / 못 놓은 사진", f"{len(md.points)} / {len(md.unplaced)}")
    first = next((p for p, c in coords.items() if c is not None
                  and wm.to_plane(c, p.parent) is not None), None)
    rep.kv("격자·원을 정한 폴더(첫 번째로 놓인 사진의 폴더)", first.parent if first else None)
    rep.w("  ※ build_map 은 격자를 **첫 폴더의 frame 하나**로만 그리고, 각 점은 **자기 폴더의")
    rep.w("    frame** 으로 평면에 놓는다 → 슬롯마다 frame 이 다르면 다른 슬롯 점이 격자와 어긋난다.")
    drawn, src = _drawn_cells(md.frame)
    by_folder = defaultdict(lambda: [0, 0])
    for pt in md.points:
        cell = wm.cell_of(md.frame, pt.x, pt.y) if md.frame else None
        by_folder[pt.path.parent.name][0] += 1
        by_folder[pt.path.parent.name][1] += bool(cell is not None and cell not in drawn)
    rep.kv("격자 출처", src)
    for nm, (n, out) in sorted(by_folder.items()):
        rep.w(f"  {nm:<30} 점 {n:>6}  격자 밖 {out:>6}")
        if out:
            rep.find(f"LOT 합산: [{nm}] 점 {out}/{n} 이 첫 폴더 격자 밖에 찍힌다")
    rep.w(*[f"  {ln}" for ln in _take_logs()])


# ---------------------------------------------------------------------------
# LOT 폴더(와 부모)의 부가 파일 — die 크기 단서가 여기 있을 수 있다
# ---------------------------------------------------------------------------
_CLUE_PAT = re.compile(
    r"die\s*_?(step|pitch|size|index)|diepitch|dieindex|diestep|pitch|step_?[xy]|"
    r"index_?[xy]|[xy]_?index|center|diameter|wafer\s*size|samplesize|notch|flat|"
    r"col|row|x_?size|y_?size", re.I)
_SIDE_MAX_FILES = 60
_SIDE_MAX_BYTES = 8 * 1024 * 1024


def _lot_side_files(rep: Report, target: Path, slots: dict[str, Path]) -> None:
    rep.h1("LOT 폴더·부모의 부가 파일 (.txt/.csv/.lot 등) — die 크기·배치 단서")
    dirs = [target, *[d for d in _search_dirs(target)[1:]]]
    for lvl, d in enumerate(dirs):
        try:
            files = sorted((p for p in d.iterdir() if p.is_file() and not _is_image(p.name)),
                           key=lambda p: p.name.lower())
        except OSError as e:
            rep.w(f"  {d}: 열거 실패 {e}")
            continue
        rep.h2(f"{'대상 폴더' if lvl == 0 else f'부모 {lvl}단계'} {d} — 비-사진 파일 {len(files)}개")
        files = [p for p in files if p.suffix.lower() not in (".py", ".pyc")
                 and not p.name.startswith("wafer_map_진단_")]
        for p in files[:_SIDE_MAX_FILES]:
            rep.w(f"  - {p.name}  ({_stat(p)})")
            try:
                size = p.stat().st_size
                with p.open("rb") as fh:
                    head = fh.read(8192)
            except OSError:
                continue
            if size > _SIDE_MAX_BYTES or not _looks_text(head):
                continue
            _DUMPED.setdefault(p, "LOT 부가")
            txt, enc, _ = _read_text(p)
            lines = txt.splitlines()
            hits = [(i, ln.strip()) for i, ln in enumerate(lines, 1) if _CLUE_PAT.search(ln)]
            rep.w(f"      {enc}, {len(lines)}줄, 단서 줄 {len(hits)}개"
                  + (" — 처음 25개:" if hits else ""))
            for i, ln in hits[:25]:
                rep.w(f"        L{i}: {ln[:200]}")
            # 슬롯명이 이름에 든 파일이면 그 슬롯과 연결해 둔다
            for nm in slots:
                if nm.lower() in p.name.lower():
                    rep.w(f"      ↳ 슬롯 [{nm}] 과 이름이 같다")
        if len(files) > _SIDE_MAX_FILES:
            rep.w(f"  … 외 {len(files) - _SIDE_MAX_FILES}개 생략")


# ---------------------------------------------------------------------------
# 단독 재현 — 앱 코드 없이 'LIVE 전용 폴더' 경로를 그대로 계산한다
# ---------------------------------------------------------------------------
# 앱 상수(models.py)와 같은 값 — 기하 파일이 하나도 없는 폴더에서 앱이 쓰는 폴백.
_SIM_DIA = 300000.0           # DEFAULT_WAFER_DIAMETER
_SIM_COL_ORIGIN = 2           # CAMTEK_COL_OFFSET (Center_X 미기록 폴백)
_SIM_MIN_PITCH, _SIM_MAX_PITCH = 100.0, 500000.0
# 참고 비교용: 저장소 실물 die 맵(GX57001305, T254) 의 DieStep — **다른 웨이퍼 값(가정)**.
_SIM_T254 = (14400.1, 31109.8)


def _parse_live(stem: str):
    """camtek_live.parse_live_name 과 같은 규칙(앱 없이)."""
    toks = stem.split("_")
    at = next((i for i in range(len(toks) - 1)
               if _INT.match(toks[i]) and _INT.match(toks[i + 1])), None)
    if at is None or at < 2:
        return None
    col, row = int(toks[at]), int(toks[at + 1])
    if col < 0 or row < 0:
        return None
    nums = [float(t) for t in toks[at + 2:] if _NUM.match(t)]
    if len(nums) < 2:
        return None
    return col, row, nums[0], nums[1]


class _SimFrame:
    """wafer_map.WaferFrame 과 같은 속성 — _drawn_cells/_visual_cell 에 그대로 넣는다."""
    die_cells = None

    def __init__(self, px, py, gx0, gy0, dia=_SIM_DIA):
        self.pitch_x, self.pitch_y, self.grid_x0, self.grid_y0 = px, py, gx0, gy0
        self.diameter = dia

    @property
    def radius(self):
        return self.diameter / 2.0


def _sim_geom(px: float, py: float) -> dict:
    """wafer_map._camtek 의 'LIVE 전용 + 중심 없음' 분기와 같은 계산."""
    rt = math.ceil(_SIM_DIA / py)                         # _row_total(None, …)
    co = _SIM_COL_ORIGIN                                  # _col_origin(None, …)
    cx = (co * px - px / 2.0) + _SIM_DIA / 2.0            # _assumed_edge + D/2
    cy = (rt + 1) * py + py / 2.0 - _SIM_DIA / 2.0
    return {"px": px, "py": py, "co": co, "rt": rt, "cx": cx, "cy": cy,
            "frame": _SimFrame(px, py, -cx, cy)}


def _sim_plane(g: dict, col, row, x, y) -> tuple[float, float]:
    sx = (col + g["co"]) * g["px"] + x
    sy = (g["rt"] - row) * g["py"] + y
    return sx - g["cx"], g["cy"] - sy


def _sim_cell(g: dict, px_, py_) -> tuple[int, int]:
    fr = g["frame"]
    return (math.floor((px_ - fr.grid_x0) / fr.pitch_x),
            math.floor((py_ - fr.grid_y0) / fr.pitch_y))


def _sim_estimate(names: list[str]):
    """wafer_map._live_pitch_estimate — 사진 x/y 최댓값 × 1.05."""
    pr = [v for v in (_parse_live(Path(n).stem) for n in names) if v]
    if not pr:
        return None
    px, py = max(v[2] for v in pr) * 1.05, max(v[3] for v in pr) * 1.05
    if not (_SIM_MIN_PITCH <= px <= _SIM_MAX_PITCH and _SIM_MIN_PITCH <= py <= _SIM_MAX_PITCH):
        return None
    return px, py


def _sim_scenario(rep: Report, label: str, slots: dict, recs: dict, pitch_of, focus) -> None:
    rep.h2(label)
    geoms = {}
    rep.w("  슬롯\t사진\tpitch_x\tpitch_y\tcol_origin\trow_total\tcx\tcy\t격자칸\t"
          "격자밖\t원밖\t격자 col범위\t격자 row범위")
    for nm in slots:
        pr = recs[nm]
        pt = pitch_of(nm)
        if pt is None or not pr:
            rep.w(f"  {nm}\t{len(pr)}\t(pitch 없음 → 앱은 이 슬롯 점을 '좌표 없음' 처리)")
            continue
        g = _sim_geom(*pt)
        drawn, _ = _drawn_cells(g["frame"])
        disp = {_disp_of_cell(kx, ky, g["co"], g["rt"]) for kx, ky in drawn}
        out_g = out_c = 0
        for col, row, x, y, _n in pr:
            p = _sim_plane(g, col, row, x, y)
            out_g += _sim_cell(g, *p) not in drawn
            out_c += math.hypot(*p) > _SIM_DIA / 2
        g.update(drawn=drawn, disp=disp)
        geoms[nm] = g
        rep.w(f"  {nm}\t{len(pr)}\t{pt[0]:.1f}\t{pt[1]:.1f}\t{g['co']}\t{g['rt']}\t"
              f"{g['cx']:.0f}\t{g['cy']:.0f}\t{len(drawn)}\t{out_g}\t{out_c}\t"
              f"{min(c for c, _ in disp) if disp else '-'}..{max(c for c, _ in disp) if disp else '-'}\t"
              f"{min(r for _, r in disp) if disp else '-'}..{max(r for _, r in disp) if disp else '-'}")
    if not geoms:
        return
    shapes = {frozenset(g["disp"]) for g in geoms.values()}
    rep.kv("슬롯별 격자 모양 종류", len(shapes))
    # 포커스 칸
    for fc, frw in focus:
        rep.w(f"  ▶ ({fc},{frw}) 추적")
        for nm, g in geoms.items():
            for col, row, x, y, n in recs[nm]:
                if (col, row) != (fc, frw):
                    continue
                p = _sim_plane(g, col, row, x, y)
                cell = _sim_cell(g, *p)
                dc, dr = _disp_of_cell(*cell, g["co"], g["rt"])
                same_row = sorted(c for c, r in g["disp"] if r == dr)
                rank = sum(c < dc for c in same_row)
                vis = [_visual_cell(g["frame"], g["drawn"], p[0], p[1], rot) for rot in range(4)]
                rep.w(f"     [{nm}] {n}")
                rep.w(f"        평면=({p[0]:.0f},{p[1]:.0f}) r={math.hypot(*p) / 1000:.1f}mm  "
                      f"칸표시=({dc},{dr}) 격자안={cell in g['drawn']}  "
                      f"그 행 격자 col {same_row[:1]}..{same_row[-1:]} → 왼쪽 끝부터 {rank}번째")
                rep.w("        화면에서 '왼쪽 아래 기준 칸'(0부터) rot0/1/2/3 = "
                      + " / ".join(f"({a},{b})" for a, b in vis))
    # 같은 die 의 평면 위치가 슬롯마다
    rep.w("  ▶ 같은 die 의 평면 위치(die 왼아래 모서리, mm) — 슬롯마다 같아야 정상")
    for fc, frw in focus:
        rep.w(f"     ({fc},{frw}): " + "  ".join(
            f"{nm}=({_sim_plane(g, fc, frw, 0, 0)[0] / 1000:.1f},"
            f"{_sim_plane(g, fc, frw, 0, 0)[1] / 1000:.1f})" for nm, g in geoms.items()))
    # 슬롯별 ASCII (앱 화면 재현)
    for nm, g in geoms.items():
        marks = Counter()
        for col, row, x, y, _n in recs[nm]:
            marks[_disp_of_cell(*_sim_cell(g, *_sim_plane(g, col, row, x, y)),
                                g["co"], g["rt"])] += 1
        rep.w(*_ascii_map(g["disp"], marks, f"{nm} — {label}"))
    # LOT 합산 — 격자는 첫 슬롯 frame, 점은 각자 frame (build_map 동작)
    first = next(iter(geoms))
    g0 = geoms[first]
    marks = Counter()
    out = Counter()
    for nm, g in geoms.items():
        for col, row, x, y, _n in recs[nm]:
            cell = _sim_cell(g0, *_sim_plane(g, col, row, x, y))
            d = _disp_of_cell(*cell, g0["co"], g0["rt"])
            marks[d] += 1
            out[nm] += cell not in g0["drawn"]
    rep.w(f"  LOT 합산(격자=[{first}] 기준) 격자 밖 점: {dict(out)}")
    rep.w(*_ascii_map(g0["disp"], marks, f"LOT 합산 — {label}"))


def _standalone_sim(rep: Report, slots: dict[str, Path], focus) -> None:
    rep.h1("단독 재현 — 앱 코드 없이 'LIVE 전용 폴더' 맵 계산을 그대로 흉내 낸다")
    rep.w("  전제: 슬롯·부모 어디에도 Params_WaferInfo.ini / ProductInfo.ini / Wafer2Table.ini /",
          "  s_DieLocation.dat / ColorImageGrabingInfo.ini 가 없을 때 앱이 타는 경로:",
          "   · pitch  = 그 슬롯 사진 파일명 x/y 최댓값 × 1.05   (wafer_map._live_pitch_estimate)",
          f"   · col_origin = {_SIM_COL_ORIGIN} (상수 폴백), row_total = ceil(직경/pitch_y)",
          "   · 웨이퍼 중심 = 위 두 값으로 역산(가정),  직경 300 mm,  격자 = 원 안에 온전히 드는 칸",
          "  ※ 기하 파일이 있는 폴더라면 이 섹션은 무시하고 슬롯 섹션(앱 코드 재현)을 본다.")
    recs: dict[str, list] = {}
    all_names = []
    folder_names: dict[str, list[str]] = {}
    for nm, folder in slots.items():
        try:
            # 앱 추정(_live_pitch_estimate)은 사진만이 아니라 폴더의 **모든 항목** 이름을 본다
            names = sorted(p.name for p in folder.iterdir())
        except OSError:
            names = []
        folder_names[nm] = names
        all_names += names
        rows = []
        for n in names:
            if not _is_image(n):
                continue
            v = _parse_live(Path(n).stem)
            if v:
                rows.append((*v, n))
        recs[nm] = rows
    rep.h2("슬롯별 파일명 좌표 요약")
    rep.w("  슬롯\t사진\tcol범위\trow범위\tx최대\ty최대\tx최소\ty최소")
    for nm, pr in recs.items():
        if not pr:
            rep.w(f"  {nm}\t0")
            continue
        rep.w(f"  {nm}\t{len(pr)}\t{min(v[0] for v in pr)}..{max(v[0] for v in pr)}\t"
              f"{min(v[1] for v in pr)}..{max(v[1] for v in pr)}\t"
              f"{max(v[2] for v in pr):.1f}\t{max(v[3] for v in pr):.1f}\t"
              f"{min(v[2] for v in pr):.1f}\t{min(v[3] for v in pr):.1f}")
    allx = [v[2] for pr in recs.values() for v in pr]
    ally = [v[3] for pr in recs.values() for v in pr]
    if allx:
        rep.kv("LOT 전체 x 최대 / y 최대", f"{max(allx):.1f} / {max(ally):.1f}")
        # die 내부 좌표 분포 — 실제 pitch 의 하한·형태를 본다(10등분 히스토그램)
        for axis, vals in (("x", allx), ("y", ally)):
            hi = max(vals)
            bins = Counter(min(9, int(v / hi * 10)) for v in vals) if hi > 0 else Counter()
            rep.kv(f"LOT 전체 {axis} 분포(0..max 10등분)", [bins.get(i, 0) for i in range(10)])
    per_slot = {nm: _sim_estimate(folder_names[nm]) for nm in recs}
    est_lot = _sim_estimate(all_names)
    est = {nm: v for nm, v in per_slot.items()}
    _sim_scenario(rep, "A. 앱 현재 동작 — 슬롯마다 자기 사진으로 pitch 추정", slots, recs,
                  lambda nm: est[nm], focus)
    if est_lot:
        _sim_scenario(rep, f"B. 비교 — LOT 전체 사진으로 pitch 하나 추정 "
                           f"({est_lot[0]:.1f}, {est_lot[1]:.1f})", slots, recs,
                      lambda nm: est_lot, focus)
    _sim_scenario(rep, f"C. 비교 — 저장소의 T254 실물(GX57001305) DieStep {_SIM_T254} "
                       f"(다른 웨이퍼 값 · 가정)", slots, recs, lambda nm: _SIM_T254, focus)
    bad = [nm for nm, v in per_slot.items() if v is not None]
    if len({per_slot[nm] for nm in bad}) > 1:
        rep.find("단독 재현: 기하 파일이 없어 **슬롯마다 사진 최댓값으로 pitch 를 따로 추정** — "
                 "추정값이 " + ", ".join(f"{nm}=({per_slot[nm][0]:.0f},{per_slot[nm][1]:.0f})"
                                       for nm in bad)
                 + " 로 제각각이라 슬롯을 바꾸면 격자가 바뀐다(증상 1)")


# ---------------------------------------------------------------------------
# 부록 — 원재료 텍스트 전문
# ---------------------------------------------------------------------------
def _appendix(rep: Report) -> None:
    rep.h1(f"부록 — 원재료 파일 원문 ({len(_DUMPED)}개, 중복 제거)")
    seen_sha: dict[str, Path] = {}
    for p in sorted(_DUMPED, key=str):
        rep.h2(str(p))
        try:
            with p.open("rb") as fh:
                head = fh.read(8192)
        except OSError as e:
            rep.w(f"  읽기 실패: {e}")
            continue
        if not _looks_text(head):
            rep.w(f"  (바이너리 — {_stat(p)}, 앞 64바이트 hex) {head[:64].hex(' ')}")
            continue
        sha = _sha(p)
        if sha in seen_sha:
            rep.w(f"  (내용이 {seen_sha[sha]} 와 동일 — sha {sha})")
            continue
        seen_sha[sha] = p
        big = p.name.lower().startswith("colorimagegrabing")
        txt, enc, cut = _read_text(p)
        lines = txt.splitlines()
        rep.w(f"  인코딩 {enc}, {len(lines)}줄{' (잘림)' if cut else ''}")
        show = lines[:_HEAD_LINES] if (big or len(lines) > 3000) else lines
        rep.w(*["  | " + ln for ln in show])
        if len(show) < len(lines):
            rep.w(f"  | … (이하 {len(lines) - len(show)}줄 생략)")


# ---------------------------------------------------------------------------
def _env(rep: Report, targets: list[Path], out: Path) -> None:
    rep.h1("실행 환경")
    rep.kv("시각", _dt.datetime.now().isoformat(timespec="seconds"))
    rep.kv("파이썬", f"{sys.version.split()[0]} ({sys.executable})")
    rep.kv("OS", platform.platform())
    rep.kv("스크립트", _HERE)
    rep.kv("앱 코드 루트", _REPO)
    rep.kv("앱 코드 import", "성공" if APP else "실패 — '단독 재현' 섹션이 대신 앱 계산을 흉내 낸다")
    for ln in _APP_SEARCH_LOG:
        rep.w(f"   {ln}")
    if not APP:
        rep.w(*["   " + ln for ln in _APP_IMPORT_ERR.splitlines()])
    for cand in ([_REPO / "VERSION", _REPO.parent / "VERSION", _REPO / "app" / "VERSION"]
                 if _REPO else []):
        if cand.exists():
            rep.kv(f"VERSION ({cand})", _read_text(cand)[0].strip()[:200])
    if _REPO and (_REPO / ".git").exists():
        try:
            head = (_REPO / ".git" / "HEAD").read_text().strip()
            rep.kv(".git HEAD", head)
            if head.startswith("ref:"):
                ref = _REPO / ".git" / head.split()[1]
                if ref.exists():
                    rep.kv("커밋", ref.read_text().strip())
        except OSError:
            pass
    rep.kv("진단 대상", [str(t) for t in targets])
    rep.kv("출력 파일", out)
    rep.w("", "  참고: 화면 회전(노치 방향)은 저장되지 않는 보기 상태다. 맵을 돌려 둔 채 칸을 세면",
          "  (3,3) 이 다른 칸처럼 보인다 — 포커스 칸 섹션의 '화면 회전별' 줄로 확인한다.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folders", nargs="*", default=[DEFAULT_TARGET])
    ap.add_argument("--out", default=DEFAULT_OUT_DIR, help="출력 폴더")
    ap.add_argument("--app", default=None,
                    help="앱 코드 폴더(aoi_verification 이 든 폴더). 안 주면 자동으로 찾는다")
    ap.add_argument("--focus", action="append", default=None,
                    help="추적할 die 'col,row' (여러 번 가능, 기본 3,3 과 9,3)")
    a = ap.parse_args(argv)
    focus = []
    for s in (a.focus or DEFAULT_FOCUS):
        c, r = s.split(",")
        focus.append((int(c), int(r)))

    out_dir = Path(a.out)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        out_dir = Path.cwd()
    out = out_dir / f"wafer_map_진단_{_dt.datetime.now():%Y%m%d_%H%M%S}.txt"
    rep = Report(out)
    targets = [Path(f) for f in a.folders]
    print(f"진단 중… 결과: {out}")
    try:
        _guard(rep, "환경", _env, rep, targets, out)
        for target in targets:
            rep.h1(f"대상 폴더 {target}")
            rep.kv("존재", target.exists())
            if not target.exists():
                rep.find(f"{target} 에 접근할 수 없음 (네트워크 드라이브 연결/권한 확인)")
                continue
            _guard(rep, "트리", _tree, rep, target)
            kind, slots = _guard(rep, "분류", _classify, rep, target) or ("empty", {})
            rep.kv("앱 기준 분류", {"slot": "슬롯 폴더(사진이 바로 있음)",
                                  "lot": f"LOT 폴더(슬롯 {len(slots)}개)",
                                  "deep": f"앱은 '비어 있음' 으로 봄 — 더 깊은 곳에 사진 폴더 {len(slots)}개",
                                  "empty": "사진 없음"}[kind])
            rep.kv("슬롯 순서(앱과 같은 정렬)", list(slots))
            _guard(rep, "LOT 부가 파일", _lot_side_files, rep, target, slots)
            _guard(rep, "단독 재현", _standalone_sim, rep, slots, focus)
            summary: dict = {}
            for i, (nm, folder) in enumerate(slots.items(), 1):
                print(f"  [{i}/{len(slots)}] {nm}")
                _guard(rep, f"슬롯 {nm}", _diagnose_slot, rep, nm, folder, focus, summary)
                rep.fh.flush()
            if len(summary) > 1:
                _guard(rep, "슬롯 비교", _compare_slots, rep, summary, focus)
            if APP and len(slots) > 1:
                _clear_app_caches()
                _guard(rep, "LOT 합산", _lot_combined, rep, slots)
        _guard(rep, "부록", _appendix, rep)
        rep.h1(f"자동 판정 요약 ({len(rep.findings)}건)")
        rep.w(*[f"  {i}. {m}" for i, m in enumerate(rep.findings, 1)] or ["  (특이사항 없음)"])
    except KeyboardInterrupt:
        rep.w("", "!! 사용자가 중단함 — 여기까지만 기록됨")
    except Exception:                                                   # noqa: BLE001
        rep.w("", "!! 진단 전체가 예외로 중단됨:", *traceback.format_exc().splitlines())
    finally:
        rep.close()
    print(f"완료: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
