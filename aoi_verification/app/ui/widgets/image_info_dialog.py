"""단일 사진 정보 — 사진 한 장의 결함 좌표·계측값을 제도 시트로 읽는 도구.

전체 검증 흐름(스캔 → 매칭 → 엑셀)을 돌리지 않고도 사진 한 장의 수치를 바로 확인한다.
표기는 :mod:`coords.single_info` 가 만든다 — **결과 엑셀 미매칭 행과 같은 생산자**라
두 곳의 수치가 어긋나지 않는다.

형태는 앱의 '도면' 언어를 그대로 쓴다(새 시각 언어를 만들지 않는다):

- 계측은 **타이틀블록 + 하이라인 눈금 표**다 — ``role="listHeader"``/``colHead`` 로
  덮개를 씌우고, ``QFrame[role="row"]`` 의 아래 눈금이 항목을 가른다.  눈금은
  **묶음(``InfoRow.cluster``)이 바뀌는 곳에만** 긋는다 — 행마다 그으면 선이 너무
  많아 읽기 어렵다(사용자 지시 2026-11-23).  묶음 안 행은 ``rule="none"``.
- 값은 **라벨 열(고정 폭) 바로 오른쪽에서 좌측 정렬로 시작**한다.  오른쪽 끝에 붙이면
  창이 넓어질수록 라벨과 값 사이가 벌어져 항목마다 눈이 좌우로 왕복한다 — 시작선을
  하나로 모으면 수치를 위아래로 훑을 수 있다.
- **수치는 모노**(``role="mono"``)로 찍는다.  이게 이 화면의 초점이다 — 비례 서체로
  찍으면 계측기가 아니라 산문으로 읽힌다.
- 측정정보 유무는 문장이 아니라 **판정 스탬프**(``chip="ok"``/``"none"``)로 한 번에.
- 미리보기와 표는 ``role="vrule"`` 1px 눈금으로 가른다(검토 화면의 점수 컬럼과 같은 것).

정보의 출처는 사진이 든 폴더의 정보파일(ColorImageGrabingInfo.ini / KLA 정보파일 /
Surface.flt)이다.  사진만 따로 복사한 폴더에서는 LIVE 형식 파일명일 때만 좌표가 나온다
— 그래서 빈 상태가 그 사실을 가르친다(로드 뒤에는 자리를 차지하지 않는다).

조회는 폴더 단위 ``lru_cache`` 를 타는 파서 몇 번이라 즉시 끝난다.  장시간 작업이
아니므로 백그라운드 스레드·``LoadingOverlay`` 를 쓰지 않는다.

**Scan image** 만은 예외다 — 같은 폴더의 Scan 원본(수 MB, NAS)을 읽어 잘라야 하므로
워커 스레드(`_ScanLoader`)에서 돈다.  계측 표는 Scan 을 기다리지 않고 먼저 뜬다.  Scan
목록 파일이 있으면 Color 아래에 자리를 먼저 잡고 '불러오는 중'(점 애니메이션)을 띄운 뒤
그림으로 바꾼다.  Scan 이 없으면 칸 자체를 만들지 않는다
(사용자 결정).  사진을 바꿀 때마다 세대 번호를 올려, 늦게 도착한 이전 사진의 결과가
새 사진 아래에 붙지 않게 한다.  계산은 엑셀과 같은 :mod:`coords.scan_image` 다.

**흑백 Scan 사진(``X.Y.t.N.jpeg``)만 넣으면** 거꾸로 간다(사용자 요청 2026-11-24) — 그 사진 안에 찍힌
Defect 를 :func:`coords.scan_image.defects_in` 으로 찾아, 미리보기에는 고른 Defect 의 300 µm
Crop(늘 Crop — 전체 Scan 은 크게 볼 때 노랑·파랑 네모로), 아래 칸에는 고른 Defect 의 **Color 사진**을, 표에는 그 Defect 의 Color 사진과 **같은 정보**
(:func:`coords.single_info.describe_in_scan`)를 보인다.  여럿이면 ◀ ▶ 로 넘긴다.  이때는 Color→Scan
로더를 돌리지 않는다(Scan 파일명의 두 수치는 절대좌표가 아니다).
정보 표(빠름)는 덮개 아래에서 먼저 끝내 보이고, Scan 원본 디코드와 Color 사진 준비(느림, NAS)는
`_ScanFullLoader` 가 따로 돌며 그 동안 미리보기·아래 칸이 '불러오는 중' 점을 보인다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple, Optional

from PyQt6.QtCore import (QObject, QPoint, QRect, QRectF, QSize, QThread, Qt, QTimer,
                          pyqtSignal)
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QImage, QKeySequence, QPainter,
                         QPen, QPixmap, QShortcut)
from PyQt6.QtWidgets import (QApplication, QDialog, QFrame,
                             QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea,
                             QSizePolicy, QVBoxLayout, QWidget)

from ... import i18n
from ... import config
from ...config import CONFIG
from ...coords import scan_image, single_info
from ...gl import loader as gl_loader
from ...models import lot_info
from ...utils import image_io as _io
from .. import motion, theme
from ..drop_overlay import drop_clear, drop_feedback
from ..drop_paths import guess_kinds, install_paste, local_paths
from ..folder_picker import FilePicker
from . import photo_menu
from . import sheet_host as sheets
from .loading_overlay import LoadingOverlay
from .neon_button import NeonButton
from .tool_open_save import EmptyOpen

_BOX_PAD_H = 10          # 정보 상자 안 좌우 여백
# 여러 장 넘겨 보기(현택님 2026-11-24 안 A) — 아래 사진 띠.  사진이 수천 장이어도 위젯은 지금 사진
# 앞뒤 _STRIP_HALF 장씩만 만든다.
_STRIP_PX = 40
# Scan 사진 한 장에 Defect 가 여럿이면 썸네일 뒤로 카드 두 장이 이만큼씩 비져나온다.
_STACK_OFF = 3
_STRIP_HALF = 6
# 미리보기 — 고정 정사각형이 아니라 폭 하한만 두고 세로는 표와 함께 늘어난다.
_PREVIEW_MIN_W = 300
_PREVIEW_MAX_W = 460
# 복사 확인은 모달이 아니라 버튼 옆에서 잠깐 스쳐 지나간다.
_TOAST_MS = 1800
# 라벨 열 폭의 **하한** — 값이 전부 여기서부터 시작한다.  실제 폭은 시트에 실린 가장 긴
# 라벨에 맞춰 넓힌다(`_label_col_w`).
_LABEL_COL_MIN = 132
# Scan 칸의 최소 한 변 — 창이 낮아도 이보다 작게 줄이지 않는다.
_SCAN_MIN_SIDE = 120
# Scan '불러오는 중' 점 애니메이션 한 칸 간격 — 가볍게(초당 몇 번만 글자를 바꾼다).
_SCAN_DOTS_MS = 350


def pil_to_qimage(im) -> QImage:
    """PIL 이미지 → 독립 QImage(버퍼를 복사한다).  워커 스레드에서 불러도 된다."""
    if im.mode != "L":
        im = im.convert("RGB")
    fmt = (QImage.Format.Format_Grayscale8 if im.mode == "L"
           else QImage.Format.Format_RGB888)
    data = im.tobytes()
    return QImage(data, im.width, im.height, len(im.mode) * im.width, fmt).copy()


# 살아 있는 로더를 붙잡아 둔다 — 다이얼로그의 자식으로 두면 창을 닫는 순간 실행 중인
# QThread 가 파괴돼 프로세스가 죽는다(`zoom_window._LIVE_LOADERS` 와 같은 이유).
_LIVE_SCAN_LOADS: set = set()


class _ScanLoader(QThread):
    """Scan 판정 → (있으면) 원본 디코드와 Crop.  QPixmap 은 만들지 않는다(GUI 전용)."""

    class _Signals(QObject):
        found = pyqtSignal(int)                          # 세대 — Scan 있음 판정
        gl = pyqtSignal(int, bool)                       # 세대, GL 로 볼 수 있는 사진인가
        others = pyqtSignal(int, object)                 # 세대, 같은 Scan 안 Defect 들의 Crop 범위 목록(전체화면 표시용)
        done = pyqtSignal(int, str, object, object, object, str)
        # (세대, 상태, Crop QImage, 전체 QImage, Crop 범위, Scan 파일명)

    def __init__(self, path: Path, gen: int) -> None:
        super().__init__()                  # 부모 없음(위 주석)
        self._path, self._gen = path, gen
        self.signals = self._Signals()

    def run(self) -> None:      # type: ignore[override]
        try:
            self._run()
        except Exception:                   # 예외로 끝나면 로딩 덮개가 영영 안 걷힌다
            self.signals.done.emit(self._gen, scan_image.UNREADABLE,
                                   None, None, None, "")

    def _run(self) -> None:
        # 목록 파일이 있으면 Scan 이 있을 가능성이 높다 — 판정(Scan 원본 머리 읽기)을
        # 기다리지 않고 칸부터 잡아 '불러오는 중' 을 띄운다.  판정이 '없음' 이면
        # `_on_scan_done` 이 칸을 다시 접는다.
        announced = (self._path.parent / scan_image.SCAN_LIST_NAME).exists()
        if announced:
            self.signals.found.emit(self._gen)
        # GL 은 회색 사진이거나, 컬러면 Scan 을 찾았을 때만 의미가 있다 — 컬러를 흑백으로
        # 바꿔 보는 것은 쓸모가 없다(사용자 지시 2026-11-23).  못 읽는 사진도 끈다.
        try:
            color = gl_loader.is_color(self._path)
        except Exception:
            color = None
        if color is not True:
            self.signals.gl.emit(self._gen, color is False)
        m = scan_image.resolve(self._path)
        if not m.ok:
            self.signals.done.emit(self._gen, m.status, None, None, None, "")
            return
        if not announced:
            self.signals.found.emit(self._gen)
        res = scan_image.load_crop(m)
        if res is None:
            self.signals.done.emit(self._gen, scan_image.UNREADABLE,
                                   None, None, None, "")
            return
        crop, cand = res
        if color:
            self.signals.gl.emit(self._gen, True)       # 뷰어가 이 Scan 으로 갈아탄다
        full = scan_image.load_full(cand)
        try:                                            # 전체화면에서 같은 Scan 의 Defect 를 모두 보이게 — 실패하면 하나만
            d = scan_image.defects_in(cand.path)
            self.signals.others.emit(self._gen, [tuple(h.box) for h in d.hits] if d.ok else [])
        except Exception:
            self.signals.others.emit(self._gen, [])
        self.signals.done.emit(
            self._gen, scan_image.OK, pil_to_qimage(crop),
            pil_to_qimage(full) if full is not None else None,
            tuple(cand.box), str(cand.path))


class _ScanFullLoader(QThread):
    """흑백 Scan 사진 → 원본 디코드 + 각 Defect 의 Color 사진 캐시 준비.  둘 다 NAS 를 읽어 느리다.

    정보 표는 이미 떠 있다 — 이 로더는 미리보기·아래 Color 칸만 채운다.  QPixmap 은 만들지 않는다
    (GUI 전용) — Color 는 캐시 파일만 만들어 두고 메인 스레드가 읽는다."""

    class _Signals(QObject):
        full = pyqtSignal(int, object, object)           # (세대, 전체 Scan QImage | None, Defect 별 300 µm Crop QImage 목록 | None)
        color = pyqtSignal(int, int, bool)               # (세대, Defect 번호, Color 사진을 쓸 수 있나)

    def __init__(self, path: Path, gen: int, color_paths: list, boxes=()) -> None:
        super().__init__()                  # 부모 없음(위 주석)
        self._path, self._gen = path, gen
        self._colors = list(color_paths)
        self._boxes = list(boxes)           # Defect 마다 Crop 범위 — 미리보기는 늘 이 Crop 이다
        self.signals = self._Signals()

    def _one_color(self, i: int) -> None:
        p = self._colors[i]
        ok = False
        try:
            if p.is_file():
                _io.get_mid_path(p)
                ok = True
        except Exception:
            ok = False
        self.signals.color.emit(self._gen, i, ok)

    def run(self) -> None:      # type: ignore[override]
        try:
            if self._colors:
                self._one_color(0)          # 처음 보이는 Defect 의 Color 를 먼저
            im = scan_image.load_image(self._path)
            crops = ([pil_to_qimage(scan_image.cut(im, b)) for b in self._boxes]
                     if im is not None else None)        # 자르기도 워커에서 — UI 스레드는 그리기만
            self.signals.full.emit(self._gen, pil_to_qimage(im) if im is not None else None, crops)
            for i in range(1, len(self._colors)):
                if self.isInterruptionRequested():
                    return
                self._one_color(i)
        except Exception:                   # 예외로 끝나면 '불러오는 중' 이 영영 안 걷힌다
            self.signals.full.emit(self._gen, None, None)


class _InfoLoader(QThread):
    """사진 정보 읽기(`single_info.describe`) + 미리보기 캐시 준비 — 둘 다 NAS 를 읽는다.

    QPixmap 은 만들지 않는다(GUI 전용) — 미리보기는 캐시 파일만 만들어 두고, 메인 스레드가
    그 캐시를 읽어 그린다."""

    class _Signals(QObject):
        done = pyqtSignal(int, object)                   # (세대, 정보 덩이 목록)

    def __init__(self, path: Path, gen: int, fn=None) -> None:
        super().__init__()                  # 부모 없음(위 주석)
        self._path, self._gen = path, gen
        self._fn = fn or _describe
        self.signals = self._Signals()

    def run(self) -> None:      # type: ignore[override]
        try:
            groups = self._fn(self._path)
        except Exception:                   # 빈 표로라도 이어가야 덮개가 걷힌다
            groups = _ScanSheet(self._path) if self._fn is _describe_scan else []
        self.signals.done.emit(self._gen, groups)


class _PhotoGroup(NamedTuple):
    """폴더 한 곳의 사진들 — 위 '폴더 고르기' 의 한 줄."""
    folder: Path
    photos: list
    lot: Optional[Path] = None      # 끌어다 놓은 LOT 폴더(그 아래 웨이퍼 폴더에서 나온 묶음일 때만)
    slot: Optional[str] = None      # 장비 슬롯 번호(WaferInfo.ini ActiveSlot) — 여러 폴더일 때만 읽는다


def _dir_photos(d) -> list[str]:
    """폴더 **바로 안** 사진 경로들(이름순, 대소문자 무시).  못 읽으면 빈 목록."""
    try:
        return sorted((e.path for e in os.scandir(d)
                       if e.is_file() and CONFIG.is_image(e.name)),
                      key=lambda x: os.path.basename(x).lower())
    except OSError:
        return []


def _list_groups(paths) -> list[_PhotoGroup]:
    """끌어다 놓은 것 → 폴더별 묶음.  규칙:

    - 사진이 바로 든 폴더 → 그 폴더 하나.
    - 사진이 바로 안 든 폴더 → **한 단계 아래** 하위 폴더 중 사진이 든 것마다 하나(LOT 폴더, 이름순).
    - 사진 파일 → 부모 폴더별(연달아 같은 폴더면 한 묶음).
    같은 파일은 한 번만 — 이미 나온 사진은 건너뛴다."""
    groups: list[_PhotoGroup] = []
    seen: set[str] = set()
    file_parent: Optional[str] = None       # 직전이 사진 파일 묶음이면 그 부모 폴더

    def fresh(names) -> list:
        out = []
        for name in names:
            key = os.path.normcase(os.path.normpath(name))
            if key not in seen:
                seen.add(key)
                out.append(Path(name))
        return out

    for raw in paths:
        p = Path(raw)
        try:
            is_dir = p.is_dir()
            is_file = (not is_dir) and CONFIG.is_image(p.name) and p.is_file()
        except OSError:
            continue
        if is_dir:
            file_parent = None
            names = _dir_photos(p)
            if names:
                got = fresh(names)
                if got:
                    groups.append(_PhotoGroup(p, got))
                continue
            try:                            # 사진이 없으면 한 단계 아래(LOT) — 더 깊이는 보지 않는다
                subs = sorted((e.path for e in os.scandir(p) if e.is_dir()),
                              key=lambda x: os.path.basename(x).lower())
            except OSError:
                subs = []
            for sub in subs:
                got = fresh(_dir_photos(sub))
                if got:
                    groups.append(_PhotoGroup(Path(sub), got, p))
        elif is_file:
            parent = os.path.normcase(os.path.normpath(str(p.parent)))
            got = fresh([str(p)])
            if not got:
                continue
            if file_parent == parent and groups:
                groups[-1].photos.extend(got)
            else:
                groups.append(_PhotoGroup(p.parent, got))
                file_parent = parent
    return groups


def list_photo_groups(paths) -> list[tuple[Path, list[Path]]]:
    """끌어다 놓은 것 · 고른 것 → (폴더, 사진들) 묶음 목록.  규칙은 :func:`_list_groups`.

    NAS 를 읽으므로 워커에서 부른다(테스트는 동기)."""
    return [(g.folder, g.photos) for g in _list_groups(paths)]


def list_photos(paths) -> list[Path]:
    """끌어다 놓은 것 · 고른 것 → 사진 목록(:func:`list_photo_groups` 를 펼친 것).

    폴더는 바로 안 사진을, 사진이 없는 폴더(LOT)는 한 단계 아래 웨이퍼 폴더들의 사진을 이름순으로,
    사진 파일은 그대로.  같은 파일은 한 번만."""
    return [p for _folder, photos in list_photo_groups(paths) for p in photos]


def list_groups_with_slots(paths) -> list[_PhotoGroup]:
    """:func:`_list_groups` + 폴더마다 슬롯 번호(여러 폴더일 때만 — 고르기 목록에 쓴다).  NAS 를 읽는다."""
    groups = _list_groups(paths)
    if len(groups) < 2:
        return groups
    return [g._replace(slot=lot_info.read_active_slot(g.folder)) for g in groups]


def group_label(g: _PhotoGroup) -> str:
    """고르기 목록 · 버튼의 한 줄 — ``#슬롯 · 폴더 이름 · N장`` (슬롯을 모르면 번호 없이)."""
    n = len(g.photos)
    if g.slot:
        return i18n.KO.IMAGE_INFO_GROUP_SLOT_FMT.format(slot=g.slot, name=g.folder.name, n=n)
    return i18n.KO.IMAGE_INFO_GROUP_FMT.format(name=g.folder.name, n=n)


