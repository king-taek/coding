"""build.py / portable_build.py — 빌드 스크립트의 순수 로직 + 주입 흐름 테스트.

실제 PyInstaller/다운로드는 주입(injection)으로 분리돼 있어 무거운 의존성 없이 검증한다.
"""

from __future__ import annotations

import importlib.util as _u
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = _u.spec_from_file_location(name, str(_ROOT / rel))
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build = _load("build", "scripts/build.py")
portable = _load("portable_build", "scripts/internal/portable_build.py")


# ── build.py 순수 로직 ──────────────────────────────────────────────────────
def test_venv_python_os_specific():
    p = build.venv_python(Path("/repo"))
    assert p.parts[-1] in ("python.exe", "python")
    assert ".venv" in p.parts


def test_command_builders():
    assert build.pyinstaller_cmd("py", Path("a.spec"))[:4] == [
        "py", "-m", "PyInstaller", "--noconfirm"]
    assert build.pip_install_cmd("py", "-r", "req.txt") == [
        "py", "-m", "pip", "install", "-r", "req.txt"]
    assert build.guard_cmd("py")[0] == "py"
    assert "verify_no_forbidden.py" in build.guard_cmd("py")[1]


def test_output_paths():
    assert build.output_path("online", Path("/r")).name == "AOI_Verify_Online.exe"
    assert build.output_path("exe", Path("/r")).name == "AOI_Verify"
    assert build.output_path("portable", Path("/r")).name == "dist_portable"


def test_main_usage_and_unknown(capsys):
    assert build.main([]) == 0                      # 비대화형 → 사용법 출력
    assert "exe" in capsys.readouterr().out
    assert build.main(["nope"]) == 2                # 알 수 없는 종류


def test_prompt_kind_selection():
    # VS Code ▶ 처럼 인자 없이 실행 시 번호/이름으로 빌드 종류 선택.
    assert build._prompt_kind(input_fn=lambda _: "1") == "exe"
    assert build._prompt_kind(input_fn=lambda _: "2") == "portable"
    assert build._prompt_kind(input_fn=lambda _: "3") == "online"
    assert build._prompt_kind(input_fn=lambda _: "4") == "verify"
    assert build._prompt_kind(input_fn=lambda _: "exe") == "exe"
    assert build._prompt_kind(input_fn=lambda _: "verify") == "verify"
    assert build._prompt_kind(input_fn=lambda _: "") is None       # Enter=취소
    assert build._prompt_kind(input_fn=lambda _: "9") is None      # 범위 밖


def test_removed_onedir_mode_is_gone():
    """앱을 exe 안에 얼리던 옛 모드는 자동 업데이트와 양립 불가라 제거했다.

    되살아나면 '업데이트했다는데 안 바뀐다' 사고가 그대로 재발한다."""
    assert "windows" not in build._ACTIONS
    assert not (_ROOT / "scripts" / "internal" / "aoi_verification.spec").exists()
    assert not (_ROOT / "scripts" / "internal" / "build_windows.bat").exists()


def test_launcher_spec_cannot_swallow_the_app():
    """런처 spec 이 앱을 동봉할 통로를 갖지 않는지 — 이 사고의 기계적 재발 방지."""
    text = (_ROOT / "scripts" / "internal" / "exe_launcher.spec").read_text(
        encoding="utf-8")
    assert "hiddenimports = []" in text          # 앱 모듈을 끌어들일 통로 없음
    assert '"aoi_verification",' in text         # excludes 에 명시
    assert "pathex=[]" in text                   # 저장소 루트를 Analysis 에 안 준다


def test_build_online_injected_flow():
    calls = []
    rc = build.build_online(run=lambda c, cwd=None: calls.append(
        " ".join(str(x) for x in c)) or 0, log=lambda *a: None)
    assert rc == 0
    joined = "\n".join(calls)
    assert "online.spec" in joined                  # 올바른 spec
    assert "verify_no_forbidden.py" in joined        # 보안 가드
    assert "pyinstaller>=6" in joined


