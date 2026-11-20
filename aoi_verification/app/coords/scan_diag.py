"""Scan 연결이 왜 안 되는지 현장 폴더에서 자료를 모으는 개발자용 도구(Qt 없음).

``collect(color_path)`` 가 같은 폴더의 목록·INI·픽셀 크기·Scan 크기·사진별 판정을 모아 zip 하나로 쓴다.
사진(JPEG 등)은 절대 담지 않는다 — 파일 이름·크기만 ``files.txt`` 에 남긴다.
각 구역은 따로 try/except 라 한 곳이 깨져도 나머지가 남고, 오류는 글로 적는다.

레시피 값 분석용 구역(``recipes``·``export_csv``)도 함께 모은다 — 다중 레시피 폴더에서 레시피마다 Zone 기준 ·
픽셀 크기 · 검출 값(Low_Delta 등)이 다르고, Defect 수치가 어느 픽셀 크기로 환산돼 기준과 비교되는지 따져 보려는 자료다.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import abs_coord, camtek_ini, pixel_size, scan_image
from .ini_text import decode_ini_bytes, read_ini_text

__all__ = ["collect", "collect_many", "gather"]

_SURFACE_KEYS = ("Enable", "Bright_Thr", "Low_Delta", "High_Delta", "VDark_Delta", "BrightArea", "DarkArea",
                 "BrightDiameter", "DarkDiameter", "BrightLength", "DarkLength", "Area", "Diameter", "Length")
_ZONE_DIR = re.compile(r"(?i)^(zones|recipe\d+-zones)$")
_COPY_PARENT_MAX = 100
# 레시피 분석에 안 쓰는데 큰 파일 — WaferAlignData.zip 이 LOT 수집 zip 의 97%(256 MB)였다(관측 2026-11-21).
_SKIP_NAMES = {"waferaligndata.zip"}

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
    # DirEntry 는 열거 때 받은 정보를 쓴다(Windows) — Path.stat() 은 사진 수천 장마다 NAS 왕복이었다.
    out = []
    with os.scandir(folder) as it:
        for e in it:
            try:
                out.append((e.name, e.stat().st_size if e.is_file() else -1))
            except OSError:
                out.append((e.name, -1))
    return sorted(out, key=lambda t: t[0].casefold())


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


def _recipe_names(folder: Path) -> dict:
    """RecipesInfo.ini 의 ``[Recipe-N]``(이름 · 픽셀 크기 · 배율)과 MultiRecipe.ini 의 줄(레시피 순서)."""
    out: dict = {"recipes_info": {}, "multi_recipe": None}
    txt = read_ini_text(folder / "RecipesInfo.ini") or ""
    for m in re.finditer(r"(?ims)^\s*\[Recipe-(\d+)\]\s*$(.*?)(?=^\s*\[|\Z)", txt):
        out["recipes_info"][m.group(1)] = dict(re.findall(
            r"(?m)^\s*(Name|Scan2DPixelSize|ColorPixelSize|ScanMag|VerMag)\s*=\s*(.*?)\s*$", m.group(2)))
    multi = read_ini_text(folder / "MultiRecipe.ini")
    if multi is not None:
        out["multi_recipe"] = [ln.strip() for ln in multi.splitlines() if ln.strip()][:40]
    return out


def _zone_params(path: Path) -> dict:
    kv = _kv_of(path, _SURFACE_KEYS)
    kv.pop("present", None)
    return {k: v for k, v in kv.items() if v is not None}


def _passes(zone, rows) -> int:
    """Zone 묶음 하나라도 전부(AND) 통과한 Defect 수."""
    from ..threshold import rules
    n = 0
    for r in rows:
        vals = {rules.FEATURE_AREA: r.area, rules.FEATURE_WIDTH: r.blob_breadth, rules.FEATURE_CONTRAST: r.contrast}
        if any(all(f.passes(vals[f.feature]) for f in fs if f.feature in vals) for fs in zone.bundles.values()):
            n += 1
    return n


def _scaled(zone, k_area: float, k_width: float):
    """기준 값(px)에 환산 계수를 곱한 Zone — 면적은 면적 계수, 폭은 폭 계수, Contrast 는 그대로."""
    from ..threshold import rules

    def k(f):
        return k_area if f.feature == rules.FEATURE_AREA else k_width if f.feature == rules.FEATURE_WIDTH else 1.0
    return rules.Zone(zone.zone_id, zone.name, {
        c: tuple(rules.Filter(f.feature, f.ftype, f.low * k(f), f.high * k(f)) for f in fs)
        for c, fs in zone.bundles.items()})


def _zone_rows(folder: Path, zones: dict, by: dict, ref) -> list:
    out = []
    for (d, zid), z in sorted(zones.items()):
        m = re.match(r"(?i)recipe(\d+)-", d)
        n = int(m.group(1)) if m else 1
        rows = by.get((n, zid), [])
        px = pixel_size.scan_pixel_size_xy_recipe(folder, n)
        e = {"zone_dir": d, "zone_id": zid, "zone_name": z.name, "recipe": n, "recipe_pixel_xy": list(px) if px else None,
             "filters": {str(c): [[f.feature, f.ftype, f.low, f.high] for f in fs] for c, fs in z.bundles.items()},
             "flt_n": len(rows)}
        ini = folder / d / f"{z.name}.ini"
        e["surface_params"] = _zone_params(ini) if ini.is_file() else {}
        if rows:
            e["flt_range"] = {"area": [min(r.area for r in rows), max(r.area for r in rows)],
                              "width": [min(r.blob_breadth for r in rows), max(r.blob_breadth for r in rows)],
                              "length": [min(r.blob_feret_max for r in rows), max(r.blob_feret_max for r in rows)],
                              "contrast": [min(r.contrast for r in rows), max(r.contrast for r in rows)],
                              "contrast_zero": sum(1 for r in rows if r.contrast == 0)}
            e["pass_raw"] = _passes(z, rows)
            if ref and px:
                # flt 를 폴더 RefPixelSize 로, 기준을 이 레시피 픽셀 크기로 µm 로 바꿔 같은 µm 끼리 비교한다.
                # 기준 px 를 flt 의 px 로 되돌리면 같은 비교다: 기준 × (레시피 픽셀 / Ref 픽셀)^차원.
                e["pass_um"] = _passes(_scaled(z, (px[0] / ref[0]) ** 2, px[0] / ref[0]), rows)
        out.append(e)
    return out


def _recipes(folder: Path) -> dict:
    """레시피마다 Zone 기준(px)과 Surface.flt 수치 · 통과 수를 한 표로 — 기준이 어느 환산에서 맞는지 보는 자료.

    ``pass_raw`` = flt 숫자를 기준 숫자와 그대로 비교, ``pass_um`` = 기준(px)을 그 레시피 픽셀 크기로 µm 로,
    flt 를 폴더 RefPixelSize 로 µm 로 바꿔 같은 µm 끼리 비교(Export Defects.csv 의 환산과 같은 방식).
    장비가 Defect 로 남긴 수(``flt_n``)와 맞는 쪽이 맞는 환산이다."""
    from ..threshold import rules
    from . import surface_flt
    ref = pixel_size.scan_pixel_size_xy(folder)
    recs = surface_flt.load_folder(folder)
    by: dict = defaultdict(list)
    for r in recs:
        by[(r.recipe if r.recipe is not None else 1, r.zone)].append(r)
    return {"names": _recipe_names(folder), "ref_pixel_xy": list(ref) if ref else None, "flt_records": len(recs),
            "zones": _zone_rows(folder, rules.read_zones(folder), by, ref)}


def _export_csv(folder: Path) -> dict:
    """Export 로 생긴 ``Defects.csv`` 요약 — Zone(Type)별 줄 수와 면적 · 폭 · 길이 범위(µm)."""
    import csv
    p = folder / "Defects.csv"
    if not p.is_file():
        return {"present": False}
    rows = list(csv.DictReader(p.read_text(encoding="utf-8-sig", errors="replace").splitlines()))
    by: dict = defaultdict(list)
    for r in rows:
        by[r.get("Type", "")].append(r)
    out = {}
    for t, v in by.items():
        rng = {}
        for k in ("Area", "Width", "Length"):
            try:
                xs = [float(x[k]) for x in v]
                rng[k] = [min(xs), max(xs)]
            except (KeyError, ValueError):
                pass
        out[t] = {"rows": len(v), **rng}
    return {"present": True, "rows": len(rows), "by_type": out}


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


def gather(color_path, scan: bool = True) -> dict:
    """``scan=False`` 면 사진마다 Scan 을 찾는 구역(타일 크기 · 사진별 판정)을 건너뛴다 — NAS 에서 사진
    수천 장을 하나씩 열어 웨이퍼 하나에 수 분이 걸렸다.  레시피 분석(LOT 수집)에는 필요 없다."""
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
    if scan:
        _guard(d, "scan_tile_sizes", lambda: _tile_sizes(folder, names))
        _guard(d, "photos", lambda: _photos(folder, color, names))
    else:
        d["scan_tile_sizes"] = d["photos"] = {"skipped": "레시피 수집(빠른 모드)"}
    _guard(d, "recipes", lambda: _recipes(folder))
    _guard(d, "export_csv", lambda: _export_csv(folder))
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
                       ("픽셀 크기", "pixel_size"), ("Scan 타일 JPEG 크기 (레시피별)", "scan_tile_sizes"),
                       ("레시피별 Zone 기준 · Surface.flt 수치", "recipes"), ("Export Defects.csv", "export_csv")):
        L += ["", f"## {title}", json.dumps(d[key], ensure_ascii=False, indent=2)]
    L += ["", "## 컬러 사진별 판정"]
    ph = d["photos"]
    if isinstance(ph, dict):
        L.append(json.dumps(ph, ensure_ascii=False))
    else:
        for p in ph:
            L.append(json.dumps(p, ensure_ascii=False))
    return "\n".join(L) + "\n"


def _small_files(folder: Path):
    # 사진은 이름으로 먼저 거른다 — 사진마다 stat 하면 NAS 에서 느리다.
    with os.scandir(folder) as it:
        entries = list(it)
    for e in entries:
        if (Path(e.name).suffix.lower() in _IMAGE_EXTS or e.name.lower() == "thumbs.db"
                or e.name.lower() in _SKIP_NAMES):
            continue
        try:
            if e.is_file() and e.stat().st_size <= _MAX_COPY:
                yield Path(e.path)
        except OSError:
            continue


def _copy_targets(folder: Path, parent: bool = True):
    """``(원본, zip 안 이름)`` — 폴더 바로 아래 파일 · Zone 폴더(``Zones``·``RecipeN-Zones``) · TrainData ini ·
    윗 폴더(Setup)의 파일(최대 100개)."""
    for p in _small_files(folder):
        yield p, f"copy/{p.name}"
    with os.scandir(folder) as it:
        dirs = [Path(e.path) for e in it if Path(e.name).suffix.lower() not in _IMAGE_EXTS and e.is_dir()]
    for d in dirs:
        if _ZONE_DIR.match(d.name):
            for p in _small_files(d):
                yield p, f"copy/{d.name}/{p.name}"
        elif d.name == "TrainData":
            for p in d.glob("*.ini"):
                yield p, f"copy/TrainData/{p.name}"
    if not parent:
        return
    try:
        for i, p in enumerate(_small_files(folder.parent)):
            if i >= _COPY_PARENT_MAX:
                break
            yield p, f"copy/_상위폴더/{p.name}"
    except OSError:
        pass


def _write_folder(z: zipfile.ZipFile, color: Path, prefix: str = "", parent: bool = True,
                  scan: bool = True) -> None:
    folder = color.parent
    d = gather(color, scan)
    listing = d["files"] if isinstance(d["files"], list) else []
    z.writestr(prefix + "report.txt", _report(d))
    z.writestr(prefix + "diag.json", json.dumps(d, ensure_ascii=False, indent=2))
    z.writestr(prefix + "files.txt", "\n".join(f"{n}\t{s}" for n, s in listing) + "\n")
    try:
        for p, arc in _copy_targets(folder, parent):
            z.write(p, prefix + arc)
    except OSError:
        pass


def collect(color_path, out_root=None) -> Path:
    """진단 zip 을 쓰고 그 경로를 돌려준다.  zip 을 못 쓸 때만 예외를 낸다."""
    color = Path(color_path)
    root = Path(out_root) if out_root else _default_root()
    root.mkdir(parents=True, exist_ok=True)
    out = root / f"Scan진단_{color.parent.name}_{datetime.now().strftime('%H%M%S')}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        _write_folder(z, color)
    return out


def collect_many(folders, out_root=None, progress=None) -> Path:
    """웨이퍼 폴더 여러 개(LOT)를 zip 하나에 ``<웨이퍼 폴더 이름>/`` 으로 나눠 담는다.

    레시피 분석용 빠른 모드다 — 사진마다 Scan 을 찾는 구역은 건너뛴다(그건 GL 의 Scan 진단 · :func:`collect`).
    ``progress(done, total)`` 은 웨이퍼 하나를 끝낼 때마다 부른다.  윗 폴더(Setup) 파일은 웨이퍼마다
    같으니 첫 웨이퍼에만 담긴다."""
    folders = [Path(f) for f in folders]
    root = Path(out_root) if out_root else _default_root()
    root.mkdir(parents=True, exist_ok=True)
    lot = (folders[0].name if len(folders) == 1 else folders[0].parent.name) if folders else "-"
    out = root / f"Scan진단_{lot}_{datetime.now().strftime('%H%M%S')}.zip"
    seen: set = set()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for i, f in enumerate(folders):
            name = f.name if f.name not in seen else f"{f.name}_{i}"
            seen.add(name)
            _write_folder(z, f / "-", f"{name}/", parent=(i == 0), scan=False)
            if progress is not None:
                progress(i + 1, len(folders))
    return out