class _ListLoader(QThread):
    """:func:`list_groups_with_slots` 를 워커에서 — 폴더 목록은 NAS 왕복이라 UI 를 세우지 않는다."""

    class _Signals(QObject):
        done = pyqtSignal(int, object)                   # (세대, 폴더 묶음 목록)

    def __init__(self, paths: list, gen: int) -> None:
        super().__init__()                  # 부모 없음(_LIVE_SCAN_LOADS 주석)
        self._paths, self._gen = list(paths), gen
        self.signals = self._Signals()

    def run(self) -> None:      # type: ignore[override]
        try:
            got = list_groups_with_slots(self._paths)
        except Exception:
            got = []
        self.signals.done.emit(self._gen, got)


class _ThumbLoader(QThread):
    """띠 썸네일 캐시 파일을 만든다(원본 읽기는 NAS).  QPixmap 은 메인 스레드가 캐시에서 읽는다."""

    class _Signals(QObject):
        ready = pyqtSignal(str, int)                     # (사진, Scan 사진 안 Defect 수 — 쌓음 표시용, 아니면 0)

    def __init__(self, paths: list) -> None:
        super().__init__()
        self._paths = list(paths)
        self.signals = self._Signals()

    def run(self) -> None:      # type: ignore[override]
        for p in self._paths:
            if self.isInterruptionRequested():
                return
            try:
                _io.get_thumb_path(p)
            except Exception:
                continue                    # 못 읽는 사진은 회색 칸으로 남는다
            self.signals.ready.emit(str(p), _stack_count(p))


