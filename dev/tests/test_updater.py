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


@pytest.fixture(autouse=True)
def _no_real_subprocess(monkeypatch):
    """테스트가 **실제 pip 을 돌려 네트워크를 타는 것**을 막는다.

    업데이트 경로가 패키지를 설치하게 되면서, 모킹을 빠뜨린 테스트가 조용히 진짜 pip 을
    실행하는 사고가 실제로 났다(느리고, 오프라인에서 깨지고, 결과가 환경에 따라 달라진다).
    의도적으로 pip 을 검증하는 테스트는 ``_fake_pip`` 로 이 가드를 덮어쓴다."""
    import subprocess

    def _blocked(cmd, **kw):
        raise AssertionError(
            "테스트가 실제 프로세스를 실행하려 했다(모킹 누락): "
            + " ".join(str(c) for c in cmd))

    monkeypatch.setattr(subprocess, "call", _blocked)


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


def test_check_none_in_git_checkout(tmp_path, monkeypatch):
    """개발(git 작업트리) 실행에선 자동 확인을 하지 않는다 — 네트워크도 안 건드린다.

    ★ 이 계약은 한때 'VERSION 이 없으면' 이었다(`test_check_none_in_dev_mode`).
    그런데 VERSION 이 없는 건 개발 실행만이 아니다 — git 아닌 소스로 만든 포터블
    빌드도 그렇고, 그런 배포본은 업데이트가 있다는 사실 자체를 영영 몰랐다.
    이제 기준은 '**VERSION 이 없다**' 가 아니라 '**git 작업트리다**' 이다."""
    _write_version(tmp_path, monkeypatch, None)
    (tmp_path / ".git").mkdir()
    called = {"n": 0}

    def _boom(repo, branch):
        called["n"] += 1
        raise AssertionError("개발 모드에선 원격 조회를 하면 안 됨")

    monkeypatch.setattr(updater, "latest_commit", _boom)
    assert updater.check_for_update() is None
    assert called["n"] == 0
    assert not (tmp_path / "VERSION").exists(), "작업트리에 산출물을 남기면 안 된다"


def test_check_creates_version_and_offers_update_when_missing(tmp_path,
                                                              monkeypatch):
    """★ 핵심 — VERSION 이 없는 포터블 빌드도 자동 업데이트를 받는다.

    예전엔 여기서 즉시 None 이라, 사용자는 업데이트가 있다는 것조차 몰랐다."""
    _write_version(tmp_path, monkeypatch, None)
    monkeypatch.setattr(updater, "_git_head", lambda: None)
    monkeypatch.setattr(updater, "latest_commit",
                        lambda r, b: {"sha": "NEW", "message": "fix"})

    info = updater.check_for_update()

    assert info is not None, "VERSION 이 없다고 자동 확인이 꺼지면 안 된다"
    assert info["sha"] == "NEW"
    assert info["current_unknown"] is True     # 현재 버전 미상 → 그렇게 안내한다
    assert info["branch"] == _STUB_DEFAULT
    assert info["repo"] == updater.DEFAULT_REPO
    # 초기 VERSION 이 실제로 만들어졌다.
    assert json.loads((tmp_path / "VERSION").read_text(encoding="utf-8")) == {
        "sha": "", "branch": updater.DEFAULT_BRANCH, "repo": updater.DEFAULT_REPO}


def test_ensure_version_file_never_overwrites_an_existing_stamp(tmp_path,
                                                                monkeypatch):
    """실제 SHA 를 덮어쓰면 '항상 업데이트 있음' 이 된다 — 절대 건드리지 않는다."""
    _write_version(tmp_path, monkeypatch,
                   {"sha": "REAL", "branch": "feat", "repo": "o/r"})
    assert updater.ensure_version_file()["sha"] == "REAL"
    assert json.loads((tmp_path / "VERSION").read_text(
        encoding="utf-8"))["sha"] == "REAL"


def test_ensure_version_file_seeds_from_git_head_when_available(tmp_path,
                                                                monkeypatch):
    """git 은 있는데 VERSION 만 없는 경우(클론 배포) — HEAD 를 그대로 심는다."""
    _write_version(tmp_path, monkeypatch, None)
    monkeypatch.setattr(updater, "_git_head",
                        lambda: {"sha": "G", "branch": "dev", "repo": "o/r"})
    cur = updater.ensure_version_file()
    assert cur == {"sha": "G", "branch": "dev", "repo": "o/r"}


