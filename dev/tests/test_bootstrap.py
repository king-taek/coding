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
    # ★ `HOME` 만 바꾸면 Windows 에서는 `Path.home()` 이 꿈쩍도 하지 않는다
    #   (`USERPROFILE` 을 먼저 본다) — 두 변수를 함께 옮긴다.  conftest 의
    #   `set_home` 과 같은 이유이고, 이 테스트가 Windows 에서 늘 실패하던 원인이다.
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "h"))
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


def test_bundled_ir_dir_follows_the_app_tree(tmp_path, monkeypatch):
    """IR 은 **앱 트리 루트** 기준으로 찾는다 — 네 배포가 같은 규칙을 쓴다.

    설치 루트 기준으로 두면 온라인 배포(앱을 GitHub 에서 받아 푸는 형태)가 못 찾는다."""
    from aoi_verification.app.utils import paths
    monkeypatch.setattr(paths, "_project_root", lambda: tmp_path)
    assert paths.bundled_ir_dir() is None              # 폴더가 아직 없다
    (tmp_path / "runtime" / "ir").mkdir(parents=True)
    assert paths.bundled_ir_dir() == tmp_path / "runtime" / "ir"


def test_ir_path_requires_both_xml_and_bin(tmp_path, monkeypatch):
    """★ ``.bin``(가중치) 이 없으면 read_model 이 조용히 엉뚱한 모델을 줄 수 있다.

    그건 '느리다' 가 아니라 **틀린 임베딩**이므로, 반쪽짜리 IR 은 아예 없는 것으로
    취급해 CPU 고전으로 폴백해야 한다."""
    from aoi_verification.app.learning import embedder_openvino as ov
    from aoi_verification.app.utils import paths
    monkeypatch.setattr(paths, "_project_root", lambda: tmp_path)
    d = tmp_path / "runtime" / "ir"
    d.mkdir(parents=True)
    kind = ov.MODEL_MOBILENET_V3
    assert ov.ir_path(kind) is None                    # 아무것도 없다
    (d / f"{kind}.xml").write_text("<net/>", encoding="utf-8")
    assert ov.ir_path(kind) is None, ".bin 없이 통과하면 안 된다"
    (d / f"{kind}.bin").write_bytes(b"x")
    assert ov.ir_path(kind) == d / f"{kind}.xml"
    # 지원하지 않는 백본은 파일이 있어도 None (호출부가 CPU 로 폴백).
    assert ov.ir_path("nope") is None


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


# ── lite 배포의 첫 실행 설치 (bootstrap.ensure_deps) ───────────────────────────
def _req(tmp_path, text="numpy>=1\n"):
    f = tmp_path / "requirements.txt"
    f.write_text(text, encoding="utf-8")
    return f


def test_ensure_deps_installs_once_then_never_again(tmp_path):
    """표식이 없으면 설치하고 남긴다.  두 번째부터는 pip 를 부르지 않는다.

    매 실행마다 수백 MB 를 다시 받으면 사용자는 앱이 고장 났다고 생각한다."""
    req = _req(tmp_path)
    calls = []
    assert bs.ensure_deps(tmp_path, "py", req, run=lambda c: calls.append(c) or 0)
    assert len(calls) == 1 and "install" in calls[0]
    assert bs.deps_marker(tmp_path).exists()

    calls.clear()
    assert bs.ensure_deps(tmp_path, "py", req, run=lambda c: calls.append(c) or 0)
    assert calls == []


def test_ensure_deps_does_not_mark_a_failed_install(tmp_path):
    """★ 실패했는데 표식을 남기면 다음 실행이 '설치됐다' 고 보고 **영원히** 재시도하지
    않는다.  사용자는 ImportError 만 보고, 되돌릴 방법이 없다."""
    req = _req(tmp_path)
    assert bs.ensure_deps(tmp_path, "py", req, run=lambda c: 1) is False
    assert not bs.deps_marker(tmp_path).exists()
    # 다음 실행에서 다시 시도한다.
    calls = []
    assert bs.ensure_deps(tmp_path, "py", req, run=lambda c: calls.append(c) or 0)
    assert len(calls) == 1


