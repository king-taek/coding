"""AVAGO 재리뷰 — 좌표 대조용 자료를 웨이퍼 단위로 모아 zip 하나로 만든다.

무엇을 확인하려는가: '2. #11 재 저장' 폴더 사진 파일명의 (col, row)·x·y 가 Scanresult
(Camtek INI)에서 앱이 계산한 die 좌표와 같은지, 그리고 그 (col, row) 가 1차 리뷰 Map 의
어느 칸인지.  그러려면 한 웨이퍼의 아래 파일들이 필요하다.

* 1차 리뷰 Map      ``…\\2. FVI\\1. OR\\<WaferID>.txt``
* #11 재 저장 Map   ``…\\2. FVI\\2. #11 재 저장\\<WaferID>.txt`` (있으면)
* #11 재 저장 사진   **파일명만**(사진목록 txt) — 좌표가 이름에 있다
* Scanresult 웨이퍼 폴더의 **사진 아닌 파일 전부**(ColorImageGrabingInfo.ini ·
  Params_WaferInfo.ini · s_DieLocation.dat(.md) · WaferInfo.ini …) + 사진 **파일명만**
* Scanresult 상위 2단계 폴더의 사진 아닌 파일(기하 파일이 위에 있을 수 있다)

**사진은 담지 않는다** — 좌표는 파일명과 INI 에만 있다(저장소 사진 정책).

사용법 — 앱과 같은 PC 에서 파이썬으로 실행하면 창이 차례로 뜬다::

    python collect_avago_rereview_sample.py

1) 1차 리뷰 Map 폴더(``…\\2. FVI\\1. OR``)  2) '#11 재 저장' 폴더  3) Scanresult LOT 폴더
4) 웨이퍼 선택(추천이 미리 선택돼 있다)  5) **저장할 폴더** 를 고르면 zip 이 만들어지고
그 폴더가 열린다.  읽기 전용이며 원본은 아무것도 바꾸지 않는다.

창은 PyQt6(앱에 들어 있다) → tkinter 순으로 쓰고, 둘 다 없으면 콘솔에서 묻는다.
"""

from __future__ import annotations

import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

# 대조에 좋은 웨이퍼(권장 순서) — 이름에 좌표가 있는 die 가 많고 가장자리까지 퍼진 것,
# 그리고 예전에 'Map die ≠ 장비 die' 로 막혔던 웨이퍼(수정 확인용).
RECOMMENDED = ("PH3Q42-05F4", "PH3Q42-03G6", "PH3Q42-21F3")
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif"}
MAX_FILE_MB = 50          # 이보다 큰 비사진 파일은 담지 않고 목록에만 적는다
LIVE_NAME = re.compile(r"_(-?\d+)_(-?\d+)_([^_]+)_(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)$")


# ---------------------------------------------------------------------------
# 창(대화상자) — PyQt6 → tkinter → 콘솔
# ---------------------------------------------------------------------------
class _Ui:
    def __init__(self) -> None:
        self.kind = "console"
        try:
            from PyQt6.QtWidgets import QApplication
            self._app = QApplication.instance() or QApplication(sys.argv)
            self.kind = "qt"
            return
        except Exception:
            pass
        try:
            import tkinter
            self._tk = tkinter.Tk()
            self._tk.withdraw()
            self.kind = "tk"
        except Exception:
            pass

    def folder(self, title: str, start: str = "") -> str:
        if self.kind == "qt":
            from PyQt6.QtWidgets import QFileDialog
            return QFileDialog.getExistingDirectory(None, title, start) or ""
        if self.kind == "tk":
            from tkinter import filedialog
            return filedialog.askdirectory(title=title, initialdir=start or None) or ""
        return input(f"{title}\n경로: ").strip().strip('"')

    def info(self, title: str, text: str) -> None:
        if self.kind == "qt":
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(None, title, text)
        elif self.kind == "tk":
            from tkinter import messagebox
            messagebox.showinfo(title, text)
        else:
            print(f"\n[{title}]\n{text}\n")

    def pick(self, title: str, items: list[str], preselect: set[str]) -> list[str]:
        """여러 개 고르기 — 추천을 미리 선택해 둔다."""
        if self.kind == "qt":
            from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox,
                                         QLabel, QListWidget, QVBoxLayout)
            dlg = QDialog()
            dlg.setWindowTitle(title)
            lay = QVBoxLayout(dlg)
            lay.addWidget(QLabel("대조할 웨이퍼를 고르세요 (Ctrl/Shift 로 여러 개).\n"
                                 "★ 표시가 추천입니다 — 미리 선택돼 있습니다.", dlg))
            lst = QListWidget(dlg)
            lst.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            for w in items:
                lst.addItem(("★ " if w in RECOMMENDED else "   ") + w)
                if w in preselect:
                    lst.item(lst.count() - 1).setSelected(True)
            lst.setMinimumSize(320, 420)
            lay.addWidget(lst)
            bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                  | QDialogButtonBox.StandardButton.Cancel, parent=dlg)
            bb.accepted.connect(dlg.accept)
            bb.rejected.connect(dlg.reject)
            lay.addWidget(bb)
            if not dlg.exec():
                return []
            return [it.text()[2:].strip() for it in lst.selectedItems()]
        if self.kind == "tk":
            import tkinter
            win = tkinter.Toplevel(self._tk)
            win.title(title)
            tkinter.Label(win, text="대조할 웨이퍼를 고르세요 (Ctrl/Shift 로 여러 개).\n"
                                    "★ 표시가 추천입니다 — 미리 선택돼 있습니다.").pack()
            lb = tkinter.Listbox(win, selectmode="extended", width=36, height=24)
            for i, w in enumerate(items):
                lb.insert("end", ("★ " if w in RECOMMENDED else "   ") + w)
                if w in preselect:
                    lb.selection_set(i)
            lb.pack()
            out: list[str] = []

            def ok():
                out.extend(items[i] for i in lb.curselection())
                win.destroy()
            tkinter.Button(win, text="확인", command=ok).pack()
            win.grab_set()
            self._tk.wait_window(win)
            return out
        print(title)
        for i, w in enumerate(items):
            print(f"  {i:2d}. {'★' if w in RECOMMENDED else ' '} {w}")
        raw = input("번호를 쉼표로(빈칸 = 추천): ").strip()
        if not raw:
            return [w for w in items if w in preselect]
        return [items[int(x)] for x in raw.split(",") if x.strip().isdigit()]


