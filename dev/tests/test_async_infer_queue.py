"""비동기 추론 경로(``AsyncInferQueue``) — **실제로 돌려서** 검증한다.

이 경로가 조용히 죽은 적이 있다.  옛 코드는 ``openvino.runtime`` 에서만
``AsyncInferQueue`` 를 찾았는데 그 네임스페이스가 **openvino 2026.2 에서 삭제돼**
import 가 실패했고, 그러면 순차 추론으로 폴백한다.  결과(임베딩·정확도)는 같고
**느려지기만** 해서 아무도 눈치채지 못했다.

그래서 여기서는 '위치를 찾는지' 만 보지 않고 **작은 모델을 만들어 실제로 비동기
추론을 태운다**.  모델은 torch 없이 OpenVINO opset 으로 직접 만들어, openvino 만
있으면 어디서든 돈다.
"""

from __future__ import annotations

import numpy as np
import pytest

ov = pytest.importorskip("openvino")

from aoi_verification.app.learning import embedder_openvino as emb  # noqa: E402


def _tiny_compiled(batch: int = 1):
    """입력 평균을 내는 최소 모델 — 입력별로 값이 달라 결과 대응을 확인할 수 있다."""
    from openvino import opset13 as ops
    p = ops.parameter([batch, 3, 4, 4], np.float32, name="x")
    out = ops.reduce_mean(p, reduction_axes=[1, 2, 3], keep_dims=True)
    return ov.Core().compile_model(ov.Model([out], [p], "tiny"), "CPU")


def _x(v: float, batch: int = 1):
    return np.full((batch, 3, 4, 4), v, dtype=np.float32)


def test_async_infer_queue_is_found():
    """★ 회귀 가드 — 못 찾으면 순차로 폴백해 조용히 느려진다.

    openvino 2026.2 에서 ``openvino.runtime`` 이 사라진 것이 이 테스트의 이유다."""
    emb._async_infer_queue_cls.cache_clear()
    assert emb._async_infer_queue_cls() is not None, \
        "AsyncInferQueue 를 못 찾았다 — 비동기 추론이 통째로 죽는다"


def test_infer_raw_returns_every_input_through_the_async_path(monkeypatch):
    """비동기 큐로 넣은 입력이 **하나도 빠짐없이** 결과로 돌아오는지.

    콜백 기반이라 취합을 놓치면 그 사진이 조용히 매칭에서 빠진다.
    큐 생성이 실패해도 순차 폴백이 같은 답을 주므로, **정말 비동기로 갔는지**를
    ``start_async`` 호출 수로 함께 확인한다(안 그러면 폴백을 통과로 오인한다)."""
    started = []
    base = emb._async_infer_queue_cls()
    assert base is not None

    class _Spy(base):                                     # type: ignore[misc, valid-type]
        def start_async(self, *a, **kw):
            started.append(kw.get("userdata"))
            return super().start_async(*a, **kw)

    monkeypatch.setattr(emb, "_async_infer_queue_cls", lambda: _Spy)

    compiled = _tiny_compiled()
    inputs = [(f"img{i}", _x(i)) for i in range(8)]
    raw = emb._infer_raw(compiled, inputs, n_streams=3)

    assert started == [f"img{i}" for i in range(8)], "비동기 큐를 안 탔다(순차 폴백)"
    assert set(raw) == {f"img{i}" for i in range(8)}
    for i in range(8):
        assert float(np.asarray(raw[f"img{i}"]).ravel()[0]) == pytest.approx(i)


def test_progress_is_reported_once_per_image():
    """진행률이 per-image 로 올라가야 한다(느린 NAS 에서 멈춘 것처럼 보이지 않게)."""
    compiled = _tiny_compiled()
    seen = []
    emb._infer_raw(compiled, [(f"i{i}", _x(i)) for i in range(6)],
                   n_streams=2, progress_cb=seen.append)
    assert sum(seen) == 6, f"진행 보고 합계가 장수와 다르다: {seen}"


def test_static_batch_reports_the_real_image_count():
    """정적 배치에서는 요청 1건이 B장이다 — 진행률이 1로 세어지면 안 된다."""
    batch = 4
    compiled = _tiny_compiled(batch)
    seen = []
    raw = emb._infer_raw(compiled, [(("a", "b", "c", "d"), _x(2, batch))],
                         n_streams=1, progress_cb=seen.append)
    assert sum(seen) == batch
    assert list(raw) == [("a", "b", "c", "d")]


def test_sequential_fallback_gives_the_same_results(monkeypatch):
    """★ 폴백은 **느릴 뿐 결과가 같아야** 한다 — 정확도는 절대 안 바뀐다.

    큐를 못 찾는 상황(옛 사고)을 흉내 내 두 경로의 출력을 직접 비교한다."""
    compiled = _tiny_compiled()
    inputs = [(f"n{i}", _x(i)) for i in range(5)]
    fast = emb._infer_raw(compiled, list(inputs), n_streams=4)

    # setattr 로 갈아끼운다 — del 로 지우면 원래 함수가 복구되지 않고 사라진다.
    monkeypatch.setattr(emb, "_async_infer_queue_cls", lambda: None)
    slow = emb._infer_raw(compiled, list(inputs), n_streams=4)

    assert set(fast) == set(slow)
    for k in fast:
        assert np.allclose(np.asarray(fast[k]), np.asarray(slow[k]))


def test_resolver_warns_instead_of_failing_silently(monkeypatch, caplog):
    """둘 다 없으면 **경고를 남긴다** — 조용한 성능 저하를 다시 만들지 않기 위해."""
    import importlib
    import logging

    emb._async_infer_queue_cls.cache_clear()
    monkeypatch.setattr(importlib, "import_module",
                        lambda name: (_ for _ in ()).throw(ImportError(name)))
    with caplog.at_level(logging.WARNING, logger="aoi.openvino"):
        assert emb._async_infer_queue_cls() is None
    emb._async_infer_queue_cls.cache_clear()
    assert any("AsyncInferQueue" in r.message for r in caplog.records)


def test_removed_namespace_is_not_the_only_lookup():
    """옛 위치만 보던 코드로 되돌아가지 않게 — 소스에 최신 위치가 있어야 한다."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" /
           "learning" / "embedder_openvino.py").read_text(encoding="utf-8")
    i = src.index("def _async_infer_queue_cls(")
    body = src[i:i + 1200]
    assert '"openvino", "openvino.runtime"' in body, \
        "최신 위치(top-level openvino)를 먼저 보지 않는다"
