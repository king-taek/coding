"""레시피 하나뿐인 폴더의 레시피 이름 — RecipesInfo.ini 가 없어도 Recipe.ini · SetupPath 에서 읽는다."""

from __future__ import annotations

import pytest

from aoi_verification.app.threshold import rules as R


@pytest.fixture(autouse=True)
def _clear():
    R.read_recipe_names.cache_clear()
    yield
    R.read_recipe_names.cache_clear()


def test_recipe_ini_only(tmp_path):
    (tmp_path / "Recipe.ini").write_text("[General]\nRecipe Name = PI_Bubble \n", encoding="utf-8")
    assert R.read_recipe_names(tmp_path) == {1: "PI_Bubble"}


def test_setup_path_only_last_segment(tmp_path):
    (tmp_path / "Params_WaferInfo.ini").write_text(
        "SetupPath=c:\\job\\JobA\\Setup1\\Recipes\\PI_Bubble\\\n", encoding="utf-8")
    assert R.read_recipe_names(tmp_path) == {1: "PI_Bubble"}


def test_recipes_info_wins(tmp_path):
    (tmp_path / "RecipesInfo.ini").write_text("[Recipe-1]\nName=BS_1\n[Recipe-2]\nName=BS_2\n", encoding="utf-8")
    (tmp_path / "Recipe.ini").write_text("[General]\nRecipe Name=Other\n", encoding="utf-8")
    assert R.read_recipe_names(tmp_path) == {1: "BS_1", 2: "BS_2"}


def test_nothing_is_empty(tmp_path):
    assert R.read_recipe_names(tmp_path) == {}


def test_identity_unchanged_by_fallback(tmp_path):
    (tmp_path / "Recipe.ini").write_text("[General]\nRecipe Name=PI_Bubble\n", encoding="utf-8")
    assert R.recipe_identity(tmp_path)[1] == ()


def test_zone_label_shows_name_for_single_recipe(tmp_path):
    from aoi_verification.app.ui.widgets.threshold_preview_dialog import zone_label
    (tmp_path / "Recipe.ini").write_text("[General]\nRecipe Name=PI_Bubble\n", encoding="utf-8")
    names = R.read_recipe_names(tmp_path)
    assert names.get(1) == "PI_Bubble"
    assert zone_label("Zones", None, 1, names, True)[1] == "PI_Bubble"
