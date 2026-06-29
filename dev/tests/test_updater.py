"""자동 업데이트 버전 비교 로직 검증 (네트워크는 모킹)."""

from __future__ import annotations

import json

import pytest

from aoi_verification.app.utils import updater

# 테스트에서 가정하는 '저장소 기본 브랜치'(실제 조회는 네트워크라 스텁).  입력 브랜치와
# 구분되는 값을 써서 '리졸버가 기본 브랜치로 치환했음' 을 명확히 단정한다.
_STUB_DEFAULT = "default-branch"


@pytest.fixture(autouse=True)
def _stub_default_branch(monkeypatch):
    """모든 테스트에서 GitHub 기본 브랜치 조회를 네트워크 없이 결정적으로 만든다."""
    updater._default_branch_cache.clear()
    monkeypatch.setattr(updater, "_default_branch",
                        lambda repo, timeout=10.0: _STUB_DEFAULT)


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
    assert info["branch"] == _STUB_DEFAULT      # 기본 브랜치(동적 조회)로 추적
    assert info["repo"] == updater.DEFAULT_REPO


def test_manual_check_unknown_on_network_fail(tmp_path, monkeypatch):
    _write_version(tmp_path, monkeypatch,
                   {"sha": "OLD", "branch": "feat", "repo": "o/r"})
    monkeypatch.setattr(updater, "latest_commit", lambda r, b: None)
    status, info = updater.manual_check()
    assert status == "unknown" and "error" in info


# ---------------------------------------------------------------------------
# 레거시 브랜치 정규화 — 옛 빌드(VERSION branch=claude/…)도 저장소 기본 브랜치로 합류
# ---------------------------------------------------------------------------
def test_resolve_branch_normalizes_legacy_to_default():
    # 비었거나 claude/ 로 시작하면 → 저장소 기본 브랜치(스텁)로 치환.
    assert updater._resolve_branch("claude/matching-npu-gpu-modes-GwTRB") == _STUB_DEFAULT
    assert updater._resolve_branch("claude/aoi-verification-app-LAXpX") == _STUB_DEFAULT
    assert updater._resolve_branch("") == _STUB_DEFAULT
    assert updater._resolve_branch(None) == _STUB_DEFAULT
    # 옛 기본 브랜치(main/master)는 삭제됐으므로 → 기본 브랜치로 교정(404 방지).
    assert updater._resolve_branch("main") == _STUB_DEFAULT
    assert updater._resolve_branch("master") == _STUB_DEFAULT
    # 그 외 명시적 브랜치는 그대로 존중.
    assert updater._resolve_branch("release") == "release"
    assert updater._resolve_branch("dev") == "dev"
    # 폴백 상수는 더 이상 main 이 아니다(main 삭제됨).
    assert updater.DEFAULT_BRANCH != "main"


def test_latest_self_healing_retries_default_branch(monkeypatch):
    # 추적 브랜치엔 None(삭제됨), 기본 브랜치엔 커밋 → 기본 브랜치로 자기교정.
    def _latest(repo, branch):
        return {"sha": "NEW"} if branch == _STUB_DEFAULT else None

    monkeypatch.setattr(updater, "latest_commit", _latest)
    info, used = updater._latest_self_healing("o/r", "release")
    assert info == {"sha": "NEW"}
    assert used == _STUB_DEFAULT          # 다운로드도 살아있는 브랜치로 가도록 교정


def test_latest_self_healing_returns_none_when_both_fail(monkeypatch):
    monkeypatch.setattr(updater, "latest_commit", lambda r, b: None)
    info, used = updater._latest_self_healing("o/r", "release")
    assert info is None and used == "release"


def test_manual_check_recovers_from_dead_branch(tmp_path, monkeypatch):
    # 명시적이지만 삭제된 브랜치(release)를 스탬프한 설치본 → 기본 브랜치로 복구.
    _write_version(tmp_path, monkeypatch,
                   {"sha": "OLD", "branch": "release", "repo": "o/r"})

    def _latest(repo, branch):
        return {"sha": "NEW", "message": "fix"} if branch == _STUB_DEFAULT else None

    monkeypatch.setattr(updater, "latest_commit", _latest)
    status, info = updater.manual_check()
    assert status == "update"
    assert info["sha"] == "NEW"
    assert info["branch"] == _STUB_DEFAULT     # 교정된 브랜치가 다운로드 대상