def test_ensure_deps_never_upgrades(tmp_path):
    """``--upgrade`` 는 잘 돌던 무거운 패키지까지 최신으로 끌어올린다(CLAUDE.md)."""
    req = _req(tmp_path)
    calls = []
    bs.ensure_deps(tmp_path, "py", req, run=lambda c: calls.append(c) or 0)
    assert "--upgrade" not in calls[0]


def test_ensure_deps_reinstalls_when_requirements_change(tmp_path):
    """업데이트로 패키지가 바뀌면 다시 설치해야 한다(표식 지문이 달라진다)."""
    req = _req(tmp_path, "numpy>=1\n")
    assert bs.ensure_deps(tmp_path, "py", req, run=lambda c: 0)
    req.write_text("numpy>=1\nPillow>=10\n", encoding="utf-8")
    calls = []
    assert bs.ensure_deps(tmp_path, "py", req, run=lambda c: calls.append(c) or 0)
    assert len(calls) == 1
    # 주석만 바뀐 경우는 재설치하지 않는다.
    req.write_text("# 설명\nnumpy>=1\nPillow>=10\n", encoding="utf-8")
    calls.clear()
    assert bs.ensure_deps(tmp_path, "py", req, run=lambda c: calls.append(c) or 0)
    assert calls == []


def test_ensure_deps_is_a_no_op_without_a_requirements_file(tmp_path):
    """개발 트리 등 판단 근거가 없으면 아무것도 하지 않고 진행한다."""
    calls = []
    assert bs.ensure_deps(tmp_path, "py", tmp_path / "없음.txt",
                          run=lambda c: calls.append(c) or 0)
    assert calls == []


