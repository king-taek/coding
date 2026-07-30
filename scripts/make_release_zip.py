"""make_release_zip.py — 배포용 zip 을 만든다(전달하는 사람이 실행).

``python scripts\\build.py exe`` 로 만든 ``dist\\AOI_Verify\\`` 폴더를 **검증한 뒤**
설치법을 넣고 zip 하나로 묶는다.  이 zip 파일 하나만 사용자에게 주면 된다.

    python scripts\\make_release_zip.py

산출물: ``dist\\AOI_Verify_<날짜>_<커밋7자리>.zip``

검증을 통과하지 못하면 **zip 을 만들지 않는다.**  깨진 배포본(특히 앱이 exe 안에 얼려
들어간 것)을 사용자에게 보내면, 자동 업데이트가 '성공했다' 고 하면서 아무것도 안 바뀌는
사고가 다시 난다 — 그걸 여기서 막는다.

순수 로직(이름 결정·포함 여부·설치법 문구)은 부수효과 없이 분리해 헤드리스 테스트한다.
"""

from __future__ import annotations

import sys
import zipfile
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Callable, List, Optional

# 한글 출력이 깨지지 않도록 stdout/stderr 를 UTF-8 로(Windows cp949 대비).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 이 스크립트는 scripts/ 안에 있다 → 저장소 루트는 부모.
REPO_ROOT = Path(__file__).resolve().parent.parent

INSTRUCTIONS_NAME = "설치방법.txt"

# 빌드 폴더에 남아 있으면 안 되는 것 — 업데이트 찌꺼기와 빌드 중 만들어진 산출물.
# (`결과` 는 사용자 산출물 폴더라 빌드 머신에서 시험 실행한 엑셀이 딸려가면 안 된다.
#  `python.tar.gz` 는 런타임 다운로드가 실패했을 때 남는 부분 파일이다.
#  `.irbuild` 는 IR 변환용 torch 임시 트리로, 빌드가 중간에 죽었을 때만 남는다 —
#  수백 MB 짜리라 그대로 실리면 이 작업의 목적 자체가 무너진다.)
_SKIP_TOP = {"app.new", "app.old", "app.new.part", "결과", "python.tar.gz",
             ".irbuild"}