def _sandbox_build(monkeypatch, tmp_path):
    """``build_exe`` 를 임시 폴더에서 돌리게 한다.

    ★ 이걸 안 하면 테스트가 **실제 저장소의 dist\\AOI_Verify 를 지운다** — 개발자가
    빌드해 둔 산출물이 pytest 한 번에 날아간다."""
    monkeypatch.setattr(build, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(build, "preflight", lambda *a, **k: 0)   # 디스크/git 검사 제외
    return tmp_path / build.EXE_OUT_DIRNAME


def _fake_run(calls, out: Path, exe_mb: float = 3.0):
    """PyInstaller 호출을 보면 얇은 런처 exe 를 만들어 주는 가짜 실행기."""
    def _run(cmd, cwd=None):
        joined = " ".join(str(x) for x in cmd)
        calls.append(joined)
        if "PyInstaller" in joined:
            out.mkdir(parents=True, exist_ok=True)
            (out / "AOI_Verify.exe").write_bytes(b"x" * int(exe_mb * 1024 * 1024))
        return 0
    return _run


def test_build_exe_builds_launcher_then_reuses_portable(monkeypatch, tmp_path):
    """exe 빌드는 ① 런처 spec 을 얼리고 ② 포터블 빌더로 런타임·앱을 배치한다."""
    out = _sandbox_build(monkeypatch, tmp_path)
    calls = []
    seen = {}

    class _FakeImpl:
        @staticmethod
        def run_build(repo_root, py_url, run=None, log=None, **kw):
            seen.update(kw)
            return 0

    monkeypatch.setattr(build, "_load_portable_impl", lambda: _FakeImpl)
    monkeypatch.setattr(build, "verify_exe", lambda *a, **k: 0)
    rc = build.build_exe(run=_fake_run(calls, out), log=lambda *a: None)
    assert rc == 0
    joined = "\n".join(calls)
    assert "exe_launcher.spec" in joined             # 얇은 런처 spec
    assert "verify_no_forbidden.py" in joined        # 보안 가드
    assert "--distpath" in joined                    # 산출물 위치 지정
    # 포터블 빌더에 넘긴 옵션 — 병합식 update_app.bat 은 빼고, 가중치는 동봉한다.
    assert seen["out_dirname"] == build.EXE_OUT_DIRNAME
    assert "update_app.bat" not in seen["bats"]
    assert seen["torch_home"] is True


def test_rebuild_removes_stale_onedir_output(monkeypatch, tmp_path):
    """★ 회귀 가드 — 옛 단독 exe 산출물을 지우지 않으면 검증이 영원히 실패한다.

    PyInstaller 는 onefile 이라 exe 파일 하나만 교체하고 distpath 를 비우지 않으므로,
    빌드가 직접 지워야 한다. 안 지우면 사용자는 '다시 빌드하세요' 안내를 아무리 따라도
    상태를 바꿀 수 없다(무한 루프)."""
    out = _sandbox_build(monkeypatch, tmp_path)
    (out / "_internal").mkdir(parents=True)
    (out / "_internal" / "torch").mkdir()
    (out / "app.old").mkdir()
    (out / "AOI_Verify.exe").write_bytes(b"x" * 63 * 1024 * 1024)   # 옛 63MB exe
    # 비싸게 만든 것들은 남아야 한다(다시 만들면 30분).
    (out / "python").mkdir()
    (out / "python" / "python.exe").write_bytes(b"x")
    (out / "runtime").mkdir()
    (out / "결과").mkdir()
    (out / "결과" / "사용자결과.xlsx").write_text("지키자", encoding="utf-8")

    monkeypatch.setattr(build, "_load_portable_impl",
                        lambda: type("I", (), {"run_build": staticmethod(
                            lambda *a, **k: 0)}))
    monkeypatch.setattr(build, "verify_exe", lambda *a, **k: 0)
    assert build.build_exe(run=_fake_run([], out), log=lambda *a: None) == 0

    assert not (out / "_internal").exists(), "옛 onedir 잔재가 남았다"
    assert not (out / "app.old").exists()
    assert (out / "python" / "python.exe").is_file(), "번들 런타임을 지우면 30분을 버린다"
    assert (out / "runtime").is_dir()
    assert (out / "결과" / "사용자결과.xlsx").is_file(), "사용자 산출물을 지웠다"


def test_build_exe_stops_when_launcher_came_out_fat(monkeypatch, tmp_path):
    """런처에 앱이 딸려 들어갔으면 2~3 GB 를 받기 **전에** 멈춰야 한다."""
    out = _sandbox_build(monkeypatch, tmp_path)
    monkeypatch.setattr(build, "_load_portable_impl",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("무거운 단계로 넘어가면 안 된다")))
    with pytest.raises(SystemExit):
        build.build_exe(run=_fake_run([], out, exe_mb=63.0), log=lambda *a: None)


