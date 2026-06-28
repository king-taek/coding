"""analyze_kla_001 — 합성 KLARF(.001)로 구조 분석 단일 md 생성 검증(stdlib)."""

import importlib.util
from pathlib import Path

_MOD = Path(__file__).resolve().parents[1] / "analyze_kla_001.py"
_spec = importlib.util.spec_from_file_location("analyze_kla_001", _MOD)
akla = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(akla)

_SAMPLE = """FileVersion 1 2;
FileTimestamp 09-12-25 18:31:00;
LotID "RDL4_TTTM_STR";
WaferID "00LH3106XYB3";
StepID "RDL";
DiePitch 1.23456e+004 4.56789e+004;
DieOrigin 0 0;
SampleCenterLocation 0 0;
DefectRecordSpec 12 DEFECTID XREL YREL XINDEX YINDEX XSIZE YSIZE DEFECTAREA DSIZE CLASSNUMBER ROUGHBINNUMBER TEST ;
DefectList
 1 1234 5678 0 0 12 13 150 14 0 0 0
 2 2345 6789 1 -1 20 22 400 25 3 1 0;
SummarySpec 4 TESTNO NDEFECT DEFDENSITY NDIE ;
SummaryList
 0 2 1.5 4 ;
TiffFileName 0001.tif;
EndOfFile;
"""


def test_klarf_structure_md(tmp_path):
    f = tmp_path / "RDL_RDL4_TTTM_STR.001"
    f.write_text(_SAMPLE, encoding="utf-8")
    out = tmp_path / "KLA분석.md"
    akla.main([str(f), "--out", str(out)])
    text = out.read_text(encoding="utf-8")

    # 헤더/스펙/리스트가 모두 인식된다.
    assert "RDL4_TTTM_STR" in text and "00LH3106XYB3" in text
    assert "DefectRecordSpec" in text and "12개 컬럼" in text
    # geometry 후보(XSIZE/YSIZE/DEFECTAREA/DSIZE)가 강조된다.
    for col in ("XSIZE", "YSIZE", "DEFECTAREA", "DSIZE"):
        assert col in text
    # DefectList 2개 레코드가 표로 나온다.
    assert "레코드 2개" in text
    # geometry 값 범위(DEFECTAREA 150~400) 요약.
    assert "150" in text and "400" in text
    # SummaryList 도 나온다.
    assert "SummaryList" in text


def test_specs_and_list_parsing(tmp_path):
    f = tmp_path / "x.001"
    f.write_text(_SAMPLE, encoding="utf-8")
    txt = f.read_text(encoding="utf-8")
    specs = akla.parse_specs(txt)
    assert specs["DefectRecordSpec"][0] == "DEFECTID"
    assert "DEFECTAREA" in specs["DefectRecordSpec"]
    recs = akla.parse_list(txt, "DefectList", len(specs["DefectRecordSpec"]))
    assert len(recs) == 2
    assert recs[0][0] == "1" and recs[1][9] == "3"   # CLASSNUMBER 두번째=3
    # 헤더 단일값 추출.
    assert akla.header_value(txt, "WaferID") == '"00LH3106XYB3"'


def test_missing_file_reports_gracefully(tmp_path):
    out = tmp_path / "o.md"
    akla.main([str(tmp_path / "nope.001"), "--out", str(out)])
    text = out.read_text(encoding="utf-8")
    assert "읽기 실패" in text   # raise 없이 안내 문구
