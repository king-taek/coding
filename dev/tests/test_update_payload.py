"""업데이트가 사용자 PC 로 **무엇을 보내는가** — 배포 페이로드 계약.

`_stage_tree` 는 저장소 최상위에서 `_UPDATE_SKIP_TOP` 을 빼고 전부 담되, `docs`·`scripts`
두 폴더만은 `_UPDATE_KEEP_ONLY` 로 **남길 것만** 추린다.  거르는 방향이 반대인 자리라
헷갈리기 쉽고, 틀리면 두 방향 모두 조용히 아프다:

  · 너무 많이 보내면 — 개발 기록·빌드 도구가 사용자 PC 로 나간다(예전 상태).
  · 너무 적게 보내면 — 사용자 설명서가 영영 안 간다(빌드는 docs/ 를 담지 않으므로
    자동 업데이트가 매뉴얼의 **유일한 전달 경로**다).

무거운 의존성 없이 도는 순수 파일 조작이라 헤드리스로 검증한다.
"""

from __future__ import annotations

from pathlib import Path

from aoi_verification.app.utils import updater

_REPO = Path(__file__).resolve().parents[2]
_FONT_REL = "aoi_verification/app/ui/assets/font"
_FONT_DIR = _REPO / _FONT_REL


def _fake_repo(tmp_path: Path) -> Path:
    """업데이트 zip 을 푼 모습 — 저장소 최상위를 흉내 낸 트리."""
    root = tmp_path / "coding-branch"
    for rel in (
        "main.py", "requirements.txt", "README.md", "CLAUDE.md",
        ".gitignore", ".coverage", "pytest.ini",
        "aoi_verification/app/ui/theme.py",
        "docs/사용설명서.pdf", "docs/상세설명서.pdf", "docs/체험하기.html",
        "docs/규칙_배경.md", "docs/좌표변환_분석_보고서.pdf",
        "docs/KLA 좌표 예시(TB500, T254)/2026-08-02/사진목록.txt",
        "scripts/run_aoi.bat", "scripts/run_aoi_debug.bat", "scripts/update_app.bat",
        "scripts/build.py", "scripts/internal/portable_build.py",
        "dev/양식.xlsx", "dev/tests/test_x.py",
    ):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    # ★ 글꼴은 **저장소에 있는 것 전부**를 그대로 놓는다.  픽스처가 일부만 만들면
    #   `_UPDATE_SKIP_PATHS` 의 나머지 항목이 한 번도 안 태워져, 그 항목을 지워도
    #   스위트가 초록으로 남는다(실제로 6개 중 2개만 검증되고 있었다).
    for src in sorted(_FONT_DIR.iterdir()):
        dst = root / _FONT_REL / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"font")
    return root


def _stage(tmp_path: Path) -> Path:
    staging = tmp_path / "staging"
    updater._stage_tree(_fake_repo(tmp_path), staging, lambda *a: None)
    return staging


def test_keeps_only_the_user_manual_from_docs(tmp_path):
    """★ 개발 기록은 안 보내고, 사용자 설명서 3개는 **반드시** 보낸다."""
    staging = _stage(tmp_path)

    assert sorted(p.name for p in (staging / "docs").iterdir()) == [
        "사용설명서.pdf", "상세설명서.pdf", "체험하기.html"]
    assert not (staging / "docs" / "규칙_배경.md").exists()
    assert not (staging / "docs" / "좌표변환_분석_보고서.pdf").exists()
    assert not (staging / "docs" / "KLA 좌표 예시(TB500, T254)").exists()


def test_keeps_only_the_user_bats_from_scripts(tmp_path):
    """빌드 도구는 안 보내고, 사용자가 더블클릭하는 bat 3개만 보낸다."""
    staging = _stage(tmp_path)

    assert sorted(p.name for p in (staging / "scripts").iterdir()) == [
        "run_aoi.bat", "run_aoi_debug.bat", "update_app.bat"]
    assert not (staging / "scripts" / "build.py").exists()
    assert not (staging / "scripts" / "internal").exists()


