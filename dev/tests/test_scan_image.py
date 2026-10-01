"""Color → Scan Crop 공통 모듈(`coords.scan_image`) 회귀 가드.

골든 출처(R3):
  - defect_abs_um, scan_center_abs_um, pixel_xy_um, Scan 크기 3168×1024 — `파일`
    (ColorImageGrabingInfo.ini / ScanResultImageList.txt / Params_WaferInfo.ini / JPEG)
  - clicked_scan_px — `관측` (사용자가 검증 도구에서 결함을 클릭한 위치, 11쌍 모두 일치로 기록)
  - predicted_scan_px — `유도` (중심 기준, X+/Y+, 추가 배율 1.0 식의 결과)
  원본: Scan_validation_20261001_155052.json (구현 제안서 부록).  이미지 처리는 합성 픽셀로 본다.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from aoi_verification.app.coords import scan_image as si

SCAN_WH = (3168, 1024)

# (color_file, scan_file, defect_abs, scan_center, pixel, predicted, clicked)
GOLDEN = [
    ("107552.233305.c.356640999.jpeg", "-97826.8291.t.jpeg",
     (107553.61144878, 233303.854163523), (106850.515642826, 233056.155203325),
     (1.5385029, 1.5385029), (2040.9999874254374, 672.9999956438186),
     (2043.2924016907207, 677.1310870771455)),
    ("165195.272498.c.140518092.jpeg", "-41209.47335.t.jpeg",
     (165197.212358301, 272498.239046532), (163552.526855249, 271976.590176756),
     (1.5385029, 1.5385029), (2653.0168364661668, 851.0626496550591),
     (2654.866094711332, 854.4405938457911)),
    ("173370.224372.c.-1563699932.jpeg", "-32500.-108.t.jpeg",
     (173368.717425765, 224372.431004787), (172157.146425354, 224513.204016328),
     (1.5385029, 1.5385029), (2371.499978330233, 420.5000024757873),
     (2372.4832144179404, 425.84520684584834)),
    ("185750.312482.c.-745137031.jpeg", "-19433.88486.t.jpeg",
     (185751.551158908, 312480.719990733), (185418.502644015, 313079.323614891),
     (1.5385029, 1.5385029), (1800.4757147308512, 122.91810476408148),
     (1800.9643063890828, 123.85409333394364)),
    ("196616.227784.c.-917364515.jpeg", "-6370.3113.t.jpeg",
     (196615.627313003, 227785.701433749), (198294.133930716, 227678.006233663),
     (1.5385029, 1.5385029), (493.000030020748, 581.9999981059439),
     (493.48115312743175, 582.9850877632895)),
    ("200936.245229.c.-2072857872.jpeg", "-2015.21012.t.jpeg",
     (200934.983921956, 245228.200693624), (202688.107928265, 245567.440573895),
     (1.5385029, 1.5385029), (444.5000313558112, 291.5000059661935),
     (444.49589160375285, 292.46687350042845)),
    ("250892.356667.c.1994251886.2.jpeg", "47100.132196.t.1.jpeg",
     (250891.984086476, 356667.294269001), (251592.78760944, 356820.649719416),
     (0.7696441, 0.7696441), (673.4446888321393, 312.74498016031794),
     (672.9728274512328, 314.2313813296734)),
    ("228430.166114.c.-1197347677.2.jpeg", "24173.-58345.t.2.jpeg",
     (228429.814473345, 166115.210738744), (229098.61758443, 166354.264551067),
     (0.7696441, 0.7696441), (715.022883063745, 201.39694032216636),
     (714.4445965160204, 200.99197083849037)),
    ("104881.238146.c.1031030510.2.jpeg", "-61069.47241.t.2.jpeg",
     (104881.423024388, 238145.877976747), (103978.139232364, 238235.631784586),
     (0.7682949, 0.7682949), (2759.699320695736, 395.17792056278626),
     (2759.760647227382, 395.4714329115022)),
    ("116912.177386.c.-432904197.2.jpeg", "-48028.-13834.t.2.jpeg",
     (116912.417781999, 177385.89224946), (117029.117656804, 177160.902018981),
     (0.7682949, 0.7682949), (1432.1053631815134, 804.8435819097793),
     (1432.546759225809, 809.6477546654479)),
    ("281505.238087.c.257008943.2.jpeg", "117170.47066.t.2.jpeg",
     (281505.446116386, 238088.300904983), (282218.98301714, 238093.763707389),
     (0.7682949, 0.7682949), (655.2721108079353, 504.88970627555517),
     (655.7493675969812, 505.38275740874025)),
]


@pytest.mark.parametrize("row", GOLDEN, ids=[g[0] for g in GOLDEN])
def test_golden_prediction_matches_validation(row):
    _c, _s, defect, center, px, predicted, _clicked = row
    u, v = si.scan_uv(defect, center, SCAN_WH, px)
    assert abs(u - predicted[0]) < 1e-6
    assert abs(v - predicted[1]) < 1e-6


def _max_click_err(fn) -> float:
    return max(math.hypot(*(a - b for a, b in zip(fn(r), r[6]))) for r in GOLDEN)


def test_golden_rejects_competing_hypotheses():
    """표본이 경쟁 가설(2배 보정·Y 반전·X 반전)을 실제로 구분하는지(R1)."""
    ours = _max_click_err(lambda r: si.scan_uv(r[2], r[3], SCAN_WH, r[4]))
    double = _max_click_err(lambda r: si.scan_uv(
        r[2], r[3], SCAN_WH, (r[4][0] / 2, r[4][1] / 2)))
    flip_y = _max_click_err(lambda r: (
        si.scan_uv(r[2], r[3], SCAN_WH, r[4])[0],
        SCAN_WH[1] - si.scan_uv(r[2], r[3], SCAN_WH, r[4])[1]))
    flip_x = _max_click_err(lambda r: (
        SCAN_WH[0] - si.scan_uv(r[2], r[3], SCAN_WH, r[4])[0],
        si.scan_uv(r[2], r[3], SCAN_WH, r[4])[1]))
    assert ours < 6.0                     # 관측 최대 5.43px
    assert min(double, flip_y, flip_x) > 50.0


def test_uses_actual_image_size_not_constant():
    d, c, px = (1000.0, 2000.0), (1000.0, 2000.0), (1.0, 1.0)
    assert si.scan_uv(d, c, (3168, 1024), px) == (1584.0, 512.0)
    assert si.scan_uv(d, c, (1536, 496), px) == (768.0, 248.0)


def test_crop_box_is_physical_300um_per_axis():
    # 픽셀 크기 1.5385 → 195px, 0.7696 → 390px (제안서 예시)
    assert si.crop_box((2041.0, 673.0), (1.5385029, 1.5385029))[2:] == (195, 195)
    assert si.crop_box((700.0, 300.0), (0.7696441, 0.7696441))[2:] == (390, 390)
    # 합성: X≠Y 면 픽셀 수가 축마다 다르다(물리 범위는 같다)
    left, top, cw, ch = si.crop_box((100.0, 100.0), (1.0, 2.0))
    assert (cw, ch) == (300, 150)
    assert (left, top) == (-50, 25)


def _entry(name, center, size=(100, 100)):
    return (Path(name), center, size)


def test_rank_prefers_full_coverage_then_center_distance_then_name():
    px = (1.0, 1.0)
    side = 20.0
    defect = (0.0, 0.0)
    edge = _entry("edge.t.jpeg", (45.0, 0.0))     # u=5 → Crop 이 왼쪽으로 잘림
    off = _entry("off.t.jpeg", (20.0, 10.0))      # 전부 담김, 중심에서 조금 떨어짐
    mid = _entry("b.t.jpeg", (0.0, 0.0))          # 정중앙
    twin = _entry("A.t.jpeg", (0.0, 0.0))         # 정중앙 동률 → 이름순
    out = si.rank_candidates(defect, [edge, off, mid, twin], px, side)
    assert [c.path.name for c in out] == ["A.t.jpeg", "b.t.jpeg", "off.t.jpeg",
                                          "edge.t.jpeg"]
    assert out[-1].coverage < 1.0 and out[0].coverage == 1.0


def test_rank_excludes_defect_outside_image():
    out = si.rank_candidates((0.0, 0.0), [_entry("far.t.jpeg", (500.0, 0.0))],
                             (1.0, 1.0))
    assert out == []


def test_parse_manifest_rules():
    text = ("Version=1\n"
            "a.t.jpeg,10.5,20.5\n"
            "a.t.jpeg,10.5,20.5\n"              # 같은 값 중복 → 하나
            "b.t.jpeg,1,2\n"
            "B.t.jpeg,3,4\n"                    # 대소문자만 다른 충돌 → b 제외
            "c.t.jpeg,nan,1\n"                  # NaN
            "d.t.jpeg,1\n"                      # 칸 부족
            "..\\e.t.jpeg,1,1\n"                # 경로 이동
            "C:\\f.t.jpeg,1,1\n"                # 절대경로
            "g.t.jpeg,x,1\n")                   # 숫자 아님
    status, rows = si.parse_manifest(text)
    assert status == si.OK
    assert rows == [("a.t.jpeg", 10.5, 20.5)]


@pytest.mark.parametrize("text", ["", "Version=2\na.t.jpeg,1,2\n", "a.t.jpeg,1,2\n"])
def test_parse_manifest_unsupported(text):
    assert si.parse_manifest(text) == (si.UNSUPPORTED_VERSION, [])


# ---------------------------------------------------------------------------
# 폴더 단위 (합성 이미지)
# ---------------------------------------------------------------------------
PIL = pytest.importorskip("PIL")


def _gradient(w, h):
    from PIL import Image
    im = Image.new("L", (w, h))
    im.putdata([(x + 3 * y) % 251 for y in range(h) for x in range(w)])
    return im


@pytest.fixture(autouse=True)
def _fresh_caches():
    si.clear_caches()
    yield
    si.clear_caches()


def _wafer(tmp_path, *, pixel=True, manifest=True, scan_name="-97826.8291.t.jpeg",
           listed_name=None, encoding="utf-8"):
    """Color 1장(점표기 이름, 절대 X/Y = 1000/2000) + Scan 1장(200×100, 중심 1000/2000)."""
    folder = tmp_path / "W1"
    folder.mkdir()
    color = folder / "1000.2000.c.123.jpeg"
    _gradient(40, 30).convert("RGB").save(color)
    _gradient(200, 100).save(folder / scan_name, quality=100, subsampling=0)
    if pixel:
        (folder / "Params_WaferInfo.ini").write_text(
            "[Info]\nRefPixelSizeX=1.0\nRefPixelSizeY=1.0\n")
    if manifest:
        (folder / si.SCAN_LIST_NAME).write_bytes(
            f"Version=1\n{listed_name or scan_name},1000.0,2000.0\n".encode(encoding))
    return folder, color


def test_resolve_ok_and_crop_is_centered_on_defect(tmp_path):
    _folder, color = _wafer(tmp_path)
    m = si.resolve(color, side_um=20.0)
    assert m.ok and m.defect_xy == (1000.0, 2000.0) and m.pixel_xy == (1.0, 1.0)
    best = m.best
    assert best.size_wh == (200, 100) and best.uv == (100.0, 50.0)
    assert best.box == (90, 40, 20, 20)
    crop, used = si.load_crop(m)
    assert used == best and crop.size == (20, 20)
    src = si.load_full(best)
    assert crop.tobytes() == src.crop((90, 40, 110, 60)).tobytes()


def test_resolve_matches_listed_name_case_insensitively_and_utf16(tmp_path):
    _folder, color = _wafer(tmp_path, listed_name="-97826.8291.T.JPEG",
                            encoding="utf-16")
    assert si.resolve(color).ok


def test_edge_crop_is_padded_gray_not_shifted(tmp_path):
    folder, color = _wafer(tmp_path)
    # 결함이 Scan 의 왼쪽 위 모서리 근처(u=2, v=3)가 되게 목록 중심을 옮긴다
    (folder / si.SCAN_LIST_NAME).write_text("Version=1\n-97826.8291.t.jpeg,1098,2047\n")
    m = si.resolve(color, side_um=10.0)
    assert m.ok and m.best.box == (-3, -2, 10, 10)
    crop, _ = si.load_crop(m)
    assert crop.getpixel((0, 0)) == si.PAD_GRAY
    assert crop.getpixel((9, 9)) == si.load_full(m.best).getpixel((6, 7))


@pytest.mark.parametrize("kw, status", [
    ({"manifest": False}, si.NO_MANIFEST),
    ({"pixel": False}, si.NO_PIXEL_SIZE),          # 0.77 기본값으로 자르지 않는다
    ({"listed_name": "missing.t.jpeg"}, si.NO_CANDIDATE),
])
def test_resolve_failure_states(tmp_path, kw, status):
    _folder, color = _wafer(tmp_path, **kw)
    m = si.resolve(color)
    assert m.status == status and not m.ok and si.load_crop(m) is None


def test_resolve_no_coordinates_for_non_dotted_name(tmp_path):
    folder, _color = _wafer(tmp_path)
    other = folder / "SLOT01_3_4_100_200.jpg"
    _gradient(10, 10).convert("RGB").save(other)
    assert si.resolve(other).status == si.NO_COORDINATES


def test_resolve_prefers_ini_raw_xy_over_filename(tmp_path):
    folder, color = _wafer(tmp_path)
    (folder / "ColorImageGrabingInfo.ini").write_text(
        f"[{color.name}]\nX=1010.0\nY=2005.0\n")
    m = si.resolve(color, side_um=20.0)
    assert m.defect_xy == (1010.0, 2005.0) and m.best.uv == (110.0, 55.0)


def test_unreadable_scan_returns_none(tmp_path):
    folder, color = _wafer(tmp_path)
    m = si.resolve(color)
    (folder / "-97826.8291.t.jpeg").write_bytes(b"broken")
    si.clear_caches()
    assert si.load_crop(m) is None
