"""AVAGO 재리뷰 감사(Codex) 지적 회귀 — 입력은 전부 합성이다(골든 아님).

각 테스트의 안전 기대값이 곧 계약이다.  기대값을 현재 동작에 맞춰 고치지 마라."""
from pathlib import Path
import importlib.util
import io
import zipfile

import pytest

from aoi_verification.app.coords import rereview as rr
from .test_rereview import (  # noqa: F401  (_clear_caches 는 autouse 픽스처)
    WAFER, PX, _wafer_folder, _map_dir, _map_cells, DX, DY, _clear_caches,
)


def test_alignment_rejects_hidden_second_solution():
    a = {(4 * i, 0) for i in range(55)}
    b = {(4 * i, 0) for i in range(5, 55)}
    assert b <= a and b <= {(i + 20, j) for i, j in a}
    assert rr.align(a, b) is None


def test_hidden_alignment_cannot_exclude_good_photo(tmp_path):
    a = {(4 * i, 0) for i in range(55)}
    b = {(4 * i + DX, DY) for i in range(5, 55)}
    folder = _wafer_folder(tmp_path / 'lot', WAFER, [('good', 48, 0)], die_cells=b)
    row = ['___'] * 217
    for i, _ in a:
        row[i] = '007' if i == 28 else '000'
    txt = (f'WAFER:{WAFER}\nFNLOC:180\nROWCT:1\nCOLCT:217\nBCEQU:000\nRowData:'
           + ' '.join(row))
    img = folder / 'good.jpeg'
    plan = rr.plan_wafer(WAFER, folder, [img], _map_dir(tmp_path, txt))
    assert not plan.excluded, (plan.offset, plan.cell_of)


def test_parent_die_map_must_not_exclude_good_photo(tmp_path):
    folder = _wafer_folder(tmp_path / 'lot', WAFER, [('good', 28, 9)], die_map=False)
    other = _wafer_folder(tmp_path / 'other', 'OTHER', [],
                          die_cells={(i + DX + 1, j + DY) for i, j in _map_cells()})
    for name in ('s_DieLocation.dat', 's_DieLocation.dat.md'):
        (folder.parent / name).write_bytes((other / name).read_bytes())
    img = folder / 'good.jpeg'
    plan = rr.plan_wafer(WAFER, folder, [img], _map_dir(tmp_path))
    assert not plan.excluded, (plan.offset, plan.cell_of, plan.warnings)


def test_updated_ini_must_not_use_old_reject_coordinate(tmp_path):
    folder = _wafer_folder(tmp_path / 'lot', WAFER, [('a', 27, 9)])
    img = folder / 'a.jpeg'
    maps = _map_dir(tmp_path)
    first = rr.plan_wafer(WAFER, folder, [img], maps)
    assert first.excluded == [img]
    ini = folder / 'ColorImageGrabingInfo.ini'
    txt = ini.read_text()
    txt = txt.replace(f'X={(27 + DX + .5) * PX}', f'X={(28 + DX + .5) * PX}')
    txt = txt.replace(f'Col={27 + DX}', f'Col={28 + DX}')
    ini.write_text(txt)
    second = rr.plan_wafer(WAFER, folder, [img], maps)
    assert second.excluded == [], second.cell_of


def test_duplicate_stems_must_remain_in_review(tmp_path):
    folder = _wafer_folder(tmp_path / 'lot', WAFER, [('a', 28, 9)])
    jpg = folder / 'a.jpg'
    jpg.write_bytes(b'')
    ini = folder / 'ColorImageGrabingInfo.ini'
    ini.write_text(ini.read_text() + f'\n[a.jpg]\nX={(27 + DX + .5) * PX}\n'
                   f'Y={(9 + DY + .5) * 11000}\nCol={27 + DX}\nRow={9 + DY}\n')
    img = folder / 'a.jpeg'
    plan = rr.plan_wafer(WAFER, folder, [img, jpg], _map_dir(tmp_path))
    assert img in plan.review, plan.excluded


def test_unexpected_planning_error_has_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, '_plan_into',
                        lambda *a: (_ for _ in ()).throw(ValueError('probe')))
    p = tmp_path / 'a.jpeg'
    plan = rr.plan_wafer('W', tmp_path, [p], tmp_path)
    assert plan.review == [p] and not plan.excluded
    assert plan.warnings
    assert rr.warning_lines({'W': plan})


def test_off_map_reject_is_not_added_to_map_cells(tmp_path):
    folder = _wafer_folder(tmp_path / 'lot', WAFER, [('off', 0, 0)])
    img = folder / 'off.jpeg'
    plan = rr.plan_wafer(WAFER, folder, [img], _map_dir(tmp_path))
    assert plan.aligned and plan.cell_of[img] not in plan.reject_map.cells
    assert rr.new_reject_cells(plan, [img]) <= plan.reject_map.cells
    st = rr.wafer_stats(plan, [img])
    assert st.total_reject_dies == len(plan.reject_map.rejects)


def _window(qapp, monkeypatch):
    from aoi_verification.app.ui import main_window as mw
    monkeypatch.setattr(mw.MainWindow, '_start_backend_import_async', lambda self: None)
    win = mw.MainWindow()
    win._on_backend_loaded()
    return win, mw


