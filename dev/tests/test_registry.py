"""learning.registry — 모델 목록 / active.txt / 이름 생성 / 리네임."""

from datetime import datetime

from aoi_verification.app.learning import registry as R


def _touch_model(name: str, meta: dict | None = None) -> None:
    """모델 파일을 모의 생성 (가중치 + 메타) — 가중치는 비어 있어도 된다."""
    info = R._build_files(name)
    info.weights_path.parent.mkdir(parents=True, exist_ok=True)
    info.weights_path.write_bytes(b"PT_FAKE")
    if meta:
        info.meta_path.write_text(
            __import__("json").dumps(meta, ensure_ascii=False),
            encoding="utf-8",
        )


def test_make_new_name_uses_timestamp(isolated_cache):
    """신규 timestamp 형식 (스펙 §8.2-c): model_YYYY-MM-DD_HHMMSS."""
    today = datetime(2026, 5, 13, 10, 0, 0)
    a = R.make_new_name(today)
    assert a == "model_2026-05-13_100000"
    _touch_model(a)
    # 같은 초에 또 학습 → _2.
    b = R.make_new_name(today)
    assert b == "model_2026-05-13_100000_2"
    _touch_model(b)
    assert R.make_new_name(today) == "model_2026-05-13_100000_3"


def test_active_basic_when_no_models(isolated_cache):
    assert R.get_active() == R.BASIC


def test_set_active_to_unknown_falls_back(isolated_cache):
    R.set_active("doesnotexist")
    assert R.get_active() == R.BASIC


def test_set_active_to_known(isolated_cache):
    _touch_model("2026-05-13")
    R.set_active("2026-05-13")
    assert R.get_active() == "2026-05-13"


def test_rename_with_accuracy_moves_files(isolated_cache):
    _touch_model("2026-05-13", meta={"name": "2026-05-13"})
    info = R.find("2026-05-13")
    new_info = R.rename_with_accuracy(info, 78)
    assert new_info.name == "2026-05-13_HitAt5_78"
    assert new_info.weights_path.exists()
    # 기존 이름은 없어야 함
    assert R.find("2026-05-13") is None


def test_rename_preserves_active_pointer(isolated_cache):
    _touch_model("2026-05-13")
    R.set_active("2026-05-13")
    info = R.find("2026-05-13")
    new_info = R.rename_with_accuracy(info, 72)
    assert R.get_active() == new_info.name


def test_rename_rolls_back_on_partial_failure(isolated_cache, monkeypatch):
    """meta rename 이 OSError 면 이미 옮긴 weights 도 원위치 — 분열 상태 방지."""
    _touch_model("2026-05-13", meta={"name": "2026-05-13"})
    # eval 로그도 만들어서 3개 파일 모두 옮길 거리가 있도록.
    info = R.find("2026-05-13")
    info.eval_path.parent.mkdir(parents=True, exist_ok=True)
    info.eval_path.write_text('{"ts":"x"}\n', encoding="utf-8")
    info = R.find("2026-05-13")

    real_rename = type(info.weights_path).rename
    calls = {"n": 0}

    def _flaky_rename(self, target):
        calls["n"] += 1
        # 첫 호출(weights) 은 성공, 두 번째(meta) 는 실패시킨다.
        if calls["n"] == 2:
            raise OSError("disk full")
        return real_rename(self, target)

    monkeypatch.setattr(type(info.weights_path), "rename", _flaky_rename)

    result = R.rename_with_accuracy(info, 80)
    # 실패 시 원래 info 그대로 반환 (이름 미변경)
    assert result.name == "2026-05-13"
    # 디스크에도 원래 이름이 남아 있어야 한다 (rollback 성공).
    assert info.weights_path.exists()
    assert info.meta_path.exists()
    # 새 이름 파일은 없어야 한다.
    target = R._build_files("2026-05-13_HitAt5_80")
    assert not target.weights_path.exists()
    assert not target.meta_path.exists()


def test_export_import_round_trip(isolated_cache, tmp_path):
    _touch_model("2026-05-13", meta={"name": "2026-05-13", "hit_at_5": 0.7})
    zip_path = tmp_path / "out.zip"
    R.export_model("2026-05-13", zip_path)
    assert zip_path.exists()
    # 같은 이름이 이미 있으므로 import 는 _2 로 떨어져야 함
    name2 = R.import_model(zip_path)
    assert name2 == "2026-05-13_2"
    info = R.find(name2)
    assert info is not None
    assert info.weights_path.exists()
    assert info.meta.get("name") == name2
