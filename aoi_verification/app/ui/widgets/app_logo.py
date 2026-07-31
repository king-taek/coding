"""상단 앱 로고 — **페이지 콘텐츠 안**에 놓이는 머리글.

사용자 요청: "프로그램 상단에 로고 있는 칸은 스크롤 영향이 없는 고정 칸인데,
고정이 아니도록 바꾸고 싶음".

한때 메인 창이 페이지 스택 **밖**에 로고를 하나 두고 모든 단계에서 같은 자리에
보이게 했다.  그래서 아래 내용을 스크롤해도 로고는 그대로 붙어 있었다.  지금은
각 페이지가 자기 콘텐츠 맨 위에 로고를 놓는다:

- 전체 스크롤이 있는 화면(설정·매치 검토)은 스크롤 host 안에 넣어 **위로 밀려
  올라간다** — 스크롤할수록 화면을 넓게 쓴다.
- 스크롤이 없는 화면(선별·매칭·결과)은 루트 맨 위에 놓아 보이는 결과가 같다.

색은 만드는 시점의 ``theme.COLOR_MODE`` 를 따른다.  다크 모드 전환은 페이지를
통째로 다시 만들므로(``MainWindow._recreate_pages``) 로고도 함께 새로 만들어진다.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from .. import theme
from ...utils import paths

# 로고 표시 높이(논리 px).
LOGO_H = 44


def build_logo_label(parent: QWidget | None = None) -> QLabel:
    """상단 로고 라벨.  파일을 못 읽으면 **숨긴 빈 라벨**을 돌려준다.

    로고 마크가 거의 검정이라 어두운 화면에서는 그대로 두면 배경에 묻힌다.
    알파는 건드리지 않고 RGB 만 반전해 밝은 마크로 뒤집는다."""
    label = QLabel(parent)
    label.setProperty("role", "appLogo")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)

    pm = QPixmap(str(paths.logo_path("logo_clear.png")))
    if pm.isNull():
        label.hide()
        return label
    if theme.COLOR_MODE == "dark":
        img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        img.invertPixels(QImage.InvertMode.InvertRgb)
        pm = QPixmap.fromImage(img)
    dpr = _device_pixel_ratio(parent)
    pm = pm.scaledToHeight(int(LOGO_H * dpr),
                           Qt.TransformationMode.SmoothTransformation)
    pm.setDevicePixelRatio(dpr)
    label.setPixmap(pm)
    return label


def _device_pixel_ratio(parent: QWidget | None) -> float:
    """부모가 아직 화면에 없을 수 있다 — 그때는 주 화면 값으로 폴백."""
    if parent is not None:
        try:
            dpr = parent.devicePixelRatioF()
            if dpr:
                return dpr
        except RuntimeError:
            pass
    app = QApplication.instance()
    if app is not None:
        scr = app.primaryScreen()
        if scr is not None:
            return scr.devicePixelRatio() or 1.0
    return 1.0
