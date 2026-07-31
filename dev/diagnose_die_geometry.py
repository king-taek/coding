"""die 격자 기하를 왜 못 찾는지 **단계별로** 짚어주는 진단 도구.

`die 크기를 찾지 못해 …` 안내가 떴을 때, 어느 단계에서 막혔는지 알려준다.
`Params_WaferInfo.ini` 가 분명히 있는데도 못 찾는 경우의 원인을 좁히는 게 목적이다.

사용법::

    python dev/diagnose_die_geometry.py "Y:\\...\\Setup1\\TBD-PIDS3\\25195007EWF6"

폴더를 여러 개 줘도 되고, 웨이퍼 폴더들의 **상위 폴더**를 줘도 된다(하위를 훑는다).
읽기 전용이며 아무것도 바꾸지 않는다.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aoi_verification.app.coords import camtek_ini                    # noqa: E402
from aoi_verification.app.coords import wafer_geometry as wg          # noqa: E402
from aoi_verification.app.coords.ini_text import read_ini_text        # noqa: E402


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


def _report_grid(folder: Path) -> None:
    raw = camtek_ini.load_raw_folder(folder)
    ini = camtek_ini._find_ini(folder)
    print(f"\n  [2] ColorImageGrabingInfo.ini 항목: {len(raw)} 건")
    if ini is not None:
        print(f"      파일: {ini.name}  (인코딩 {_detect_encoding(ini)}, "
              f"{ini.stat().st_size:,} bytes)")
    if not raw:
        if ini is None:
            print("      → 이 폴더에 ColorImageGrabingInfo.ini 자체가 없다"
                  "(KLA·LIVE 슬롯이면 정상)")
        else:
            print("      ⚠ 파일은 있는데 항목이 0건이다 — 형식/인코딩을 확인해야 한다.")
            txt = read_ini_text(ini) or ""
            for ln in txt.splitlines()[:8]:
                print(f"         | {ln}")
        return

    print("\n  [3] 검산 Col == floor(X/pitch_x), Row == floor(Y/pitch_y)")
    for px, py, src in wg._pitch_candidates(folder):
        dcol = {math.floor(X / px) - c for X, _, c, _ in raw.values()}
        drow = {math.floor(Y / py) - r for _, Y, _, r in raw.values()}
        ok = dcol == {0} and drow == {0}
        print(f"      {'✓' if ok else '✗'} {src}  pitch=({px}, {py})")
        if not ok:
            print(f"          floor(X/pitch_x) − Col 의 값들: {sorted(dcol)}")
            print(f"          floor(Y/pitch_y) − Row 의 값들: {sorted(drow)}")
            if len(dcol) == 1 and len(drow) == 1:
                print("          → 차이가 **일정**하다.  이 자재는 INI 의 Col/Row 가 "
                      "stage 격자 인덱스가 아니라 다른 기준일 수 있다.")
                print("             이 출력을 그대로 알려주면 규칙을 반영하겠습니다.")

    lo_x = max(X / (c + 1) for X, _, c, _ in raw.values())
    hi_x = min(X / c for X, _, c, _ in raw.values() if c > 0)
    lo_y = max(Y / (r + 1) for _, Y, _, r in raw.values())
    hi_y = min(Y / r for _, Y, _, r in raw.values() if r > 0)
    print(f"\n  [4] 이 INI 만으로 좁혀지는 pitch 구간(참고)")
    print(f"      pitch_x ∈ ({lo_x:.1f}, {hi_x:.1f}]      pitch_y ∈ ({lo_y:.1f}, {hi_y:.1f}]")


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


def diagnose(folder: Path) -> None:
    print("=" * 72)
    print(f"폴더: {folder}")
    print("=" * 72)
    _report_images(folder)
    _report_sources(folder)
    _report_grid(folder)

    wg.camtek_geometry.cache_clear()
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
            print("        → Camtek INI 가 없는 폴더다(KLA·LIVE 슬롯이면 정상).")


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
