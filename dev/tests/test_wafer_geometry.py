"""die 격자 기하를 결과 폴더에서 읽는지 검증 — TB500 하드코딩 제거 회귀 가드.

핵심 계약 3가지:
  1. TB500 실측에서 **기존과 완전히 같은 값**이 나온다(폴백이든 파일이든).
  2. die 크기가 다른 디바이스에서도 파일 값대로 변환된다.
  3. 잘못 읽은 pitch 는 ``Col == floor(X/pitch)`` 검산으로 걸러 폴백한다.

무거운 의존성(PyQt6/cv2/openvino) 없이 순수 로직만 쓴다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aoi_verification.app.coords import camtek_ini, kla_info
from aoi_verification.app.coords import wafer_geometry as wg
from aoi_verification.app.coords.models import (CAMTEK_PITCH_X, CAMTEK_PITCH_Y,
                                                KLA_ZERO_X, KLA_ZERO_Y)

_REAL = Path(__file__).resolve().parents[1] / "좌표 확인"


@pytest.fixture(autouse=True)
def _clear_caches():
    """폴더 단위 lru_cache 가 테스트 간에 새지 않게 한다."""
    for f in (wg.camtek_geometry, wg.kla_geometry, camtek_ini.load_folder,
              camtek_ini.load_raw_folder, camtek_ini.load_abs_folder,
              kla_info.load_folder, kla_info.load_folder_raw):
        f.cache_clear()
    yield
    for f in (wg.camtek_geometry, wg.kla_geometry, camtek_ini.load_folder,
              camtek_ini.load_raw_folder, camtek_ini.load_abs_folder,
              kla_info.load_folder, kla_info.load_folder_raw):
        f.cache_clear()


# ---------------------------------------------------------------------------
# helpers — 가상 스캔 폴더 만들기
# ---------------------------------------------------------------------------
def _params_ini(step_x: float, step_y: float, *, size_only: bool = False) -> str:
    keys = ("DieSize_X", "DieSize_Y") if size_only else ("DieStep_X", "DieStep_Y")
    return (f"[Geometry]\n{keys[0]}={step_x:.6f}\n{keys[1]}={step_y:.6f}\n")


def _grabbing_ini(entries) -> str:
    """entries: [(stem, X, Y, Col, Row)] → ColorImageGrabingInfo.ini 텍스트."""
    return "\n".join(
        f"[{stem}.jpeg]\nX={X}\nY={Y}\nCol={C}\nRow={R}\n"
        for stem, X, Y, C, R in entries
    )


def _make_camtek_folder(tmp_path: Path, pitch_x: float, pitch_y: float,
                        entries, *, params_in_parent: bool = False,
                        params_pitch=None, size_only: bool = False) -> Path:
    """가상 Camtek 결과 폴더. ``params_pitch`` 를 주면 INI 에 그 값을 적는다."""
    folder = tmp_path / "wafer" / "imgs" if params_in_parent else tmp_path / "imgs"
    folder.mkdir(parents=True)
    (folder / "ColorImageGrabingInfo.ini").write_text(
        _grabbing_ini(entries), encoding="utf-8")
    px, py = params_pitch if params_pitch else (pitch_x, pitch_y)
    target = folder.parent if params_in_parent else folder
    (target / "Params_WaferInfo.ini").write_text(
        _params_ini(px, py, size_only=size_only), encoding="utf-8")
    return folder


# ---------------------------------------------------------------------------
# Camtek pitch 를 Params_WaferInfo.ini 에서 읽는다
# ---------------------------------------------------------------------------
class TestCamtekPitchFromFile:
    def test_diestep_is_used(self, tmp_path):
        """DieStep_X/Y 를 읽어 die-local x/y 를 계산한다."""
        # pitch 10000×12000 인 가상 디바이스, Col=3 Row=2 → floor 검산도 통과
        folder = _make_camtek_folder(
            tmp_path, 10000.0, 12000.0,
            [("a", 34500.0, 26400.0, 3, 2)])
        geom = wg.camtek_geometry(folder)
        assert geom.pitch_x == 10000.0
        assert geom.pitch_y == 12000.0
        assert geom.source.startswith("Params_WaferInfo.ini:DieStep")

        c = camtek_ini.load_folder(folder)["a"]
        assert c.x == pytest.approx(34500.0 - 3 * 10000.0)   # 4500
        assert c.y == pytest.approx(26400.0 - 2 * 12000.0)   # 2400

    def test_diesize_is_fallback_when_no_diestep(self, tmp_path):
        """DieStep 이 없으면 DieSize 로 폴백한다."""
        folder = _make_camtek_folder(
            tmp_path, 10000.0, 12000.0,
            [("a", 34500.0, 26400.0, 3, 2)], size_only=True)
        geom = wg.camtek_geometry(folder)
        assert (geom.pitch_x, geom.pitch_y) == (10000.0, 12000.0)
        assert geom.source.startswith("Params_WaferInfo.ini:DieSize")

    def test_found_in_parent_folder(self, tmp_path):
        """사진 폴더가 웨이퍼 폴더의 하위여도 부모를 올라가 찾는다."""
        folder = _make_camtek_folder(
            tmp_path, 10000.0, 12000.0,
            [("a", 34500.0, 26400.0, 3, 2)], params_in_parent=True)
        assert wg.camtek_geometry(folder).pitch_x == 10000.0

    def test_missing_file_falls_back_to_constants(self, tmp_path):
        """Params_WaferInfo.ini 가 없으면 TB500 폴백."""
        folder = tmp_path / "imgs"
        folder.mkdir()
        geom = wg.camtek_geometry(folder)
        assert geom.source == "fallback"
        assert (geom.pitch_x, geom.pitch_y) == (CAMTEK_PITCH_X, CAMTEK_PITCH_Y)

    def test_out_of_range_pitch_rejected(self, tmp_path):
        """비상식적인 값(µm 아님)은 채택하지 않는다."""
        folder = _make_camtek_folder(
            tmp_path, 10000.0, 12000.0,
            [("a", 34500.0, 26400.0, 3, 2)], params_pitch=(0.5, 0.6))
        assert wg.camtek_geometry(folder).source == "fallback"

    def test_pitch_inconsistent_with_colrow_rejected(self, tmp_path):
        """``Col == floor(X/pitch)`` 검산에 실패하면 그 값을 쓰지 않는다.

        (엉뚱한 키를 읽었거나 INI 가 다른 웨이퍼의 것일 때 조용히 틀린 좌표를 내지 않게)"""
        folder = _make_camtek_folder(
            tmp_path, 10000.0, 12000.0,
            [("a", 34500.0, 26400.0, 3, 2), ("b", 51000.0, 60500.0, 5, 5)],
            params_pitch=(20000.0, 12000.0))
        assert wg.camtek_geometry(folder).source == "fallback"

    def test_no_grabbing_ini_skips_verification(self, tmp_path):
        """검산할 INI 항목이 없으면 검산을 건너뛴다(검산 불가 ≠ 불일치)."""
        folder = tmp_path / "imgs"
        folder.mkdir()
        (folder / "Params_WaferInfo.ini").write_text(
            _params_ini(10000.0, 12000.0), encoding="utf-8")
        assert wg.camtek_geometry(folder).pitch_x == 10000.0


# ---------------------------------------------------------------------------
# KLA — DiePitch + SampleTestPlan
# ---------------------------------------------------------------------------
def _kla_header(plan: str | None = "  -3 -1\n  -1 -3\n  0 0\n  3 -1\n  3 2 ") -> str:
    """실물 .001 헤더의 축약본. ``plan=None`` 이면 SampleTestPlan 자체를 뺀다."""
    head = ('FileVersion 1 2;\n'
            'DiePitch 3.7247930000e+004 4.4905340000e+004;\n'
            'DieOrigin 0.0000000000e+000 0.0000000000e+000;\n'
            'WaferID "W6459076XYG1";\n')
    if plan is not None:
        head += f"SampleTestPlan 5\n{plan};\n"
    return head + "DefectRecordSpec 22 DEFECTID X Y XREL YREL XINDEX YINDEX ;\n"


class TestKlaHeader:
    def test_zero_from_sample_test_plan(self):
        """die 인덱스 원점 = SampleTestPlan 인덱스 최솟값의 부호 반전."""
        g = wg.parse_kla_header(_kla_header())
        assert g is not None
        assert (g.zero_x, g.zero_y) == (3, 3)      # min XINDEX=−3, min YINDEX=−3
        assert g.pitch_x == pytest.approx(37247.93)
        assert g.pitch_y == pytest.approx(44905.34)
        assert g.source == "DiePitch+SampleTestPlan"

    def test_other_device_grid(self):
        """격자가 다른 디바이스는 다른 원점이 나온다."""
        g = wg.parse_kla_header(_kla_header("  -1 -4\n  0 0\n  5 2 "))
        assert (g.zero_x, g.zero_y) == (1, 4)

    def test_no_sample_test_plan_falls_back(self):
        """SampleTestPlan 이 없으면 원점만 폴백(pitch 는 그대로 읽는다)."""
        g = wg.parse_kla_header(_kla_header(None))
        assert g is not None
        assert (g.zero_x, g.zero_y) == (KLA_ZERO_X, KLA_ZERO_Y)
        assert g.source == "DiePitch"

    def test_no_diepitch_returns_none(self):
        assert wg.parse_kla_header("WaferID \"X\";\n") is None

    def test_folder_end_to_end(self, tmp_path):
        """가상 KLA 폴더 → col/row 가 파일의 격자대로 나온다."""
        folder = tmp_path / "slot"
        folder.mkdir()
        (folder / "res.001").write_text(
            _kla_header("  -1 -4\n  0 0\n  5 2 ")
            + "TiffFileName img_a.jpg;\nDefectList\n"
              " 1 100.0 200.0 1234.5 6789.0 2 -1 1.0 ;\n",
            encoding="utf-8")
        c = kla_info.load_folder(folder)["img_a"]
        assert (c.col, c.row) == (2 + 1, -1 + 4)          # zero_x=1, zero_y=4
        assert c.x == 1234                                 # round(XREL)
        assert c.y == round(44905.34 - 6789.0)             # DiePitchY − YREL


# ---------------------------------------------------------------------------
# TB500 실측 회귀 — 값이 예전과 같아야 한다
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _REAL.exists(), reason="dev/좌표 확인 실측 폴더 없음")
class TestRealSamples:
    def test_kla_geometry_matches_old_constants(self):
        """실측 .001 에서 읽은 원점이 옛 하드코딩 값(3,3)과 같다 — 전환의 정당성."""
        g = wg.kla_geometry(_REAL / "KLA" / "예시1")
        assert (g.zero_x, g.zero_y) == (KLA_ZERO_X, KLA_ZERO_Y)
        assert g.source == "DiePitch+SampleTestPlan"

    @pytest.mark.parametrize("name", ["예시1", "예시2", "예시3"])
    def test_camtek_grid_invariant(self, name):
        """실측 불변식 ``Col == floor(X/pitch)``·``Row == floor(Y/pitch)``.

        pitch 검산의 근거 — 깨지면 검산 로직 자체를 다시 봐야 한다."""
        import math
        raw = camtek_ini.load_raw_folder(_REAL / "Camtek" / name)
        assert raw, "샘플 INI 를 못 읽었다"
        for X, Y, col_i, row_i in raw.values():
            assert math.floor(X / CAMTEK_PITCH_X) == col_i
            assert math.floor(Y / CAMTEK_PITCH_Y) == row_i

    def test_real_coord_unchanged(self):
        """보고서 기준값 — 전환 후에도 좌표가 동일해야 한다."""
        c = camtek_ini.load_folder(
            _REAL / "Camtek" / "예시1")["272646.165679.c.1000203959.2"]
        assert (c.col, c.row) == (5, 3)
        assert c.x == pytest.approx(11913.861, abs=1e-3)
        assert c.y == pytest.approx(30959.654, abs=1e-3)

        k = kla_info.load_folder(_REAL / "KLA" / "예시1")["w6459076xyg1_2_0_23_2"]
        assert (k.col, k.row) == (5, 3)
        assert (k.x, k.y) == (11819.0, 31035.0)
