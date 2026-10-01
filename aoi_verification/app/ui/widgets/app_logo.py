"""상단 앱 로고 — **페이지 콘텐츠 안**에 놓이는 머리글.

사용자 요청: "프로그램 상단에 로고 있는 칸은 스크롤 영향이 없는 고정 칸인데,
고정이 아니도록 바꾸고 싶음".

한때 메인 창이 페이지 스택 **밖**에 로고를 하나 두고 모든 단계에서 같은 자리에
보이게 했다.  그래서 아래 내용을 스크롤해도 로고는 그대로 붙어 있었다.  지금은
각 페이지가 자기 콘텐츠 맨 위에 로고를 놓는다:

- 전체 스크롤이 있는 화면(설정·매치 검토)은 스크롤 host 안에 넣어 **위로 밀려
  올라간다** — 스크롤할수록 화면을 넓게 쓴다.
- 스크롤이 없는 화면(선별·매칭·결과)은 루트 맨 위에 놓아 보이는 결과가 같다.
- 설정 화면은 밴드가 아니라 **제목 줄의 왼쪽 끝**에 놓는다(``inline=True``).  사용자
  지적: "메인 로고가 너무 많은 부분을 차지함" — 44px 마크 하나가 한 줄(로고 + 눈금 +
  줄 간격 ≈ 89px)을 통째로 차지했다.  제목·배지와 한 줄에 앉히면 그 높이가
  본문에 돌아온다.  밴드용 여백·눈금은 QSS(``[inline="true"]``)가 지운다.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from ...utils import paths

# 로고 표시 높이(논리 px).
LOGO_H = 44


def build_logo_label(parent: QWidget | None = None, *,
                     inline: bool = False) -> QLabel:
    """상단 로고 라벨.  파일을 못 읽으면 **숨긴 빈 라벨**을 돌려준다.

    ``inline=True`` 면 밴드가 아니라 **줄 안의 항목**이다 — 왼쪽 정렬이고, 밴드용
    여백·눈금을 QSS 가 지운다(모듈 주석)."""
    label = QLabel(parent)
    label.setProperty("role", "appLogo")
    if inline:
        label.setProperty("inline", "true")
        label.setAlignment(Qt.AlignmentFlag.AlignLeft
                           | Qt.AlignmentFlag.AlignVCenter)
    else:
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)

    if not _paint(label):
        label.hide()
    return label


def _paint(label: QLabel) -> bool:
    """로고 픽스맵을 라벨에 넣는다.  실패하면 False."""
    pm = QPixmap(str(paths.logo_path("logo_clear.png")))
    if pm.isNull():
        return False
    dpr = _device_pixel_ratio(label.parentWidget() or label)
    pm = pm.scaledToHeight(int(LOGO_H * dpr),
                           Qt.TransformationMode.SmoothTransformation)
    pm.setDevicePixelRatio(dpr)
    label.setPixmap(pm)
    return True


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
