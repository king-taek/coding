"""Setup 화면의 die 크기 안내 — 허용 오차를 자재에 맞게 정하도록 돕는 문구.

die 4 mm 자재에서 기본 허용 오차 500 µm 는 die 폭의 12 % 라 오매칭 위험이 크다.
**값을 대신 바꾸지는 않고**(기존 결과가 조용히 달라지면 안 된다) 알리기만 한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication                           # noqa: E402

from aoi_verification.app import i18n                              # noqa: E402
from aoi_verification.app.coords import camtek_ini                 # noqa: E402
from aoi_verification.app.coords import wafer_geometry as wg       # noqa: E402
from aoi_verification.app.ui.pages.setup_page import SetupPage     # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clear_caches():
    for f in (wg.camtek_geometry, camtek_ini.load_raw_folder,
              camtek_ini.load_folder):
        f.cache_clear()
    yield
    for f in (wg.camtek_geometry, camtek_ini.load_raw_folder,
              camtek_ini.load_folder):
        f.cache_clear()


def _slot(root: Path, pitch_x: float, pitch_y: float, *, with_params=True) -> Path:
    """``root/slot1/`` 에 격자가 일관된 가상 스캔 폴더를 만든다."""
    folder = root / "slot1"
    folder.mkdir(parents=True)
    # Col == floor(X/pitch) 가 성립하도록 절대좌표를 만든다(검산 통과용).
    entries = "".join(
        f"[d{c}.jpeg]\nX={c * pitch_x + pitch_x * 0.5}\n"
        f"Y={r * pitch_y + pitch_y * 0.5}\nCol={c}\nRow={r}\n"
        for c, r in ((1, 1), (3, 2))
    )
    (folder / "ColorImageGrabingInfo.ini").write_text(entries, encoding="utf-8")
    if with_params:
        (folder / "Params_WaferInfo.ini").write_text(
            f"[Geometry]\nDieStep_X={pitch_x:.6f}\nDieStep_Y={pitch_y:.6f}\n",
            encoding="utf-8")
    return folder


def _hint(page: SetupPage) -> str:
    return page._die_hint.text()


def test_detected_die_size_is_shown(qapp, tmp_path):
    """정상 — 감지된 die 크기와 출처를 보여준다."""
    _slot(tmp_path, 37247.7, 44905.4)
    page = SetupPage()
    page.ref_path_edit.setText(str(tmp_path))
    page.val_path_edit.setText(str(tmp_path))
    page._validate()
    text = _hint(page)
    assert "37,248" in text and "44,905" in text
    assert "Params_WaferInfo.ini" in text
    # die 37 mm 에 기본 500 µm 는 1.3 % → 경고 없음
    assert i18n.KO.DIE_TOL_TOO_LARGE_FMT.split("{")[0] not in text
    assert page._die_hint.property("role") == "muted"
    page.deleteLater()


def test_small_die_warns_about_tolerance(qapp, tmp_path):
    """★ die 가 작으면 기본 허용 오차가 과하다고 경고한다 (실측 PGEE48 = 4.16 mm)."""
    _slot(tmp_path, 4160.9, 5294.0)
    page = SetupPage()
    page.coord_tol_spin.setValue(500.0)
    page.ref_path_edit.setText(str(tmp_path))
    page.val_path_edit.setText(str(tmp_path))
    page._validate()
    text = _hint(page)
    assert "4,161" in text
    assert "12 %" in text                      # 500 / 4160.9 = 12.0 %
    assert page._die_hint.property("role") == "warn"
    # 값 자체는 건드리지 않는다 — 알리기만 한다.
    assert page.coord_tol_spin.value() == 500.0
    page.deleteLater()


def test_small_die_no_warning_when_tolerance_lowered(qapp, tmp_path):
    """허용 오차를 자재에 맞게 낮추면 경고가 사라진다."""
    _slot(tmp_path, 4160.9, 5294.0)
    page = SetupPage()
    page.coord_tol_spin.setValue(50.0)         # die 폭의 1.2 %
    page.ref_path_edit.setText(str(tmp_path))
    page.val_path_edit.setText(str(tmp_path))
    page._validate()
    assert "%" not in _hint(page)
    assert page._die_hint.property("role") == "muted"
    page.deleteLater()


def test_missing_geometry_warns(qapp, tmp_path):
    """Camtek INI 항목이 있는데 die 크기를 못 찾으면 좌표 매칭을 쓸 수 없다고 알린다."""
    # 격자가 TB500 과 다른데 pitch 를 알려주는 파일이 없다 → 기하 확정 불가
    _slot(tmp_path, 4160.9, 5294.0, with_params=False)
    page = SetupPage()
    page.ref_path_edit.setText(str(tmp_path))
    page.val_path_edit.setText(str(tmp_path))
    page._validate()
    assert _hint(page) == i18n.KO.DIE_SIZE_NOT_FOUND
    assert page._die_hint.property("role") == "warn"
    page.deleteLater()


# ---------------------------------------------------------------------------
# ★ die 크기를 몰라도 좌표가 나오는 슬롯은 **경고하지 않는다**
#
# KLA 슬롯과 LIVE 파일명 슬롯은 Camtek INI 가 없어 die pitch 를 못 읽지만, 각자
# 자기 경로(.001 / 파일명)로 좌표를 만든다.  이걸 '좌표 매칭을 쓸 수 없습니다' 로
# 경고하면 멀쩡한 스캔에서 사용자를 오도한다(실제로 그렇게 떴다).
# ---------------------------------------------------------------------------
def _kla_slot(root: Path) -> Path:
    folder = root / "slot1"
    folder.mkdir(parents=True)
    (folder / "res.001").write_text(
        'DiePitch 3.7247930000e+004 4.4905340000e+004;\n'
        'SampleTestPlan 2\n  -3 -1\n  0 0 ;\n'
        'TiffFileName W1_2_0_23_2.jpg;\n'
        'DefectList\n 1 1 2 11819.4 13870.7 2 0 1.0 ;\n', encoding="utf-8")
    (folder / "W1_2_0_23_2.jpg").write_bytes(b"")
    return folder


def test_kla_slot_shows_die_pitch_not_a_warning(qapp, tmp_path):
    """KLA 슬롯은 `.001` 의 DiePitch 로 die 크기를 보여준다 — 경고가 아니다."""
    _kla_slot(tmp_path)
    page = SetupPage()
    page.ref_path_edit.setText(str(tmp_path))
    page.val_path_edit.setText(str(tmp_path))
    page._validate()
    text = _hint(page)
    assert i18n.KO.DIE_SIZE_SRC_KLA in text
    assert "37,248" in text
    assert page._die_hint.property("role") == "muted"
    page.deleteLater()


def test_live_filename_slot_is_silent(qapp, tmp_path):
    """LIVE 파일명 슬롯은 die 크기를 알 수 없지만 좌표는 나온다 — 아무 말도 하지 않는다."""
    folder = tmp_path / "slot1"
    folder.mkdir(parents=True)
    (folder / "R_DEV_LIVE_LOT_W1_4_5_Bump_30229.803_1987.994.jpg").write_bytes(b"")
    page = SetupPage()
    page.ref_path_edit.setText(str(tmp_path))
    page.val_path_edit.setText(str(tmp_path))
    page._validate()
    assert _hint(page) == ""
    assert page._die_hint.property("role") == "muted"
    page.deleteLater()


def test_empty_path_shows_nothing(qapp, tmp_path):
    """경로가 비었으면 빈 상태에서 겁주지 않는다(`_validate` 의 기존 관습)."""
    page = SetupPage()
    page._validate()
    assert _hint(page) == ""
    page.deleteLater()
