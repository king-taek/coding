"""die 격자 기하를 왜 못 찾는지 **단계별로** 짚어주는 진단 도구.

`die 크기를 찾지 못해 …` 안내가 떴을 때, 어느 단계에서 막혔는지 알려준다.
`Params_WaferInfo.ini` 가 분명히 있는데도 못 찾는 경우의 원인을 좁히는 게 목적이다.

사용법::

    python dev/diagnose_die_geometry.py "Y:\\...\\Setup1\\TBD-PIDS3\\25195007EWF6"

폴더를 여러 개 줘도 되고, 웨이퍼 폴더들의 **상위 폴더**를 줘도 된다(하위를 훑는다).
읽기 전용이며 아무것도 바꾸지 않는다.
"""

from __future__ import annotations

import logging
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aoi_verification.app.coords import camtek_ini                    # noqa: E402
from aoi_verification.app.coords import camtek_live, kla_info         # noqa: E402
from aoi_verification.app.coords import wafer_geometry as wg          # noqa: E402
from aoi_verification.app.coords.ini_text import read_ini_text        # noqa: E402

# 앱 로거를 막는다 — 이 도구는 자기 말로 결론을 찍는다.  안 막으면 마크다운 원문
# 경고가 stderr 로 새어 print() 출력 사이에 끼어든다(폴더당 여러 번).
_applog = logging.getLogger("aoi.coords")
_applog.addHandler(logging.NullHandler())
_applog.propagate = False

# 원시 행 덤프 최대 줄 수.
_MAX_DUMP = 20


def _detect_encoding(path: Path) -> str:
    try:
        head = path.read_bytes()[:4]
    except OSError:
        return "읽기 실패"
    if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "UTF-16 (BOM)"
    if head[:3] == b"\xef\xbb\xbf":
        return "UTF-8 (BOM)"
    return "UTF-8 / ANSI"


def _report_sources(folder: Path) -> None:
    print("  [1] pitch 후보 파일 탐색 (폴더 + 부모 2단계)")
    found_any = False
    for base in wg._search_dirs(folder):
        for rel, kx, ky in wg._CAMTEK_SOURCES:
            p = base / rel
            if not p.exists():
                continue
            found_any = True
            px, py = wg._read_key(p, kx), wg._read_key(p, ky)
            where = "같은 폴더" if base == folder else f"부모({base.name})"
            if px is not None and py is not None:
                print(f"      ✓ {rel} [{where}] {kx}={px} {ky}={py}")
            else:
                print(f"      ✗ {rel} [{where}] — {kx}/{ky} 를 못 읽음 "
                      f"({kx}={px}, {ky}={py}),  인코딩={_detect_encoding(p)}")
                txt = read_ini_text(p) or ""
                hits = [ln.strip() for ln in txt.splitlines()
                        if "die" in ln.lower() and "=" in ln]
                if hits:
                    print(f"         파일 안의 die 관련 줄: {hits[:6]}")
    if not found_any:
        print("      ✗ Params_WaferInfo.ini / ProductInfo.ini 를 아예 못 찾음")
        print(f"         (탐색한 폴더: {[str(d) for d in wg._search_dirs(folder)]})")
    _report_geometry_block(folder)


# `[Geometry]`/`[Geometric]` 에서 die 격자 판단에 쓰는 값들 — 한눈에 보이게 덤프한다.
_GEOM_KEYS = ("DieSize_X", "DieSize_Y", "DieStep_X", "DieStep_Y",
              "Diameter", "MeasuredDiameter", "EBR", "Center_X", "Center_Y",
              "FlatNotchVal", "FlatNotchOrientation")


def _report_geometry_block(folder: Path) -> None:
    """Params_WaferInfo.ini 의 기하 값 + RecipesInfo.ini 의 레시피별 pitch."""
    for base in wg._search_dirs(folder):
        p = base / "Params_WaferInfo.ini"
        if not p.exists():
            continue
        txt = read_ini_text(p) or ""
        hits = [ln.strip() for ln in txt.splitlines()
                if ln.strip().split("=")[0].strip() in _GEOM_KEYS]
        if hits:
            where = "같은 폴더" if base == folder else f"부모({base.name})"
            print(f"      · Params_WaferInfo.ini [{where}] 기하 값: "
                  f"{', '.join(hits)}")
        break
    for base in wg._search_dirs(folder):
        p = base / "RecipesInfo.ini"
        if not p.exists():
            continue
        txt = read_ini_text(p) or ""
        cur = ""
        for ln in txt.splitlines():
            s = ln.strip()
            if s.startswith("["):
                cur = s
            elif s.split("=")[0].strip() in ("Name", "DiePitchX", "DiePitchY"):
                print(f"      · RecipesInfo.ini {cur} {s}")
        break


