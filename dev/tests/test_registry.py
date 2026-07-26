"""learning.registry — 모델 조회 / active.txt 포인터."""

from aoi_verification.app.learning import registry as R


def _touch_model(name: str) -> None:
    """모델 가중치 파일을 모의 생성 — 내용은 비어 있어도 된다."""
    info = R.find(name) or R.ModelInfo(
        name=name, weights_path=R.paths.models_dir() / f"{name}{R._WEIGHTS_EXT}")
    info.weights_path.parent.mkdir(parents=True, exist_ok=True)
    info.weights_path.write_bytes(b"PT_FAKE")


def test_active_basic_when_no_models(isolated_cache):
    assert R.get_active() == R.BASIC


def test_find_returns_none_for_unknown(isolated_cache):
    assert R.find("doesnotexist") is None


def test_set_active_to_unknown_falls_back(isolated_cache):
    R.set_active("doesnotexist")
    assert R.get_active() == R.BASIC


def test_set_active_to_known(isolated_cache):
    _touch_model("2026-05-13")
    R.set_active("2026-05-13")
    assert R.get_active() == "2026-05-13"


def test_active_falls_back_when_model_file_disappears(isolated_cache):
    _touch_model("2026-05-13")
    R.set_active("2026-05-13")
    R.find("2026-05-13").weights_path.unlink()
    assert R.get_active() == R.BASIC
