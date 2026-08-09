"""좌(기준)·우(후보) 나란히 크게보기 뷰어 (#1e/#4).

기준 사진은 고정하고, 후보를 이전/다음으로 순환하며 비교한다.  두 이미지 모두
원본 파일을 직접 디코드해 ‘최고 화질’ 로 보여준다(팝업이므로 비용 허용).
선택적으로 하단에 액션 버튼(예: ‘이 후보로 선택/매치’)을 두고, 누르면 현재
후보 ``ImageItem`` 을 ``action_requested`` 로 내보내고 닫는다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QColor, QKeySequence, QPainter, QPixmap, QShortcut)
from PyQt6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                             QSizePolicy, QVBoxLayout, QWidget)

from ... import i18n
from ...models.slot import ImageItem
from .. import theme
from .neon_button import NeonButton


def _decode_original(path: Path) -> QPixmap:
    pix = QPixmap(str(path))
    if pix.isNull():
        pix = QPixmap(800, 600)
        pix.fill(QColor(theme.PANEL))
    return pix


def _decode_fast(path: Path) -> QPixmap:
    """먼저 얹을 그림 — 미리 만들어 둔 mid 캐시(~800px)가 있으면 그것을 쓴다.

    ★ 예전엔 후보를 넘길 때마다 원본을 통째로 디코드해 클릭당 0.5~2초씩 멈췄다.
    ★ 이 모듈은 의도적으로 `image_io`(numpy·PIL)를 **모듈 최상단에서 import 하지
      않는다** — 팝업 경로가 무거워지지 않게 한 격리다.  여기서만 지역 import 하고,
      캐시가 없거나 실패하면 곧바로 원본 디코드로 되돌아간다."""
    try:
        from ...utils import image_io
        pix = QPixmap(str(image_io.get_mid_path(path)))
        if not pix.isNull():
            return pix
    except Exception:
        pass
    return _decode_original(path)


def fit_scale(pix_w: int, pix_h: int, box_w: int, box_h: int) -> float:
    """이미지를 박스에 꽉 채우는 배율(비율 유지).  순수 함수 — 헤드리스 테스트용.

    ``zoom_window.fit_scale`` 과 같은 계산이지만 그 모듈을 import 하지 않는다 —
    거긴 image_io(numpy·PIL) 를 끌고 온다."""
    if pix_w <= 0 or pix_h <= 0 or box_w <= 0 or box_h <= 0:
        return 1.0
    return min(box_w / float(pix_w), box_h / float(pix_h))


_ZOOM_STEP = 1.1
_SCALE_MIN = 0.02
_SCALE_MAX = 8.0


class _Pane(QWidget):
    """제목 + 비율 유지로 꽉 채우는 이미지 라벨 (원본 화질) + 휠 확대/드래그 이동.

    확대는 **패널마다 따로**다(사용자 결정: "마우스 있는 쪽만").  기준을 그대로
    두고 후보만 파고들어 보는 비교가 가능해진다.
    """

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self._pix: Optional[QPixmap] = None
        # 기준·후보가 동일한 크기로 보이도록 두 패널이 공유하는 목표 박스 (#3).
        self._box: Optional[QSize] = None
        # 맞춤 배율 대비 **배수**.  1.0 이면 기존과 똑같은 '박스에 꽉 맞춤' 경로를
        # 그대로 탄다(그 경로는 기존 테스트가 고정하고 있다).
        self._zoom = 1.0
        self._off_x = 0
        self._off_y = 0
        self._last_drag = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self._title = QLabel(title, self)
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setStyleSheet(f"color: {theme.INK}; font-weight: 700;")
        lay.addWidget(self._title)
        self._img = QLabel(self)
        self._img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img.setStyleSheet(
            f"background: {theme.VIEWER_BG}; border: 1px solid {theme.LINE};")
        # 크기 제약 없는 QLabel 에 라벨 크기로 스케일한 pixmap 을 넣으면
        # minimumSizeHint 이 그 pixmap 크기로 커져 리사이즈마다 창이 계속 커진다.
        # Ignored 정책 + 1×1 최소크기로 레이아웃 성장 피드백을 끊는다.
        self._img.setSizePolicy(QSizePolicy.Policy.Ignored,
                                QSizePolicy.Policy.Ignored)
        self._img.setMinimumSize(1, 1)
        lay.addWidget(self._img, stretch=1)

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_pixmap(self, pix: QPixmap) -> None:
        self._pix = pix
        self._redraw()

    def set_target_box(self, box: QSize) -> None:
        """두 패널이 같은 박스에 맞춰 스케일하도록 공통 목표 크기 주입 (#3)."""
        self._box = box
        self._redraw()

    def img_size(self) -> QSize:
        return self._img.size()

    def resizeEvent(self, e):  # noqa: N802
        self._redraw()
        super().resizeEvent(e)

    # -- 확대/이동 ------------------------------------------------------
    def zoom_by(self, factor: float) -> None:
        """맞춤 배율 대비 배수를 곱한다.  **유효 배율**(맞춤×배수)로 클램프한다.

        휠 이벤트가 아니라 이 메서드가 확대의 단일 출구다 — 헤드리스에서 그대로
        부를 수 있다."""
        target = self._target_box()
        if self._pix is None or target is None:
            return
        base = fit_scale(self._pix.width(), self._pix.height(),
                         target.width(), target.height())
        if base <= 0:
            return
        eff = max(_SCALE_MIN, min(_SCALE_MAX, base * self._zoom * factor))
        self._zoom = eff / base
        if abs(self._zoom - 1.0) < 1e-6:
            # 맞춤으로 돌아왔으면 이동도 푼다 — 안 그러면 사진이 화면 밖에 남는다.
            self._zoom = 1.0
            self._off_x = self._off_y = 0
        self._redraw()

    def wheelEvent(self, e):  # noqa: N802
        self.zoom_by(_ZOOM_STEP if e.angleDelta().y() > 0 else 1.0 / _ZOOM_STEP)
        e.accept()

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._last_drag = e.position().toPoint()

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._last_drag is None:
            return
        cur = e.position().toPoint()
        self._off_x += cur.x() - self._last_drag.x()
        self._off_y += cur.y() - self._last_drag.y()
        self._last_drag = cur
        self._redraw()

    def mouseReleaseEvent(self, e):  # noqa: N802
        self._last_drag = None

    # -- 그리기 ---------------------------------------------------------
    def _target_box(self) -> Optional[QSize]:
        """공통 박스가 있으면 그것(기준·후보 동일 크기), 없으면 라벨 크기."""
        box = self._box if (self._box is not None
                            and self._box.width() > 0
                            and self._box.height() > 0) else self._img.size()
        return box if (box.width() > 0 and box.height() > 0) else None

    def _redraw(self) -> None:
        if self._pix is None or self._pix.isNull():
            return
        target = self._target_box()
        if target is None:
            return
        if self._zoom == 1.0 and self._off_x == 0 and self._off_y == 0:
            # 기본(미확대) 경로는 예전 그대로 — 박스에 맞춘 픽스맵을 그냥 얹는다.
            self._img.setPixmap(self._pix.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
            return
        # 확대·이동 중에는 라벨 크기의 캔버스에 그려 넣는다(잘림 = 파고들어 보기).
        base = fit_scale(self._pix.width(), self._pix.height(),
                         target.width(), target.height())
        # ★ 드래그 팬은 오프셋만 바뀌는데 매 마우스 이벤트마다 **원본 해상도**를
        #   다시 줄이고 있었다(여기가 풀스크린 뷰어보다 무겁다 — 원본을 들고 있다).
        #   결과 배율이 같으면 만들어 둔 것을 그대로 쓴다.
        key = (self._pix.cacheKey(), round(base * self._zoom, 6),
               target.width(), target.height())
        if getattr(self, "_scaled_key", None) == key:
            scaled = self._scaled_cache
        else:
            scaled = self._pix.scaled(
                max(1, int(self._pix.width() * base * self._zoom)),
                max(1, int(self._pix.height() * base * self._zoom)),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._scaled_key = key
            self._scaled_cache = scaled
        cw = max(1, self._img.width())
        ch = max(1, self._img.height())
        canvas = QPixmap(cw, ch)
        canvas.fill(QColor(theme.VIEWER_BG))   # 뷰어 바탕(테마 무관 순검정)
        p = QPainter(canvas)
        p.drawPixmap((cw - scaled.width()) // 2 + self._off_x,
                     (ch - scaled.height()) // 2 + self._off_y, scaled)
        p.end()
        self._img.setPixmap(canvas)


class SideBySideViewer(QDialog):
    """기준(좌) + 후보(우, 이전/다음 순환) 비교 팝업.

    ``candidates`` 는 ``(ImageItem, caption)`` 리스트(점수 등 캡션 포함).
    ``action_label`` 이 주어지면 하단에 액션 버튼을 두고, 클릭 시 현재 후보
    ``ImageItem`` 을 ``action_requested`` 로 emit 하고 닫는다.
    """

    action_requested = pyqtSignal(object)        # 현재 후보 ImageItem

    def __init__(self,
                 ref_path: Path,
                 candidates: List[Tuple[ImageItem, str]],
                 start_index: int = 0,
                 *,
                 ref_caption: str = "기준 사진",
                 action_label: Optional[str] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setModal(True)
        # ★ 여기서 `setStyleSheet("background-color: …")` 를 부르면 **자식 전체**가
        #   그 배경을 물려받아, 채운 버튼(role=primary)의 면이 시트 색으로 덮이고
        #   글자만 on_accent 로 남아 글씨가 배경에 묻힌다(상단 액션 버튼 실측).
        #   면은 QSS 의 `QDialog[role="sheet"]` 가 칠한다 — 인라인 금지.
        self.setProperty("role", "sheet")
        self._ref_path = Path(ref_path)
        self._candidates = list(candidates)
        self._idx = max(0, min(int(start_index), len(self._candidates) - 1)) \
            if self._candidates else 0
        self._ref_caption = ref_caption

        # ★ `setMaximumSize(주 모니터 크기)` 를 걸지 않는다 — 그것이 '크게 보기인데
        #   팝업이 작게 뜬다' 의 원인이었다(매치 검토 실측).  이 다이얼로그는 시트
        #   호스트가 **창 크기 - 8px** 로 배치하는데, 최대크기가 걸려 있으면 그
        #   배치가 잘려 창보다 작은 팝업이 된다.  주 모니터가 앱이 떠 있는 모니터보다
        #   작거나(다중 모니터) 창이 그보다 크면 그대로 재현된다.  화면 초과 성장을
        #   막는 일은 이미 `sheet_host._place` 가 한다 — 그것이 유일한 배치 주체다.
        scr = QApplication.primaryScreen()
        if scr is not None:
            # 호스트를 못 찾아 별도 창으로 폴백할 때만 쓰이는 초기 크기.
            g = scr.availableGeometry()
            self.resize(int(g.width() * 0.9), int(g.height() * 0.88))
        else:
            self.resize(1400, 850)
        # ★ 창 제어(최소화/최대화/F11) 헬퍼를 부르지 않는다 — 이 다이얼로그는
        #   별도 OS 창이 아니라 **메인 창 안의 시트**로 뜬다(widgets/sheet_host.py).
        #   최대화·전체화면은 메인 창이 담당한다.
        self._build(action_label)
        QShortcut(QKeySequence("Esc"), self, activated=self.close)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=self._prev)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=self._next)

        self._ref_pane.set_pixmap(_decode_fast(self._ref_path))
        self._upgrade_to_original(self._ref_pane, self._ref_path)
        self._render_candidate()

    # ------------------------------------------------------------------
    def _build(self, action_label: Optional[str]) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # 상단 바: 위치 · (방향키 안내) · 이전 · 다음 · (액션) · 닫기 (#4).
        # 이전 버튼을 다음 버튼 바로 옆으로 모으고, 방향키 조작 가능을 표기한다.
        bar = QHBoxLayout()
        self.pos_label = QLabel("", self)
        self.pos_label.setStyleSheet(f"color: {theme.MUTE}; font-weight: 700;")
        bar.addWidget(self.pos_label)
        bar.addStretch(1)
        key_hint = QLabel(i18n.KO.COMPARE_HINT, self)
        key_hint.setStyleSheet(f"color: {theme.MUTE}; font-size: 12px;")
        bar.addWidget(key_hint)
        self.btn_prev = NeonButton("◀ 이전", role="ghost")
        self.btn_prev.clicked.connect(self._prev)
        bar.addWidget(self.btn_prev)
        self.btn_next = NeonButton("다음 ▶", role="ghost")
        self.btn_next.clicked.connect(self._next)
        bar.addWidget(self.btn_next)
        if action_label:
            self.btn_action = NeonButton(action_label, role="primary")
            self.btn_action.clicked.connect(self._fire_action)
            bar.addWidget(self.btn_action)
        self.btn_close = NeonButton("닫기", role="ghost")
        self.btn_close.clicked.connect(self.close)
        bar.addWidget(self.btn_close)
        root.addLayout(bar)

        body = QHBoxLayout()
        body.setSpacing(10)
        self._ref_pane = _Pane(self._ref_caption, self)
        self._cand_pane = _Pane("후보", self)
        body.addWidget(self._ref_pane, stretch=1)
        body.addWidget(self._cand_pane, stretch=1)
        root.addLayout(body, stretch=1)

    # ------------------------------------------------------------------
    def _sync_panes(self) -> None:
        """기준·후보가 동일한 크기로 보이도록 두 패널의 공통 목표 박스를 맞춘다 (#3).

        두 이미지 라벨 크기의 원소별 최소값을 공통 박스로 삼아 양쪽에 주입한다.
        같은 종횡비(같은 웨이퍼 크롭)면 표시 크기가 정확히 일치하고, 종횡비가
        달라도 두 이미지가 같은 박스 안에 동일 기준으로 맞춰진다."""
        rs = self._ref_pane.img_size()
        cs = self._cand_pane.img_size()
        box = QSize(min(rs.width(), cs.width()), min(rs.height(), cs.height()))
        if box.width() <= 0 or box.height() <= 0:
            return
        self._ref_pane.set_target_box(box)
        self._cand_pane.set_target_box(box)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._sync_panes()

    def showEvent(self, e):  # noqa: N802
        """첫 표시 시점에 공통 박스를 **다시** 계산한다.

        ``__init__`` 의 ``_render_candidate`` 는 아직 레이아웃이 돌지 않은 상태라
        이미지 라벨이 기본 최소크기(≈100×30)로 잡혀 있다.  그때 계산한 박스를
        그대로 두면 ``_redraw`` 가 박스를 우선하므로, 창이 아무리 커도 사진이
        40×30 같은 크기로 시작한다(실측).  표시 직후 한 틱 뒤 — 레이아웃이 실제
        크기를 배분한 다음 — 다시 맞춘다.  타이머는 **이 위젯이 소유**해 창이
        먼저 닫히면 함께 파괴된다(WA_DeleteOnClose 상태에서 죽은 객체 호출 방지)."""
        super().showEvent(e)
        self._sync_panes()
        QTimer.singleShot(0, self._sync_panes_if_alive)

    def _sync_panes_if_alive(self) -> None:
        try:
            self._sync_panes()
        except RuntimeError:          # 이미 파괴된 C++ 객체 — 무시
            pass

    # ------------------------------------------------------------------
    def _current_item(self) -> Optional[ImageItem]:
        if not self._candidates:
            return None
        return self._candidates[self._idx][0]

    def _render_candidate(self) -> None:
        if not self._candidates:
            self.pos_label.setText("후보 없음")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            return
        item, caption = self._candidates[self._idx]
        self._cand_pane.set_title(caption or item.filename)
        # ★ 후보를 바꿔도 확대 배율·위치는 **유지한다**(초기화하지 않는다).  같은
        #   자리를 같은 배율로 후보끼리 비교하는 것이 이 화면의 목적이다.
        self._cand_pane.set_pixmap(_decode_fast(Path(item.path)))
        self._upgrade_to_original(self._cand_pane, Path(item.path))
        self.pos_label.setText(f"{self._idx + 1} / {len(self._candidates)}")
        self.btn_prev.setEnabled(self._idx > 0)
        self.btn_next.setEnabled(self._idx < len(self._candidates) - 1)
        self._sync_panes()

    def _upgrade_to_original(self, pane, path: Path) -> None:
        """mid 로 먼저 보여 준 뒤, 원본이 준비되면 조용히 바꿔 끼운다.

        ★ 배율은 건드리지 않는다.  `_Pane` 은 그릴 때마다 `fit_scale` 로 base 를 다시
        계산하므로(`_zoom` 은 그 base 대비 배수) 원본으로 바뀌어도 화면 배율이 같다 —
        풀스크린 뷰어의 `_scale *= 옛폭/새폭` 보정을 여기 그대로 옮기면 오히려 틀린다."""
        try:
            from .zoom_window import _spawn_original_loader
        except Exception:
            return
        if os.environ.get("QT_QPA_PLATFORM", "") == "offscreen":
            return                        # 헤드리스에선 원본 교체가 의미 없다
        token = getattr(self, "_upgrade_token", 0) + 1
        self._upgrade_token = token

        def _apply(img) -> None:
            if getattr(self, "_upgrade_token", 0) != token:
                return                    # 사용자가 이미 다음 후보로 넘어갔다
            pix = QPixmap.fromImage(img)
            if not pix.isNull():
                pane.set_pixmap(pix)
                self._sync_panes()

        try:
            loader = _spawn_original_loader(path)
            loader.signals.loaded.connect(_apply)
        except Exception:
            pass

    def _prev(self) -> None:
        if self._idx > 0:
            self._idx -= 1
            self._render_candidate()

    def _next(self) -> None:
        if self._idx < len(self._candidates) - 1:
            self._idx += 1
            self._render_candidate()

    def _fire_action(self) -> None:
        item = self._current_item()
        if item is not None:
            self.action_requested.emit(item)
        self.accept()
