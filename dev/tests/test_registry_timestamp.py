"""registry — timestamp 모델 이름, latest.txt, rename guard."""

from datetime import datetime

import pytest

from aoi_verification.app.learning import registry


def test_is_timestamp_name():
    assert registry.is_timestamp_name("model_2026-05-15_142311") is True
    assert registry.is_timestamp_name("model_2026-05-15_142311_2") is True
    # 옛 날짜 형식은 timestamp 가 아님.
    assert registry.is_timestamp_name("2026-05-15") is False
    assert registry.is_timestamp_name("2026-05-15_HitAt5_87") is False
    assert registry.is_timestamp_name("basic") is False


def test_make_new_name_uses_timestamp(isolated_cache):
    name = registry.make_new_name(datetime(2026, 5, 15, 14, 23, 11))
    assert name == "model_2026-05-15_142311"


def test_make_new_name_disambiguates_same_second(isolated_cache):
    # 첫 호출 후 가짜 모델을 디스크에 만들어 같은 timestamp 가 존재하는 상황 시뮬레이션.
    name1 = registry.make_new_name(datetime(2026, 5, 15, 14, 23, 11))
    info1 = registry._build_files(name1)
    info1.weights_path.parent.mkdir(parents=True, exist_ok=True)
    info1.weights_path.write_bytes(b"x")
    name2 = registry.make_new_name(datetime(2026, 5, 15, 14, 23, 11))
    assert name2 == f"{name1}_2"


def test_latest_get_set_roundtrip(isolated_cache):
    assert registry.get_latest() is None
    # 모델 파일이 없으면 latest 도 None 으로 fallback.
    registry.set_latest("model_2026-05-15_142311")
    assert registry.get_latest() is None
    # 모델 파일 만들어두면 정상 반환.
    info = registry._build_files("model_2026-05-15_142311")
    info.weights_path.parent.mkdir(parents=True, exist_ok=True)
    info.weights_path.write_bytes(b"x")
    assert registry.get_latest() == "model_2026-05-15_142311"


def test_apply_latest_if_active_unset(isolated_cache):
    # active 없음 → latest 로 fallback
    info = registry._build_files("model_2026-05-15_142311")
    info.weights_path.parent.mkdir(parents=True, exist_ok=True)
    info.weights_path.write_bytes(b"x")
    registry.set_latest("model_2026-05-15_142311")
    assert registry.get_active() == registry.BASIC
    registry.apply_latest_if_active_unset()
    assert registry.get_active() == "model_2026-05-15_142311"


def test_rename_with_accuracy_skips_timestamp_models(isolated_cache):
    """신규 timestamp 이름은 Hit@5 자동 리네임 대상에서 제외."""
    info = registry._build_files("model_2026-05-15_142311")
    info.weights_path.parent.mkdir(parents=True, exist_ok=True)
    info.weights_path.write_bytes(b"x")

    out = registry.rename_with_accuracy(info, 87)
    # 이름이 변경되지 않아야 함.
    assert out.name == "model_2026-05-15_142311"
    assert out.weights_path.exists()


def test_rename_with_accuracy_renames_old_date_model(isolated_cache):
    """옛 날짜 모델은 기존 동작대로 _HitAt5_n 으로 리네임."""
    info = registry._build_files("2026-05-15")
    info.weights_path.parent.mkdir(parents=True, exist_ok=True)
    info.weights_path.write_bytes(b"x")
    info.meta_path.write_text('{"name": "2026-05-15"}', encoding="utf-8")

    out = registry.rename_with_accuracy(info, 87)
    assert out.name == "2026-05-15_HitAt5_87"
    assert out.weights_path.exists()
    assert not info.weights_path.exists()
