"""설치 폴더에 **아이콘 달린 바로가기**(``AOI 레시피 검증.lnk``)를 만든다.

회사 보안이 서명 없는 ``AOI_Verify.exe`` 를 지워서 실행은 ``run_aoi.bat`` 으로 한다.
그런데 .bat 은 자기 아이콘을 가질 수 없다 — Windows 에서 아이콘을 붙일 수 있는 것은
바로가기(.lnk)뿐이다.  .lnk 는 **절대 경로**를 담아야 해서 빌드가 미리 만들어 둘 수
없고(사용자마다 압축을 푸는 위치가 다르다), 그래서 앱이 켜질 때 그 자리에 만든다.

★ 파일은 직접 쓴다(MS-SHLLINK 형식).  PowerShell·VBScript 로 만들면 그 자체가
  '스크립트를 띄우는 앱' 으로 보여 보안 프로그램을 다시 자극할 수 있고, pywin32 는
  번들에 없다.  필요한 블록은 헤더·LinkInfo(로컬 경로)·문자열 3개뿐이다.
★ 부가 기능이다 — 어떤 실패도 앱 실행을 막지 않는다(로그만 남긴다).
"""

from __future__ import annotations

import logging
import os
import struct
from pathlib import Path
from typing import Optional

from .. import i18n

_log = logging.getLogger("aoi.shortcut")

_BAT = "run_aoi.bat"
_ICON_REL = Path("app") / "aoi_verification" / "app" / "ui" / "assets" / "logo.ico"

# ShellLinkHeader.LinkFlags
_HAS_LINK_INFO = 0x02
_HAS_NAME = 0x04
_HAS_WORKING_DIR = 0x10
_HAS_ICON_LOCATION = 0x40
_IS_UNICODE = 0x80
_SW_SHOWMINNOACTIVE = 7          # bat 의 콘솔 창이 화면에 번쩍이지 않게 최소화로 띄운다
_CLSID = bytes.fromhex("0114020000000000c000000000000046")


def _ansi(text: str) -> bytes:
    """LinkInfo 의 ANSI 경로 — 유니코드 경로가 함께 있으니 못 옮긴 글자는 ``?`` 여도 된다."""
    try:
        return text.encode("mbcs", "replace") + b"\0"
    except LookupError:                       # Windows 가 아닌 곳(테스트)
        return text.encode("ascii", "replace") + b"\0"


def _string(text: str) -> bytes:
    """StringData 항목 — 글자 수(2바이트) + UTF-16LE, 끝 NUL 없음."""
    data = text.encode("utf-16-le")
    return struct.pack("<H", len(data) // 2) + data


def build_lnk(target: Path, working_dir: Path, icon: Path, description: str) -> bytes:
    """``target`` 을 가리키는 .lnk 파일 내용."""
    flags = (_HAS_LINK_INFO | _HAS_NAME | _HAS_WORKING_DIR
             | _HAS_ICON_LOCATION | _IS_UNICODE)
    header = struct.pack(
        "<I16sII8s8s8sIiIH2sII",
        0x4C, _CLSID, flags,
        0x20,                                  # FILE_ATTRIBUTE_ARCHIVE
        b"\0" * 8, b"\0" * 8, b"\0" * 8,       # 생성·접근·수정 시각(모름)
        0, 0, _SW_SHOWMINNOACTIVE, 0, b"\0" * 2, 0, 0)

    # LinkInfo — 헤더 0x24(유니코드 경로 오프셋 포함) + VolumeID + 경로들.
    volume = struct.pack("<IIII", 0x11, 3, 0, 0x10) + b"\0"   # DRIVE_FIXED, 라벨 없음
    base_ansi = _ansi(str(target))
    suffix_ansi = b"\0"
    base_uni = str(target).encode("utf-16-le") + b"\0\0"
    suffix_uni = b"\0\0"
    head = 0x24
    off_volume = head
    off_base = off_volume + len(volume)
    off_suffix = off_base + len(base_ansi)
    off_base_uni = off_suffix + len(suffix_ansi)
    off_suffix_uni = off_base_uni + len(base_uni)
    size = off_suffix_uni + len(suffix_uni)
    link_info = struct.pack(
        "<IIIIIIIII", size, head, 0x01,        # VolumeIDAndLocalBasePath
        off_volume, off_base, 0, off_suffix, off_base_uni, off_suffix_uni,
    ) + volume + base_ansi + suffix_ansi + base_uni + suffix_uni
    assert len(link_info) == size

    strings = _string(description) + _string(str(working_dir)) + _string(str(icon))
    return header + link_info + strings + b"\0\0\0\0"    # ExtraData 끝 표시


def _launcher_root(install_root: Optional[Path], app_root: Path) -> Optional[Path]:
    """bat 이 놓인 설치 루트.  옛 bat(AOI_APP_HOME 을 안 넘김)으로 켠 경우도 레이아웃으로 찾는다."""
    for root in (install_root, app_root.parent):
        if root is not None and (root / _BAT).is_file() and (root / "python").is_dir():
            return root
    return None


def ensure_shortcut(install_root: Optional[Path], app_root: Path) -> Optional[Path]:
    """설치 루트에 바로가기를 만든다(내용이 같으면 그대로 둔다).  만든/있는 경로, 아니면 None."""
    if os.name != "nt":
        return None
    try:
        root = _launcher_root(install_root, app_root)
        if root is None:
            return None                        # 개발 작업트리·온라인 배포 — 할 일 없다
        root = root.resolve()
        data = build_lnk(root / _BAT, root, root / _ICON_REL, i18n.KO.APP_TITLE)
        lnk = root / f"{i18n.KO.APP_TITLE}.lnk"
        try:
            if lnk.read_bytes() == data:
                return lnk
        except OSError:
            pass
        lnk.write_bytes(data)
        return lnk
    except Exception:
        _log.warning("바로가기를 만들지 못했습니다", exc_info=True)
        return None
