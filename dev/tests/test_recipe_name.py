"""recipe 이름 조회 — RecipesInfo.ini 가 없는 단일 recipe 폴더의 폴백."""

from __future__ import annotations

from aoi_verification.app.coords import recipe_name


def test_single_recipe_folder_uses_recipe_ini_name(tmp_path):
    # 실측(P6GW68-03B4): RecipesInfo.ini 없음, Recipe.ini [General] Recipe Name=2D
    (tmp_path / "Recipe.ini").write_text(
        "[Scan]\nrecipe_1=0,2D,,,,,\n[General]\nRecipe Name=2D\nLastCam=3\n",
        encoding="utf-8")
    recipe_name.recipe_map.cache_clear()
    assert recipe_name.name_for(tmp_path, 0) == "2D"
    assert recipe_name.name_for(tmp_path, 1) is None


def test_recipes_info_wins_over_recipe_ini(tmp_path):
    (tmp_path / "RecipesInfo.ini").write_text(
        "[Recipe-1]\nName=PI_Bubble\n", encoding="utf-8")
    (tmp_path / "Recipe.ini").write_text("[General]\nRecipe Name=2D\n",
                                         encoding="utf-8")
    recipe_name.recipe_map.cache_clear()
    assert recipe_name.name_for(tmp_path, 1) == "PI_Bubble"
    assert recipe_name.name_for(tmp_path, 0) is None


def test_no_files_gives_none(tmp_path):
    recipe_name.recipe_map.cache_clear()
    assert recipe_name.name_for(tmp_path, 0) is None
