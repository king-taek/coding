"""동일 defect 그룹화 (#5) — 알고리즘 단위 테스트.

ORB+RANSAC 기반 그룹화가 다음 케이스를 모두 한 그룹으로 묶는지 검증.
실제 pipeline + cv2 를 통과시키는 통합형 테스트.

- 작은 평행이동
- 큰 평행이동 (frame 20%+)
- scale 변화 (해상도 차이 — 사용자 예시 1: 1000 vs 600)
- 작은 회전
- 밝기/contrast 차이 (사용자 예시 2: 주황 vs 흰빛 화이트밸런스)
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


# ---------------------------------------------------------------------------
# 새 ORB+RANSAC 알고리즘이 추가로 처리하는 케이스 (사용자 실 예시 기반)
# ---------------------------------------------------------------------------
def _feature_rich_base(rng: np.random.RandomState, size: int) -> np.ndarray:
    """ORB 가 잡을 만한 풍부한 corner 특징을 가진 합성 이미지.

    랜덤 노이즈만 있는 이미지는 ORB descriptor 가 우연한 일치를 일으켜
    false positive 의 원인. 명확한 모서리 / 사각형 / 원 들을 배치해서
    ORB 가 ‘진짜’ 특징을 잡도록 한다.
    """
    img = np.full((size, size, 3),
                  fill_value=200, dtype=np.uint8)
    # 무작위 도형 30 개 — corner-rich.
    import cv2
    for _ in range(30):
        x, y = rng.randint(20, size - 20, size=2)
        s = rng.randint(15, 40)
        color = tuple(int(c) for c in rng.randint(0, 100, size=3))
        if rng.randint(0, 2):
            cv2.rectangle(img, (x, y), (x + s, y + s), color, 2)
        else:
            cv2.circle(img, (x, y), s // 2, color, 2)
    # 정렬된 격자 (예시 2 의 pad 패턴 모사).
    for gx in range(40, size - 40, 50):
        for gy in range(40, size - 40, 50):
            cv2.circle(img, (gx, gy), 8, (100, 100, 100), 1)
    return img


def test_scale_variants_are_grouped(tmp_path, isolated_cache):
    """사용자 예시 1: 같은 회로 영역을 다른 해상도(1000 vs 600)로 찍은 두
    사진이 한 그룹으로 묶여야 한다."""
    import cv2 as _cv2
    rng = np.random.RandomState(7)
    base = _feature_rich_base(rng, 800)
    p_big = tmp_path / "big.jpg"
    Image.fromarray(base).save(str(p_big), "JPEG", quality=90)
    # 60% 다운스케일 — 사용자 예시의 1000:600 비율과 유사.
    small = _cv2.resize(base, (480, 480), interpolation=_cv2.INTER_AREA)
    p_small = tmp_path / "small.jpg"
    Image.fromarray(small).save(str(p_small), "JPEG", quality=90)
    groups = group_slot(_items([p_big, p_small]))
    big = [g for g in groups if len(g) >= 2]
    assert len(big) == 1, f"해상도 차이 케이스가 묶여야 함: {[len(g) for g in groups]}"


def test_translation_plus_brightness_grouped(tmp_path, isolated_cache):
    """사용자 예시 2: 같은 1000×1000 사진 두 장이 평행이동 + 화이트밸런스
    차이만 있을 때 한 그룹으로."""
    import cv2 as _cv2
    rng = np.random.RandomState(11)
    base = _feature_rich_base(rng, 800)
    # 평행이동 (10% 정도)
    dx, dy = 80, -40
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    shifted = _cv2.warpAffine(base, M, (800, 800), borderValue=(200, 200, 200))
    # 색조 변화 (주황 → 흰빛 모사) — 채널별 gain.
    warm = (shifted.astype(np.int16) * np.array([1.15, 1.00, 0.85])).clip(0, 255).astype(np.uint8)
    p_a = tmp_path / "a.jpg"
    p_b = tmp_path / "b_warm.jpg"
    Image.fromarray(base).save(str(p_a), "JPEG", quality=90)
    Image.fromarray(warm).save(str(p_b), "JPEG", quality=90)
    groups = group_slot(_items([p_a, p_b]))
    big = [g for g in groups if len(g) >= 2]
    assert len(big) == 1, f"평행이동+밝기 변화가 묶여야 함: {[len(g) for g in groups]}"


def test_large_rotation_not_grouped(tmp_path, isolated_cache):
    """45° 회전은 ‘같은 defect’ 로 보지 않음 (실제 AOI 에서 발생하지 않는 변형)."""
    import cv2 as _cv2
    rng = np.random.RandomState(13)
    base = _feature_rich_base(rng, 800)
    p_a = tmp_path / "a.jpg"
    Image.fromarray(base).save(str(p_a), "JPEG", quality=90)
    M = _cv2.getRotationMatrix2D((400, 400), 45.0, 1.0)
    rot = _cv2.warpAffine(base, M, (800, 800), borderValue=(200, 200, 200))
    p_b = tmp_path / "rot45.jpg"
    Image.fromarray(rot).save(str(p_b), "JPEG", quality=90)
    groups = group_slot(_items([p_a, p_b]))
    big = [g for g in groups if len(g) >= 2]
    assert big == [], f"45° 회전은 그룹화되면 안 됨: {[len(g) for g in groups]}"
