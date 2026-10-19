"""설치 폴더 바로가기(.lnk) — exe 대신 run_aoi.bat 으로 켜되 아이콘이 보이게.

.bat 은 아이콘을 못 가지므로 앱이 켜질 때 설치 루트에 바로가기를 만든다.  Windows 가
없는 곳에서도 파일 구조(MS-SHLLINK)를 직접 해석해 검증한다."""

from __future__ import annotations

import struct
from pathlib import Path, PureWindowsPath

from aoi_verification.app import i18n
from aoi_verification.app.utils import shortcut


def _strings_after_link_info(data: bytes) -> list:
    """헤더(76) → LinkInfo(크기 첫 4바이트) → StringData 3개를 읽는다."""
    off = 76 + struct.unpack_from("<I", data, 76)[0]
    out = []
    for _ in range(3):
        n = struct.unpack_from("<H", data, off)[0]
        out.append(data[off + 2: off + 2 + n * 2].decode("utf-16-le"))
        off += 2 + n * 2
    assert data[off:] == b"\0\0\0\0"           # ExtraData 끝 표시로 정확히 끝난다
    return out


def test_lnk_points_at_the_bat_with_the_app_icon():
    root = PureWindowsPath(r"C:\AOI 검증")
    data = shortcut.build_lnk(root / "run_aoi.bat", root,
                              root / "app" / "logo.ico", "AOI 레시피 검증")
    assert struct.unpack_from("<I", data, 0)[0] == 0x4C
    assert data[4:20] == bytes.fromhex("0114020000000000c000000000000046")
    flags = struct.unpack_from("<I", data, 20)[0]
    assert flags & 0x80 and flags & 0x40 and flags & 0x02       # 유니코드·아이콘·LinkInfo
    assert struct.unpack_from("<I", data, 60)[0] == 7           # 최소화로 띄움(콘솔 번쩍임 방지)

    li = 76
    size, head, _flags, _vol, _base, _net, _suf, base_u, _suf_u = \
        struct.unpack_from("<9I", data, li)
    assert head == 0x24                                          # 유니코드 경로 오프셋 포함
    end = li + base_u
    while data[end:end + 2] != b"\0\0":                         # UTF-16 글자 단위로 끝을 찾는다
        end += 2
    target = data[li + base_u: end].decode("utf-16-le")
    assert target == r"C:\AOI 검증\run_aoi.bat"                  # 한글 경로도 그대로

    desc, workdir, icon = _strings_after_link_info(data)
    assert desc == "AOI 레시피 검증"
    assert workdir == r"C:\AOI 검증"
    assert icon == r"C:\AOI 검증\app\logo.ico"


def _install(tmp_path: Path) -> Path:
    root = tmp_path / "AOI_Verify"
    (root / "python").mkdir(parents=True)
    (root / "run_aoi.bat").write_text("@echo off\n")
    (root / "app").mkdir()
    return root


def test_creates_once_next_to_the_bat_even_with_the_old_bat(tmp_path, monkeypatch):
    """옛 bat 은 AOI_APP_HOME 을 안 넘긴다 — 그래도 레이아웃으로 찾아 만든다."""
    monkeypatch.setattr(shortcut.os, "name", "nt")
    root = _install(tmp_path)
    lnk = shortcut.ensure_shortcut(None, root / "app")
    assert lnk == root.resolve() / f"{i18n.KO.APP_TITLE}.lnk"
    first = lnk.stat().st_mtime_ns
    assert shortcut.ensure_shortcut(root, root / "app") == lnk   # 내용이 같으면 다시 안 쓴다
    assert lnk.stat().st_mtime_ns == first


def test_nothing_for_dev_checkout_or_non_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(shortcut.os, "name", "nt")
    dev = tmp_path / "repo"
    dev.mkdir()
    assert shortcut.ensure_shortcut(None, dev) is None             # bat·python 없음
    assert not list(tmp_path.rglob("*.lnk"))
    monkeypatch.setattr(shortcut.os, "name", "posix")
    assert shortcut.ensure_shortcut(None, _install(tmp_path) / "app") is None


def test_failure_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(shortcut.os, "name", "nt")
    root = _install(tmp_path)
    (root / f"{i18n.KO.APP_TITLE}.lnk").mkdir()                    # 쓸 수 없는 자리
    assert shortcut.ensure_shortcut(root, root / "app") is None


def test_refresh_bats_copies_the_updated_launcher_to_the_install_root(tmp_path):
    """설치 루트의 bat 은 업데이트가 안 닿는다 — 앱이 app\\scripts 사본으로 바꿔 놓는다."""
    root = tmp_path / "AOI_Verify"
    (root / "python").mkdir(parents=True)
    (root / "python" / "pythonw.exe").write_bytes(b"x")
    scripts = root / "app" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "run_aoi.bat").write_text("new run\n")
    (scripts / "run_aoi_debug.bat").write_text("new debug\n")
    (root / "run_aoi.bat").write_text("old run\n")          # 옛 bat — AOI_APP_HOME 없음

    changed = shortcut.refresh_bats(None, root / "app")      # 옛 bat 으로 켜도 찾는다
    assert sorted(p.name for p in changed) == ["run_aoi.bat", "run_aoi_debug.bat"]
    assert (root / "run_aoi.bat").read_text() == "new run\n"
    assert (root / "run_aoi_debug.bat").read_text() == "new debug\n"
    assert not list(root.glob("*.new"))
    assert shortcut.refresh_bats(root, root / "app") == []   # 같으면 다시 안 쓴다


def test_refresh_bats_leaves_dev_checkouts_alone(tmp_path):
    repo = tmp_path / "coding"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "run_aoi.bat").write_text("x")
    assert shortcut.refresh_bats(None, repo) == []
    assert not (tmp_path / "run_aoi.bat").exists()


def test_main_only_touches_bats_from_pythonw_after_a_delay():
    """cmd 는 도는 중인 bat 을 다시 읽는다 — 디버그 bat(python.exe) 실행 중엔 바꾸면 안 된다."""
    src = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
    body = src[src.index("def _install_launchers"):src.index("def main(")]
    assert '!= "pythonw"' in body and "threading.Timer(" in body
    assert body.index("refresh_bats") < body.index("ensure_shortcut")   # bat 이 먼저 있어야 바로가기
