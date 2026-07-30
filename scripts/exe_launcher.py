"""exe_launcher.py — 'exe + app 폴더' 배포의 얇은 런처.  **앱 코드 0줄.**

이 파일에 앱 코드를 절대 넣지 마라.  PyInstaller 의 ``FrozenImporter`` 는 exe 안의 PYZ
사본으로 디스크의 최신 사본을 **가린다** — 옛 단독 exe 빌드에서 "업데이트가 적용되었습니다"
라고 하고도 코드가 그대로였던 근본 원인이다.  그래서 여기서는 **표준 라이브러리만** import
한다(``aoi_verification`` 은 절대 import 하지 않는다).

이 exe 는 **업데이트되지 않는다**(사용자 손에 있는 그 파일 그대로다).  따라서 여기 있는
로직은 전부 '나중에 못 고쳐도 되는가?' 를 통과해야 한다.

  통과함  : 경로 계산 · 교체 상태기계(중단 복구 포함) · 자식 실행 · 오류 표시
  통과 못함: 네트워크 · zip · pip  → 전부 ``updater``(업데이트되는 코드) 쪽에 둔다

교체를 런처가 맡는 이유: 실행 중인 앱은 자기가 돌아가는 폴더를 안전하게 바꿀 수 없다.
런처는 파이썬이 ``app/`` 에서 아무것도 import 하기 전에 돌기 때문에 할 수 있다.

**불변식: 이 파일의 모든 실패 경로는 '구버전을 그대로 실행' 으로 끝난다.**
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, NamedTuple

# 앱에게 "교체해 줄 런처가 있다"고 알리는 유일한 신호.  updater 는 이 값이 있을 때만
# 스테이징(app.new) 방식으로 가고, 없으면 제자리 적용(포터블/온라인)으로 간다.
APP_HOME_ENV = "AOI_APP_HOME"


class Layout(NamedTuple):
    root: Path          # 설치 루트(exe 가 있는 폴더)
    app: Path           # 실행 중인 앱 소스
    new: Path           # 적용 대기 중인 새 앱(존재 자체가 '준비 완료' 신호)
    old: Path           # 교체 중 백업
    pythonw: Path       # 번들 파이썬(콘솔 없음)
    python: Path        # 번들 파이썬(콘솔 있음 — 첫 실행 설치 진행을 보여준다)
    marker: Path        # 의존성 설치 표식.  없으면 '아직 설치 안 됨'
    main_py: Path


def app_paths(exe: Path) -> Layout:
    """모든 경로는 **exe 위치 기준**으로 잡는다.

    ``__file__`` 은 얼린 exe 안에서 임시 폴더를 가리켜 의미가 없다 — 반드시
    ``sys.executable`` 을 써야 한다."""
    root = Path(exe).resolve().parent
    return Layout(
        root=root,
        app=root / "app",
        new=root / "app.new",
        old=root / "app.old",
        pythonw=root / "python" / "pythonw.exe",
        python=root / "python" / "python.exe",
        marker=root / ".deps_installed",
        main_py=root / "app" / "main.py",
    )


def swap_pending(lay: Layout) -> None:
    """적용 대기 중인 업데이트(``app.new``)를 ``app`` 으로 교체한다.

    Windows 는 디렉터리를 원자적으로 교체할 수 없어(``os.replace`` 는 파일 전용)
    rename 2번으로 한다.  그 사이에서 중단되면 ``app`` 이 아예 없어지는데, 그건
    사용자가 못 고치고 이 런처는 업데이트되지 않으므로 **복구를 여기 넣어야 한다.**

    **어떤 실패든 구버전이 살아남는다** — 교체는 다음 실행에 다시 시도된다."""
    app, new, old = lay.app, lay.new, lay.old

    if not app.exists():                        # 지난번 교체가 중간에 끊겼다
        if new.exists():
            new.rename(app)                     # ① 직후 중단 → 전진 복구
        elif old.exists():
            old.rename(app)                     # ② 실패 후 중단 → 후진 복구
        shutil.rmtree(old, ignore_errors=True)
        return

    if not new.exists():                        # 대기 중인 업데이트 없음
        shutil.rmtree(old, ignore_errors=True)  # 지난 교체의 잔재만 정리
        return

    shutil.rmtree(old, ignore_errors=True)
    if old.exists():
        return          # 옛 백업을 못 치웠다(AV/핸들) → 이번 교체는 보류, 다음 실행에 재시도

    try:
        app.rename(old)                         # ① 실패해도 상태는 그대로다
    except OSError:
        return                                  # 앱이 아직 떠 있거나 잠김
    try:
        new.rename(app)                         # ②
    except OSError:
        old.rename(app)                         # 롤백 — 구버전 복귀
        return
    shutil.rmtree(old, ignore_errors=True)


def launch_cmd(lay: Layout) -> List[str]:
    """앱 실행 명령 — 번들 파이썬으로 ``app/main.py``.

    표식(``.deps_installed``)이 없으면 **콘솔이 보이는 ``python.exe``** 로 띄운다.
    lite 배포(``build.py exe-lite``)의 첫 실행은 앱이 pip 로 라이브러리를 받느라 몇 분이
    걸리는데, ``pythonw.exe`` 로 띄우면 화면에 아무것도 없어 사용자가 '안 켜진다' 고
    판단한다.  둘째 실행부터는 표식이 생겨 조용히(``pythonw``) 뜬다.

    설치 자체는 **앱 코드**가 한다 — 이 파일은 어느 실행 파일로 띄울지만 고른다
    (네트워크·pip 는 업데이트되지 않는 런처에 넣지 않는다는 이 파일의 계약)."""
    first_run = not lay.marker.exists() and lay.python.exists()
    exe = lay.python if first_run else lay.pythonw
    return [str(exe), str(lay.main_py)]


def _error(msg: str) -> None:
    """``console=False`` 라 stderr 가 보이지 않는다 — 메시지 상자로 알린다(의존성 0)."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "AOI 검증", 0x10)
    except Exception:
        print(msg, file=sys.stderr)


def main() -> int:
    lay = app_paths(Path(sys.executable))
    try:
        swap_pending(lay)
    except OSError:
        pass                    # 교체 실패는 치명적이지 않다 — 구버전으로 계속 간다

    if not lay.pythonw.exists() or not lay.main_py.exists():
        _error("설치가 손상되었습니다. 받은 zip 을 다시 압축 해제해 주세요.\n"
               f"위치: {lay.root}")
        return 2

    # cwd 를 설치 루트로 둔다 — app\ 안에 CWD 핸들을 만들면 다음 교체의 rename 이 막힌다.
    # (main.py 가 자기 부모를 sys.path 에 넣으므로 cwd 는 import 에 영향이 없다.)
    # PYTHONNOUSERSITE — 번들은 자체 완결이 설계 전제다.  버전이 겹치는 파이썬이 PC 에
    # 있으면 개인(user) 패키지 폴더가 딸려 들어와, 번들에 빠진 것이 있어도 그 PC 에서만
    # 잘 도는 상태가 된다.  그걸 막아 '어디서든 같은 결과' 를 보장한다.
    subprocess.Popen(launch_cmd(lay), cwd=str(lay.root),
                     env=dict(os.environ, **{APP_HOME_ENV: str(lay.root),
                                             "PYTHONNOUSERSITE": "1"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
