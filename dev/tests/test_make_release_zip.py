"""make_release_zip.py — 배포 zip 생성기의 순수 로직 + 실제 zip 왕복 테스트.

무거운 의존성 없이 검증한다(zipfile 은 표준 라이브러리).
"""

from __future__ import annotations

import importlib.util as _u
import zipfile
from pathlib import Path, PurePosixPath

_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = _u.spec_from_file_location(name, str(_ROOT / rel))
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rel = _load("make_release_zip", "scripts/make_release_zip.py")
build = _load("build", "scripts/build.py")


# ── 순수 로직 ──────────────────────────────────────────────────────────────
def test_zip_name_carries_date_and_commit():
    """어느 버전을 누구에게 줬는지 나중에 추적할 수 있어야 한다."""
    n = rel.zip_basename({"sha": "abcdef1234567890", "branch": "b"}, "20260727")
    assert n == "AOI_Verify_20260727_abcdef1.zip"
    # 커밋을 모르면 날짜만.
    assert rel.zip_basename({}, "20260727") == "AOI_Verify_20260727.zip"
    assert rel.zip_basename({"sha": ""}, "20260727") == "AOI_Verify_20260727.zip"


def test_update_leftovers_and_test_results_are_excluded():
    """빌드 머신에서 생긴 찌꺼기가 사용자에게 딸려가면 안 된다."""
    inc = lambda s: rel.should_include(PurePosixPath(s))
    assert inc("AOI_Verify.exe")
    assert inc("app/main.py")
    assert inc("python/python.exe")
    assert inc("runtime/torch/hub/checkpoints/x.pth")
    assert inc(".deps_installed")            # 의존성 표식은 반드시 들어가야 한다
    # 업데이트 찌꺼기 · 시험 실행 산출물 · 낡은 바이트코드는 제외.
    assert not inc("app.new/main.py")
    assert not inc("app.old/main.py")
    assert not inc("app.new.part/main.py")
    assert not inc("결과/시험출력.xlsx")
    assert not inc("app/aoi_verification/__pycache__/x.pyc")
    # python\ 런타임의 __pycache__ 는 건드리지 않는다(첫 실행 속도).
    assert inc("python/Lib/site-packages/numpy/__pycache__/x.pyc")


def test_instructions_cover_what_users_get_wrong():
    """설치법에 빠지면 실제로 사고가 나는 항목들."""
    t = rel.instructions_text()
    assert "AOI_Verify.exe" in t                  # 무엇을 더블클릭하는지
    assert "C:\\AOI_Verify" in t                  # 짧은 경로 권장
    assert "Program Files" in t                   # 쓰기 불가 위치 경고
    assert "run_aoi_debug.bat" in t               # 안 켜질 때 진단
    assert "결과" in t                             # 결과 파일 위치
    assert "추가 정보" in t                        # 미서명 경고 대응
    assert "다시 실행" in t                        # 업데이트는 재실행 시 적용
    assert t.endswith("\r\n") and "\r\n" in t     # 메모장에서 깨지지 않게 CRLF


def test_read_version_tolerates_missing_or_broken(tmp_path):
    assert rel.read_version(tmp_path) == {}
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "VERSION").write_text("{ 깨진 json", encoding="utf-8")
    assert rel.read_version(tmp_path) == {}


