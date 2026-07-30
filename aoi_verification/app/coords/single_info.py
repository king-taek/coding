"""사진 1장 → 결함 계측 정보 (엑셀·정보 화면 공용).

``coords.resolve`` 와 ``coords.geometry.resolve`` 는 이미 이미지 한 장 경로만 받아
좌표/geometry 를 fail-safe 로 돌려준다.  이 모듈은 그 결과를 **표기로 만드는 유일한
곳**이다 — 엑셀 미매칭 행(D열, :mod:`workers.exporter`) 과 단일 사진 정보 화면
(:mod:`ui.widgets.image_info_dialog`) 이 같은 수치를 쓴다.

두 소비자가 필요로 하는 모양이 다르다:

- 엑셀은 셀 안에 쌓을 **한 줄 문자열**("area 55.00 ㎛²")
- 정보 화면은 제도 시트 표의 **라벨/값 2열**("area" · "55.00 ㎛²")

그래서 계측 하나를 :class:`Measure` 로 만들고 세 표기(label·value·line)를 **한 자리에서
같은 수치로** 채운다.  따로 포맷하면 두 화면이 갈라진다.

    :func:`measures`        계측 항목(Measure) 목록 — 정보 화면이 소비
    :func:`geometry_lines`  Surface.flt 계측 줄 (또는 미지원/없음 마커)
    :func:`coord_lines`     die col/row · die 내부 x/y · KLA 원본 좌표 줄
    :func:`defect_lines`    위 둘을 엑셀과 같은 순서로 이어붙인 것
    :func:`describe`        정보 화면용 — 그룹별 행, **값을 얻은 항목만**

PyQt·openpyxl 에 의존하지 않는다(무거운 의존성 없이 헤드리스 테스트되도록).
:mod:`coords` 의 관습대로 전 구간 fail-safe — **절대 raise 하지 않는다.**
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import i18n
from . import abs_coord, geometry, kla_info
from . import resolve as _resolve_coord

__all__ = ["Measure", "InfoRow", "InfoGroup", "measures", "geometry_lines",
           "coord_lines", "defect_lines", "describe", "has_measurements"]


@dataclass(frozen=True)
class Measure:
    """계측 항목 하나 — 두 화면이 쓰는 세 표기를 함께 들고 있다."""
    label: str    # 정보 화면 좌측 라벨 ("area")
    value: str    # 정보 화면 우측 값, 모노로 찍힌다 ("55.00 ㎛²")
    line: str     # 엑셀 셀 안 한 줄 ("area 55.00 ㎛²")


@dataclass(frozen=True)
class InfoRow:
    """정보 화면 표의 한 행."""
    label: str
    value: str
    mono: bool = False   # 값이 수치라 모노로 찍을지
    stamp: str = ""      # 판정 스탬프 칩: "" | "ok" | "none"


@dataclass(frozen=True)
class InfoGroup:
    """제도 시트 표 한 덩이 — 타이틀블록 제목 + 행들."""
    title: str
    rows: tuple[InfoRow, ...]


def _m(label: str, value: str) -> Measure:
    """label/value 가 그대로 이어붙는 계측 — 엑셀 줄은 "라벨 값"."""
    return Measure(label=label, value=value, line=f"{label} {value}")


# ---------------------------------------------------------------------------
# 계측 항목 — 두 화면의 공통 원천
# ---------------------------------------------------------------------------
def geometry_measures(image_path: Path) -> tuple[str, list[Measure]]:
    """(status, 계측 목록).

    status 는 :func:`coords.geometry.resolve` 의 것을 그대로 넘긴다 —
    ``ok`` / ``disabled`` / ``no_flt`` / ``no_data``.  ``ok`` 가 아니면 목록은 빈다.
    """
    try:
        res = geometry.resolve(Path(image_path))
        if res.status != "ok" or res.geometry is None:
            return res.status, []
        g = res.geometry
        # 이름만 표기(코드 숫자 없이). 이름을 못 찾은 자재만 코드로 폴백(빈칸 방지).
        recipe_disp = g.recipe_name or str(g.recipe)
        zone_disp = g.zone_name or str(g.zone)
        # contrast 는 일부 자재(예: PI)만 측정 — 0 은 '측정 안 함'이라 0.00 대신 '—'.
        contrast_val = (i18n.KO.DEFECT_CONTRAST_NONE_VALUE if g.contrast == 0
                        else i18n.KO.DEFECT_CONTRAST_VALUE_FMT.format(v=g.contrast))
        return "ok", [
            Measure(
                label=i18n.KO.DEFECT_RECIPE_ZONE_LABEL,
                value=i18n.KO.DEFECT_RECIPE_ZONE_VALUE_FMT.format(
                    recipe=recipe_disp, zone=zone_disp),
                line=i18n.KO.DEFECT_RECIPE_ZONE_FMT.format(
                    recipe=recipe_disp, zone=zone_disp),
            ),
            _m(i18n.KO.DEFECT_AREA_LABEL,
               i18n.KO.DEFECT_AREA_VALUE_FMT.format(v=g.area_um2)),
            _m(i18n.KO.DEFECT_WIDTH_LABEL,
               i18n.KO.DEFECT_WIDTH_VALUE_FMT.format(v=g.width_um)),
            _m(i18n.KO.DEFECT_LENGTH_LABEL,
               i18n.KO.DEFECT_LENGTH_VALUE_FMT.format(v=g.length_um)),
            _m(i18n.KO.DEFECT_CONTRAST_LABEL, contrast_val),
        ]
    except Exception:
        return "no_data", []


def coord_measures(image_path: Path) -> list[Measure]:
    """die col/row 와 die 내부 local x/y(µm).

    Surface.flt 유무와 무관하므로 측정정보 미지원 자재에도 위치 식별용으로 붙는다.
    KLA 결함은 변환값(Camtek 좌표계) 아래에 자체 원본 좌표(XREL/YREL)도 덧붙인다.
    """
    try:
        c = _resolve_coord(Path(image_path))
        if c is None:
            return []
        out = [
            Measure(
                label=i18n.KO.DEFECT_COLROW_LABEL,
                value=i18n.KO.DEFECT_COLROW_VALUE_FMT.format(col=c.col, row=c.row),
                line=i18n.KO.DEFECT_COLROW_FMT.format(col=c.col, row=c.row),
            ),
            Measure(
                label=i18n.KO.DEFECT_XY_LABEL,
                value=i18n.KO.DEFECT_XY_VALUE_FMT.format(x=c.x, y=c.y),
                line=i18n.KO.DEFECT_XY_FMT.format(x=c.x, y=c.y),
            ),
        ]
        if c.source == "kla" and c.native_x is not None and c.native_y is not None:
            out.append(Measure(
                label=i18n.KO.DEFECT_KLA_NATIVE_LABEL,
                value=i18n.KO.DEFECT_XY_VALUE_FMT.format(x=c.native_x,
                                                         y=c.native_y),
                line=i18n.KO.EXPORT_KLA_NATIVE_FMT.format(x=c.native_x,
                                                          y=c.native_y),
            ))
        return out
    except Exception:
        return []


def measures(image_path: Path) -> list[Measure]:
    """계측 전부 — geometry 다음 좌표, 엑셀과 같은 순서."""
    p = Path(image_path)
    return geometry_measures(p)[1] + coord_measures(p)


# ---------------------------------------------------------------------------
# 엑셀 표기 — 계측에서 파생
# ---------------------------------------------------------------------------
def geometry_lines(image_path: Path) -> list[str]:
    """Surface.flt 계측 줄.

    status 별 동작은 엑셀 표기와 동일하다: ``disabled`` → 빈 목록(마커도 안 띄움) /
    ``no_flt`` → 미지원 자재 마커 / ``no_data`` → 데이터 없음 마커.
    """
    status, ms = geometry_measures(Path(image_path))
    if status == "ok":
        return [m.line for m in ms]
    if status == "disabled":
        return []
    if status == "no_flt":
        return [i18n.KO.GEOM_NOT_SUPPORTED]
    return [i18n.KO.GEOM_NO_DATA]        # "no_data"


def coord_lines(image_path: Path) -> list[str]:
    """die col/row 와 die 내부 local x/y(µm) 줄."""
    return [m.line for m in coord_measures(Path(image_path))]


def defect_lines(image_path: Path) -> list[str]:
    """엑셀 미매칭 행 D열에 파일명 아래로 붙는 줄 전부 — geometry → 좌표 순."""
    p = Path(image_path)
    return geometry_lines(p) + coord_lines(p)


# ---------------------------------------------------------------------------
# 정보 화면용 — 제도 시트 표
# ---------------------------------------------------------------------------
def describe(image_path: Path) -> list[InfoGroup]:
    """사진 1장의 정보를 두 덩이로 — **값을 얻은 항목만** 담는다.

    ``파일``   식별 정보(파일명·폴더·WaferID·좌표 출처)
    ``결함 계측`` 좌표·측정값, 그리고 측정정보 유무를 알리는 판정 스탬프

    매칭 상대·점수처럼 짝(기준↔검증)이 있어야 정해지는 값은 원리상 나올 수 없어
    아예 넣지 않는다.  계측을 하나도 못 얻으면 두 번째 덩이는 반환하지 않는다.
    """
    p = Path(image_path)
    groups: list[InfoGroup] = []

    file_rows = [
        InfoRow(i18n.KO.IMAGE_INFO_ROW_FILE, p.name, mono=True),
        InfoRow(i18n.KO.IMAGE_INFO_ROW_FOLDER, str(p.parent), mono=True),
    ]
    wafer_id = _safe(lambda: kla_info.read_wafer_id(p.parent))
    if wafer_id:
        file_rows.append(
            InfoRow(i18n.KO.IMAGE_INFO_ROW_WAFER_ID, wafer_id, mono=True))
    coord = _safe(lambda: _resolve_coord(p))
    if coord is not None:
        file_rows.append(InfoRow(
            i18n.KO.IMAGE_INFO_ROW_SOURCE,
            i18n.KO.IMAGE_INFO_SOURCE_NAMES.get(coord.source, coord.source)))
    groups.append(InfoGroup(i18n.KO.IMAGE_INFO_GROUP_FILE, tuple(file_rows)))

    defect_rows = [InfoRow(m.label, m.value, mono=True)
                   for m in coord_measures(p)]
    xy = _safe(lambda: abs_coord.absolute_xy(p))
    if xy is not None:
        defect_rows.append(InfoRow(
            i18n.KO.IMAGE_INFO_ROW_ABS_XY,
            i18n.KO.IMAGE_INFO_ABS_XY_FMT.format(x=xy[0], y=xy[1]), mono=True))

    status, geo = geometry_measures(p)
    defect_rows.extend(InfoRow(m.label, m.value, mono=True) for m in geo)
    if status == "ok":
        res = _safe(lambda: geometry.resolve(p))
        if res is not None and res.geometry is not None:
            defect_rows.append(InfoRow(
                i18n.KO.IMAGE_INFO_ROW_PIXEL,
                i18n.KO.IMAGE_INFO_PIXEL_FMT.format(v=res.geometry.pixel_um),
                mono=True))

    # ★ 계측을 **하나도** 못 얻었으면 덩이 자체를 만들지 않는다.  스탬프만 남기면
    #   "이 자재는 원래 측정 안 함"(정상)과 "정보파일이 없어 못 읽음"(비정상)이
    #   화면상 구별되지 않고, 호출부가 '읽을 정보 없음' 안내를 띄울 기회를 잃는다.
    if not defect_rows:
        return groups

    if status == "ok":
        defect_rows.append(InfoRow(
            "", i18n.KO.IMAGE_INFO_STAMP_MEASURED, stamp="ok"))
    elif status == "no_flt":
        defect_rows.append(InfoRow("", i18n.KO.GEOM_NOT_SUPPORTED, stamp="none"))
    elif status == "no_data":
        defect_rows.append(InfoRow("", i18n.KO.GEOM_NO_DATA, stamp="none"))
    # status == "disabled" 는 스탬프도 띄우지 않는다(엑셀과 같은 규칙).

    groups.append(InfoGroup(i18n.KO.IMAGE_INFO_GROUP_DEFECT,
                            tuple(defect_rows)))
    return groups


def has_measurements(groups: list[InfoGroup]) -> bool:
    """:func:`describe` 결과에 결함 계측 덩이가 있는지 — 호출부의 '없음' 판정용."""
    return any(g.title == i18n.KO.IMAGE_INFO_GROUP_DEFECT for g in groups)


def _safe(fn):
    """호출이 실패하면 None — coords 파서들은 fail-safe 지만 호출부도 방어한다."""
    try:
        return fn()
    except Exception:
        return None