def test_default_branch_resolves_from_api(monkeypatch):
    # 실제 _default_branch 는 GET /repos/{repo} 의 default_branch 를 읽는다.
    monkeypatch.undo()  # autouse 스텁 해제하고 진짜 함수 검증
    updater._default_branch_cache.clear()
    monkeypatch.setattr(updater, "_http_get",
                        lambda url, headers, timeout: json.dumps(
                            {"default_branch": "the-default"}).encode("utf-8"))
    assert updater._default_branch("o/r") == "the-default"
    # 캐시: 두 번째 호출은 네트워크 없이 같은 값.
    monkeypatch.setattr(updater, "_http_get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("blocked")))
    assert updater._default_branch("o/r") == "the-default"


def test_default_branch_falls_back_when_api_blocked(monkeypatch):
    monkeypatch.undo()
    updater._default_branch_cache.clear()
    monkeypatch.setattr(updater, "_http_get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("blocked")))
    assert updater._default_branch("o/r") == updater.DEFAULT_BRANCH


def test_check_migrates_legacy_version_branch_to_default(tmp_path, monkeypatch):
    # 과거 포터블 빌드(VERSION 에 작업 브랜치 스탬프) 는 기본 브랜치를 추적해야 한다.
    _write_version(tmp_path, monkeypatch,
                   {"sha": "OLD", "branch": "claude/matching-npu-gpu-modes-GwTRB",
                    "repo": "o/r"})
    seen = {}

    def _latest(repo, branch):
        seen["branch"] = branch
        return {"sha": "NEW", "message": "fix"}

    monkeypatch.setattr(updater, "latest_commit", _latest)
    info = updater.check_for_update()
    assert info and info["sha"] == "NEW"
    assert info["branch"] == _STUB_DEFAULT       # 반환값도 기본 브랜치로 정규화
    assert seen["branch"] == _STUB_DEFAULT       # 원격 조회도 기본 브랜치로 수행


def test_manual_check_migrates_legacy_version_branch_to_default(tmp_path, monkeypatch):
    _write_version(tmp_path, monkeypatch,
                   {"sha": "OLD", "branch": "claude/anything", "repo": "o/r"})
    monkeypatch.setattr(updater, "latest_commit",
                        lambda r, b: {"sha": "NEW", "message": "fix"})
    status, info = updater.manual_check()
    assert status == "update" and info["branch"] == _STUB_DEFAULT


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


def test_ssl_context_default_verifies():
    import ssl
    ctx = updater._ssl_context()
    assert ctx is not None and ctx.verify_mode == ssl.CERT_REQUIRED


def test_ssl_context_insecure_disables_verify():
    import ssl
    ctx = updater._ssl_context(insecure=True)
    assert ctx.verify_mode == ssl.CERT_NONE and ctx.check_hostname is False


def test_urlopen_falls_back_to_insecure_on_cert_error(monkeypatch):
    import ssl

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"OK"

    calls = {"insecure": []}

    class _Op:
        def __init__(self, insecure): self.insecure = insecure
        def open(self, req, timeout=None):
            calls["insecure"].append(self.insecure)
            if not self.insecure:
                raise urllib_error_ssl()
            return _Resp()

    monkeypatch.setattr(updater, "_opener", lambda insecure=False: _Op(insecure))
    monkeypatch.delenv("AOI_UPDATE_INSECURE", raising=False)
    updater._insecure_used = False
    with updater._urlopen("https://github.com/x", {}, 5) as r:
        assert r.read() == b"OK"
    assert calls["insecure"] == [False, True]          # 검증 시도 → 실패 → 비검증 재시도
    assert updater.insecure_fallback_used() is True


def urllib_error_ssl():
    import ssl
    import urllib.error
    return urllib.error.URLError(ssl.SSLCertVerificationError("bad cert"))


def test_is_git_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "_app_root", lambda: tmp_path)
    assert updater.is_git_checkout() is False
    (tmp_path / ".git").mkdir()
    assert updater.is_git_checkout() is True


# ── 의존성 변경 감지(requirements.txt) — 자동 재설치는 안 하고 알림만 ─────────
def _dirs(tmp_path, old_req, new_req):
    src = tmp_path / "src"
    app = tmp_path / "app"
    src.mkdir(parents=True); app.mkdir(parents=True)
    if new_req is not None:
        (src / "requirements.txt").write_text(new_req, encoding="utf-8")
    if old_req is not None:
        (app / "requirements.txt").write_text(old_req, encoding="utf-8")
    return src, app


def test_apply_requirements_detects_change(tmp_path):
    src, app = _dirs(tmp_path, old_req="numpy==1\n", new_req="numpy==1\ntimm\n")
    assert updater._apply_requirements(src, app) is True


def test_apply_requirements_no_change_when_same(tmp_path):
    src, app = _dirs(tmp_path, old_req="numpy==1\n", new_req="numpy==1\n")
    assert updater._apply_requirements(src, app) is False
    # 공백/개행만 다른 경우도 변경으로 보지 않는다.
    src2, app2 = _dirs(tmp_path / "b", old_req="numpy==1", new_req="numpy==1\n\n")
    assert updater._apply_requirements(src2, app2) is False


def test_apply_requirements_first_time_does_not_falsely_flag(tmp_path):
    # 기존 requirements.txt 가 아직 없으면(이 기능 도입 후 첫 업데이트) 오인 안 함.
    src, app = _dirs(tmp_path, old_req=None, new_req="numpy==1\n")
    assert updater._apply_requirements(src, app) is False


# ── download_and_apply — 미러링(필요한 것 전부) + 진행 보고 + dev 데이터 제외 ──
def _make_branch_zip(tmp_path):
    """가짜 브랜치 zip 을 만든다 — 'coding-x/' 최상위 폴더 아래 앱 트리."""
    import io
    import zipfile
    buf = io.BytesIO()
    files = {
        "coding-x/aoi_verification/app/__init__.py": "x = 1\n",
        "coding-x/main.py": "print('hi')\n",
        "coding-x/requirements.txt": "numpy==1\n",
        "coding-x/docs/새문서.md": "doc\n",
        "coding-x/scripts/run_this_before.py": "setup\n",
        # 개발 전용 모음(dev/) — 통째로 제외 대상.  단, dev/양식.xlsx 는 구동에
        # 필요하므로 앱 루트로 따로 복사돼야 한다.
        "coding-x/dev/양식.xlsx": "TEMPLATE",
        "coding-x/dev/tests/test_x.py": "def test(): pass\n",
        "coding-x/dev/bench결과/result.json": "{}",
        "coding-x/pytest.ini": "[pytest]\n",                   # 제외 대상(루트 앵커)
    }
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


class _FakeResp:
    def __init__(self, data: bytes):
        self._d = data
        self._i = 0
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        if n is None or n < 0:
            chunk, self._i = self._d[self._i:], len(self._d)
            return chunk
        chunk = self._d[self._i:self._i + n]
        self._i += len(chunk)
        return chunk


def test_download_and_apply_mirrors_needed_and_skips_dev_data(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    (app / "requirements.txt").write_text("numpy==1\n", encoding="utf-8")  # 동일 → 변경 아님
    zip_bytes = _make_branch_zip(tmp_path)
    monkeypatch.setattr(updater, "_app_root", lambda: app)
    monkeypatch.setattr(updater, "_urlopen",
                        lambda url, headers, timeout: _FakeResp(zip_bytes))

    seen = []
    ok = updater.download_and_apply("o/r", "x", "SHA123",
                                    progress=lambda d, t, p: seen.append((d, t, p)))
    assert ok is True
    # 구동에 필요한 것은 전부 받아 미러링된다.
    assert (app / "aoi_verification" / "app" / "__init__.py").exists()
    assert (app / "main.py").exists()
    # dev/ 는 통째로 제외되지만, 그 안의 양식.xlsx 는 앱 루트로 따로 복사된다.
    assert (app / "양식.xlsx").read_text() == "TEMPLATE"
    assert (app / "docs" / "새문서.md").exists()              # 새 문서도 함께
    assert (app / "scripts" / "run_this_before.py").exists()
    # 개발 전용 모음(dev/)·루트 앵커 설정은 제외된다.
    assert not (app / "dev").exists()
    assert not (app / "pytest.ini").exists()
    # VERSION 기록 + 진행 보고(단계 메시지)가 있었다.
    import json as _json
    ver = _json.loads((app / "VERSION").read_text())
    assert ver["sha"] == "SHA123"
    phases = {p for _, _, p in seen}
    assert any("다운로드" in p for p in phases)
    assert any("적용" in p for p in phases)
    assert updater.deps_changed() is False               # requirements 동일