def test_check_none_when_already_latest_with_seeded_version(tmp_path,
                                                            monkeypatch):
    """초기 VERSION 을 만든 뒤 한 번 적용하면, 다음부터는 정상 비교로 돌아간다."""
    _write_version(tmp_path, monkeypatch, None)
    monkeypatch.setattr(updater, "_git_head", lambda: None)
    monkeypatch.setattr(updater, "latest_commit", lambda r, b: {"sha": "NEW"})
    assert updater.check_for_update() is not None       # 1회차: 미상 → 제안
    updater._write_version("NEW", _STUB_DEFAULT, updater.DEFAULT_REPO)
    assert updater.check_for_update() is None           # 2회차: 최신 → 조용


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
def _make_branch_zip(tmp_path, extra=None, drop=()):
    """가짜 브랜치 zip 을 만든다 — 'coding-x/' 최상위 폴더 아래 앱 트리.

    ``_verify_staged`` 가 요구하는 필수 파일을 모두 담는다(실제 저장소와 같은 모양).
    ``extra`` 로 파일을 더하고 ``drop`` 으로 뺄 수 있다(검증 실패 경로 테스트용)."""
    import io
    import zipfile
    buf = io.BytesIO()
    files = {
        "coding-x/aoi_verification/app/__init__.py": "x = 1\n",
        # 아래 셋은 '앱으로 쓸 수 있는 트리인가' 를 판정하는 필수 항목이다.
        "coding-x/aoi_verification/app/ui/main_window.py": "class MainWindow: pass\n",
        "coding-x/aoi_verification/app/ui/style.qss": "QWidget {}\n",
        "coding-x/aoi_verification/app/utils/updater.py": "DEFAULT_BRANCH = 'b'\n",
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
        # ★ Claude Code 스킬 모음 — 이 저장소에서 **작업할 때** 쓰는 도구이고 앱 구동과
        #   무관하다(실제로 198개 파일·4.8 MB).  기본 브랜치는 자동 업데이트가 추적하는
        #   브랜치이므로, 제외하지 않으면 모든 포터블 설치가 이것을 내려받는다.
        "coding-x/.claude/skills/impeccable/SKILL.md": "skill\n",
        "coding-x/.claude/skills/impeccable/reference/audit.md": "ref\n",
    }
    files.update(extra or {})
    for name in drop:
        files.pop(name, None)
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
    # ★ Claude Code 스킬 모음도 제외된다 — **결과로** 판정한다(목록에 이름이 있는지가
    #   아니라 파일이 앱 폴더에 안 생기는지가 계약이다).  이걸 빼먹으면 업데이트마다
    #   4.8 MB 개발 도구가 사용자 앱 폴더로 따라간다.
    assert not (app / ".claude").exists(), \
        "개발 전용 스킬 모음이 앱 폴더로 미러링됐다"
    # VERSION 기록 + 진행 보고(단계 메시지)가 있었다.
    import json as _json
    ver = _json.loads((app / "VERSION").read_text())
    assert ver["sha"] == "SHA123"
    phases = {p for _, _, p in seen}
    assert any("다운로드" in p for p in phases)
    assert any("적용" in p for p in phases)
    assert updater.deps_changed() is False               # requirements 동일
    assert not (app / ".update.part").exists()           # 스테이징 흔적을 남기지 않는다


def test_in_place_apply_propagates_upstream_deletions(tmp_path, monkeypatch):
    """상류에서 지운 모듈이 사용자 디스크에서도 사라져야 한다.

    옛 방식(copytree 덮어쓰기)은 지우지 않아, 삭제된 모듈이 영원히 남아 계속 import 됐다."""
    app = tmp_path / "app"
    (app / "aoi_verification" / "app").mkdir(parents=True)
    (app / "aoi_verification" / "app" / "지워진모듈.py").write_text("old", encoding="utf-8")
    (app / "aoi_verification" / "app" / "__pycache__").mkdir()
    (app / "aoi_verification" / "app" / "__pycache__" / "x.pyc").write_bytes(b"stale")
    zip_bytes = _make_branch_zip(tmp_path)
    monkeypatch.setattr(updater, "_app_root", lambda: app)
    monkeypatch.setattr(updater, "_urlopen",
                        lambda url, headers, timeout: _FakeResp(zip_bytes))

    assert updater.download_and_apply("o/r", "x", "SHA") is True
    assert (app / "aoi_verification" / "app" / "__init__.py").exists()
    assert not (app / "aoi_verification" / "app" / "지워진모듈.py").exists()
    assert not (app / "aoi_verification" / "app" / "__pycache__").exists()


