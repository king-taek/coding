"""AVAGO 재리뷰 진단 도구 — Map 폴더 + Scanresult 폴더가 왜 (안) 맞는지 확인한다.

사용:  python diagnose_avago_rereview.py            # GUI (PyQt6) — 기본은 앞에서 2장만
       python diagnose_avago_rereview.py --cli --map "..." --scan "..."   # 글자 보고서만

앱 코드(aoi_verification)를 import 한다 — 이 파일을 앱 폴더(main.py 가 있는 곳)나 그 아래에 두거나,
``--app <앱 폴더>`` 를 준다.  파일을 **읽기만** 한다(쓰기·수정 없음).

진단하는 것
  1. Map 폴더 안에서 <WaferID>.txt 가 실제로 어느 하위 폴더에 있는가 (예: ``2. FVI\\1. OR``)
  2. 웨이퍼 폴더 이름 ↔ Map 파일 이름이 맞는가 (대소문자·접두·접미 차이)
  3. 웨이퍼마다 INI·die 크기·장비 die 목록(s_DieLocation.dat) 상태
  4. Map 칸 ↔ 장비 die 정렬 (성공/실패, 실패 시 가장 비슷한 가설)
  5. 그 결과 어떤 사진이 빠지고 남는가

판단이 필요한 곳(Map 위치·파일 짝·정렬 가설)은 GUI 에서 직접 고르면 '판단 결과' 글이 만들어진다.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# 앱 코드 위치 찾기
# ─────────────────────────────────────────────────────────────────────────────
def locate_app(hint: str | None = None) -> Path | None:
    """``aoi_verification`` 패키지가 들어 있는 폴더(= sys.path 에 넣을 곳)."""
    starts = [Path(hint)] if hint else []
    starts += [Path(__file__).resolve().parent, Path.cwd()]
    for s in starts:
        for base in [s, *s.parents]:
            if (base / "aoi_verification" / "app").is_dir():
                return base
            if (base / "app" / "aoi_verification" / "app").is_dir():
                return base / "app"
    return None


def import_app(hint: str | None = None):
    root = locate_app(hint)
    if root is None:
        raise SystemExit("앱 코드(aoi_verification)를 못 찾았습니다 — --app <main.py 가 있는 폴더> 를 주세요.")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from aoi_verification.app.coords import camtek_ini, rereview, wafer_geometry  # noqa
    from aoi_verification.app.models import slot as slot_mod  # noqa
    return root, camtek_ini, rereview, wafer_geometry, slot_mod


# ─────────────────────────────────────────────────────────────────────────────
# 진단 결과 모양
# ─────────────────────────────────────────────────────────────────────────────
OK, WARN, BAD = "ok", "warn", "bad"


@dataclass
class Hypo:
    """정렬 가설 하나 — 맵을 ``flip`` 으로 뒤집고 ``(dx, dy)`` 만큼 옮겼을 때."""
    flip: str                  # "" | lr | ud | rot180
    dx: int
    dy: int
    hit: int                   # 장비 die 중 맵 칸 위에 놓인 수
    outside: int               # 맵 밖에 떨어진 장비 die 수
    map_only: int              # 맵에만 있는 칸 수

    def label(self) -> str:
        f = {"": "그대로", "lr": "좌우 뒤집기", "ud": "상하 뒤집기", "rot180": "180° 회전"}[self.flip]
        return f"{f} · 이동 ({self.dx:+d},{self.dy:+d}) · 겹침 {self.hit}/{self.hit + self.outside}"


@dataclass
class WaferDiag:
    slot: str
    folder: Path
    n_photos: int = 0
    map_file: Path | None = None
    map_candidates: list = field(default_factory=list)   # 이름이 비슷한 Map 파일들
    map_rows_cols: tuple = ()
    map_n_cells: int = 0
    map_n_rejects: int = 0
    ini: str = ""
    n_coords: int = 0
    pitch: tuple | None = None
    n_die_local: int = 0
    n_die_any: int = 0
    plan: object = None
    hypos: list = field(default_factory=list)
    die_cells: frozenset = frozenset()
    photo_cells: dict = field(default_factory=dict)      # 사진 → stage (x_index, y_index)
    rmap: object = None
    issues: list = field(default_factory=list)          # [(level, 글)]
    level: str = OK


def _norm(s: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", s.upper())


# ─────────────────────────────────────────────────────────────────────────────
# Map 폴더 탐색
# ─────────────────────────────────────────────────────────────────────────────
def scan_map_tree(map_root: Path, max_depth: int = 3) -> dict:
    """``{폴더: [txt 이름…]}`` — Map 폴더와 그 하위(깊이 ≤ max_depth)에서 .txt 가 있는 곳."""
    out: dict = {}

    def walk(d: Path, depth: int) -> None:
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        txt = sorted(p.name for p in entries if p.is_file() and p.suffix.lower() == ".txt")
        if txt:
            out[d] = txt
        if depth < max_depth:
            for p in entries:
                try:
                    if p.is_dir():
                        walk(p, depth + 1)
                except OSError:
                    continue

    walk(map_root, 0)
    return out


def best_map_dir(tree: dict, slots: list) -> Path | None:
    """웨이퍼 이름과 가장 많이 맞는 Map 폴더."""
    want = {_norm(s) for s in slots}
    best, score = None, 0
    for d, names in tree.items():
        k = sum(1 for n in names if _norm(Path(n).stem) in want)
        if k > score:
            best, score = d, k
    return best


def near_names(slot: str, names: list) -> list:
    """정확히는 안 맞지만 비슷한 Map 파일(접두·접미·대소문자·기호 차이)."""
    key = _norm(slot)
    out = []
    for n in names:
        s = _norm(Path(n).stem)
        if s == key or (len(key) >= 6 and (key in s or s in key)):
            out.append(n)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 정렬 가설
# ─────────────────────────────────────────────────────────────────────────────
_FLIPS = {"": lambda i, j, R, C: (i, j),
          "lr": lambda i, j, R, C: (C - 1 - i, j),
          "ud": lambda i, j, R, C: (i, R - 1 - j),
          "rot180": lambda i, j, R, C: (C - 1 - i, R - 1 - j)}


def flipped(cells, rows: int, cols: int, flip: str) -> set:
    f = _FLIPS[flip]
    return {f(i, j, rows, cols) for i, j in cells}


def best_hypotheses(map_cells, die_cells, rows: int, cols: int, top: int = 6) -> list:
    """가장 많이 겹치는 (뒤집기, 이동) 후보들 — 끝(최솟값·최댓값) 맞춤 근처 ±6 칸만 본다."""
    b = set(die_cells)
    if not map_cells or not b:
        return []
    res = []
    for flip in _FLIPS:
        a = flipped(map_cells, rows, cols, flip)
        dxs, dys = set(), set()
        for ref_a, ref_b, acc in ((min, min, "lo"), (max, max, "hi")):
            ax, ay = ref_a(i for i, _ in a), ref_a(j for _, j in a)
            bx, by = ref_b(i for i, _ in b), ref_b(j for _, j in b)
            for d in range(-6, 7):
                dxs.add(bx - ax + d)
                dys.add(by - ay + d)
        for dx in dxs:
            for dy in dys:
                hit = sum(1 for (i, j) in a if (i + dx, j + dy) in b)
                res.append(Hypo(flip, dx, dy, hit, len(b) - hit, len(a) - hit))
    res.sort(key=lambda h: (-h.hit, h.flip != "", abs(h.dx) + abs(h.dy)))
    return res[:top]


# ─────────────────────────────────────────────────────────────────────────────
# 웨이퍼 하나 진단
# ─────────────────────────────────────────────────────────────────────────────
def diagnose_wafer(app, slot: str, folder: Path, images: list, map_dir: Path | None,
                   map_names: list, map_stem_override: str | None = None,
                   reject_dir: Path | None = None) -> WaferDiag:
    _root, camtek_ini, rr, wg, _slot = app
    d = WaferDiag(slot=slot, folder=folder, n_photos=len(images))
    for fn in (camtek_ini.load_folder, camtek_ini.load_raw_folder, camtek_ini.load_abs_folder,
               camtek_ini.load_recipe_folder, wg.camtek_geometry):
        fn.cache_clear()

    # 1) Map 파일
    stem = map_stem_override or slot
    if map_dir is not None:
        d.map_file = rr.find_map(map_dir, stem)
        if d.map_file is None:
            d.map_candidates = near_names(slot, map_names)
            if d.map_candidates:
                d.issues.append((WARN, f"Map 폴더에 '{slot}.txt' 는 없고 이름이 비슷한 파일이 있습니다: "
                                       + ", ".join(d.map_candidates[:4])))
            else:
                d.issues.append((BAD, f"Map 폴더에 '{slot}.txt' 가 없습니다."))
        else:
            rm, warn = rr.load_map(map_dir, stem)
            d.rmap = rm
            if rm is None:
                d.issues.append((BAD, f"Map 파일을 쓸 수 없습니다({d.map_file.name}) — WAFER 머리글·FNLOC(180)·"
                                      "RowData 줄 길이를 확인하세요."))
            else:
                d.map_rows_cols = (rm.rows, rm.cols)
                d.map_n_cells, d.map_n_rejects = len(rm.cells), len(rm.rejects)

    # 2) Scanresult 쪽
    ini = camtek_ini._find_ini(folder)
    d.ini = ini.name if ini else ""
    if ini is None:
        d.issues.append((BAD, "Camtek INI(ColorImageGrabingInfo.ini)가 없어 사진의 die 를 알 수 없습니다."))
    geom = rr._geometry(folder)
    if geom is None:
        d.issues.append((BAD, "die 크기(pitch)를 못 읽었습니다 — Params_WaferInfo.ini 의 DieStep_X/Y 필요."))
    else:
        d.pitch = (geom.pitch_x, geom.pitch_y)
        coords = camtek_ini.load_folder(folder)
        d.n_coords = sum(1 for p in images if p.stem.lower() in coords)
        if d.n_coords < len(images):
            d.issues.append((WARN, f"좌표를 읽은 사진 {d.n_coords}/{len(images)}장 — 나머지는 항상 재리뷰에 들어갑니다."))
        for p in images:
            c = coords.get(p.stem.lower())
            if c is not None and c.source == "camtek_ini":
                d.photo_cells[p] = (c.col + geom.col_origin, geom.row_total - c.row)
        local = wg.die_map_cells(folder, geom.pitch_x, geom.pitch_y, local_only=True)
        anyc = wg.die_map_cells(folder, geom.pitch_x, geom.pitch_y)
        d.n_die_local, d.n_die_any = len(local or ()), len(anyc or ())
        d.die_cells = frozenset(local or ())
        if not local:
            msg = "이 웨이퍼 폴더에 s_DieLocation.dat(.md)가 없거나 읽을 수 없습니다"
            if anyc:
                msg += f" — 상위 폴더에는 있지만(die {len(anyc)}개) 다른 웨이퍼 것일 수 있어 쓰지 않습니다."
            d.issues.append((BAD, msg + "."))

    # 3) 앱과 같은 계획 + 정렬 진단
    d.plan = rr.plan_wafer(slot, folder, images, map_dir, wafer=stem, reject_dir=reject_dir)
    if d.rmap is not None and d.die_cells:
        if d.plan.aligned:
            dx, dy = d.plan.offset
            d.issues.append((OK, f"Map ↔ 장비 die 정렬 성공: 이동 ({dx:+d},{dy:+d}) · 맵 {len(d.rmap.cells)}칸 / 장비 "
                                 f"{len(d.die_cells)}칸 · 맵에만 있는 칸 {len(d.plan.unscanned)}개."))
        else:
            d.hypos = best_hypotheses(d.rmap.cells, d.die_cells, d.rmap.rows, d.rmap.cols)
            d.issues.append((BAD, f"Map ↔ 장비 die 정렬 실패 — 맵 {len(d.rmap.cells)}칸 / 장비 {len(d.die_cells)}칸 "
                                  f"(차이 {len(d.rmap.cells) - len(d.die_cells):+d})."))
            if d.hypos:
                top = d.hypos[0]
                d.issues.append((WARN, "가장 비슷한 가설: " + top.label()
                                 + ("" if top.flip == "" else "  ← 뒤집힌 맵일 수 있습니다(방향 확인 필요)")))
    n_ex = len(d.plan.excluded)
    d.issues.append((OK if n_ex or not d.plan.warnings else WARN,
                     f"결과: 사진 {d.n_photos}장 중 {n_ex}장 제외 · {len(d.plan.review)}장 재리뷰."))
    d.level = BAD if any(l == BAD for l, _ in d.issues) else WARN if any(l == WARN for l, _ in d.issues) else OK
    return d


# ─────────────────────────────────────────────────────────────────────────────
# 전체 진단
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Overall:
    map_root: Path
    scan_root: Path
    reject_root: Path | None
    tree: dict
    chosen_map_dir: Path | None
    wafers: list
    notes: list = field(default_factory=list)          # [(level, 글)]
    unmatched_maps: list = field(default_factory=list)


def run_diagnosis(app, map_root: Path, scan_root: Path, reject_root: Path | None = None,
                  map_dir: Path | None = None, stem_overrides: dict | None = None,
                  progress=None, limit: int = 2, only: list | None = None) -> Overall:
    _root, _ci, rr, _wg, slot_mod = app
    notes: list = []
    if not scan_root.is_dir():
        raise SystemExit(f"Scanresult 폴더가 없습니다: {scan_root}")
    dirs = slot_mod._one_side_dirs(scan_root)
    all_slots = sorted(dirs)
    if only:
        want = {_norm(n) for n in only}
        slots = [n for n in all_slots if _norm(n) in want]
    else:
        slots = all_slots[:limit] if limit else all_slots
    if len(slots) < len(all_slots):
        notes.append((OK, f"웨이퍼 {len(all_slots)}장 중 {len(slots)}장만 진단합니다: " + ", ".join(slots)))
    if not slots:
        notes.append((BAD, "Scanresult 폴더에서 웨이퍼(사진이 있는 하위 폴더)를 찾지 못했습니다."))
    tree = scan_map_tree(map_root) if map_root and map_root.is_dir() else {}
    if not map_root or not map_root.is_dir():
        notes.append((BAD, f"Map 폴더가 없습니다: {map_root}"))
    chosen = map_dir
    if chosen is None and tree:
        chosen = best_map_dir(tree, slots) or (map_root if map_root in tree else None)
    if tree:
        if map_root not in tree:
            where = chosen if chosen else next(iter(tree))
            notes.append((BAD if chosen else WARN,
                          f"입력한 Map 폴더 바로 안에는 .txt 가 없습니다. 맵 파일은 하위 폴더에 있습니다 → "
                          f"앱의 Map 칸에는 '{where}' 를 넣어야 합니다."))
        elif chosen and chosen != map_root:
            notes.append((WARN, f"입력한 폴더에도 .txt 가 있지만 웨이퍼 이름이 더 많이 맞는 곳은 '{chosen}' 입니다."))
        if len(tree) > 1:
            notes.append((WARN, "Map(.txt) 이 있는 폴더가 여러 곳입니다: "
                          + " | ".join(f"{d.relative_to(map_root) if d != map_root else '.'} ({len(n)}개)"
                                       for d, n in tree.items())))
    elif map_root and map_root.is_dir():
        notes.append((BAD, "Map 폴더와 하위 3단계 안에 .txt 파일이 하나도 없습니다."))
    map_names = tree.get(chosen, []) if chosen else []
    wafers = []
    for k, slot in enumerate(slots, start=1):
        folder = dirs[slot]
        images = slot_mod._list_images(folder)
        wafers.append(diagnose_wafer(app, slot, folder, images, chosen, map_names,
                                     (stem_overrides or {}).get(slot), reject_root))
        if progress:
            progress(k, len(slots), slot)
    used = {w.map_file.name.lower() for w in wafers if w.map_file}
    unmatched = [n for n in map_names if n.lower() not in used]
    if unmatched and slots:
        notes.append((WARN, f"어떤 웨이퍼 폴더와도 짝이 안 된 Map 파일 {len(unmatched)}개: " + ", ".join(unmatched[:8])))
    return Overall(map_root, scan_root, reject_root, tree, chosen, wafers, notes, unmatched)


# ─────────────────────────────────────────────────────────────────────────────
# 글자 보고서 (GUI 의 '복사' 와 --cli 가 같이 쓴다)
# ─────────────────────────────────────────────────────────────────────────────
_ICON = {OK: "[OK]  ", WARN: "[주의]", BAD: "[문제]"}
_WORD = {OK: "OK", WARN: "주의", BAD: "문제"}


def build_report(ov: Overall, decisions: dict | None = None) -> str:
    decisions = decisions or {}
    L = [f"AVAGO 재리뷰 진단 보고  ({datetime.now():%Y-%m-%d %H:%M})", "",
         f"Map 폴더(입력)   : {ov.map_root}",
         f"Map 사용 폴더    : {ov.chosen_map_dir}",
         f"Scanresult      : {ov.scan_root}"]
    if ov.reject_root:
        L.append(f"1차 Reject 사진 : {ov.reject_root}")
    n = len(ov.wafers)
    n_map = sum(1 for w in ov.wafers if w.rmap is not None)
    n_al = sum(1 for w in ov.wafers if w.plan is not None and w.plan.aligned)
    n_ex = sum(len(w.plan.excluded) for w in ov.wafers if w.plan)
    n_ph = sum(w.n_photos for w in ov.wafers)
    L += ["", f"요약: 웨이퍼 {n}장 · Map 읽음 {n_map} · 정렬 성공 {n_al} · 사진 {n_ph}장 중 제외 {n_ex}장", ""]
    if ov.notes:
        L.append("■ 전체 점검")
        L += [f"  {_ICON[l]} {t}" for l, t in ov.notes]
        L.append("")
    L.append("■ 웨이퍼별")
    for w in ov.wafers:
        L.append(f"- {w.slot}  [{_WORD[w.level]}]  사진 {w.n_photos}장 · 좌표 {w.n_coords}장 · "
                 f"die목록 {w.n_die_local}칸 · Map {w.map_n_cells}칸(Reject {w.map_n_rejects})"
                 + (f" · pitch {w.pitch[0]:.0f}×{w.pitch[1]:.0f}" if w.pitch else ""))
        for l, t in w.issues:
            L.append(f"    {_ICON[l]} {t}")
        for h in w.hypos[:3]:
            L.append(f"      가설: {h.label()}")
        if w.slot in decisions:
            L.append(f"    ▶ 사용자 판단: {decisions[w.slot]}")
    if any(k.startswith("_") for k in decisions):
        L += ["", "■ 사용자 판단(전체)"]
        L += [f"  - {v}" for k, v in decisions.items() if k.startswith("_")]
    return "\n".join(L)


# ─────────────────────────────────────────────────────────────────────────────
# GUI — 작은 노트북 화면에서도 보이게(화면의 85% 안, 글자 12px, 스크롤) · 판단은 버튼 한 번
# ─────────────────────────────────────────────────────────────────────────────
_QSS = """
* { font-family: 'Malgun Gothic','Segoe UI',sans-serif; font-size: 12px; color: #1f2933; }
QWidget#root { background: #f6f5f2; }
QLabel#h1 { font-size: 16px; font-weight: 600; }
QLabel#muted { color: #6b7785; }
QLabel#ask { font-size: 13px; font-weight: 600; }
QFrame#card { background: #ffffff; border: 1px solid #e4e1da; border-radius: 10px; }
QLineEdit, QSpinBox { background: #ffffff; border: 1px solid #d9d5cc; border-radius: 6px; padding: 4px 8px; }
QLineEdit:focus { border: 1px solid #2f6f62; }
QPushButton { background: #ffffff; border: 1px solid #d9d5cc; border-radius: 6px; padding: 5px 12px; }
QPushButton:hover { border-color: #2f6f62; }
QPushButton#primary { background: #2f6f62; border: 1px solid #2f6f62; color: #ffffff; font-weight: 600; }
QPushButton#primary:hover { background: #28604f; }
QPushButton#choice { padding: 9px 14px; font-size: 13px; text-align: left; }
QPushButton#choice:checked { background: #e6efec; border: 2px solid #2f6f62; font-weight: 600; }
QPushButton:disabled { color: #aab2bb; background: #f1efea; }
QListWidget { background: #ffffff; border: none; outline: 0; }
QListWidget::item { padding: 6px 8px; border-radius: 6px; margin: 1px 2px; }
QListWidget::item:selected { background: #e6efec; color: #1f2933; }
QPlainTextEdit { background: #ffffff; border: 1px solid #e4e1da; border-radius: 6px; padding: 6px;
                 font-family: Consolas,'D2Coding',monospace; font-size: 12px; }
QProgressBar { border: none; background: #e4e1da; border-radius: 3px; max-height: 5px; }
QProgressBar::chunk { background: #2f6f62; border-radius: 3px; }
"""
_DOT = {OK: "#3f8f5f", WARN: "#d19a2b", BAD: "#c4493b"}


def run_gui(app, args) -> int:
    from PyQt6.QtCore import Qt, QThread, pyqtSignal
    from PyQt6.QtGui import QBrush, QColor, QGuiApplication
    from PyQt6.QtWidgets import (QApplication, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                                 QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar,
                                 QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget)

    class Worker(QThread):
        progress = pyqtSignal(int, int, str)
        done = pyqtSignal(object)
        failed = pyqtSignal(str)

        def __init__(self, **kw):
            super().__init__()
            self.kw = kw

        def run(self):
            try:
                self.done.emit(run_diagnosis(app, progress=lambda a, b, s: self.progress.emit(a, b, s), **self.kw))
            except SystemExit as e:
                self.failed.emit(str(e))
            except Exception:  # noqa: BLE001
                import traceback
                self.failed.emit(traceback.format_exc())

    class Win(QWidget):
        def __init__(self):
            super().__init__()
            self.setObjectName("root")
            self.setWindowTitle("AVAGO 재리뷰 진단")
            geo = QGuiApplication.primaryScreen().availableGeometry()
            self.resize(min(1000, int(geo.width() * 0.85)), min(680, int(geo.height() * 0.85)))
            self.setMinimumSize(560, 420)
            self.ov = None
            self.decisions: dict = {}
            self.worker = None
            self.map_override = None
            self.stem_over: dict = {}
            self.cur = None

            root = QVBoxLayout(self)
            root.setContentsMargins(12, 10, 12, 10)
            root.setSpacing(8)
            t = QLabel("AVAGO 재리뷰 진단")
            t.setObjectName("h1")
            root.addWidget(t)

            card = QFrame()
            card.setObjectName("card")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(10, 8, 10, 8)
            cl.setSpacing(5)
            self.e_map, self.e_scan, self.e_rj = (QLineEdit(args.map or ""), QLineEdit(args.scan or ""),
                                                  QLineEdit(args.reject or ""))
            for label, e in (("Map 폴더", self.e_map), ("Scanresult", self.e_scan), ("1차 Reject(선택)", self.e_rj)):
                row = QHBoxLayout()
                lb = QLabel(label)
                lb.setFixedWidth(92)
                b = QPushButton("…")
                b.setFixedWidth(36)
                b.clicked.connect(lambda _=False, e=e: self.pick(e))
                row.addWidget(lb)
                row.addWidget(e, 1)
                row.addWidget(b)
                cl.addLayout(row)
            row = QHBoxLayout()
            row.addWidget(QLabel("앞에서"))
            self.spin = QSpinBox()
            self.spin.setRange(0, 99)
            self.spin.setValue(args.limit)
            self.spin.setSpecialValueText("전부")
            row.addWidget(self.spin)
            row.addWidget(QLabel("장만  ·  이름 지정"))
            self.e_only = QLineEdit(args.only or "")
            self.e_only.setPlaceholderText("(선택) 웨이퍼 이름, 쉼표로 구분")
            row.addWidget(self.e_only, 1)
            self.btn_run = QPushButton("진단 시작")
            self.btn_run.setObjectName("primary")
            self.btn_run.clicked.connect(self.start)
            row.addWidget(self.btn_run)
            cl.addLayout(row)
            self.bar = QProgressBar()
            self.bar.setTextVisible(False)
            self.bar.setVisible(False)
            cl.addWidget(self.bar)
            root.addWidget(card)

            split = QSplitter()
            self.lst = QListWidget()
            self.lst.currentRowChanged.connect(self.on_select)
            self.lst.setMinimumWidth(140)
            self.lst.setMaximumWidth(260)
            split.addWidget(self.lst)
            self.detail = QPlainTextEdit()
            self.detail.setReadOnly(True)
            split.addWidget(self.detail)
            split.setStretchFactor(1, 1)
            root.addWidget(split, 1)

            # 판단 영역 — 질문 하나 + 큰 선택 버튼들(필요할 때만 보인다)
            self.ask_box = QFrame()
            self.ask_box.setObjectName("card")
            al = QVBoxLayout(self.ask_box)
            al.setContentsMargins(10, 8, 10, 8)
            al.setSpacing(5)
            self.ask = QLabel("")
            self.ask.setObjectName("ask")
            self.ask.setWordWrap(True)
            al.addWidget(self.ask)
            self.btn_lay = QVBoxLayout()
            self.btn_lay.setSpacing(4)
            al.addLayout(self.btn_lay)
            self.group = QButtonGroup(self)
            self.group.setExclusive(True)
            self.ask_box.setVisible(False)
            root.addWidget(self.ask_box)

            bottom = QHBoxLayout()
            self.status = QLabel("")
            self.status.setObjectName("muted")
            self.btn_show = QPushButton("보고서 보기")
            self.btn_show.setEnabled(False)
            self.btn_show.clicked.connect(self.show_report)
            self.btn_copy = QPushButton("판단 결과 복사")
            self.btn_copy.setObjectName("primary")
            self.btn_copy.setEnabled(False)
            self.btn_copy.clicked.connect(self.copy)
            bottom.addWidget(self.status, 1)
            bottom.addWidget(self.btn_show)
            bottom.addWidget(self.btn_copy)
            root.addLayout(bottom)

        # ── 실행
        def pick(self, edit):
            d = QFileDialog.getExistingDirectory(self, "폴더 선택", edit.text() or "")
            if d:
                edit.setText(d)

        def start(self):
            m, s, r = self.e_map.text().strip(), self.e_scan.text().strip(), self.e_rj.text().strip()
            if not s:
                QMessageBox.information(self, "진단", "Scanresult 폴더를 먼저 넣어 주세요.")
                return
            self.btn_run.setEnabled(False)
            self.bar.setVisible(True)
            self.bar.setRange(0, 0)
            self.status.setText("읽는 중…")
            self.lst.clear()
            self.worker = Worker(map_root=Path(m) if m else Path(""), scan_root=Path(s),
                                 reject_root=Path(r) if r else None, map_dir=self.map_override,
                                 stem_overrides=self.stem_over, limit=self.spin.value(),
                                 only=_names(self.e_only.text()))
            self.worker.progress.connect(self.on_progress)
            self.worker.done.connect(self.on_done)
            self.worker.failed.connect(self.on_failed)
            self.worker.start()

        def on_progress(self, a, b, slot):
            self.bar.setRange(0, b)
            self.bar.setValue(a)
            self.status.setText(f"{a}/{b}  {slot}")

        def on_failed(self, msg):
            self.btn_run.setEnabled(True)
            self.bar.setVisible(False)
            self.status.setText("실패")
            self.detail.setPlainText(msg)

        def on_done(self, ov):
            self.ov = ov
            self.btn_run.setEnabled(True)
            self.bar.setVisible(False)
            self.status.setText("완료")
            self.btn_copy.setEnabled(True)
            self.btn_show.setEnabled(True)
            self.lst.clear()
            lv = BAD if any(l == BAD for l, _ in ov.notes) else WARN if any(l == WARN for l, _ in ov.notes) else OK
            for text, level in [("전체 점검", lv)] + [(w.slot, w.level) for w in ov.wafers]:
                it = QListWidgetItem("● " + text)
                it.setForeground(QBrush(QColor(_DOT[level])))
                self.lst.addItem(it)
            self.lst.setCurrentRow(0)

        # ── 상세 + 판단(질문은 한 번에 하나, 버튼 한 번)
        def on_select(self, row):
            self._clear_ask()
            if self.ov is None or row < 0:
                return
            if row == 0:
                self.detail.setPlainText("\n".join(f"{_ICON[l]} {t}" for l, t in self.ov.notes) or "전체 점검: 문제 없음")
                self._ask_map_dir()
                return
            w = self.ov.wafers[row - 1]
            self.cur = w
            lines = [f"{w.slot}   사진 {w.n_photos}장 · 좌표 {w.n_coords}장 · die목록 {w.n_die_local}칸 · "
                     f"Map {w.map_n_cells}칸(Reject {w.map_n_rejects})", ""]
            lines += [f"{_ICON[l]} {t}" for l, t in w.issues]
            if w.plan is not None:
                lines += ["", "앱이 낼 경고:"] + [f"  · {x}" for x in app[2].warning_lines({w.slot: w.plan})]
            self.detail.setPlainText("\n".join(lines))
            self._ask_wafer(w)

        def _clear_ask(self):
            for b in list(self.group.buttons()):
                self.group.removeButton(b)
                b.setParent(None)
                b.deleteLater()
            self.ask_box.setVisible(False)

        def _show_ask(self, question, options, key, on_pick=None):
            """options: [(버튼 글, 판단 글, 값)] — 이미 고른 게 있으면 눌러 둔다."""
            self.ask.setText(question)
            for label, note, val in options:
                b = QPushButton(label)
                b.setObjectName("choice")
                b.setCheckable(True)
                b.setChecked(self.decisions.get(key) == note)
                b.clicked.connect(lambda _=False, n=note, v=val, k=key: self._picked(k, n, v, on_pick))
                self.group.addButton(b)
                self.btn_lay.addWidget(b)
            self.ask_box.setVisible(True)

        def _picked(self, key, note, val, on_pick):
            self.decisions[key] = note
            if on_pick:
                on_pick(val)

        def _ask_map_dir(self):
            dirs = list(self.ov.tree)
            if len(dirs) < 2:
                return
            opts = [(f"{d}   ({len(self.ov.tree[d])}개)", f"Map 폴더는 '{d}'", d) for d in dirs]
            self._show_ask("Map(.txt) 이 있는 폴더가 여러 곳입니다. 어느 폴더가 맞습니까?", opts, "_map_dir",
                           on_pick=self._rerun_with_dir)

        def _rerun_with_dir(self, d):
            self.map_override = d
            self.start()

        def _ask_wafer(self, w):
            if w.map_file is None and w.map_candidates:
                opts = [(f"맞음 — {n}", f"Map 파일은 '{n}' 가 맞음", n) for n in w.map_candidates[:4]]
                opts.append(("아님 — Map 없이 전부 재리뷰", "맞는 Map 파일 없음", None))
                self._show_ask("이 웨이퍼의 Map 파일은 어느 것입니까?", opts, w.slot, on_pick=self._pick_map_name)
            elif w.hypos and not (w.plan and w.plan.aligned):
                top = w.hypos[0]
                if top.flip and top.outside == 0:
                    self._show_ask(f"Map 을 '{top.label()}' 하면 장비 die 가 전부 들어맞습니다. 이 방향이 맞습니까?",
                                   [("맞음 (뒤집힌 Map 이 맞다)", "뒤집힌 Map 가설 맞음: " + top.label(), None),
                                    ("모름 / 아님 — 정렬하지 말고 전부 재리뷰", "정렬 안 함(전부 재리뷰)", None)], w.slot)
                else:
                    self._show_ask("정렬이 안 됩니다. 어떻게 할까요?",
                                   [("정렬 없이 전부 재리뷰 (안전)", "정렬 안 함(전부 재리뷰)", None),
                                    ("이 웨이퍼는 Map 이 틀린 것 같다", "Map 이 틀린 것으로 판단", None)], w.slot)

        def _pick_map_name(self, name):
            if name:
                self.stem_over[self.cur.slot] = Path(name).stem
                self.start()                     # 고른 짝으로 다시 진단

        # ── 출력
        def report(self):
            return build_report(self.ov, self.decisions)

        def copy(self):
            QGuiApplication.clipboard().setText(self.report())
            self.status.setText("복사됨 — 붙여넣기 하세요")

        def show_report(self):
            self.detail.setPlainText(self.report())

    qapp = QApplication.instance() or QApplication(sys.argv)
    qapp.setStyleSheet(_QSS)
    w = Win()
    w.show()
    if args.map and args.scan and args.auto:
        w.start()
    if args.shot:                      # 검증용 — 끝나면 화면을 저장하고 종료
        from PyQt6.QtCore import QTimer

        def _shot():
            if w.ov is None:
                QTimer.singleShot(300, _shot)
                return
            w.lst.setCurrentRow(1)
            w.grab().save(args.shot)
            qapp.quit()
        QTimer.singleShot(500, _shot)
    return qapp.exec()


# ─────────────────────────────────────────────────────────────────────────────
def _names(text):
    return [t.strip() for t in (text or "").split(",") if t.strip()] or None


def main() -> int:
    ap = argparse.ArgumentParser(description="AVAGO 재리뷰 진단")
    ap.add_argument("--map")
    ap.add_argument("--scan")
    ap.add_argument("--reject")
    ap.add_argument("--app", help="aoi_verification 이 있는 앱 폴더(main.py 위치)")
    ap.add_argument("--limit", type=int, default=2, help="앞에서 N장만 진단(0=전부, 기본 2)")
    ap.add_argument("--only", help="진단할 웨이퍼 이름(쉼표로 여러 개)")
    ap.add_argument("--cli", action="store_true", help="GUI 없이 글자 보고서만 출력")
    ap.add_argument("--auto", action="store_true", help="GUI 를 열자마자 진단 시작")
    ap.add_argument("--shot", help=argparse.SUPPRESS)
    args = ap.parse_args()
    app = import_app(args.app)
    if args.cli:
        if not args.scan:
            ap.error("--cli 에는 --scan 이 필요합니다")
        ov = run_diagnosis(app, Path(args.map) if args.map else Path(""), Path(args.scan),
                           Path(args.reject) if args.reject else None,
                           limit=args.limit, only=_names(args.only))
        print(build_report(ov))
        return 0
    return run_gui(app, args)


if __name__ == "__main__":
    sys.exit(main())
