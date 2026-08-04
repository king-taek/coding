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
from aoi_verification.app.coords.models import (CAMTEK_COL_OFFSET,
                                                CAMTEK_PITCH_X, CAMTEK_PITCH_Y,
                                                KLA_ZERO_X, KLA_ZERO_Y)

_REAL = Path(__file__).resolve().parents[1] / "좌표 확인"
# 사용자가 올린 RDL4 실물 — 같은 결함이 두 형태로 확보된 로트(§6-J).
_RDL4_REAL = (Path(__file__).resolve().parents[2] / "docs"
              / "RDL4 LOT files(26년 05월, FDV)")


@pytest.fixture(autouse=True)
def _clear_caches():
    """폴더 단위 lru_cache 가 테스트 간에 새지 않게 한다."""
    for f in (wg.camtek_geometry, wg.kla_geometry, camtek_ini.load_folder,
              camtek_ini.load_raw_folder, camtek_ini.load_abs_folder,
              camtek_ini.load_recipe_folder,
              kla_info.load_folder, kla_info.load_folder_raw):
        f.cache_clear()
    yield
    for f in (wg.camtek_geometry, wg.kla_geometry, camtek_ini.load_folder,
              camtek_ini.load_raw_folder, camtek_ini.load_abs_folder,
              camtek_ini.load_recipe_folder,
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


def _grabbing_ini_rec(entries) -> str:
    """entries: [(stem, X, Y, Col, Row, RecipeNumber)] → INI 텍스트."""
    return "\n".join(
        f"[{stem}.jpeg]\nX={X}\nY={Y}\nCol={C}\nRow={R}\nRecipeNumber={N}\n"
        for stem, X, Y, C, R, N in entries
    )


def _make_bare_folder(folder: Path, entries) -> Path:
    """pitch 를 알려주는 파일이 **하나도 없는** 스캔 폴더."""
    folder.mkdir(parents=True)
    (folder / "ColorImageGrabingInfo.ini").write_text(
        _grabbing_ini(entries), encoding="utf-8")
    return folder


def _make_product_info_folder(tmp_path: Path, entries,
                              pitch_x: float, pitch_y: float) -> Path:
    """``ProductInfo.ini`` 만 있는 스캔 폴더 (실측 PGEE48 배치)."""
    folder = _make_bare_folder(tmp_path / "imgs", entries)
    (folder / "ProductInfo.ini").write_text(
        f"[Geometric]\nXDieSize={pitch_x - 13.5:.3f}\nYDieSize={pitch_y - 15.1:.3f}\n"
        f"XDieIndex={pitch_x:.3f}\nYDieIndex={pitch_y:.3f}\nCols=74\nRows=58\n",
        encoding="utf-8")
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

    def test_diesize_is_not_a_candidate(self, tmp_path):
        """★ die '크기' 는 pitch 가 아니다 — 후보에 넣지 않는다.

        스크라이브 street 이 있는 자재는 크기와 간격이 다르다(실측 PGEE48: 13.5 µm 차).
        크기를 pitch 로 쓰면 col 70 에서 die 폭의 23% 가 어긋난다."""
        folder = _make_camtek_folder(
            tmp_path, 10000.0, 12000.0,
            [("a", 34500.0, 26400.0, 3, 2)], size_only=True)
        # DieSize 만 있는 INI → pitch 후보 없음.  상수 후보도 검산에 걸린다.
        assert wg.camtek_geometry(folder) is None
        # die 로 쪼개지는 못하지만 절대좌표 폴백으로 **매칭은 된다**.
        c = camtek_ini.load_folder(folder)["a"]
        assert c.source == "camtek_abs"
        assert (c.x, c.y) == (34500.0, 26400.0)      # 절대값 그대로(die-local 아님)

    def test_product_info_is_second_candidate(self, tmp_path):
        """Params_WaferInfo.ini 가 없으면 ProductInfo.ini 의 `XDieIndex/YDieIndex`."""
        folder = _make_product_info_folder(
            tmp_path, [("a", 34500.0, 26400.0, 3, 2)], 10000.0, 12000.0)
        geom = wg.camtek_geometry(folder)
        assert geom is not None
        assert (geom.pitch_x, geom.pitch_y) == (10000.0, 12000.0)
        assert geom.source.startswith("ProductInfo.ini:XDieIndex")

    def test_candidates_are_tried_in_order_until_one_verifies(self, tmp_path):
        """첫 후보가 검산에 실패하면 **다음 후보로 넘어간다**(바로 포기하지 않는다)."""
        entries = [("a", 34500.0, 26400.0, 3, 2), ("b", 51000.0, 60500.0, 5, 5)]
        folder = _make_product_info_folder(tmp_path, entries, 10000.0, 12000.0)
        # 1순위(Params DieStep)에 틀린 값을 심는다 → 2순위 ProductInfo 가 채택돼야 한다.
        (folder / "Params_WaferInfo.ini").write_text(
            _params_ini(20000.0, 12000.0), encoding="utf-8")
        geom = wg.camtek_geometry(folder)
        assert geom is not None
        assert geom.pitch_x == 10000.0
        assert geom.source.startswith("ProductInfo.ini")

    def test_found_in_parent_folder(self, tmp_path):
        """사진 폴더가 웨이퍼 폴더의 하위여도 부모를 올라가 찾는다."""
        folder = _make_camtek_folder(
            tmp_path, 10000.0, 12000.0,
            [("a", 34500.0, 26400.0, 3, 2)], params_in_parent=True)
        assert wg.camtek_geometry(folder).pitch_x == 10000.0

    def test_constants_are_a_candidate_but_get_verified(self, tmp_path):
        """상수 후보도 **검산을 받는다** — 통과하면 쓰고, 아니면 좌표를 안 만든다.

        실측 폴더처럼 Params_WaferInfo.ini 가 없어도 데이터가 상수를 확인해 주면 그건
        추정이 아니라 검증된 값이다."""
        ok = _make_bare_folder(tmp_path / "ok",
                               [("a", 3 * CAMTEK_PITCH_X + 500.0,
                                 2 * CAMTEK_PITCH_Y + 500.0, 3, 2)])
        geom = wg.camtek_geometry(ok)
        assert geom is not None and geom.source == "models.py 상수"

    def test_other_device_never_gets_tb500_numbers(self, tmp_path):
        """★ 핵심 안전장치 — 격자가 다른 자재는 좌표를 **안 만든다**.

        예전엔 여기서 TB500 pitch 로 폴백해 x 가 −1,652,266 µm 같은 값이 됐다."""
        # PGEE48 격자(4160.9 × 5294.0), pitch 를 알려주는 파일이 하나도 없다.
        X, Y = 50 * 4160.9 + 2000.0, 20 * 5294.0 + 2000.0
        folder = _make_bare_folder(tmp_path / "pgee", [("a", X, Y, 50, 20)])
        assert wg.camtek_geometry(folder) is None
        c = camtek_ini.load_folder(folder)["a"]
        # TB500 pitch 로 계산한 쓰레기값이 아니라 **절대좌표**가 나온다.
        assert c.source == "camtek_abs"
        assert (c.x, c.y) == (float(int(X)), float(int(Y)))
        assert c.x > 0 and c.y > 0                   # 옛 폴백이면 −1,652,266 같은 값이었다

    def test_out_of_range_pitch_rejected(self, tmp_path):
        """비상식적인 값(µm 아님)은 후보로 읽지 않는다."""
        folder = _make_camtek_folder(
            tmp_path, 10000.0, 12000.0,
            [("a", 34500.0, 26400.0, 3, 2)], params_pitch=(0.5, 0.6))
        assert wg.camtek_geometry(folder) is None

    def test_pitch_inconsistent_with_colrow_rejected(self, tmp_path):
        """``Col == floor(X/pitch)`` 검산에 실패하면 그 값을 쓰지 않는다.

        (엉뚱한 키를 읽었거나 INI 가 다른 웨이퍼의 것일 때 조용히 틀린 좌표를 내지 않게)"""
        folder = _make_camtek_folder(
            tmp_path, 10000.0, 12000.0,
            [("a", 34500.0, 26400.0, 3, 2), ("b", 51000.0, 60500.0, 5, 5)],
            params_pitch=(20000.0, 12000.0))
        assert wg.camtek_geometry(folder) is None

    def test_no_grabbing_ini_means_no_coords(self, tmp_path):
        """검산할 INI 항목이 없으면 좌표 자체를 만들 수 없다 — 기하도 확정하지 않는다."""
        folder = tmp_path / "imgs"
        folder.mkdir()
        (folder / "Params_WaferInfo.ini").write_text(
            _params_ini(10000.0, 12000.0), encoding="utf-8")
        assert wg.camtek_geometry(folder) is None

    def test_no_warning_when_folder_has_no_camtek_ini(self, tmp_path, caplog):
        """★ Camtek INI 가 아예 없는 폴더(KLA·LIVE 슬롯)는 **조용히** None.

        경고는 '변환할 항목이 있는데 pitch 를 못 정한' 진짜 문제일 때만 낸다 —
        안 그러면 KLA 폴더마다 무의미한 경고가 쌓인다."""
        import logging
        folder = tmp_path / "kla"
        folder.mkdir()
        (folder / "res.001").write_text("DiePitch 1.0e+004 1.0e+004;\n", encoding="utf-8")
        assert wg.has_camtek_entries(folder) is False
        with caplog.at_level(logging.WARNING, logger="aoi.coords"):
            assert wg.camtek_geometry(folder) is None
        assert caplog.records == []

    def test_warns_when_camtek_entries_cannot_be_converted(self, tmp_path, caplog):
        """반대로 Camtek INI 항목이 있는데 pitch 를 못 정하면 **경고를 남긴다**."""
        import logging
        folder = _make_bare_folder(tmp_path / "pgee",
                                   [("a", 50 * 4160.9 + 2000.0, 20 * 5294.0 + 2000.0, 50, 20)])
        assert wg.has_camtek_entries(folder) is True
        with caplog.at_level(logging.WARNING, logger="aoi.coords"):
            assert wg.camtek_geometry(folder) is None
        assert any("die pitch" in r.message for r in caplog.records)

    def test_constants_need_a_meaningful_check(self, tmp_path):
        """모든 항목이 Col=0·Row=0 이면 어떤 pitch 든 통과한다 — 상수를 추정으로 쓰지 않는다."""
        folder = _make_bare_folder(tmp_path / "origin",
                                   [("a", 100.0, 200.0, 0, 0)])
        assert wg.camtek_geometry(folder) is None


# ---------------------------------------------------------------------------
# ★ 골든 테스트 — 사용자 도출 규칙의 실측 4개 사례 (장비 화면 정답)
#
#   col = floor(X/DieStep_X) − 2
#   row = ceil(Diameter/DieStep_Y) − floor(Y/DieStep_Y)
#   x/y = floor(나머지)
#
# 서로 다른 3개 device 에서 전부 성립함이 확인된 규칙이다.  특히 사례 1(pitch_y
# 31831.4 → row 기준 10)은 row 기준을 상수 7 로 박았을 때 row=−2 가 나오던 —
# 즉 "다른 디바이스에서 좌표 엉망" 을 재현하던 케이스다.
# ---------------------------------------------------------------------------
_GOLDEN = [
    # (라벨, X, Y, step_x, step_y, 기대 col, row, die_x, die_y)
    ("dev-A(row 기준 10)", 105192.662706218, 295526.990594525,
     25022.9, 31831.4, 2, 1, 5101.0, 9044.0),
    ("BNN-PIDS3 #1", 204859.860137703, 267707.809283319,
     37247.7, 44905.4, 3, 2, 18621.0, 43180.0),
    ("BNN-PIDS3 #2", 229010.636221118, 354559.829210790,
     37247.7, 44905.4, 4, 0, 5524.0, 40222.0),
    ("dev-B", 182032.376963739, 242041.055466280,
     27474.5, 47835.5, 4, 2, 17185.0, 2863.0),
]


class TestGoldenEquipmentExamples:
    @pytest.mark.parametrize("label,X,Y,sx,sy,col,row,dx,dy", _GOLDEN,
                             ids=[g[0] for g in _GOLDEN])
    def test_end_to_end(self, tmp_path, label, X, Y, sx, sy, col, row, dx, dy):
        import math
        ci, ri = math.floor(X / sx), math.floor(Y / sy)
        folder = tmp_path / "slot"
        folder.mkdir()
        (folder / "Params_WaferInfo.ini").write_text(
            f"[Geometry]\nDieStep_X={sx:.6f}\nDieStep_Y={sy:.6f}\n"
            f"[Geometric]\nDiameter=300000.000000\n", encoding="utf-8")
        (folder / "ColorImageGrabingInfo.ini").write_text(
            f"[img.jpeg]\nX={X}\nY={Y}\nCol={ci}\nRow={ri}\n", encoding="utf-8")
        c = camtek_ini.load_folder(folder)["img"]
        assert (c.col, c.row, c.x, c.y) == (col, row, dx, dy)

    def test_die_y_is_floored_not_rounded(self, tmp_path):
        """★ 버림 가드 — 나머지 43180.809 는 43180 이다(반올림 43181 아님)."""
        _, X, Y, sx, sy, *_ = _GOLDEN[1]
        rem = Y - 5 * sy
        assert rem == pytest.approx(43180.809, abs=1e-3)   # 반올림이면 43181 이 될 값
        # 위 end_to_end 가 43180.0 을 단언하므로 이 사실이 회귀 가드로 작동한다.

    def test_row_base_follows_diameter(self, tmp_path):
        """row 기준이 ceil(Diameter/pitch_y) 로 **파일의 Diameter 를 따라간다**."""
        folder = tmp_path / "slot"
        folder.mkdir()
        # pitch_y 31831.4, Diameter 200 mm → 기준 ceil(200000/31831.4) = 7 (300 mm 면 10)
        (folder / "Params_WaferInfo.ini").write_text(
            "[Geometry]\nDieStep_X=25022.900000\nDieStep_Y=31831.400000\n"
            "[Geometric]\nDiameter=200000.000000\n", encoding="utf-8")
        (folder / "ColorImageGrabingInfo.ini").write_text(
            "[img.jpeg]\nX=105192.662706218\nY=95526.990594525\nCol=4\nRow=3\n",
            encoding="utf-8")
        c = camtek_ini.load_folder(folder)["img"]
        assert c.row == 7 - 3                  # 300 mm 였다면 10 − 3 = 7


# ---------------------------------------------------------------------------
# INI 읽기 견고성 — '파일은 있는데 값이 없다' 로 조용히 실패하던 경로
# ---------------------------------------------------------------------------
class TestIniReading:
    _BODY = "[Geometry]\r\nDieStep_X=37247.700000\r\nDieStep_Y=44905.400000\r\n"

    @pytest.mark.parametrize("label,encoded", [
        ("utf-8", _BODY.encode("utf-8")),
        ("utf-8-bom", _BODY.encode("utf-8-sig")),
        ("utf-16-bom", _BODY.encode("utf-16")),      # ★ 장비가 이렇게 쓰는 경우가 있다
        ("cp949", _BODY.encode("cp949")),
    ])
    def test_encodings(self, tmp_path, label, encoded):
        """UTF-16 도 읽어야 한다 — UTF-8 로만 읽으면 예외 없이 키를 못 찾는다."""
        p = tmp_path / "Params_WaferInfo.ini"
        p.write_bytes(encoded)
        assert wg._read_key(p, "DieStep_X") == 37247.7

    @pytest.mark.parametrize("enc", ["utf-8", "utf-8-sig", "utf-16"])
    def test_coord_ini_encodings(self, tmp_path, enc):
        """★ 좌표 INI 도 UTF-16 을 읽어야 한다.

        UTF-8 로만 읽으면 UTF-16 파일이 예외 없이 깨져 **항목 0건**이 된다 —
        '파일은 멀쩡히 있는데 좌표가 안 나온다' 로 조용히 실패하던 경로.
        (UTF-16LE 의 `X=1` 은 `X\\x00=\\x001` 로 디코드되는데 `\\x00` 은 유효한 UTF-8 이라
        치환문자조차 안 남고 `^(\\w+)\\s*=` 패턴이 빗나간다.)"""
        folder = tmp_path / "s"
        folder.mkdir()
        body = ("[a.jpeg]\r\nX=272647.761085349\r\nY=165675.853620774\r\n"
                "Col=7\r\nRow=3\r\n")
        (folder / "ColorImageGrabingInfo.ini").write_bytes(body.encode(enc))
        (folder / "Params_WaferInfo.ini").write_bytes(
            "[Geometry]\r\nDieStep_X=37247.700000\r\nDieStep_Y=44905.400000\r\n".encode(enc))
        assert len(camtek_ini.load_raw_folder(folder)) == 1
        c = camtek_ini.load_folder(folder)["a"]
        assert (c.col, c.row, c.source) == (5, 4, "camtek_ini")

    def test_comma_decimal_is_rejected_not_truncated(self, tmp_path):
        """★ `37247,700000`(쉼표 소수점)을 37247.0 으로 조용히 받지 않는다.

        잘라 읽으면 검산을 통과해 버려 **틀린 pitch 로 좌표를 만들** 수 있다."""
        p = tmp_path / "Params_WaferInfo.ini"
        p.write_text("[Geometry]\nDieStep_X=37247,700000\n", encoding="utf-8")
        assert wg._read_key(p, "DieStep_X") is None


# ---------------------------------------------------------------------------
# ★ 레시피마다 INI 의 Row 원점이 다르다 — 15호기 실측 (docs §6-F)
#
# 같은 결함을 두 레시피가 각각 찍은 쌍에서 Col 은 같은데 Row 만 1 어긋난다.
# 그래서 (1) 좌표 변환은 INI 필드를 안 쓰고 floor(좌표/pitch) 로 유도하고,
# (2) 검산은 X 만 등호를 요구하고 Y 는 레시피별 상수만 본다.
# ---------------------------------------------------------------------------
_PX15, _PY15 = 37248.3, 44905.3

# (stem, X, Y, Col, Row, RecipeNumber) — 15호기 실측 11건 전수
_W15 = [
    ("e0", 233772.904192119, 163340.112966944, 6, 3, 1),
    ("e1", 188062.270142103,  58551.454939582, 5, 2, 2),
    ("e2", 200452.201247592,  87166.935915637, 5, 2, 2),
    ("e3", 225291.594265483, 117680.444167723, 6, 3, 2),
    ("e4", 261809.911748775,  91042.1416570301, 7, 3, 2),
    ("e5", 302342.430001159, 160651.700554255, 8, 4, 2),
    ("h0", 268193.498386342, 238680.107201584, 7, 6, 2),
    # ↓ 같은 결함을 두 레시피가 각각 찍은 쌍 — 결정적 증거
    ("f_r1", 177040.194847314, 164273.0968992,  4, 3, 1),
    ("f_r2", 177039.528173140, 164272.800876176, 4, 4, 2),
    ("a_r1", 312572.038914189, 168315.709590306, 8, 3, 1),
    ("a_r2", 312570.607145627, 168316.092231146, 8, 4, 2),
]


def _make_w15_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "w15"
    folder.mkdir(parents=True)
    (folder / "ColorImageGrabingInfo.ini").write_text(
        _grabbing_ini_rec(_W15), encoding="utf-8")
    (folder / "Params_WaferInfo.ini").write_text(
        _params_ini(_PX15, _PY15) + "[Geometric]\nDiameter=300000.000000\n",
        encoding="utf-8")
    return folder


class TestRecipeRowOriginDiffers:
    def test_diestep_is_accepted(self, tmp_path):
        """★ 예전엔 여기서 검산이 실패해 멀쩡한 DieStep 을 거부했다."""
        geom = wg.camtek_geometry(_make_w15_folder(tmp_path))
        assert geom is not None
        assert (geom.pitch_x, geom.pitch_y) == (_PX15, _PY15)
        assert geom.source == "Params_WaferInfo.ini:DieStep_X/DieStep_Y"

    def test_die_local_xy_stays_inside_the_die(self, tmp_path):
        """die 내부 좌표는 ``0 ≤ v < pitch`` 다.

        INI 의 Row 를 그대로 쓰던 예전 코드는 recipe2 에서 y = −15349 를 냈다 —
        die 안의 위치로 불가능한 값이다."""
        coords = camtek_ini.load_folder(_make_w15_folder(tmp_path))
        assert len(coords) == len(_W15)
        for stem, c in coords.items():
            assert c.source == "camtek_ini"
            assert 0 <= c.x < _PX15, stem
            assert 0 <= c.y < _PY15, stem

    @pytest.mark.parametrize("r1,r2", [("f_r1", "f_r2"), ("a_r1", "a_r2")])
    def test_same_defect_two_recipes_agree(self, tmp_path, r1, r2):
        """★ 결정적 증거 — 같은 결함이면 레시피가 달라도 같은 die·같은 좌표다.

        INI 는 Row 를 3 / 4 로 다르게 적었지만 물리 위치는 1.5 µm 이내로 같다."""
        coords = camtek_ini.load_folder(_make_w15_folder(tmp_path))
        a, b = coords[r1], coords[r2]
        assert (a.col, a.row) == (b.col, b.row)      # 같은 die
        assert abs(a.x - b.x) <= 2 and abs(a.y - b.y) <= 2

    def test_mixed_diffs_within_one_recipe_still_rejected(self, tmp_path):
        """완화는 **레시피별**로만 한다 — 한 레시피 안에서 섞이면 여전히 거부."""
        folder = tmp_path / "mixed"
        folder.mkdir(parents=True)
        (folder / "ColorImageGrabingInfo.ini").write_text(
            _grabbing_ini_rec([("a", 233772.904192119, 163340.112966944, 6, 3, 1),
                               ("b", 188062.270142103, 58551.454939582, 5, 2, 1)]),
            encoding="utf-8")
        (folder / "Params_WaferInfo.ini").write_text(
            _params_ini(_PX15, _PY15), encoding="utf-8")
        assert wg.camtek_geometry(folder) is None

    def test_wrong_device_pitch_still_rejected(self, tmp_path):
        """★ Y 완화가 안전장치를 약화시키지 않았다 — X 등호가 그대로 걸러낸다.

        PGEE48 격자(4160.9 × 5294.0)를 15호기 데이터에 넣으면 Δcol 이 38..67 로
        발산해 **그 값이 채택되지 않는다**(뒤 후보로 넘어간다)."""
        folder = tmp_path / "w15"
        folder.mkdir(parents=True)
        (folder / "ColorImageGrabingInfo.ini").write_text(
            _grabbing_ini_rec(_W15), encoding="utf-8")
        (folder / "Params_WaferInfo.ini").write_text(
            _params_ini(4160.9, 5294.0), encoding="utf-8")
        assert wg._grid_check(folder, 4160.9, 5294.0)[0] is False
        geom = wg.camtek_geometry(folder)
        assert geom is None or (geom.pitch_x, geom.pitch_y) != (4160.9, 5294.0)

    def test_offset_is_logged_not_silent(self, tmp_path, caplog):
        """조용한 폴백 금지 — 어긋난 채로 통과했으면 기록을 남긴다."""
        import logging
        with caplog.at_level(logging.INFO, logger="aoi.coords"):
            assert wg.camtek_geometry(_make_w15_folder(tmp_path)) is not None
        assert any("INI Row" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# ★ LIVE 파일명 ↔ Camtek INI 실측 쌍 — 같은 웨이퍼(W7548303XYC2)의 같은 결함
#
# 1차 리뷰를 거친 사진은 LIVE 파일명을 쓰고 원본은 Camtek INI 를 쓴다.  두 소스가 같은
# 규약이어야 (col,row) ±1 버킷 게이트에 함께 들어온다.  예전엔 LIVE row 를 −1 정규화해
# Δrow 가 2 가 되면서 **이 쌍이 매치 실패**했다(→ docs/디바이스_하드코딩_조사.md §6-G).
# ---------------------------------------------------------------------------
_RDL4_PX, _RDL4_PY = 37247.9, 44905.3
_RDL4_LIVE = ("TB500_RDL4 - Multi_FDV-RDL4_W7548303XYC2_3_0_"
              "27582.7103387744_43144.6146330819_Foreign Material.jpg")
# (stem, X, Y, Col, Row, RecipeNumber) — 같은 결함을 두 레시피가 각각 찍은 쌍
_RDL4_INI = [("176573.312577.c.597332200.1",
              176574.75988497, 312576.825126637, 4, 6, 1),
             ("176573.312578.c.1643013205.2",
              176573.932796436, 312574.83842825, 4, 6, 2)]


# 사용자가 사진으로 확인한 LIVE↔Camtek 쌍 8건 (RDL4 로트, 웨이퍼 2장).
# (Camtek X, Y, Col, Row, recipe, LIVE col, LIVE row, LIVE x, LIVE y)
_RDL4_PAIRS = [
    # W7548306XYA2
    (51210.8, 183932.3, 1, 4, 2, 0, 2, 13969.17941697, 4306.11285221326),
    (163831.252093174, 191432.114730261, 4, 4, 2, 3, 2, 14841.5559416573, 11810.704052486),
    (197846.319956548, 46706.4001757557, 5, 1, 2, 4, 5, 11609.3842929103, 1801.71715355555),
    # W7548304XYG4
    (211050.7, 214374.9, 5, 4, 2, 4, 2, 24815.9486854024, 34749.6600228519),
    (255350.424751695, 116718.981250212, 6, 2, 1, 5, 4, 31863.2366998812, 26908.4427394723),
    (294490.590623552, 139212.097250194, 7, 3, 1, 6, 3, 33756.2046850637, 4495.03684559136),
    (294507.624563042, 139202.508245703, 7, 3, 1, 6, 3, 33756.2046850637, 4495.03684559136),
]


class TestLiveTokenOffsetIsSystematic:
    """★ LIVE 토큰과 Camtek 이 **일정한 오프셋**을 갖는다 — 이 로트에서는 (+1, −1).

    사용자가 사진으로 확인한 8쌍 전부 같은 값이다.  die 내부 좌표는 20 µm 안에서 겹친다.
    즉 같은 결함인데 **표시 규약만 다르다**(→ ``docs/디바이스_하드코딩_조사.md`` §6-H).

    ⚠ 이 오프셋은 로트마다 다르다 — 보고서 예시 쌍(다른 제품)은 0 이다.  그래서 고정
    보정을 넣지 않고 ``(col,row) ±1`` 이웃 게이트가 흡수하게 둔다.  게이트의 여유를
    쓰고 있다는 뜻이므로, 그 예산을 더 줄이는 변경(게이트 축소)은 하지 말 것.
    """

    @pytest.mark.parametrize("row", _RDL4_PAIRS)
    def test_offset_is_plus1_minus1_and_die_coords_agree(self, tmp_path, row):
        import math
        X, Y, _C, _R, _rec, lc, lr, lx, ly = row
        px, py, rt = 37247.9, 44905.3, 7          # 앱의 현재 규약
        xi, yi = math.floor(X / px), math.floor(Y / py)
        ccol, crow = xi - 2, rt - yi
        assert (lc - ccol, lr - crow) == (1, -1)
        assert math.hypot(lx - (X - xi * px), ly - (Y - yi * py)) <= 20

    def test_gate_absorbs_the_offset(self):
        """★ 그래서 **매칭은 된다** — ±1 이웃 안에 들어온다."""
        for X, Y, _C, _R, _rec, lc, lr, _lx, _ly in _RDL4_PAIRS:
            import math
            xi, yi = math.floor(X / 37247.9), math.floor(Y / 44905.3)
            assert abs(lc - (xi - 2)) <= 1 and abs(lr - (7 - yi)) <= 1

    def test_col_origin_is_derived_so_col_matches_live(self, tmp_path):
        """★ ``col_origin`` 을 ``Center_X`` 에서 유도하면 col 이 LIVE 와 **정확히** 맞는다.

        상수 2 를 쓰면 이 웨이퍼에서 ``col = 1 − 2 = −1`` 이 나온다 — 0-based 웨이퍼 맵에
        없는 값이다.  그게 상수가 틀렸다는 증거였다(§6-H)."""
        folder = tmp_path / "rdl4"
        folder.mkdir()
        (folder / "ColorImageGrabingInfo.ini").write_text(_grabbing_ini_rec(
            [(f"p{i}", X, Y, C, R, rec)
             for i, (X, Y, C, R, rec, *_) in enumerate(_RDL4_PAIRS)]), encoding="utf-8")
        (folder / "Params_WaferInfo.ini").write_text(
            _params_ini(37247.9, 44905.3)
            + "[Geometric]\nDiameter=300000.000000\nCenter_X=167448.973100\n",
            encoding="utf-8")
        geom = wg.camtek_geometry(folder)
        assert geom is not None and geom.col_origin == 1     # 상수였다면 2
        coords = camtek_ini.load_folder(folder)
        for i, (*_rest, lc, _lr, _lx, _ly) in enumerate(_RDL4_PAIRS):
            c = coords[f"p{i}"]
            assert c.col == lc, "col 이 LIVE 토큰과 어긋난다"
            assert c.col >= 0, "0-based 맵에 음수 col 은 없다"

    def test_col_origin_falls_back_when_center_is_unrecorded(self, tmp_path):
        """``Center_X=0`` 은 값이 아니라 '기록 안 됨' — 상수로 폴백한다.

        실물(W6458244XYE1)이 그렇고, 같은 파일에 ``MeasuredDiameter=0`` 이 함께 온다.
        그 웨이퍼는 KLA 가 ``col_origin=2`` 를 확인해 줬으므로 폴백이 맞다(§6-G)."""
        folder = tmp_path / "nocenter"
        folder.mkdir()
        (folder / "ColorImageGrabingInfo.ini").write_text(
            _grabbing_ini_rec([("a", 278197.718521499, 216679.594004774, 7, 4, 1)]),
            encoding="utf-8")
        (folder / "Params_WaferInfo.ini").write_text(
            _params_ini(37247.9, 44905.3)
            + "[Geometric]\nDiameter=300000.000000\nCenter_X=0.000000\n", encoding="utf-8")
        geom = wg.camtek_geometry(folder)
        assert geom is not None and geom.col_origin == 2
        assert camtek_ini.load_folder(folder)["a"].col == 5   # KLA 와 맞는 값


class TestLiveMatchesIni:
    @staticmethod
    def _folder(tmp_path: Path) -> Path:
        folder = tmp_path / "W7548303XYC2"
        folder.mkdir(parents=True)
        (folder / "ColorImageGrabingInfo.ini").write_text(
            _grabbing_ini_rec(_RDL4_INI), encoding="utf-8")
        (folder / "Params_WaferInfo.ini").write_text(
            _params_ini(_RDL4_PX, _RDL4_PY) + "[Geometric]\nDiameter=300000.000000\n",
            encoding="utf-8")
        return folder

    def test_geometry_resolves(self, tmp_path):
        """이 로트는 두 레시피 모두 INI Col/Row 가 좌표와 일치한다 — DieStep 채택."""
        geom = wg.camtek_geometry(self._folder(tmp_path))
        assert geom is not None
        assert (geom.pitch_x, geom.pitch_y) == (_RDL4_PX, _RDL4_PY)

    @pytest.mark.parametrize("stem", [e[0] for e in _RDL4_INI])
    def test_live_and_ini_land_in_the_same_bucket(self, tmp_path, stem):
        """★ 이 버그의 회귀 가드 — (col,row) 차가 ±1 안이고 die 좌표가 1 µm 안이다."""
        from aoi_verification.app.coords import camtek_live
        ini = camtek_ini.load_folder(self._folder(tmp_path))[stem]
        live = camtek_live.resolve(Path(_RDL4_LIVE))
        assert live is not None
        assert abs(live.col - ini.col) <= 1, "col 이 버킷 밖이다"
        assert abs(live.row - ini.row) <= 1, "row 가 버킷 밖이다 (예전 −1 정규화의 증상)"
        # 두 레시피가 같은 결함을 각각 찍어 원시 X/Y 가 2 µm 안에서 다르다.
        assert abs(live.x - ini.x) <= 2 and abs(live.y - ini.y) <= 2

    def test_neighbor_gate_actually_finds_it(self, tmp_path):
        """게이트를 **실제로 태워** 후보로 잡히는지 본다(단언만 하지 않는다)."""
        pytest.importorskip("PyQt6.QtCore")      # coord_matcher 가 모듈 수준에서 쓴다
        from aoi_verification.app.coords import camtek_live
        from aoi_verification.app.workers.coord_matcher import _match_neighbors
        live = camtek_live.resolve(Path(_RDL4_LIVE))
        coords = camtek_ini.load_folder(self._folder(tmp_path))
        val_map: dict = {}
        for stem, c in coords.items():
            val_map.setdefault((c.col, c.row), []).append(
                (Path(f"{stem}.jpeg"), c.x, c.y))
        got = _match_neighbors(live.x, live.y, live.col, live.row, val_map, tol=500.0)
        assert got, "LIVE 결함이 Camtek 후보를 하나도 못 찾았다 = 매치 실패"
        assert {p.stem for p, _ in got} <= {e[0] for e in _RDL4_INI}


# ---------------------------------------------------------------------------
# ★ 한 실행 안에서 좌표 프레임을 통일한다
#
# die pitch 검산은 웨이퍼 폴더마다 독립이라 ref 는 통과하고 val 은 실패할 수 있다.
# 그러면 row 규약이 달라(row_total−y_index vs −Row) (col,row)±1 게이트가 절대 안 맞고
# 그 슬롯이 통째로 '매치 실패' 가 된다.
# ---------------------------------------------------------------------------
class TestFrameUnification:
    @staticmethod
    def _two_folders(tmp_path: Path):
        """pitch 를 확정하는 폴더 하나 + 못 하는 폴더 하나."""
        from aoi_verification.app.coords import resolve_batch
        good = _make_camtek_folder(
            tmp_path / "good", CAMTEK_PITCH_X, CAMTEK_PITCH_Y,
            [("g", 3 * CAMTEK_PITCH_X + 500.0, 2 * CAMTEK_PITCH_Y + 500.0, 3, 2)])
        bad = _make_bare_folder(         # pitch 를 알려주는 파일이 없는 다른 격자
            tmp_path / "bad",
            [("b", 50 * 4160.9 + 2000.0, 20 * 5294.0 + 2000.0, 50, 20)])
        return resolve_batch, good / "g.jpeg", bad / "b.jpeg"

    def test_mixed_frames_are_unified_to_absolute(self, tmp_path):
        """★ 섞이면 **전부** 절대좌표로 내린다."""
        resolve_batch, gp, bp = self._two_folders(tmp_path)
        got = resolve_batch([gp, bp])
        assert {c.source for c in got.values()} == {"camtek_abs"}
        # 절대좌표라 x/y 가 원시 wafer 좌표 그대로다.
        assert got[gp].x == float(int(3 * CAMTEK_PITCH_X + 500.0))

    def test_uniform_frame_is_left_alone(self, tmp_path):
        """섞이지 않으면 손대지 않는다 — die-내부 좌표를 유지한다."""
        resolve_batch, gp, _bp = self._two_folders(tmp_path)
        got = resolve_batch([gp])
        assert got[gp].source == "camtek_ini"
        assert got[gp].x == 500.0

    def test_unification_is_announced(self, tmp_path, caplog):
        """조용히 내리지 않는다 — 이유를 경고로 남긴다."""
        import logging
        resolve_batch, gp, bp = self._two_folders(tmp_path)
        with caplog.at_level(logging.WARNING, logger="aoi.coords"):
            resolve_batch([gp, bp])
        assert any("절대 wafer 좌표로 통일" in r.message for r in caplog.records)

    @pytest.mark.parametrize("src", ["camtek_live", "kla"])
    def test_unfixable_frames_are_announced(self, tmp_path, caplog, src):
        """★ 절대좌표로 **되돌릴 수 없는** 소스가 섞이면 조용히 전멸하지 않게 경고한다.

        LIVE 파일명·KLA 는 die-내부 좌표만 갖고 있어 통일이 불가능하다.  통일 대신
        '어디를 봐야 하는지' 를 남긴다 — 안 그러면 슬롯이 통째로 매치 실패인데 이유가
        아무 데도 안 남는다."""
        import logging
        from aoi_verification.app.coords import resolve_batch
        bad = _make_bare_folder(          # pitch 를 못 정하는 폴더 → camtek_abs
            tmp_path / "bad",
            [("b", 50 * 4160.9 + 2000.0, 20 * 5294.0 + 2000.0, 50, 20)])
        good = tmp_path / "good"
        good.mkdir()
        if src == "camtek_live":
            other = good / "R_DEV_W1_3_2_Defect_12345_6789.jpg"
            other.touch()
        else:
            (good / "res.001").write_text(
                'DiePitch 3.7247930000e+004 4.4905340000e+004;\n'
                'SampleTestPlan 2\n  -3 0 \n  0 0 ;\n'
                'DefectRecordSpec 7 DEFECTID X Y XREL YREL XINDEX YINDEX ;\n'
                'DefectList;\nTiffFileName shot.jpg;\nDefectList\n'
                ' 1 0.0 0.0 100.0 200.0 0 0;\nEndOfFile;\n', encoding="utf-8")
            other = good / "shot.jpg"
            other.touch()
        with caplog.at_level(logging.WARNING, logger="aoi.coords"):
            got = resolve_batch([other, bad / "b.jpeg"])
        assert got[other] is not None and got[other].source == src
        assert any("통일할 수 없어" in r.message and src in r.message
                   for r in caplog.records)


# ---------------------------------------------------------------------------
# ★ die pitch 를 몰라도 매칭은 된다 — 절대 wafer 좌표 폴백
#
# ColorImageGrabingInfo.ini 의 X/Y 만으로 매칭이 성립한다.  die 로 쪼개는 건
# (a) 화면·엑셀의 die-내부 좌표 표기와 (b) KLA 교차 매칭에만 필요하다.
# ---------------------------------------------------------------------------
class TestAbsoluteFallback:
    def test_matching_works_without_die_pitch(self, tmp_path):
        """같은 결함은 매칭되고, 옆 die 의 같은 상대위치 결함은 기각된다."""
        pytest.importorskip("PyQt6.QtCore")   # coord_matcher 가 모듈 수준에서 쓴다
        from aoi_verification.app.workers import coord_matcher as cm

        X, Y, P = 101023.784185837, 157436.626491902, 37247.7
        folder = _make_bare_folder(tmp_path / "s", [
            ("a", X, Y, 4, 4),
            ("b", X + 0.2, Y + 0.1, 4, 4),      # 같은 결함(재스캔)
            ("c", X + P, Y, 5, 4),              # 옆 die 의 **같은 상대위치**
        ])
        m = camtek_ini.load_folder(folder)
        assert {v.source for v in m.values()} == {"camtek_abs"}

        vmap = {}
        for stem in ("b", "c"):
            d = m[stem]
            vmap.setdefault((d.col, d.row), []).append((Path(stem), d.x, d.y))
        a = m["a"]
        got = cm._match_neighbors(a.x, a.y, a.col, a.row, vmap, 500.0)
        assert [p.name for p, _ in got] == ["b"]

    def test_absolute_is_stricter_than_die_local(self, tmp_path):
        """★ 절대좌표는 die-내부 비교보다 **엄격**하다.

        die-내부로 재면 인접 die 의 같은 상대위치 결함이 거리 0 으로 보여 오매칭했다.
        절대좌표로 재면 정확히 1 pitch 만큼 떨어져 있어 기각된다."""
        pytest.importorskip("PyQt6.QtCore")   # coord_matcher 가 모듈 수준에서 쓴다
        from aoi_verification.app.workers import coord_matcher as cm

        X, Y, P = 101023.8, 157436.6, 37247.7
        folder = _make_bare_folder(tmp_path / "s",
                                   [("a", X, Y, 4, 4), ("c", X + P, Y, 5, 4)])
        m = camtek_ini.load_folder(folder)
        a, c = m["a"], m["c"]
        vmap = {(c.col, c.row): [(Path("c"), c.x, c.y)]}
        assert cm._match_neighbors(a.x, a.y, a.col, a.row, vmap, 500.0) == []

    def test_col_is_exact_row_is_not_faked(self, tmp_path):
        """col 은 pitch 없이도 정확하다.  row 는 기준을 모르므로 꾸며내지 않는다."""
        folder = _make_bare_folder(tmp_path / "s", [("a", 101023.8, 157436.6, 4, 4)])
        c = camtek_ini.load_folder(folder)["a"]
        assert c.col == 4 - 2          # Col − CAMTEK_COL_OFFSET, pitch 불필요
        assert c.row < 0               # 음수 = 장비 화면 값이 아님이 드러난다(버킷 전용)

    def test_display_shows_absolute_and_admits_unknown_row(self, tmp_path):
        """표시 문구는 있는 사실만 적는다 — col + 절대 wafer 좌표, row 는 '미상'."""
        from aoi_verification.app import i18n
        from aoi_verification.app.coords import single_info

        folder = _make_bare_folder(tmp_path / "s", [("a", 101023.8, 157436.6, 4, 4)])
        (folder / "a.jpeg").write_bytes(b"")
        lines = single_info.coord_lines(folder / "a.jpeg")
        assert lines[0] == i18n.KO.DEFECT_COL_ONLY_FMT.format(col=2)
        assert lines[1] == i18n.KO.DEFECT_ABS_XY_FMT.format(x=101023.0, y=157436.0)
        # die-내부 좌표인 척하지 않는다
        assert not any(l.startswith("x ") for l in lines)


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
    def test_zero_x_from_sample_test_plan(self):
        """``zero_x`` 만 SampleTestPlan XINDEX 최솟값의 부호 반전으로 산출한다."""
        g = wg.parse_kla_header(_kla_header())
        assert g is not None
        assert g.zero_x == 3                       # min XINDEX = −3
        assert g.pitch_x == pytest.approx(37247.93)
        assert g.pitch_y == pytest.approx(44905.34)
        assert g.source == "DiePitch+SampleTestPlan"

    def test_zero_y_is_not_derived_from_sample_test_plan(self):
        """★ ``zero_y`` 는 산출하지 않는다 — SampleTestPlan 으로는 알 수 없다.

        그 목록에는 **검사한 die 만** 있어서, 맵 아래쪽 행이 통째로 미사용이면 최솟값이
        맵 원점과 어긋난다.  실측 자재가 정확히 그 경우로, 산출값은 3 이지만 정답은 4 다
        (거리 121 µm 로 확인된 Camtek 쌍과의 정렬이 근거 — models.KLA_ZERO_Y 주석 참조)."""
        g = wg.parse_kla_header(_kla_header())
        assert -min(-3, -3, 0, -1, 2) == 3         # SampleTestPlan 이 시사하는 값
        assert g.zero_y == KLA_ZERO_Y == 4         # 실제로 쓰는 값

    def test_other_device_grid_changes_zero_x_only(self):
        """격자가 다른 자재는 zero_x 가 따라 바뀐다(zero_y 는 상수)."""
        g = wg.parse_kla_header(_kla_header("  -1 -4\n  0 0\n  5 2 "))
        assert g.zero_x == 1
        assert g.zero_y == KLA_ZERO_Y

    def test_no_sample_test_plan_falls_back(self):
        """SampleTestPlan 이 없으면 zero_x 도 상수(pitch 는 그대로 읽는다)."""
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
        assert (c.col, c.row) == (2 + 1, -1 + KLA_ZERO_Y)  # zero_x=1(파일), zero_y=상수
        assert c.x == 1234                                 # round(XREL)
        assert c.y == round(44905.34 - 6789.0)             # DiePitchY − YREL


# ---------------------------------------------------------------------------
# ★ row 기준을 Center_Y 에서 유도 — 같은 결함이 두 형태로 확보된 실측 (§6-J)
#
# 18호기 폴더에 **같은 결함 사진이 두 이름으로** 있다(1차 리뷰 사진을 사용자가 직접
# 가져다 놓은 것 — 좌표가 파일명에 박힌 형태는 실제로 드물게 쓰인다):
#   · 점표기   `51216.183928.c.1345447384.1.jpeg`      → 절대 X/Y 를 INI 가 갖는다
#   · col/row  `TB500_RDL4 - Multi_…_0_2_13969.17…_4306.11…_Irregular Bump.jpg`
# 두 번째가 **장비가 스스로 계산한 col/row** 다.  옛 식 `ceil(Diameter/pitch_y)` 는
# row 를 1 크게 냈고(사용자 신고: 단일 사진 정보가 5,5 인데 실제는 5,4), Center_Y 에서
# 유도하면 두 형태가 오차 0 으로 맞는다.
# ---------------------------------------------------------------------------
_RDL4_PARAMS = (_params_ini(37247.9, 44905.3)
                + "[Geometric]\nDiameter=300000.000000\n"
                  "Center_X=167448.973100\nCenter_Y=179674.003400\n")


class TestRowTotalFromCenterY:
    """★ ``row_total`` 은 ``Center_Y`` 에서 유도한다 — 마지막 '완전히 들어오는' die 행.

    ``col_origin`` 이 '첫 완전 열' 인 것과 **같은 규칙의 반대쪽 축**이다(Camtek 은 Y 가
    아래로 커져서 마지막 행이 row 0)."""

    @pytest.mark.parametrize("stem,X,Y,C,R,rec,col,row", [
        # 18호기 실물 INI 값 ↔ **같은 사진**의 col/row 파일명 토큰
        ("51216.183928.c.1345447384.1",
         51217.07941697, 183927.312852213, 1, 4, 1, 0, 2),
        ("255348.116718.c.473266639.1",
         255350.636699881, 116719.042739472, 6, 2, 1, 5, 4),
    ])
    def test_matches_the_equipment_written_filename(self, tmp_path, stem, X, Y,
                                                    C, R, rec, col, row):
        folder = tmp_path / "rdl4"
        folder.mkdir()
        (folder / "ColorImageGrabingInfo.ini").write_text(
            _grabbing_ini_rec([(stem, X, Y, C, R, rec)]), encoding="utf-8")
        (folder / "Params_WaferInfo.ini").write_text(_RDL4_PARAMS, encoding="utf-8")
        geom = wg.camtek_geometry(folder)
        assert geom is not None
        assert (geom.col_origin, geom.row_total) == (1, 6), "옛 식이면 row_total=7"
        c = camtek_ini.load_folder(folder)[stem]
        assert (c.col, c.row) == (col, row)

    def test_unrecorded_center_y_keeps_the_old_formula(self, tmp_path):
        """``Center_Y`` 미기록이면 옛 식 ``ceil(Diameter/pitch_y)`` 로 폴백한다.

        장비 화면 정답 4사례(:class:`TestGoldenEquipmentExamples`)가 그 값이고
        ``Center_Y`` 를 갖고 있지 않다 — 그래서 그 사례들이 안 깨진다.
        ⚠ 두 식은 보통 1 다르다.  폴백은 '모를 때의 기존 동작 보존' 이다."""
        folder = tmp_path / "nocenter"
        folder.mkdir()
        (folder / "ColorImageGrabingInfo.ini").write_text(
            _grabbing_ini_rec([("a", 255350.6, 116719.0, 6, 2, 1)]), encoding="utf-8")
        (folder / "Params_WaferInfo.ini").write_text(
            _params_ini(37247.9, 44905.3)
            + "[Geometric]\nDiameter=300000.000000\nCenter_Y=0.000000\n",
            encoding="utf-8")
        geom = wg.camtek_geometry(folder)
        assert geom is not None and geom.row_total == 7      # ceil(300000/44905.3)

    def test_absurd_result_falls_back_and_warns(self, caplog):
        """비상식적 결과는 채택하지 않고 경고를 남긴다(조용한 폴백 금지).

        ``_col_origin`` 의 상식 범위 검사와 같은 장치다."""
        import logging
        import math
        with caplog.at_level(logging.WARNING, logger="aoi.coords"):
            got = wg._row_total(400000.0, 300000.0, 150.0)    # 행이 3665개 → 비상식
        assert got == math.ceil(300000.0 / 150.0)
        assert any("row 기준" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# ★ Center_X/Y 가 미기록(0)일 때의 2순위 중심 소스 — Wafer2Table.ini (§6-L)
#
# 실측(T254 `GX57001305`): 이 파일의 어파인 변환에서 푼 중심이 **장비 화면 정답**
# (col_origin=2, row_total=10)을 그대로 낸다.  `Params_WaferInfo.ini` 는 그 스캔에서
# Center_X=Center_Y=0.000000 이라 예전에는 추측(ceil)에 의존했다.
# ---------------------------------------------------------------------------
_W2T_REAL = (
    "[WAFER ALIGNMENT]\n"
    "Wafer2Table_X=          0.9999932263         0.0012932406   -166254.5886308713\n"
    "Wafer2Table_Y=         -0.0012932406         0.9999932263   -202412.7818198704\n"
    "Rotate  w2t=   -0.074098\n"
    "Offset  w2t= -166254.589  -202412.782\n"
)
# 파일이 담은 앵커 3점 — (table_x, table_y, wafer_x, wafer_y).  변환 방향의 증거다.
_W2T_ANCHORS = [
    (158331.042831, 186603.091890, -7681.375999, -16013.833047),
    (28730.142831, 186603.091890, -137284.037356, -15850.267591),
    (287931.942831, 186603.091890, 121915.429053, -16185.361044),
]


class TestWaferCenterFromWafer2Table:
    _T254_PARAMS = (_params_ini(14400.1, 31109.8)
                    + "[Geometric]\nDiameter=300000.000000\n"
                      "Center_X=0.000000\nCenter_Y=0.000000\n")

    @staticmethod
    def _folder(tmp_path: Path, *, w2t: str = _W2T_REAL, params: str = None) -> Path:
        folder = tmp_path / "t254"
        folder.mkdir()
        (folder / "ColorImageGrabingInfo.ini").write_text(
            # 실물: X=29649.1 Y=164545.2 → x_index 2, y_index 5 (장비 화면 0,5)
            _grabbing_ini_rec([("a", 29649.148148, 164545.207407, 2, 5, 1)]),
            encoding="utf-8")
        (folder / "Params_WaferInfo.ini").write_text(
            params or TestWaferCenterFromWafer2Table._T254_PARAMS, encoding="utf-8")
        if w2t:
            (folder / "Wafer2Table.ini").write_text(w2t, encoding="utf-8")
        return folder

    def test_anchor_points_confirm_transform_direction(self):
        """★ 파일의 변환은 ``wafer = M·table + t`` 다 — 앵커 3점이 3 µm 안에 재현된다.

        방향을 반대로 읽으면 중심이 음수가 나와 전부 틀린다."""
        import math
        a11, a12, t1 = 0.9999932263, 0.0012932406, -166254.5886308713
        a21, a22, t2 = -0.0012932406, 0.9999932263, -202412.7818198704
        for tx, ty, wx, wy in _W2T_ANCHORS:
            got = (a11 * tx + a12 * ty + t1, a21 * tx + a22 * ty + t2)
            assert math.hypot(got[0] - wx, got[1] - wy) <= 3.0

    def test_center_is_solved_from_the_transform(self, tmp_path):
        cx, cy = wg._wafer_center(self._folder(tmp_path))
        assert cx == pytest.approx(165993.7, abs=1.0)
        assert cy == pytest.approx(202628.8, abs=1.0)

    def test_matches_equipment_screen(self, tmp_path):
        """★ 여기서 푼 중심이 장비 화면 정답 ``(0,5)`` 를 낸다."""
        folder = self._folder(tmp_path)
        geom = wg.camtek_geometry(folder)
        assert (geom.col_origin, geom.row_total) == (2, 10)
        assert (lambda c: (c.col, c.row))(camtek_ini.load_folder(folder)["a"]) == (0, 5)

    def test_recorded_center_wins(self, tmp_path):
        """``Center_X/Y`` 가 기록돼 있으면 그쪽이 1순위 — Wafer2Table 은 안 본다."""
        folder = self._folder(tmp_path, params=(
            _params_ini(14400.1, 31109.8)
            + "[Geometric]\nDiameter=300000.000000\n"
              "Center_X=167448.973100\nCenter_Y=179674.003400\n"))
        assert wg._wafer_center(folder) == (167448.9731, 179674.0034)

    def test_no_file_leaves_center_unknown(self, tmp_path):
        """파일이 없으면 ``None`` — 호출부가 옛 폴백을 쓴다(동작 불변)."""
        folder = self._folder(tmp_path, w2t="")
        assert wg._wafer_center(folder) == (None, None)
        geom = wg.camtek_geometry(folder)
        assert (geom.col_origin, geom.row_total) == (CAMTEK_COL_OFFSET, 10)

    @pytest.mark.parametrize("body,label", [
        ("[WAFER ALIGNMENT]\nWafer2Table_X= 0 0 -1\nWafer2Table_Y= 0 0 -1\n", "특이행렬"),
        ("[WAFER ALIGNMENT]\nWafer2Table_X= 1 0 -1e9\nWafer2Table_Y= 0 1 -1e9\n", "범위 밖"),
        ("[WAFER ALIGNMENT]\nWafer2Table_X= 1 0 -166254.6\n", "Y 줄 없음"),
    ])
    def test_bad_transform_is_rejected(self, tmp_path, body, label):
        """못 믿을 값은 채택하지 않는다 — 추측 좌표를 내느니 폴백이 낫다."""
        assert wg._wafer_center(self._folder(tmp_path, w2t=body)) == (None, None)


@pytest.mark.skipif(not _RDL4_REAL.exists(), reason="RDL4 실물 폴더 없음")
class TestRdl4RealFolders:
    """★ 실물 폴더 그대로 — 17호기·18호기, 점표기·col/row 두 형태가 **같은 값**을 낸다."""

    _EXPECT = {"W7548306XYA2": (0, 2), "W7548304XYG4": (5, 4)}

    @pytest.mark.parametrize("machine", ["17호기", "18호기"])
    def test_every_photo_form_agrees(self, machine):
        from aoi_verification.app.coords import camtek_live
        seen = 0
        for wafer, expect in self._EXPECT.items():
            folder = _RDL4_REAL / machine / wafer
            if not folder.exists():
                continue
            for img in sorted(folder.glob("*.jp*g")):
                coord = camtek_live.resolve(img) or camtek_ini.resolve(img)
                assert coord is not None, img.name
                assert (coord.col, coord.row) == expect, img.name
                seen += 1
        assert seen >= 2, "실물 사진을 못 읽었다"

    @pytest.mark.parametrize("machine", ["17호기", "18호기"])
    def test_die_index_range_matches_the_geometry(self, machine):
        """유도한 '완전히 들어오는 die' 범위가 실측 결함 전수의 인덱스 범위와 같다."""
        import math
        for wafer in self._EXPECT:
            folder = _RDL4_REAL / machine / wafer
            if not folder.exists():
                continue
            geom = wg.camtek_geometry(folder)
            assert geom is not None
            raw = camtek_ini.load_raw_folder(folder)
            assert raw
            xi = [math.floor(X / geom.pitch_x) for X, _Y, _c, _r in raw.values()]
            yi = [math.floor(Y / geom.pitch_y) for _X, Y, _c, _r in raw.values()]
            assert min(xi) == geom.col_origin           # 첫 완전 열
            assert max(yi) == geom.row_total            # 마지막 완전 행
            assert min(c.col for c in camtek_ini.load_folder(folder).values()) == 0
            assert min(c.row for c in camtek_ini.load_folder(folder).values()) == 0


# ---------------------------------------------------------------------------
# ★ KLA die 인덱스 원점을 SampleCenterLocation 에서 유도 — 사용자 장비 확인 7건 (§6-K)
#
# 옛 상수 KLA_ZERO_Y=4 는 **device 마다 다른 값**이었다.  T254 는 4 지만 TB500 은 3 이라,
# TB500 자재에서 KLA row 가 전 구간 1 컸다.
# ---------------------------------------------------------------------------
_KLA_REAL = (Path(__file__).resolve().parents[2] / "docs"
             / "KLA 좌표 예시(TB500, T254)")
# {폴더: (zero_x, zero_y, {사진 stem: 장비로 확인한 (col,row)})}
_KLA_TRUTH = {
    "2026-08-03-20-28_2": (3, 3, {                     # TB500  08235234EWA1
        "08235234ewa1_2_-1_7_1": (5, 2),
        "08235234ewa1_-2_-2_31_2": (1, 1),
        "08235234ewa1_-2_0_7_3": (1, 3)}),
    "2026-08-02-23-09_4": (9, 4, {                     # T254   00MEU018XYG1
        "00meu018xyg1_-1_4_23_1": (8, 8),
        "00meu018xyg1_4_3_23_2": (13, 7),
        "00meu018xyg1_4_-2_31_3": (13, 2),
        "00meu018xyg1_-8_-2_23_4": (1, 2)}),
}


@pytest.mark.skipif(not _KLA_REAL.exists(), reason="KLA 좌표 예시 폴더 없음")
class TestKlaZeroFromSampleCenter:
    @pytest.mark.parametrize("name", sorted(_KLA_TRUTH))
    def test_zero_matches_equipment(self, name):
        zx, zy, coords = _KLA_TRUTH[name]
        g = wg.kla_geometry(_KLA_REAL / name)
        assert (g.zero_x, g.zero_y) == (zx, zy)
        assert g.source == "DiePitch+SampleCenterLocation"
        got = kla_info.load_folder(_KLA_REAL / name)
        assert got, "실측 .001 을 못 읽었다"
        for stem, expect in coords.items():
            c = got[stem]
            assert (c.col, c.row) == expect, stem

    def test_zero_y_is_not_a_constant(self):
        """★ 두 device 가 서로 다른 ``zero_y`` 를 낸다 — 상수로 되돌리면 한쪽이 틀린다."""
        ys = {wg.kla_geometry(_KLA_REAL / n).zero_y for n in _KLA_TRUTH}
        assert ys == {3, 4}

    @pytest.mark.parametrize("name", sorted(_KLA_TRUTH))
    def test_sample_test_plan_range_agrees(self, name):
        """유도한 '완전히 들어오는 die' 집합이 ``SampleTestPlan`` 범위와 같다.

        두 값이 독립 경로라 서로를 검산해 준다."""
        import re
        info = next((_KLA_REAL / name).glob("*.001"))
        text = info.read_bytes().decode("utf-8", "replace").replace("\x00", "")
        g = wg.parse_kla_header(text)
        assert g is not None
        plan = re.search(r"SampleTestPlan\s+\d+(.*?);", text, re.I | re.S)
        pairs = [(int(a), int(b))
                 for a, b in re.findall(r"(-?\d+)\s+(-?\d+)", plan.group(1))]
        assert -min(x for x, _ in pairs) == g.zero_x
        assert -min(y for _, y in pairs) == g.zero_y


# ---------------------------------------------------------------------------
# TB500 실측 회귀 — 값이 예전과 같아야 한다
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _REAL.exists(), reason="dev/좌표 확인 실측 폴더 없음")
class TestRealSamples:
    def test_kla_geometry_from_real_file(self):
        """실측 .001 → pitch 는 DiePitch, 원점은 SampleCenterLocation 기하에서.

        ``zero_y`` 는 **3** 이다.  옛 상수 ``KLA_ZERO_Y=4`` 는 TB500 자재에서 row 를
        1 크게 냈다 — 사용자가 장비로 확인한 값이 3 임을 증명한다(§6-K)."""
        g = wg.kla_geometry(_REAL / "KLA" / "예시1")
        assert (g.zero_x, g.zero_y) == (3, 3)
        assert g.zero_x == KLA_ZERO_X            # X 는 옛 상수와 같다
        assert g.zero_y != KLA_ZERO_Y            # Y 는 상수(4)가 아니다
        assert g.source == "DiePitch+SampleCenterLocation"
        assert g.pitch_x == pytest.approx(37247.93)
        assert g.pitch_y == pytest.approx(44905.34)

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

    def test_real_coord_matches_equipment(self):
        """실측 좌표 — die-내부 x/y 는 불변, KLA col/row 는 장비 확인 규약 (5,3).

        ⚠ Camtek 쪽이 ``(5,4)`` 로 1 큰 것은 **이 픽스처의 한계**다 — 이 폴더에는
        ``Params_WaferInfo.ini`` 가 없어(사진만 복사됨) ``Center_Y`` 를 못 읽고 옛 식으로
        폴백한다.  실물 스캔 결과 폴더에는 그 파일이 사진과 같은 자리에 늘 있다.
        자세한 건 :func:`test_coords.test_real_pair_kla_camtek_rows_align`."""
        c = camtek_ini.load_folder(
            _REAL / "Camtek" / "예시1")["272646.165679.c.1000203959.2"]
        assert (c.col, c.row) == (5, 4)              # 폴백값 — 아래 KLA 가 정답 규약
        assert (c.x, c.y) == (11913.0, 30959.0)      # floor — 장비 표기는 버림

        k = kla_info.load_folder(_REAL / "KLA" / "예시1")["w6459076xyg1_2_0_23_2"]
        assert (k.col, k.row) == (5, 3)
        assert (k.x, k.y) == (11819.0, 31035.0)