def test_verification_rejects_incomplete_tree_and_keeps_old_version(tmp_path, monkeypatch):
    """필수 파일이 빠진 zip 은 적용하지 않는다 — 구버전과 VERSION 이 그대로 남는다.

    옛 코드는 '예외가 안 났으면 성공' 이라, 반쪽 트리도 성공으로 보고했다."""
    app = tmp_path / "app"
    app.mkdir()
    (app / "main.py").write_text("OLD", encoding="utf-8")
    (app / "VERSION").write_text('{"sha": "OLDSHA", "branch": "b", "repo": "r"}',
                                 encoding="utf-8")
    zip_bytes = _make_branch_zip(tmp_path, drop=("coding-x/dev/양식.xlsx",))
    monkeypatch.setattr(updater, "_app_root", lambda: app)
    monkeypatch.setattr(updater, "_urlopen",
                        lambda url, headers, timeout: _FakeResp(zip_bytes))

    assert updater.download_and_apply("o/r", "x", "NEWSHA") is False
    assert "양식.xlsx" in updater.last_error()           # 왜 안 됐는지 알려준다
    assert (app / "main.py").read_text() == "OLD"        # 구버전 그대로
    import json as _json
    assert _json.loads((app / "VERSION").read_text())["sha"] == "OLDSHA"
    assert not (app / ".update.part").exists()


# ── exe 모드(런처가 교체) — 스테이징만 하고 살아있는 app/ 은 건드리지 않는다 ──
def _exe_install(tmp_path, monkeypatch):
    """'exe + app 폴더' 설치를 흉내낸다.

    빌드가 남기는 의존성 표식(`.deps_installed`)도 함께 만든다 — 실제 빌드가 그렇고,
    이게 없으면 요구사항이 그대로여도 설치를 한 번 돌게 된다."""
    from aoi_verification.app.utils import bootstrap
    root = tmp_path / "AOI_Verify"
    app = root / "app"
    app.mkdir(parents=True)
    (app / "main.py").write_text("OLD", encoding="utf-8")
    (app / "VERSION").write_text('{"sha": "OLDSHA", "branch": "b", "repo": "r"}',
                                 encoding="utf-8")
    (app / "requirements.txt").write_text("numpy==1\n", encoding="utf-8")
    bootstrap.write_deps_marker(root, "numpy==1\n")
    monkeypatch.setenv("AOI_APP_HOME", str(root))
    monkeypatch.setattr(updater, "_app_root", lambda: app)
    return root, app


def test_staged_update_leaves_the_running_app_untouched(tmp_path, monkeypatch):
    """★ 핵심 계약 — 실행 중인 app/ 도 app/VERSION 도 바뀌지 않는다.

    VERSION 을 미리 쓰면 교체가 보류·실패했을 때 앱이 '최신입니다' 라며 구버전을
    영원히 실행한다."""
    root, app = _exe_install(tmp_path, monkeypatch)
    zip_bytes = _make_branch_zip(tmp_path)
    monkeypatch.setattr(updater, "_urlopen",
                        lambda url, headers, timeout: _FakeResp(zip_bytes))

    assert updater.download_and_apply("o/r", "x", "NEWSHA") is True

    assert (app / "main.py").read_text() == "OLD"        # 살아있는 앱은 그대로
    import json as _json
    assert _json.loads((app / "VERSION").read_text())["sha"] == "OLDSHA"
    # 새 버전은 app.new 에 완성된 채로 대기한다.
    new = root / "app.new"
    assert new.is_dir()
    assert (new / "main.py").read_text() == "print('hi')\n"
    assert _json.loads((new / "VERSION").read_text())["sha"] == "NEWSHA"
    assert not (root / "app.new.part").exists()          # 미완성 이름은 남지 않는다
    assert updater.update_pending() is True


def test_pending_update_suppresses_further_checks(tmp_path, monkeypatch):
    """이미 받아뒀으면 재시작만 하면 된다 — 매번 다시 받지 않는다."""
    root, _app = _exe_install(tmp_path, monkeypatch)
    (root / "app.new").mkdir()
    monkeypatch.setattr(updater, "_latest_self_healing",
                        lambda repo, branch: (_ for _ in ()).throw(
                            AssertionError("네트워크를 부르면 안 된다")))
    assert updater.check_for_update() is None


