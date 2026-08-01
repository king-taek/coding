"""고효율 모드 GPU 전처리 경로가 **실제로 돌아가는지** — 조용히 죽던 회귀.

배경(실측 재현): ``_preprocess_parallel`` 이 지워진 인자 ``px`` 를 여전히 넘기고 있어
첫 항목에서 ``NameError: name 'px' is not defined`` 로 죽었다.  `개발자 벤치마크 제거`
커밋이 함수 시그니처에서 ``px`` 를 빼면서 두 호출부를 남긴 것이다.

왜 아무도 몰랐나: 이 경로는 **Intel GPU 가 실제로 있어야** 탄다(``device_embed`` →
``compile_model_on`` 이 성공한 뒤).  개발 PC·CI 에는 GPU 가 없어 도달하지 않고,
두 함수 모두 ``# pragma: no cover - 환경 의존`` 이라 커버리지 리포트에도 안 잡혔다.
사용자 PC 에서만 고효율 모드가 첫 사진에서 죽는다.

여기서는 GPU 없이도 그 제너레이터를 직접 돌려 계약을 고정한다:
1. 이름이 다 정의돼 있다(NameError 없음).
2. ``_make_input_array`` 를 **정확한 인자 수**로 부른다(TypeError 없음).
3. 읽을 수 없는 파일은 조용히 건너뛴다(전체가 죽지 않는다).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import inspect
from pathlib import Path

import pytest

pytest.importorskip("numpy")

from aoi_verification.app.learning import embedder_openvino as ov  # noqa: E402


def test_preprocess_parallel_runs_without_undefined_names(tmp_path):
    """제너레이터를 한 번 돌린다 — 옛 코드라면 여기서 NameError 가 난다."""
    missing = tmp_path / "없는파일.png"
    gen = ov._preprocess_parallel([missing], None, None)
    # 읽을 수 없는 파일이므로 결과는 비어야 하지만, **예외 없이** 끝나야 한다.
    assert list(gen) == []


def test_preprocess_parallel_yields_prepared_arrays(tmp_path, monkeypatch):
    """준비된 배열을 (경로, 배열) 로 흘려보낸다 — 인자 수가 맞아야 도달한다."""
    import numpy as np

    seen: list[tuple] = []

    def fake_make(path, cfg=None, side=None):
        seen.append((path, cfg, side))
        return np.zeros((3, 4, 4), dtype=np.float32)

    monkeypatch.setattr(ov, "_make_input_array", fake_make)
    paths = [tmp_path / f"{i}.png" for i in range(5)]
    out = list(ov._preprocess_parallel(paths, "CFG", "ref"))

    assert len(out) == 5, "준비된 항목이 전부 나오지 않았다"
    assert {p for p, _a in out} == set(paths)
    # 시그니처와 호출부가 어긋나면 (TypeError) 여기까지 못 온다.
    assert all(cfg == "CFG" and side == "ref" for _p, cfg, side in seen)


def test_call_sites_match_the_helper_signature():
    """호출 인자 수를 시그니처에 못 박는다 — 인자를 지울 때 호출부를 함께 고치게."""
    params = inspect.signature(ov._make_input_array).parameters
    assert list(params) == ["path", "cfg", "side"], (
        "_make_input_array 시그니처가 바뀌었다 — _preprocess_parallel 의 "
        "pool.submit 호출부도 함께 고쳐야 한다")


def test_a_failing_item_does_not_kill_the_rest(tmp_path, monkeypatch):
    """한 장이 실패해도 나머지는 계속 나온다(슬롯 전체가 죽지 않게)."""
    import numpy as np

    bad = tmp_path / "bad.png"

    def fake_make(path, cfg=None, side=None):
        if path == bad:
            raise ValueError("깨진 파일")
        return np.zeros((3, 4, 4), dtype=np.float32)

    monkeypatch.setattr(ov, "_make_input_array", fake_make)
    items = [tmp_path / "a.png", bad, tmp_path / "c.png"]
    out = list(ov._preprocess_parallel(items, None, None))
    assert {p for p, _a in out} == {tmp_path / "a.png", tmp_path / "c.png"}
