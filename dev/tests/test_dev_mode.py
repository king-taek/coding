"""개발자 모드(Ctrl+Shift+D) — 업데이트 저장소·브랜치 고르기 (네트워크는 모킹)."""

from __future__ import annotations

import json

import pytest

from aoi_verification.app.utils import prefs, updater

_STUB_DEFAULT = "default-branch"


@pytest.fixture(autouse=True)
def _stub_default_branch(monkeypatch):
    updater._default_branch_cache.clear()
    monkeypatch.setattr(updater, "_default_branch",
                        lambda repo, timeout=10.0: _STUB_DEFAULT)


def _version(tmp_path, monkeypatch, data):
    monkeypatch.setattr(updater, "_app_root", lambda: tmp_path)
    if data is not None:
        (tmp_path / "VERSION").write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# dev_target — 켜져 있고 브랜치가 적혀 있을 때만 유효
# ---------------------------------------------------------------------------
def test_dev_target_none_by_default():
    assert updater.dev_target() is None


def test_dev_target_ignored_while_dev_mode_off():
    prefs.patch(dev_mode=False, dev_update_branch="claude/feat")
    assert updater.dev_target() is None


def test_dev_target_needs_a_branch():
    prefs.patch(dev_mode=True, dev_update_branch="  ")
    assert updater.dev_target() is None


def test_dev_target_defaults_repo():
    prefs.patch(dev_mode=True, dev_update_branch="claude/feat")
    assert updater.dev_target() == (updater.DEFAULT_REPO, "claude/feat")
    prefs.patch(dev_update_repo="me/fork")
    assert updater.dev_target() == ("me/fork", "claude/feat")


# ---------------------------------------------------------------------------
# 자동 업데이트가 고른 브랜치를 따른다 — claude/ 도 정규화하지 않는다
# ---------------------------------------------------------------------------
def test_check_follows_dev_branch_without_normalizing(tmp_path, monkeypatch):
    _version(tmp_path, monkeypatch,
             {"sha": "OLD", "branch": _STUB_DEFAULT, "repo": "o/r"})
    prefs.patch(dev_mode=True, dev_update_repo="me/fork",
                dev_update_branch="claude/feat")
    seen = []

    def _latest(repo, branch):
        seen.append((repo, branch))
        return {"sha": "NEW", "message": "m"}

    monkeypatch.setattr(updater, "latest_commit", _latest)
    info = updater.check_for_update()
    assert info["repo"] == "me/fork" and info["branch"] == "claude/feat"
    assert seen == [("me/fork", "claude/feat")]


def test_dev_branch_missing_does_not_heal_to_default(tmp_path, monkeypatch):
    """고른 브랜치가 없으면 조용히 기본 브랜치를 받지 않는다."""
    _version(tmp_path, monkeypatch, {"sha": "OLD", "branch": "x", "repo": "o/r"})
    prefs.patch(dev_mode=True, dev_update_branch="gone")
    monkeypatch.setattr(updater, "latest_commit",
                        lambda r, b: {"sha": "NEW"} if b == _STUB_DEFAULT else None)
    assert updater.check_for_update() is None
    assert updater.manual_check()[0] == "unknown"


def test_dev_install_returns_to_default_after_dev_mode_off(tmp_path, monkeypatch):
    """개발자 모드로 받은 설치본(VERSION dev)은 모드를 끄면 기본 저장소·브랜치로 돌아간다
    — 다른 저장소·일반 이름 브랜치여도(정규화 규칙에 안 걸려도)."""
    _version(tmp_path, monkeypatch,
             {"sha": "DEV", "branch": "feature-x", "repo": "me/fork", "dev": True})
    monkeypatch.setattr(updater, "latest_commit", lambda r, b: {"sha": "MAIN"})
    status, info = updater.manual_check()
    assert status == "update"
    assert info["repo"] == updater.DEFAULT_REPO and info["branch"] == _STUB_DEFAULT


