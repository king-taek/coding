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


def test_req_lines_ignores_comments_and_blanks():
    """주석/빈 줄 변경을 '의존성 변경' 으로 오인하면 exe 배포에서 업데이트가 통째로 막힌다."""
    assert bs.req_lines("# 설명\nnumpy==1\n\n  \ntorch>=2  # 왜\n") == [
        "numpy==1", "torch>=2"]
    # 같은 요구사항이면 주석이 달라도 같은 지문 → '안 바뀜' 으로 판정된다.
    assert bs.deps_installed.__doc__ is not None
    a, b = "numpy==1\n", "# 새 주석\nnumpy==1\n\n"
    assert bs._req_fingerprint(a) == bs._req_fingerprint(b)
    assert bs._req_fingerprint(a) != bs._req_fingerprint("numpy==2\n")


def test_results_dir_moves_out_of_app_folder_for_exe_installs(tmp_path, monkeypatch):
    """exe 설치에서 결과 엑셀은 app\\ 바깥에 저장돼야 한다.

    자동 업데이트가 app\\ 을 통째로 새 트리로 교체하므로, 안에 두면 결과가 사라진다."""
    from aoi_verification.app.utils import paths
    monkeypatch.setenv("AOI_APP_HOME", str(tmp_path))
    d = paths.results_dir()
    assert d == tmp_path / "결과"
    assert d.is_dir()
    assert "app" not in d.parts[:-1] or d.parent == tmp_path


def test_bundled_torch_home_only_when_present(tmp_path, monkeypatch):
    """동봉 가중치가 있을 때만 TORCH_HOME 을 돌린다(개발/포터블은 기존 동작 유지)."""
    from aoi_verification.app.utils import paths
    monkeypatch.delenv("AOI_APP_HOME", raising=False)
    assert paths.bundled_torch_home() is None
    monkeypatch.setenv("AOI_APP_HOME", str(tmp_path))
    assert paths.bundled_torch_home() is None          # 폴더가 아직 없다
    (tmp_path / "runtime" / "torch").mkdir(parents=True)
    assert paths.bundled_torch_home() == tmp_path / "runtime" / "torch"


def test_results_stay_outside_app_even_without_the_env_var(tmp_path, monkeypatch):
    """★ 백신 때문에 run_aoi.bat 으로 켜면 AOI_APP_HOME 이 없다.

    그때 결과가 app\\결과 로 떨어지면, 다음 업데이트에서 런처가 app.old 를 지울 때
    사용자 결과 엑셀이 통째로 사라진다. 레이아웃으로도 설치 루트를 알아내야 한다."""
    from aoi_verification.app.utils import paths
    monkeypatch.delenv("AOI_APP_HOME", raising=False)
    (tmp_path / "AOI_Verify.exe").write_bytes(b"x")     # exe 배포본의 지문
    (tmp_path / "python").mkdir()
    monkeypatch.setattr(paths, "_project_root", lambda: tmp_path / "app")

    assert paths._exe_install_root() == tmp_path
    assert paths.results_dir() == tmp_path / "결과"      # app\ 바깥


def test_layout_detection_does_not_fire_for_dev_or_portable(tmp_path, monkeypatch):
    """개발·포터블에는 AOI_Verify.exe 가 없다 — 오탐하면 결과 위치가 엉뚱해진다."""
    from aoi_verification.app.utils import paths
    monkeypatch.delenv("AOI_APP_HOME", raising=False)
    (tmp_path / "python").mkdir()                        # 포터블: python\ 은 있지만 exe 없음
    monkeypatch.setattr(paths, "_project_root", lambda: tmp_path / "app")
    assert paths._exe_install_root() is None
    assert paths.results_dir() == tmp_path / "app" / "결과"


def test_updater_install_root_stays_env_only():
    """★ updater 는 레이아웃으로 판정하면 안 된다.

    거기서 묻는 것은 '설치 루트가 어디냐' 가 아니라 '교체해 줄 런처가 있느냐' 다.
    포터블에는 런처가 없으므로, 레이아웃으로 판정하면 업데이트가 app.new 에 고여
    영원히 적용되지 않는다."""
    from pathlib import Path as _P
    src = (_P(__file__).resolve().parents[2] / "aoi_verification" / "app" /
           "utils" / "updater.py").read_text(encoding="utf-8")
    i = src.index("def _install_root(")
    body = src[i:i + 600]
    assert "_APP_HOME_ENV" in body
    assert "_exe_install_root" not in body, "updater 가 레이아웃 판정을 끌어다 썼다"


def test_run_bats_isolate_from_the_pcs_own_packages():
    """bat 실행 경로도 번들만 쓰게 해야 한다 — 그리고 이 파일들은 app\\ 바깥이라
    자동 업데이트로 고칠 수 없다(재빌드·재배포가 필요하다)."""
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[2] / "scripts"
    for name in ("run_aoi.bat", "run_aoi_debug.bat"):
        text = (root / name).read_text(encoding="utf-8")
        assert "PYTHONNOUSERSITE=1" in text, name
        # 포터블도 같은 파일을 쓴다 — AOI_APP_HOME 을 **설정하면** 포터블 업데이트가
        # app.new 에 고여 영원히 적용되지 않는다.  (주석에 이름이 나오는 건 무방하다.)
        sets = [ln for ln in text.splitlines()
                if ln.strip().lower().startswith("set ")]
        assert not any("AOI_APP_HOME" in ln for ln in sets), \
            f"{name} 이 AOI_APP_HOME 을 설정해 포터블 업데이트를 망가뜨린다"
