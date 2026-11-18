"""Scan Crop 영속 캐시 — Overkill 튜닝이 같은 웨이퍼를 다시 열 때 Scan 원본(NAS)을 또 읽지 않게 한다.

키 = (웨이퍼 폴더, Scan 목록 파일의 mtime·크기, 결함 X·Y, 픽셀 크기, Crop 한 변).  목록이 같으면 같은 계산이다
(:mod:`scan_image` 는 목록 · 픽셀 크기 · 결함 좌표 · 원본 JPEG 크기만으로 정해진다).  **적중 때 Crop 을 잘라 온 원본 파일의
크기·mtime 이 그대로인지 다시 확인한다** — 원본이 바뀌었으면 적중으로 치지 않는다.

Crop 은 **무손실 PNG** 다(JPEG 로 다시 저장하면 픽셀이 달라진다).  쓸 때는 임시 파일에 완성한 뒤 ``os.replace`` —
쓰다 끊긴 파일이 적중으로 믿기지 않게(``image_io._ensure_resized`` 와 같은 패턴).  읽다 실패하면 없는 것으로 본다.
캐시 폴더 정리는 ``cache.prune_old_cache`` 가 한다(TTL).  Qt 를 쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from ..utils import paths
from .scan_image import CROP_SIDE_UM, SCAN_LIST_NAME

__all__ = ["manifest_sig", "get", "put"]

_LOG = logging.getLogger("aoi.scan")
_VERSION = "1"


def _sig_of(path) -> Optional[str]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return f"{st.st_mtime_ns}:{st.st_size}"


def manifest_sig(folder) -> Optional[str]:
    """Scan 목록 파일의 mtime·크기 — 폴더당 한 번 구해 :func:`get` · :func:`put` 에 넘긴다.  없으면 None."""
    return _sig_of(Path(folder) / SCAN_LIST_NAME)


def _path(folder, sig: str, xy, px, side_um: float) -> Path:
    h = hashlib.sha1()
    h.update(f"{_VERSION}|{os.path.abspath(str(folder))}|{sig}|{xy[0]!r}|{xy[1]!r}|{px[0]!r}|{px[1]!r}|{side_um!r}".encode("utf-8", "replace"))
    return paths.scan_crop_cache_dir() / f"{h.hexdigest()}.png"


def get(folder, sig: Optional[str], xy, px, side_um: float = CROP_SIDE_UM):
    """캐시된 ``(PIL Crop, 원본 경로)`` — 없거나 원본이 바뀌었거나 파일이 깨졌으면 None."""
    if sig is None or px is None:
        return None
    try:
        from PIL import Image
        p = _path(folder, sig, xy, px, side_um)
        with Image.open(p) as im:
            im.load()
            info = dict(im.text)
            src, src_sig = info.get("src", ""), info.get("src_sig", "")
            crop = im.copy()
        if not src or _sig_of(src) != src_sig:
            return None
        return crop, src
    except Exception:       # noqa: BLE001 — 없음 · 깨짐 · 읽기 실패는 모두 '적중 아님'(다시 자른다)
        return None


def put(folder, sig: Optional[str], xy, px, crop, src, side_um: float = CROP_SIDE_UM) -> None:
    """Crop 을 저장한다.  실패해도 화면은 그대로(캐시일 뿐)."""
    if sig is None or px is None:
        return
    src_sig = _sig_of(src)
    if src_sig is None:
        return
    try:
        from PIL import PngImagePlugin
        info = PngImagePlugin.PngInfo()
        info.add_text("src", str(src))
        info.add_text("src_sig", src_sig)
        out = _path(folder, sig, xy, px, side_um)
        tmp = out.with_name(f"{out.stem}.{os.getpid()}-{threading.get_ident()}.part.png")
        try:
            crop.save(str(tmp), format="PNG", pnginfo=info, compress_level=1)
            os.replace(tmp, out)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
    except Exception:       # noqa: BLE001
        _LOG.debug("Scan Crop 캐시 저장 실패", exc_info=True)