# ---------------------------------------------------------------------------
# 순수 로직 (테스트 대상) — 부수효과 없음
# ---------------------------------------------------------------------------
def read_version(out: Path) -> dict:
    """``app/VERSION`` (JSON) 을 읽는다.  없거나 깨졌으면 빈 dict."""
    import json
    try:
        data = json.loads((out / "app" / "VERSION").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def zip_basename(version: dict, today: str) -> str:
    """zip 파일 이름 — 날짜 + 커밋 7자리.

    어느 버전을 누구에게 줬는지 나중에 추적할 수 있어야 한다.  커밋을 모르면(미상)
    날짜만 쓴다."""
    sha = str((version or {}).get("sha") or "")[:7]
    return f"AOI_Verify_{today}_{sha}.zip" if sha else f"AOI_Verify_{today}.zip"


def should_include(rel: PurePosixPath) -> bool:
    """zip 에 넣을 파일인지.  경로는 ``AOI_Verify/`` 기준 상대경로다."""
    parts = rel.parts
    if not parts:
        return False
    if parts[0] in _SKIP_TOP:
        return False
    # app\ 안의 __pycache__ 는 넣지 않는다 — 자동 업데이트도 새 트리에서 이걸 없애므로,
    # 배포본에 낡은 바이트코드를 심어두면 그 정리 효과가 반감된다.
    if parts[0] == "app" and "__pycache__" in parts:
        return False
    return True


def instructions_text() -> str:
    """zip 안에 넣을 설치법.  메모장에서 열리도록 CRLF 로 쓴다."""
    lines = [
        "AOI 검증 프로그램 — 설치 및 사용 방법",
        "=" * 44,
        "",
        "",
        "[1] 설치 (처음 한 번만)",
        "-" * 44,
        "",
        "  1. 이 zip 파일을 마우스 오른쪽 → '압축 풀기' 로 해제합니다.",
        "",
        "  2. ★ 중요 — 경로가 짧은 곳에 푸세요.",
        "",
        "       권장:   C:\\AOI_Verify",
        "       피하세요: 바탕 화면, 내 문서, 다운로드 폴더 안쪽 깊은 위치",
        "",
        "     경로가 길면 (한글 폴더가 여러 겹 섞인 경우 특히) 윈도우의 경로 길이",
        "     제한에 걸려 프로그램이 제대로 동작하지 않을 수 있습니다.",
        "",
        "  3. ★ 중요 — 'C:\\Program Files' 안에는 두지 마세요.",
        "",
        "     그 폴더는 쓰기가 제한되어 자동 업데이트가 되지 않습니다.",
        "",
        "  4. 파이썬을 따로 설치할 필요는 없습니다. 필요한 것이 모두 들어 있습니다.",
        "",
        "",
        "[2] 실행",
        "-" * 44,
        "",
        "  압축을 푼 폴더 안의  AOI_Verify.exe  를 더블클릭합니다.",
        "",
        "  * 처음 실행할 때 '알 수 없는 게시자' 경고가 뜰 수 있습니다.",
        "    [추가 정보] → [실행] 을 누르면 됩니다.",
        "    (프로그램에 서명이 없어서 뜨는 경고이며, 동작에는 문제가 없습니다.)",
        "",
        "  * 바탕 화면에 바로가기를 만들려면 AOI_Verify.exe 를 오른쪽 클릭 →",
        "    [보내기] → [바탕 화면에 바로 가기 만들기] 를 누르세요.",
        "",
        "",
        "[3] 업데이트 — 따로 하실 일이 없습니다",
        "-" * 44,
        "",
        "  프로그램을 켜면 새 버전이 있는지 자동으로 확인합니다.",
        "",
        "    새 버전 있음  →  '업데이트할까요?' 안내가 뜹니다",
        "    [예] 를 누름  →  내려받은 뒤 프로그램이 종료됩니다",
        "    다시 실행     →  새 버전으로 켜집니다",
        "",
        "  * 받는 도중 문제가 생기면 이전 버전이 그대로 유지됩니다.",
        "    프로그램이 못 쓰게 되는 일은 없으니 안심하고 다시 시도하세요.",
        "",
        "  * 업데이트를 받아도 지금까지 만든 결과 파일과 설정은 그대로 남습니다.",
        "",
        "",
        "[4] 결과 파일은 어디에 저장되나요",
        "-" * 44,
        "",
        "  압축을 푼 폴더 안의  결과  폴더에 저장됩니다.",
        "  (예:  C:\\AOI_Verify\\결과 )",
        "",
        "",
        "[5] 프로그램이 켜지지 않을 때",
        "-" * 44,
        "",
        "  1. 같은 폴더의  run_aoi_debug.bat  을 더블클릭하세요.",
        "     검은 창이 뜨면서 오류 내용이 표시됩니다.",
        "",
        "  2. 그 창의 내용을 화면 캡처해서 담당자에게 보내주세요.",
        "",
        "  3. 회사 백신이 AOI_Verify.exe 를 막는 경우에는",
        "     같은 폴더의  run_aoi.bat  으로도 실행할 수 있습니다.",
        "",
        "",
        "[6] 폴더 안에 무엇이 들어 있나요",
        "-" * 44,
        "",
        "  AOI_Verify.exe      프로그램 실행 파일 (이것을 더블클릭)",
        "  app                 프로그램 본체 (자동 업데이트로 갱신됩니다)",
        "  python              프로그램 구동에 필요한 것들",
        "  runtime             검사에 쓰는 모델 파일",
        "  결과                 결과 엑셀이 저장되는 곳",
        "  run_aoi.bat         예비 실행 방법",
        "  run_aoi_debug.bat   오류 확인용",
        "",
        "  ※ app · python · runtime 폴더는 직접 수정하거나 옮기지 마세요.",
        "",
        "",
        "[7] 삭제하려면",
        "-" * 44,
        "",
        "  압축을 푼 폴더를 통째로 지우면 됩니다. 따로 제거 절차는 없습니다.",
        "  (결과 파일이 필요하면 '결과' 폴더를 먼저 다른 곳에 복사해 두세요.)",
        "",
    ]
    return "\r\n".join(lines) + "\r\n"


# ---------------------------------------------------------------------------
# 실행 — 검증 후 zip 생성
# ---------------------------------------------------------------------------
def _load_build_module():
    """``scripts/build.py`` 의 검증 로직을 재사용한다(같은 검사를 두 곳에 두지 않는다)."""
    import importlib.util as _u
    path = Path(__file__).resolve().parent / "build.py"
    spec = _u.spec_from_file_location("build", str(path))
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def collect_files(out: Path) -> List[Path]:
    """zip 에 넣을 파일 목록(정렬)."""
    files = []
    for p in sorted(out.rglob("*")):
        if not p.is_file():
            continue
        if should_include(PurePosixPath(p.relative_to(out).as_posix())):
            files.append(p)
    return files


def make_zip(repo_root: Path = REPO_ROOT, log: Callable = print,
             today: Optional[str] = None) -> int:
    """검증 → 설치법 작성 → zip.  반환 0=성공."""
    build = _load_build_module()
    out = repo_root / build.EXE_OUT_DIRNAME

    # 1) 검증 — 깨진 배포본을 사용자에게 보내지 않기 위한 관문.
    log("[1/3] 배포본 검증 ...")
    bad = [label for good, label in build.verify_checks(out) if not good]
    if bad:
        # ★ 실패 항목만 나열하면 사용자는 '무엇이 없다' 만 알고 '무엇을 하라' 는 모른다.
        #   빌드를 안 돌린 것 / 옛 산출물이 남은 것 / 중간에 실패한 것은 대응이 전혀 다르다.
        log("[실패] 검증을 통과하지 못했습니다 — zip 을 만들지 않습니다.")
        build.report_diagnosis(out, log)
        log("")
        log("       [참고] 확인되지 않은 항목:")
        for label in bad:
            log(f"         - {label}")
        return 1
    log("  [OK] 전 항목 통과")

    # 2) 설치법 — 폴더에도 남긴다(폴더째 복사해 주는 경우에도 보이도록).
    log("[2/3] 설치법 작성 ...")
    # BOM 있는 UTF-8 — 없으면 구버전 메모장이 cp949 로 읽어 한글이 깨진다.
    # 사용자에게 나가는 유일한 안내 문서라 이게 깨지면 안 된다.
    (out / INSTRUCTIONS_NAME).write_text(instructions_text(), encoding="utf-8-sig")

    # 3) zip — 최상위에 AOI_Verify\ 폴더가 오게 담는다(풀면 폴더 하나가 생긴다).
    version = read_version(out)
    today = today or date.today().strftime("%Y%m%d")
    zip_path = out.parent / zip_basename(version, today)
    files = collect_files(out)
    total = len(files)
    log(f"[3/3] zip 생성 ... ({total}개 파일 — 몇 분 걸립니다)")

    tmp_path = zip_path.with_suffix(".zip.part")
    tmp_path.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as z:
            for i, p in enumerate(files, start=1):
                z.write(p, str(PurePosixPath(out.name)
                               / p.relative_to(out).as_posix()))
                if i % 500 == 0 or i == total:
                    log(f"       {i}/{total} ({i * 100 // total}%)")
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        log(f"[실패] zip 생성 중 오류: {exc}")
        return 1
    zip_path.unlink(missing_ok=True)
    tmp_path.rename(zip_path)      # 완성된 것만 최종 이름을 갖는다

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    log("")
    log(f"[완료] {zip_path}  ({size_mb:.0f} MB)")
    log("       이 파일 하나만 사용자에게 전달하면 됩니다.")
    log(f"       (압축을 풀면 {out.name} 폴더가 생기고, 그 안에 "
        f"{INSTRUCTIONS_NAME} 이 들어 있습니다.)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    return make_zip()


if __name__ == "__main__":
    raise SystemExit(main())
