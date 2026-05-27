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