def stack_badge_count(ok: bool, n_hits: int) -> int:
    """Scan 사진 한 장 안 Defect 수 → 띠 썸네일에 '쌓임' 표시를 할 개수.  둘 미만이면 0(평범한 칸)."""
    return n_hits if ok and n_hits >= 2 else 0


def _stack_count(path) -> int:
    """띠 썸네일용 — Scan 사진이 아니거나 읽지 못하면 0.  NAS 를 읽으므로 워커에서 부른다."""
    try:
        if not scan_image.is_scan_name(Path(path).name):
            return 0
        m = scan_image.defects_in(path)
        return stack_badge_count(m.ok, len(m.hits))
    except Exception:
        return 0


def _describe(path: Path) -> list:
    """정보 덩이 + 미리보기 캐시 예열(실패해도 메인 스레드가 폴백을 그린다)."""
    groups = single_info.describe(path)
    try:
        _io.get_mid_path(path)
    except Exception:
        pass
    return groups


class _ScanSheet:
    """흑백 Scan 사진 한 장의 결과 — 워커가 만들고 메인 스레드가 그린다(QPixmap 없음).

    정보(``hits``·``groups``)는 처음에 한꺼번에 오고, 느린 것(``full`` 원본 · Color 사진)은
    :class:`_ScanFullLoader` 가 나중에 채운다."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.status = scan_image.UNREADABLE
        self.hits: tuple = ()
        self.groups: list = []          # Defect 마다 정보 덩이 목록
        self.full: QImage | None = None
        self.crops: list | None = None  # Defect 별 300 µm Crop QImage(워커가 자른다) — 미리보기가 쓴다
        self._crop_pix: dict[int, QPixmap] = {}
        self.full_failed = False        # 원본 디코드가 끝났는데 못 읽음
        self.color_ok: dict[int, bool] = {}   # Defect 번호 → Color 사진 사용 가능 여부(없으면 아직 모름)
        self._full_pix: QPixmap | None = None     # 메인 스레드에서 한 번만 만든다(넘길 때마다 바꾸지 않게)
        self.file_group = None
        self.gl_ok = False

    def full_pix(self) -> QPixmap:
        if self._full_pix is None:
            self._full_pix = QPixmap.fromImage(self.full)
        return self._full_pix

    def crop_pix(self, i: int) -> Optional[QPixmap]:
        """Defect ``i`` 의 Crop 픽스맵(없으면 None).  메인 스레드에서 한 번만 만든다."""
        if not self.crops or not 0 <= i < len(self.crops) or self.crops[i] is None:
            return None
        if i not in self._crop_pix:
            self._crop_pix[i] = QPixmap.fromImage(self.crops[i])
        return self._crop_pix[i]


def _describe_scan(path: Path) -> _ScanSheet:
    """Scan 사진 → 안에 든 Defect · 정보.  빠른 부분만 — 원본 디코드 · Color 사진은 :class:`_ScanFullLoader`.

    NAS 를 읽으므로 워커에서 부른다.  예외를 내지 않는다."""
    sheet = _ScanSheet(path)
    sheet.file_group = single_info.scan_file_group(path)
    try:
        sheet.gl_ok = gl_loader.is_color(path) is False
    except Exception:
        sheet.gl_ok = False
    m = scan_image.defects_in(path)
    sheet.status = m.status
    if not m.ok:
        return sheet
    sheet.hits = m.hits
    sheet.groups = [single_info.describe_in_scan(path, h.path) for h in m.hits]
    if not m.hits:                      # Defect 가 없으면 원본을 따로 읽지 않는다 — 중간 크기 캐시로 보인다
        try:
            _io.get_mid_path(path)
        except Exception:
            pass
    return sheet


def _async_info() -> bool:
    """헤드리스 테스트는 결정성을 위해 동기로 돈다(`zoom_window` 의 원본 로더와 같은 관례)."""
    return os.environ.get("QT_QPA_PLATFORM", "") != "offscreen"


class _StepPill(QFrame):
    """사진 안에 겹친 Defect 넘기기 띠 — 띠 빈 곳을 눌러도 아래 사진의 '크게 보기' 가 열리지 않게 받아 둔다."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("stepPill")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def mousePressEvent(self, e):           # noqa: N802
        e.accept()


class _ClickLabel(QLabel):
    """누르면 크게(``clicked``).  우클릭은 모든 사진과 같은 메뉴(``photo_menu``) — [크게 보기] 도 ``clicked``."""

    clicked = pyqtSignal()

    def __init__(self, parent=None, path_of=None) -> None:
        super().__init__(parent)
        self._path_of = path_of or (lambda: None)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def mousePressEvent(self, e):           # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)

    def _on_context_menu(self, pos) -> None:
        pm = self.pixmap()
        if pm is None or pm.isNull():               # 사진이 없으면(빈 칸 · '불러오는 중') 메뉴도 없다
            return
        if photo_menu.ask(self, self.mapToGlobal(pos), self._path_of()):
            self.clicked.emit()