def test_non_dev_explicit_branch_still_respected(tmp_path, monkeypatch):
    """dev 표식이 없는 설치본의 명시적 브랜치 존중(기존 규칙)은 그대로다."""
    _version(tmp_path, monkeypatch, {"sha": "OLD", "branch": "release", "repo": "o/r"})
    monkeypatch.setattr(updater, "latest_commit", lambda r, b: {"sha": "NEW"})
    assert updater.manual_check()[1]["branch"] == "release"


# ---------------------------------------------------------------------------
# check_target — 최신이어도 받을 대상을 돌려준다(강제 설치용)
# ---------------------------------------------------------------------------
def test_check_target_latest_still_carries_install_info(tmp_path, monkeypatch):
    _version(tmp_path, monkeypatch, {"sha": "SAME", "branch": "b", "repo": "o/r"})
    monkeypatch.setattr(updater, "latest_commit",
                        lambda r, b: {"sha": "SAME", "message": "m"})
    status, info = updater.check_target("o/r", "b")
    assert status == "latest"
    assert info == {"repo": "o/r", "branch": "b", "sha": "SAME", "message": "m"}


def test_check_target_update_and_empty_branch_means_default(tmp_path, monkeypatch):
    _version(tmp_path, monkeypatch, {"sha": "OLD", "branch": "b", "repo": "o/r"})
    monkeypatch.setattr(updater, "latest_commit", lambda r, b: {"sha": "NEW"})
    status, info = updater.check_target("o/r", "")
    assert status == "update" and info["branch"] == _STUB_DEFAULT


def test_check_target_unknown_on_failure(tmp_path, monkeypatch):
    _version(tmp_path, monkeypatch, {"sha": "OLD", "branch": "b", "repo": "o/r"})
    monkeypatch.setattr(updater, "latest_commit", lambda r, b: None)
    status, info = updater.check_target("o/r", "nope")
    assert status == "unknown" and info["error"]


def test_list_branches_puts_default_first(monkeypatch):
    body = json.dumps([{"name": "a"}, {"name": _STUB_DEFAULT}, {"name": "claude/x"}])
    monkeypatch.setattr(updater, "_http_get",
                        lambda url, h, t: body.encode("utf-8"))
    assert updater.list_branches("o/r") == [_STUB_DEFAULT, "a", "claude/x"]


def test_list_branches_empty_on_failure(monkeypatch):
    def boom(url, h, t):
        raise OSError("blocked")
    monkeypatch.setattr(updater, "_http_get", boom)
    assert updater.list_branches("o/r") == []
    assert updater.last_error()


def test_write_version_marks_dev(tmp_path):
    updater._write_version_to(tmp_path, "S", "b", "o/r", dev=True)
    assert json.loads((tmp_path / "VERSION").read_text())["dev"] is True
    updater._write_version_to(tmp_path, "S", "b", "o/r")
    assert "dev" not in json.loads((tmp_path / "VERSION").read_text())


# ---------------------------------------------------------------------------
# UI — 단축키 토글 · 설정 창
# ---------------------------------------------------------------------------
def test_dialog_saves_choice_and_returns_install_info(styled_qapp, tmp_path, monkeypatch):
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets.dev_mode_dialog import DevModeDialog

    _version(tmp_path, monkeypatch, {"sha": "SAME", "branch": "b", "repo": "o/r"})
    dlg = DevModeDialog()
    dlg.repo_edit.setText("me/fork")
    dlg.branch_combo.setEditText("claude/feat")
    # 백그라운드 스레드 대신 결과 슬롯을 직접 — 최신이어도 설치 대상이 잡힌다.
    dlg._on_checked("latest", {"repo": "me/fork", "branch": "claude/feat",
                               "sha": "SAME", "message": ""}, True)
    assert dlg.install_info["branch"] == "claude/feat"
    p = prefs.load()
    assert (p.dev_update_repo, p.dev_update_branch) == ("me/fork", "claude/feat")


