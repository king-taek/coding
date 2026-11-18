"""다중 레시피 폴더의 Scan 연결 — 레시피마다 Scan 픽셀 크기가 다르다(`scan_image` · `pixel_size`).

실물(R3): AOI-22 TB500_RDL3 - Multi / Setup1 / MBL-RDL3 / S3RNW054XME4 (Scan 진단 수집 zip, 2026-11-19).
  - 결함 X/Y(ColorImageGrabingInfo.ini) · Scan 목록 · 픽셀 크기 · Scan 크기 — `파일`
    레시피 1: Params_WaferInfo.ini RefPixelSize 0.3858551, Scan 2848×1024 (20x)
    레시피 2: Recipe2-ProductInfo.ini Scan2DPixelSize 1.5380, Scan 3168×1024 (5x)
  - 레시피 2 사진 6장이 1.538 로 각자 다른 Scan 안에 든다 — `유도`.  레시피 1 값(0.3859)으로는 0/6 이었다
    (앱이 'Scan 없음' 을 띄운 원인, `관측`).  ⚠ 6장만으로는 1.15~3.99 가 다 들어맞아 값 자체는 표본이
    가르지 못한다(미구분) — 1.538 은 장비 파일에 적힌 값이라 쓴다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from aoi_verification.app.coords import pixel_size, scan_image as si  # noqa: E402

LIST = [
    "-108790.3917.t.1.jpeg,96360.7094181184,183653.645604025",
    "-98457.-15192.t.1.jpeg,106676.346954551,164533.796726972",
    "13154.3223.t.1.jpeg,218306.035152855,182845.739497349",
    "49323.-17364.t.1.jpeg,254456.020209678,162222.978497981",
    "-109824.3763.t.1.jpeg,95327.0996600671,183500.201001549",
    "-25083.-104462.t.1.jpeg,179966.933592375,75193.7801016912",
    "-25083.-104792.t.1.jpeg,179966.626335762,74864.1040784837",
    "23487.51360.t.1.jpeg,228684.29259041,230973.172226908",
    "73091.-89600.t.1.jpeg,278157.463320493,89964.0459330221",
    "97895.7409.t.1.jpeg,303051.819174787,186951.779978519",
    "36920.-40781.t.1.jpeg,242031.768508164,138817.337685201",
    "43122.90091.t.1.jpeg,248355.718644383,269686.204990077",
    "-276.19350.t.2.jpeg,204886.471901217,198982.728008225",
    "-14149.-56271.t.2.jpeg,190944.121799248,123370.263664239",
    "87576.-24442.t.2.jpeg,292697.987158937,155107.386493376",
    "27465.8315.t.2.jpeg,232619.02385854,187922.541519794",
    "-9525.-24129.t.2.jpeg,195598.317488045,155510.882322879",
    "-65011.2049.t.2.jpeg,140136.361646764,181741.152852449",
]
SIZE = {"1": (2848, 1024), "2": (3168, 1024)}

# (컬러 사진, 결함 X/Y, 기대 Scan — 레시피 1 은 Scan 이 겹쳐 레시피 번호만 본다)
RECIPE2 = [
    ((141158.802744997, 181803.65284157), "-65011.2049.t.2.jpeg"),
    ((191753.126133522, 123091.880043792), "-14149.-56271.t.2.jpeg"),
    ((197412.168777437, 155916.556322789), "-9525.-24129.t.2.jpeg"),
    ((206394.688814542, 199397.404841914), "-276.19350.t.2.jpeg"),
    ((233211.832294358, 187485.535630164), "27465.8315.t.2.jpeg"),
    ((292905.870761635, 154702.177663732), "87576.-24442.t.2.jpeg"),
]
RECIPE1 = [
    (106599.618129741, 164427.437486589), (179780.115584142, 75002.7659478116),
    (218280.95459409, 182756.221206902), (228614.81301172, 230859.415830376),
    (242201.402855701, 138946.197405897), (248055.165314896, 269550.672262914),
    (254109.117102761, 162141.929324474), (278021.282862794, 89942.6452083272),
    (303112.713146816, 186908.899336441), (95861.3439334231, 183623.084374896),
]


@pytest.fixture
def folder(tmp_path):
    si.clear_caches()
    pixel_size.scan_pixel_size_xy.cache_clear()
    pixel_size._recipe_pixel_size_xy.cache_clear()
    f = tmp_path / "S3RNW054XME4"
    f.mkdir()
    (f / si.SCAN_LIST_NAME).write_text("Version=1\n" + "\n".join(LIST) + "\n", encoding="utf-8")
    (f / "Params_WaferInfo.ini").write_text("[x]\nRefPixelSizeX=0.3858551\nRefPixelSizeY=0.3858551\n")
    (f / "ProductInfo.ini").write_text("[x]\nMag=20.000000\nScan2DPixelSize=0.3859\n")
    (f / "Recipe2-ProductInfo.ini").write_text("[x]\nMag=5.000000\nScan2DPixelSize=1.5380\n")
    for row in LIST:
        name = row.split(",")[0]
        Image.new("L", SIZE[name.split(".")[-2]]).save(f / name)
    return f


def test_recipe2_scans_use_their_own_pixel_size(folder):
    got = si.resolve_xy_many(folder, [xy for xy, _ in RECIPE2])
    assert [m.status for m in got] == [si.OK] * 6
    assert [m.best.path.name for m in got] == [name for _, name in RECIPE2]


def test_recipe1_unchanged(folder):
    got = si.resolve_xy_many(folder, RECIPE1)
    assert [m.status for m in got] == [si.OK] * 10
    assert all(m.best.path.name.endswith(".t.1.jpeg") for m in got)


def test_recipe_pixel_size_does_not_fall_back_to_recipe1(folder):
    """Recipe2 값이 없으면 레시피 1 값으로 대신하지 않는다 — 그 Scan 은 후보에서 빠진다."""
    (folder / "Recipe2-ProductInfo.ini").unlink()
    pixel_size._recipe_pixel_size_xy.cache_clear()
    assert pixel_size.scan_pixel_size_xy_recipe(folder, 2) is None
    assert pixel_size.scan_pixel_size_xy_recipe(folder, 1) == (0.3858551, 0.3858551)
    got = si.resolve_xy_many(folder, [xy for xy, _ in RECIPE2])
    assert [m.status for m in got] == [si.NO_CANDIDATE] * 6


def test_tile_recipe():
    assert si.tile_recipe("-65011.2049.t.2.jpeg") == 2
    assert si.tile_recipe("-97826.8291.t.jpeg") is None
    assert si.tile_recipe("13154.3223.t.1.jpeg") == 1
