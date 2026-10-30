"""글자 렌더링 4가지 방식을 한 창에 나란히 띄운다 (현택님 PC 확인용, 단일 파일).

실행: VS Code 에서 이 파일을 열고 실행(F5).  PyQt6 가 필요하다(앱과 같은 파이썬).
창에서 가장 부드러운 줄 번호를 알려 주면 그 방식으로 앱을 맞춘다.
"""
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import (QApplication, QGridLayout, QLabel, QScrollArea,
                             QWidget)

TEXT = "교차검증 (양방향)  허용 오차 200 µm  AOI 레시피 검증  일부 슬롯만"
BG, INK = "#ECE8E1", "#1E1E1E"          # 앱 설정 화면과 비슷한 바탕·글자색
SIZES = (13, 15)
ZOOM = 4

H = QFont.HintingPreference
S = QFont.StyleStrategy
MODES = [
    ("① 지금 앱 (기본값)", H.PreferDefaultHinting, None),
    ("② 이번 수정 (세로 힌팅)", H.PreferVerticalHinting, None),
    ("③ 회색 번짐 (지금 + 무지개색 끔)", H.PreferDefaultHinting, S.NoSubpixelAntialias),
    ("④ 세로 힌팅 + 회색 번짐", H.PreferVerticalHinting, S.NoSubpixelAntialias),
]

# 앱에 동봉된 NanumSquare 를 찾는다.  없으면 맑은 고딕으로 비교한다(차이는 같은 방식으로 난다).
FONT_DIRS = [
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path("C:/"),
    Path("D:/"),
]


def find_nanum() -> str | None:
    for root in FONT_DIRS:
        for pat in ("*/aoi_verification/app/ui/assets/font/NanumSquareR.ttf",
                    "*/*/aoi_verification/app/ui/assets/font/NanumSquareR.ttf",
                    "*/*/*/aoi_verification/app/ui/assets/font/NanumSquareR.ttf"):
            try:
                hits = list(root.glob(pat))
            except OSError:
                hits = []
            if hits:
                return str(hits[0])
    return None


def main() -> None:
    app = QApplication(sys.argv)
    family = "Malgun Gothic"
    path = find_nanum()
    if path:
        for name in ("NanumSquareR.ttf", "NanumSquareB.ttf"):
            QFontDatabase.addApplicationFont(str(Path(path).with_name(name)))
        family = "NanumSquare"

    page = QWidget()
    page.setStyleSheet(f"background:{BG}; color:{INK};")
    grid = QGridLayout(page)
    grid.setVerticalSpacing(14)
    head = QLabel(f"글꼴: {family}   ·   배율: {app.devicePixelRatio():.2f}   ·   "
                  f"Qt {sys.modules['PyQt6.QtCore'].QT_VERSION_STR}")
    grid.addWidget(head, 0, 0, 1, 2)

    row = 1
    for title, hint, strategy in MODES:
        cap = QLabel(title)
        f = QFont(); f.setPixelSize(14); f.setBold(True); cap.setFont(f)
        grid.addWidget(cap, row, 0, 1, 2)
        row += 1
        for px in SIZES:
            font = QFont()
            font.setFamilies([family, "Malgun Gothic"])
            font.setPixelSize(px)
            font.setHintingPreference(hint)
            if strategy is not None:
                font.setStyleStrategy(strategy)
            live = QLabel(TEXT)
            live.setFont(font)
            live.adjustSize()
            grid.addWidget(QLabel(f"{px}px"), row, 0)
            grid.addWidget(live, row, 1)
            row += 1
        # 확대 보기 — 같은 줄을 4배로 (13px)
        font = QFont(); font.setFamilies([family, "Malgun Gothic"]); font.setPixelSize(13)
        font.setHintingPreference(hint)
        if strategy is not None:
            font.setStyleStrategy(strategy)
        probe = QLabel("교차검증 (양방향)")
        probe.setFont(font)
        probe.setStyleSheet(f"background:{BG}; color:{INK};")
        probe.adjustSize()
        pm = probe.grab()
        zoom = QLabel()
        zoom.setPixmap(pm.scaled(pm.width() * ZOOM, pm.height() * ZOOM,
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.FastTransformation))
        grid.addWidget(QLabel("확대"), row, 0)
        grid.addWidget(zoom, row, 1)
        row += 1

    scroll = QScrollArea()
    scroll.setWidget(page)
    scroll.setWidgetResizable(True)
    scroll.setWindowTitle("글자 렌더링 비교 — 가장 부드러운 번호를 알려 주세요")
    scroll.resize(1000, 900)
    scroll.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
