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
# ★ die pitch 를 몰라도 매칭은 된다 — 절대 wafer 좌표 폴백
#
# ColorImageGrabingInfo.ini 의 X/Y 만으로 매칭이 성립한다.  die 로 쪼개는 건
# (a) 화면·엑셀의 die-내부 좌표 표기와 (b) KLA 교차 매칭에만 필요하다.
# ---------------------------------------------------------------------------
class TestAbsoluteFallback:
    def test_matching_works_without_die_pitch(self, tmp_path):
        """같은 결함은 매칭되고, 옆 die 의 같은 상대위치 결함은 기각된다."""
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
# TB500 실측 회귀 — 값이 예전과 같아야 한다
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _REAL.exists(), reason="dev/좌표 확인 실측 폴더 없음")
class TestRealSamples:
    def test_kla_geometry_from_real_file(self):
        """실측 .001 → pitch 는 DiePitch, zero_x 는 SampleTestPlan 에서."""
        g = wg.kla_geometry(_REAL / "KLA" / "예시1")
        assert (g.zero_x, g.zero_y) == (KLA_ZERO_X, KLA_ZERO_Y)
        assert g.source == "DiePitch+SampleTestPlan"
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
        """실측 좌표 — die-내부 x/y 는 불변, col/row 는 장비 화면 기준 (5,4)."""
        c = camtek_ini.load_folder(
            _REAL / "Camtek" / "예시1")["272646.165679.c.1000203959.2"]
        assert (c.col, c.row) == (5, 4)
        assert (c.x, c.y) == (11913.0, 30959.0)      # floor — 장비 표기는 버림

        k = kla_info.load_folder(_REAL / "KLA" / "예시1")["w6459076xyg1_2_0_23_2"]
        assert (k.col, k.row) == (5, 4)
        assert (k.x, k.y) == (11819.0, 31035.0)
