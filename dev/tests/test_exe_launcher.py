"""exe_launcher.py — 'exe + app 폴더' 배포 런처의 교체 상태기계 테스트.

이 런처는 **업데이트되지 않는다**(사용자 손에 있는 exe 그대로다).  그래서 여기 로직이
틀리면 나중에 고칠 방법이 없다 — 특히 rename 2번 사이에서 중단된 경우의 복구가 그렇다.
모든 경로를 무거운 의존성 없이(순수 파일시스템) 검증한다.

**핵심 불변식: 어떤 실패 경로에서도 앱은 살아남는다.**
"""

from __future__ import annotations

import importlib.util as _u
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = _u.spec_from_file_location(
        "exe_launcher", str(_ROOT / "scripts" / "exe_launcher.py"))
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


launcher = _load()


# ── 경로 해석 ──────────────────────────────────────────────────────────────
def test_app_paths_are_relative_to_the_exe():
    """모든 경로는 exe 위치 기준이어야 한다 — 얼린 exe 안에서 __file__ 은 무의미하다."""
    lay = launcher.app_paths(Path("/inst/AOI_Verify/AOI_Verify.exe"))
    root = Path("/inst/AOI_Verify")
    assert lay.root == root
    assert lay.app == root / "app"
    assert lay.new == root / "app.new"
    assert lay.old == root / "app.old"
    assert lay.pythonw == root / "python" / "pythonw.exe"
    assert lay.main_py == root / "app" / "main.py"


def test_launch_cmd_and_app_home_signal():
    lay = launcher.app_paths(Path("/inst/AOI_Verify/AOI_Verify.exe"))
    cmd = launcher.launch_cmd(lay)
    assert cmd[0].endswith("pythonw.exe")
    assert cmd[1].endswith("main.py")
    # updater 는 이 환경변수가 있을 때만 스테이징 모드로 간다.
    assert launcher.APP_HOME_ENV == "AOI_APP_HOME"


def test_launcher_contains_no_app_code():
    """런처에 앱을 import 하는 줄이 생기면 exe 안에 앱이 딸려 들어간다(사고 재발)."""
    text = (_ROOT / "scripts" / "exe_launcher.py").read_text(encoding="utf-8")
    assert "import aoi_verification" not in text
    assert "from aoi_verification" not in text


# ── 교체 상태기계 ──────────────────────────────────────────────────────────
def _mk(d: Path, marker: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(marker, encoding="utf-8")
    return d


def _marker(d: Path) -> str:
    return (d / "main.py").read_text(encoding="utf-8")


def _layout(tmp_path) -> "launcher.Layout":
    return launcher.app_paths(tmp_path / "AOI_Verify.exe")


def test_swap_applies_pending_update(tmp_path):
    lay = _layout(tmp_path)
    _mk(lay.app, "old")
    _mk(lay.new, "new")

    launcher.swap_pending(lay)

    assert _marker(lay.app) == "new"
    assert not lay.new.exists()
    assert not lay.old.exists()


def test_swap_is_noop_without_pending_update(tmp_path):
    lay = _layout(tmp_path)
    _mk(lay.app, "old")

    launcher.swap_pending(lay)

    assert _marker(lay.app) == "old"


def test_swap_purges_stale_backup_then_applies(tmp_path):
    """지난 교체가 백업을 못 지웠어도, 치우고 나서 정상 교체된다."""
    lay = _layout(tmp_path)
    _mk(lay.app, "old")
    _mk(lay.new, "new")
    _mk(lay.old, "stale")

    launcher.swap_pending(lay)

    assert _marker(lay.app) == "new"
    assert not lay.old.exists()


def test_swap_resumes_when_interrupted_between_renames(tmp_path):
    """rename ① 직후 전원이 끊긴 상태(app 없음 + old + new) → 전진 복구."""
    lay = _layout(tmp_path)
    _mk(lay.old, "old")
    _mk(lay.new, "new")

    launcher.swap_pending(lay)

    assert _marker(lay.app) == "new"
    assert not lay.old.exists()
    assert not lay.new.exists()


def test_swap_rolls_back_when_only_backup_survives(tmp_path):
    """rename ② 가 실패한 뒤 끊긴 상태(app 없음 + old 만) → 후진 복구.

    앱이 사라진 채로 남으면 사용자가 절대 고칠 수 없다 — 반드시 되살아나야 한다."""
    lay = _layout(tmp_path)
    _mk(lay.old, "old")

    launcher.swap_pending(lay)

    assert _marker(lay.app) == "old"
    assert not lay.old.exists()


def test_swap_keeps_old_app_when_first_rename_fails(tmp_path, monkeypatch):
    """앱이 아직 떠 있어 rename 이 막히면 — 아무것도 바뀌지 않고 다음 실행에 재시도한다."""
    lay = _layout(tmp_path)
    _mk(lay.app, "old")
    _mk(lay.new, "new")

    def _boom(self, target):
        raise OSError("in use")

    monkeypatch.setattr(Path, "rename", _boom)
    launcher.swap_pending(lay)

    assert _marker(lay.app) == "old"          # 구버전 그대로
    assert _marker(lay.new) == "new"          # 대기 상태 보존 → 다음에 다시 시도


def test_swap_rolls_back_when_second_rename_fails(tmp_path, monkeypatch):
    """새 앱을 제자리에 못 넣으면 구버전을 되돌린다(앱 없는 상태로 두지 않는다)."""
    lay = _layout(tmp_path)
    _mk(lay.app, "old")
    _mk(lay.new, "new")

    real = Path.rename
    calls = {"n": 0}

    def _fail_second(self, target):
        calls["n"] += 1
        if calls["n"] == 2:                   # app.new → app 만 실패시킨다
            raise OSError("locked")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", _fail_second)
    launcher.swap_pending(lay)

    assert _marker(lay.app) == "old"          # 롤백됨 — 앱이 사라지지 않았다


def test_swap_defers_when_stale_backup_cannot_be_purged(tmp_path, monkeypatch):
    """옛 백업을 못 치우면 이번 교체는 보류한다(섞인 상태를 만들지 않는다)."""
    lay = _layout(tmp_path)
    _mk(lay.app, "old")
    _mk(lay.new, "new")
    _mk(lay.old, "stale")

    monkeypatch.setattr(launcher.shutil, "rmtree",
                        lambda *a, **k: None)          # 삭제가 통하지 않는 상황
    launcher.swap_pending(lay)

    assert _marker(lay.app) == "old"
    assert _marker(lay.new) == "new"                   # 다음 실행에 재시도
