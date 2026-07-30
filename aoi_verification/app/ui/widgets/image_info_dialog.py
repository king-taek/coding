"""단일 사진 정보 — 사진 한 장의 결함 좌표·계측값을 제도 시트로 읽는 도구.

전체 검증 흐름(스캔 → 매칭 → 엑셀)을 돌리지 않고도 사진 한 장의 수치를 바로 확인한다.
표기는 :mod:`coords.single_info` 가 만든다 — **결과 엑셀 미매칭 행과 같은 생산자**라
두 곳의 수치가 어긋나지 않는다.

형태는 앱의 '도면' 언어를 그대로 쓴다(새 시각 언어를 만들지 않는다):

- 계측은 **타이틀블록 + 하이라인 눈금 표**다 — ``role="listHeader"``/``colHead`` 로
  덮개를 씌우고, 행마다 ``QFrame[role="row"]`` 의 아래 눈금이 항목을 가른다.
- **수치는 모노**(``role="mono"``)로 찍는다.  이게 이 화면의 초점이다 — 비례 서체로
  찍으면 계측기가 아니라 산문으로 읽힌다.
- 측정정보 유무는 문장이 아니라 **판정 스탬프**(``chip="ok"``/``"none"``)로 한 번에.
- 미리보기와 표는 ``role="vrule"`` 1px 눈금으로 가른다(검토 화면의 점수 컬럼과 같은 것).

정보의 출처는 사진이 든 폴더의 정보파일(ColorImageGrabingInfo.ini / KLA 정보파일 /
Surface.flt)이다.  사진만 따로 복사한 폴더에서는 LIVE 형식 파일명일 때만 좌표가 나온다
— 그래서 빈 상태가 그 사실을 가르친다(로드 뒤에는 자리를 차지하지 않는다).

조회는 폴더 단위 ``lru_cache`` 를 타는 파서 몇 번이라 즉시 끝난다.  장시간 작업이
아니므로 백그라운드 스레드·``LoadingOverlay`` 를 쓰지 않는다.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFontMetrics, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame,
                             QGridLayout, QHBoxLayout, QLabel, QScrollArea,
                             QSizePolicy, QVBoxLayout, QWidget)

from ... import i18n
from ...config import CONFIG
from ...coords import single_info
from ...utils import image_io as _io
from .. import theme
from .neon_button import NeonButton

# 미리보기 — 고정 정사각형이 아니라 폭 하한만 두고 세로는 표와 함께 늘어난다.
_PREVIEW_MIN_W = 300
_PREVIEW_MAX_W = 460
# 복사 확인은 모달이 아니라 버튼 옆에서 잠깐 스쳐 지나간다.
_TOAST_MS = 1800


class _ElidingValue(QLabel):
    """값 라벨 — 폭을 넘으면 **가운데** 생략하고 전체는 툴팁으로.

    경로·파일명은 공백이 없어 ``setWordWrap`` 이 듣지 않는다.  줄바꿈에 맡기면
    QLabel 의 최소 폭이 문자열 길이만큼 부풀어 스크롤 영역이 **가로로 넘친다**
    (실측: 341자 경로에서 내용 1357px vs 뷰포트 652px).  앞뒤를 남기는 가운데
    생략이라 폴더의 시작과 파일명의 끝을 둘 다 읽을 수 있다 —
    ``_SlotTile._elide`` 와 같은 판단이다.
    """

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self._full = text
        self.setToolTip(text)
        # 가로로 밀지 않게 최소 폭을 스스로 낮춘다.
        self.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        self._apply()

    def text_full(self) -> str:
        return self._full

    def _apply(self) -> None:
        fm = QFontMetrics(self.font())
        avail = max(40, self.width())
        self.setText(fm.elidedText(self._full, Qt.TextElideMode.ElideMiddle,
                                   avail))

    def resizeEvent(self, event):       # noqa: N802
        super().resizeEvent(event)
        self._apply()


class ImageInfoDialog(QDialog):
    """사진 1장 → 결함 계측 시트.  파일 선택 버튼과 드래그&드롭 둘 다 받는다."""

    def __init__(self, parent=None, *, image_path: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.IMAGE_INFO_TITLE)
        self.setAcceptDrops(True)
        self._path: Path | None = None
        self._groups: list = []
        self._build()
        if image_path:
            self.show_image(Path(image_path))

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        # 동급 시트와 같은 여백 — bulk_select_dialog / slot_select_dialog 가 16/10.
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(theme.PROFILE.section_gap // 2)

        root.addLayout(self._build_head())

        body = QHBoxLayout()
        body.setSpacing(theme.PROFILE.card_pad)
        body.addWidget(self._build_preview())
        rule = QFrame(self)
        rule.setProperty("role", "vrule")
        body.addWidget(rule)
        body.addWidget(self._build_sheet(), stretch=1)
        root.addLayout(body, stretch=1)

        root.addLayout(self._build_actions())

        # 파워 유저 경로 — 사진 열기를 손에서 떼지 않고.  ★ Ctrl+C 는 묶지 않는다:
        # 값 라벨이 선택 복사를 지원하므로, 창 단축키로 가로채면 사용자가 고른 수치
        # 대신 시트 전체가 복사돼 버린다.  전체 복사는 버튼이 맡는다.
        QShortcut(QKeySequence.StandardKey.Open, self, self._on_pick)
        self._render()
        # 열자마자 주요 동작에 포커스 — 시트 호스트는 다이얼로그 자신에 포커스를
        # 주므로, 그대로 두면 첫 Tab 이 허비된다.
        QTimer.singleShot(0, self.pick_btn.setFocus)

    def _build_head(self) -> QVBoxLayout:
        """부제 한 줄 — 시트가 무엇인지 말한다.

        ★ 표제는 여기서 그리지 않는다.  `windowTitle` 이 있으면 `sheet_host` 가
          제목 막대를 씌우므로(chrome), 안에 또 그리면 같은 제목이 두 번 뜬다.
        """
        head = QVBoxLayout()
        head.setSpacing(2)
        sub = QLabel(i18n.KO.IMAGE_INFO_SUBTITLE, self)
        sub.setProperty("role", "subtitle")
        sub.setWordWrap(True)
        head.addWidget(sub)
        return head

    def _build_preview(self) -> QWidget:
        host = QWidget(self)
        host.setMinimumWidth(_PREVIEW_MIN_W)
        host.setMaximumWidth(_PREVIEW_MAX_W)
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.preview = QLabel(host)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(_PREVIEW_MIN_W, _PREVIEW_MIN_W)
        # 프레임이 사진을 **딱 감싸도록** 위쪽 정렬로 두고, 크기는 픽스맵에 맞춘다.
        lay.addWidget(self.preview, alignment=Qt.AlignmentFlag.AlignTop
                      | Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)
        self._set_drop_active(False)
        return host

    def _build_sheet(self) -> QWidget:
        """계측 표 — 스크롤 안에서 그룹별로 다시 그린다."""
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        # 가로 스크롤은 **절대** 만들지 않는다(CLAUDE.md 'UI 사용성 관습').
        # 공백 없는 긴 경로는 줄바꿈되지 않으므로 값 쪽에서 가운데 생략한다(_elide).
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return self._scroll

    def _build_actions(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        self.pick_btn = NeonButton(i18n.KO.IMAGE_INFO_PICK, role="primary")
        self.pick_btn.setMinimumHeight(theme.PROFILE.control_h_lg)
        self.pick_btn.setMinimumWidth(160)
        self.pick_btn.clicked.connect(self._on_pick)
        bar.addWidget(self.pick_btn)
        # 복사 확인 — 모달 대신 버튼 옆에서 잠깐 뜨는 줄(자리는 늘 예약).
        self._toast = QLabel("", self)
        self._toast.setProperty("role", "muted")
        self._toast.setMinimumWidth(96)
        bar.addWidget(self._toast)
        bar.addStretch(1)
        self.copy_btn = NeonButton(i18n.KO.IMAGE_INFO_COPY, role="ghost")
        self.copy_btn.setMinimumHeight(theme.PROFILE.control_h_lg)
        bar.addWidget(self.copy_btn)
        self.copy_btn.clicked.connect(self._on_copy)
        # ★ 바닥에 '닫기' 를 두지 않는다.  `windowTitle` 이 있으면 sheet_host 가
        #   제목줄에 ✕(닫기)를 붙이므로, 또 두면 닫기 어포던스가 둘이 된다.
        #   이 앱의 관습도 그렇다 — 확인/취소를 받는 '동작' 시트만 바닥 버튼을 두고,
        #   보기 전용 시트(unmatched_review·label_maker·dev_benchmark)는 두지 않는다.
        #   Esc 도 그대로 동작한다.
        # ★ Enter 를 autoDefault 에 맡기지 않는다 — 마지막으로 눌린 버튼이 다시
        #   실행돼 '복사'가 뜻하지 않게 재발동한다(slot_select_dialog 와 같은 함정).
        self.pick_btn.setDefault(True)
        self.copy_btn.setAutoDefault(False)
        return bar

    # ------------------------------------------------------------------
    # 입력 — 파일 선택 / 드래그&드롭
    # ------------------------------------------------------------------
    def _on_pick(self) -> None:
        # QFileDialog 는 시트로 감싸지 않는다 — OS 파일 브라우저를 그대로 쓴다
        # (sheet_host 모듈 docstring 의 명시적 결정).
        patterns = " ".join(f"*{e}" for e in CONFIG.image_extensions)
        start = str(self._path.parent) if self._path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, i18n.KO.IMAGE_INFO_PICK, start,
            i18n.KO.IMAGE_INFO_FILE_FILTER_FMT.format(patterns=patterns))
        if path:
            self.show_image(Path(path))

    def dragEnterEvent(self, event):        # noqa: N802
        if self._dropped_image(event) is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self._set_drop_active(True)

    def dragLeaveEvent(self, event):        # noqa: N802
        self._set_drop_active(False)
        event.accept()

    def dropEvent(self, event):             # noqa: N802
        self._set_drop_active(False)
        path = self._dropped_image(event)
        if path is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self.show_image(path)

    def _set_drop_active(self, on: bool) -> None:
        """드롭 대상임을 미리보기 면이 스스로 말하게 — 놓기 전에 보인다.

        프레임 관습은 검토 화면 썸네일과 같다(`match_review_page` 의 thumb_frame):
        비어 있으면 점선, 사진이 들어오면 실선.  드래그가 들어온 순간만 강조 잉크로
        바뀐다 — 새 색을 만들지 않고 ACCENT 를 그대로 쓴다.
        """
        radius = theme.PROFILE.radius_sm
        if on:
            self.preview.setStyleSheet(
                f"border: 1px solid {theme.ACCENT}; border-radius: {radius}px;"
                f" background-color: {theme.ACCENT_TINT}; color: {theme.ACCENT};")
        else:
            style = "solid" if self._path is not None else "dashed"
            self.preview.setStyleSheet(
                f"border: 1px {style} {theme.THUMB_FRAME};"
                f" border-radius: {radius}px; color: {theme.MUTE};")
        self.preview.setText(
            i18n.KO.IMAGE_INFO_DROP_HERE if (on and self._path is None) else "")

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
        """사진을 조회해 미리보기와 계측 표를 갱신한다."""
        self._path = Path(path)
        self._groups = single_info.describe(self._path)
        self.pick_btn.setText(i18n.KO.IMAGE_INFO_PICK_ANOTHER)
        self._render()

    def resizeEvent(self, event):           # noqa: N802
        super().resizeEvent(event)
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        """미리보기를 현재 창 폭에 맞춰 다시 스케일한다(비율 유지).

        ★ 고정 크기를 쓰면 안 된다 — 420px 고정이 좁은 창에서 폭의 절반 이상을
          먹고 수치를 잘라 냈다(실측 640px 폭에서 정보 패널 192px).  창을 따라
          줄고 늘어야 full_bleed 시트를 요구한 값을 한다.
        """
        avail = self.width() - 2 * 16
        box = int(max(_PREVIEW_MIN_W * 0.6,
                      min(_PREVIEW_MAX_W, avail * 0.36)))
        if self._path is None:
            self.preview.setFixedSize(box, box)
            return
        pix = _io.load_thumb_qpixmap(self._path, box, kind="mid")
        self.preview.setPixmap(pix)
        # 프레임이 사진을 딱 감싸도록 라벨을 픽스맵 크기로 — 레터박스 여백 제거.
        self.preview.setFixedSize(pix.size())
        self.preview.setAccessibleName(self._path.name)

    # ------------------------------------------------------------------
    def _render(self) -> None:
        """계측 표를 **통째로 새로 만들어** 갈아끼운다.

        위젯만 걷어내는 방식은 두 가지로 어긋난다 — ``deleteLater`` 는 지연이라 이전
        안내가 새 내용 위에 겹쳐 보이고, ``setRowStretch`` 는 레이아웃에 남아 다음
        렌더의 행 간격을 벌린다.  ``QScrollArea.setWidget`` 이 이전 위젯을 지우므로
        새 호스트를 넘기는 편이 짧고 확실하다.
        """
        host = QWidget(self)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(theme.PROFILE.section_gap // 2)

        if self._path is None:
            col.addWidget(self._notice(host, i18n.KO.IMAGE_INFO_NO_FILE))
        else:
            for group in self._groups:
                col.addWidget(self._group_block(host, group))
            # ★ 계측을 하나도 못 읽었으면 복구 안내를 띄운다.  '파일' 덩이는 경로만
            #   있으면 항상 생기므로, 덩이 개수로 판정하면 이 안내가 영원히 안 뜬다.
            if not single_info.has_measurements(self._groups):
                col.addWidget(self._notice(host, i18n.KO.IMAGE_INFO_EMPTY))
        col.addStretch(1)

        self._scroll.setWidget(host)
        self.copy_btn.setEnabled(bool(self._groups))
        self._refresh_preview()

    @staticmethod
    def _notice(parent: QWidget, text: str) -> QLabel:
        notice = QLabel(text, parent)
        notice.setWordWrap(True)
        notice.setProperty("role", "muted")
        notice.setAlignment(Qt.AlignmentFlag.AlignTop)
        return notice

    def _group_block(self, parent: QWidget, group) -> QWidget:
        """타이틀블록(그룹명 + 컬럼 머리) + 하이라인 눈금 행들."""
        block = QWidget(parent)
        lay = QVBoxLayout(block)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = QFrame(block)
        header.setProperty("role", "listHeader")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 6)
        name = QLabel(group.title, header)
        name.setProperty("role", "colHead")
        hl.addWidget(name)
        hl.addStretch(1)
        unit = QLabel(i18n.KO.IMAGE_INFO_COL_VALUE, header)
        unit.setProperty("role", "colHead")
        hl.addWidget(unit)
        lay.addWidget(header)

        for row in group.rows:
            lay.addWidget(self._row_widget(block, row))
        return block

    def _row_widget(self, parent: QWidget, row) -> QWidget:
        """한 행 — 아래 눈금이 항목을 가른다(면 없음, 제도 시트)."""
        frame = QFrame(parent)
        frame.setProperty("role", "row")
        grid = QGridLayout(frame)
        grid.setContentsMargins(0, theme.PROFILE.row_pad_v,
                                0, theme.PROFILE.row_pad_v)
        grid.setHorizontalSpacing(theme.PROFILE.card_pad)
        grid.setColumnStretch(1, 1)

        if row.label:
            label = QLabel(row.label, frame)
            label.setProperty("role", "muted")
            label.setAlignment(Qt.AlignmentFlag.AlignLeft
                               | Qt.AlignmentFlag.AlignTop)
            grid.addWidget(label, 0, 0)

        if row.stamp:
            # 판정 스탬프 — 문장 대신 칩 하나로.  짧은 고정 문구라 생략하지 않는다
            # (생략 라벨의 Ignored 폭 정책을 주면 칩이 0px 로 접힌다).
            chip = QLabel(row.value, frame)
            chip.setProperty("chip", row.stamp)
            holder = QHBoxLayout()
            holder.setContentsMargins(0, 0, 0, 0)
            holder.addWidget(chip)
            holder.addStretch(1)
            grid.addLayout(holder, 0, 0, 1, 2)
            return frame

        value = _ElidingValue(row.value, frame)
        value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        value.setAlignment(Qt.AlignmentFlag.AlignRight
                           | Qt.AlignmentFlag.AlignVCenter)
        if row.mono:
            value.setProperty("role", "mono")
        grid.addWidget(value, 0, 1)
        return frame

    # ------------------------------------------------------------------
    def as_text(self) -> str:
        """표 내용을 복사용 텍스트로 — ``라벨\\t값``, 그룹 사이는 빈 줄.

        라벨 없는 줄(판정 스탬프)도 앞에 탭을 둔다 — 엑셀에 붙여넣었을 때 값이
        전부 같은 열에 서게 하려는 것이다(안 그러면 스탬프만 A열로 밀린다).
        """
        blocks: list[str] = []
        for group in self._groups:
            lines = [f"[{group.title}]"]
            lines += [f"{r.label}\t{r.value}" for r in group.rows]
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def _on_copy(self) -> None:
        if not self._groups:
            return
        QApplication.clipboard().setText(self.as_text())
        self._toast.setText(i18n.KO.IMAGE_INFO_COPIED)
        QTimer.singleShot(_TOAST_MS, lambda: self._toast.setText(""))
