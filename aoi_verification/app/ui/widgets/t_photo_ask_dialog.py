"""``.t.`` 사진을 이번 검증에 포함할지 묻는 시트 — **예시 사진 한 장을 보여준다.**

사용자 요청: "폴더 안에 `.t.` 가 이름에 들어가 있는 사진들은 뺄지 말지 결정해야
하는데, 이런 사진들이 있을 때에는 검증 시작을 눌렀을 때 사용자에게 예시 사진 하나만
보여주면서 이런 사진들도 포함할 것인지 물어보도록 해줘. No 누르면 다 제외하고 진행."

★ **왜 글자만으로는 안 되는가.**  이 판정은 파일명 규칙(`models.slot.has_t_token`)일
  뿐이라, 그 사진이 실제로 무엇인지는 자재마다 다르다.  '항상 뺀다' 와 '항상 넣는다'
  사이를 세 번 오간 자리이므로(같은 함수의 주석 참조) 코드가 정하지 않고 그때그때
  묻되, **판단 근거를 함께 보여 준다** — 사진 한 장이 그 근거다.

★ 미리보기는 **중간 크기 캐시**를 쓴다(`image_io.get_mid_path`).  원본 AOI 사진은
  수 MB 라 팝업 하나 띄우려고 통째로 디코드할 이유가 없다(슬롯 매핑 미리보기가
  같은 이유로 고쳐진 적이 있다 — P-14).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QSizePolicy,
                             QVBoxLayout)

from ... import i18n
from .. import theme
from .neon_button import NeonButton
from .scalable_image import ScalableImage

__all__ = ["TPhotoAskDialog"]

_PREVIEW_LONG_EDGE = 360


class TPhotoAskDialog(QDialog):
    """포함할지 묻는다.  :attr:`include` 가 답 — 닫기/Esc 는 '제외'다.

    ★ 기본을 '제외' 로 두는 이유: 요청이 "No 누르면 다 제외하고 진행" 이고, 창을
    닫아 버린 경우를 '포함' 으로 해석하면 묻지 않은 것과 같아진다.  모르면 덜
    포함하는 쪽이 되돌리기 쉽다(다음 실행에서 다시 묻는다)."""

    def __init__(self, sample: Path, total: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.T_PHOTO_ASK_TITLE)
        self._include = False
        self._sample = Path(sample)
        self._build(total)

    @property
    def include(self) -> bool:
        return self._include

    # ------------------------------------------------------------------
    def _build(self, total: int) -> None:
        root = QVBoxLayout(self)
        m = theme.PROFILE.page_margin // 2
        root.setContentsMargins(m, m, m, m)
        root.setSpacing(12)

        head = QLabel(i18n.KO.T_PHOTO_ASK_HEAD_FMT.format(n=f"{total:,}"), self)
        head.setProperty("role", "title")
        head.setWordWrap(True)
        root.addWidget(head)

        body = QLabel(i18n.KO.T_PHOTO_ASK_BODY, self)
        body.setProperty("role", "muted")
        body.setWordWrap(True)
        root.addWidget(body)

        # 예시 사진 — 못 읽어도 창은 떠야 한다(묻는 것이 본체다).
        self._preview = ScalableImage(self)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setSizePolicy(QSizePolicy.Policy.Expanding,
                                    QSizePolicy.Policy.Expanding)
        self._preview.set_target_size(_PREVIEW_LONG_EDGE)
        self._load_preview()
        root.addWidget(self._preview, stretch=1)

        name = QLabel(self._sample.name, self)
        name.setProperty("role", "muted")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name.setWordWrap(True)
        root.addWidget(name)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        self.btn_exclude = NeonButton(i18n.KO.T_PHOTO_ASK_NO, role="ghost")
        self.btn_exclude.clicked.connect(self._on_exclude)
        self.btn_include = NeonButton(i18n.KO.T_PHOTO_ASK_YES, role="primary")
        self.btn_include.clicked.connect(self._on_include)
        row.addWidget(self.btn_exclude)
        row.addWidget(self.btn_include)
        root.addLayout(row)

    def _load_preview(self) -> None:
        """중간 크기 캐시로 미리보기.  실패하면 문구로 대신한다(전 구간 fail-safe)."""
        try:
            from ...utils.image_io import get_mid_path
            self._preview.set_image(Path(get_mid_path(self._sample)))
            if self._preview.pixmap() is not None:
                return
        except Exception:
            pass
        self._preview.clear_image()
        self._preview.setText(i18n.KO.T_PHOTO_ASK_NO_PREVIEW)

    # ------------------------------------------------------------------
    def _on_include(self) -> None:
        self._include = True
        self.accept()

    def _on_exclude(self) -> None:
        self._include = False
        self.accept()
