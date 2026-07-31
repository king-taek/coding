"""좌표 파서 단위 테스트.

보고서 예시 5개(Camtek INI) + LIVE 파일명 1개로 파서를 검증한다.
무거운 의존성 없이 순수 로직만 테스트하므로 importorskip 불필요.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aoi_verification.app.coords.camtek_ini import _extract_coord as _extract_raw_coord
from aoi_verification.app.coords.camtek_live import resolve as live_resolve
from aoi_verification.app.coords.wafer_geometry import FALLBACK_CAMTEK


def _extract_coord(content):
    """이 파일의 기대값은 전부 **TB500 격자** 기준 — 기하를 명시해 고정한다.

    (die 기하는 이제 폴더에서 읽는다. 다른 디바이스는 test_wafer_geometry.py 가 본다.)"""
    return _extract_raw_coord(content, FALLBACK_CAMTEK)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _make_ini_section(**kv) -> str:
    return "\n".join(f"{k}={v}" for k, v in kv.items())


def _approx(a: float, b: float, tol: float = 1e-3) -> bool:
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------
# Camtek INI — 보고서 5개 예시 검증
# ---------------------------------------------------------------------------
class TestCamtekIni:
    def test_example1(self):
        """보고서 예시 1: X=253716.003307344 Y=91798.7938704543 Col=6 Row=2 → col=4 row=5"""
        content = _make_ini_section(X="253716.003307344", Y="91798.7938704543",
                                     Col="6", Row="2")
        c = _extract_coord(content)
        assert c is not None
        assert c.col == 4
        assert c.row == 5
        assert c.x == 30229.0    # floor — 장비 표기는 버림
        assert c.y == 1987.0
        assert c.source == "camtek_ini"

    def test_example2(self):
        """보고서 예시 2: Col=7 Row=5 → col=5 row=2"""
        content = _make_ini_section(X="285837.569021826", Y="241931.965714178",
                                     Col="7", Row="5")
        c = _extract_coord(content)
        assert c is not None
        assert c.col == 5
        assert c.row == 2
        assert c.x == 25103.0
        assert c.y == 17404.0

    def test_example3(self):
        """경계 예시: Col=4 Row=6 → col=2 row=1

        Row=6 은 실측 원본 Row 범위(1..6)의 최하단.  맵 세로 눈금 0..6 중 row 0(=Row 7)은
        이 자재가 쓰지 않으므로 최하단 결함이 row 1 로 나오는 게 정상이다."""
        content = _make_ini_section(X="183424.006310378", Y="337589.125854985",
                                     Col="4", Row="6")
        c = _extract_coord(content)
        assert c is not None
        assert c.col == 2
        assert c.row == 1
        assert c.x == 34433.0
        assert c.y == float(int(337589.125854985 - 6 * 44905.4))   # floor

    def test_example4(self):
        """보고서 예시 4: Col=4 Row=3 → col=2 row=4"""
        content = _make_ini_section(X="182587.539096461", Y="149593.522482771",
                                     Col="4", Row="3")
        c = _extract_coord(content)
        assert c is not None
        assert c.col == 2
        assert c.row == 4
        assert c.x == 33596.0
        assert c.y == 14877.0

    def test_example5(self):
        """보고서 예시 5: Col=4 Row=2 → col=2 row=5"""
        content = _make_ini_section(X="180377.576920526", Y="100976.81821231",
                                     Col="4", Row="2")
        c = _extract_coord(content)
        assert c is not None
        assert c.col == 2
        assert c.row == 5
        assert c.x == 31386.0
        assert c.y == 11166.0

    def test_faultx_fallback(self):
        """X/Y 없으면 FaultX/FaultY 사용."""
        content = _make_ini_section(FaultX="253716.003307344", FaultY="91798.7938704543",
                                     Col="6", Row="2")
        c = _extract_coord(content)
        assert c is not None
        assert c.col == 4
        assert c.row == 5

    def test_equipment_screen_reading(self):
        """★ 정답 기준 — 장비(AOI Data Viewer) 웨이퍼 맵 **직접 판독** 실측.

        INI ``Col=8, Row=5`` → 장비 화면 ``col=6, row=2``.
        맵 규약: 왼쪽 맨 아래가 (0,0), 오른쪽·위로 1씩 증가(col·row 둘 다 0-based).

        이 한 건이 ``row = 7 − Row`` 를 확정한다 — ``row = Row − 3`` 은 실측 Row 범위
        1..6 에서 음수가 나와 0-based 맵에 존재할 수 없다.
        (이전 세션이 ``CAMTEK_ROW_TOTAL`` 을 7→6 으로 바꿔 전 구간 row 가 1 작아졌던
        회귀의 재발 방지용이다.  이 값을 건드리려면 장비 화면을 다시 판독할 것.)"""
        content = _make_ini_section(X="334789.573892829", Y="267755.638995209",
                                     Col="8", Row="5")
        c = _extract_coord(content)
        assert c is not None
        assert (c.col, c.row) == (6, 2)

    def test_missing_keys_returns_none(self):
        """필수 키 누락 시 None 반환."""
        content = _make_ini_section(X="100.0", Y="200.0")   # Col/Row 없음
        assert _extract_coord(content) is None

    def test_empty_section_returns_none(self):
        assert _extract_coord("") is None


# ---------------------------------------------------------------------------
# Camtek LIVE 파일명 파서
# ---------------------------------------------------------------------------
class TestCamtekLive:
    def test_standard_filename(self):
        """보고서 예시 1과 대응하는 LIVE 파일명 — row 토큰 5(표시 규약)는 −1 정규화돼 4."""
        p = Path(
            "R_TB500_LIVE_PI4_VLP-PDIS3_W6317098XYB5_4_5_Over Sized Bump"
            "_30229.803_1987.994.jpg"
        )
        c = live_resolve(p)
        assert c is not None
        assert c.col == 4
        assert c.row == 4
        assert _approx(c.x, 30229.803)
        assert _approx(c.y, 1987.994)
        assert c.source == "camtek_live"

    def test_integer_coords(self):
        """x/y 가 정수만 있어도 파싱 가능."""
        p = Path("R_DEV_W123_3_2_Defect_12345_6789.jpg")
        c = live_resolve(p)
        assert c is not None
        assert c.col == 3
        assert c.row == 1
        assert c.x == 12345.0
        assert c.y == 6789.0

    def test_non_live_filename_returns_none(self):
        """R_ 로 시작하지 않으면 None."""
        p = Path("253715.91797.c.-1104740629.1.jpeg")
        assert live_resolve(p) is None

    def test_defect_name_with_spaces(self):
        """DefectName 에 공백이 있어도 파싱 가능 — _PAT 은 공백 포함 허용."""
        p = Path("R_TB500_W1_2_3_Over Sized Bump_100.5_200.0.jpg")
        c = live_resolve(p)
        assert c is not None
        assert c.col == 2
        assert c.row == 2

    @pytest.mark.parametrize("name", [
        "W6459076XYG1_2_0_23_2.jpg",
        "W6459081XYE1_1_2_14_2.jpg",
        "W6460162XYF4_2_1_7_1.jpg",
    ])
    def test_kla_filename_rejected(self, name):
        """KLA 이미지 파일명은 LIVE 로 파싱되지 않아야 한다."""
        assert live_resolve(Path(name)) is None


# ---------------------------------------------------------------------------
# 통합: coords.__init__.resolve 우선순위
# ---------------------------------------------------------------------------
class TestCoordResolve:
    def test_live_priority_over_ini(self, tmp_path):
        """LIVE 파일명이 있으면 INI 를 무시."""
        from aoi_verification.app.coords import resolve

        # INI 파일 생성 (다른 좌표)
        ini = tmp_path / "ColorImageGrabingInfo.ini"
        ini.write_text(
            "[R_TB500_W1_4_5_Bump_30229.803_1987.994.jpeg]\n"
            "X=999999.0\nY=999999.0\nCol=6\nRow=2\n",
            encoding="utf-8",
        )
        # LIVE 형식 파일명 — 파일이 실제로 존재할 필요 없음(resolve 는 경로만 파싱)
        img = tmp_path / "R_TB500_W1_4_5_Bump_30229.803_1987.994.jpg"
        c = resolve(img)
        # LIVE 파서가 먼저이므로 LIVE 좌표 반환
        assert c is not None
        assert c.source == "camtek_live"
        assert c.col == 4
        assert c.row == 4

    def test_kla_filename_falls_through_to_kla_resolver(self, tmp_path):
        """KLA 파일명은 camtek_live 를 건너뛰고 kla_info 로 해석돼야 한다."""
        from aoi_verification.app.coords import resolve
        from aoi_verification.app.coords import kla_info

        kla_info.load_folder.cache_clear()
        kla_info.load_folder_raw.cache_clear()
        info = tmp_path / "INFO.001"
        info.write_text(
            'DiePitch 3.7247930000e+004 4.4905340000e+004;\n'
            'TiffFileName W6459076XYG1_2_0_23_2.jpg;\n'
            'DefectList\n'
            ' 2 67855.280 14093.720 11819.421 13870.779 2 0 '
            '2.600 3.900 10.16 3.9 23 3 0 0 1 1 0 2671 0 1 1 1 0;\n',
            encoding="utf-8",
        )
        img = tmp_path / "W6459076XYG1_2_0_23_2.jpg"
        c = resolve(img)
        assert c is not None
        assert c.source == "kla"
        assert c.col == 5
        assert c.row == 4      # YINDEX 0 + KLA_ZERO_Y(4) — 장비 화면 기준


# ---------------------------------------------------------------------------
# 회귀: 실측 데이터에서 KLA ↔ Camtek 의 같은 결함이 같은 (col,row) 로 정렬
# ---------------------------------------------------------------------------
_COORD_DATA = Path(__file__).resolve().parents[2] / "dev" / "좌표 확인"


@pytest.mark.skipif(not _COORD_DATA.exists(), reason="실측 데이터 폴더 없음")
def test_real_pair_kla_camtek_rows_align():
    """dev/좌표 확인 실측 쌍: Camtek 예시1(Col=7,Row=3) ↔ KLA 예시1(XINDEX=2,YINDEX=0).

    두 좌표가 121 µm 밖에 안 떨어져 있어 **같은 물리적 결함**임이 확인된 쌍이다.
    따라서 두 변환이 같은 (col,row) 를 내야 한다 — 이 테스트의 요점은 **정렬**이다.

    값은 장비 화면 기준 (5,4):  Camtek 7−3 = 4,  KLA 0 + KLA_ZERO_Y(4) = 4.
    (Camtek 만 −1 하고 KLA 를 그대로 두면 정렬은 유지되지만 둘 다 장비 화면과 어긋난다 —
    실제로 그렇게 어긋나 있었다.  ``TestCamtekIni.test_equipment_screen_reading`` 참조.)"""
    from aoi_verification.app.coords import camtek_ini, kla_info

    camtek_ini.load_folder.cache_clear()
    kla_info.load_folder.cache_clear()
    kla_info.load_folder_raw.cache_clear()

    cam = camtek_ini.resolve(
        _COORD_DATA / "Camtek" / "예시1" / "272646.165679.c.1000203959.2.jpeg")
    kla = kla_info.resolve(
        _COORD_DATA / "KLA" / "예시1" / "W6459076XYG1_2_0_23_2.jpg")
    assert cam is not None and kla is not None
    assert (cam.col, cam.row) == (kla.col, kla.row)      # 정렬 — 이게 핵심
    assert (cam.col, cam.row) == (5, 4)                  # 장비 화면 기준 절대값
