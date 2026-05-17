"""동일 defect 그룹화 (#5) — 알고리즘 단위 테스트.

phase correlation 이 작은 평행 이동에 강하다는 가정을 검증한다.  fake
pHash/score 가 아니라 실제 pipeline + cv2 를 통과시키는 통합형 테스트.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

PIL = pytest.importorskip("PIL.Image")
from PIL import Image                                                # noqa: E402

cv2 = pytest.importorskip("cv2")

from aoi_verification.app.models.slot import ImageItem                # noqa: E402
from aoi_verification.app.similarity.grouping import group_slot       # noqa: E402


def _img(folder: Path, name: str, base: np.ndarray,
         shift: tuple[int, int] = (0, 0)) -> Path:
    """`base` 를 (dx, dy) 만큼 평행이동한 사진 저장."""
    dx, dy = shift
    canvas = np.zeros_like(base)
    h, w = base.shape[:2]
    canvas[max(0, dy):h + min(0, dy), max(0, dx):w + min(0, dx)] = (
        base[max(0, -dy):h + min(0, -dy), max(0, -dx):w + min(0, -dx)]
    )
    p = folder / name
    Image.fromarray(canvas).save(str(p), "JPEG")
    return p


def _items(paths: list[Path]) -> list[ImageItem]:
    return [ImageItem(slot="S1", path=p, side="ref") for p in paths]


def test_shifted_versions_are_grouped(tmp_path, isolated_cache):
    """같은 패턴이 작은 평행이동만 다르면 한 그룹으로 묶인다."""
    rng = np.random.RandomState(42)
    base = rng.randint(0, 255, (400, 400, 3), dtype=np.uint8)
    base[150:250, 150:250] = [255, 0, 0]    # 분명한 결함 마커

    paths = [
        _img(tmp_path, "same_0.jpg", base, shift=(0, 0)),
        _img(tmp_path, "same_1.jpg", base, shift=(5, 0)),
        _img(tmp_path, "same_2.jpg", base, shift=(0, 8)),
        _img(tmp_path, "same_3.jpg", base, shift=(-4, -6)),
    ]
    groups = group_slot(_items(paths))
    # ‘동일 defect’ 한 그룹 (≥2) 만 검사 — 싱글톤 (있을 수 있음) 은 제외.
    big = [g for g in groups if len(g) >= 2]
    assert len(big) == 1, f"한 그룹으로 묶여야 함: {[len(g) for g in groups]}"
    assert {it.path.name for it in big[0]} == {p.name for p in paths}


def test_unrelated_images_are_not_grouped(tmp_path, isolated_cache):
    """완전히 다른 사진들은 같은 그룹으로 묶이지 않는다."""
    rng = np.random.RandomState(0)
    paths = []
    for i in range(4):
        rnd = rng.randint(0, 255, (400, 400, 3), dtype=np.uint8)
        p = tmp_path / f"diff_{i}.jpg"
        Image.fromarray(rnd).save(str(p), "JPEG")
        paths.append(p)
    groups = group_slot(_items(paths))
    big = [g for g in groups if len(g) >= 2]
    assert big == [], f"무관 사진이 묶이면 안 됨: {[len(g) for g in groups]}"


def test_mixed_input_yields_one_group_plus_singletons(tmp_path, isolated_cache):
    """동일-defect 3 장 + 무관 2 장 → 한 그룹 + 싱글톤 2 개."""
    rng = np.random.RandomState(123)
    base = rng.randint(0, 255, (400, 400, 3), dtype=np.uint8)
    base[100:200, 200:300] = [0, 255, 0]
    same_paths = [
        _img(tmp_path, "s0.jpg", base, shift=(0, 0)),
        _img(tmp_path, "s1.jpg", base, shift=(6, 0)),
        _img(tmp_path, "s2.jpg", base, shift=(0, -4)),
    ]
    diff_paths = []
    for i in range(2):
        rnd = rng.randint(0, 255, (400, 400, 3), dtype=np.uint8)
        p = tmp_path / f"d{i}.jpg"
        Image.fromarray(rnd).save(str(p), "JPEG")
        diff_paths.append(p)

    groups = group_slot(_items(same_paths + diff_paths))
    big = [g for g in groups if len(g) >= 2]
    singles = [g for g in groups if len(g) == 1]
    assert len(big) == 1
    assert len(big[0]) == 3
    assert len(singles) == 2


def test_single_item_returns_one_singleton(tmp_path, isolated_cache):
    p = tmp_path / "only.jpg"
    Image.new("RGB", (200, 200), "gray").save(str(p), "JPEG")
    groups = group_slot(_items([p]))
    assert groups == [[ImageItem(slot="S1", path=p, side="ref")]]