def test_dialog_check_only_does_not_install(styled_qapp, tmp_path, monkeypatch):
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets.dev_mode_dialog import DevModeDialog

    _version(tmp_path, monkeypatch, None)
    dlg = DevModeDialog()
    dlg._on_checked("update", {"repo": "o/r", "branch": "b", "sha": "NEW"}, False)
    assert dlg.install_info is None
    assert "NEW" in dlg.status.text()
    dlg._reset()
    dlg.reject()
    assert prefs.load().dev_update_branch == ""


def test_main_window_shortcut_toggles_dev_mode(styled_qapp, monkeypatch):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtGui import QKeySequence, QShortcut
    from aoi_verification.app.ui import main_window as mw

    opened = []
    monkeypatch.setattr(mw.MainWindow, "_open_dev_mode", lambda self: opened.append(1))
    win = mw.MainWindow()
    try:
        keys = [s.key().toString() for s in win.findChildren(QShortcut)]
        assert QKeySequence("Ctrl+Shift+D").toString() in keys
        assert not win._dev_btn.isVisibleTo(win)
        win._toggle_dev_mode()
        assert prefs.load().dev_mode and opened == [1]
        assert win._dev_btn.isVisibleTo(win)
        win._toggle_dev_mode()
        assert not prefs.load().dev_mode and opened == [1]
        assert not win._dev_btn.isVisibleTo(win)
    finally:
        win.close()


