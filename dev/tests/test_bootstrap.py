"""온라인 부트스트래퍼(launcher exe 핵심) — 순수 로직 헤드리스 테스트.

네트워크/프로세스 실행은 주입(injection)으로 분리돼 있어 무거운 의존성 없이 검증한다.
"""

from __future__ import annotations

from pathlib import Path

from aoi_verification.app.utils import bootstrap as bs


def test_data_root_prefers_localappdata(tmp_path):
    r = bs.data_root({"LOCALAPPDATA": str(tmp_path)})
    assert r == tmp_path / bs.APP_DIRNAME
    assert bs.APP_DIRNAME == "AOI Recipe Verification"   # 설치 폴더 이름
    # LOCALAPPDATA 없으면 HOME 아래 숨김 폴더.
    r2 = bs.data_root({"HOME": str(tmp_path)})
    assert r2 == tmp_path / ("." + bs.APP_DIRNAME)


def test_cache_root_honors_data_home(tmp_path, monkeypatch):
    # AOI_DATA_HOME 이 지정되면 캐시가 그 폴더 안(<home>/cache)에 담긴다(설치 폴더 일원화).
    from aoi_verification.app.utils import paths
    monkeypatch.setenv("AOI_DATA_HOME", str(tmp_path / "install"))
    assert paths.cache_root() == tmp_path / "install" / "cache"
    # 미지정이면 사용자 홈의 기본 캐시.
    monkeypatch.delenv("AOI_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    assert paths.cache_root() == tmp_path / "h" / ".aoi_verification_cache"


def test_app_is_present(tmp_path):
    assert not bs.app_is_present(tmp_path)
    (tmp_path / "main.py").write_text("x", encoding="utf-8")
    assert not bs.app_is_present(tmp_path)            # 패키지 폴더 아직 없음
    (tmp_path / "aoi_verification").mkdir()
    assert bs.app_is_present(tmp_path)


def test_deps_marker_tracks_requirements_change(tmp_path):
    assert bs.deps_installed(tmp_path, "numpy==1\n") is False
    bs.write_deps_marker(tmp_path, "numpy==1\n")
    assert bs.deps_installed(tmp_path, "numpy==1\n") is True
    # requirements 가 바뀌면 재설치 필요(표식 불일치).
    assert bs.deps_installed(tmp_path, "numpy==2\n") is False
    # req_text 가 None(파일 없음)이면 표식 존재만으로 통과.
    assert bs.deps_installed(tmp_path, None) is True


def test_target_python_prefers_bundled(tmp_path):
    # 번들 파이썬이 있으면 그것을 쓴다.
    pdir = tmp_path / "python"
    pdir.mkdir()
    (pdir / "bin").mkdir()
    (pdir / "bin" / "python3").write_text("", encoding="utf-8")
    assert bs.target_python(tmp_path, frozen=True, sys_executable="/x/py").endswith("python3")
    # 번들 없고 frozen 이면 시스템 python 위임.
    assert bs.target_python(tmp_path / "empty", frozen=True, sys_executable="/x/py") == "python"
    # 개발 실행(frozen 아님)은 현재 인터프리터.
    assert bs.target_python(tmp_path / "e2", frozen=False, sys_executable="/x/py") == "/x/py"


def test_pip_and_launch_cmds(tmp_path):
    req = tmp_path / "requirements.txt"
    assert bs.pip_install_cmd("py", req)[:4] == ["py", "-m", "pip", "install"]
    assert str(req) in bs.pip_install_cmd("py", req)
    assert bs.launch_cmd("py", tmp_path / "main.py") == ["py", str(tmp_path / "main.py")]


def test_bootstrap_full_flow_injected(tmp_path):
    """앱 없음 → fetch → pip → launch 순서와 종료코드를 가짜 주입으로 검증."""
    root = tmp_path / "app"
    calls = []

    def fetch_app(dest: Path) -> bool:
        (dest / "main.py").write_text("print(1)", encoding="utf-8")
        (dest / "aoi_verification").mkdir(parents=True, exist_ok=True)
        (dest / "requirements.txt").write_text("numpy==1\n", encoding="utf-8")
        calls.append("fetch")
        return True

    def run(cmd):
        calls.append(("pip" if "pip" in cmd else "launch"))
        return 0

    rc = bs.bootstrap(root, repo="o/r", branch="b",
                      fetch_app=fetch_app, run=run, frozen=True)
    assert rc == 0
    assert calls == ["fetch", "pip", "launch"]        # 받고 → 설치 → 실행
    assert bs.deps_installed(root, "numpy==1\n")      # 표식 기록됨

    # 두 번째 실행: 앱·의존성 이미 있음 → fetch/pip 생략, launch 만.
    calls.clear()
    rc2 = bs.bootstrap(root, repo="o/r", branch="b",
                       fetch_app=fetch_app, run=run, frozen=True)
    assert rc2 == 0 and calls == ["launch"]


def test_bootstrap_fetch_failure_returns_error(tmp_path):
    rc = bs.bootstrap(tmp_path / "x", repo="o/r", branch="b",
                      fetch_app=lambda d: False, run=lambda c: 0, frozen=True)
    assert rc == 3                                    # 다운로드 실패 코드