def test_incomplete_staging_never_becomes_a_ready_signal(tmp_path, monkeypatch):
    """준비 중 실패하면 app.new 가 만들어지지 않는다(존재 자체가 '완료' 신호이므로)."""
    root, app = _exe_install(tmp_path, monkeypatch)
    zip_bytes = _make_branch_zip(tmp_path)
    monkeypatch.setattr(updater, "_urlopen",
                        lambda url, headers, timeout: _FakeResp(zip_bytes))

    def _boom(src_root, staging, emit):
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "half.py").write_text("x", encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(updater, "_stage_tree", _boom)
    assert updater.download_and_apply("o/r", "x", "NEWSHA") is False
    assert not (root / "app.new").exists()
    assert not (root / "app.new.part").exists()
    assert (app / "main.py").read_text() == "OLD"


def _fake_pip(monkeypatch, rc: int, calls: list):
    """pip 호출을 가로챈다 — 실제 설치 없이 '무엇을 어떻게 부르는지' 를 검증한다."""
    import subprocess

    def _call(cmd, **kw):
        calls.append([str(c) for c in cmd])
        return rc

    monkeypatch.setattr(subprocess, "call", _call)


def test_new_packages_are_installed_then_the_update_applies(tmp_path, monkeypatch):
    """패키지가 바뀌면 동봉 파이썬에 설치하고, **성공해야** 코드를 적용한다."""
    from aoi_verification.app.utils import bootstrap
    root, _app = _exe_install(tmp_path, monkeypatch)
    (root / "python").mkdir(exist_ok=True)
    (root / "python" / "python.exe").write_bytes(b"x")   # 번들 파이썬
    zip_bytes = _make_branch_zip(
        tmp_path, extra={"coding-x/requirements.txt": "numpy==1\n새패키지==2\n"})
    monkeypatch.setattr(updater, "_urlopen",
                        lambda url, headers, timeout: _FakeResp(zip_bytes))
    calls = []
    _fake_pip(monkeypatch, 0, calls)

    seen = []
    ok = updater.download_and_apply("o/r", "x", "NEWSHA",
                                    progress=lambda d, t, p: seen.append((d, t, p)))
    assert ok is True
    assert (root / "app.new").is_dir()
    assert updater.deps_blocked() is False
    # 번들 파이썬으로, --upgrade 없이(=빠진 것만) 설치했다.
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0].endswith("python.exe") and "python" in cmd[0]
    assert cmd[1:4] == ["-m", "pip", "install"]
    assert "--upgrade" not in cmd, "이미 잘 돌던 torch 까지 최신으로 끌어올리면 안 된다"
    # 설치 단계는 진행량을 모르므로 busy(total<=0) 로 보고한다 — 0 에서 멈추지 않게.
    assert any(t <= 0 and "설치" in p for _d, t, p in seen)
    # 표식이 새 목록으로 갱신돼, 다음 업데이트 때 재설치가 돌지 않는다.
    assert bootstrap.deps_installed(root, "numpy==1\n새패키지==2\n")


def test_failed_package_install_leaves_the_old_version_running(tmp_path, monkeypatch):
    """설치가 실패하면 코드를 적용하지 않는다 — 앱이 ImportError 로 죽는 것을 막는다."""
    from aoi_verification.app.utils import bootstrap
    root, app = _exe_install(tmp_path, monkeypatch)
    (root / "python").mkdir(exist_ok=True)
    (root / "python" / "python.exe").write_bytes(b"x")
    zip_bytes = _make_branch_zip(
        tmp_path, extra={"coding-x/requirements.txt": "numpy==1\n새패키지==2\n"})
    monkeypatch.setattr(updater, "_urlopen",
                        lambda url, headers, timeout: _FakeResp(zip_bytes))
    _fake_pip(monkeypatch, 1, [])                        # pip 실패

    assert updater.download_and_apply("o/r", "x", "NEWSHA") is False
    assert updater.deps_blocked() is True
    assert not (root / "app.new").exists()               # 적용하지 않는다
    assert (app / "main.py").read_text() == "OLD"        # 앱은 계속 쓸 수 있다
    # 표식도 갱신되지 않아 다음 실행에 다시 설치를 시도한다.
    assert not bootstrap.deps_installed(root, "numpy==1\n새패키지==2\n")


def test_unchanged_requirements_never_run_pip(tmp_path, monkeypatch):
    """주석·빈 줄만 바뀐 것으로 2 GB 재설치가 돌면 안 된다."""
    from aoi_verification.app.utils import bootstrap
    root, _app = _exe_install(tmp_path, monkeypatch)
    zip_bytes = _make_branch_zip(
        tmp_path, extra={"coding-x/requirements.txt": "# 설명 추가\nnumpy==1\n\n"})
    monkeypatch.setattr(updater, "_urlopen",
                        lambda url, headers, timeout: _FakeResp(zip_bytes))
    calls = []
    _fake_pip(monkeypatch, 0, calls)

    assert updater.download_and_apply("o/r", "x", "NEWSHA") is True
    assert calls == [], "요구사항이 그대로인데 pip 을 돌렸다"
    assert (root / "app.new").is_dir()
