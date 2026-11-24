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
from . import abs_coord, geometry
from ..models import lot_info
from . import resolve as _resolve_coord
from .models import KIND_HEIGHT, NUMBER_WARN_KLA_GUESS, NUMBER_WARN_NO_TABLE

__all__ = ["Measure", "InfoRow", "InfoGroup", "measures", "geometry_lines",
           "coord_lines", "defect_lines", "describe", "describe_in_scan",
           "scan_file_group", "has_measurements"]


@dataclass(frozen=True)
class Measure:
    """계측 항목 하나 — 두 화면이 쓰는 세 표기를 함께 들고 있다."""
    label: str    # 정보 화면 좌측 라벨 ("area")
    value: str    # 정보 화면 우측 값, 모노로 찍힌다 ("55.00 ㎛²")
    line: str     # 엑셀 셀 안 한 줄 ("area 55.00 ㎛²")
    cluster: str = ""   # 정보 화면 묶음 — 같은 묶음끼리는 구분선 없이 붙인다


@dataclass(frozen=True)
class InfoRow:
    """정보 화면 표의 한 행."""
    label: str
    value: str
    mono: bool = False   # 값이 수치라 모노로 찍을지
    stamp: str = ""      # 판정 스탬프 칩: "" | "ok" | "none"
    cluster: str = ""    # 묶음 — 구분선은 묶음이 바뀌는 곳에만 긋는다


@dataclass(frozen=True)
class InfoGroup:
    """제도 시트 표 한 덩이 — 타이틀블록 제목 + 행들."""
    title: str
    rows: tuple[InfoRow, ...]


# 정보 화면 묶음(사용자 지시 2026-11-23 — 묶음 안 구분선을 없애 읽기 쉽게).
CLUSTER_FILE = "file"     # 파일명 · 폴더 · WaferID · 좌표 출처
CLUSTER_WHERE = "where"   # col/row · x/y · 절대 X/Y
CLUSTER_WHAT = "what"     # recipe/zone · type · defect · classify
CLUSTER_SIZE = "size"     # area · width · length · contrast(또는 height) · 픽셀 크기
CLUSTER_STAMP = "stamp"   # 측정정보 판정 스탬프


def _m(label: str, value: str, cluster: str = "") -> Measure:
    """label/value 가 그대로 이어붙는 계측 — 엑셀 줄은 "라벨 값"."""
    return Measure(label=label, value=value, line=f"{label} {value}",
                   cluster=cluster)


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
        recipe_disp = (i18n.KO.DEFECT_RECIPE_NONE_VALUE if g.recipe is None
                       else g.recipe_name or str(g.recipe))
        zone_disp = g.zone_name or str(g.zone)
        head = [
            Measure(
                label=i18n.KO.DEFECT_RECIPE_ZONE_LABEL,
                value=i18n.KO.DEFECT_RECIPE_ZONE_VALUE_FMT.format(
                    recipe=recipe_disp, zone=zone_disp),
                line=i18n.KO.DEFECT_RECIPE_ZONE_FMT.format(
                    recipe=recipe_disp, zone=zone_disp),
                cluster=CLUSTER_WHAT,
            ),
            # 장비 Export 의 Type 과 같은 모양 — '<검사>-<Zone>'(예: Height-amk_mask_6_170).
            _m(i18n.KO.DEFECT_TYPE_LABEL,
               i18n.KO.DEFECT_TYPE_VALUE_FMT.format(kind=g.kind, zone=zone_disp),
               CLUSTER_WHAT),
        ]
        # 이름 두 가지(사용자 결정 2026-11-23 '둘 다') — 사전에 없으면 그 줄만 뺀다.
        if g.type_name:
            head.append(_m(i18n.KO.DEFECT_TYPE_NAME_LABEL, g.type_name,
                           CLUSTER_WHAT))
        if g.class_name:
            head.append(_m(i18n.KO.DEFECT_CLASS_LABEL, g.class_name,
                           CLUSTER_WHAT))
        if g.kind == KIND_HEIGHT:
            return "ok", head + [
                _m(i18n.KO.DEFECT_HEIGHT_LABEL,
                   i18n.KO.DEFECT_HEIGHT_VALUE_FMT.format(v=g.height_um),
                   CLUSTER_SIZE),
            ]
        # contrast 는 일부 자재(예: PI)만 측정 — 0 은 '측정 안 함'이라 0.00 대신 '—'.
        contrast_val = (i18n.KO.DEFECT_CONTRAST_NONE_VALUE if g.contrast == 0
                        else i18n.KO.DEFECT_CONTRAST_VALUE_FMT.format(v=g.contrast))
        return "ok", head + [
            _m(i18n.KO.DEFECT_AREA_LABEL,
               i18n.KO.DEFECT_AREA_VALUE_FMT.format(v=g.area_um2), CLUSTER_SIZE),
            _m(i18n.KO.DEFECT_WIDTH_LABEL,
               i18n.KO.DEFECT_WIDTH_VALUE_FMT.format(v=g.width_um), CLUSTER_SIZE),
            _m(i18n.KO.DEFECT_LENGTH_LABEL,
               i18n.KO.DEFECT_LENGTH_VALUE_FMT.format(v=g.length_um), CLUSTER_SIZE),
            _m(i18n.KO.DEFECT_CONTRAST_LABEL, contrast_val, CLUSTER_SIZE),
        ]
    except Exception:
        return "no_data", []