# ── 실제 zip 왕복 ──────────────────────────────────────────────────────────
def _good_bundle(tmp_path) -> Path:
    """검증을 통과하는 최소 배포본 + 섞여 있으면 안 되는 찌꺼기."""
    out = tmp_path / build.EXE_OUT_DIRNAME
    app = out / "app"
    pkg = app / "aoi_verification"
    (pkg / "app" / "ui" / "assets").mkdir(parents=True)
    (pkg / "app" / "ui" / "style.qss").write_text("x", encoding="utf-8")
    (pkg / "app" / "ui" / "assets" / "logo.ico").write_bytes(b"x")
    for i in range(55):
        (pkg / f"m{i}.py").write_text("x", encoding="utf-8")
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / "stale.pyc").write_bytes(b"stale")
    (app / "main.py").write_text("x", encoding="utf-8")
    (app / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    (app / "양식.xlsx").write_bytes(b"x" * 1000)
    (app / "VERSION").write_text(
        '{"sha": "1234567abcdef", "branch": "b", "repo": "r"}', encoding="utf-8")
    (out / "AOI_Verify.exe").write_bytes(b"x" * 3_000_000)
    (out / "python").mkdir()
    (out / "python" / "python.exe").write_bytes(b"x")
    (out / "python" / "pythonw.exe").write_bytes(b"x")
    (out / ".deps_installed").write_text("fp", encoding="utf-8")
    ckpt = out / "runtime" / "torch" / "hub" / "checkpoints"
    ckpt.mkdir(parents=True)
    (ckpt / "a.pth").write_bytes(b"x")
    (ckpt / "b.pth").write_bytes(b"x")
    (out / "python" / "big.bin").write_bytes(b"\x00" * (810 * 1024 * 1024))
    # 찌꺼기 — zip 에 들어가면 안 된다.
    # (`app.new` 는 검증 단계에서 아예 걸러지므로 여기 두지 않는다 — 그건
    #  test_make_zip_refuses_a_broken_bundle 과 should_include 단위 테스트가 다룬다.)
    (out / "app.old").mkdir()
    (out / "app.old" / "main.py").write_text("옛 버전 백업", encoding="utf-8")
    (out / "결과").mkdir()
    (out / "결과" / "시험출력.xlsx").write_text("빌드 머신에서 시험", encoding="utf-8")
    return out


def test_make_zip_produces_a_clean_shippable_archive(tmp_path):
    out = _good_bundle(tmp_path)
    logs = []
    assert rel.make_zip(tmp_path, log=logs.append, today="20260727") == 0

    zp = out.parent / "AOI_Verify_20260727_1234567.zip"
    assert zp.is_file()
    with zipfile.ZipFile(zp) as z:
        names = z.namelist()
        # 풀면 폴더 하나가 생긴다(파일이 흩어지지 않게).
        assert all(n.startswith("AOI_Verify/") for n in names), names[:5]
        assert "AOI_Verify/AOI_Verify.exe" in names
        assert "AOI_Verify/app/main.py" in names
        assert "AOI_Verify/.deps_installed" in names
        # 설치법이 들어 있고 읽힌다.
        assert "AOI_Verify/설치방법.txt" in names
        text = z.read("AOI_Verify/설치방법.txt").decode("utf-8")
        assert "AOI_Verify.exe" in text and "C:\\AOI_Verify" in text
        # 찌꺼기는 빠졌다.
        assert not any(n.startswith("AOI_Verify/app.old") for n in names)
        assert not any("결과/" in n for n in names)
        assert not any("app/aoi_verification/__pycache__" in n for n in names)
    # 폴더째 복사하는 경우를 위해 설치법은 빌드 폴더에도 남는다.
    assert (out / rel.INSTRUCTIONS_NAME).is_file()
    assert any("완료" in m for m in logs)


def test_make_zip_refuses_a_broken_bundle(tmp_path):
    """★ 앱이 exe 안에 얼려 들어간 배포본은 zip 하지 않는다 — 사고 재발 방지."""
    out = _good_bundle(tmp_path)
    (out / "_internal").mkdir()               # 앱을 다시 얼린 지문
    logs = []
    assert rel.make_zip(tmp_path, log=logs.append, today="20260727") != 0
    assert not list(out.parent.glob("*.zip"))
    assert any("_internal" in m for m in logs)


def test_stale_bundle_gets_actionable_advice_not_just_red_lines(tmp_path):
    """★ '다시 빌드하세요' 만으로는 절대 해결되지 않는 상태다 — 지우라고 말해야 한다.

    이 안내가 없으면 사용자는 빌드를 반복해도 상태가 바뀌지 않는 무한 루프에 빠진다."""
    out = _good_bundle(tmp_path)
    (out / "_internal").mkdir()
    logs = []
    rel.make_zip(tmp_path, log=logs.append, today="20260727")
    joined = "\n".join(logs)
    assert "진단" in joined
    assert "지우" in joined or "rmdir" in joined


def test_never_built_says_so_instead_of_listing_missing_files(tmp_path):
    """빌드를 안 돌린 것과 빌드가 깨진 것은 대응이 다르다 — 구분해서 말해야 한다."""
    logs = []
    assert rel.make_zip(tmp_path, log=logs.append) != 0
    joined = "\n".join(logs)
    assert "build.py exe" in joined
    assert "빌드를 아직 실행하지 않았습니다" in joined


def test_partial_build_names_the_stage_it_stopped_at(tmp_path):
    """중간에 멈춘 빌드는 '어느 단계' 인지 알려줘야 다시 시도할 수 있다."""
    out = tmp_path / build.EXE_OUT_DIRNAME
    out.mkdir(parents=True)
    (out / "AOI_Verify.exe").write_bytes(b"x")     # 런처만 만들어진 상태
    logs = []
    assert rel.make_zip(tmp_path, log=logs.append) != 0
    assert any("CPython 런타임" in m for m in logs)


def test_instructions_file_is_readable_by_notepad(tmp_path):
    """BOM 이 없으면 구버전 메모장이 cp949 로 읽어 한글이 깨진다."""
    _good_bundle(tmp_path)
    assert rel.make_zip(tmp_path, log=lambda *a: None, today="20260727") == 0
    raw = (tmp_path / build.EXE_OUT_DIRNAME / rel.INSTRUCTIONS_NAME).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM 이 없다"
    assert "AOI 검증" in raw.decode("utf-8-sig")


def test_failed_runtime_download_leftover_is_not_shipped(tmp_path):
    """다운로드가 실패하면 python.tar.gz 부분 파일이 남는다 — 배포되면 안 된다."""
    inc = lambda s: rel.should_include(PurePosixPath(s))
    assert not inc("python.tar.gz")


def test_failed_zip_leaves_no_half_written_file(tmp_path, monkeypatch):
    """중간에 실패하면 .zip 이 남으면 안 된다 — 반쪽 zip 을 전달하는 사고 방지."""
    out = _good_bundle(tmp_path)
    real = zipfile.ZipFile.write

    def _boom(self, filename, arcname=None, **kw):
        if str(filename).endswith("main.py"):
            raise OSError("디스크 가득 참")
        return real(self, filename, arcname, **kw)

    monkeypatch.setattr(zipfile.ZipFile, "write", _boom)
    logs = []
    assert rel.make_zip(tmp_path, log=logs.append, today="20260727") != 0
    assert not list(out.parent.glob("*.zip"))
    assert not list(out.parent.glob("*.zip.part"))
