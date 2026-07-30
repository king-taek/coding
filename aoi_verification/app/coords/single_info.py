"""사진 1장 → 사람이 읽는 결함 정보 줄 (엑셀·화면 공용).

``coords.resolve`` 와 ``coords.geometry.resolve`` 는 이미 이미지 한 장 경로만 받아
좌표/geometry 를 fail-safe 로 돌려준다.  이 모듈은 그 결과를 **문자열로 만드는 유일한
곳**이다 — 엑셀 미매칭 행(D열, :mod:`workers.exporter`) 과 단일 사진 정보 화면
(:mod:`ui.widgets.image_info_dialog`) 이 같은 함수를 써서 영원히 같은 값을 낸다.

    :func:`geometry_lines`  Surface.flt 기반 measurement 줄 (또는 미지원/없음 마커)
    :func:`coord_lines`     die col/row · die 내부 x/y · KLA 원본 좌표 줄
    :func:`defect_lines`    위 둘을 엑셀과 같은 순서로 이어붙인 것
    :func:`describe`        화면 패널용 — 파일 단위 항목 + 결함 줄, 값 있는 것만

PyQt·openpyxl 에 의존하지 않는다(무거운 의존성 없이 헤드리스 테스트되도록).
:mod:`coords` 의 관습대로 전 구간 fail-safe — **절대 raise 하지 않는다.**
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import i18n
from . import abs_coord, geometry, kla_info
from . import resolve as _resolve_coord

__all__ = ["InfoRow", "geometry_lines", "coord_lines", "defect_lines", "describe"]


@dataclass(frozen=True)
class InfoRow:
    """화면 패널의 한 줄.

    ``label`` 이 비어 있으면 값만 있는 줄(엑셀에 그대로 나가는 결함 정보 줄)이고,
    비어 있지 않으면 라벨/값 2열로 보여줄 파일 단위 항목이다.
    """
    label: str
    value: str


# ---------------------------------------------------------------------------
# 엑셀과 공유하는 결함 정보 줄
# ---------------------------------------------------------------------------
def geometry_lines(image_path: Path) -> list[str]:
    """Surface.flt 결함 measurement 줄.

    status 별 동작은 엑셀 표기와 동일하다:
    ``disabled`` → 빈 목록(마커도 안 띄움) / ``no_flt`` → 미지원 자재 마커 /
    ``no_data`` → 데이터 없음 마커 / ``ok`` → recipe·zone → area → width →
    length → contrast.
    """
    try:
        res = geometry.resolve(Path(image_path))
        if res.status == "disabled":
            return []
        if res.status == "ok" and res.geometry is not None:
            g = res.geometry
            # 이름만 표기(코드 숫자 없이). 이름을 못 찾은 자재만 코드로 폴백(빈칸 방지).
            recipe_disp = g.recipe_name or str(g.recipe)
            zone_disp = g.zone_name or str(g.zone)
            contrast = (i18n.KO.DEFECT_CONTRAST_NONE if g.contrast == 0
                        else i18n.KO.DEFECT_CONTRAST_FMT.format(v=g.contrast))
            return [
                i18n.KO.DEFECT_RECIPE_ZONE_FMT.format(recipe=recipe_disp,
                                                      zone=zone_disp),
                i18n.KO.DEFECT_AREA_FMT.format(v=g.area_um2),
                i18n.KO.DEFECT_WIDTH_FMT.format(v=g.width_um),
                i18n.KO.DEFECT_LENGTH_FMT.format(v=g.length_um),
                contrast,
            ]
        if res.status == "no_flt":
            return [i18n.KO.GEOM_NOT_SUPPORTED]
        return [i18n.KO.GEOM_NO_DATA]        # "no_data"
    except Exception:
        return []


def coord_lines(image_path: Path) -> list[str]:
    """die col/row 와 die 내부 local x/y(µm) 줄.

    Surface.flt 유무와 무관하므로 측정정보 미지원 자재에도 위치 식별용으로 붙는다.
    KLA 결함은 변환값(Camtek 좌표계) 아래에 자체 원본 좌표(XREL/YREL)도 덧붙인다.
    """
    try:
        c = _resolve_coord(Path(image_path))
        if c is None:
            return []
        lines = [
            i18n.KO.DEFECT_COLROW_FMT.format(col=c.col, row=c.row),
            i18n.KO.DEFECT_XY_FMT.format(x=c.x, y=c.y),
        ]
        if c.source == "kla" and c.native_x is not None and c.native_y is not None:
            lines.append(i18n.KO.EXPORT_KLA_NATIVE_FMT.format(
                x=c.native_x, y=c.native_y))
        return lines
    except Exception:
        return []


def defect_lines(image_path: Path) -> list[str]:
    """엑셀 미매칭 행 D열에 파일명 아래로 붙는 줄 전부 — geometry → 좌표 순."""
    p = Path(image_path)
    return geometry_lines(p) + coord_lines(p)


# ---------------------------------------------------------------------------
# 화면 패널용
# ---------------------------------------------------------------------------
def describe(image_path: Path) -> list[InfoRow]:
    """사진 1장의 정보 줄 목록 — **값을 얻은 항목만** 담는다.

    앞쪽은 파일 하나에서만 의미 있는 라벨/값 항목(파일명·폴더·WaferID·좌표 출처·
    절대 좌표·픽셀 크기), 뒤쪽은 :func:`defect_lines` 가 만든 엑셀과 동일한 줄들.
    매칭 상대·점수처럼 짝(기준↔검증)이 있어야 정해지는 값은 원리상 나올 수 없어
    아예 넣지 않는다.
    """
    p = Path(image_path)
    rows: list[InfoRow] = [
        InfoRow(i18n.KO.IMAGE_INFO_ROW_FILE, p.name),
        InfoRow(i18n.KO.IMAGE_INFO_ROW_FOLDER, str(p.parent)),
    ]

    wafer_id = _safe(lambda: kla_info.read_wafer_id(p.parent))
    if wafer_id:
        rows.append(InfoRow(i18n.KO.IMAGE_INFO_ROW_WAFER_ID, wafer_id))

    coord = _safe(lambda: _resolve_coord(p))
    if coord is not None:
        source = i18n.KO.IMAGE_INFO_SOURCE_NAMES.get(coord.source, coord.source)
        rows.append(InfoRow(i18n.KO.IMAGE_INFO_ROW_SOURCE, source))

    xy = _safe(lambda: abs_coord.absolute_xy(p))
    if xy is not None:
        rows.append(InfoRow(i18n.KO.IMAGE_INFO_ROW_ABS_XY,
                            i18n.KO.IMAGE_INFO_ABS_XY_FMT.format(x=xy[0], y=xy[1])))

    res = _safe(lambda: geometry.resolve(p))
    if res is not None and res.status == "ok" and res.geometry is not None:
        rows.append(InfoRow(i18n.KO.IMAGE_INFO_ROW_PIXEL,
                            i18n.KO.IMAGE_INFO_PIXEL_FMT.format(
                                v=res.geometry.pixel_um)))

    rows.extend(InfoRow("", line) for line in defect_lines(p))
    return rows


def _safe(fn):
    """호출이 실패하면 None — coords 파서들은 fail-safe 지만 호출부도 방어한다."""
    try:
        return fn()
    except Exception:
        return None