def test_first_run_install_does_not_need_heavy_imports():
    """★ 첫 실행에는 PyQt6·numpy 가 아직 **없다.**

    설치를 준비하는 코드가 그것들을 끌고 오면, 설치하기도 전에 ImportError 로 죽어
    사용자가 영원히 앱을 못 켠다.  main.py 가 쓰는 두 모듈은 표준 라이브러리만 쓴다."""
    import ast
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[2] / "aoi_verification" / "app" / "utils"
    heavy = {"numpy", "cv2", "PyQt6", "openvino", "skimage", "PIL", "psutil"}
    for name in ("bootstrap.py", "paths.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                mods.add(n.module.split(".")[0])
        assert not (mods & heavy), f"{name} 이 무거운 의존성을 끌어온다: {mods & heavy}"


# ── 완전 온라인 배포 — 파이썬까지 받아온다 ─────────────────────────────────
def _tar_of(tmp_path, member_rel: str) -> bytes:
    """``member_rel`` 하나가 든 tar.gz 바이트 — 받아 푸는 흐름을 흉내낸다."""
    import io
    import tarfile
    import os as _os
    stage = tmp_path / "_stage" / Path(member_rel).parent
    stage.mkdir(parents=True, exist_ok=True)
    f = tmp_path / "_stage" / member_rel
    # ★ 1 MB 를 넘겨야 한다 — 그 미만은 '프록시 차단 페이지' 로 걸러진다.
    #   압축돼도 남게 랜덤 바이트를 쓴다.
    f.write_bytes(_os.urandom(2 * 1024 * 1024))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        tf.add(str(f), arcname=member_rel)
    return buf.getvalue()


def _member_for_this_os() -> str:
    import os
    return "python/python.exe" if os.name == "nt" else "python/bin/python3"


def test_ensure_python_downloads_and_extracts(tmp_path):
    """온라인 배포의 첫 실행 — 사용자 PC 에 파이썬이 없어도 되게 만드는 단계."""
    blob = _tar_of(tmp_path, _member_for_this_os())
    root = tmp_path / "install"
    seen = []

    def fetch(url, dst):
        seen.append(url)
        dst.write_bytes(blob)

    assert bs.ensure_python(root, "http://x/py.tar.gz", fetch=fetch) is True
    assert bs.python_is_present(root)
    assert seen == ["http://x/py.tar.gz"]
    assert not (root / "python.tar.gz").exists(), "받은 압축 파일을 안 지웠다"


def test_ensure_python_is_a_no_op_when_already_there(tmp_path):
    """두 번째 실행부터는 다시 받지 않는다(수십 MB)."""
    root = tmp_path / "install"
    (root / "python" / "bin").mkdir(parents=True)
    (root / "python" / "bin" / "python3").write_text("", encoding="utf-8")
    (root / "python" / "python.exe").write_text("", encoding="utf-8")
    called = []
    assert bs.ensure_python(root, "http://x", fetch=lambda u, d: called.append(u))
    assert called == []


def test_ensure_python_rejects_a_proxy_block_page(tmp_path):
    """★ 회사 프록시가 차단 페이지(HTML)를 200 으로 돌려주면 '성공' 처럼 보인다.

    크기로 걸러내지 않으면 다음 단계에서 정체불명의 압축 해제 오류만 남는다."""
    root = tmp_path / "install"
    logs = []
    ok = bs.ensure_python(root, "http://x", log=logs.append,
                          fetch=lambda u, d: d.write_bytes(b"<html>blocked</html>"))
    assert ok is False
    assert any("차단" in m or "너무 작" in m for m in logs)
    assert not (root / "python.tar.gz").exists()       # 잔재를 남기지 않는다


def test_ensure_python_reports_download_failure(tmp_path):
    logs = []

    def boom(url, dst):
        raise OSError("네트워크 없음")

    assert bs.ensure_python(tmp_path, "http://x", log=logs.append, fetch=boom) is False
    assert any("다운로드 실패" in m for m in logs)


def test_online_boot_order_python_then_app_then_deps(tmp_path):
    """완전 온라인의 첫 실행 순서 — 파이썬 → 앱 → 라이브러리 → 실행."""
    root = tmp_path / "install"
    steps = []

    def fake_ensure_python(r, url, log=None):
        steps.append("python")
        (Path(r) / "python" / "bin").mkdir(parents=True, exist_ok=True)
        (Path(r) / "python" / "bin" / "python3").write_text("", encoding="utf-8")
        return True

    def fetch_app(dest: Path) -> bool:
        steps.append("app")
        (dest / "main.py").write_text("print(1)", encoding="utf-8")
        (dest / "aoi_verification").mkdir(parents=True, exist_ok=True)
        (dest / "requirements.txt").write_text("numpy==1\n", encoding="utf-8")
        return True

    def run(cmd):
        steps.append("pip" if "pip" in cmd else "launch")
        return 0

    rc = bs.bootstrap(root, repo="o/r", branch="b", fetch_app=fetch_app, run=run,
                      frozen=True, python_url="http://x/py.tar.gz",
                      ensure_python_fn=fake_ensure_python)
    assert rc == 0
    assert steps == ["python", "app", "pip", "launch"]


def test_online_boot_stops_when_python_cannot_be_prepared(tmp_path):
    """파이썬을 못 받으면 앱을 받으러 가지 않는다 — 어차피 실행할 수 없다."""
    steps = []
    rc = bs.bootstrap(tmp_path, repo="o/r", branch="b",
                      fetch_app=lambda d: steps.append("app") or True,
                      run=lambda c: steps.append("run") or 0,
                      frozen=True, python_url="http://x",
                      ensure_python_fn=lambda *a, **k: False)
    assert rc == 2 and steps == []


def test_the_cpython_url_has_one_source_of_truth():
    """빌드와 온라인 launcher 가 같은 주소를 써야 한다 — 두 곳에 적으면 어긋난다."""
    import importlib.util as u
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[2]
    spec = u.spec_from_file_location("build", str(root / "scripts" / "build.py"))
    build = u.module_from_spec(spec)
    spec.loader.exec_module(build)
    assert build.PY_STANDALONE_URL == bs.PY_STANDALONE_URL
    assert bs.PY_STANDALONE_URL.startswith("https://")
