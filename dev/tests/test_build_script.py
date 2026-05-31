"""build.py / portable_build.py — 빌드 스크립트의 순수 로직 + 주입 흐름 테스트.

실제 PyInstaller/다운로드는 주입(injection)으로 분리돼 있어 무거운 의존성 없이 검증한다.
"""

from __future__ import annotations

import importlib.util as _u
from pathlib import Path

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
    assert build.output_path("windows", Path("/r")).name == "AOI_Verify.exe"
    assert build.output_path("portable", Path("/r")).name == "dist_portable"


def test_main_usage_and_unknown(capsys):
    assert build.main([]) == 0                      # 비대화형 → 사용법 출력
    assert "online" in capsys.readouterr().out
    assert build.main(["nope"]) == 2                # 알 수 없는 종류


def test_prompt_kind_selection():
    # VS Code ▶ 처럼 인자 없이 실행 시 번호/이름으로 빌드 종류 선택.
    assert build._prompt_kind(input_fn=lambda _: "1") == "online"
    assert build._prompt_kind(input_fn=lambda _: "2") == "portable"
    assert build._prompt_kind(input_fn=lambda _: "3") == "windows"
    assert build._prompt_kind(input_fn=lambda _: "windows") == "windows"
    assert build._prompt_kind(input_fn=lambda _: "") is None       # Enter=취소
    assert build._prompt_kind(input_fn=lambda _: "9") is None      # 범위 밖


def test_build_online_injected_flow():
    calls = []
    rc = build.build_online(run=lambda c, cwd=None: calls.append(
        " ".join(str(x) for x in c)) or 0, log=lambda *a: None)
    assert rc == 0
    joined = "\n".join(calls)
    assert "online.spec" in joined                  # 올바른 spec
    assert "verify_no_forbidden.py" in joined        # 보안 가드
    assert "pyinstaller>=6" in joined


def test_build_windows_uses_full_spec_and_requirements():
    calls = []
    build.build_windows(run=lambda c, cwd=None: calls.append(
        " ".join(str(x) for x in c)) or 0, log=lambda *a: None)
    joined = "\n".join(calls)
    assert "aoi_verification.spec" in joined         # 전부 동봉 spec
    assert "requirements.txt" in joined              # 의존성 설치


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
