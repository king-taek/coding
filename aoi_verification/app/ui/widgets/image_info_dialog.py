"""단일 사진 정보 — 사진 한 장에서 결함 정보를 읽어 보여주는 도구.

전체 검증 흐름(스캔 → 매칭 → 엑셀)을 돌리지 않고도 사진 한 장의 좌표·measurement 를
바로 확인하기 위한 화면.  표기 문자열은 :mod:`coords.single_info` 가 만든다 —
**결과 엑셀 미매칭 행과 같은 생산자**라 두 곳의 값이 어긋나지 않는다.

정보의 출처는 사진이 들어 있는 폴더의 정보파일(ColorImageGrabingInfo.ini / KLA 정보
파일 / Surface.flt)이다.  사진만 따로 복사한 폴더에서는 LIVE 형식 파일명일 때만 좌표가
나온다 — 그래서 힌트로 미리 알린다.

조회는 폴더 단위 ``lru_cache`` 를 타는 파서 몇 번 호출이라 즉시 끝난다.  장시간 작업이
아니므로 백그라운드 스레드·``LoadingOverlay`` 를 쓰지 않는다.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame,
                             QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                             QScrollArea, QVBoxLayout, QWidget)

from ... import i18n
from ...config import CONFIG
from ...coords import single_info
from ...utils import image_io as _io
from .. import theme
from . import sheet_host as sheets
from .neon_button import NeonButton

_PREVIEW_PX = 420


class ImageInfoDialog(QDialog):
    """사진 1장 → 결함 정보 패널.  파일 선택 버튼과 드래그&드롭 둘 다 받는다."""

    def __init__(self, parent=None, *, image_path: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.IMAGE_INFO_TITLE)
        self.setAcceptDrops(True)
        self._path: Path | None = None
        self._rows: list = []
        self._build()
        if image_path:
            self.show_image(Path(image_path))

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)

        hint = QLabel(i18n.KO.IMAGE_INFO_HINT, self)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.MUTE};")
        root.addWidget(hint)

        # 경로 줄 — 표시 전용(선택은 버튼/드롭으로만).
        top = QHBoxLayout()
        self.path_edit = QLineEdit(self)
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText(i18n.KO.IMAGE_INFO_PLACEHOLDER)
        top.addWidget(self.path_edit, stretch=1)
        self.pick_btn = NeonButton(i18n.KO.IMAGE_INFO_PICK, role="primary")
        self.pick_btn.clicked.connect(self._on_pick)
        top.addWidget(self.pick_btn)
        root.addLayout(top)

        # 본문: 좌(미리보기) / 우(정보) --------------------------------------
        body = QHBoxLayout()
        self.preview = QLabel(self)
        self.preview.setFixedSize(_PREVIEW_PX, _PREVIEW_PX)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet(
            f"border: 1px solid {theme.LINE}; border-radius: 6px;")
        body.addWidget(self.preview)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        body.addWidget(self._scroll, stretch=1)
        root.addLayout(body, stretch=1)

        # 하단 버튼 ----------------------------------------------------------
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.copy_btn = NeonButton(i18n.KO.IMAGE_INFO_COPY, role="ghost")
        self.copy_btn.clicked.connect(self._on_copy)
        bottom.addWidget(self.copy_btn)
        close_btn = NeonButton(i18n.KO.MSG_BTN_CLOSE, role="ghost")
        close_btn.clicked.connect(self.reject)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        self._render_rows()

    # ------------------------------------------------------------------
    # 입력 — 파일 선택 / 드래그&드롭
    # ------------------------------------------------------------------
    def _on_pick(self) -> None:
        # QFileDialog 는 시트로 감싸지 않는다 — OS 파일 브라우저를 그대로 쓴다
        # (sheet_host 모듈 docstring 의 명시적 결정).
        patterns = " ".join(f"*{e}" for e in CONFIG.image_extensions)
        path, _ = QFileDialog.getOpenFileName(
            self, i18n.KO.IMAGE_INFO_PICK, "",
            i18n.KO.IMAGE_INFO_FILE_FILTER_FMT.format(patterns=patterns))
        if path:
            self.show_image(Path(path))

    def dragEnterEvent(self, event):        # noqa: N802
        if self._dropped_image(event) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):             # noqa: N802
        path = self._dropped_image(event)
        if path is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self.show_image(path)

    @staticmethod
    def _dropped_image(event) -> Path | None:
        """드롭된 것 중 **첫 번째 사진 파일** 경로.  사진이 없으면 None."""
        data = event.mimeData()
        if not data.hasUrls():
            return None
        for url in data.urls():
            local = url.toLocalFile()
            if local and CONFIG.is_image(local):
                return Path(local)
        return None

    # ------------------------------------------------------------------
    def show_image(self, path: Path) -> None:
        """사진을 조회해 미리보기와 정보 패널을 갱신한다."""
        self._path = Path(path)
        self.path_edit.setText(str(self._path))
        self.preview.setPixmap(
            _io.load_thumb_qpixmap(self._path, _PREVIEW_PX, kind="mid"))
        self._rows = single_info.describe(self._path)
        self._render_rows()

    # ------------------------------------------------------------------
    def _render_rows(self) -> None:
        """정보 패널을 **통째로 새로 만들어** 갈아끼운다.

        위젯만 걷어내는 방식은 두 가지로 어긋난다 — ``deleteLater`` 는 지연이라 이전
        안내 문구가 새 내용 위에 겹쳐 보이고, ``setRowStretch`` 는 레이아웃에 남아
        다음 렌더의 행 간격을 벌린다.  ``QScrollArea.setWidget`` 이 이전 위젯을
        지우므로 새 호스트를 넘기는 편이 짧고 확실하다.
        """
        host = QWidget(self)
        grid = QGridLayout(host)
        grid.setContentsMargins(12, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)

        if self._path is None or not self._rows:
            notice = QLabel(i18n.KO.IMAGE_INFO_NO_FILE if self._path is None
                            else i18n.KO.IMAGE_INFO_EMPTY, host)
            notice.setWordWrap(True)
            notice.setStyleSheet(f"color: {theme.MUTE};")
            grid.addWidget(notice, 0, 0, 1, 2)
        else:
            for r, row in enumerate(self._rows):
                value = QLabel(row.value, host)
                value.setWordWrap(True)
                value.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse)
                if row.label:
                    label = QLabel(row.label, host)
                    label.setStyleSheet(f"color: {theme.MUTE};")
                    label.setAlignment(Qt.AlignmentFlag.AlignRight
                                       | Qt.AlignmentFlag.AlignTop)
                    grid.addWidget(label, r, 0)
                    grid.addWidget(value, r, 1)
                else:
                    grid.addWidget(value, r, 1)
        # 내용이 짧아도 위에서부터 쌓이도록 마지막에 남는 공간을 몰아준다.
        grid.setRowStretch(grid.rowCount(), 1)

        self._info_grid = grid
        self._scroll.setWidget(host)
        # 복사할 게 없으면 버튼이 아무 일도 안 하는 대신 눌리지 않게.
        self.copy_btn.setEnabled(bool(self._rows))

    # ------------------------------------------------------------------
    def as_text(self) -> str:
        """패널 내용을 복사용 텍스트로 — 라벨 있는 줄은 ``라벨\\t값``."""
        return "\n".join(
            f"{row.label}\t{row.value}" if row.label else row.value
            for row in self._rows)

    def _on_copy(self) -> None:
        if not self._rows:
            return
        QApplication.clipboard().setText(self.as_text())
        sheets.info(self, i18n.KO.IMAGE_INFO_TITLE, i18n.KO.IMAGE_INFO_COPIED)
