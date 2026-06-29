#!/usr/bin/env python3
"""KLA ↔ Camtek 좌표 변환 어긋남 진단 — 같은 웨이퍼의 두 폴더를 비교.

KLA defect 사진과 Camtek defect 사진의 좌표 매칭이 안 될 때, 두 장비의 변환
(DefectCoord: col/row/x/y)이 같은 물리적 결함을 같은 값으로 만드는지 **실측으로**
확인하기 위한 도구다.  추측으로 변환식을 고치면 오매칭(정확도 파손) 위험이 있으므로,
먼저 이 도구로 systematic offset/flip 을 드러낸 뒤 데이터에 맞게 교정한다.

사용:
    python dev/diagnose_kla_camtek_coords.py <KLA폴더> <Camtek폴더>

무거운 의존성(PyQt6/cv2/openvino) 없이 stdlib + coords 모듈만 쓴다.
순수 헬퍼(_relation/diagnose)는 dev/tests/test_diagnose_kla_camtek.py 로 헤드리스 검증.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

# 저장소 루트를 import 경로에 추가(dev/ 에서 직접 실행 대비).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aoi_verification.app.coords.models import DefectCoord  # noqa: E402


# ── 순수 헬퍼 (헤드리스 테스트 대상) ─────────────────────────────────────────
def _spread(vals: Sequence[float]) -> float:
    """최대−최소(범위).  비었으면 0."""
    return (max(vals) - min(vals)) if vals else 0.0


def _median(vals: Sequence[float]) -> float:
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _relation(kla_vals: Sequence[float], cam_vals: Sequence[float]) -> dict:
    """KLA 값과 Camtek 값의 일관된 관계를 추정.

    - 차(kla−cam)의 범위가 더 작으면 **offset**: cam ≈ kla − value.
    - 합(kla+cam)의 범위가 더 작으면 **flip**:   cam ≈ value − kla (축 반전).
    spread(범위)가 작을수록 그 관계가 일관적(=systematic)이라는 뜻."""
    diffs = [k - c for k, c in zip(kla_vals, cam_vals)]
    sums = [k + c for k, c in zip(kla_vals, cam_vals)]
    sd, ss = _spread(diffs), _spread(sums)
    if sd <= ss:
        return {"kind": "offset", "value": _median(diffs), "spread": sd}
    return {"kind": "flip", "value": _median(sums), "spread": ss}


def _nearest(k: DefectCoord, cam: Sequence[DefectCoord]) -> DefectCoord:
    """die 내부 (x,y) 유클리드 거리로 가장 가까운 Camtek 결함."""
    return min(cam, key=lambda c: math.hypot(k.x - c.x, k.y - c.y))


def diagnose(kla: Sequence[DefectCoord],
             cam: Sequence[DefectCoord]) -> dict:
    """KLA·Camtek DefectCoord 목록을 받아 변환 어긋남 요약을 만든다.

    각 KLA 결함을 (x,y) 최근접 Camtek 결함과 짝지어 col/row/x/y 관계(offset/flip)와
    (col,row) 게이트 일치율을 계산한다.  반환은 dict(필드별 relation + gate_match_rate
    + pairs).  실제 변환 교정은 이 요약을 보고 한다."""
    if not kla or not cam:
        return {"pairs": [], "gate_match_rate": 0.0,
                "col": None, "row": None, "x": None, "y": None}

    pairs: List[Tuple[DefectCoord, DefectCoord]] = [
        (k, _nearest(k, cam)) for k in kla
    ]
    kc = [k.col for k, _ in pairs]; cc = [c.col for _, c in pairs]
    kr = [k.row for k, _ in pairs]; cr = [c.row for _, c in pairs]
    kx = [k.x for k, _ in pairs]; cx = [c.x for _, c in pairs]
    ky = [k.y for k, _ in pairs]; cy = [c.y for _, c in pairs]
    gate = sum(1 for k, c in pairs if k.col == c.col and k.row == c.row)
    return {
        "pairs": pairs,
        "gate_match_rate": gate / len(pairs),
        "col": _relation(kc, cc),
        "row": _relation(kr, cr),
        "x": _relation(kx, cx),
        "y": _relation(ky, cy),
    }


# ── CLI ─────────────────────────────────────────────────────────────────────
def _load(folder: Path) -> List[DefectCoord]:
    """폴더의 모든 이미지 결함 좌표를 DefectCoord 목록으로(소스 자동 판별)."""
    from aoi_verification.app.coords import camtek_ini, camtek_live, kla_info
    out: List[DefectCoord] = []
    # 폴더 단위 파서(캐시)로 한 번에 — stem 별 좌표를 모은다.
    for d in (camtek_live, camtek_ini, kla_info):
        try:
            m = d.load_folder(folder)
        except Exception:
            m = {}
        if m:
            out.extend(m.values())
            break
    return out


def _fmt_rel(name: str, rel: dict) -> str:
    if rel is None:
        return f"  {name}: (데이터 없음)"
    return (f"  {name}: {rel['kind']}  value={rel['value']:.3g}  "
            f"spread={rel['spread']:.3g}"
            + ("   ← 일관적" if rel["spread"] < 1e-6 or
               (name in ('col', 'row') and rel['spread'] <= 0) else ""))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="KLA↔Camtek 좌표 변환 어긋남 진단")
    ap.add_argument("kla_folder", type=Path, help="KLA 결함 사진 폴더(.001 포함)")
    ap.add_argument("cam_folder", type=Path, help="Camtek 결함 사진 폴더(INI/LIVE)")
    args = ap.parse_args(argv)

    kla = _load(args.kla_folder)
    cam = _load(args.cam_folder)
    print(f"KLA 결함 {len(kla)}개, Camtek 결함 {len(cam)}개")
    if not kla or not cam:
        print("한쪽 폴더에서 좌표를 읽지 못했습니다 — 경로/파일 형식 확인.")
        return 1

    rep = diagnose(kla, cam)
    print(f"(col,row) 게이트 일치율: {rep['gate_match_rate']*100:.0f}%  "
          f"(1.0 이어야 같은 die 로 인정)")
    print("관계 추정 (cam = kla−value[offset] 또는 value−kla[flip]):")
    for f in ("col", "row", "x", "y"):
        print(_fmt_rel(f, rep[f]))
    print("\n해석 가이드:")
    print("- col/row 가 offset 이고 spread≈0 이면 그 정수만큼 더하면 게이트가 맞음.")
    print("- col/row 가 flip 이면 한쪽 인덱스 축이 반대 — 변환에서 뒤집기 필요/제거.")
    print("- x/y 가 flip 이면 die 내부 축 방향 반대, offset 이면 원점(코너↔중심) 차이.")
    print("- 이 결과로 kla_info.py 변환식/ models.py 상수를 데이터에 맞게 교정한다.")
    # 처음 몇 쌍 표로.
    print("\n예시 쌍 (KLA → 최근접 Camtek):")
    for k, c in rep["pairs"][:12]:
        print(f"  KLA(col{k.col},row{k.row},x{k.x:.0f},y{k.y:.0f}) ↔ "
              f"CAM(col{c.col},row{c.row},x{c.x:.0f},y{c.y:.0f})  "
              f"Δcol{k.col-c.col} Δrow{k.row-c.row} "
              f"Δx{k.x-c.x:.0f} Δy{k.y-c.y:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
