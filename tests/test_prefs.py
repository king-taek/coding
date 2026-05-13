"""utils.prefs — 라운드트립 / patch / 기본값."""

from aoi_verification.app.utils import prefs


def test_defaults():
    p = prefs.UiPrefs()
    assert 0.0 <= p.threshold <= 1.0
    assert p.image_long_edge_select >= 300


def test_round_trip(tmp_path, isolated_cache):
    p = prefs.UiPrefs(
        threshold=0.55,
        image_long_edge_select=900,
        image_long_edge_match=1000,
        last_ref_machine="2호기",
    )
    prefs.save(p)
    loaded = prefs.load()
    assert loaded.threshold == 0.55
    assert loaded.image_long_edge_select == 900
    assert loaded.image_long_edge_match == 1000
    assert loaded.last_ref_machine == "2호기"


def test_patch_updates_in_place(isolated_cache):
    prefs.save(prefs.UiPrefs(threshold=0.5))
    out = prefs.patch(threshold=0.8, last_val_machine="4호기")
    assert out.threshold == 0.8
    assert out.last_val_machine == "4호기"
    # 디스크에도 반영
    again = prefs.load()
    assert again.threshold == 0.8
    assert again.last_val_machine == "4호기"


def test_corrupt_file_falls_back_to_default(isolated_cache):
    file = prefs._file()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("not-json", encoding="utf-8")
    p = prefs.load()
    assert p.threshold == 0.70
