"""Slot 의 모든 사진을 중간(800px) 크기로 보여주는 줌-뷰 윈도우.

- 패널 종류(source) 별로 타이틀과 액션 버튼이 달라진다.
- 단일 클릭 → 선택 토글(액션 버튼 활성화) / **우클릭 '크게 보기'** → 풀스크린 뷰어.
  (더블클릭 확대는 제거했다 — 사진을 연달아 고를 때 오발했다.)
- 다중 선택 + 일괄 액션 지원.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from PyQt6.QtCore import QObject, QThread, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QColor, QImage, QPainter, QPixmap, QShortcut,
                         QKeySequence)
from PyQt6.QtWidgets import (QApplication, QDialog, QGridLayout, QHBoxLayout,
                              QLabel, QMenu, QScrollArea,
                              QVBoxLayout, QWidget)

from ... import i18n
from .. import theme
from ...models.slot import ImageItem
from ...utils import image_io
from .neon_button import NeonButton
from .option_group import columns_for_width
from . import sheet_host as sheets

# source 종류 ----------------------------------------------------------------
SOURCE_TARGET = "target"        # 검증 대상 (Right)
SOURCE_EXCLUDED = "excluded"    # 제외됨 (Bottom)
SOURCE_CANDIDATES = "candidate" # 후보 (Left)


# ---------------------------------------------------------------------------
class _MidTile(QWidget):
    """800px 중간 이미지 + 파일명 + 선택 상태."""

    clicked = pyqtSignal(object)         # ImageItem
    # ★ 확대는 **우클릭 '크게 보기'** 로만 연다.  더블클릭 확대를 없앤 이유: 사진을
    #   빠르게 연달아 고르다 보면 두 번째 클릭이 더블클릭으로 붙어 원하지 않는 풀스크린
    #   창이 떠 버린다(사용자 지적).  선택은 프레스마다 토글되는 잦은 동작이고 확대는
    #   드문 동작이라, 같은 버튼의 연타에 둘을 겹쳐 두면 잦은 쪽이 드문 쪽을 오발한다.
    view_requested = pyqtSignal(object)  # ImageItem (크게 보기)
    toggled = pyqtSignal(object, bool)   # (ImageItem, on)

    TILE_W = 360
    TILE_H = 360

    def __init__(self, item: ImageItem, *, dim: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self._dim = dim
        self._selected = False
        self.setFixedSize(self.TILE_W, self.TILE_H + 30)
        self.setStyleSheet(
            f"QWidget {{ background: {theme.PANEL}; border: 1px solid {theme.LINE}; "
            "border-radius: 8px; }"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        self._img_label = QLabel(self)
        self._img_label.setFixedSize(self.TILE_W - 12, self.TILE_H - 18)
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # ★ 여기서 바로 읽지 않는다.  생성자에서 mid 를 디코드하면 슬롯 사진 수만큼
        #   창이 굳은 뒤에야 열렸다(120장 1.0초 실측, 썸네일 사전생성을 건너뛴 세션이면
        #   원본 디코드가 겹쳐 훨씬 길다).  화면에 처음 그려질 때 채운다.
        self._image_loaded = False
        lay.addWidget(self._img_label)

        cap = QLabel(item.filename, self)
        cap.setProperty("role", "muted")
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setStyleSheet("border: none;")
        lay.addWidget(cap)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def paintEvent(self, event):  # noqa: N802
        # 화면에 처음 나타날 때 한 번만 읽는다(지연 로드).
        if not self._image_loaded:
            self._image_loaded = True
            # ★ **부모 있는** 타이머로 미룬다 — 정적 `QTimer.singleShot` 은 타일이
            #   그 한 틱 사이에 파괴돼도 계속 살아 있어 죽은 위젯의 슬롯을 부른다
            #   (창을 열자마자 닫으면 실제로 그렇게 된다).  부모가 있으면 함께 죽는다.
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._load_pix)
            t.start(0)
        super().paintEvent(event)

    def _load_pix(self) -> None:
        # ★ 공유 RAM 캐시를 쓴다(image_io.cached_tile_pixmap) — dim 처리까지 내장이라
        #   예전의 수동 QPainter 페이드가 필요 없고, 창을 다시 열 때 재디코드도 없다.
        #   같은 헬퍼를 썸네일 그리드(thumb_grid._ThumbTile)도 쓴다.
        try:
            pix = image_io.cached_tile_pixmap(
                self.item.path, self.TILE_W - 12,
                prefer_mid=True, dim=self._dim,
            )
        except Exception:
            pix = QPixmap(self.TILE_W, self.TILE_H)
            pix.fill(QColor(theme.PANEL))
        if pix.isNull():
            pix = QPixmap(self.TILE_W, self.TILE_H)
            pix.fill(QColor(theme.PANEL))
        self._img_label.setPixmap(pix)

    def set_selected(self, on: bool) -> None:
        self._selected = on
        if on:
            self.setStyleSheet(
                f"QWidget {{ background: {theme.PANEL}; border: 2px solid {theme.ACCENT}; "
                "border-radius: 8px; }"
            )
        else:
            self.setStyleSheet(
                f"QWidget {{ background: {theme.PANEL}; border: 1px solid {theme.LINE}; "
                "border-radius: 8px; }"
            )
        self.toggled.emit(self.item, on)

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.set_selected(not self._selected)
            self.clicked.emit(self.item)
        super().mousePressEvent(e)

    def _on_context_menu(self, pos) -> None:
        """우클릭 '크게 보기' — 이 타일의 유일한 확대 경로.

        ★ 더블클릭을 없애면서 이 메뉴를 **새로 붙였다.**  다른 타일들은 원래 더블클릭과
        우클릭 두 경로가 있었지만 이 타일만 더블클릭 하나였다 — 그대로 지우면 확대
        기능이 사라졌을 것이다.  제스처를 없애는 것과 기능을 없애는 것은 다르다."""
        menu = QMenu(self)
        act = menu.addAction(i18n.KO.CTX_VIEW_LARGER)
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen is act:
            self.view_requested.emit(self.item)


# ---------------------------------------------------------------------------
def fit_scale(pix_w: int, pix_h: int, view_w: int, view_h: int) -> float:
    """이미지를 뷰에 **꽉 채우는** 배율(비율 유지).  순수 함수 — 헤드리스 테스트용.

    ★ 1.0 을 상한으로 두지 않는다.  '크게 보기'가 요구받는 것은 원본 픽셀 크기가
    아니라 **화면을 채우는 것**이다.  mid 캐시(긴 변 ≤800px)를 창 전체(≈1400px)
    한가운데 1:1 로 그리던 것이 정확히 '크게 보기인데 작다'는 버그였다."""
    if pix_w <= 0 or pix_h <= 0 or view_w <= 0 or view_h <= 0:
        return 1.0
    return min(view_w / float(pix_w), view_h / float(pix_h))


# 살아 있는 로더를 여기에 붙잡아 둔다 — **뷰어의 자식으로 두지 않는다.**
#
# ★ 이유: 실행 중인 QThread 가 파괴되면 Qt 는 "QThread: Destroyed while thread is
#   still running" 으로 프로세스를 죽인다.  로더를 다이얼로그의 자식으로 두면
#   사용자가 디코드가 끝나기 전에 창을 닫는 순간 정확히 그 일이 벌어진다.
#   부모를 떼고 여기서 참조를 쥐고 있다가 `finished` 에서 놓아 주면, 창이 언제
#   닫히든 스레드는 자기 수명을 다 살고 조용히 사라진다(`motion.py` 가 애니메이션
#   자기삭제를 금지한 것과 같은 원칙: 가드를 덧대지 말고 원인을 없앤다).
_LIVE_LOADERS: set = set()


class _OriginalLoader(QThread):
    """원본 이미지를 **워커 스레드에서** 디코드한다(UI 가 멈추지 않게).

    ★ 여기서 ``QPixmap`` 을 만들면 안 된다 — GUI 스레드 전용이다.  ``QImage`` 로
    받아 시그널로 넘기고, 변환은 메인 스레드에서 한다.  ``signals`` 는 메인
    스레드에서 만들어지므로 emit 은 큐 연결이 된다."""

    class _Signals(QObject):
        loaded = pyqtSignal(QImage)

    def __init__(self, path: Path) -> None:
        super().__init__()                  # 부모 없음(위 주석)
        self._path = path
        self.signals = self._Signals()

    def run(self) -> None:      # type: ignore[override]
        try:
            img = QImage(str(self._path))
        except Exception:
            return
        if not img.isNull():
            self.signals.loaded.emit(img)


def _spawn_original_loader(path: Path) -> "_OriginalLoader":
    ld = _OriginalLoader(path)
    _LIVE_LOADERS.add(ld)
    ld.finished.connect(lambda: _LIVE_LOADERS.discard(ld))
    ld.start()
    return ld


class FullscreenViewer(QDialog):
    """우클릭 '크게 보기' 로 열리는 풀스크린 뷰어 (휠 줌 + 드래그 팬).

    두 가지를 지킨다:

    1. **처음부터 창에 꽉 찬다.**  배율은 첫 표시 때 ``fit_scale`` 로 한 번만
       정한다(그 뒤 리사이즈는 사용자의 휠 줌을 덮지 않는다).
    2. **원본 화질로 올라온다.**  즉시 mid 캐시로 그려 대기시간을 없애고,
       원본은 백그라운드에서 디코드해 도착하면 갈아 끼운다 — 이때 화면상
       크기가 튀지 않게 배율을 픽셀 비율로 재보정한다.
    """

    # 원본이 뷰포트보다 이 배 이상 크면 그만큼만 남기고 한 번 줄인다.
    # 휠 틱마다 5000px 스무스 스케일을 돌리지 않기 위한 상한(그래도 mid 의
    # 800px 보다 훨씬 선명하다).
    _WORK_OVERSAMPLE = 2

    def __init__(self, image_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(image_path.name)
        self.setModal(True)
        self.setStyleSheet(f"background-color: {theme.VIEWER_BG};")
        # 작은 모니터에서 1280×800 이 화면을 넘어가지 않도록 화면 가용 영역의
        # 90% 안으로 제한.  (실제 크기는 시트 호스트가 full_bleed 로 덮어쓴다.)
        scr = QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.resize(min(1280, int(g.width() * 0.9)),
                        min(800, int(g.height() * 0.9)))
        else:
            self.resize(1280, 800)

        self._path = image_path
        self._scale = 1.0
        self._fitted = False              # 맞춤은 첫 표시 때 한 번만
        self._offset_x = 0
        self._offset_y = 0
        self._last_drag = None
        self._loader: Optional[_OriginalLoader] = None

        # 풀 사이즈 이미지는 디코드가 느릴 수 있어 우선 mid 로 즉시 그린다.
        self._pix = QPixmap(str(image_io.get_mid_path(image_path)))
        if self._pix.isNull():
            self._pix = QPixmap(800, 600)
            self._pix.fill(QColor(theme.VIEWER_BG))

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet(f"background-color: {theme.VIEWER_BG};")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._label)

        QShortcut(QKeySequence("Esc"), self, activated=self.close)
        self._start_original_load()

    # -- 원본 화질로 교체 ------------------------------------------------
    def _start_original_load(self) -> None:
        """원본을 백그라운드에서 디코드.  헤드리스면 건너뛴다(테스트 결정성)."""
        import os
        if os.environ.get("QT_QPA_PLATFORM", "") == "offscreen":
            return
        self._loader = _spawn_original_loader(self._path)
        self._loader.signals.loaded.connect(self._on_original_loaded)

    def closeEvent(self, e):  # noqa: N802
        """닫힐 때 로더 연결을 끊는다 — 죽어 가는 위젯으로 tick 이 들어가지 않게.

        스레드 자체는 기다리지 않는다(`_LIVE_LOADERS` 가 수명을 책임진다) —
        디코드가 끝날 때까지 닫기를 붙잡아 두면 그게 곧 UI 멈춤이다."""
        ld = self._loader
        if ld is not None:
            try:
                ld.signals.loaded.disconnect(self._on_original_loaded)
            except (TypeError, RuntimeError):
                pass
            self._loader = None
        super().closeEvent(e)

    def _on_original_loaded(self, img: "QImage") -> None:
        """워커가 준 QImage → (메인 스레드에서) QPixmap 으로 교체.

        ★ 배율을 픽셀 비율로 나눠 준다 — 안 그러면 화질이 좋아지는 순간 사진이
        갑자기 몇 배로 커진다.  사용자가 이미 줌/팬 했더라도 화면은 그대로다."""
        if img.isNull():
            return
        # 지나치게 큰 원본은 뷰포트의 몇 배 선까지만 — 휠 줌 응답을 지키기 위해.
        cap = self._WORK_OVERSAMPLE * max(1, self.width(), self.height())
        if max(img.width(), img.height()) > cap:
            img = img.scaled(cap, cap, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        pix = QPixmap.fromImage(img)
        if pix.isNull() or self._pix.width() <= 0:
            return
        self._scale *= self._pix.width() / float(pix.width())
        self._pix = pix
        self._redraw()

    # -- 배율 ------------------------------------------------------------
    def _fit_to_view(self) -> None:
        self._scale = fit_scale(self._pix.width(), self._pix.height(),
                                self.width(), self.height())
        self._offset_x = 0
        self._offset_y = 0

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        self._redraw()

    def resizeEvent(self, e):  # noqa: N802
        self._redraw()
        super().resizeEvent(e)

    def wheelEvent(self, e):  # noqa: N802
        step = 1.1 if e.angleDelta().y() > 0 else (1.0 / 1.1)
        self._scale = max(0.02, min(8.0, self._scale * step))
        self._redraw()

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._last_drag = e.position().toPoint()

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._last_drag is not None:
            cur = e.position().toPoint()
            self._offset_x += cur.x() - self._last_drag.x()
            self._offset_y += cur.y() - self._last_drag.y()
            self._last_drag = cur
            self._redraw()

    def mouseReleaseEvent(self, e):  # noqa: N802
        self._last_drag = None

    def _redraw(self) -> None:
        # ★ 맞춤은 **그리기 직전에, 딱 한 번** 한다.  showEvent/resizeEvent 어느
        #   쪽이 먼저 오는지는 이 뷰어가 어떻게 띄워지느냐에 달려 있고(시트 호스트는
        #   setGeometry 로 자식에 동기 리사이즈를, 독립 창은 표시 후 리사이즈를 준다),
        #   둘 중 하나에만 걸어 두면 나머지 경로에서 배율 1.0 인 채로 첫 프레임이
        #   그려진다 — 바로 그게 '크게 보기인데 작다' 였다.
        if not self._fitted and self.width() > 0 and self.height() > 0:
            self._fitted = True
            self._fit_to_view()
        # ★ 스케일 결과를 한 장 캐시한다.  드래그 팬은 offset 만 바뀌는데도 매
        #   마우스 이벤트마다 수천 px 를 SmoothTransformation 으로 다시 줄이고 있었다
        #   — 큰 모니터에서 팬이 마우스를 따라오지 못한 이유다.  무효화 지점은
        #   `_pix` 와 `_scale` 뿐이므로(맞춤·휠 줌·원본 교체) 그 둘을 키로 쓴다.
        key = (self._pix.cacheKey(), round(float(self._scale), 6))
        if getattr(self, "_scaled_key", None) == key:
            scaled = self._scaled_cache
        else:
            w = int(self._pix.width() * self._scale)
            h = int(self._pix.height() * self._scale)
            scaled = self._pix.scaled(
                w, h, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._scaled_key = key
            self._scaled_cache = scaled
        # 단순 중앙 + offset
        cw = self.width()
        ch = self.height()
        canvas = QPixmap(cw, ch)
        canvas.fill(QColor(theme.VIEWER_BG))
        p = QPainter(canvas)
        x = (cw - scaled.width()) // 2 + self._offset_x
        y = (ch - scaled.height()) // 2 + self._offset_y
        p.drawPixmap(x, y, scaled)
        p.end()
        self._label.setPixmap(canvas)


# ---------------------------------------------------------------------------
class ZoomWindow(QDialog):
    """Slot 내 사진 전체를 mid 크기로 보여주는 다이얼로그."""

    # action_requested: (action_id, list[ImageItem])
    action_requested = pyqtSignal(str, list)

    def __init__(self,
                 slot_name: str,
                 items: Iterable[ImageItem],
                 source: str,
                 *,
                 already_matched_items: Iterable[ImageItem] = (),
                 view_only: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        # 닫는 즉시 C++ 위젯 해제 — 매번 열 때마다 부모에 누적되지 않도록.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._view_only = bool(view_only)
        self._slot = slot_name
        self._items = list(items)
        self._already_matched = list(already_matched_items)
        self._source = source
        self._tiles: list[_MidTile] = []
        self._selected: list[ImageItem] = []

        title_fmt = {
            SOURCE_TARGET: i18n.KO.ZOOM_TITLE_TARGETS,
            SOURCE_EXCLUDED: i18n.KO.ZOOM_TITLE_EXCLUDED,
            SOURCE_CANDIDATES: i18n.KO.ZOOM_TITLE_CANDIDATES,
        }[source]
        self.setWindowTitle(title_fmt.format(slot=slot_name))
        self._resize_within_screen(1280, 800)

        # ★ 창 제어(최소화/최대화/F11) 헬퍼를 부르지 않는다 — 이 다이얼로그는
        #   별도 OS 창이 아니라 **메인 창 안의 시트**로 뜬다(widgets/sheet_host.py).
        #   최대화·전체화면은 메인 창이 담당한다.

        self._build()

    @staticmethod
    def _screen_available(parent) -> tuple[int, int]:
        """다이얼로그가 띄워질 모니터의 ‘작업 영역(taskbar 제외)’ 크기."""
        scr = None
        if parent is not None and hasattr(parent, "screen"):
            scr = parent.screen()
        if scr is None:
            scr = QApplication.primaryScreen()
        if scr is None:
            return (1280, 800)
        g = scr.availableGeometry()
        return (g.width(), g.height())

    def _resize_within_screen(self, w: int, h: int) -> None:
        sw, sh = self._screen_available(self.parent())
        # 모니터의 90% 까지만 차지 → 다이얼로그 옆 / 작업표시줄이 가려지지 않도록.
        self.resize(min(w, int(sw * 0.9)), min(h, int(sh * 0.9)))

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # 액션 바
        bar = QHBoxLayout()
        bar.setSpacing(8)

        if self._view_only:
            # 액션 없음 — 단순 확대 뷰어.
            self._btn_a = self._btn_b = None  # type: ignore[assignment]
        elif self._source == SOURCE_TARGET:
            self._btn_a = NeonButton(i18n.KO.ZOOM_BTN_EXCLUDE, role="danger")
            self._btn_b = NeonButton(i18n.KO.ZOOM_BTN_TO_CENTER, role="warn")
            self._btn_a.clicked.connect(lambda: self._emit("exclude"))
            self._btn_b.clicked.connect(lambda: self._emit("recenter"))
        elif self._source == SOURCE_EXCLUDED:
            self._btn_a = NeonButton(i18n.KO.ZOOM_BTN_TO_TARGET, role="primary")
            self._btn_b = NeonButton(i18n.KO.ZOOM_BTN_TO_CENTER, role="warn")
            self._btn_a.clicked.connect(lambda: self._emit("verify"))
            self._btn_b.clicked.connect(lambda: self._emit("recenter"))
        elif self._source == SOURCE_CANDIDATES:
            # Stage 2 의 +N 후보 — 단일 선택으로 매칭 확정 가능 (#2)
            self._btn_a = NeonButton(i18n.KO.ZOOM_BTN_PICK_MATCH, role="primary")
            self._btn_a.clicked.connect(lambda: self._emit("pick"))
            # 좁은 노트북 화면에서도 라벨이 잘리지 않도록 최소 폭 확보.
            self._btn_a.setMinimumWidth(220)
            self._btn_b = None  # type: ignore[assignment]
        else:
            self._btn_a = self._btn_b = None  # type: ignore[assignment]

        if self._btn_a is not None:
            self._btn_a.setEnabled(False)
            # 라벨이 잘리지 않도록 sizeHint 를 최소값으로 보장.
            self._btn_a.setMinimumWidth(
                max(self._btn_a.sizeHint().width(),
                    self._btn_a.minimumWidth())
            )
            bar.addWidget(self._btn_a)
        if self._btn_b is not None:
            self._btn_b.setEnabled(False)
            self._btn_b.setMinimumWidth(
                max(self._btn_b.sizeHint().width(),
                    self._btn_b.minimumWidth())
            )
            bar.addWidget(self._btn_b)
        bar.addStretch(1)
        close = NeonButton(i18n.KO.BTN_OK, role="ghost")
        close.clicked.connect(self.accept)
        bar.addWidget(close)
        root.addLayout(bar)

        # 그리드 (스크롤) — ★ 지역 변수로 두지 않는다.  창 폭에 맞춰 열 수를 다시
        #   계산하려면 리사이즈 때 이 셋에 닿아야 한다.
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        self._scroll.setWidget(host)
        self._grid = QGridLayout(host)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setSpacing(10)

        # 배치 대상을 순서대로 모은다 — 구분선(sep)은 한 줄을 통째로 쓰므로 따로 표시.
        self._flow: list[tuple[QWidget, bool]] = []      # (위젯, 한 줄 전체 차지)
        for item in self._items:
            tile = _MidTile(item, dim=False, parent=host)
            tile.toggled.connect(self._on_toggle)
            tile.view_requested.connect(self._open_fullscreen)
            self._tiles.append(tile)
            self._flow.append((tile, False))

        # 이미 매칭된 항목들 (Phase B 시나리오) — 회색 처리 후 클릭 가능
        if self._already_matched:
            sep = QLabel(i18n.KO.INFO_ALREADY_MATCHED_SECTION, host)
            sep.setProperty("role", "muted")
            self._flow.append((sep, True))
            for item in self._already_matched:
                self._flow.append((_MidTile(item, dim=True, parent=host), False))

        self._active_cols = 0
        self._relayout_tiles()
        root.addWidget(self._scroll, stretch=1)

    # ------------------------------------------------------------------
    def _effective_cols(self) -> int:
        """★ 열 수를 3 으로 고정하면 800px 창(최소 지원 폭)에서 세 번째 열이 잘리고
        가로 스크롤이 생겼다.  뷰포트 폭에 맞춰 줄이되 3 을 넘지는 않는다."""
        vp = self._scroll.viewport().width() if self._scroll is not None else 0
        if vp <= 0:
            vp = max(self.width() - 40, 1)
        return max(1, min(3, columns_for_width(vp, _MidTile.TILE_W,
                                               self._grid.spacing())))

    def _relayout_tiles(self) -> None:
        cols = self._effective_cols()
        if cols == self._active_cols:
            return                       # ★ 열 수가 바뀔 때만 재배치(드래그마다 재조립 금지)
        self._active_cols = cols
        while self._grid.count():
            self._grid.takeAt(0)
        row = col = 0
        for widget, full_row in self._flow:
            if full_row:
                if col:
                    row += 1
                    col = 0
                self._grid.addWidget(widget, row, 0, 1, cols)
                row += 1
                continue
            self._grid.addWidget(widget, row, col)
            col += 1
            if col >= cols:
                col = 0
                row += 1

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._relayout_tiles()

    # ------------------------------------------------------------------
    def _on_toggle(self, item: ImageItem, selected: bool) -> None:
        if selected:
            if item not in self._selected:
                self._selected.append(item)
        else:
            if item in self._selected:
                self._selected.remove(item)
        any_sel = bool(self._selected)
        if self._btn_a is not None:
            self._btn_a.setEnabled(any_sel)
        if self._btn_b is not None:
            self._btn_b.setEnabled(any_sel)

    def _emit(self, action: str) -> None:
        if not self._selected:
            return
        self.action_requested.emit(action, list(self._selected))
        # 처리 후 즉시 닫는다 — 호출자가 데이터 갱신 후 다시 열도록
        self.accept()

    def _open_fullscreen(self, item: ImageItem) -> None:
        viewer = FullscreenViewer(item.path, self)
        sheets.run(viewer, full_bleed=True)
