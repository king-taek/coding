"""여러 디바이스의 map·die·결함 좌표 원재료를 zip 하나로 모은다(읽기 전용, 더블클릭).

2026-10-25 현택님이 준 경로(TB500 2건 · T254 2건 · TI 2건 · QORVO 2건).  각 쌍은
Conder scan 폴더(LIVE 파일명 = 장비 정답) ↔ Camtek Scanresult 폴더다.  모으는 방식은
``collect_avago.collect`` 를 그대로 쓴다: 사진은 이름만, zip 은 뺀다.

한 zip 에 담으니 500 MB 가 돼 올릴 수 없었다(현택님).  파일 목록을 보니 대부분이 앱이
읽지 않는 Camtek 정렬·프레임 데이터(``DieAlignment.dat`` · ``s_FrameData.dat`` …)였다 →
그 이름들을 빼고(:data:`SKIP_NAMES`), 작은 die 제품에서 10 MB 씩인 ``s_DieLocation.dat``
은 폴더(root)마다 :data:`MAP_LIMIT` 장만 담는다 — 나머지 웨이퍼의 맵 요약은 앱기하.txt 에
이미 계산돼 들어간다.  zip 은 디바이스마다 따로 만들고 확장자별 용량(``용량.txt``)을 붙인다.
"""

from __future__ import annotations

import os
import sys
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_avago  # noqa: E402

# 앱이 읽지 않는 Camtek 대용량 부산물(2026-10-25 첫 수집의 압축 후 용량 상위).
SKIP_NAMES = {n.casefold() for n in (
    "DieAlignment.dat", "s_FrameData.dat", "s_DieAlignPos.dat", "Zones.dat",
    "WaferInfo.dat", "WaferInfo.org", "DieAlignMiss.flt", "s_Plane.dat",
    "s_Coplanarity.dat", "ChuckMappingForScanCamera_dX.grd",
    "ChuckMappingForScanCamera_dY.grd")}
MAP_LIMIT = 3
_maps: Counter = Counter()
_orig_add_dir = collect_avago._add_dir


class _Dir:
    """``iterdir`` 에서 :data:`SKIP_NAMES`·초과 die 맵을 걸러 내는 ``Path`` 대리자."""

    def __init__(self, d: Path, tag: str, log: list[str]):
        self._d, self._tag, self._log = d, tag, log

    def __getattr__(self, name):
        return getattr(self._d, name)

    def __str__(self):
        return str(self._d)

    def iterdir(self):
        for p in self._d.iterdir():
            n = p.name.casefold()
            if n in SKIP_NAMES:
                continue
            if n == "s_dielocation.dat":
                if _maps[self._tag] >= MAP_LIMIT:
                    self._log.append(f"  - die 맵 {MAP_LIMIT}장 초과라 뺌: {p}")
                    continue
                _maps[self._tag] += 1
            yield p


def _add_dir(zf, d, arc, log):
    return _orig_add_dir(zf, _Dir(d, arc.split("/")[0], log), arc, log)


collect_avago._add_dir = _add_dir

_CONDER = r"\\k5cifsn2\k5tsvdata$\1. Conder Scan"

DEVICES = [
    ("TB500 SGG — REVERSE ETCH", [
        _CONDER + r"\531. NVIDIA INT\143. ASTB500-SM822-8MAZ\1400. E4N789.S1V DCC20 (SGG)\1. REVERSE ETCH",
        r"V:\AOI-16\Scanresult\R_TB500 LIVE_0860154PD_R-ETCH\SETUP\SGG 1018S",
    ]),
    ("TB500 KUH — BS CUP", [
        _CONDER + r"\531. NVIDIA INT\134. ASTB500-SP822-8MAY\1347. E3P676.C0W DCC20 (KUH)\2. BS CUP",
        r"X:\AOI-3\Scanresult\R_ TB500_0860154PD_BS UBM\SETUP\KUH-1018N",
    ]),
    ("T254 TSC — RDL1 (KLA)", [
        _CONDER + r"\531. NVIDIA INT\113. 4DT-T254-H-M1\414. T254.INT406 (TSC)\4. RDL1_재리뷰",
        r"T:\2026-10-17\T254.INT406@6323",
    ]),
    ("T254 TSC — FS UBM", [
        _CONDER + r"\531. NVIDIA INT\113. 4DT-T254-H-M1\414. T254.INT406 (TSC)\5. FS UBM",
        r"X:\AOI-3\Scanresult\R_T254 LIVE_0858711PD_FS UBM\6324\TSC-FS",
    ]),
    ("TI CCG", [
        _CONDER + r"\672. TI DALLAS\78. 3L781987B17-WUP\106. 6501169AK5 (CCG)\1. FVI\1. OR",
        r"V:\AOI-12\Scanresult\2D@R2-3L781987B17-WUP_0858675PD-0B\6412\CCG",
    ]),
    ("TI CCB", [
        _CONDER + r"\672. TI DALLAS\78. 3L781987B17-WUP\100. 6501165AK5 (CCB)\1. FVI",
        r"X:\AOI-7\Scanresult\2D@R2-3L781987B17-WUP_0858675PD-0B\6412\CCB",
    ]),
    ("QORVO CTX — FVI (Camtek 레시피 3개)", [
        _CONDER + r"\575. QORVO US\44. W97218SGZ4BK16\75. 6H7ENK (CTX)\2. FVI",
        r"M:\AOI-9\Scanresult\2D@R2-W97218SGB1K16_0860463PD-0A\6321\CTK SRD",
        r"M:\AOI-9\Scanresult\2D@R2-W97218SGB1K16_0860463PD-0A\6321\CTK",
        r"M:\AOI-9\Scanresult\2D@R2-W97218SGB1K16_0860463PD-0A\6321\CTK EDGE",
    ]),
    ("QORVO CTX — UBM", [
        _CONDER + r"\575. QORVO US\44. W97218SGZ4BK16\75. 6H7ENK (CTX)\1. UBM",
        r"V:\AOI-12\Scanresult\2D@RE-W97218SGB1K16_0860463PD-0A\6412\CTK",
    ]),
]


def _size_report(dst: Path) -> None:
    """zip 안 확장자별 용량(압축 전)을 ``용량.txt`` 로 덧붙인다."""
    size: Counter = Counter()
    with zipfile.ZipFile(dst) as zf:
        for i in zf.infolist():
            size[Path(i.filename).suffix.lower() or "(없음)"] += i.file_size
    with zipfile.ZipFile(dst, "a", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("용량.txt", "\n".join(f"{e}\t{b / 1e6:.1f} MB" for e, b in size.most_common()) + "\n")


def main() -> int:
    out = Path.home() / "Desktop" / "좌표대조_디바이스별"
    out.mkdir(parents=True, exist_ok=True)
    for k, (label, folders) in enumerate(DEVICES, 1):
        print(f"[{k}/{len(DEVICES)}] {label} 모으는 중...")
        dst = collect_avago.collect([Path(f) for f in folders], out)
        _size_report(dst)
        print(f"   {dst.name}  {dst.stat().st_size / 1e6:.1f} MB")
    print(f"\n만들었습니다: {out}\nzip 을 하나씩 대화에 올려 주세요.")
    try:
        os.startfile(str(out))                    # type: ignore[attr-defined]  (Windows)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    code = main()
    input("엔터를 누르면 닫힙니다.")
    sys.exit(code)
