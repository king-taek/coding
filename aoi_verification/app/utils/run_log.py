"""검증 실행 로그 — 실행한 컴퓨터별 폴더에 사용 통계를 기록한다.

기록 항목: 사용 옵션(엔진/자동화/임계치/전처리), 사진 갯수, slot 갯수, 걸린 시간,
폴더가 로컬인지 원격(NAS)인지 등.  **캐시로 빠르게 끝난 매치는 제외**(통계 의미 없음).

컴퓨터별로 폴더를 따로 둔다(폴더명 = 호스트명 기반, 사용자 구분 안 함).  로컬에
먼저 쓰고, (설정 시) GitHub 로 업로드한다 — 업로드는 ``uploader`` 콜러블이 주어질
때만 동작하며, 자격증명은 코드에 담지 않는다.
"""

from __future__ import annotations

import json
import os
import platform
import re
import socket
import time
from pathlib import Path
from typing import Optional

from . import paths

# 이 시간(초) 미만으로 끝난 매칭은 '캐시를 통한 빠른 매치' 로 보고 기록하지 않는다.
CACHE_FAST_SEC = 2.0


def machine_id() -> str:
    """실행 컴퓨터 식별자(폴더명용) — 호스트명 기반, 사용자 구분 안 함.

    파일명에 안전하도록 영숫자/._- 외 문자는 '_' 로 치환.  비면 'unknown'."""
    name = ""
    try:
        name = socket.gethostname() or platform.node()
    except Exception:
        name = platform.node()
    name = (name or "").strip()
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name or "unknown"


def path_location(p) -> str:
    """경로가 로컬 디스크인지 원격(NAS/네트워크)인지 — 'local'/'remote'/'unknown'."""
    s = str(p or "")
    if not s:
        return "unknown"
    # UNC 경로(\\server\share)는 네트워크.
    if s.startswith("\\\\") or s.startswith("//"):
        return "remote"
    if os.name == "nt":
        try:
            import ctypes
            drive = os.path.splitdrive(os.path.abspath(s))[0]
            if drive:
                # GetDriveTypeW: 3=고정(로컬), 4=네트워크, 2=이동식 …
                t = ctypes.windll.kernel32.GetDriveTypeW(drive + "\\")
                if t == 4:
                    return "remote"
                if t in (2, 3, 6):
                    return "local"
        except Exception:
            return "unknown"
        return "unknown"
    # POSIX: 마운트 종류 판별이 비표준이라 보수적으로 unknown.
    return "unknown"


def _log_dir() -> Path:
    d = paths.cache_root() / "run_logs" / machine_id()
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_record(*, options: dict, ref_root, val_root,
                 slot_count: int, ref_photos: int, val_photos: int,
                 elapsed_s: float, kla_used: bool, ocr_used: bool) -> dict:
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "machine": machine_id(),
        "os": platform.platform(),
        "options": options,
        "ref_root_location": path_location(ref_root),
        "val_root_location": path_location(val_root),
        "slot_count": int(slot_count),
        "ref_photos": int(ref_photos),
        "val_photos": int(val_photos),
        "total_photos": int(ref_photos) + int(val_photos),
        "elapsed_sec": round(float(elapsed_s), 2),
        "kla_used": bool(kla_used),
        "ocr_used": bool(ocr_used),
    }


def record(rec: dict, *, elapsed_s: float, uploader=None) -> Optional[Path]:
    """기록(로컬) + (선택) 업로드.  캐시 빠른 매치(elapsed < CACHE_FAST_SEC)면 건너뜀.

    성공 시 로컬 파일 경로 반환, 건너뛰거나 실패 시 None.  모든 예외는 삼킨다
    (로깅 실패가 본 기능을 막지 않도록)."""
    try:
        if float(elapsed_s) < CACHE_FAST_SEC:
            return None
        fname = time.strftime("%Y%m%d-%H%M%S") + ".json"
        fpath = _log_dir() / fname
        text = json.dumps(rec, ensure_ascii=False, indent=2)
        fpath.write_text(text, encoding="utf-8")
        if uploader is not None:
            try:
                uploader(machine_id(), fname, text)
            except Exception:
                pass
        return fpath
    except Exception:
        return None