# DefectCoord.number_warn → ko.py 문구 이름.  camtek_abs 는 위에서 숫자 자체를 바꾼다.
_NUMBER_WARN_TEXT = {
    NUMBER_WARN_NO_TABLE: "DEFECT_NUMBER_WARN_NO_TABLE",
    NUMBER_WARN_KLA_GUESS: "DEFECT_NUMBER_WARN_KLA_GUESS",
}


def coord_measures(image_path: Path) -> list[Measure]:
    """die col/row 와 die 내부 local x/y(µm).

    Surface.flt 유무와 무관하므로 측정정보 미지원 자재에도 위치 식별용으로 붙는다.
    KLA 결함은 변환값(Camtek 좌표계) 아래에 자체 원본 좌표(XREL/YREL)도 덧붙인다.
    """
    try:
        c = _resolve_coord(Path(image_path))
        if c is None:
            return []
        if c.source == "camtek_abs":
            # die pitch 를 몰라 die 로 못 쪼갠 경우 — 있는 사실만 적는다.
            # col 도 상수 오프셋이라 첫 열이 2 가 아닌 제품(RDL4 1 · QORVO 5)에서 틀린다 —
            # 숫자 대신 경고를 적고(사용자 결정 2026-10-25), **절대 wafer 좌표**를 준다.
            return [
                Measure(
                    label=i18n.KO.DEFECT_COL_ONLY_LABEL,
                    value=i18n.KO.DEFECT_COL_UNKNOWN_VALUE,
                    line=i18n.KO.DEFECT_COL_UNKNOWN_FMT,
                    cluster=CLUSTER_WHERE,
                ),
                Measure(
                    label=i18n.KO.DEFECT_ABS_XY_LABEL,
                    value=i18n.KO.DEFECT_XY_VALUE_FMT.format(x=c.x, y=c.y),
                    line=i18n.KO.DEFECT_ABS_XY_FMT.format(x=c.x, y=c.y),
                    cluster=CLUSTER_WHERE,
                ),
            ]
        out = [
            # 화면 번호(레시피 시프트 적용) — 장비 화면과 같게(사용자 결정 2026-10-25).
            Measure(
                label=i18n.KO.DEFECT_COLROW_LABEL,
                value=i18n.KO.DEFECT_COLROW_VALUE_FMT.format(col=c.screen_col,
                                                            row=c.screen_row),
                line=i18n.KO.DEFECT_COLROW_FMT.format(col=c.screen_col, row=c.screen_row),
                cluster=CLUSTER_WHERE,
            ),
        ]
        # 번호가 틀렸을 수 있을 때만 바로 밑에 경고 — 정상이면 아무것도 붙이지 않는다.
        warn = _NUMBER_WARN_TEXT.get(c.number_warn)
        if warn:
            text = getattr(i18n.KO, warn)
            out.append(Measure(label=i18n.KO.DEFECT_NUMBER_WARN_LABEL, value=text,
                               line=i18n.KO.DEFECT_NUMBER_WARN_FMT.format(text=text),
                               cluster=CLUSTER_WHERE))
        out += [
            Measure(
                label=i18n.KO.DEFECT_XY_LABEL,
                value=i18n.KO.DEFECT_XY_VALUE_FMT.format(x=c.x, y=c.y),
                line=i18n.KO.DEFECT_XY_FMT.format(x=c.x, y=c.y),
                cluster=CLUSTER_WHERE,
            ),
        ]
        if c.source == "kla" and c.native_x is not None and c.native_y is not None:
            out.append(Measure(
                label=i18n.KO.DEFECT_KLA_NATIVE_LABEL,
                value=i18n.KO.DEFECT_XY_VALUE_FMT.format(x=c.native_x,
                                                         y=c.native_y),
                line=i18n.KO.EXPORT_KLA_NATIVE_FMT.format(x=c.native_x,
                                                          y=c.native_y),
                cluster=CLUSTER_WHERE,
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
        InfoRow(i18n.KO.IMAGE_INFO_ROW_FILE, p.name, mono=True,
                cluster=CLUSTER_FILE),
        *_id_rows(p.parent),
        InfoRow(i18n.KO.IMAGE_INFO_ROW_FOLDER, str(p.parent), mono=True,
                cluster=CLUSTER_FILE),
    ]
    coord = _safe(lambda: _resolve_coord(p))
    if coord is not None:
        file_rows.append(InfoRow(
            i18n.KO.IMAGE_INFO_ROW_SOURCE,
            i18n.KO.IMAGE_INFO_SOURCE_NAMES.get(coord.source, coord.source),
            cluster=CLUSTER_FILE))
    groups.append(InfoGroup(i18n.KO.IMAGE_INFO_GROUP_FILE, tuple(file_rows)))

    defect_rows = [InfoRow(m.label, m.value, mono=True, cluster=m.cluster)
                   for m in coord_measures(p)]
    xy = _safe(lambda: abs_coord.absolute_xy(p))
    if xy is not None:
        defect_rows.append(InfoRow(
            i18n.KO.IMAGE_INFO_ROW_ABS_XY,
            i18n.KO.IMAGE_INFO_ABS_XY_FMT.format(x=xy[0], y=xy[1]), mono=True,
            cluster=CLUSTER_WHERE))

    status, geo = geometry_measures(p)
    defect_rows.extend(InfoRow(m.label, m.value, mono=True, cluster=m.cluster)
                       for m in geo)
    if status == "ok":
        res = _safe(lambda: geometry.resolve(p))
        if res is not None and res.geometry is not None:
            defect_rows.append(InfoRow(
                i18n.KO.IMAGE_INFO_ROW_PIXEL,
                i18n.KO.IMAGE_INFO_PIXEL_FMT.format(v=res.geometry.pixel_um),
                mono=True, cluster=CLUSTER_SIZE))

    # ★ 계측을 **하나도** 못 얻었으면 덩이 자체를 만들지 않는다.  스탬프만 남기면
    #   "이 자재는 원래 측정 안 함"(정상)과 "정보파일이 없어 못 읽음"(비정상)이
    #   화면상 구별되지 않고, 호출부가 '읽을 정보 없음' 안내를 띄울 기회를 잃는다.
    if not defect_rows:
        return groups

    if status == "ok":
        defect_rows.append(InfoRow(
            "", i18n.KO.IMAGE_INFO_STAMP_MEASURED, stamp="ok",
            cluster=CLUSTER_STAMP))
    elif status == "no_flt":
        defect_rows.append(InfoRow("", i18n.KO.GEOM_NOT_SUPPORTED, stamp="none",
                                   cluster=CLUSTER_STAMP))
    elif status == "no_data":
        defect_rows.append(InfoRow("", i18n.KO.GEOM_NO_DATA, stamp="none",
                                   cluster=CLUSTER_STAMP))
    # status == "disabled" 는 스탬프도 띄우지 않는다(엑셀과 같은 규칙).

    groups.append(InfoGroup(i18n.KO.IMAGE_INFO_GROUP_DEFECT,
                            tuple(defect_rows)))
    return groups


def scan_file_group(scan_path: Path) -> InfoGroup:
    """흑백 Scan 사진 자체의 '파일' 덩이 — 파일명·폴더·WaferID 만.

    Scan 사진에는 :func:`describe` 를 쓰지 않는다 — Scan 파일명의 두 수치는 절대좌표가 아닌데
    점표기 해석(:func:`abs_coord.dotted_xy`)이 그것을 좌표로 읽어 엉뚱한 '절대 X / Y' 를 낸다."""
    p = Path(scan_path)
    rows = [InfoRow(i18n.KO.IMAGE_INFO_ROW_SCAN, p.name, mono=True, cluster=CLUSTER_FILE),
            *_id_rows(p.parent),
            InfoRow(i18n.KO.IMAGE_INFO_ROW_FOLDER, str(p.parent), mono=True,
                    cluster=CLUSTER_FILE)]
    return InfoGroup(i18n.KO.IMAGE_INFO_GROUP_FILE, tuple(rows))


def describe_in_scan(scan_path: Path, defect_path: Path) -> list[InfoGroup]:
    """흑백 Scan 사진 안에서 찾은 Defect 하나 — 그 Defect 의 Color 사진과 **같은 생산자**
    (:func:`describe`)로 만들고, '파일' 덩이 맨 위에 연 Scan 사진 이름을 붙인다.
    Defect 의 파일명 줄은 'Defect 사진' 으로 부른다(사진이 폴더에 없어도 정보파일로 읽힌다)."""
    groups = describe(Path(defect_path))
    out = []
    for g in groups:
        if g.title == i18n.KO.IMAGE_INFO_GROUP_FILE:
            rows = [InfoRow(i18n.KO.IMAGE_INFO_ROW_SCAN, Path(scan_path).name, mono=True,
                            cluster=CLUSTER_FILE)]
            rows += [InfoRow(i18n.KO.IMAGE_INFO_ROW_DEFECT_PHOTO, r.value, r.mono, r.stamp,
                             r.cluster) if r.label == i18n.KO.IMAGE_INFO_ROW_FILE else r
                     for r in g.rows]
            g = InfoGroup(g.title, tuple(rows))
        out.append(g)
    return out


def _id_rows(folder: Path) -> list[InfoRow]:
    """LOT · Wafer ID · Slot# 세 줄 — 장비 파일에서 못 읽은 칸은 '—'(현택님 결정 2026-11-24: 줄은 늘 둔다)."""
    ids = _safe(lambda: lot_info.read_wafer_ids(folder)) or lot_info.WaferIds()
    blank = i18n.KO.IMAGE_INFO_UNKNOWN
    return [InfoRow(label, value or blank, mono=True, cluster=CLUSTER_FILE)
            for label, value in ((i18n.KO.IMAGE_INFO_ROW_LOT, ids.lot),
                                 (i18n.KO.IMAGE_INFO_ROW_WAFER_ID, ids.wafer_id),
                                 (i18n.KO.IMAGE_INFO_ROW_SLOT, ids.slot))]


def has_measurements(groups: list[InfoGroup]) -> bool:
    """:func:`describe` 결과에 결함 계측 덩이가 있는지 — 호출부의 '없음' 판정용."""
    return any(g.title == i18n.KO.IMAGE_INFO_GROUP_DEFECT for g in groups)


def _safe(fn):
    """호출이 실패하면 None — coords 파서들은 fail-safe 지만 호출부도 방어한다."""
    try:
        return fn()
    except Exception:
        return None
