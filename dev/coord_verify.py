"""좌표 검증 틀 — '좌표 자료 수집' 결과로 **지금 앱**의 col·row·x·y 를 정답과 대조한다.

개발 전용 도구(배포 안 됨).  실행:

    python dev/coord_verify.py <수집결과_풀어둔_폴더> <작업폴더> [--hide]

* ``<수집결과>`` : 줄여 묶은 zip 을 전부 푼 폴더(``NNN_셋업_웨이퍼`` 하위 폴더 · ``이유.txt`` ·
  ``좌표실험_*.json``).  원래 NAS 경로대로 ``<작업폴더>`` 아래에 트리를 다시 짓는다 —
  앱이 형제·부모 폴더를 보기 때문이다.  ``s_DieLocation.txt`` 는 앱이 읽는 ``.dat`` 로 되돌린다.
* 정답 세 가지(서로 독립):
    1. 번호표 — 수집기가 따로 읽은 ``[ExportData]``/``DieLut`` 원점(``hyp.EXP/LUT``).
    2. Conder — 같은 스캔 사진의 파일명 col·row·x·y(화면 번호).
    3. 장비 화면 — 사용자가 장비에서 읽어 준 값(:data:`EQUIPMENT`).
* ``--hide`` : 번호표가 있는 폴더마다 **그 폴더의** 번호표를 숨기고 다시 돌린다 —
  번호표가 없는 웨이퍼에서 앱이 무엇을 내는지 재는 시험.

가장 중요한 수치는 **조용히 틀림**(틀렸는데 경고가 없음)이다.  0 이어야 한다.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import struct
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 사용자가 장비 화면에서 읽어 준 값(관측) — (폴더 끝 두 단계, 사진 이름 앞부분) → (col, row) 화면 번호.
EQUIPMENT = {
    ("AWW 3D/UQVE05001-01D1", "159853.23239.gr"): (41, 71),
    ("HTY-BUMP TOP/P34286-01E0", "151146.285839.c"): (15, 1),
    ("HTY-BUMP TOP/P34286-01E0", "151145.279267.c"): (15, 2),
    ("CCU/P8V798-12B2", "299227.110236.c"): (61, 44),
    ("CWM RE/GNF7K21-A4", "8517.138517.c"): (1, 230),
    ("SNU-EDGE/GAYHY17-E5", "152464.299939.gr"): (95, 2),
    ("SNU-EDGE/GAYHY17-E5", "153482.300005.gr"): (96, 2),
    ("SNU-EDGE/GAYHY01-A7", "107274.11220.gr"): (66, 371),
    ("SNU-EDGE/GAYHY01-A7", "118332.8100.gr"): (73, 375),
    ("SNU-EDGE/GAYHY01-A7", "121495.7320.gr"): (75, 376),
    ("SNU-EDGE/GAYHY01-A7", "135394.4979.gr"): (84, 379),
    ("BNU BUMPTOP/P35004-01E2", "31935.161285.c"): (1, 17),
    ("XGW-EDGE/GRN9H01-G4", "157686.74844.gr"): (103, 223),
}

_DIELOC = "s_DieLocation"
_NUM = re.compile(r"^-?\d+(\.\d+)?$")
_INT = re.compile(r"^-?\d+$")


def _txt_to_dat(txt: Path, dat: Path) -> bool:
    """수집기가 줄여 둔 die 위치 글을 앱이 읽는 ``.dat``(레코드 16 바이트: x, y double)로 되돌린다."""
    lines = txt.read_text(encoding="utf-8").splitlines()
    pts: list[tuple[float, float]] = []
    if len(lines) > 1 and lines[1].strip() == "격자":
        xs = [float(v) for v in lines[2].split("\t")[1:]]
        ys = [float(v) for v in lines[3].split("\t")[1:]]
        for y, row in zip(ys, lines[4:]):
            pts += [(x, y) for x, f in zip(xs, row) if f == "1"]
    elif len(lines) > 1 and lines[1].strip() == "점":
        for ln in lines[2:]:
            a, b = ln.split("\t")
            pts.append((float(a), float(b)))
    else:
        return False
    dat.write_bytes(b"".join(struct.pack("<dd", x, y) for x, y in pts))
    Path(str(dat) + ".md").write_text(
        '<RecordSize Size="16"/>\n<Field Name="x" Offset="0" Vartype="5"/>\n'
        '<Field Name="y" Offset="8" Vartype="5"/>\n', encoding="utf-8")
    return True


def _windows_to_parts(p: str) -> list[str]:
    p = p.replace("\\\\", "").replace("\\", "/").replace(":", "")
    return [s for s in p.split("/") if s]


def build_work(src: Path, work: Path) -> list[dict]:
    """수집 결과 → 원래 경로 트리.  반환: 폴더마다 {sub, path, rec}."""
    order = [ln.split("\t")[0] for ln in (src / "이유.txt").read_text(encoding="utf-8").splitlines()
             if "\t" in ln]
    js = next(src.glob("좌표실험_*.json"))
    recs = {r["folder"]: r for r in json.loads(js.read_text(encoding="utf-8"))["camtek"]}
    if work.exists():
        shutil.rmtree(work)
    out = []
    for sub in sorted(os.listdir(src)):
        m = re.match(r"(\d{3})_", sub)
        if not m or not (src / sub).is_dir():
            continue
        orig = order[int(m.group(1))]
        dst = work.joinpath(*_windows_to_parts(orig))
        dst.mkdir(parents=True, exist_ok=True)
        for f in (src / sub).iterdir():
            if f.is_file() and f.name != f"{_DIELOC}.txt":
                os.symlink(f, dst / f.name)
        if (src / sub / f"{_DIELOC}.txt").is_file():
            (dst / f"{_DIELOC}.dat.md").unlink(missing_ok=True)
            _txt_to_dat(src / sub / f"{_DIELOC}.txt", dst / f"{_DIELOC}.dat")
        up = src / sub / "_상위"
        if up.is_dir():
            for lvl in up.iterdir():
                anc = next((a for a in dst.parents if a.name == lvl.name), None)
                if anc is None:
                    continue
                for f in lvl.iterdir():
                    if not (anc / f.name).exists():
                        os.symlink(f, anc / f.name)
        out.append({"sub": sub, "path": dst, "rec": recs.get(orig, {}), "orig": orig})
    # 원재료 없이 요약만 있는 번호표 웨이퍼 — 빌리기의 참조가 되도록 최소 파일을 만든다
    # (pitch · 중심 · 지름 · 번호표만).  검증 대상에는 넣지 않는다.
    have = {it["orig"] for it in out}
    for orig, r in recs.items():
        hyp = r.get("hyp") or {}
        t = hyp.get("EXP") or hyp.get("LUT")
        if orig in have or not t or not r.get("px") or not r.get("cx"):
            continue
        dst = work.joinpath(*_windows_to_parts(orig))
        if dst.exists():
            continue
        dst.mkdir(parents=True)
        ex = r.get("export") or {"cMin": t[0], "cMax": t[0], "rMin": t[1], "rMax": t[1]}
        (dst / "Params_WaferInfo.ini").write_text(
            f"[Geometric]\nDieStep_X={r['px']}\nDieStep_Y={r['py']}\nDiameter={r.get('dia') or 300000}\n"
            f"Center_X={r['cx']}\nCenter_Y={r['cy']}\n", encoding="utf-8")
        (dst / "ProductInfo.ini").write_text(
            f"[Info]\nXDieIndex={r['px']}\nYDieIndex={r['py']}\n[ExportData]\n"
            + "".join(f"{k}={ex[k]}\n" for k in ("cMin", "cMax", "rMin", "rMax")), encoding="utf-8")
    return out


def _photos(folder: Path) -> list[str]:
    p = folder / "사진목록.txt"
    if not p.is_file():
        return []
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]


def _live(name: str):
    """Conder 파일명 → (col, row, x, y) — 앱의 camtek_live 규칙과 같은 토큰 읽기."""
    t = name.rsplit(".", 1)[0].split("_")
    for i in range(2, len(t) - 1):
        if _INT.match(t[i]) and _INT.match(t[i + 1]):
            rest = [float(v) for v in t[i + 2:] if _NUM.match(v)]
            return (int(t[i]), int(t[i + 1]), rest[0], rest[1]) if len(rest) >= 2 else None
    return None


def check_folder(item: dict) -> dict:
    from aoi_verification.app.coords import camtek_ini, wafer_geometry as wg

    f: Path = item["path"]
    rec = item["rec"]
    hyp = rec.get("hyp") or {}
    truth = hyp.get("EXP") or hyp.get("LUT")
    g = wg.camtek_geometry(f)
    coords = camtek_ini.load_folder(f)
    raw = camtek_ini.load_raw_folder(f)
    res = {"sub": item["sub"], "orig": item["orig"],
           "px": rec.get("px"), "py": rec.get("py"),
           "truth": truth, "app": (g.col_origin, g.row_total) if g else None,
           "source": g.source if g else None,
           "warn": bool(coords) and any(c.number_warn for c in coords.values()),
           "geom_none": g is None and bool(raw)}
    res["origin_ok"] = None if truth is None or g is None else [g.col_origin, g.row_total] == list(truth)

    # Conder 같은 스캔 사진
    parts = item["orig"].replace("/", "\\").split("\\")
    pre = f"{parts[-4]}_{parts[-2]}_{parts[-1]}_"
    cl = f / "사진목록_Conder.txt"
    names = [ln.strip() for ln in cl.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")] if cl.is_file() else []
    c = Counter()
    bad_ex = []
    if g is not None:
        for n in names:
            if not n.startswith(pre):
                continue
            q = _live(n)
            if q is None:
                continue
            col, row, x, y = q
            hits = [s for s, (X, Y, *_r) in raw.items()
                    if abs((X - math.floor(X / g.pitch_x) * g.pitch_x) - x) <= 3
                    and abs((Y - math.floor(Y / g.pitch_y) * g.pitch_y) - y) <= 3]
            if len(hits) != 1:
                c["짝 못 찾음"] += 1
                continue
            d = coords.get(hits[0])
            if d is None:
                c["앱 좌표 없음"] += 1
                continue
            same = (d.screen_col, d.screen_row) == (col, row) and abs(d.x - x) <= 3 and abs(d.y - y) <= 3
            c["같음" if same else "다름"] += 1
            if not same and len(bad_ex) < 3:
                bad_ex.append([n[len(pre):], [d.screen_col, d.screen_row, d.x, d.y]])
    res["conder"] = dict(c)
    res["conder_bad"] = bad_ex

    # 장비 화면
    tail = "/".join(parts[-2:])
    eq = []
    for (ft, stem), want in EQUIPMENT.items():
        if ft != tail:
            continue
        hit = [k for k in coords if k.startswith(stem.lower())]
        d = coords.get(hit[0]) if hit else None
        got = (d.screen_col, d.screen_row) if d else None
        eq.append({"photo": stem, "want": want, "got": got, "ok": got == tuple(want),
                   "warn": bool(d and d.number_warn)})
    res["equipment"] = eq
    return res


def _hide(folder: Path):
    """그 폴더의 번호표(ExportData · DieLut)를 숨긴다 — 되돌릴 함수를 돌려준다."""
    undo = []
    lut = folder / "DieLut.dat"
    if lut.exists():
        tmp = folder / "DieLut.dat.hidden"
        lut.rename(tmp)
        undo.append(lambda: tmp.rename(lut))
    pi = folder / "ProductInfo.ini"
    if pi.exists():
        text = pi.read_text(encoding="utf-8", errors="replace")
        cut = re.sub(r"(?ims)^\s*\[ExportData\]\s*$.*?(?=^\s*\[|\Z)", "", text)
        if cut != text:
            link = os.readlink(pi) if pi.is_symlink() else None
            pi.unlink()
            pi.write_text(cut, encoding="utf-8")

            def back(pi=pi, link=link, text=text):
                pi.unlink()
                if link:
                    os.symlink(link, pi)
                else:
                    pi.write_text(text, encoding="utf-8")
            undo.append(back)
    return lambda: [u() for u in reversed(undo)]


def run(src: Path, work: Path, hide: bool = False) -> dict:
    import logging
    logging.disable(logging.WARNING)
    from aoi_verification.app import coords as C

    items = build_work(src, work)
    rows = []
    for it in items:
        C.clear_caches()
        rows.append(check_folder(it))
    hidden = []
    if hide:
        for it in items:
            hyp = (it["rec"].get("hyp") or {})
            if not (hyp.get("EXP") or hyp.get("LUT")):
                continue
            undo = _hide(it["path"])
            C.clear_caches()
            try:
                r = check_folder(it)
            finally:
                undo()
            r["truth"] = hyp.get("EXP") or hyp.get("LUT")
            r["origin_ok"] = None if r["app"] is None else list(r["app"]) == list(r["truth"])
            hidden.append(r)
    return {"rows": rows, "hidden": hidden}


def summary(res: dict) -> str:
    L = []

    def block(title, rows):
        n = len(rows)
        none = sum(r["geom_none"] for r in rows)
        t = [r for r in rows if r["origin_ok"] is not None]
        ok = sum(r["origin_ok"] for r in t)
        silent = [r for r in t if not r["origin_ok"] and not r["warn"]]
        cc = Counter()
        for r in rows:
            cc.update(r["conder"])
        L.append(f"■ {title}: 폴더 {n} · die 기하 없음(절대좌표) {none}")
        L.append(f"   번호 원점: 정답 있는 {len(t)} 중 맞음 {ok} · 틀림 {len(t) - ok} · 그중 경고 없이 틀림 {len(silent)}")
        L.append(f"   Conder 같은 스캔 사진: {dict(cc)}")
        for r in silent[:12]:
            L.append(f"     조용히 틀림 {r['sub']}  앱 {r['app']} 정답 {r['truth']}  [{r['source']}]")
    block("그대로", res["rows"])
    if res["hidden"]:
        block("번호표 숨김", res["hidden"])
    eq = [e | {"sub": r["sub"]} for r in res["rows"] for e in r["equipment"]]
    L.append(f"■ 장비 화면 {len(eq)}장: 맞음 {sum(e['ok'] for e in eq)} · 틀림인데 경고 없음 "
             f"{sum((not e['ok']) and not e['warn'] for e in eq)}")
    for e in eq:
        L.append(f"   {'✓' if e['ok'] else '✗'} {e['sub'][:34]:34} {e['photo']:22} 장비 {e['want']} 앱 {e['got']}"
                 f"{' ⚠' if e['warn'] else ''}")
    return "\n".join(L)


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    res = run(Path(a[0]), Path(a[1]), hide="--hide" in sys.argv)
    out = Path(a[1]).with_suffix(".json")
    out.write_text(json.dumps(res, ensure_ascii=False, default=str), encoding="utf-8")
    print(summary(res))