def test_repo_junk_is_not_shipped(tmp_path):
    """개발 잔재는 최상위에서 걸러진다."""
    staging = _stage(tmp_path)

    for gone in (".coverage", ".gitignore", "CLAUDE.md", "README.md",
                 "pytest.ini", "dev"):
        assert not (staging / gone).exists(), gone


def test_the_app_itself_still_ships_whole(tmp_path):
    """★ 앱 본체는 예전처럼 통째로 간다 — 추림은 docs·scripts 에만 적용된다."""
    staging = _stage(tmp_path)

    assert (staging / "aoi_verification" / "app" / "ui" / "theme.py").is_file()
    assert (staging / "main.py").is_file()
    assert (staging / "requirements.txt").is_file()
    assert (staging / "양식.xlsx").is_file()      # dev/ 에서 앱 루트로 옮겨진다


def test_keep_list_names_exist_in_the_repo():
    """★ 남기기로 한 이름이 **실재하는지** 대조한다.

    이 목록은 이름이 틀려도 조용히 지나간다(상류 이름 변경에도 업데이트가 계속 돌아야
    하므로 일부러 그렇게 뒀다).  그래서 오타·개명을 잡아 줄 자리가 여기뿐이다 —
    틀리면 사용자에게 설명서가 안 간다."""
    for top, names in updater._UPDATE_KEEP_ONLY.items():
        for name in names:
            assert (_REPO / top / name).exists(), f"{top}/{name} 이 저장소에 없다"


def test_only_registered_fonts_are_shipped(tmp_path):
    """★ 등록하지 않는 글꼴은 배포되지 않는다(2.8 MB 를 사용자에게 보내던 자리).

    저장소에는 남겨 두고 배포에서만 뺀다 — 트리 깊은 곳이라 `_UPDATE_SKIP_PATHS` 가
    `copytree(ignore=…)` 로 거른다."""
    staging = _stage(tmp_path)

    shipped = sorted(p.name for p in (staging / _FONT_REL).iterdir())
    assert shipped == ["NanumSquareB.ttf", "NanumSquareR.ttf"]


def test_every_unregistered_font_is_excluded():
    """★ **양방향** 불변식 — 등록하지 않는 글꼴은 하나도 빠짐없이 제외 목록에 있다.

    아래 `test_skip_paths_…` 는 *목록에 남아 있는* 항목만 훑으므로 **목록에서 지워진**
    항목은 원리적으로 못 잡는다.  실제로 6개 중 4개를 지워도 스위트가 초록이었고,
    그러면 1.7 MB 가 조용히 다시 모든 사용자에게 실려 나간다.  이 단언은 두 방향을
    한 번에 못 박는다 — 제외 항목을 지워도, 미등록 글꼴을 새로 넣기만 해도 깨진다."""
    from aoi_verification.app.ui import theme

    unregistered = {p.name for p in _FONT_DIR.iterdir()} - set(theme._FONT_FILES)
    excluded = {Path(rel).name for rel in updater._UPDATE_SKIP_PATHS
                if Path(rel).parent.as_posix() == _FONT_REL}
    assert excluded == unregistered


def test_skip_paths_match_the_repo_and_never_hide_a_registered_font():
    """★ 제외 목록이 실재하는 파일을 가리키고, **등록 대상은 절대 안 뺀다.**

    등록하는 글꼴을 실수로 제외 목록에 넣으면 사용자 PC 에서 글꼴이 사라져 화면이
    PC 마다 달라진다 — 조용히 나빠지는 종류라 여기서 막는다."""
    from aoi_verification.app.ui import theme

    registered = set(theme._FONT_FILES)
    for rel in updater._UPDATE_SKIP_PATHS:
        assert (_REPO / rel).exists(), f"{rel} 이 저장소에 없다(오타이거나 이미 지워졌다)"
        assert Path(rel).name not in registered, f"{rel} 은 등록 대상이라 빼면 안 된다"