# ---------------------------------------------------------------------------
# 모으기
# ---------------------------------------------------------------------------
def _find_ci(folder: Path, name: str) -> Path | None:
    """대소문자 무시로 ``folder/name`` 찾기."""
    p = folder / name
    if p.exists():
        return p
    try:
        for q in folder.iterdir():
            if q.name.lower() == name.lower():
                return q
    except OSError:
        pass
    return None


def _wafer_dirs(root: Path) -> dict[str, Path]:
    try:
        return {p.name.upper(): p for p in root.iterdir() if p.is_dir()}
    except OSError:
        return {}


def _map_names(root: Path) -> set[str]:
    try:
        return {p.stem.upper() for p in root.iterdir()
                if p.is_file() and p.suffix.lower() == ".txt"}
    except OSError:
        return set()


def _add_folder_files(zf: zipfile.ZipFile, src: Path, arc: str, log: list[str],
                      photos: list[str] | None = None) -> None:
    """``src`` 바로 아래 파일 — 사진은 이름만 ``photos`` 에, 나머지는 zip 에."""
    try:
        entries = sorted(src.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        log.append(f"  ! 폴더를 못 읽음: {src} ({exc})")
        return
    for p in entries:
        if not p.is_file():
            continue
        if p.name.casefold() == "thumbs.db":       # 썸네일 부산물 = 사진으로 친다
            continue
        if p.suffix.lower() in IMAGE_EXT:
            if photos is not None:
                photos.append(p.name)
            continue
        size_mb = p.stat().st_size / 1e6
        if size_mb > MAX_FILE_MB:
            log.append(f"  - 너무 커서 뺌({size_mb:.0f} MB): {p}")
            continue
        zf.write(p, f"{arc}/{p.name}")
        log.append(f"  + {arc}/{p.name} ({size_mb:.2f} MB)")


def _summarize_live(names: list[str]) -> list[str]:
    """#11 사진 파일명의 (col,row)·x·y 요약 — 대조 전에 눈으로 보는 용도."""
    rows: dict[tuple[int, int], int] = {}
    bad = 0
    for n in names:
        m = LIVE_NAME.search(Path(n).stem)
        if not m:
            bad += 1
            continue
        key = (int(m.group(1)), int(m.group(2)))
        rows[key] = rows.get(key, 0) + 1
    out = [f"  사진 {len(names)}장 · die {len(rows)}칸 · 좌표 못 읽음 {bad}장"]
    out += [f"    (col {c:>2}, row {r:>2}) × {k}" for (c, r), k in sorted(rows.items())]
    return out


def collect(map_or: Path, map_11: Path | None, scan_lot: Path, wafers: list[str],
            out_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = (wafers[0] if len(wafers) == 1 else f"{wafers[0]}_외{len(wafers) - 1}")
    dst = out_dir / f"AVAGO재리뷰_좌표대조_{name}_{stamp}.zip"
    scan_dirs = _wafer_dirs(scan_lot)
    d11 = _wafer_dirs(map_11) if map_11 else {}
    report = [f"수집 {datetime.now():%Y-%m-%d %H:%M}",
              f"1차 리뷰 Map 폴더 : {map_or}",
              f"#11 재 저장 폴더  : {map_11 or '(없음)'}",
              f"Scanresult LOT    : {scan_lot}", ""]
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        # Scanresult 상위 2단계(기하 파일이 위에 있을 수 있다) — LOT 마다 한 번.
        report.append("[Scanresult 상위 폴더의 사진 아닌 파일]")
        for k, up in enumerate((scan_lot, scan_lot.parent)):
            _add_folder_files(zf, up, f"_scanresult_up{k}", report)
        report.append("")
        for w in wafers:
            report.append(f"[{w}]")
            for label, root, arc in (("1차 리뷰 Map", map_or, "map_OR"),
                                     ("#11 재 저장 Map", map_11, "map_11")):
                p = _find_ci(root, f"{w}.txt") if root else None
                if p:
                    zf.write(p, f"{w}/{arc}/{p.name}")
                    report.append(f"  + {w}/{arc}/{p.name}")
                else:
                    report.append(f"  ! {label} 없음: {w}.txt")
            sdir = scan_dirs.get(w.upper())
            if sdir is None:
                report.append("  ! Scanresult 에 웨이퍼 폴더가 없음")
            else:
                photos: list[str] = []
                _add_folder_files(zf, sdir, f"{w}/scanresult", report, photos)
                zf.writestr(f"{w}/scanresult/사진목록.txt",
                            "# Scanresult 사진 파일명(사진 자체는 담지 않음)\n"
                            + "\n".join(photos) + "\n")
                report.append(f"  + Scanresult 사진 파일명 {len(photos)}개")
            wdir = d11.get(w.upper())
            if wdir is None:
                report.append("  ! #11 재 저장에 웨이퍼 폴더가 없음")
            else:
                photos = []
                _add_folder_files(zf, wdir, f"{w}/map_11_files", report, photos)
                zf.writestr(f"{w}/map_11_files/사진목록.txt",
                            "# #11 재 저장 사진 파일명(사진 자체는 담지 않음)\n"
                            + "\n".join(photos) + "\n")
                report.append("  #11 재 저장 사진 파일명 좌표:")
                report += _summarize_live(photos)
            report.append("")
        zf.writestr("수집_보고.txt", "\n".join(report) + "\n")
    return dst


def main() -> int:
    ui = _Ui()
    ui.info("AVAGO 재리뷰 좌표 대조 자료 모으기",
            "창이 차례로 뜹니다.\n\n"
            "1) 1차 리뷰 Map 폴더  (…\\2. FVI\\1. OR)\n"
            "2) '#11 재 저장' 폴더  (…\\2. FVI\\2. #11 재 저장) — 없으면 취소\n"
            "3) Scanresult LOT 폴더\n"
            "4) 웨이퍼 선택 (추천이 미리 선택됨)\n"
            "5) 결과 zip 을 저장할 폴더\n\n"
            "사진은 담지 않고 파일명만 적습니다.  원본은 바꾸지 않습니다.")
    map_or = ui.folder("1) 1차 리뷰 Map 폴더 (…\\2. FVI\\1. OR)")
    if not map_or:
        return 1
    map_or_p = Path(map_or)
    map_11 = ui.folder("2) '#11 재 저장' 폴더 (없으면 취소)", str(map_or_p.parent))
    scan = ui.folder("3) Scanresult LOT 폴더")
    if not scan:
        return 1
    scan_p = Path(scan)
    map_11_p = Path(map_11) if map_11 else None
    candidates = sorted(set(_wafer_dirs(scan_p)) & _map_names(map_or_p))
    if not candidates:
        ui.info("웨이퍼 없음", "Map 폴더의 <WaferID>.txt 와 이름이 같은 웨이퍼 폴더가\n"
                "Scanresult 에 없습니다.  폴더를 다시 확인하세요.")
        return 1
    pre = {w for w in candidates if w in RECOMMENDED[:2]} or {candidates[0]}
    wafers = ui.pick("4) 웨이퍼 선택", candidates, pre)
    if not wafers:
        return 1
    out = ui.folder("5) 결과 zip 을 저장할 폴더", str(Path.home() / "Desktop"))
    if not out:
        return 1
    dst = collect(map_or_p, map_11_p, scan_p, wafers, Path(out))
    ui.info("완료", f"만들었습니다:\n{dst}\n\n이 zip 을 대화에 올려 주세요.")
    try:
        os.startfile(str(Path(out)))         # type: ignore[attr-defined]  (Windows)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
