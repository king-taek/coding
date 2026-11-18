"""Scan 연결이 왜 안 되는지 현장 폴더에서 자료를 모으는 개발자용 도구(Qt 없음).

``collect(color_path)`` 가 같은 폴더의 목록·INI·픽셀 크기·Scan 크기·사진별 판정을 모아 zip 하나로 쓴다.
사진(JPEG 등)은 절대 담지 않는다 — 파일 이름·크기만 ``files.txt`` 에 남긴다.
각 구역은 따로 try/except 라 한 곳이 깨져도 나머지가 남고, 오류는 글로 적는다.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import abs_coord, camtek_ini, pixel_size, scan_image
from .ini_text import decode_ini_bytes, read_ini_text

__all__ = ["collect", "gather"]

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
_MAX_COPY = 20 * 1024 * 1024
_FIELDS = ("RecipeNumber", "X", "Y", "FaultX", "FaultY", "PixelSizeX", "PixelSizeY",
           "OpticName", "Mag", "Type", "ImageSizeX", "ImageSizeY")
_NONE = "없음"
_RECIPE_INIS = ("Recipe*-Params_WaferInfo.ini", "Recipe*-ProductInfo.ini", "Recipe*-RecipesInfo.ini")


def _default_root() -> Path:
    return (Path.home() / "Desktop" / "프로그램 개발중" / "분석용 자료 수집"
            / "AOI 레시피 검증" / datetime.now().strftime("%Y%m%d"))


def _suffix(name: str) -> str:
    """`X.Y.c.ID.N.jpeg` → 'N'(정수일 때), 아니면 '없음'."""
    toks = Path(name).stem.split(".") if "." in name else [name]
    last = toks[-1] if len(toks) > 1 else ""
    return last if last.lstrip("-").isdigit() else _NONE


def _guard(out: dict, key: str, fn):
    try:
        out[key] = fn()
    except Exception as e:                      # 구역 하나가 깨져도 나머지는 남긴다
        out[key] = {"error": f"{type(e).__name__}: {e}"}


def _listing(folder: Path) -> list[tuple[str, int]]:
    out = []
    for p in sorted(folder.iterdir(), key=lambda q: q.name.casefold()):
        try:
            out.append((p.name, p.stat().st_size if p.is_file() else -1))
        except OSError:
            out.append((p.name, -1))
    return out


def _counts(names) -> dict:
    color, tile = Counter(), Counter()
    for n in names:
        low = n.lower()
        if not low.endswith((".jpeg", ".jpg")):
            continue
        if ".c." in low:
            color[_suffix(n)] += 1
        elif ".t." in low:
            tile[_suffix(n)] += 1
    return {"color": dict(color), "scan_tiles": dict(tile)}


def _manifest(folder: Path, names: set[str]) -> dict:
    p = folder / scan_image.SCAN_LIST_NAME
    if not p.is_file():
        return {"present": False}
    text = decode_ini_bytes(p.read_bytes())
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    status, rows = scan_image.parse_manifest(text)
    real = {n.casefold() for n in names}
    by = defaultdict(lambda: {"rows": 0, "missing": 0, "examples": [], "x": [], "y": []})
    for name, x, y in rows:
        d = by[_suffix(name)]
        d["rows"] += 1
        d["x"].append(x)
        d["y"].append(y)
        if name.casefold() not in real:
            d["missing"] += 1
            if len(d["examples"]) < 10:
                d["examples"].append(name)
    groups = {}
    for k, d in by.items():
        groups[k] = {"rows": d["rows"], "missing_files": d["missing"], "missing_examples": d["examples"],
                     "x_min": min(d["x"]), "x_max": max(d["x"]), "y_min": min(d["y"]), "y_max": max(d["y"])}
    return {"present": True, "first_line": lines[0] if lines else "", "parse_status": status,
            "row_count": len(rows), "by_suffix": groups}


def _kv_of(path: Path, keys) -> dict:
    txt = read_ini_text(path)
    if txt is None:
        return {"present": False}
    out = {"present": True}
    for k in keys:
        m = re.search(r"(?im)^\s*" + re.escape(k) + r"\s*=\s*(.*?)\s*$", txt)
        out[k] = m.group(1) if m else None
    return out


def _pixel(folder: Path) -> dict:
    out = {"scan_pixel_size_xy": pixel_size.scan_pixel_size_xy(folder), "sources": []}
    for rel, kx, ky in pixel_size._SOURCES:
        out["sources"].append({"file": rel, **_kv_of(folder / rel, [k for k in (kx, ky) if k])})
    extra = []
    for pat in _RECIPE_INIS:
        for p in sorted(folder.glob(pat)):
            keys = ("RefPixelSizeX", "RefPixelSizeY", "PixelSize_X", "PixelSize_Y", "Scan2DPixelSize")
            extra.append({"file": p.name, **_kv_of(p, keys)})
    out["recipe_files"] = extra
    return out


def _tile_sizes(folder: Path, names: list[str]) -> dict:
    tiles = [n for n in names if ".t." in n.lower() and n.lower().endswith((".jpeg", ".jpg"))]
    with ThreadPoolExecutor(max_workers=min(scan_image._SIZE_WORKERS, max(1, len(tiles)))) as pool:
        sizes = list(pool.map(lambda n: scan_image._image_size(str(folder / n)), tiles))
    by = defaultdict(Counter)
    for n, wh in zip(tiles, sizes):
        by[_suffix(n)][f"{wh[0]}x{wh[1]}" if wh else "읽기 실패"] += 1
    return {k: dict(v) for k, v in by.items()}


def _ini_fields(folder: Path) -> dict:
    ini = camtek_ini._find_ini(folder)
    if ini is None:
        return {}
    text = read_ini_text(ini) or ""
    parts = camtek_ini._SECTION_PAT.split(text)
    it = iter(parts[1:])
    out = {}
    for name, content in zip(it, it):
        kv = camtek_ini._kv(content)
        out[Path(name.strip()).stem.lower()] = {k: kv.get(k.upper()) for k in _FIELDS}
    return out


def _nearest(xy, entries, pixel_xy) -> dict | None:
    """결함이 어느 Scan 에도 안 들 때, 이미지 범위 밖으로 가장 덜 벗어난 타일."""
    best = None
    for path, center, (w, h), *own in entries:
        px = own[0] if own else pixel_xy                # 레시피별 픽셀 크기(scan_image._with_recipe_px)
        if px is None:
            continue
        u, v = scan_image.scan_uv(xy, center, (w, h), px)
        dx = max(0.0 - u, 0.0, u - (w - 1))
        dy = max(0.0 - v, 0.0, v - (h - 1))
        d = (dx * dx + dy * dy) ** 0.5
        if best is None or d < best["outside_px"]:
            best = {"name": Path(path).name, "suffix": _suffix(Path(path).name),
                    "uv": [round(u, 1), round(v, 1)], "size": [w, h], "outside_px": round(d, 1),
                    "outside_um": round(d * (px[0] + px[1]) / 2.0, 1), "pixel_xy": list(px)}
    return best


def _photos(folder: Path, focus: Path, names: list[str]) -> list[dict]:
    colors = [n for n in names if ".c." in n.lower() and n.lower().endswith((".jpeg", ".jpg"))]
    colors.sort(key=lambda n: (n != focus.name, n.casefold()))
    fields = _ini_fields(folder)
    abs_map = camtek_ini.load_abs_folder(folder)
    status, rows = scan_image._index(str(folder), scan_image._mtime(folder / scan_image.SCAN_LIST_NAME),
                                     scan_image._mtime(folder))
    px = pixel_size.scan_pixel_size_xy(folder)
    out = []
    for n in colors:
        rec = {"name": n, "suffix": _suffix(n), "ini": fields.get(Path(n).stem.lower())}
        try:
            stem = Path(n).stem.lower()
            if stem in abs_map:
                xy, src = abs_map[stem], "ini"
            else:
                xy = abs_coord.dotted_xy(n)
                src = "파일명" if xy else _NONE
            rec["defect_xy"], rec["defect_xy_source"] = (list(xy) if xy else None), src
            m = scan_image.resolve(folder / n)
            rec["status"] = m.status
            if m.best:
                b = m.best
                rec["best"] = {"name": b.path.name, "coverage": round(b.coverage, 3),
                               "uv": [round(b.uv[0], 1), round(b.uv[1], 1)]}
            if m.status == scan_image.NO_CANDIDATE and xy and px and rows:
                rec["nearest_tile"] = _nearest(xy, scan_image._with_recipe_px(folder, rows), px)
        except Exception as e:
            rec["status"] = scan_image.UNREADABLE
            rec["error"] = f"{type(e).__name__}: {e}"
        out.append(rec)
    return out


def _summary(photos) -> dict:
    t: dict = defaultdict(Counter)
    if isinstance(photos, list):
        for p in photos:
            t[p["suffix"]][p.get("status", "?")] += 1
    return {k: dict(v) for k, v in sorted(t.items())}


def gather(color_path) -> dict:
    color = Path(color_path)
    folder = color.parent
    d: dict = {"folder": str(folder), "focus_photo": color.name,
               "created": datetime.now().isoformat(timespec="seconds")}
    files = []
    _guard(d, "files", lambda: files.extend(_listing(folder)) or [list(f) for f in files])
    names = [n for n, _ in files]
    _guard(d, "counts", lambda: _counts(names))
    _guard(d, "manifest", lambda: _manifest(folder, set(names)))
    _guard(d, "pixel_size", lambda: _pixel(folder))
    _guard(d, "scan_tile_sizes", lambda: _tile_sizes(folder, names))
    _guard(d, "photos", lambda: _photos(folder, color, names))
    d["summary"] = _summary(d["photos"])
    return d


def _report(d: dict) -> str:
    L = ["# Scan 진단 보고서", f"폴더: {d['folder']}", f"연 사진: {d['focus_photo']}", f"만든 때: {d['created']}", "",
         "## 요약 (레시피 번호 x 판정)"]
    for k, v in d["summary"].items():
        L.append(f"  레시피 {k}: " + ", ".join(f"{s}={n}" for s, n in sorted(v.items())))
    if not d["summary"]:
        L.append("  (컬러 사진 없음)")
    for title, key in (("사진 개수 (레시피별)", "counts"), ("Scan 목록", "manifest"),
                       ("픽셀 크기", "pixel_size"), ("Scan 타일 JPEG 크기 (레시피별)", "scan_tile_sizes")):
        L += ["", f"## {title}", json.dumps(d[key], ensure_ascii=False, indent=2)]
    L += ["", "## 컬러 사진별 판정"]
    ph = d["photos"]
    if isinstance(ph, dict):
        L.append(json.dumps(ph, ensure_ascii=False))
    else:
        for p in ph:
            L.append(json.dumps(p, ensure_ascii=False))
    return "\n".join(L) + "\n"


def _copy_targets(folder: Path):
    seen = set()
    cands = list(folder.iterdir())
    td = folder / "TrainData"
    if td.is_dir():
        cands += list(td.glob("*.ini"))
    for p in cands:
        try:
            if (not p.is_file() or p.suffix.lower() in _IMAGE_EXTS or p.name.lower() == "thumbs.db"
                    or p.stat().st_size > _MAX_COPY or p in seen):
                continue
        except OSError:
            continue
        seen.add(p)
        yield p


def collect(color_path, out_root=None) -> Path:
    """진단 zip 을 쓰고 그 경로를 돌려준다.  zip 을 못 쓸 때만 예외를 낸다."""
    color = Path(color_path)
    folder = color.parent
    root = Path(out_root) if out_root else _default_root()
    root.mkdir(parents=True, exist_ok=True)
    d = gather(color)
    listing = d["files"] if isinstance(d["files"], list) else []
    out = root / f"Scan진단_{folder.name}_{datetime.now().strftime('%H%M%S')}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.txt", _report(d))
        z.writestr("diag.json", json.dumps(d, ensure_ascii=False, indent=2))
        z.writestr("files.txt", "\n".join(f"{n}\t{s}" for n, s in listing) + "\n")
        try:
            for p in _copy_targets(folder):
                arc = f"copy/TrainData/{p.name}" if p.parent.name == "TrainData" else f"copy/{p.name}"
                z.write(p, arc)
        except OSError:
            pass
    return out