def _folder_kind(folder: Path) -> str:
    """ColorImageGrabingInfo.ini 가 없는 폴더의 성격 — 정상인지 문제인지 가른다."""
    if any((folder / n).exists() for n in wg._DIAMETER_SOURCES):
        return ("Camtek 슬롯인데 결함이 0 건이다 — **정상**"
                "(결함이 없으면 ColorImageGrabingInfo.ini 자체가 안 만들어진다)")
    if kla_info._info_candidates(folder):
        return "KLA 슬롯이다(.001·정보파일 있음) — 정상, KLA 자기 경로로 좌표를 만든다"
    try:
        imgs = [p for p in folder.iterdir() if p.is_file()]
    except OSError:
        imgs = []
    if any(camtek_live.resolve(p) is not None for p in imgs):
        return "LIVE 파일명 슬롯이다 — 정상, 파일명에서 좌표를 만든다"
    return "Camtek·KLA·LIVE 어느 쪽도 아니다 — 좌표를 만들 수 없는 폴더"


def _interval(lo: float, hi: float, axis: str) -> None:
    """pitch 구간 출력 — **공집합이면 그렇게 말한다**(구간처럼 찍으면 오해한다)."""
    if lo >= hi:
        print(f"      pitch_{axis} : 해 없음 — 어떤 pitch 로도 "
              f"{'Col' if axis == 'x' else 'Row'} == floor("
              f"{axis.upper()}/pitch_{axis}) 를 만족시킬 수 없다.")
        print(f"                  (하한 {lo:.1f} > 상한 {hi:.1f}) "
              f"→ pitch 가 아니라 **인덱스 기준(원점)이 다른 것**이다.")
    else:
        print(f"      pitch_{axis} ∈ ({lo:.1f}, {hi:.1f}]")


def _report_grid(folder: Path) -> None:
    raw = camtek_ini.load_raw_folder(folder)
    recipes = camtek_ini.load_recipe_folder(folder)
    ini = camtek_ini._find_ini(folder)
    print(f"\n  [2] ColorImageGrabingInfo.ini 항목: {len(raw)} 건")
    if ini is not None:
        print(f"      파일: {ini.name}  (인코딩 {_detect_encoding(ini)}, "
              f"{ini.stat().st_size:,} bytes)")
    if not raw:
        if ini is None:
            print(f"      → 이 폴더에 ColorImageGrabingInfo.ini 자체가 없다.")
            print(f"         {_folder_kind(folder)}")
        else:
            print("      ⚠ 파일은 있는데 항목이 0건이다 — 형식/인코딩을 확인해야 한다.")
            txt = read_ini_text(ini) or ""
            for ln in txt.splitlines()[:8]:
                print(f"         | {ln}")
        return

    # ── 원시 행 덤프 — 규칙을 확정하려면 이게 있어야 한다 ────────────────
    px0, py0, src0 = next(iter(wg._pitch_candidates(folder)))
    print(f"\n  [2-a] 원시값 (Δ 는 1순위 후보 {src0} pitch=({px0}, {py0}) 기준)")
    print(f"        {'파일명':38} {'X':>12} {'Y':>12} {'Col':>4} {'Row':>4}"
          f" {'rec':>4} {'Δcol':>5} {'Δrow':>5}")
    for stem, (X, Y, c, r) in sorted(raw.items(), key=lambda kv: kv[0])[:_MAX_DUMP]:
        print(f"        {stem[:38]:38} {X:12.1f} {Y:12.1f} {c:4d} {r:4d}"
              f" {recipes.get(stem, 0):4d} {math.floor(X/px0)-c:5d}"
              f" {math.floor(Y/py0)-r:5d}")
    if len(raw) > _MAX_DUMP:
        print(f"        … {len(raw) - _MAX_DUMP} 건 더 (총 {len(raw)} 건)")

    print("\n  [3] 검산 — X 는 등호, Y 는 레시피별 상수(레시피마다 Row 원점이 다를 수 있다)")
    for px, py, src in wg._pitch_candidates(folder):
        dcol = Counter(math.floor(X / px) - c for X, _, c, _ in raw.values())
        drow_by_rec: dict[int, Counter] = {}
        for stem, (_X, Y, _c, r) in raw.items():
            drow_by_rec.setdefault(recipes.get(stem, 0), Counter())[
                math.floor(Y / py) - r] += 1
        x_ok = set(dcol) == {0}
        y_ok = all(len(d) == 1 for d in drow_by_rec.values())
        print(f"      {'✓' if x_ok and y_ok else '✗'} {src}  pitch=({px}, {py})")
        print(f"          Col 검산 {'✓ 전 항목 등호' if x_ok else '✗'} — "
              f"Δcol {_counts(dcol)}")
        for rec in sorted(drow_by_rec):
            d = drow_by_rec[rec]
            mark = '✓ 상수' if len(d) == 1 else '✗ 섞임'
            print(f"          Row 검산 recipe{rec}: {mark} — Δrow {_counts(d)}")

    print(f"\n  [4] 이 INI 만으로 좁혀지는 pitch 구간(참고)")
    cols = [(X, c) for X, _, c, _ in raw.values() if c > 0]
    rows = [(Y, r) for _, Y, _, r in raw.values() if r > 0]
    if cols:
        _interval(max(X / (c + 1) for X, c in cols),
                  min(X / c for X, c in cols), "x")
    else:
        print("      pitch_x : 전 항목 Col=0 — 검산이 pitch 를 전혀 제약하지 못한다.")
    if rows:
        _interval(max(Y / (r + 1) for Y, r in rows),
                  min(Y / r for Y, r in rows), "y")
    else:
        print("      pitch_y : 전 항목 Row=0 — 검산이 pitch 를 전혀 제약하지 못한다.")