def test_dialog_ignores_results_after_close(styled_qapp, tmp_path, monkeypatch):
    """닫힌 창에 늦게 온 확인 결과가 다른 창에서 고른 값을 덮어쓰면 안 된다."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets.dev_mode_dialog import DevModeDialog

    _version(tmp_path, monkeypatch, None)
    old = DevModeDialog()
    old.branch_combo.setEditText("A")
    old.reject()
    prefs.patch(dev_update_branch="B")          # 그 사이 새 창에서 B 를 골랐다
    old._on_checked("update", {"repo": "o/r", "branch": "A", "sha": "S"}, True)
    assert old.install_info is None
    assert prefs.load().dev_update_branch == "B"


# ---------------------------------------------------------------------------
# 개발자 모드 설치는 배포분 + dev/ 를 받는다(나머지 개발 파일은 일반 업데이트처럼 뺀다)
# ---------------------------------------------------------------------------
_FULL_REPO = {
    "aoi_verification/app/ui/main_window.py": b"class MainWindow: pass\n",
    "aoi_verification/app/ui/style.qss": b"QWidget {}\n",
    "aoi_verification/app/utils/updater.py": b"DEFAULT_BRANCH = 'b'\n",
    "main.py": b"print('hi')\n",
    "requirements.txt": b"numpy==1\n",
    "dev/양식.xlsx": b"TEMPLATE",
    "dev/tests/test_x.py": b"def test(): pass\n",
    "docs/사용설명서.pdf": b"manual",
    "scripts/run_aoi.bat": b"@echo\n",
}
# 개발자 모드에서도 받지 않는 것 — dev/ 밖의 개발 파일(일반 업데이트와 같은 거르기).
_NOT_FETCHED = {
    "docs/개발기록.md": b"doc\n",
    "scripts/internal/build.bat": b"rem\n",
    "pytest.ini": b"[pytest]\n",
    "CLAUDE.md": b"rules\n",
    "aoi_verification/app/ui/assets/font/NanumSquareB.otf": b"font",
    ".claude/skills/x/run.sh": b"echo\n",           # 회사 보안 사고 원인
}


def test_dev_install_fetches_release_files_plus_dev(tmp_path, monkeypatch):
    import urllib.parse

    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr(updater, "_app_root", lambda: app)
    monkeypatch.setattr(updater, "_install_root", lambda: None)   # 포터블(제자리 적용)
    tree = {"truncated": False,
            "tree": [{"path": p, "type": "blob"} for p in {**_FULL_REPO, **_NOT_FETCHED}]
            + [{"path": "dev", "type": "tree"}]}
    asked = []

    def fake_get(url, headers, timeout):
        asked.append(url)
        if "/git/trees/SHA1" in url:
            return json.dumps(tree).encode("utf-8")
        prefix = "https://raw.githubusercontent.com/me/fork/SHA1/"
        assert url.startswith(prefix), url
        return _FULL_REPO[urllib.parse.unquote(url[len(prefix):])]

    monkeypatch.setattr(updater, "_http_get", fake_get)
    monkeypatch.setattr(updater, "_urlopen",
                        lambda *a, **k: pytest.fail("배포용 zip 을 받으면 안 된다"))

    assert updater.download_and_apply("me/fork", "claude/feat", "SHA1", dev=True)
    for rel in _FULL_REPO:
        assert (app / rel).read_bytes() == _FULL_REPO[rel], rel
    for rel in _NOT_FETCHED:
        assert not (app / rel).exists(), rel
        assert not any(u.endswith(urllib.parse.quote(rel, safe="/")) for u in asked), rel
    assert (app / "양식.xlsx").read_bytes() == b"TEMPLATE"     # 앱 루트 양식도 그대로
    ver = json.loads((app / "VERSION").read_text())
    assert ver == {"sha": "SHA1", "branch": "claude/feat", "repo": "me/fork", "dev": True}


def test_dev_install_refuses_truncated_tree(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr(updater, "_app_root", lambda: app)
    monkeypatch.setattr(updater, "_install_root", lambda: None)
    monkeypatch.setattr(updater, "_http_get",
                        lambda u, h, t: json.dumps({"truncated": True, "tree": []}).encode())
    assert not updater.download_and_apply("o/r", "b", "S", dev=True)
    assert not (app / "VERSION").exists()


def test_dev_install_fails_whole_when_one_file_fails(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr(updater, "_app_root", lambda: app)
    monkeypatch.setattr(updater, "_install_root", lambda: None)
    tree = {"tree": [{"path": p, "type": "blob"} for p in _FULL_REPO]}

    def fake_get(url, headers, timeout):
        if "/git/trees/" in url:
            return json.dumps(tree).encode("utf-8")
        if url.endswith("main.py"):
            raise OSError("reset")
        return b"x"

    monkeypatch.setattr(updater, "_http_get", fake_get)
    assert not updater.download_and_apply("o/r", "b", "S", dev=True)
    assert list(app.iterdir()) == []                  # 반쪽 트리를 적용하지 않는다


def test_dev_wanted_is_release_set_plus_dev():
    """일반 업데이트가 내보내는 것 + dev/ — 그 밖의 개발 파일은 여전히 뺀다."""
    w = updater._dev_wanted
    assert w("dev/tests/test_x.py") and w("dev/양식.xlsx")
    assert w("main.py") and w("aoi_verification/app/ui/main_window.py")
    assert w("docs/사용설명서.pdf") and w("scripts/run_aoi.bat")
    assert not w(".claude/skills/x/run.sh")
    assert not w("docs/개발기록.md") and not w("scripts/internal/build.bat")
    assert not w("CLAUDE.md") and not w("pytest.ini")
    assert not w("aoi_verification/app/ui/assets/font/NanumSquareB.otf")


def test_dialog_log_button_opens_the_cache_folder(styled_qapp, monkeypatch):
    """[로그 폴더 열기] 는 app.log 가 있는 캐시 루트를 탐색기로 연다(사용자 요청)."""
    from PyQt6.QtGui import QDesktopServices
    from aoi_verification.app.ui.widgets.dev_mode_dialog import DevModeDialog
    from aoi_verification.app.utils import paths

    opened: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl",
                        staticmethod(lambda url: opened.append(url.toLocalFile())))
    dlg = DevModeDialog()
    try:
        assert "app.log" in dlg.btn_log.toolTip()
        dlg.btn_log.click()
        assert opened == [str(paths.cache_root())]
    finally:
        dlg.deleteLater()