def test_empty_new_plan_cannot_reuse_previous_selection(qapp, tmp_path, monkeypatch):
    from aoi_verification.app.models.slot import ImageItem, Slot, ScanResult
    from aoi_verification.app.ui.pages.select_page import Stage1State
    from aoi_verification.app.ui.pages.setup_page import SetupInput
    win, mw = _window(qapp, monkeypatch)
    old = tmp_path / 'old' / WAFER / 'old.jpeg'
    current = tmp_path / 'new' / WAFER / 'current.jpeg'
    win._select_page._state = Stage1State(
        queue=[], targets={WAFER: [ImageItem(WAFER, old, 'ref')]})
    win._input = SetupInput(mode='single', ref_root=current.parent.parent,
                            val_root=tmp_path, ref_machine='NEW LOT',
                            val_machine='', threshold=.7, rereview=True)
    win._scan = ScanResult(
        slots={WAFER: Slot(WAFER, ref_images=[ImageItem(WAFER, current, 'ref')],
                           ref_dir=current.parent)}, ref_only=[], val_only=[])
    win._scan_token = 1
    plan = rr.WaferPlan(WAFER, current.parent, review=[], excluded=[current])
    monkeypatch.setattr(mw.sheets, 'choose', lambda *a, **k: 'go')
    try:
        win._on_rereview_planned(1, {WAFER: plan})
        assert win._result_page._result.unmatched_refs == []
    finally:
        win.close()


def test_late_plan_cannot_filter_new_session(qapp, tmp_path, monkeypatch):
    from aoi_verification.app.models.slot import ImageItem, Slot, ScanResult
    from aoi_verification.app.ui.pages.setup_page import SetupInput
    win, mw = _window(qapp, monkeypatch)
    new_path = tmp_path / 'new' / WAFER / 'new.jpeg'
    win._input = SetupInput(mode='single', ref_root=new_path.parent.parent,
                            val_root=tmp_path, ref_machine='NEW LOT',
                            val_machine='', threshold=.7, rereview=True)
    win._scan = ScanResult(
        slots={WAFER: Slot(WAFER, ref_images=[ImageItem(WAFER, new_path, 'ref')],
                           ref_dir=new_path.parent)}, ref_only=[], val_only=[])
    win._scan_token = 2
    old_plan = rr.WaferPlan(WAFER, tmp_path / 'old' / WAFER, review=[],
                            excluded=[tmp_path / 'old' / WAFER / 'old.jpeg'])
    monkeypatch.setattr(mw.sheets, 'choose', lambda *a, **k: 'go')
    try:
        win._on_rereview_planned(1, {WAFER: old_plan})   # 옛 세대(token 1)
        assert len(win._scan.slots[WAFER].ref_images) == 1
    finally:
        win.close()


def test_unaligned_rejects_are_not_claimed_new(styled_qapp, tmp_path):
    from aoi_verification.app.models.result import rereview_result
    from aoi_verification.app.ui.pages.result_page import ResultPage
    from PyQt6.QtWidgets import QLabel
    p = tmp_path / 'a.jpeg'
    plan = rr.WaferPlan('W', tmp_path, review=[p], die_of={p: (1, 2)},
                        warnings=[(rr.W_ALIGN_FAIL, (10, 9))])
    result = rereview_result('LOT', {'W': plan}, {'W': [p]}, {})
    page = ResultPage()
    try:
        page.show_result(result)
        values = [lab.text() for lab in page.findChildren(QLabel)
                  if lab.property('role') == 'rrHeroValue']
        assert values == ['0'], values
    finally:
        page.deleteLater()


def test_crop_keeps_full_view_exif_orientation(styled_qapp, tmp_path):
    PIL = pytest.importorskip('PIL.Image')
    from aoi_verification.app.ui.widgets.scalable_image import ScalableImage
    p = tmp_path / 'synthetic.jpeg'
    img = PIL.new('RGB', (120, 80), 'red')
    exif = PIL.Exif()
    exif[274] = 6
    img.save(p, exif=exif)
    widget = ScalableImage()
    try:
        widget.set_image(p)
        full = widget._pix_orig.width() < widget._pix_orig.height()
        widget.set_center_crop(.5)
        cropped = widget._pix_orig.width() < widget._pix_orig.height()
        assert full == cropped, (full, cropped)
    finally:
        widget.deleteLater()


def test_collector_excludes_thumbs_db(tmp_path):
    script = Path(__file__).parents[1] / 'collect_avago_rereview_sample.py'
    spec = importlib.util.spec_from_file_location('audit_collector', script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / 'Thumbs.db').write_bytes(b'synthetic thumbnail placeholder')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        mod._add_folder_files(z, tmp_path, 'up', [])
        assert 'up/Thumbs.db' not in z.namelist()


def test_window_teardown_during_planner_does_not_abort():
    import subprocess
    import sys
    script = r'''
import threading, time
try:
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
except ImportError:
    pass
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6 import sip
from aoi_verification.app.ui import main_window as mw
from aoi_verification.app.coords import rereview as rr
mw.MainWindow._start_backend_import_async = lambda self: None
mw.MainWindow._check_for_update_async = lambda self: None
mw.MainWindow._maybe_show_whats_new = lambda self: None
mw.MainWindow._maybe_offer_openvino = lambda self: None
app = QApplication([])
win = mw.MainWindow()
started = threading.Event()
def slow(*a):
    started.set()
    time.sleep(2)
    return rr.WaferPlan('W', Path('.'))
rr.plan_wafer = slow
from aoi_verification.app.models.slot import ImageItem, Slot, ScanResult
from aoi_verification.app.ui.pages.setup_page import SetupInput
win._input = SetupInput(mode='single', ref_root=Path('.'), val_root=Path('.'),
                        ref_machine='L', val_machine='', threshold=.7, rereview=True)
sr = ScanResult(slots={'W': Slot('W', ref_images=[ImageItem('W', Path('a.jpeg'), 'ref')],
                                 ref_dir=Path('.'))}, ref_only=[], val_only=[])
win._rereview_after_scan(sr)           # 앱이 실제로 워커를 만드는 경로
assert started.wait(2)
win.close()
sip.delete(win)
print('survived', flush=True)
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == 0, (result.returncode, result.stderr[-1000:])
