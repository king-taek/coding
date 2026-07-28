"""portable_build.py — 자체 포함 CPython 포터블 폴더 빌드(파이썬 구현).

make_portable.bat 의 파이썬 버전.  python-build-standalone 의 install_only 런타임을
받아 ``dist_portable/python`` 에 풀고, 의존성 설치 + 앱 소스 복사 + 런처/업데이트
스크립트 동봉 + VERSION 스탬프를 만든다.  결과: ``dist_portable/`` (대상 PC 에서
``run_aoi.bat`` 더블클릭, 파이썬 불필요).

순수 경로/명령 구성은 부수효과 없이 분리해 테스트 가능하게 했고, 실제 다운로드/압축/
복사 같은 무거운 부수효과만 ``run_build`` 가 수행한다(보통 Windows 에서 실행).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

# 한글 출력이 깨지지 않도록 stdout/stderr 를 UTF-8 로(Windows cp949 대비).
# build.py 가 import 해서 쓸 때는 부모의 가드가 적용되지만, make_portable.bat 으로
# 직접 실행되는 경로도 있어 여기에도 둔다.  없으면 로그를 파일로 남기려는 순간
# UnicodeEncodeError 로 빌드가 죽는다(출력 문구의 '—' 같은 문자).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

OUT_DIRNAME = "dist_portable"
REPO_SLUG = "king-taek/coding"


def _default_branch_name() -> str:
    """git 정보를 못 얻었을 때 VERSION 에 적을 브랜치.

    ``updater.DEFAULT_BRANCH`` 를 **그대로 재사용**한다 — 같은 상수를 두 곳에 적으면
    기본 브랜치를 바꿀 때 한쪽만 고쳐져 어긋난다(CLAUDE.md 의 자동 업데이트 불변식).
    빌드 스크립트에서 앱 패키지를 못 읽는 상황도 있으므로 방어적으로 감싼다."""
    try:
        import sys
        root = Path(__file__).resolve().parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from aoi_verification.app.utils.updater import DEFAULT_BRANCH
        return DEFAULT_BRANCH
    except Exception:
        return ""


def portable_python(out_dir: Path) -> Path:
    """포터블 폴더 안 CPython 실행 파일 경로."""
    if os.name == "nt":
        return out_dir / "python" / "python.exe"
    return out_dir / "python" / "bin" / "python3"


def _import_bootstrap():
    """앱의 ``bootstrap`` 모듈 — requirements 정규화를 앱과 공유하기 위해.

    같은 정규화가 두 곳에 생기면 어긋난다(그러면 빌드와 자동 업데이트의 판단이 갈린다)."""
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from aoi_verification.app.utils import bootstrap
    return bootstrap


def _isolated(python_exe: Path, *args) -> list:
    """동봉 파이썬을 **개발 PC 로부터 격리해** 실행하는 명령.

    ``-s`` 는 user site-packages(``%APPDATA%\\Python\\Python311\\site-packages``)를
    ``sys.path`` 에서 뺀다.  이게 없으면 동봉 파이썬이 개발 PC 의 패키지를 보고,
    pip 이 전부 'already satisfied' 로 건너뛰어 **번들이 빈 채로 남는다**(개발 PC 에서는
    잘 돌고 사용자 PC 에서는 전부 ImportError).  덤으로 pip 이 스스로 ``--user`` 설치로
    전환하는 것도 막는다(``site.ENABLE_USER_SITE`` 가 False 면 non-user 로 고정).

    ``-I`` 를 쓰면 안 된다 — ``-E`` 를 함께 걸어 ``PYTHONUTF8``·``PYTHONIOENCODING`` 까지
    무시하게 되고, 이 저장소는 그 인코딩 문제로 이미 두 번 데였다.
    ``pip --isolated`` 는 설정파일만 무시하고 ``sys.path`` 는 그대로라 무효다."""
    return [str(python_exe), "-s", *[str(a) for a in args]]


def site_packages_dir(out: Path) -> Path:
    """번들 파이썬의 site-packages 경로(OS 별)."""
    if os.name == "nt":
        return Path(out) / "python" / "Lib" / "site-packages"
    base = Path(out) / "python" / "lib"
    cands = sorted(base.glob("python3.*")) if base.is_dir() else []
    return (cands[0] if cands else base / "python3") / "site-packages"


def _normalize_dist(name: str) -> str:
    """배포명 정규화(PEP 503) — ``opencv-python`` → ``opencv_python``."""
    import re
    return re.sub(r"[-_.]+", "_", name).strip().lower()


def required_dists(req_text: str) -> list:
    """requirements 본문에서 **배포명만** 뽑아 정규화한다.

    ``.dist-info`` 폴더명이 배포명과 같은 이름공간이라, 이걸로 대조하면
    import 명 매핑표(opencv-python→cv2, Pillow→PIL …)가 아예 필요 없다.
    환경 마커(``; python_version >= "3.10"``)가 붙은 줄은 플랫폼별 조건부라 제외한다
    — 없다고 실패로 치면 오탐이 난다."""
    import re
    bootstrap = _import_bootstrap()          # 주석·빈 줄 제거를 앱과 **같은 함수**로

    out = []
    for line in bootstrap.req_lines(req_text):
        if ";" in line:                     # 환경 마커 — 조건부라 필수로 보지 않는다
            continue
        name = re.split(r"[<>=!~\[]", line, 1)[0]
        name = _normalize_dist(name)
        if name:
            out.append(name)
    return out


def missing_packages(out: Path, req_file: Path) -> list:
    """번들에 **실제로 설치되지 않은** 배포명 목록.  비어 있으면 정상.

    pip 의 rc 는 믿을 수 없다 — 개발 PC 의 패키지를 보고 'already satisfied' 로
    건너뛰어도 0 을 반환한다.  그래서 디스크의 ``.dist-info`` 를 직접 센다."""
    sp = site_packages_dir(out)
    if not sp.is_dir():
        try:
            req_text = Path(req_file).read_text(encoding="utf-8")
        except OSError:
            return ["(site-packages 없음)"]
        return required_dists(req_text) or ["(site-packages 없음)"]
    have = {_normalize_dist(p.name.split("-")[0])
            for p in sp.glob("*.dist-info")}
    try:
        req_text = Path(req_file).read_text(encoding="utf-8")
    except OSError:
        return []
    return [n for n in required_dists(req_text) if n not in have]


def version_stamp(sha: str, branch: str, repo: str = REPO_SLUG) -> str:
    """app/VERSION 에 기록할 JSON 문자열(자동 업데이트 식별자)."""
    return json.dumps({"sha": sha, "branch": branch, "repo": repo})


def _git_head(repo_root: Path) -> tuple[str, str]:
    """현재 커밋 SHA·브랜치(없으면 빈 문자열)."""
    def _q(args):
        try:
            return subprocess.check_output(
                ["git", "-C", str(repo_root), *args],
                stderr=subprocess.DEVNULL, timeout=5).decode().strip()
        except Exception:
            return ""
    return _q(["rev-parse", "HEAD"]), _q(["rev-parse", "--abbrev-ref", "HEAD"])


def run_build(repo_root: Path, py_url: str,
              run: Callable = None, log: Callable = print,
              out_dirname: str = OUT_DIRNAME,
              bats: tuple = ("run_aoi.bat", "run_aoi_debug.bat", "update_app.bat"),
              torch_home: bool = False) -> int:
    """포터블 빌드 수행.  ``run`` 은 명령 실행기(주입 가능).  반환: 0=성공.

    ``out_dirname``/``bats``/``torch_home`` 은 'exe + app 폴더' 빌드가 이 함수를 그대로
    재사용하기 위한 것이다(레이아웃이 같다 — ``python\\`` + ``app\\``).  exe 빌드는
    산출물 폴더가 ``dist/AOI_Verify`` 이고, 병합식 ``update_app.bat`` 은 동봉하지 않으며
    (하는 일이 옛 미러링 버그 그 자체다), 모델 가중치를 함께 받아 둔다."""
    if run is None:
        def run(cmd, cwd=None):
            log(">> " + " ".join(str(c) for c in cmd))
            return subprocess.call([str(c) for c in cmd],
                                   cwd=str(cwd) if cwd else None)

    out = repo_root / out_dirname
    out.mkdir(parents=True, exist_ok=True)
    ppy = portable_python(out)

    # 1) 자체 포함 CPython 준비(이미 있으면 재사용).
    if ppy.exists():
        log(f"[1/4] reuse existing {ppy}")
    else:
        log(f"[1/4] downloading CPython: {py_url}")
        tgz = out / "python.tar.gz"
        try:
            import urllib.request
            urllib.request.urlretrieve(py_url, str(tgz))
        except Exception as exc:
            log(f"[FAILED] download error: {exc}")
            return 1
        if tgz.stat().st_size < 1_000_000:
            # 프록시가 차단 페이지(HTML)를 200 으로 돌려주면 urlretrieve 는 '성공' 으로
            # 보므로, URL 이 멀쩡한데도 여기로 온다.  URL 만 의심하게 만들면 안 된다.
            log("[FAILED] downloaded file too small — 회사 프록시가 차단 페이지를 "
                "돌려줬거나 다운로드가 잘렸습니다.")
            log(f"         받은 파일: {tgz} ({tgz.stat().st_size} bytes)")
            log("         브라우저로 아래 주소가 받아지는지 확인하세요:")
            log(f"         {py_url}")
            return 1
        log("       extracting ...")
        import tarfile
        try:
            with tarfile.open(str(tgz)) as tf:
                tf.extractall(str(out))
        except Exception as exc:
            # 여기서 raw traceback 이 나면 원인이 네트워크인지 디스크인지 알 수 없다.
            log(f"[FAILED] extract error: {exc}")
            log("         받은 파일이 손상됐거나(차단 페이지) 디스크·경로 길이 문제입니다.")
            return 1
        tgz.unlink(missing_ok=True)
    if not ppy.exists():
        log(f"[FAILED] {ppy} not found after extract")
        return 1

    # 2) 의존성 설치 + 보안 가드.
    log("[2/4] installing dependencies (torch/openvino — takes a while) ...")
    # ↓ 실패해도 아무 말 없이 rc=1 만 돌려주면, 30분 돌린 사용자는 pip 출력 수백 줄
    #   끝에서 '왜 멈췄는지' 를 알 수 없다.  어느 단계인지 반드시 말한다.
    if run(_isolated(ppy, "-m", "pip", "install", "--upgrade", "pip")) != 0:
        log("[FAILED] pip 자체 업그레이드에 실패했습니다(네트워크/프록시 확인).")
        return 1
    if run(_isolated(ppy, "-m", "pip", "install", "-r",
                     repo_root / "requirements.txt")) != 0:
        log("[FAILED] 의존성 설치에 실패했습니다 (requirements.txt).")
        log("         위 pip 출력의 마지막 오류를 확인하세요. 네트워크·프록시·디스크 "
            "여유 공간이 흔한 원인입니다.")
        log("         python\\ 런타임은 남아 있으므로 다시 실행하면 이어서 진행합니다.")
        return 1

    # ★ pip 이 rc=0 을 냈다고 설치가 된 것이 아니다.  개발 PC 의 user site 가 보이면
    #   전 패키지가 'already satisfied' 로 넘어가고 번들은 빈 채로 남는다(-s 로 막지만,
    #   막혔는지를 **사실로** 확인한다).  이걸 안 보면 의존성 0 개인 배포본이 나가고,
    #   사용자 PC 에서는 창도 안 뜨고 죽는다.
    missing = missing_packages(out, repo_root / "requirements.txt")
    if missing:
        log("[FAILED] 번들에 패키지가 설치되지 않았습니다: " + ", ".join(missing))
        log(f"         확인 위치: {site_packages_dir(out)}")
        log("         pip 이 개발 PC 의 패키지를 보고 '이미 설치됨' 으로 건너뛰었을 수 "
            "있습니다.")
        log("         (이대로 배포하면 사용자 PC 에서 앱이 아예 뜨지 않습니다.)")
        return 1

    guard = repo_root / "scripts" / "internal" / "verify_no_forbidden.py"
    # 배포되는 번들 자체를 검사한다(개발 PC 의 user site 는 -s 로 배제).
    if run(_isolated(ppy, guard)) != 0:
        log("[FAILED] 보안 가드 — **배포될 번들** 에 금지 패키지가 있습니다.")
        log("         위 [금지] 메시지의 안내를 따르세요.")
        return 1
    # 개발 PC 환경도 검사한다(user site 포함).  배포물에는 안 들어가지만 회사 정책 대상이다.
    if run([str(ppy), str(guard)]) != 0:
        log("[FAILED] 보안 가드 — **개발 PC 환경** 에 금지 패키지가 있습니다.")
        log("         배포본에는 들어가지 않지만 회사 정책상 제거해야 합니다.")
        log("         위 [금지] 메시지가 알려주는 경로·명령을 그대로 쓰세요.")
        return 1

    # 이 번들이 **어떤 requirements 로 만들어졌는지** 표식을 남긴다.  자동 업데이트가
    # 새 requirements 를 받았을 때 '동봉된 python\ 으로 감당되는가' 를 이걸로 판단한다.
    # ★ 반드시 위의 실설치 확인을 통과한 뒤에 쓴다 — 표식이 거짓이면 사용자 PC 의 자동
    #   업데이트가 '패키지 안 바뀜' 으로 판단해 설치를 영원히 건너뛴다(자가 치유 불가).
    if not _write_deps_marker(repo_root, out):
        log("[FAILED] 의존성 표식(.deps_installed)을 남기지 못했습니다.")
        log("         이대로 배포하면 자동 업데이트가 패키지 변경을 감지하지 못합니다.")
        return 1

    # 모델 가중치 동봉(exe 배포용) — 첫 매칭 때 download.pytorch.org 에서 받게 돼 있는데
    # 사내망은 막혀 있을 수 있다.  app\ 바깥(runtime\torch)에 둬 업데이트 교체에 안 쓸린다.
    if torch_home:
        log("       fetching torchvision weights (bundled, ~55 MB) ...")
        if run(_isolated(ppy, "-c", _TORCH_FETCH_SRC,
                         out / "runtime" / "torch")) != 0:
            log("[FAILED] could not fetch model weights — 사내망 PC 에서 매칭이 "
                "동작하지 않을 수 있습니다.")
            return 1

    # 3) 앱 소스 + 리소스 + 런처 스크립트 복사.
    log("[3/4] copying app source ...")
    app = out / "app"
    app.mkdir(parents=True, exist_ok=True)
    _copytree(repo_root / "aoi_verification", app / "aoi_verification")
    shutil.copy2(repo_root / "main.py", app / "main.py")
    # requirements.txt 도 함께 둔다 — 업데이트가 새 것과 비교할 대상이자, 수동 갱신
    # 안내(python\python.exe -m pip install -r requirements.txt)가 실제로 동작하게 한다.
    shutil.copy2(repo_root / "requirements.txt", app / "requirements.txt")
    # 엑셀 템플릿(dev/*.xlsx)을 app 루트로 — template_path 가 찾는다.
    for xlsx in (repo_root / "dev").glob("*.xlsx"):
        shutil.copy2(xlsx, app / xlsx.name)
    scripts = repo_root / "scripts"
    for bat in bats:
        src = scripts / bat
        if src.exists():
            shutil.copy2(src, out / bat)

    # 4) VERSION 스탬프(자동 업데이트용).
    # ★ git 정보를 못 얻어도 **반드시 쓴다.**  예전엔 `if sha and branch:` 라
    #   git 아닌 소스(zip 다운로드 등)에서 만든 빌드엔 VERSION 이 아예 없었고,
    #   그런 배포본은 업데이트가 있다는 사실 자체를 모른 채로 남았다.
    #   SHA 가 비면 '미상' 이라는 뜻이고, 앱이 최신을 받아 적용한 뒤 진짜 SHA 를
    #   기록한다(`updater._write_version`).  브랜치는 updater 가 확인 시점에
    #   저장소 기본 브랜치로 정규화한다(`updater._resolve_branch`).
    sha, branch = _git_head(repo_root)
    branch = branch or _default_branch_name()
    (app / "VERSION").write_text(version_stamp(sha, branch), encoding="utf-8")
    log(f"       VERSION: {branch or '(기본)'} @ {sha or '(미상)'}")
    log(f"[4/4] done. Zip the whole {out_dirname}/ folder; on target PC unzip "
        "and run it (no Python needed).")
    return 0


def _copytree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)


def _write_deps_marker(repo_root: Path, out: Path) -> bool:
    """설치 루트에 '이 requirements 로 패키지를 깔았다' 표식을 남긴다.  성공 시 True.

    앱의 ``bootstrap`` 과 **같은 함수**를 쓴다 — 지문 계산이 두 곳에 있으면 어긋난다.
    실패를 삼키면 안 된다: 이 표식이 없으면 자동 업데이트가 패키지 변경을 감지하지 못해,
    코드만 새것이 되는 '반쪽 업데이트' 를 막을 근거가 사라진다."""
    try:
        bootstrap = _import_bootstrap()
        req = (repo_root / "requirements.txt").read_text(encoding="utf-8")
        bootstrap.write_deps_marker(out, req)      # 내부에서 예외를 삼키므로
        return bootstrap.deps_marker(out).exists()  # 결과로 확인한다
    except Exception:
        return False


# 번들 파이썬 안에서 실행되는 코드 — torchvision 가중치를 지정 폴더로 미리 받아 둔다.
# (앱의 embedder 가 쓰는 것과 **같은** 가중치: MobileNetV3-Small · ResNet18)
_TORCH_FETCH_SRC = (
    "import os, sys\n"
    "d = sys.argv[1]\n"
    "os.makedirs(d, exist_ok=True)\n"
    "os.environ['TORCH_HOME'] = d\n"
    "from torchvision import models as m\n"
    "m.mobilenet_v3_small(weights=m.MobileNet_V3_Small_Weights.IMAGENET1K_V1)\n"
    "m.resnet18(weights=m.ResNet18_Weights.IMAGENET1K_V1)\n"
    "print('weights cached in', d)\n"
)