def _counts(c: Counter) -> str:
    return ", ".join(f"{k}: {n}건" for k, n in sorted(c.items()))


def _report_images(folder: Path) -> int:
    """이 폴더에 앱이 쓸 사진이 있는지, 있다면 좌표가 어디서 나오는지."""
    from aoi_verification.app.config import CONFIG
    from aoi_verification.app.coords import resolve
    from aoi_verification.app.models.slot import is_ignored_name

    try:
        imgs = [p for p in folder.iterdir()
                if p.is_file() and CONFIG.is_image(p.name)
                and not is_ignored_name(p.name)]
    except OSError:
        return 0
    print(f"\n  [0] 앱이 쓸 사진: {len(imgs)} 장")
    if not imgs:
        return 0
    c = resolve(imgs[0])
    if c is None:
        print(f"      ✗ 좌표를 못 만든다 (예: {imgs[0].name})")
        print("         → 이 폴더로는 좌표 매칭이 안 된다.")
    else:
        print(f"      ✓ 좌표 나옴 — source={c.source}, col={c.col} row={c.row} "
              f"x={c.x:.0f} y={c.y:.0f}  (예: {imgs[0].name})")
    return len(imgs)


def _scan_paths(root: Path) -> None:
    """Params_WaferInfo.ini 가 알려주는 스캔 결과 경로를 그대로 보여준다."""
    for base in wg._search_dirs(root):
        p = base / "Params_WaferInfo.ini"
        if not p.exists():
            continue
        txt = read_ini_text(p) or ""
        hits = [ln.strip() for ln in txt.splitlines()
                if ln.strip().lower().startswith(("scanresultspath", "recipepath",
                                                  "setuppath"))]
        if hits:
            print(f"\n  [참고] {p} 가 가리키는 경로")
            for h in hits:
                print(f"      {h}")
        break


def _is_slot_folder(folder: Path) -> bool:
    """진단할 가치가 있는 폴더인가 — 사진도 INI 도 정보파일도 없으면 슬롯이 아니다."""
    if camtek_ini._find_ini(folder) is not None:
        return True
    if any((folder / n).exists() for n in wg._DIAMETER_SOURCES):
        return True
    if kla_info._info_candidates(folder):
        return True
    try:
        from aoi_verification.app.config import CONFIG
        from aoi_verification.app.models.slot import is_ignored_name
        return any(p.is_file() and CONFIG.is_image(p.name)
                   and not is_ignored_name(p.name) for p in folder.iterdir())
    except OSError:
        return False


def diagnose(folder: Path) -> None:
    print("=" * 72)
    print(f"폴더: {folder}")
    print("=" * 72)
    if not _is_slot_folder(folder):
        print("  → 슬롯 폴더가 아니다(사진·INI·정보파일 전부 없음) — 건너뜀")
        return
    # 캐시를 **맨 앞에서 한 번만** 비운다.  예전엔 [결론] 직전에 비워서 같은 폴더의
    # 경고가 두 번 났다(LRU 가 유일한 중복억제 장치였다).
    wg.camtek_geometry.cache_clear()
    _report_images(folder)
    _report_sources(folder)
    _report_grid(folder)

    geom = wg.camtek_geometry(folder)
    print("\n  [결론]")
    if geom is not None:
        print(f"      ✓ die 기하 확정: pitch=({geom.pitch_x}, {geom.pitch_y}) "
              f"col_origin={geom.col_origin} row_total={geom.row_total}")
        print(f"        출처: {geom.source}")
    else:
        has = wg.has_camtek_entries(folder)
        print("      ✗ die 기하를 확정하지 못했다.")
        print(f"        Camtek INI 좌표 있음: {has}")
        if has:
            print("        → 절대 wafer 좌표로 매칭한다(매칭은 정상, die 단위 표기만 불가).")
        else:
            print(f"        → {_folder_kind(folder)}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    for arg in argv[1:]:
        root = Path(arg)
        if not root.is_dir():
            print(f"[건너뜀] 폴더가 아님: {root}")
            continue
        # ★ 폴더 자신을 **항상** 먼저 본다(예전엔 하위만 보고 정작 준 폴더를 빼먹었다).
        diagnose(root)
        print()
        try:
            subs = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            subs = []
        for t in subs:
            diagnose(t)
            print()
        _scan_paths(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