class _StripTile(_ClickLabel):
    """띠 썸네일 한 칸.  Scan 사진 안에 Defect 가 여럿이면 카드 두 장이 뒤로 비져나오고 개수 배지가 붙는다.

    평범한 칸은 QSS(``stripTile``)가 테두리를 그리고, 쌓인 칸만 직접 그린다(뒤 카드가 칸 밖으로 나가야 해서)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.stack_n = 0

    def set_stack(self, n: int) -> None:
        """Defect 수 ``n``(2 이상이면 쌓임 표시, 아니면 평범한 칸)."""
        n = n if n >= 2 else 0
        if n == self.stack_n:
            return
        self.stack_n = n
        grow = 2 * _STACK_OFF if n else 0
        self.setProperty("role", "stripTileStack" if n else "stripTile")
        self.style().unpolish(self)
        self.style().polish(self)
        self.setFixedSize(_STRIP_PX + grow, _STRIP_PX + grow)
        self.update()

    def paintEvent(self, event):    # noqa: N802
        if not self.stack_n:
            super().paintEvent(event)
            return
        o = _STACK_OFF
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = theme.PROFILE.radius_sm
        line = QColor(theme.LINE2)
        # 뒤 카드 둘(위 · 오른쪽으로 비져나옴) → 맨 앞 카드(왼쪽 아래).
        for k in (2, 1):
            p.setPen(QPen(line, 1))
            p.setBrush(QColor(theme.PANEL))
            p.drawRoundedRect(QRectF(k * o + 0.5, (2 - k) * o + 0.5,
                                     _STRIP_PX - 1, _STRIP_PX - 1), r, r)
        front = QRectF(0.5, 2 * o + 0.5, _STRIP_PX - 1, _STRIP_PX - 1)
        cur = self.property("current") == "true"
        p.setPen(QPen(QColor(theme.ACCENT) if cur else line, 2 if cur else 1))
        p.setBrush(QColor(theme.PANEL))
        p.drawRoundedRect(front, r, r)
        pm = self.pixmap()
        if pm is not None and not pm.isNull():
            w, h = pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio()
            p.drawPixmap(int(front.center().x() - w / 2), int(front.center().y() - h / 2), pm)
        # 개수 배지 — 맨 앞 카드 오른쪽 아래
        f = QFont(self.font())
        f.setPixelSize(10)
        f.setBold(True)
        p.setFont(f)
        txt = str(self.stack_n)
        bw = max(14, QFontMetrics(f).horizontalAdvance(txt) + 8)
        badge = QRect(self.width() - bw - 1, self.height() - 14 - 1, bw, 14)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        p.drawRoundedRect(badge, 7, 7)
        p.setPen(QColor(theme.ON_ACCENT))
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, txt)
        p.end()


def _shape_columns(grid: QGridLayout, label_w: int) -> None:
    """라벨 열은 고정 폭, 값 열이 남은 자리를 먹는다 — 값의 시작선을 한 줄로 모은다.

    타이틀블록과 행이 **같은 함수로** 열을 잡아야 컬럼 머리가 값 위에 정확히 선다.
    """
    grid.setColumnMinimumWidth(0, label_w)
    grid.setColumnStretch(1, 1)


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
        install_paste(self, self._on_paste_paths, self._on_paste_no_path)
        self._path: Path | None = None
        self._paths: list[Path] = []        # 넘겨 볼 사진들(한 장이면 띠를 숨긴다)
        self._pos = 0
        self._list_gen = 0
        self._thumb_ok: set[str] = set()    # 띠 썸네일 캐시가 준비된 사진
        self._stack_n: dict[str, int] = {}  # Scan 사진 → 안에 든 Defect 수(2 이상만 의미 — 띠 쌓임 표시)
        self._photo_groups: list[_PhotoGroup] = []   # 연 폴더 묶음(둘 이상이면 위에 고르기 버튼)
        self._group_i = -1                  # 지금 보는 묶음 번호, -1 = 전체
        self._group_menu: QMenu | None = None
        self._thumb_job: _ThumbLoader | None = None
        self._strip_map: dict = {}          # 띠에 보이는 사진 → 칸
        self._groups: list = []
        self._scan_gen = 0
        self._info_gen = 0
        self._scan_crop: QImage | None = None
        self._scan_others: list = []        # 같은 Scan 안 다른 Defect 의 Crop 범위 — 전체화면에 함께 그린다
        self._scan_full: QImage | None = None
        self._scan_box = None
        self._scan_name = ""
        self._scan_dots = QTimer(self)
        self._scan_dots.setInterval(_SCAN_DOTS_MS)
        self._scan_dots.timeout.connect(self._tick_scan_dots)
        self._scan_dot_i = 0
        self._sheet: _ScanSheet | None = None     # 흑백 Scan 사진을 넣었을 때만
        self._hit_i = 0
        self._loading = LoadingOverlay(self)
        self._picker = FilePicker(self, purpose="image_info")
        self._picker.picked.connect(self.open_many)
        self._build()
        if image_path:
            self.show_image(Path(image_path))

    def sizeHint(self) -> QSize:  # noqa: N802
        """시트 호스트가 읽는 크기.  값 라벨이 `Ignored` 폭이라 레이아웃 계산값은
        값 열을 거의 0 으로 잡는다 — 그대로 두면 창 안 시트에서 값이 '1.' 처럼
        잘린다.  창보다 크면 시트 호스트가 줄인다."""
        return QSize(1100, 800)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        # 동급 시트와 같은 여백 — bulk_select_dialog / slot_select_dialog 가 16/10.
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(theme.PROFILE.section_gap // 2)

        # [사진 선택] 은 비어 있는 동안 가운데 큰 파랑(``EmptyOpen``) 하나뿐이다 — 연 뒤에는 Ctrl+O ·
        # 끌어다 놓기 · Ctrl+V 로 다시 연다(위 왼쪽 버튼은 없앴다, 사용자 지시).
        top = QHBoxLayout()
        # 폴더(웨이퍼)를 둘 이상 열었을 때만 — 어느 폴더 사진을 넘겨 볼지 고른다.
        self.group_btn = NeonButton("", role="ghost")
        self.group_btn.setAutoDefault(False)
        self.group_btn.clicked.connect(self._open_group_menu)
        top.addWidget(self.group_btn)
        self.group_btn.setVisible(False)
        top.addStretch(1)
        root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(theme.PROFILE.card_pad)
        body.addWidget(self._build_preview())
        rule = QFrame(self)
        rule.setProperty("role", "vrule")
        body.addWidget(rule)
        body.addWidget(self._build_sheet(), stretch=1)
        root.addLayout(body, stretch=1)

        root.addWidget(self._build_strip())
        self._build_actions()      # 레이아웃 밖에 떠 있는 버튼 — 시트가 바닥까지 쓴다

        # 파워 유저 경로 — 사진 열기를 손에서 떼지 않고.  ★ Ctrl+C 는 묶지 않는다:
        # 값 라벨이 선택 복사를 지원하므로, 창 단축키로 가로채면 사용자가 고른 수치
        # 대신 시트 전체가 복사돼 버린다.  전체 복사는 버튼이 맡는다.
        QShortcut(QKeySequence.StandardKey.Open, self, self._on_pick)
        self._empty_open = EmptyOpen(self, i18n.KO.IMAGE_INFO_PICK, self._on_pick,
                                     i18n.KO.EMPTY_DROP_HINT, preview="info")
        # 여러 장일 때 ← → 로 넘긴다(띠가 숨어 있으면 아무 일도 없다).
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self.step_photo(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self.step_photo(1))
        self._render()
        # 열자마자 주요 동작에 포커스 — 시트 호스트는 다이얼로그 자신에 포커스를
        # 주므로, 그대로 두면 첫 Tab 이 허비된다.
        QTimer.singleShot(0, self._focus_pick)

    def _focus_pick(self) -> None:
        if self._path is None:
            self._empty_open.btn.setFocus()
        elif not self.group_btn.isHidden():
            self.group_btn.setFocus()
        else:
            self.copy_btn.setFocus()

    def _build_preview(self) -> QWidget:
        host = QWidget(self)
        host.setMinimumWidth(_PREVIEW_MIN_W)
        host.setMaximumWidth(_PREVIEW_MAX_W)
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.preview = _ClickLabel(host, path_of=lambda: self._path)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.clicked.connect(self._open_full_color)
        self.preview.setFixedSize(_PREVIEW_MIN_W, _PREVIEW_MIN_W)
        # 프레임이 사진을 **딱 감싸도록** 위쪽 정렬로 두고, 크기는 픽스맵에 맞춘다.
        lay.addWidget(self.preview, alignment=Qt.AlignmentFlag.AlignTop
                      | Qt.AlignmentFlag.AlignHCenter)
        # 흑백 Scan 사진 안 Defect 가 여럿일 때만 — ◀ Defect i / n ▶.  사진 **안** 아래쪽에 반투명 띠로
        # 겹쳐 띄운다(현택님 2026-11-25 '차라리 사진 안에') — 레이아웃 줄을 따로 먹지 않는다.
        self.step_bar = _StepPill(self.preview)
        step = QHBoxLayout(self.step_bar)
        step.setContentsMargins(4, 2, 4, 2)
        step.setSpacing(2)
        self.prev_btn = QPushButton(i18n.KO.SCAN_DEFECT_PREV, self.step_bar)
        self.next_btn = QPushButton(i18n.KO.SCAN_DEFECT_NEXT, self.step_bar)
        self.step_label = QLabel("", self.step_bar)
        self.step_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.step_label.setProperty("role", "mono")
        for b, d in ((self.prev_btn, -1), (self.next_btn, 1)):
            b.setFixedSize(26, 24)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setAutoDefault(False)
            b.clicked.connect(lambda _=False, d=d: self._step_hit(d))
        step.addWidget(self.prev_btn)
        step.addWidget(self.step_label)
        step.addWidget(self.next_btn)
        self.step_bar.setStyleSheet(
            "QFrame#stepPill { background: rgba(20, 20, 20, 90); border: none; border-radius: 12px; }"
            " QLabel { color: white; background: transparent; border: none; padding: 0 4px; }"
            " QPushButton { color: white; background: transparent; border: none; padding: 0px;"
            " border-radius: 10px; }"
            " QPushButton:hover { background: rgba(255, 255, 255, 50); }")
        self.step_bar.setVisible(False)
        # Scan image — Scan 이 있다고 판정될 때만 보인다.
        self.scan_title = QLabel(i18n.KO.SCAN_IMAGE_TITLE, host)
        self.scan_title.setProperty("role", "mutedCaption")
        self.scan_view = _ClickLabel(host, path_of=self._scan_view_path)
        self.scan_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scan_view.setWordWrap(True)
        self.scan_view.clicked.connect(self._on_scan_view_clicked)
        lay.addWidget(self.scan_title)
        lay.addWidget(self.scan_view, alignment=Qt.AlignmentFlag.AlignTop
                      | Qt.AlignmentFlag.AlignHCenter)
        self._set_scan_visible(False)
        lay.addStretch(1)
        self._preview_host = host
        self._style_preview()
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

    def _build_strip(self) -> QWidget:
        """여러 장 넘겨 보기 띠 — 지금 사진 앞뒤 썸네일 · i / n(← → 로 넘긴다).  한 장이면 숨긴다."""
        bar = QWidget(self)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._strip_tiles = QHBoxLayout()
        self._strip_tiles.setSpacing(4)
        lay.addStretch(1)
        lay.addLayout(self._strip_tiles)
        lay.addStretch(1)
        self.photo_pos = QLabel("", bar)
        self.photo_pos.setProperty("role", "mutedStrong")
        self.photo_pos.setMinimumWidth(120)
        self.photo_pos.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.photo_pos)
        self.strip_bar = bar
        bar.setVisible(False)
        return bar

    def _build_actions(self) -> QWidget:
        # ★ 루트 레이아웃에 넣지 않는다 — 가로 한 줄을 통째로 차지해 시트 높이를 깎았다.
        #   오른쪽 아래에 버튼 크기만큼만 떠 있고(`_place_float`), 스크롤해 가려져도
        #   필요한 정보는 다 보인다(사용자 결정).  EmptyOpen 보다 먼저 만들어 그 아래에 깔린다.
        self._float = QWidget(self)
        bar = QHBoxLayout(self._float)
        bar.setContentsMargins(0, 0, 0, 0)
        # 복사 확인 — 모달 대신 버튼 옆에서 잠깐 뜨는 줄(자리는 늘 예약).
        self._toast = QLabel("", self._float)
        self._toast.setProperty("role", "muted")
        self._toast.setMinimumWidth(96)
        bar.addWidget(self._toast)
        # 지금 보고 있는 사진을 그대로 GL 히스토그램 뷰어로 넘긴다(컬러 결함 사진이면 Scan 의
        # 결함 영역이 확대돼 열린다).  사진이 없거나, Scan 없는 컬러 사진이면 누를 수 없다
        # (`_ScanLoader` 가 판정한다).
        self.gl_btn = NeonButton(i18n.KO.IMAGE_INFO_GL, role="ghost", parent=self._float)
        self.gl_btn.setMinimumHeight(theme.PROFILE.control_h_lg)
        self.gl_btn.setEnabled(False)
        self.gl_btn.clicked.connect(self._on_open_gl)
        bar.addWidget(self.gl_btn)
        self.gl_btn.setVisible(config.GL_HISTOGRAM_READY)   # 준비 전에는 숨긴다
        self.copy_btn = NeonButton(i18n.KO.IMAGE_INFO_COPY, role="ghost", parent=self._float)
        self.copy_btn.setMinimumHeight(theme.PROFILE.control_h_lg)
        bar.addWidget(self.copy_btn)
        self.copy_btn.clicked.connect(self._on_copy)
        # ★ 바닥에 '닫기' 를 두지 않는다.  `windowTitle` 이 있으면 sheet_host 가
        #   제목줄에 ✕(닫기)를 붙이므로, 또 두면 닫기 어포던스가 둘이 된다.
        #   이 앱의 관습도 그렇다 — 확인/취소를 받는 '동작' 시트만 바닥 버튼을 두고,
        #   보기 전용 시트(unmatched_review·label_maker·dev_benchmark)는 두지 않는다.
        #   시트일 때는 Esc 로 닫힌다.  설정 화면의 왼쪽 메뉴가 **화면으로** 끼워 쓸
        #   때는 닫기 대신 메뉴로 나가므로 Esc 를 삼킨다(`SetupPage._make_tool_page`).
        # ★ Enter 를 autoDefault 에 맡기지 않는다 — 마지막으로 눌린 버튼이 다시
        #   실행돼 '복사'가 뜻하지 않게 재발동한다(slot_select_dialog 와 같은 함정).
        self.copy_btn.setAutoDefault(False)
        self.gl_btn.setAutoDefault(False)
        self._place_float()
        return self._float

    def _place_float(self) -> None:
        """떠 있는 버튼 묶음을 오른쪽 아래(여백 16)에 붙인다.  여러 장 띠가 보이면 그 위에."""
        f = self._float
        f.adjustSize()
        bottom = self.height() - 16
        if self.strip_bar.isVisible():
            bottom -= self.strip_bar.sizeHint().height() + theme.PROFILE.section_gap // 2
        f.move(self.width() - 16 - f.width(), bottom - f.height())

    # ------------------------------------------------------------------
    # 입력 — 파일 선택 / 드래그&드롭
    # ------------------------------------------------------------------
    def _on_pick(self) -> None:
        # OS 파일 브라우저를 그대로 쓴다(sheet_host 모듈 docstring 의 결정) — 비동기로
        # (`ui/folder_picker.py`, `exec()` 는 Windows 에서 UI 스레드를 세운다).
        patterns = " ".join(f"*{e}" for e in CONFIG.image_extensions)
        start = str(self._path.parent) if self._path else ""
        self._picker.open(i18n.KO.IMAGE_INFO_PICK, start,
                          i18n.KO.IMAGE_INFO_FILE_FILTER_FMT.format(patterns=patterns),
                          multi=True)       # 여러 장 고를 수 있다(넘겨 보기)

    def hideEvent(self, event):         # noqa: N802
        drop_clear(self)                # 끌던 중에 숨겨져도 표시가 남지 않게
        super().hideEvent(event)

    def dragEnterEvent(self, event):        # noqa: N802
        # 이름만 본다(끄는 동안 디스크를 읽지 않는다) — 사진 파일이나 폴더면 받는다.
        kinds = guess_kinds(self._dropped_paths(event))
        ok = bool(kinds & {"image", "folder"})
        drop_feedback(self, event, None, i18n.KO.DROP_PILL_OPEN_IMAGE if ok
                      else i18n.KO.DROP_PILL_ONLY_FOLDER_PHOTO, ok)

    def dragMoveEvent(self, event):         # noqa: N802
        self.dragEnterEvent(event)

    def dragLeaveEvent(self, event):        # noqa: N802
        drop_clear(self)
        event.accept()

    def dropEvent(self, event):             # noqa: N802
        drop_clear(self)
        paths = self._dropped_paths(event)
        if not guess_kinds(paths) & {"image", "folder"}:
            event.ignore()
            return
        event.acceptProposedAction()
        self.open_many(paths)

    def _on_paste_paths(self, paths: list[str]) -> None:
        """Ctrl+V — 붙여 넣은 사진 · 폴더를 모두 연다(여러 장이면 넘겨 보기)."""
        if not guess_kinds(paths) & {"image", "folder"}:
            self._flash(i18n.KO.DROP_WRONG_KIND_IMAGE)
        else:
            self.open_many(paths)

    def _on_paste_no_path(self, has_image: bool) -> None:
        self._flash(i18n.KO.DROP_NO_PATH_IMAGE if has_image
                    else i18n.KO.DROP_NOTHING_TO_PASTE)

    def _flash(self, text: str) -> None:
        # 비어 있는 동안은 빈 화면이 호스트를 덮어 아래 토스트가 안 보인다 — 빈 화면 위에 띄운다.
        if self._path is None:
            self._empty_open.flash(text, _TOAST_MS)
            return
        motion.flash_text(self._toast, text, hold_ms=_TOAST_MS)
        self._place_float()

    def _style_preview(self) -> None:
        """미리보기 프레임 — 비어 있으면 점선, 사진이 들어오면 실선(검토 화면 썸네일과 같은 관습).

        끌어놓기 표시는 ``drop_overlay`` 가 맡는다."""
        style = "solid" if self._path is not None else "dashed"
        self.preview.setStyleSheet(
            f"border: 1px {style} {theme.THUMB_FRAME};"
            f" border-radius: {theme.PROFILE.radius_sm}px; color: {theme.MUTE};")

    @staticmethod
    def _dropped_paths(event) -> list[str]:
        """드롭된 로컬 경로들(디스크를 보지 않는다)."""
        data = event.mimeData()
        return local_paths(data) if data is not None and data.hasUrls() else []

    # ------------------------------------------------------------------
    def _on_open_gl(self) -> None:
        if self._path is None:
            return
        from .gl_viewer_dialog import GlViewerDialog
        sheets.run(GlViewerDialog(self, image_path=str(self._path)), full_bleed=True)

    def open_many(self, paths) -> None:
        """사진 · 폴더 여러 개 → 넘겨 볼 목록을 만들고 첫 사진을 연다(현택님 2026-11-24 안 A).

        폴더 안에 사진이 없으면 한 단계 아래 폴더들(LOT)을 본다 — 폴더별 묶음은 위 고르기 버튼으로.
        폴더 목록은 NAS 를 읽으므로 워커에서 만들고 그동안 로딩 표시를 띄운다."""
        paths = [str(p) for p in paths]
        if not paths:
            return
        self._list_gen += 1
        if not _async_info():
            self._on_listed(self._list_gen, list_groups_with_slots(paths))
            return
        self._loading.show_overlay(i18n.KO.IMAGE_INFO_LISTING)
        self._loading.set_progress(0, 0, i18n.KO.IMAGE_INFO_LISTING)   # 양을 모른다 — busy
        ld = _ListLoader(paths, self._list_gen)
        ld.signals.done.connect(self._on_listed)
        _LIVE_SCAN_LOADS.add(ld)
        ld.finished.connect(lambda: _LIVE_SCAN_LOADS.discard(ld))
        ld.start()

    def _on_listed(self, gen: int, groups) -> None:
        if gen != self._list_gen:
            return
        photos = [p for g in groups for p in g.photos]
        if not photos:
            self._loading.hide_overlay()
            self._flash(i18n.KO.IMAGE_INFO_NO_PHOTOS)
            return
        self._photo_groups, self._group_i = list(groups), -1
        self._paths, self._pos = photos, 0
        self._load_photo(self._paths[0])
        self._style_preview()               # 사진이 들어왔으니 점선 → 실선
        self._render_strip()
        self._update_group_btn()

    # ------------------------------------------------------------------
    # 폴더 고르기 — 둘 이상 열었을 때
    # ------------------------------------------------------------------
    def _all_label(self) -> str:
        total = sum(len(g.photos) for g in self._photo_groups)
        return i18n.KO.IMAGE_INFO_GROUP_ALL_FMT.format(n=total)

    def _update_group_btn(self) -> None:
        many = len(self._photo_groups) >= 2
        self.group_btn.setVisible(many)
        if many:
            label = (group_label(self._photo_groups[self._group_i])
                     if self._group_i >= 0 else self._all_label())
            self.group_btn.setText(i18n.KO.IMAGE_INFO_GROUP_BTN_FMT.format(label=label))

    def _open_group_menu(self) -> None:
        """폴더 목록 — 표준 메뉴를 막지 않고(``popup``) 연다."""
        menu = QMenu(self)                  # 사진 메뉴 아님 — 폴더 고르기 목록
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        act = menu.addAction(self._all_label())
        act.setCheckable(True)
        act.setChecked(self._group_i < 0)
        act.triggered.connect(lambda _=False: self.choose_group(-1))
        lot = None
        for i, g in enumerate(self._photo_groups):
            if g.lot is not None and g.lot != lot:
                head = menu.addAction(g.lot.name)       # LOT 폴더 이름 머리줄 — 누를 수 없다
                head.setEnabled(False)
            lot = g.lot
            act = menu.addAction(group_label(g))
            act.setCheckable(True)
            act.setChecked(i == self._group_i)
            act.triggered.connect(lambda _=False, i=i: self.choose_group(i))
        self._group_menu = menu
        menu.popup(self.group_btn.mapToGlobal(QPoint(0, self.group_btn.height())))

    def choose_group(self, i: int) -> None:
        """폴더 ``i``(-1 = 전체)의 사진으로 넘겨 볼 목록을 바꾸고 첫 사진을 연다."""
        if i >= len(self._photo_groups):
            return
        self._group_i = i
        self._paths = ([p for g in self._photo_groups for p in g.photos] if i < 0
                       else list(self._photo_groups[i].photos))
        self._pos = 0
        self._load_photo(self._paths[0])
        self._render_strip()
        self._update_group_btn()

    def step_photo(self, d: int) -> None:
        """여러 장일 때 앞 · 뒤 사진으로(끝에서 멈춘다)."""
        self.go_photo(self._pos + d)

    def go_photo(self, i: int) -> None:
        if len(self._paths) < 2 or not 0 <= i < len(self._paths) or i == self._pos:
            return
        self._pos = i
        self._load_photo(self._paths[i])
        self._render_strip()

    def _render_strip(self) -> None:
        """띠를 지금 사진 앞뒤 :data:`_STRIP_HALF` 장으로 다시 만든다(위젯 수가 사진 수와 무관)."""
        n = len(self._paths)
        self.strip_bar.setVisible(n > 1)
        self._place_float()
        while self._strip_tiles.count():
            w = self._strip_tiles.takeAt(0).widget()
            if w is not None:
                w.hide()                    # 지워질 때까지 띠 왼쪽 위에 겹쳐 보이지 않게
                w.deleteLater()
        self._strip_map = {}
        if n < 2:
            return
        self.photo_pos.setText(i18n.KO.IMAGE_INFO_PHOTO_POS_FMT.format(i=self._pos + 1, n=n))
        lo = max(0, self._pos - _STRIP_HALF)
        hi = min(n, self._pos + _STRIP_HALF + 1)
        need = []
        for i in range(lo, hi):
            p = self._paths[i]
            tile = _StripTile(self.strip_bar)
            tile.setFixedSize(_STRIP_PX, _STRIP_PX)
            tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tile.setCursor(Qt.CursorShape.PointingHandCursor)
            tile.setToolTip(p.name)
            tile.setProperty("role", "stripTile")
            tile.setProperty("current", "true" if i == self._pos else "false")
            tile.clicked.connect(lambda i=i: self.go_photo(i))
            self._strip_tiles.addWidget(tile)
            self._strip_map[str(p)] = tile
            self._apply_stack(str(p))
            if str(p) in self._thumb_ok:
                self._set_strip_pix(str(p))
            else:
                need.append(p)
        if need:
            self._want_thumbs(need)

    def _apply_stack(self, path: str) -> None:
        """알려진 Defect 수대로 칸을 쌓임 모양으로 · 툴팁에 개수."""
        tile = self._strip_map.get(path)
        if tile is None:
            return
        n = self._stack_n.get(path, 0)
        try:
            tile.set_stack(n)
            tile.setToolTip(Path(path).name if n < 2 else
                            f"{Path(path).name}\n"
                            + i18n.KO.IMAGE_INFO_STRIP_DEFECTS_FMT.format(n=n))
        except RuntimeError:
            pass                            # 띠를 다시 만들며 지운 칸

    def _want_thumbs(self, paths: list) -> None:
        """띠 썸네일 캐시를 워커에서 만든다 — 이전 요청이 돌고 있으면 멈추고 지금 띠 것만."""
        if not _async_info():
            return                          # 헤드리스 테스트는 회색 칸 그대로
        if self._thumb_job is not None:
            self._thumb_job.requestInterruption()
        job = _ThumbLoader(paths)
        job.signals.ready.connect(self._on_thumb_ready)
        _LIVE_SCAN_LOADS.add(job)
        job.finished.connect(lambda: _LIVE_SCAN_LOADS.discard(job))
        self._thumb_job = job
        job.start()

    def _on_thumb_ready(self, path: str, stack_n: int = 0) -> None:
        self._thumb_ok.add(path)
        self._stack_n[path] = stack_n
        self._apply_stack(path)
        self._set_strip_pix(path)

    def _set_strip_pix(self, path: str) -> None:
        tile = self._strip_map.get(path)
        if tile is None:
            return
        try:
            tile.setPixmap(_io.load_thumb_qpixmap(Path(path), _STRIP_PX - 4))
        except RuntimeError:
            pass                            # 띠를 다시 만들며 지운 칸

    def show_image(self, path: Path) -> None:
        """사진 한 장을 연다 — 넘겨 보기 목록은 이 한 장으로 바뀐다."""
        self._list_gen += 1                 # 읽던 폴더 목록이 늦게 와도 버린다
        self._paths, self._pos = [Path(path)], 0
        self._photo_groups, self._group_i = [], -1
        self._update_group_btn()
        self._load_photo(Path(path))
        self._render_strip()

    def _load_photo(self, path: Path) -> None:
        """사진을 조회해 미리보기와 계측 표를 갱신한다.

        정보 읽기·미리보기·Scan 은 전부 NAS 를 읽어 느리므로 로딩 표시를 띄우고 워커에서
        돌린다.  덮개는 **정보가 오면 바로** 걷힌다 — Scan 원본(수 MB)을 기다리지 않는다.
        Scan 은 자기 칸에서 '불러오는 중' 점 애니메이션으로 따로 기다린다(사용자 요청)."""
        self._path = Path(path)
        self._empty_open.set_empty(False)
        self.gl_btn.setEnabled(False)     # GL 로 볼 수 있는지는 `_ScanLoader` 가 알려 준다
        self._info_gen += 1
        self._scan_gen += 1               # 이전 사진의 Scan 결과가 늦게 와도 버린다
        self._sheet = None
        self._clear_scan()
        fn = _describe_scan if scan_image.is_scan_name(self._path.name) else _describe
        if not _async_info():
            self._on_info_done(self._info_gen, fn(self._path))
            return
        self._loading.show_overlay(i18n.KO.IMAGE_INFO_LOADING)
        self._loading.set_progress(0, 0, i18n.KO.IMAGE_INFO_LOADING)   # busy — 0 에 멈추지 않는다
        ld = _InfoLoader(self._path, self._info_gen, fn)
        ld.signals.done.connect(self._on_info_done)
        _LIVE_SCAN_LOADS.add(ld)
        ld.finished.connect(lambda: _LIVE_SCAN_LOADS.discard(ld))
        ld.start()

    def _on_info_done(self, gen: int, groups) -> None:
        if gen != self._info_gen:
            return
        self._loading.hide_overlay()    # 정보는 다 왔다 — Scan 은 자기 칸에서 기다린다
        if isinstance(groups, _ScanSheet):
            self._sheet = groups
            self.gl_btn.setEnabled(groups.gl_ok)
            self._show_hit(0)
            self._start_scan_full()
            return
        self._groups = groups
        self._start_scan()
        self._render()

    # ------------------------------------------------------------------
    # 흑백 Scan 사진 — 안에 든 Defect 고르기
    # ------------------------------------------------------------------
    def _place_step_bar(self) -> None:
        """Defect 넘기기 띠를 Scan 사진 안 아래 가운데에 둔다(사진 크기가 바뀔 때마다)."""
        bar = self.step_bar
        if bar.isHidden():
            return
        bar.adjustSize()
        pw, ph = self.preview.width(), self.preview.height()
        bar.move(max(0, (pw - bar.width()) // 2), max(0, ph - bar.height() - 6))
        bar.raise_()

    def _show_hit(self, i: int) -> None:
        """Defect ``i`` 를 고른다 — 표·Color 사진·미리보기 네모가 함께 바뀐다(파일을 다시 읽지 않는다)."""
        sheet = self._sheet
        n = len(sheet.hits)
        self._hit_i = i % n if n else 0
        self.scan_title.setText(i18n.KO.SCAN_DEFECT_COLOR_TITLE)
        self.step_bar.setVisible(n > 1)
        self._place_step_bar()
        self._scan_crop = None                  # 이 모드에서는 300 µm Crop 을 보이지 않는다
        if n:
            hit = sheet.hits[self._hit_i]
            self._groups = sheet.groups[self._hit_i]
            self._scan_full = sheet.full
            self._scan_box, self._scan_name = tuple(hit.box), str(sheet.path)
            self._scan_others = [tuple(h.box) for h in sheet.hits]
            self.step_label.setText(i18n.KO.SCAN_DEFECT_STEP_FMT.format(i=self._hit_i + 1, n=n))
            self._set_scan_visible(True)
        else:
            self._groups = [sheet.file_group] if sheet.file_group else []
            self._scan_box = self._scan_full = None
            self._scan_dots.stop()
            self._set_scan_visible(False)
        self._render()

    def _start_scan_full(self) -> None:
        """느린 부분 — Scan 원본 디코드와 Color 사진 준비를 워커에 맡긴다(덮개는 이미 걷혔다)."""
        sheet = self._sheet
        if sheet is None or not sheet.hits:
            return
        ld = _ScanFullLoader(sheet.path, self._scan_gen, [h.path for h in sheet.hits],
                             [h.box for h in sheet.hits])
        ld.signals.full.connect(self._on_scan_full)
        ld.signals.color.connect(self._on_hit_color)
        _LIVE_SCAN_LOADS.add(ld)
        ld.finished.connect(lambda: _LIVE_SCAN_LOADS.discard(ld))
        ld.start()

    def _on_scan_full(self, gen: int, img, crops=None) -> None:
        if gen != self._scan_gen or self._sheet is None:
            return                      # 이미 다른 사진으로 넘어갔다
        self._sheet.full = img
        self._sheet.crops = crops
        self._sheet.full_failed = img is None
        self._scan_full = img
        self._refresh_preview()

    def _on_hit_color(self, gen: int, i: int, ok: bool) -> None:
        if gen != self._scan_gen or self._sheet is None:
            return
        self._sheet.color_ok[i] = ok
        if i == self._hit_i:
            self._refresh_preview()

    def _scan_view_path(self):
        """아래 칸 우클릭 메뉴가 가리킬 사진 — Scan 사진 모드에서는 고른 Defect 의 Color 사진."""
        sheet = self._sheet
        if sheet is not None and sheet.hits and sheet.color_ok.get(self._hit_i):
            return sheet.hits[self._hit_i].path
        return None

    def _step_hit(self, d: int) -> None:
        if self._sheet is not None and self._sheet.hits:
            self._show_hit(self._hit_i + d)

    def _scan_preview_pix(self, max_w: int, max_h: int) -> QPixmap:
        """고른 Defect 의 300 µm Crop(늘 Crop — 전체 Scan 은 크게 볼 때만).  Crop 이 아직이면 중간 크기 캐시."""
        pix = self._sheet.crop_pix(self._hit_i)
        if pix is None:
            return _io.load_thumb_qpixmap(self._path, max_w, kind="mid")
        return pix.scaled(max_w, max(1, max_h), Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)

    # ------------------------------------------------------------------
    # Scan image
    # ------------------------------------------------------------------
    def _set_scan_visible(self, on: bool) -> None:
        self.scan_title.setVisible(on)
        self.scan_view.setVisible(on)

    def _clear_scan(self) -> None:
        """이전 사진의 Scan 칸 · Defect 넘기기를 치운다."""
        self._scan_crop = self._scan_full = self._scan_box = None
        self._scan_others = []
        self._scan_name = ""
        self._scan_dots.stop()
        self._set_scan_visible(False)
        self.step_bar.setVisible(False)
        self.scan_title.setText(i18n.KO.SCAN_IMAGE_TITLE)

    def _start_scan(self) -> None:
        """이전 사진의 Scan 을 즉시 치우고 새 사진의 판정을 워커에 맡긴다."""
        self._scan_gen += 1
        self._clear_scan()
        ld = _ScanLoader(self._path, self._scan_gen)
        ld.signals.found.connect(self._on_scan_found)
        ld.signals.gl.connect(self._on_gl_known)
        ld.signals.others.connect(self._on_scan_others)
        ld.signals.done.connect(self._on_scan_done)
        _LIVE_SCAN_LOADS.add(ld)
        ld.finished.connect(lambda: _LIVE_SCAN_LOADS.discard(ld))
        ld.start()

    def _on_scan_others(self, gen: int, boxes) -> None:
        if gen == self._scan_gen:
            self._scan_others = list(boxes or [])

    def _on_gl_known(self, gen: int, ok: bool) -> None:
        if gen == self._scan_gen:
            self.gl_btn.setEnabled(ok)

    def _on_scan_found(self, gen: int) -> None:
        if gen != self._scan_gen:
            return
        self._scan_dot_i = 0
        self._show_scan_text(self._scan_loading_text())
        self._scan_dots.start()

    def _scan_loading_text(self) -> str:
        frames = i18n.KO.SCAN_IMAGE_LOADING_DOTS
        return (f"{i18n.KO.SCAN_IMAGE_LOADING}\n"
                f"{frames[self._scan_dot_i % len(frames)]}")

    def _tick_scan_dots(self) -> None:
        """점 한 칸 이동 — 글자만 바꾼다(칸 크기·테두리는 그대로)."""
        self._scan_dot_i += 1
        text = self._scan_loading_text()
        sh = self._sheet
        if sh is None:
            self.scan_view.setText(text)
            return
        if self._full_loading():
            self.preview.setText(text)
        if sh.hits and sh.color_ok.get(self._hit_i) is None:
            self.scan_view.setText(text)

    def _full_loading(self) -> bool:
        """Scan 사진 모드에서 원본 디코드를 기다리는 중인가."""
        sh = self._sheet
        return bool(sh is not None and sh.hits and sh.full is None and not sh.full_failed)

    def _sync_scan_dots(self) -> None:
        """Scan 사진 모드 — 기다리는 칸이 하나라도 있으면 점이 돌고, 다 오면 멈춘다."""
        sh = self._sheet
        if sh is None:
            return
        waiting = self._full_loading() or bool(sh.hits and sh.color_ok.get(self._hit_i) is None)
        if waiting and not self._scan_dots.isActive():
            self._scan_dots.start()
        elif not waiting:
            self._scan_dots.stop()

    def _on_scan_done(self, gen, status, crop, full, box, name) -> None:
        if gen != self._scan_gen:
            return                      # 이미 다른 사진으로 넘어갔다
        self._scan_dots.stop()
        if status == scan_image.OK and crop is not None:
            self._scan_crop, self._scan_full = crop, full
            self._scan_box, self._scan_name = box, name
            self._set_scan_visible(True)
            self._refresh_preview()
        elif status == scan_image.UNREADABLE:
            self._show_scan_text(i18n.KO.SCAN_IMAGE_UNREADABLE)
        else:
            self._set_scan_visible(False)

    def _show_scan_text(self, text: str) -> None:
        self._scan_crop = None
        self.scan_view.setPixmap(QPixmap())
        self.scan_view.setText(text)
        self.scan_view.setCursor(Qt.CursorShape.ArrowCursor)
        self.scan_view.setToolTip("")
        self.scan_view.setStyleSheet(
            f"border: 1px dashed {theme.THUMB_FRAME};"
            f" border-radius: {theme.PROFILE.radius_sm}px; color: {theme.MUTE};")
        self._set_scan_visible(True)
        self._refresh_preview()

    def _scan_side(self, preview_w: int, preview_h: int) -> int:
        """Scan 칸 한 변 — Color 폭을 넘지 않고, 미리보기 열 높이 안에 들게."""
        host = self._preview_host
        room = (host.height() - preview_h - self.scan_title.sizeHint().height()
                - 2 * host.layout().spacing())
        return max(_SCAN_MIN_SIDE, min(preview_w, room))

    def _refresh_scan(self, preview_w: int, preview_h: int) -> None:
        """이미 메모리에 있는 Crop 만 다시 스케일한다(파일을 다시 읽지 않는다)."""
        if self._sheet is not None:
            self._refresh_hit_color(preview_w, preview_h)
            return
        if self.scan_view.isHidden() and self._scan_crop is None:
            return
        side = self._scan_side(preview_w, preview_h)
        if self._scan_crop is None:
            self.scan_view.setFixedSize(side, side)
            return
        pix = QPixmap.fromImage(self._scan_crop).scaled(
            side, side, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.scan_view.setText("")
        self.scan_view.setPixmap(pix)
        self.scan_view.setFixedSize(pix.size())
        self.scan_view.setStyleSheet(f"border: 1px solid {theme.THUMB_FRAME};")
        self.scan_view.setCursor(Qt.CursorShape.PointingHandCursor)
        self.scan_view.setToolTip(i18n.KO.SCAN_IMAGE_OPEN_TIP)
        self.scan_view.setAccessibleName(i18n.KO.SCAN_IMAGE_TITLE)

    def _refresh_hit_color(self, preview_w: int, preview_h: int) -> None:
        """Scan 사진 모드 아래 칸 — 고른 Defect 의 Color 사진(없으면 안내 · 오는 중이면 점)."""
        sheet = self._sheet
        if not sheet.hits or self.scan_view.isHidden():
            return
        side = self._scan_side(preview_w, preview_h)
        ok = sheet.color_ok.get(self._hit_i)
        if ok:
            pix = _io.load_thumb_qpixmap(sheet.hits[self._hit_i].path, side, kind="mid")
            self.scan_view.setText("")
            self.scan_view.setPixmap(pix)
            self.scan_view.setFixedSize(pix.size())
            self.scan_view.setStyleSheet(f"border: 1px solid {theme.THUMB_FRAME};")
            self.scan_view.setCursor(Qt.CursorShape.PointingHandCursor)
            self.scan_view.setToolTip(i18n.KO.IMAGE_INFO_OPEN_TIP)
            self.scan_view.setAccessibleName(i18n.KO.SCAN_DEFECT_COLOR_TITLE)
            return
        self.scan_view.setPixmap(QPixmap())
        self.scan_view.setText(i18n.KO.SCAN_DEFECT_COLOR_MISSING if ok is False
                               else self._scan_loading_text())
        self.scan_view.setFixedSize(side, side)
        self.scan_view.setCursor(Qt.CursorShape.ArrowCursor)
        self.scan_view.setToolTip("")
        self.scan_view.setStyleSheet(
            f"border: 1px dashed {theme.THUMB_FRAME};"
            f" border-radius: {theme.PROFILE.radius_sm}px; color: {theme.MUTE};")

    def _on_scan_view_clicked(self) -> None:
        """아래 칸 클릭 — Scan 사진 모드면 Color 사진을, 아니면 전체 Scan 을 크게."""
        sheet = self._sheet
        if sheet is None:
            self._open_full_scan()
            return
        if sheet.hits and sheet.color_ok.get(self._hit_i):
            from .zoom_window import FullscreenViewer
            sheets.run(FullscreenViewer(sheet.hits[self._hit_i].path, self), full_bleed=True)

    def _open_full_color(self) -> None:
        """사진(Color) 미리보기를 누르면 기존 전체화면 뷰어로 크게 본다(Scan 칸과 같은 뷰어)."""
        if self._path is None:
            return
        if self._sheet is not None and self._scan_full is not None:
            self._open_full_scan()           # 흑백 Scan 사진 — 고른 Defect 범위를 덧그려 연다
            return
        from .zoom_window import FullscreenViewer
        sheets.run(FullscreenViewer(self._path, self), full_bleed=True)

    def _open_full_scan(self) -> None:
        """전체 Scan 원본에 잘라낸 범위를 덧그려 기존 전체화면 뷰어로 연다."""
        if self._scan_full is None or self._scan_box is None:
            return
        from .zoom_window import FullscreenViewer
        viewer = FullscreenViewer(Path(self._scan_name), self,
                                  image=self._scan_full, overlay=self._scan_box,
                                  others=self._scan_others)
        sheets.run(viewer, full_bleed=True)

    def resizeEvent(self, event):           # noqa: N802
        super().resizeEvent(event)
        self._place_float()
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
            self.preview.setCursor(Qt.CursorShape.ArrowCursor)
            self.preview.setToolTip("")
            return
        self.preview.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preview.setToolTip(i18n.KO.IMAGE_INFO_OPEN_TIP)
        scan_mode = self._sheet is not None
        if self._full_loading():
            # Scan 원본을 읽는 동안 — 자리를 잡고 '불러오는 중'.  아래 칸과 세로를 나눠 쓸 크기로 잡아
            # 원본이 와도 칸이 뛰지 않게 한다.
            side = box
            if not self.scan_view.isHidden():
                host = self._preview_host
                extra = (self.scan_title.sizeHint().height()
                         + 2 * host.layout().spacing())
                room = host.height() - extra
                if room > 0:
                    side = max(_SCAN_MIN_SIDE, min(box, int(room * 0.5)))
            self.preview.setPixmap(QPixmap())
            self.preview.setText(self._scan_loading_text())
            self.preview.setFixedSize(side, side)
            self.preview.setAccessibleName(self._path.name)
            self._refresh_scan(side, side)
            self._sync_scan_dots()
            self._place_step_bar()
            return
        pix = (self._scan_preview_pix(box, box) if scan_mode
               else _io.load_thumb_qpixmap(self._path, box, kind="mid"))
        # Scan 이 있으면 세로를 둘이 나눠 쓴다 — Color 가 높이를 다 먹으면 Scan 이 120px 대로
        # 쪼그라든다(흑백이 작다는 지적).  Color 를 줄여 Scan 이 Color 와 비슷한 크기가 되게 한다.
        if not self.scan_view.isHidden() and pix.height() > 0:
            host = self._preview_host
            extra = (self.scan_title.sizeHint().height()
                     + 2 * host.layout().spacing())
            room = host.height() - extra
            if room > 0:
                color_h = min(pix.height(), int(room * 0.5))
                if color_h < pix.height():
                    w = max(1, int(pix.width() * color_h / pix.height()))
                    pix = (self._scan_preview_pix(w, color_h) if scan_mode
                           else _io.load_thumb_qpixmap(self._path, w, kind="mid"))
        self.preview.setPixmap(pix)
        # 프레임이 사진을 딱 감싸도록 라벨을 픽스맵 크기로 — 레터박스 여백 제거.
        self.preview.setFixedSize(pix.size())
        self.preview.setAccessibleName(self._path.name)
        self._refresh_scan(pix.width(), pix.height())
        self._sync_scan_dots()
        self._place_step_bar()

    # ------------------------------------------------------------------
    def _render(self) -> None:
        """계측 표를 **통째로 새로 만들어** 갈아끼운다.

        위젯만 걷어내는 방식은 두 가지로 어긋난다 — ``deleteLater`` 는 지연이라 이전
        안내가 새 내용 위에 겹쳐 보이고, ``setRowStretch`` 는 레이아웃에 남아 다음
        렌더의 행 간격을 벌린다.  ``QScrollArea.setWidget`` 이 이전 위젯을 지우므로
        새 호스트를 넘기는 편이 짧고 확실하다.
        """
        self._empty_open.set_empty(self._path is None)
        host = QWidget(self)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(theme.PROFILE.section_gap // 2)

        if self._path is None:
            col.addWidget(self._notice(host, i18n.KO.IMAGE_INFO_NO_FILE))
        else:
            label_w = self._label_col_w(host)
            for group in self._groups:
                col.addWidget(self._group_block(host, group, label_w))
            # ★ 계측을 하나도 못 읽었으면 복구 안내를 띄운다.  '파일' 덩이는 경로만
            #   있으면 항상 생기므로, 덩이 개수로 판정하면 이 안내가 영원히 안 뜬다.
            if self._sheet is not None and not self._sheet.hits:
                text = (i18n.KO.SCAN_DEFECT_NONE if self._sheet.status == scan_image.OK
                        else i18n.KO.SCAN_DEFECT_FAIL.get(self._sheet.status,
                                                          i18n.KO.SCAN_DEFECT_FAIL_OTHER))
                col.addWidget(self._notice(host, text))
            elif not single_info.has_measurements(self._groups):
                col.addWidget(self._notice(host, i18n.KO.IMAGE_INFO_EMPTY))
        col.addStretch(1)

        self._scroll.setWidget(host)
        self.copy_btn.setEnabled(bool(self._groups))
        self._refresh_preview()

    def _label_col_w(self, parent: QWidget) -> int:
        """라벨 열 폭 — 시트에 실린 **가장 긴 라벨**에 맞춘다(하한 ``_LABEL_COL_MIN``).

        ★ 상수로 못 박지 마라.  한 행이 곧 눈금을 그리는 ``QFrame`` 하나라 행마다
          grid 가 따로고, Qt 는 서로 다른 grid 의 열 폭을 맞춰 주지 않는다.  하한을
          넘는 라벨이 한 줄이라도 있으면 **그 행만** 값이 오른쪽으로 밀려 이 화면의
          약속("값이 한 기준선에서 시작")이 조용히 깨진다.  라벨 폭은 서체에 따라
          달라진다 — 동봉 글꼴을 못 읽어 폴백으로 떨어지면 같은 라벨이 두 배 가까이
          넓어져(실측 "recipe / zone" 83px → 182px) 하한을 훌쩍 넘는다.
        """
        parent.ensurePolished()     # 스타일시트의 서체가 적용된 뒤에 잰다
        fm = QFontMetrics(parent.font())
        widest = max((fm.horizontalAdvance(row.label)
                      for group in self._groups for row in group.rows
                      if row.label), default=0)
        return max(_LABEL_COL_MIN, widest)

    @staticmethod
    def _notice(parent: QWidget, text: str) -> QLabel:
        """안내 문구 — 줄바꿈해서 뷰포트 폭에 맞춘다.

        ★ 가로 폭 정책을 ``Ignored`` 로 낮춘다.  ``setWordWrap`` 라벨은 최소 폭을
          0 까지 내리지 않고 '적당한 가로세로 비'를 유지하려 드는데(실측 220px),
          그 최소치가 뷰포트보다 넓으면 **가로 스크롤이 생긴다** — 이 앱이 금지한
          것이다(CLAUDE.md 'UI 사용성 관습').  줄바꿈할 곳이 있는 문장이라 폭을
          낮춰도 잘리지 않고 아래로 늘어난다(``wordWrap`` 라벨은 heightForWidth 를
          이미 갖고 있어 높이가 따라온다).  ``_ElidingValue`` 가 값 라벨에 쓰는
          것과 같은 판단이고, 거기와 달리 생략이 아니라 줄바꿈으로 감당한다.
        """
        notice = QLabel(text, parent)
        notice.setWordWrap(True)
        notice.setProperty("role", "muted")
        notice.setAlignment(Qt.AlignmentFlag.AlignTop)
        policy = notice.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        notice.setSizePolicy(policy)
        return notice

    def _group_block(self, parent: QWidget, group, label_w: int) -> QWidget:
        """묶음 제목 + 밝은 상자 안 행들(현택님 2026-11-24 안 B '묶음마다 상자').

        상자 안 구분선은 예전처럼 **묶음(cluster)이 바뀌는 곳에만** 긋고, 마지막 행은 상자 테두리와
        겹치지 않게 긋지 않는다."""
        block = QWidget(parent)
        lay = QVBoxLayout(block)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        lay.addWidget(self._group_header(block, group.title, label_w))
        box = QFrame(block)
        box.setProperty("role", "infoBox")
        inner = QVBoxLayout(box)
        inner.setContentsMargins(_BOX_PAD_H, 2, _BOX_PAD_H, 2)
        inner.setSpacing(0)
        rows = group.rows
        for i, row in enumerate(rows):
            frame = self._row_widget(box, row, label_w)
            last = i + 1 == len(rows)
            # 다음 행이 같은 묶음이면 아래 눈금을 감춘다 — 묶음 사이에만 선.
            if last or (row.cluster and rows[i + 1].cluster == row.cluster):
                frame.setProperty("rule", "none")
            inner.addWidget(frame)
        lay.addWidget(box)
        return block

    @staticmethod
    def _group_header(parent: QWidget, title: str, label_w: int) -> QWidget:
        """묶음 제목 — 상자 바로 위.  '값' 열 머리는 두지 않는다(묶음마다 같은 글자가 되풀이됐다)."""
        name = QLabel(title, parent)
        name.setProperty("role", "infoGroupTitle")
        return name

    def _row_widget(self, parent: QWidget, row, label_w: int) -> QWidget:
        """한 행 — 아래 눈금이 항목을 가른다(면 없음, 제도 시트)."""
        frame = QFrame(parent)
        frame.setProperty("role", "row")
        grid = QGridLayout(frame)
        grid.setContentsMargins(0, theme.PROFILE.row_pad_v,
                                0, theme.PROFILE.row_pad_v)
        grid.setHorizontalSpacing(theme.PROFILE.card_pad)
        _shape_columns(grid, label_w)

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
        # ★ 값은 **왼쪽 정렬**이다.  오른쪽 끝에 붙이면 창이 넓어질수록 라벨과 값
        #   사이가 벌어져 항목마다 눈이 좌우로 왕복한다.
        value.setAlignment(Qt.AlignmentFlag.AlignLeft
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
        motion.flash_text(self._toast, i18n.KO.IMAGE_INFO_COPIED,
                          hold_ms=_TOAST_MS)
        self._place_float()
