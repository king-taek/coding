"""KLA↔Camtek 좌표 진단 도구의 순수 헬퍼 검증(무거운 의존성 없이).

_relation 이 offset/flip 을 구분하고, diagnose 가 합성 데이터에서 systematic
패턴과 (col,row) 게이트 일치율을 바르게 잡아내는지 본다.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

# dev/diagnose_kla_camtek_coords.py 를 모듈로 직접 로드(패키지 아님).
_SPEC_PATH = Path(__file__).resolve().parents[1] / "diagnose_kla_camtek_coords.py"
_spec = importlib.util.spec_from_file_location("diag_kla_camtek", _SPEC_PATH)
diag = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diag)

from aoi_verification.app.coords.models import DefectCoord


def _kla(col, row, x, y):
    return DefectCoord(col=col, row=row, x=float(x), y=float(y), source="kla")


def _cam(col, row, x, y):
    return DefectCoord(col=col, row=row, x=float(x), y=float(y),
                       source="camtek_ini")


def test_relation_detects_offset():
    # cam = kla - 2 (일관된 오프셋)
    rel = diag._relation([3, 4, 5], [1, 2, 3])
    assert rel["kind"] == "offset"
    assert rel["value"] == 2
    assert rel["spread"] == 0


def test_relation_detects_flip():
    # cam = 7 - kla (합이 7로 일관) → flip
    rel = diag._relation([2, 3, 5], [5, 4, 2])
    assert rel["kind"] == "flip"
    assert rel["value"] == 7
    assert rel["spread"] == 0


def test_diagnose_flags_row_flip_and_gate_miss():
    # 같은 die 인데 Camtek row 가 뒤집혀(7-row) 있어 (col,row) 게이트가 어긋나는 상황.
    # x 를 서로 다르게 둬 최근접 짝이 1:1 로 잡히게 한다.
    kla = [_kla(4, 5, 100, 10), _kla(4, 3, 200, 20), _kla(5, 2, 300, 30)]
    cam = [_cam(4, 2, 100, 10), _cam(4, 4, 200, 20), _cam(5, 5, 300, 30)]
    rep = diagnose = diag.diagnose(kla, cam)
    assert rep["row"]["kind"] == "flip"      # row = 7 - row
    assert rep["row"]["value"] == 7
    assert rep["col"]["kind"] == "offset" and rep["col"]["value"] == 0
    # row 가 어긋나 게이트는 전부 불일치.
    assert rep["gate_match_rate"] == 0.0


def test_diagnose_perfect_alignment_gate_full():
    kla = [_kla(4, 5, 100, 10), _kla(2, 1, 200, 20)]
    cam = [_cam(4, 5, 101, 11), _cam(2, 1, 199, 19)]
    rep = diag.diagnose(kla, cam)
    assert rep["gate_match_rate"] == 1.0


def test_diagnose_empty_inputs():
    rep = diag.diagnose([], [_cam(1, 1, 0, 0)])
    assert rep["gate_match_rate"] == 0.0
    assert rep["pairs"] == []