def test_build_exe_reports_why_when_stage_fails(monkeypatch, tmp_path):
    """30분 돌리고 실패했는데 '실패했다' 는 말조차 없으면 원인을 알 수 없다."""
    out = _sandbox_build(monkeypatch, tmp_path)
    monkeypatch.setattr(build, "_load_portable_impl",
                        lambda: type("I", (), {"run_build": staticmethod(
                            lambda *a, **k: 1)}))
    logs = []
    rc = build.build_exe(run=_fake_run([], out), log=logs.append)
    assert rc != 0
    assert any("실패" in m for m in logs), "실패 사실을 알리지 않았다"
    # run_build 가 이미 정확한 [FAILED] 를 냈으므로 그쪽을 보라고 안내한다
    # (여기서 파일시스템으로 추측하면 서로 모순되는 안내가 된다).
    assert any("확인하세요" in m for m in logs)


# ── verify_exe 검증 로직 ────────────────────────────────────────────────────
def _make_good_bundle(tmp_path) -> Path:
    """검증을 통과하는 최소 산출물을 만든다."""
    out = tmp_path / build.EXE_OUT_DIRNAME
    app = out / "app"
    pkg = app / "aoi_verification"
    (pkg / "app" / "ui" / "assets").mkdir(parents=True)
    (pkg / "app" / "ui" / "style.qss").write_text("x", encoding="utf-8")
    (pkg / "app" / "ui" / "assets" / "logo.ico").write_bytes(b"x")
    for i in range(55):                          # 실제 코드 트리처럼 모듈이 많아야 한다
        (pkg / f"m{i}.py").write_text("x", encoding="utf-8")
    (app / "main.py").write_text("x", encoding="utf-8")
    (app / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    (app / "양식.xlsx").write_bytes(b"x" * 1000)
    (app / "VERSION").write_text('{"sha": "a", "branch": "b", "repo": "r"}',
                                 encoding="utf-8")
    (out / "AOI_Verify.exe").write_bytes(b"x" * 3_000_000)
    (out / "python").mkdir()
    (out / "python" / "python.exe").write_bytes(b"x")
    (out / "python" / "pythonw.exe").write_bytes(b"x")
    (out / ".deps_installed").write_text("fp", encoding="utf-8")
    ckpt = out / "runtime" / "torch" / "hub" / "checkpoints"
    ckpt.mkdir(parents=True)
    (ckpt / "mobilenet.pth").write_bytes(b"x")
    (ckpt / "resnet18.pth").write_bytes(b"x")
    # ★ 번들에 패키지가 실제로 설치돼 있어야 통과한다.  이 헬퍼가 예전엔 용량 더미만
    #   채우고도 통과했다는 사실 자체가, 옛 '총 용량 800MB' 검사가 아무것도 증명하지
    #   못했다는 증거다(의존성 0 개인 번들이 그 검사만으로는 구분되지 않았다).
    sp = portable.site_packages_dir(out)
    sp.mkdir(parents=True, exist_ok=True)
    req = (out / "app" / "requirements.txt").read_text(encoding="utf-8")
    for name in portable.required_dists(req):
        (sp / f"{name}-1.0.0.dist-info").mkdir()
    (sp / "big.bin").write_bytes(b"\x00" * (510 * 1024 * 1024))
    return out


def test_verify_exe_pass(tmp_path):
    _make_good_bundle(tmp_path)
    logs = []
    assert build.verify_exe(tmp_path, log=logs.append) == 0
    assert any("빌드 정상" in m for m in logs)


def test_verify_exe_fails_when_app_is_frozen_into_the_exe(tmp_path):
    """★ 핵심 회귀 가드 — _internal/ 이 생기면 앱을 다시 exe 안에 얼린 것이다."""
    out = _make_good_bundle(tmp_path)
    (out / "_internal").mkdir()
    logs = []
    assert build.verify_exe(tmp_path, log=logs.append) != 0
    assert any("_internal" in m and "[!!]" in m for m in logs)


def test_verify_exe_fails_on_empty_app_package(tmp_path):
    """폴더만 있고 코드가 없는 상태로는 통과하지 못한다(옛 검증의 빈틈)."""
    out = _make_good_bundle(tmp_path)
    for p in (out / "app" / "aoi_verification").glob("m*.py"):
        p.unlink()
    assert build.verify_exe(tmp_path, log=lambda *a: None) != 0


def test_verify_exe_fails_without_bundled_weights(tmp_path):
    """가중치가 없으면 사내망에서 매칭이 안 된다 — 빌드 실패로 잡는다."""
    out = _make_good_bundle(tmp_path)
    import shutil
    shutil.rmtree(out / "runtime")
    assert build.verify_exe(tmp_path, log=lambda *a: None) != 0


def test_verify_exe_fail_missing_exe(tmp_path):
    logs = []
    assert build.verify_exe(tmp_path, log=logs.append) != 0
    assert any("[!!]" in m for m in logs)


def test_import_probe_uses_bundled_python_and_app_dir(tmp_path):
    cmd = build.import_probe_cmd(tmp_path / "out")
    assert cmd[0].endswith("python.exe")
    assert "aoi_verification" in cmd[-1] and "PyQt6" in cmd[-1]


# ── portable_build.py 순수 로직 ─────────────────────────────────────────────
def test_portable_python_path():
    p = portable.portable_python(Path("/out"))
    assert p.parts[-2] == "python" or p.parts[-3] == "python"


def test_version_stamp_json():
    import json
    s = portable.version_stamp("abc", "main")
    d = json.loads(s)
    assert d["sha"] == "abc" and d["branch"] == "main" and d["repo"]


def test_portable_run_build_aborts_without_runtime(tmp_path, monkeypatch):
    # 다운로드를 가짜로 막아(작은 파일) 즉시 실패 경로를 검증 — 실제 네트워크 없이.
    def fake_urlretrieve(url, dst):
        Path(dst).write_bytes(b"x")                 # 1 byte → too small
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlretrieve", fake_urlretrieve)
    rc = portable.run_build(tmp_path, "http://example/none.tar.gz",
                            run=lambda c, cwd=None: 0, log=lambda *a: None)
    assert rc == 1                                   # 런타임 없음 → 실패


# ── 산출물 상태 진단 — '무엇이 없다' 가 아니라 '무엇을 하라' ──────────────────
def _stage(tmp_path, *, stage: str) -> Path:
    """지정한 단계까지 진행된 산출물 폴더를 만든다(run_build 의 기록 순서 그대로)."""
    out = tmp_path / build.EXE_OUT_DIRNAME
    if stage == "missing":
        return out
    out.mkdir(parents=True)
    if stage == "empty":
        return out
    if stage == "stale":
        (out / "_internal").mkdir()
        (out / "AOI_Verify.exe").write_bytes(b"x")
        return out
    (out / "AOI_Verify.exe").write_bytes(b"x")
    if stage == "launcher":
        return out
    (out / "python").mkdir()
    (out / "python" / "python.exe").write_bytes(b"x")
    if stage == "runtime":
        return out
    (out / ".deps_installed").write_text("fp", encoding="utf-8")
    if stage == "deps":
        return out
    ck = out / "runtime" / "torch" / "hub" / "checkpoints"
    ck.mkdir(parents=True)
    (ck / "a.pth").write_bytes(b"x")
    (ck / "b.pth").write_bytes(b"x")
    if stage == "weights":
        return out
    (out / "app").mkdir()
    (out / "app" / "VERSION").write_text("{}", encoding="utf-8")
    return out


@pytest.mark.parametrize("stage,expected", [
    ("missing", "not_built"),
    ("empty", "not_built"),
    ("stale", "stale_onedir"),
    ("launcher", "partial:runtime"),
    ("runtime", "partial:deps"),
    ("deps", "partial:weights"),
    ("weights", "partial:appcopy"),
    ("done", "complete"),
])
def test_diagnose_distinguishes_states(tmp_path, stage, expected):
    out = _stage(tmp_path, stage=stage)
    state, todo = build.diagnose(out)
    assert state == expected
    if expected == "complete":
        assert todo == []
    else:
        assert todo and todo[0], "무엇을 하라는 안내가 비어 있다"


def test_diagnose_tells_stale_users_to_delete_the_folder(tmp_path):
    """옛 산출물은 '다시 빌드하세요' 로는 절대 해결되지 않는다 — 지우라고 해야 한다."""
    out = _stage(tmp_path, stage="stale")
    _state, todo = build.diagnose(out)
    joined = "\n".join(todo)
    assert "지우" in joined or "rmdir" in joined


# ── 사전 점검 ──────────────────────────────────────────────────────────────
def test_preflight_stops_on_low_disk(tmp_path):
    logs = []
    assert build.preflight(tmp_path, log=logs.append,
                           free_bytes=2 * 1024 ** 3, dirty=False) != 0
    assert any("디스크" in m for m in logs)


def test_preflight_warns_but_continues_on_dirty_tree(tmp_path):
    """커밋 안 한 변경이 있으면 VERSION 이 거짓말을 한다 — 경고하되 막지는 않는다."""
    logs = []
    assert build.preflight(tmp_path, log=logs.append,
                           free_bytes=500 * 1024 ** 3, dirty=True) == 0
    assert any("커밋" in m for m in logs)


# ── 옛 산출물 판별 ─────────────────────────────────────────────────────────
def test_stale_paths_keeps_the_expensive_parts(tmp_path):
    out = tmp_path / "dist" / "AOI_Verify"
    for name in ("_internal", "app.new", "app.old", "python", "runtime", "결과"):
        (out / name).mkdir(parents=True)
    (out / "AOI_Verify.exe").write_bytes(b"x")
    names = {p.name for p in build.stale_paths(out)}
    assert names == {"_internal", "app.new", "app.old", "AOI_Verify.exe"}
    # 다시 만들면 30분 걸리는 것과 사용자 산출물은 건드리지 않는다.
    assert "python" not in names and "runtime" not in names and "결과" not in names


# ── cp949 리다이렉트 ───────────────────────────────────────────────────────
@pytest.mark.parametrize("rel", [
    "scripts/build.py",
    "scripts/internal/portable_build.py",
    "scripts/make_release_zip.py",
])
def test_build_scripts_survive_cp949_redirect(rel):
    """빌드 로그를 파일로 남기는 순간 UnicodeEncodeError 로 죽으면 안 된다.

    한국어 Windows 에서 stdout 을 리다이렉트하면 인코딩이 cp949 로 떨어진다.
    출력 문구의 '—' 같은 문자가 그대로 있으면 30분짜리 빌드가 로그를 남기려다 죽는다."""
    text = (_ROOT / rel).read_text(encoding="utf-8")
    assert "reconfigure(encoding=\"utf-8\"" in text, \
        f"{rel} 에 stdout UTF-8 가드가 없다"


# ── 번들 격리 — "내 컴퓨터에선 되는데" 방지 ────────────────────────────────
def _stub_repo(tmp_path) -> Path:
    """run_build 가 복사할 최소 소스 트리."""
    (tmp_path / "aoi_verification").mkdir(parents=True)
    (tmp_path / "aoi_verification" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "main.py").write_text("x", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("numpy>=1\nPillow>=10\n", encoding="utf-8")
    (tmp_path / "dev").mkdir()
    (tmp_path / "dev" / "양식.xlsx").write_bytes(b"x")
    (tmp_path / "scripts").mkdir()
    return tmp_path


def _prepared_bundle(tmp_path, *, with_packages=True) -> Path:
    """CPython 이 이미 풀린 상태(다운로드 분기를 건너뛰게) + 선택적으로 설치된 패키지."""
    out = tmp_path / "out"
    ppy = portable.portable_python(out)
    ppy.parent.mkdir(parents=True, exist_ok=True)
    ppy.write_bytes(b"x")
    if with_packages:
        sp = portable.site_packages_dir(out)
        sp.mkdir(parents=True, exist_ok=True)
        for dist in ("numpy-2.0.0", "pillow-11.0.0"):
            (sp / f"{dist}.dist-info").mkdir()
    return out


def test_bundled_python_is_never_run_with_user_site(tmp_path):
    """★ 회귀 가드 — 동봉 파이썬이 개발 PC 의 user site 를 보면 pip 이 전 패키지를
    'already satisfied' 로 건너뛰어 **의존성 0 개인 배포본**이 나온다.

    개발 PC 에서는 잘 돌고 사용자 PC 에서는 창도 안 뜨고 죽는다."""
    repo = _stub_repo(tmp_path)
    out = _prepared_bundle(tmp_path)
    ppy = str(portable.portable_python(out))
    calls = []

    portable.run_build(repo, "http://unused", out_dirname="out",
                       run=lambda c, cwd=None: calls.append([str(x) for x in c]) or 0,
                       log=lambda *a: None)

    bundled = [c for c in calls if c and c[0] == ppy]
    assert bundled, "동봉 파이썬을 한 번도 부르지 않았다 — 테스트가 무의미하다"
    # 개발 PC 검사용 가드 한 번만 예외다(일부러 격리를 풀어 user site 를 본다).
    # 나머지 — 특히 pip — 는 전부 격리돼야 한다.
    for cmd in bundled:
        assert cmd[1] == "-s", f"user site 격리 없이 동봉 파이썬 호출: {cmd}"


def test_run_build_guards_only_the_bundle(tmp_path):
    """run_build 는 배포될 번들만 검사한다 — 개발 PC 검사는 무거운 단계 **앞**에서 한다."""
    repo = _stub_repo(tmp_path)
    out = _prepared_bundle(tmp_path)
    ppy = str(portable.portable_python(out))
    calls = []

    portable.run_build(repo, "http://unused", out_dirname="out",
                       run=lambda c, cwd=None: calls.append([str(x) for x in c]) or 0,
                       log=lambda *a: None)

    guards = [c for c in calls if any("verify_no_forbidden" in x for x in c)]
    assert len(guards) == 1, "run_build 는 **번들만** 검사한다(개발 PC 검사는 앞단계)"
    assert "-s" in guards[0], "번들 검사가 개발 PC 의 user site 를 보면 안 된다"


def test_build_fails_when_pip_installed_nothing_into_the_bundle(tmp_path):
    """pip 이 rc=0 을 내도 site-packages 가 비었으면 빌드를 세운다.

    'already satisfied' 로 전부 건너뛴 경우가 정확히 이 상태다."""
    repo = _stub_repo(tmp_path)
    out = _prepared_bundle(tmp_path, with_packages=False)   # 아무것도 안 깔림
    logs = []

    rc = portable.run_build(repo, "http://unused", out_dirname="out",
                            run=lambda c, cwd=None: 0,      # pip 은 성공했다고 거짓말
                            log=logs.append)

    assert rc != 0, "빈 번들인데 빌드가 성공했다"
    joined = "\n".join(logs)
    assert "numpy" in joined and "pillow" in joined          # 무엇이 빠졌는지 알려준다
    # ★ 표식을 남기면 안 된다 — 남기면 사용자 PC 의 자동 업데이트가 '패키지 안 바뀜' 으로
    #   판단해 설치를 영원히 건너뛴다(자가 치유 불가).
    assert not (out / ".deps_installed").exists()


def test_required_dists_needs_no_import_name_mapping(tmp_path):
    """`.dist-info` 이름으로 대조하면 opencv-python→cv2 같은 매핑표가 필요 없다."""
    req = ("# 주석\nopencv-python>=4.8\nPillow>=10.0\nscikit-image>=0.21\n"
           "\ntruststore>=0.8 ; python_version >= \"3.10\"\n")
    got = portable.required_dists(req)
    assert got == ["opencv_python", "pillow", "scikit_image"]
    # 환경 마커가 붙은 줄은 플랫폼 조건부라 '필수' 로 치지 않는다(오탐 방지).
    assert "truststore" not in got


def test_missing_packages_reads_the_bundle_not_the_dev_machine(tmp_path):
    out = _prepared_bundle(tmp_path)
    req = tmp_path / "requirements.txt"
    req.write_text("numpy>=1\nPillow>=10\ntorch>=2\n", encoding="utf-8")
    assert portable.missing_packages(out, req) == ["torch"]
    (portable.site_packages_dir(out) / "torch-2.0.0.dist-info").mkdir()
    assert portable.missing_packages(out, req) == []


def test_import_probe_disables_user_site(tmp_path):
    """프로브가 개발 PC 의 패키지로 거짓 통과하면 아무것도 증명하지 못한다."""
    cmd = build.import_probe_cmd(tmp_path / "out")
    assert cmd[1] == "-s", "프로브가 user site 를 배제하지 않는다"
    assert "torch" in cmd[-1] and "openvino" in cmd[-1]      # 무거운 것까지 확인


def test_build_exe_does_not_guess_when_run_build_already_reported(monkeypatch, tmp_path):
    """run_build 가 정확한 이유를 출력했는데 그 위에 추측을 덮으면 모순 안내가 된다."""
    out = _sandbox_build(monkeypatch, tmp_path)
    monkeypatch.setattr(build, "_load_portable_impl",
                        lambda: type("I", (), {"run_build": staticmethod(
                            lambda *a, **k: 1)}))
    logs = []
    build.build_exe(run=_fake_run([], out), log=logs.append)
    joined = "\n".join(logs)
    assert "[FAILED]" in joined or "확인하세요" in joined
    assert "진단" not in joined, "이미 사유가 나온 자리에서 추측 진단을 덧붙였다"


def test_dev_machine_is_checked_before_the_expensive_steps(monkeypatch, tmp_path):
    """★ 순서 회귀 가드 — 개발 PC 검사가 뒤에 있으면 사용자는 CPython 다운로드와
    2~3 GB pip 설치로 30분 이상을 태운 **뒤에야** 같은 실패를 다시 본다."""
    out = _sandbox_build(monkeypatch, tmp_path)
    monkeypatch.setattr(build, "_load_portable_impl",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("개발 PC 검사 전에 무거운 단계로 갔다")))
    calls = []

    def _run(cmd, cwd=None):
        joined = " ".join(str(x) for x in cmd)
        calls.append(joined)
        return 1 if "verify_no_forbidden" in joined else 0

    monkeypatch.setattr(build, "dev_python", lambda: __import__("sys").executable)
    logs = []
    assert build.build_exe(run=_run, log=logs.append) != 0
    # 가드가 첫 명령들 중 하나여야 한다 — PyInstaller 나 pip 보다 앞.
    assert any("verify_no_forbidden" in c for c in calls)
    assert not any("PyInstaller" in c for c in calls), "런처 빌드까지 갔다"
    assert any("개발 PC" in m for m in logs)


def test_dev_python_points_at_the_venv_base(tmp_path):
    """venv 는 user site 를 안 본다 — 개발 PC 를 보려면 그 venv 의 원본을 봐야 한다."""
    import sys as _sys
    p = build.dev_python()
    assert str(_sys.base_prefix) in p
    assert p.endswith("python.exe") or p.endswith("python3")
