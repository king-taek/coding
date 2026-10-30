"""업데이트 안내 시트 — 업데이트 후 첫 실행 때 한 번(사용자 결정: 하이라이트형).

위: 강조색 띠에 대표 기능(제목·설명·그림).  아래: '그 밖에 달라진 점' 한 줄씩과
주황 '주의' 띠.  내용은 :mod:`i18n.whats_new` 가, 합치기는 :mod:`utils.whats_new` 가 한다.
색은 전부 테마 토큰이라 다크 모드에서도 그대로 맞는다.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (QAbstractScrollArea, QDialog, QFrame, QHBoxLayout, QLabel,
                             QScrollArea, QVBoxLayout, QWidget)

from ... import i18n
from ...utils import paths
from ...utils.whats_new import View
from .. import theme
from .neon_button import NeonButton

_WIDTH = 560
_IMAGE_W = 120


def _rgba(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def _label(text: str, *, size: int, color: str, weight: int = 400,
           wrap: bool = True) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(wrap)
    lb.setTextFormat(Qt.TextFormat.PlainText)
    # ★ 글꼴 이름을 같이 준다 — 빼면 앱 QSS 의 글꼴로 돌아가 굵기 지정이 먹지 않는다.
    lb.setStyleSheet(f"color:{color}; font-family:{theme.FONT_BODY}; font-size:{size}px;"
                     f" font-weight:{weight}; background:transparent; border:0;")
    return lb


class WhatsNewDialog(QDialog):
    def __init__(self, view: View, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.WHATS_NEW_TITLE)
        self.setFixedWidth(_WIDTH)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._hero(view))

        # ★ '그 밖에' 목록만 스크롤한다 — 밀린 업데이트가 많으면 안내창이 화면 높이에 잘리는데,
        #   스크롤이 없으면 줄마다 높이가 눌려 글자가 사라진다.  
        #   주의 띠도 같은 스크롤 안에 둔다 — 바깥에 두면 여러 개일 때 목록 자리를 다 먹는다.
        inner = QWidget()
        inner.setObjectName("whatsNewList")
        inner.setStyleSheet("#whatsNewList { background:transparent; }")
        body = QVBoxLayout(inner)
        body.setContentsMargins(28, 18, 28, 4)
        body.setSpacing(10)
        if view.others:
            body.addWidget(_label(i18n.KO.WHATS_NEW_OTHERS, size=12,
                                  color=theme.MUTE, weight=700))
            dots = (theme.PASS, theme.WARN, theme.ACCENT, theme.INK2)
            for k, (area, line) in enumerate(view.others):
                body.addLayout(self._other_row(area, line, dots[k % len(dots)]))
        for text in view.notices:
            body.addSpacing(4)
            body.addWidget(self._notice(text))
        body.addStretch(1)
        self.scroll = QScrollArea(self)
        self.scroll.setWidget(inner)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 내용만큼이 기본 높이, 모자라면 줄어들며 스크롤이 생긴다.
        self.scroll.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self.scroll.setMinimumHeight(120)
        self.scroll.setStyleSheet("QScrollArea { background:transparent; border:0; }")
        root.addWidget(self.scroll, 1)

        bar = QHBoxLayout()
        bar.setContentsMargins(28, 12, 28, 20)
        bar.addStretch(1)
        self.ok_btn = NeonButton(i18n.KO.WHATS_NEW_OK, role="primary")
        self.ok_btn.setMinimumWidth(112)
        self.ok_btn.setMinimumHeight(theme.PROFILE.control_h_lg)
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        bar.addWidget(self.ok_btn)
        root.addLayout(bar)

    # ------------------------------------------------------------------
    def _hero(self, view: View) -> QFrame:
        hero = QFrame(self)
        hero.setObjectName("whatsNewHero")
        hero.setStyleSheet(f"#whatsNewHero {{ background:{theme.ACCENT}; border:0; }}")
        h = QHBoxLayout(hero)
        h.setContentsMargins(28, 24, 24, 24)
        h.setSpacing(18)
        col = QVBoxLayout()
        col.setSpacing(6)
        on = theme.ON_ACCENT
        self.kicker = _label(i18n.KO.WHATS_NEW_KICKER_FMT.format(date=view.date),
                             size=11, color=_rgba(on, 0.75), weight=700, wrap=False)
        self.headline = _label(view.headline, size=22, color=on, weight=700)
        self.summary = _label(view.summary, size=12, color=_rgba(on, 0.85))
        for w in (self.kicker, self.headline, self.summary):
            col.addWidget(w)
        col.addStretch(1)
        h.addLayout(col, 1)
        self.image = None
        if view.image:
            pm = QPixmap(str(paths.logo_path(view.image)))   # ui/assets 의 파일
            if not pm.isNull():
                pm.setDevicePixelRatio(pm.width() / float(_IMAGE_W))
                self.image = QLabel(hero)
                self.image.setPixmap(pm)
                self.image.setStyleSheet("background:transparent; border:0;")
                h.addWidget(self.image, 0, Qt.AlignmentFlag.AlignTop)
        return hero

    @staticmethod
    def _other_row(area: str, line: str, color: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background:{color}; border-radius:4px;")
        row.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        if area:
            row.addWidget(_label(area, size=13, color=theme.INK, weight=700, wrap=False))
            row.addWidget(_label("·", size=13, color=theme.MUTE, wrap=False))
        row.addWidget(_label(line, size=13, color=theme.INK), 1)
        return row

    @staticmethod
    def _notice(text: str) -> QFrame:
        f = QFrame()
        f.setObjectName("whatsNewNotice")
        f.setStyleSheet(f"#whatsNewNotice {{ background:{theme.WARN_TINT}; border:0;"
                        f" border-radius:{theme.PROFILE.radius_sm}px; }}")
        h = QHBoxLayout(f)
        h.setContentsMargins(12, 9, 12, 9)
        h.setSpacing(8)
        h.addWidget(_label(i18n.KO.WHATS_NEW_NOTICE, size=11, color=theme.WARN,
                           weight=700, wrap=False))
        h.addWidget(_label(text, size=12, color=theme.INK, weight=600), 1)
        return f
