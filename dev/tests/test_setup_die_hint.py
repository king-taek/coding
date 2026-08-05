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
    """백그라운드 스캔이 끝나기를 기다린 뒤 문구를 읽는다.

    `_refresh_die_hint` 는 폴더를 **워커 스레드에서** 훑는다(폴더를 고르는 순간 창이
    멈추지 않게).  결과는 시그널로 늦게 오므로 테스트는 스레드를 기다리고 이벤트를
    한 번 돌려야 한다."""
    scan = getattr(page, "_die_scan", None)
    if scan is not None:
        assert scan.wait(10_000), "die 기하 스캔이 끝나지 않았다"
    QApplication.processEvents()
    text = page._die_hint.text()
    assert text != i18n.KO.DIE_SIZE_CHECKING, "스캔 결과가 아직 반영되지 않았다"
    return text


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


# ---------------------------------------------------------------------------
# ★ 스캔은 **백그라운드**에서 — 폴더를 고르는 순간 창이 멈추지 않는다
#
# 실제 신고: 폴더 선택 후 렉.  원인은 이 스캔이 UI 스레드에서 돌았기 때문이다
# (실측: 슬롯 25 × 결함 3,000 → 1.4초).  계산량은 줄이지 않았다 — pitch 검산은
# 정확도 가드라 완화하면 격자가 다른 자재에 상수가 채택된다(CLAUDE.md).  자리만 옮겼다.
# ---------------------------------------------------------------------------
def test_ui_thread_does_not_wait_for_the_scan(qapp, tmp_path, monkeypatch):
    """★ `_validate` 는 스캔을 기다리지 않고 즉시 돌아온다."""
    import threading
    import time

    started = threading.Event()
    ran_on: list[int] = []
    ui_thread = threading.get_ident()

    def slow(_ref_text):
        ran_on.append(threading.get_ident())
        started.set()
        time.sleep(0.6)                       # 느린 NAS 를 흉내낸다
        return ((37247.7, 44905.4), "느린 스캔", False)

    monkeypatch.setattr(SetupPage, "_detect_die_geometry", staticmethod(slow))
    page = SetupPage()
    page.coord_tol_spin.setValue(500.0)
    page.ref_path_edit.setText(str(tmp_path))

    t0 = time.perf_counter()
    page._validate()
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.3, f"UI 스레드가 스캔을 기다렸다 ({elapsed:.2f}초)"
    # 기다리는 동안 빈 줄이 아니라 '확인 중' 이 보인다(로딩바 규칙과 같은 취지).
    assert page._die_hint.text() == i18n.KO.DIE_SIZE_CHECKING
    assert started.wait(5), "워커가 시작되지 않았다"
    assert "37,248" in _hint(page)             # 끝나면 결과가 반영된다
    # ★ 타이밍과 별개로 **스레드가 실제로 다르다** — 느린 PC 에서도 흔들리지 않는 단언.
    assert ran_on and ran_on[0] != ui_thread, "스캔이 UI 스레드에서 돌았다"
    page.deleteLater()


def test_late_result_of_previous_folder_is_dropped(qapp):
    """폴더를 연달아 바꿀 때, 늦게 끝난 옛 스캔이 새 안내를 덮어쓰지 않는다."""
    page = SetupPage()
    page._die_token = 5
    page._on_die_scan_done(4, (4160.9, 5294.0), "옛 폴더", False)   # 옛 세대
    assert page._die_hint.text() == ""
    assert page._die_scanned_for is None
    page._on_die_scan_done(5, (37247.7, 44905.4), "새 폴더", False)  # 현 세대
    assert "37,248" in page._die_hint.text()
    page.deleteLater()


def test_no_duplicate_scan_while_one_is_running(qapp, tmp_path, monkeypatch):
    """기준 폴더를 고른 직후 검증 폴더를 고를 때 같은 폴더를 두 번 훑지 않는다.

    두 입력란이 같은 디바운스(`_schedule_validate`)를 공유해 `_validate` 가 다시
    오는데, 그때마다 워커를 새로 띄우면 느린 NAS 에서 부하가 그대로 두 배가 된다."""
    import threading

    calls: list[str] = []
    started, gate = threading.Event(), threading.Event()

    def slow(ref_text):
        calls.append(ref_text)
        started.set()
        gate.wait(5)
        return ((37247.7, 44905.4), "느린 스캔", False)

    monkeypatch.setattr(SetupPage, "_detect_die_geometry", staticmethod(slow))
    page = SetupPage()
    page.ref_path_edit.setText(str(tmp_path))
    page._validate()
    assert started.wait(5), "워커가 시작되지 않았다"
    page.val_path_edit.setText(str(tmp_path))
    page._validate()                          # 스캔이 도는 중에 다시 왔다
    assert len(calls) == 1, f"같은 폴더를 {len(calls)}번 훑었다"
    gate.set()
    assert "37,248" in _hint(page)
    page.deleteLater()


def test_same_folder_is_not_rescanned(qapp, tmp_path, monkeypatch):
    """허용 오차를 건드릴 때마다 폴더를 다시 훑지 않는다 — 문구만 다시 그린다."""
    calls: list[str] = []
    real = SetupPage._detect_die_geometry
    monkeypatch.setattr(
        SetupPage, "_detect_die_geometry",
        staticmethod(lambda t: (calls.append(t), real(t))[1]))

    _slot(tmp_path, 4160.9, 5294.0)
    page = SetupPage()
    page.coord_tol_spin.setValue(500.0)
    page.ref_path_edit.setText(str(tmp_path))
    page._validate()
    assert "12 %" in _hint(page)               # die 4.16mm 에 500µm → 경고
    assert len(calls) == 1

    page.coord_tol_spin.setValue(50.0)         # 자재에 맞게 낮춘다
    page._validate()
    assert "%" not in _hint(page)              # 경고가 사라진다(문구는 갱신됨)
    assert len(calls) == 1, "허용 오차만 바꿨는데 폴더를 다시 훑었다"
    page.deleteLater()
