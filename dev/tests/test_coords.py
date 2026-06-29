"""좌표 파서 단위 테스트.

보고서 예시 5개(Camtek INI) + LIVE 파일명 1개로 파서를 검증한다.
무거운 의존성 없이 순수 로직만 테스트하므로 importorskip 불필요.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aoi_verification.app.coords.camtek_ini import _extract_coord
from aoi_verification.app.coords.camtek_live import resolve as live_resolve
from aoi_verification.app.coords.models import DefectCoord


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
        assert _approx(c.x, 30229.803307344)
        assert _approx(c.y, 1987.9938704543)
        assert c.source == "camtek_ini"

    def test_example2(self):
        """보고서 예시 2: Col=7 Row=5 → col=5 row=2"""
        content = _make_ini_section(X="285837.569021826", Y="241931.965714178",
                                     Col="7", Row="5")
        c = _extract_coord(content)
        assert c is not None
        assert c.col == 5
        assert c.row == 2
        assert _approx(c.x, 25103.669021826)
        assert _approx(c.y, 17404.965714178)

    def test_example3(self):
        """보고서 예시 3: Col=4 Row=7 → col=2 row=0"""
        content = _make_ini_section(X="183424.006310378", Y="337589.125854985",
                                     Col="4", Row="7")
        c = _extract_coord(content)
        assert c is not None
        assert c.col == 2
        assert c.row == 0
        assert _approx(c.x, 34433.206310378)
        assert _approx(c.y, 23251.325854985)

    def test_example4(self):
        """보고서 예시 4: Col=4 Row=3 → col=2 row=4"""
        content = _make_ini_section(X="182587.539096461", Y="149593.522482771",
                                     Col="4", Row="3")
        c = _extract_coord(content)
        assert c is not None
        assert c.col == 2
        assert c.row == 4
        assert _approx(c.x, 33596.739096461)
        assert _approx(c.y, 14877.322482771)

    def test_example5(self):
        """보고서 예시 5: Col=4 Row=2 → col=2 row=5"""
        content = _make_ini_section(X="180377.576920526", Y="100976.81821231",
                                     Col="4", Row="2")
        c = _extract_coord(content)
        assert c is not None
        assert c.col == 2
        assert c.row == 5
        assert _approx(c.x, 31386.776920526)
        assert _approx(c.y, 11166.01821231)

    def test_faultx_fallback(self):
        """X/Y 없으면 FaultX/FaultY 사용."""
        content = _make_ini_section(FaultX="253716.003307344", FaultY="91798.7938704543",
                                     Col="6", Row="2")
        c = _extract_coord(content)
        assert c is not None
        assert c.col == 4
        assert c.row == 5

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
        """보고서 예시 1과 대응하는 LIVE 파일명."""
        p = Path(
            "R_TB500_LIVE_PI4_VLP-PDIS3_W6317098XYB5_4_5_Over Sized Bump"
            "_30229.803_1987.994.jpg"
        )
        c = live_resolve(p)
        assert c is not None
        assert c.col == 4
        assert c.row == 5
        assert _approx(c.x, 30229.803)
        assert _approx(c.y, 1987.994)
        assert c.source == "camtek_live"

    def test_integer_coords(self):
        """x/y 가 정수만 있어도 파싱 가능."""
        p = Path("R_DEV_W123_3_2_Defect_12345_6789.jpg")
        c = live_resolve(p)
        assert c is not None
        assert c.col == 3
        assert c.row == 2
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
        assert c.row == 3

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
        assert c.row == 5

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
        assert c.row == 3
