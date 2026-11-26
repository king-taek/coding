"""사진 우클릭 메뉴 한 벌 — [크게 보기] / [Defect 정보] / [GL 히스토그램].

현택님 2026-11-24 결정(D1b·D1c·D1d): 사진이 있는 모든 화면(매치 검토 · 후보 선별 ·
Overkill · Zone · 매치 없음 검토 …)이 **같은 메뉴**를 띄운다.  화면마다 메뉴를 따로 만들지
말고 :func:`ask` 하나를 부른다(회귀 가드 ``test_photo_mouse_rules``).

- [크게 보기] 는 화면마다 여는 창이 달라(단일 · 좌우 비교 · Scan 범위) 고르면 ``True`` 만
  돌려주고 여는 일은 부른 쪽이 한다.
- 나머지 둘은 여기서 그 사진으로 도구를 연다.  사진 경로를 모르는 그림(Scan 에서 잘라 온
  Crop 등)은 두 항목이 꺼진다.
- 화면에만 있는 항목은 ``extras`` 로 구분선 뒤에 붙인다 — 지우면 기능이 사라지는 것만.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional

from PyQt6.QtWidgets import QMenu, QWidget

from ... import config, i18n
from . import sheet_host as sheets


def build(owner: QWidget, path=None,
          extras: Iterable[tuple[str, Callable[[], None]]] = ()) -> tuple[QMenu, dict]:
    """메뉴와 ``{"view"|"info"|"gl"|extra 이름: QAction}`` — 테스트가 항목을 본다."""
    menu = QMenu(owner)
    acts = {"view": menu.addAction(i18n.KO.CTX_VIEW_LARGER),
            "info": menu.addAction(i18n.KO.CTX_PHOTO_INFO),
            "gl": menu.addAction(i18n.KO.CTX_PHOTO_GL)}
    for key in ("info", "gl"):
        acts[key].setEnabled(path is not None)
    acts["gl"].setVisible(config.GL_HISTOGRAM_READY)     # 준비 전에는 숨긴다(사진 정보 화면의 GL 버튼과 같게)
    extras = list(extras)
    if extras:
        menu.addSeparator()
        for text, fn in extras:
            a = menu.addAction(text)
            a.setData(fn)
            acts[text] = a
    return menu, acts


def ask(owner: QWidget, global_pos, path=None,
        extras: Iterable[tuple[str, Callable[[], None]]] = ()) -> bool:
    """메뉴를 띄운다.  [크게 보기] 면 ``True`` — 나머지는 여기서 처리하고 ``False``."""
    menu, acts = build(owner, path, extras)
    chosen = menu.exec(global_pos)
    menu.deleteLater()
    if chosen is None:
        return False
    if chosen is acts["view"]:
        return True
    if chosen is acts["info"]:
        open_info(owner, path)
    elif chosen is acts["gl"]:
        open_gl(owner, path)
    elif callable(chosen.data()):
        chosen.data()()
    return False


def open_large(owner: QWidget, path) -> None:
    """사진 한 장을 큰 화면(``FullscreenViewer`` — 휠 확대 · 끌어 옮기기)으로.  더블클릭 · [크게 보기] 공용."""
    if path is None:
        return
    from .zoom_window import FullscreenViewer
    viewer = FullscreenViewer(Path(path), _host(owner))
    sheets.run(viewer, full_bleed=True)
    viewer.deleteLater()


def _host(owner: QWidget) -> Optional[QWidget]:
    """도구 창의 부모 — 타일은 메뉴를 닫은 뒤 다시 그려질 수 있어 창을 부모로 둔다."""
    return owner.window() if owner is not None else None


def open_info(owner: QWidget, path) -> None:
    """[Defect 정보] — Defect Map 의 점 더블클릭과 같은 창."""
    if path is None:
        return
    from .image_info_dialog import ImageInfoDialog
    sheets.run(ImageInfoDialog(_host(owner), image_path=str(Path(path))), full_bleed=True)


def open_gl(owner: QWidget, path) -> None:
    """[GL 히스토그램] — 사진 정보 화면의 [GL 보기] 와 같은 창."""
    if path is None:
        return
    from .gl_viewer_dialog import GlViewerDialog
    sheets.run(GlViewerDialog(_host(owner), image_path=str(Path(path))), full_bleed=True)
