"""자동 업데이트 버전 비교 로직 검증 (네트워크는 모킹)."""

from __future__ import annotations

import json

from aoi_verification.app.utils import updater


def _write_version(tmp_path, monkeypatch, data: dict | None):
    monkeypatch.setattr(updater, "_app_root", lambda: tmp_path)
    if data is not None:
        (tmp_path / "VERSION").write_text(json.dumps(data), encoding="utf-8")


def test_current_version_reads_json(tmp_path, monkeypatch):
    _write_version(tmp_path, monkeypatch,
                   {"sha": "abc123", "branch": "main", "repo": "o/r"})
    cur = updater.current_version()
    assert cur and cur["sha"] == "abc123" and cur["branch"] == "main"


def test_current_version_none_when_missing(tmp_path, monkeypatch):
    _write_version(tmp_path, monkeypatch, None)
    assert updater.current_version() is None


def test_check_detects_new_commit(tmp_path, monkeypatch):
    _write_version(tmp_path, monkeypatch,
                   {"sha": "OLD", "branch": "feat", "repo": "o/r"})
    monkeypatch.setattr(updater, "latest_commit",
                        lambda repo, branch: {"sha": "NEW", "message": "fix"})
    info = updater.check_for_update()
    assert info and info["sha"] == "NEW" and info["branch"] == "feat"
    assert info["repo"] == "o/r"


def test_check_none_when_up_to_date(tmp_path, monkeypatch):
    _write_version(tmp_path, monkeypatch,
                   {"sha": "SAME", "branch": "feat", "repo": "o/r"})
    monkeypatch.setattr(updater, "latest_commit",
                        lambda repo, branch: {"sha": "SAME"})
    assert updater.check_for_update() is None


def test_check_none_in_dev_mode(tmp_path, monkeypatch):
    # VERSION 없음(개발 모드) → 네트워크도 안 건드리고 None.
    _write_version(tmp_path, monkeypatch, None)
    called = {"n": 0}

    def _boom(repo, branch):
        called["n"] += 1
        raise AssertionError("개발 모드에선 원격 조회를 하면 안 됨")

    monkeypatch.setattr(updater, "latest_commit", _boom)
    assert updater.check_for_update() is None
    assert called["n"] == 0


def test_check_none_when_network_fails(tmp_path, monkeypatch):
    _write_version(tmp_path, monkeypatch,
                   {"sha": "OLD", "branch": "feat", "repo": "o/r"})
    monkeypatch.setattr(updater, "latest_commit", lambda repo, branch: None)
    assert updater.check_for_update() is None


# ---------------------------------------------------------------------------
# 수동 확인(manual_check) — 소스/클론에서도 git HEAD 폴백
# ---------------------------------------------------------------------------
def test_manual_check_latest(tmp_path, monkeypatch):
    _write_version(tmp_path, monkeypatch,
                   {"sha": "SAME", "branch": "feat", "repo": "o/r"})
    monkeypatch.setattr(updater, "latest_commit", lambda r, b: {"sha": "SAME"})
    assert updater.manual_check() == ("latest", {})


def test_manual_check_update(tmp_path, monkeypatch):
    _write_version(tmp_path, monkeypatch,
                   {"sha": "OLD", "branch": "feat", "repo": "o/r"})
    monkeypatch.setattr(updater, "latest_commit",
                        lambda r, b: {"sha": "NEW", "message": "fix"})
    status, info = updater.manual_check()
    assert status == "update" and info["sha"] == "NEW" and info["branch"] == "feat"


def test_manual_check_git_fallback_when_no_version(tmp_path, monkeypatch):
    _write_version(tmp_path, monkeypatch, None)            # VERSION 없음
    monkeypatch.setattr(updater, "_git_head",
                        lambda: {"sha": "G", "branch": "dev", "repo": "o/r"})
    monkeypatch.setattr(updater, "latest_commit", lambda r, b: {"sha": "H"})
    status, info = updater.manual_check()
    assert status == "update" and info["branch"] == "dev"


def test_manual_check_offers_latest_when_current_unknown(tmp_path, monkeypatch):
    # VERSION·git 모두 없어도 내장 기본 repo/branch 로 최신을 받아 적용 제안.
    _write_version(tmp_path, monkeypatch, None)
    monkeypatch.setattr(updater, "_git_head", lambda: None)
    monkeypatch.setattr(updater, "latest_commit", lambda r, b: {"sha": "NEW"})
    status, info = updater.manual_check()
    assert status == "update"
    assert info["current_unknown"] is True
    assert info["branch"] == updater.DEFAULT_BRANCH
    assert info["repo"] == updater.DEFAULT_REPO


def test_manual_check_unknown_on_network_fail(tmp_path, monkeypatch):
    _write_version(tmp_path, monkeypatch,
                   {"sha": "OLD", "branch": "feat", "repo": "o/r"})
    monkeypatch.setattr(updater, "latest_commit", lambda r, b: None)
    status, info = updater.manual_check()
    assert status == "unknown" and "error" in info


def test_latest_commit_atom_fallback(monkeypatch):
    """api.github.com 실패 시 github.com Atom 피드로 SHA 를 읽어 폴백."""
    sha = "a" * 40
    atom = f'<feed><entry><id>tag:github.com,2008:Grit::Commit/{sha}</id></entry></feed>'

    def fake_get(url, headers, timeout):
        if "api.github.com" in url:
            raise urllib_error_403()
        return atom.encode("utf-8")

    monkeypatch.setattr(updater, "_http_get", fake_get)
    info = updater.latest_commit("o/r", "feat")
    assert info and info["sha"] == sha


def urllib_error_403():
    import urllib.error
    return urllib.error.HTTPError("http://api", 403, "blocked", {}, None)


def test_latest_commit_records_error_on_total_failure(monkeypatch):
    def boom(url, headers, timeout):
        raise ConnectionError("proxy down")
    monkeypatch.setattr(updater, "_http_get", boom)
    assert updater.latest_commit("o/r", "feat") is None
    assert updater.last_error()        # 사유 기록됨


def test_is_git_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "_app_root", lambda: tmp_path)
    assert updater.is_git_checkout() is False
    (tmp_path / ".git").mkdir()
    assert updater.is_git_checkout() is True
