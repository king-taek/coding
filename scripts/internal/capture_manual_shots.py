#!/usr/bin/env python3
"""사용 설명서용 화면 캡처 — **실제 앱**을 offscreen 으로 띄워 2배 해상도로 찍는다.

    python scripts/internal/capture_manual_shots.py

산출물은 ``dev/사용설명서_자료/`` 에 들어간다 — **개발 전용**이라 자동 업데이트에서
제외된다(`updater._UPDATE_SKIP_TOP` 가 ``dev/`` 를 통째로 건너뛴다).  사용자에게는
완성된 ``docs/사용설명서.pdf`` 만 내려간다:

- ``*.png`` — 화면 캡처(논리 크기의 2배 픽셀).  설명서 HTML 이 그대로 참조한다.
- ``shots.json`` — **위젯 실좌표 매니페스트**.  설명서의 강조 상자·화살표가 눈짐작이
  아니라 이 좌표에 앵커된다.  화면이 바뀌면 좌표도 함께 바뀌므로 주석이 어긋난 채
  남지 않는다.

왜 이렇게 하는가 (되돌리지 말 것):

- **`QT_SCALE_FACTOR=2` + `motion.snapshot`**.  ``snapshot`` 은 이미 대상 위젯의
  ``devicePixelRatioF`` 만큼 픽셀을 늘리고 그 값을 픽스맵에 실어 준다 — 배율만 올리면
  **레이아웃은 그대로**인 채 인쇄용 해상도가 나온다.  창을 크게 렌더하면 글자 크기와
  레이아웃의 비율이 달라져 실제 화면이 아니게 된다.
- **`w.grab()` 을 쓰지 않는다.**  페이지는 자기 배경을 칠하지 않아 grab 은 Qt 기본
  팔레트(밝은 회색)를 남긴다.  ``motion.snapshot`` 이 ``theme.BG`` 를 먼저 칠한다.
- **offscreen 에서 `motion.enabled()` 가 False** 라 모든 애니메이션이 즉시 끝난 상태로
  스냅된다 — 캡처가 시간에 의존하지 않는다(결정론).
- **시트(창 안 팝업)는 중첩 이벤트 루프 안에서 찍는다.**  ``sheets.run/choose`` 는
  ``QEventLoop`` 로 블로킹하므로, 호출 **전에** ``QTimer.singleShot(0, …)`` 으로 캡처를
  예약해 그 루프 안에서 찍고 시트를 닫는다.
- **다크(흑연) 캡처는 창을 새로 만든다.**  위젯이 생성 시점에 색을 f-string 으로 굽기
  때문에(theme.py 주석) QSS 재적용만으로는 색이 다 바뀌지 않는다.
- **예시 사진은 합성한다**(`demo_images.py`).  설명서는 사외로 나갈 수 있어 실제 장비
  사진을 넣지 않는다.  대신 화면에 적힌 거리와 **앞뒤가 맞는** 그림을 그린다 — 허용
  오차 안이면 두 장비가 같은 결함을 찍은 것처럼, 초과면 다른 결함처럼 보인다.
  만드는 위치는 임시 폴더이고 저장소에는 남지 않는다.

캡처가 하나라도 빠지면 **실패로 끝난다**(`_assert_complete`) — 옛 캡처 스크립트가
조용히 0장을 남긴 사고가 있었다(docs/화면_디자인_도면.md).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "dev" / "사용설명서_자료"
SAMPLE_ROOT = REPO / "docs" / "RDL4 LOT files(26년 05월, FDV)"

# Qt 를 불러오기 **전에** 환경을 정한다 — 뒤늦게 바꾸면 이미 만들어진 화면에 안 먹는다.
_TMP = Path(tempfile.mkdtemp(prefix="aoi-manual-"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "2"
os.environ["HOME"] = str(_TMP / "home")        # 캐시가 사용자 홈을 건드리지 않게
(_TMP / "home").mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(REPO))

from PyQt6.QtCore import QEvent, QPoint, QTimer              # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget            # noqa: E402

WIN_W, WIN_H = 1280, 860

# 캡처 대상 전체 목록 — 하나라도 빠지면 실패한다.
EXPECTED = [
    "01_셋업_빈화면", "02_셋업_입력완료", "03_셋업_구형엔진",
    "04_크롭_폴더카드", "05_크롭_판정기준배지", "06_크롭_액션바",
    "07_로딩_스캔중", "08_로딩_진행률",
    "09_시트_KLA확인", "10_시트_슬롯선택",
    "11_1단계_후보선별", "12_시트_선택모드",
    "13_2단계_매칭", "14_크롭_후보그리드",
    "15_검토_시트", "16_크롭_검토행", "17_시트_좌우비교",
    "18_결과", "19_시트_매칭결과검토",
    "20_시트_사진정보", "21_시트_업데이트",
    "22_셋업_흑연",
]
# ★ 여기 있는 것은 **설명서가 실제로 쓰는 그림만**이다.  그림을 새로 쓰려면 목록에
#   추가하고 **본문에서 참조까지** 해야 한다 — `make_manual_pdf.py` 가 참조되지 않는
#   캡처를 알려 주고, 본문이 가리키는 그림이 없으면 아예 인쇄를 막는다.


# ---------------------------------------------------------------------------
# 스테이징 자료 — 합성 결함 사진으로 스캔 트리를 만든다
# ---------------------------------------------------------------------------
# ★ 실제 장비 사진을 쓰지 않는다(설명서는 사외로 나갈 수 있다).  대신 `demo_images` 가
#   RDL 배선과 결함처럼 보이는 그림을 그린다.  단, **거리에 타당한** 그림이어야 한다:
#   허용 오차 안이면 두 장비가 *같은 결함*을 찍은 것처럼, 초과면 *다른 결함*처럼 보인다.
_SLOTS = ["W7548304XYG4", "W7548306XYA2"]
_TOL = 500.0

# 슬롯 하나의 사진 6장 — (기준↔검증 거리 µm, 차순위 후보 거리들 µm).
# 차순위는 **1위보다 멀어야** 한다(앱은 가까운 순서로 놓는다).
_PLAN = [
    (30, [145, 280]), (60, [225]), (620, [830, 1050]),
    (105, [195, 310]), (15, []), (1025, [1285]),
]
# 파일명에 들어갈 좌표 — Camtek INI 규약 ``{x}.{y}.c.{hash}.{n}.jpeg``
_REF_XY = ["255350.116718", "241902.128440", "198431.141277",
           "176220.098355", "152884.163019", "131077.087640"]
_VAL_XY = ["255348.116718", "241897.128444", "198425.141281",
           "176216.098361", "152878.163025", "131070.087633"]


def _scene(slot_idx: int, i: int, *, side: str) -> int:
    """이 사진이 그릴 '장면'(=결함) 번호.

    기준과 검증이 **같은 번호면 같은 결함**이다.  허용 오차를 넘는 쌍만 검증 쪽을 다른
    번호로 돌려 실제로 다른 결함이 보이게 한다 — 화면의 숫자와 그림이 어긋나지 않도록.
    """
    base = slot_idx * 100 + i
    if side == "ref" or _PLAN[i][0] <= _TOL:
        return base
    return 900 + base


def _build_stage_tree(dst: Path) -> tuple[Path, Path]:
    """기준(17호기)·검증(18호기) 두 루트를 만든다 → ``slot.scan`` 이 읽는 모양."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from demo_images import defect_image

    ref_root = dst / "기준_17호기"
    val_root = dst / "검증_18호기"
    for root, src_machine, side, coords in (
            (ref_root, "17호기", "ref", _REF_XY),
            (val_root, "18호기", "val", _VAL_XY)):
        for slot_idx, slot in enumerate(_SLOTS):
            out_dir = root / slot
            out_dir.mkdir(parents=True, exist_ok=True)
            # 좌표·계측 정보 파일은 실제 폴더의 것을 그대로 쓴다(사진이 아니다) —
            # '사진 정보 보기' 화면이 실제와 같은 항목을 보여 주게 하려는 것.
            src_dir = SAMPLE_ROOT / src_machine / slot
            for ini in src_dir.glob("*.ini"):
                shutil.copy2(ini, out_dir / ini.name)
            for i, xy in enumerate(coords):
                img = defect_image(_scene(slot_idx, i, side=side),
                                   machine=0 if side == "ref" else 1, px=1000)
                img.save(out_dir / f"{xy}.c.{1000000 + (i + 1) * 7919}.{i + 1}.jpeg",
                         quality=88)
    return ref_root, val_root


# ---------------------------------------------------------------------------
# 캡처 도구
# ---------------------------------------------------------------------------
def _sheet_rects(win, labels: tuple[str, ...]) -> dict:
    """맨 위 시트 안에서 **버튼 글자로** 위젯을 찾아 좌표를 만든다.

    시트마다 속성 이름을 외우지 않아도 되고, 글자가 바뀌면 좌표가 조용히 사라져
    설명서 주석이 엉뚱한 자리에 남지 않는다.
    """
    from PyQt6.QtWidgets import QAbstractButton

    stack = win._sheets._stack
    if not stack:
        return {}
    top = stack[-1]["root"]
    found: dict = {}
    for btn in top.findChildren(QAbstractButton):
        text = btn.text().strip()
        if text in labels and text not in found:
            found[text] = btn
    return found


def _pump(times: int = 20) -> None:
    """모듈 수준 이벤트 펌프 — `Shooter.pump` 와 같은 이유로 지연 삭제까지 비운다."""
    app = QApplication.instance()
    for _ in range(times):
        app.processEvents()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


class Shooter:
    """찍고 · 좌표를 적고 · 빠진 것을 잡아내는 한 곳."""

    def __init__(self, app: QApplication, out_dir: Path) -> None:
        self.app = app
        self.out = out_dir
        self.out.mkdir(parents=True, exist_ok=True)
        # 이전 실행 결과를 비운다 — 형식(png↔jpg)이 바뀌면 옛 파일이 남아 설명서가
        # 갱신되지 않은 그림을 계속 가리킬 수 있다.
        for old in list(self.out.glob("*.png")) + list(self.out.glob("*.jpg")):
            old.unlink()
        self.manifest: dict[str, dict] = {}

    def pump(self, times: int = 12) -> None:
        """이벤트를 흘리고 **삭제 예약된 위젯을 실제로 지운다**.

        ★ ``processEvents()`` 만 돌리면 Qt 는 ``DeferredDelete`` 를 처리하지 않는다
        (그 이벤트가 게시된 루프 레벨에서만 처리한다).  화면을 다시 그리는 페이지는
        옛 위젯을 ``deleteLater()`` 로 치우는데, 그것이 살아남아 **레이아웃 밖 기본
        위치에 함께 렌더**됐다 — 실제 앱에는 없는 유령 글자가 캡처에만 찍혔다
        (1단계 슬롯 머리글이 겹쳐 보이던 원인).
        """
        for _ in range(times):
            self.app.processEvents()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def _rects(self, host: QWidget, spec: dict[str, QWidget]) -> dict[str, list[int]]:
        """위젯 → host 기준 논리좌표 사각형.  설명서 주석이 여기에 앵커된다."""
        out: dict[str, list[int]] = {}
        for key, w in spec.items():
            if w is None:
                continue
            try:
                # 빈 라벨은 폭이 몇 px 뿐이라 강조 상자를 그릴 수 없다 — 아예 적지 않는다.
                if not w.isVisible() or w.width() < 8 or w.height() < 8:
                    continue
                pos = w.mapTo(host, QPoint(0, 0))
                out[key] = [pos.x(), pos.y(), w.width(), w.height()]
            except RuntimeError:
                continue
        return out

    def shoot(self, name: str, widget: QWidget, *, rects: dict | None = None) -> Path:
        from aoi_verification.app.ui import motion, theme

        # 게으른 썸네일은 처음 그려질 때 디코드를 예약한다 — 한 번 그려 깨운 뒤 다시 찍는다.
        motion.snapshot(widget)
        self.pump(20)
        pm = motion.snapshot(widget)
        path = self.out / f"{name}.png"
        if not pm.save(str(path)):
            raise RuntimeError(f"캡처 저장 실패: {path}")
        self.manifest[name] = {
            "file": path.name,
            "w": widget.width(), "h": widget.height(),
            "dpr": pm.devicePixelRatio(),
            "mode": theme.COLOR_MODE, "bg": theme.BG,
            "scrim": list(theme.SCRIM_RGBA),
            "rects": self._rects(widget, rects or {}),
        }
        print(f"  · {name}  {pm.width()}×{pm.height()}")
        return path

    def crop(self, name: str, src: str, rect: list[int], *, pad: int = 10) -> None:
        """찍어 둔 2배 PNG 에서 잘라낸 클로즈업 — 원본을 다시 그리지 않는다."""
        from PIL import Image

        info = self.manifest[src]
        dpr = int(info["dpr"])
        x, y, w, h = rect
        img = Image.open(self.out / info["file"])
        box = (max(0, (x - pad) * dpr), max(0, (y - pad) * dpr),
               min(img.width, (x + w + pad) * dpr),
               min(img.height, (y + h + pad) * dpr))
        img.crop(box).save(self.out / f"{name}.png")
        # 잘라낸 그림 안에 들어오는 좌표는 **크롭 기준으로 옮겨서** 함께 남긴다 —
        # 그래야 확대 그림의 강조 상자도 눈짐작 없이 같은 방식으로 그릴 수 있다.
        ox, oy = x - pad, y - pad
        local = {}
        for key, (rx, ry, rw, rh) in info["rects"].items():
            if rx >= ox and ry >= oy and rx + rw <= ox + w + pad * 2 and ry + rh <= oy + h + pad * 2:
                local[key] = [rx - ox, ry - oy, rw, rh]
        self.manifest[name] = {
            "file": f"{name}.png", "w": w + pad * 2, "h": h + pad * 2,
            "dpr": dpr, "rects": local, "crop_of": src,
        }
        print(f"  · {name}  (크롭: {src}, 좌표 {len(local)}개)")

    def sheet(self, name: str, win, open_fn, *, rects_fn=None) -> None:
        """시트를 띄우는 **블로킹 호출** 안에서 찍는다.

        ``sheets.run/choose`` 는 중첩 ``QEventLoop`` 로 동기 의미를 유지한다.  그래서
        호출 전에 캡처를 예약해 두고, 루프가 돌기 시작하면 찍은 뒤 맨 위 시트를 닫는다.
        """
        def grab() -> None:
            self.pump(20)
            rects = rects_fn() if rects_fn else {}
            self.shoot(name, win, rects=rects)
            stack = win._sheets._stack
            if stack:
                stack[-1]["widget"].close()
            self.pump(10)

        QTimer.singleShot(0, grab)
        open_fn()
        self.pump(10)

    def optimize(self) -> None:
        """형식을 **재서 고른다** — 같은 그림이면 더 작은 쪽.

        ★ 한때 256색 팔레트(MAXCOVERAGE)로 무조건 줄였다.  용량은 줄었지만 **바탕색이
        바뀌었다**: 사진이 많은 1단계 화면에서 벨럼 ``#ECE9E2`` 가 (226,238,222)
        초록빛으로 틀어졌다.  이 설명서는 화면의 색 자체를 가르치는 문서라 색이 틀리면
        내용이 틀린 것이다.  그래서 방식을 믿지 않고 결과를 잰다.

        실측(2560×1720):

        | 화면 | PNG | JPEG q95 | 평균오차 |
        |---|---|---|---|
        | 셋업(사진 없음) | **0.18 MB** | 0.39 MB | 0.62 |
        | 검토(사진 많음) | 1.14 MB | **0.64 MB** | 0.36 |
        | 좌우 비교(사진 큼) | 2.79 MB | **0.95 MB** | 0.67 |

        면이 넓은 UI 화면은 PNG 가, 사진이 많은 화면은 JPEG 가 작다.  그래서 둘 다
        만들어 **평탄한 바탕이 그대로이고 전체 오차가 작을 때만** 작은 쪽을 쓴다.
        docs/ 는 자동 업데이트로 사용자 PC 에 내려가므로 용량도 그냥 둘 수 없다.
        """
        from PIL import Image

        for info in self.manifest.values():
            png = self.out / info["file"]
            orig = Image.open(png).convert("RGB")
            orig.save(png, optimize=True, compress_level=9)
            jpg = png.with_suffix(".jpg")
            orig.save(jpg, "JPEG", quality=95, subsampling=0, optimize=True)
            reloaded = Image.open(jpg).convert("RGB")
            if (jpg.stat().st_size < png.stat().st_size
                    and self._same_enough(orig, reloaded)):
                png.unlink()
                info["file"] = jpg.name
            else:
                jpg.unlink()

    @staticmethod
    def _same_enough(a, b) -> bool:
        """두 그림이 눈으로 같은가 — 평탄한 바탕과 전체 오차로 판정.

        바탕 표본은 채널당 1 까지만 봐준다(부호화 반올림).  잡으려는 사고인 팔레트
        드리프트는 10 이상 틀어졌으므로 이 여유로도 확실히 걸린다.
        """
        from PIL import ImageChops, ImageStat

        for pt in ((6, 6), (a.width - 6, 6), (6, a.height - 6)):
            if any(abs(x - y) > 1 for x, y in zip(a.getpixel(pt), b.getpixel(pt))):
                return False
        return max(ImageStat.Stat(ImageChops.difference(a, b)).mean) < 1.0

    def verify_colors(self) -> None:
        """저장된 PNG 의 바탕색이 **찍을 때의 테마 색과 정확히 같은지** 확인한다.

        팔레트 양자화가 벨럼 바탕을 초록빛으로 바꿔 놓았던 적이 있다 — 색을 가르치는
        문서에서 색이 틀리면 내용이 틀린 것이므로 저장 결과를 직접 잰다.

        시트·로딩 화면은 **스크림**이 덮여 있어 모서리가 어두워지는 것이 정상이다.
        그래서 '순수 바탕' 과 '스크림 아래 바탕' 두 값을 모두 인정하고, 매니페스트에
        어느 쪽이었는지 적어 둔다(설명서가 스크림 유무를 그대로 쓴다).
        """
        from PIL import Image

        bad = []
        for name, info in self.manifest.items():
            if info.get("crop_of"):
                continue
            bg = tuple(int(info["bg"].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
            sr, sg, sb, sa = info["scrim"]
            a = sa / 255.0
            dim = tuple(round(c * (1 - a) + s * a)
                        for c, s in zip(bg, (sr, sg, sb)))
            got = Image.open(self.out / info["file"]).convert("RGB").getpixel((6, 6))
            # 채널당 1 은 Qt 의 알파 합성 반올림과 JPEG 부호화에서 나온다.  잡으려는
            # 사고(팔레트 드리프트)는 10 이상 틀어졌으므로 이 여유로 확실히 걸린다.
            near = lambda a, b: all(abs(x - y) <= 1 for x, y in zip(a, b))  # noqa: E731
            if near(got, bg):
                info["scrimmed"] = False
            elif near(got, dim):
                info["scrimmed"] = True
            else:
                bad.append(f"{name} {got} ∉ {{{bg}, {dim}}}")
        if bad:
            raise SystemExit("바탕색이 테마와 다릅니다: " + ", ".join(bad))

    def finish(self) -> None:
        (self.out / "shots.json").write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        self._assert_complete()

    def _assert_complete(self) -> None:
        """빠진 캡처와 **빈 캡처**를 둘 다 잡는다.

        파일 크기로 판정하지 않는다 — 무광 팔레트 PNG 는 작은 크롭이 5 KB 밖에 안 돼
        멀쩡한 그림이 '비었다' 로 걸린다.  실제로 볼 것은 **색이 몇 가지나 있는가**다
        (한 가지 색이면 그리다 만 것).
        """
        from PIL import Image

        missing = [n for n in EXPECTED if n not in self.manifest]
        if missing:
            raise SystemExit(f"캡처 누락: {', '.join(missing)}")
        blank = []
        for name in EXPECTED:
            img = Image.open(self.out / self.manifest[name]["file"]).convert("RGB")
            # getcolors 는 한도를 넘으면 None 을 준다 — '색이 아주 많다'는 뜻이므로
            # 빈 그림일 수 없다.  None 을 빈 목록으로 취급하면 정상 캡처가 걸린다.
            colors = img.getcolors(4096)
            if min(img.size) < 40 or (colors is not None and len(colors) < 8):
                blank.append(name)
        if blank:
            raise SystemExit(f"캡처가 비어 있습니다: {', '.join(blank)}")
        total = sum((self.out / i["file"]).stat().st_size
                    for i in self.manifest.values())
        print(f"\n캡처 {len(self.manifest)}장 · 합계 {total / 1e6:.1f} MB → {self.out}")


# ---------------------------------------------------------------------------
# 화면별 스테이징
# ---------------------------------------------------------------------------
def _make_window():
    """실제 MainWindow — 시작 시 나가는 네트워크·백그라운드 작업만 막는다."""
    from aoi_verification.app.ui import main_window as mw
    from aoi_verification.app.ui.pages.match_page import MatchPage

    for attr in ("_check_for_update_async", "_maybe_offer_openvino",
                 "_warmup_accel_async", "_start_backend_import_async",
                 "_prune_old_cache_async"):
        if hasattr(mw.MainWindow, attr):
            setattr(mw.MainWindow, attr, lambda self: None)
    # 점수 사전계산은 실제 매칭 엔진(cv2)을 부른다 — 화면만 필요하므로 끈다.
    MatchPage._start_precompute = lambda self: None

    win = mw.MainWindow()
    win.resize(WIN_W, WIN_H)
    win.show()
    _pump(20)
    win._on_backend_loaded()          # 나머지 4개 페이지 생성
    _pump(20)
    return win


def _scan(ref_root: Path, val_root: Path):
    from aoi_verification.app.models import slot as slot_mod
    return slot_mod.scan(ref_root, val_root)


def _fill_setup(win, ref_root: Path, val_root: Path) -> None:
    sp = win._setup_page
    sp.ref_path_edit.setText(str(ref_root))
    sp.val_path_edit.setText(str(val_root))
    sp.ref_machine_edit.setText("17호기")
    sp.val_machine_edit.setText("18호기")
    _pump(30)


# 화면에 보이는 경로 문자열만 실제 사용 환경처럼 바꾼다.  검증(빨간 테두리·시작 버튼)은
# **진짜 폴더로 이미 통과한 상태**를 그대로 쓴다 — 리눅스 임시 경로가 그대로 찍히면
# 설명서가 Windows 사용자에게 없는 경로를 가르치게 된다.
_SHOWN_REF = r"D:\검사자료\2026-05\17호기"
_SHOWN_VAL = r"D:\검사자료\2026-05\18호기"


def _cosmetic_paths(win) -> None:
    sp = win._setup_page
    for edit, text in ((sp.ref_path_edit, _SHOWN_REF), (sp.val_path_edit, _SHOWN_VAL)):
        edit.blockSignals(True)         # textChanged → 재검증을 막는다
        edit.setText(text)
        edit.blockSignals(False)
    timer = getattr(sp, "_validate_timer", None)
    if timer is not None:
        timer.stop()
    # 캡처 중 다른 경로(허용 오차 변경 등)로 재검증이 들어와도 화면 상태를 흔들지 않게.
    sp._validate = lambda: True
    _pump(10)


def _setup_rects(win) -> dict:
    sp = win._setup_page
    cards = getattr(sp, "_setting_cards", (None, None))
    return {
        "기준장비카드": getattr(sp, "ref_group", None),
        "검증장비카드": getattr(sp, "val_group", None),
        "기준폴더입력": sp.ref_path_edit,
        "검증폴더입력": sp.val_path_edit,
        "기준호기입력": sp.ref_machine_edit,
        "검증호기입력": sp.val_machine_edit,
        "판정기준배지": getattr(sp, "_mode_badge_card", None),
        "다크모드스위치": getattr(sp, "_dark_switch", None),
        "실행옵션카드": cards[0] if cards else None,
        "매칭설정카드": cards[1] if len(cards) > 1 else None,
        "자동화수준": getattr(sp, "auto_group", None),
        "진행범위": getattr(sp, "scope_group", None),
        "허용오차입력": getattr(sp, "coord_tol_spin", None),
        "허용오차줄": getattr(sp, "_tol_row", None),
        "구형엔진스위치": getattr(sp, "legacy_switch", None),
        "구형모드선택": getattr(sp, "legacy_group", None),
        "임계치줄": getattr(sp, "_threshold_row", None),
        "사용방법": getattr(sp, "_howto_section", None),
        "업데이트확인": getattr(sp, "update_btn", None),
        "사진정보보기": getattr(sp, "image_info_btn", None),
        "검증시작": getattr(sp, "start_btn", None),
    }


def _stage_select(win, sr) -> dict:
    """1단계 — 진행 중인 모습(왼쪽 남은 사진 · 오른쪽 검증 대상 · 제외 몇 장)."""
    from aoi_verification.app import i18n

    page = win._select_page
    items, targets, excluded = [], {}, {}
    for name in sr.common_slot_names:
        imgs = list(sr.slots[name].ref_images)
        targets[name] = imgs[:2]
        excluded[name] = imgs[2:3]
        items.extend(imgs[3:])
    page.load_state(items, targets=targets, excluded=excluded,
                    phase_label=i18n.KO.STAGE1_TITLE)
    win._stack.setCurrentWidget(page)
    _pump(30)
    return {
        "왼쪽패널": getattr(page, "left_panel", None),
        "중앙사진": getattr(page, "center_img", None),
        "오른쪽패널": getattr(page, "right_panel", None),
        "검증버튼": getattr(page, "btn_verify", None),
        "제외버튼": getattr(page, "btn_exclude", None),
        "되돌리기버튼": getattr(page, "btn_undo", None),
        "선택종료": getattr(page, "btn_end_selection", None),
        "제외사진보기": getattr(page, "btn_view_excluded", None),
        "진행표시": getattr(page, "progress_label", None),
        "슬롯별장수": getattr(page, "lot_counts_label", None),
        "사진크기": getattr(page, "size_slider", None),
    }


def _stage_match(win, sr) -> dict:
    """2단계 — 기준 사진 한 장과 검증 후보 격자."""
    from aoi_verification.app import i18n
    from aoi_verification.app.workers.matcher import Candidate

    page = win._match_page
    slot = sr.common_slot_names[0]
    refs = list(sr.slots[slot].ref_images)
    vals = list(sr.slots[slot].val_images)
    page.load_state(refs, {slot: vals}, 0.55,
                    phase_label=i18n.KO.STAGE2_TITLE_COORD)
    page._show_center(refs[0])
    # 좌표 매칭에서 실제로 나오는 모양 — 가까운 후보 하나와 점점 먼 후보들.
    scores = [0.96, 0.62, 0.41, 0.28, 0.19]
    page._populate_right([Candidate(item=v, score=s)
                          for v, s in zip(vals, scores)])
    win._stack.setCurrentWidget(page)
    _pump(30)
    return {
        "기준사진": getattr(page, "center_img", None),
        "후보격자": getattr(page, "_right_host", None),
        "매칭없음": getattr(page, "no_match_btn", None),
        "되돌리기": getattr(page, "undo_btn", None),
        "사진크기": getattr(page, "size_slider", None),
        "슬롯라벨": getattr(page, "slot_label", None),
        "진행표시": getattr(page, "progress_label", None),
    }


def _review_data(sr):
    """검토 화면용 매치 묶음 — 일치·허용 초과·매치 없음이 모두 보이게."""
    from aoi_verification.app.models.result import MatchResult

    matches, cands = [], {}

    # 거리(µm) → score.  0 이상 = 허용 내(dist=(1-score)*tol), 음수 = 초과(dist=-score*tol).
    def score_of(dist: float) -> float:
        return 1.0 - dist / _TOL if dist <= _TOL else -(dist / _TOL)

    idx = 0
    for name in sr.common_slot_names:
        s = sr.slots[name]
        for ref, val in zip(s.ref_images, s.val_images):
            if idx >= len(_PLAN):
                break
            dist, runner_dists = _PLAN[idx]
            matches.append(MatchResult(slot=name, ref_path=ref.path,
                                       val_path=val.path, score=score_of(dist)))
            pool = [v for v in s.val_images if v.path != val.path]
            cands[(name, ref.path.name)] = (
                [(val, score_of(dist))]
                + [(v, score_of(d)) for v, d in zip(pool, runner_dists)])
            idx += 1
    return matches, cands


def _stage_review(win, sr) -> dict:
    page = win._match_review_page
    matches, cands = _review_data(sr)
    page.load_state(matches, candidates_by_ref=cands, coord_mode=True,
                    tolerance=500.0, coord_failed_count=1)
    win._stack.setCurrentWidget(page)
    _pump(40)
    # 한 줄은 '매치 없음' 으로 표시해 세 가지 판정이 한 화면에 다 보이게 한다.
    rows = list(getattr(page, "_rows", []))
    normal = [r for r in rows if getattr(r, "match", None) is not None
              and r.match.score >= 0]
    if len(normal) >= 2:
        page._on_toggle(normal[-1].match)
    _pump(20)
    rows = list(getattr(page, "_rows", []))
    out = {
        "탤리": getattr(page, "_tally_label", None),
        "사진크기": getattr(page, "size_slider", None),
        "검토완료": getattr(page, "btn_done", None),
        "거리컬럼": getattr(page, "_hdr_metric", None),
    }
    over = next((r for r in rows if getattr(r, "match", None) is not None
                 and r.match.score < 0), None)
    ok = next((r for r in rows if getattr(r, "match", None) is not None
               and r.match.score >= 0), None)
    if over is not None:
        out["허용초과행"] = over
        out["크게보기"] = getattr(over, "btn_view", None)
        out["판정칩"] = getattr(over, "_chip", None)
        # 행 안쪽 부품 — 확대 그림(크롭)의 강조 상자가 이 좌표를 쓴다.
        out["기준썸네일"] = getattr(over, "_ref_img", None)
        out["확정된짝"] = getattr(over, "_val_host", None)
        out["거리수치"] = getattr(over, "_metric_label", None)
        runners = getattr(over, "_runner_host", None)
        first_line = getattr(over, "_first_line_host", None)
        if first_line is not None:
            out["차순위후보"] = first_line
        elif runners is not None:
            out["차순위후보"] = runners
    if ok is not None:
        out["정상행"] = ok
        out["매치없음토글"] = getattr(ok, "btn_toggle", None)
    return out


def _stage_result(win, sr) -> dict:
    from aoi_verification.app.models.result import FinalResult, MissEntry

    matches, _ = _review_data(sr)
    slot0 = sr.common_slot_names[0]
    misses = [MissEntry(slot=slot0, side="ref",
                        path=sr.slots[slot0].ref_images[-1].path,
                        note="미매칭 (사용자 검토)")]
    res = FinalResult(mode="cross", ref_machine="17호기", val_machine="18호기",
                      matches=matches, unmatched_refs=misses,
                      slot_only_ref=["W7548311XYB1"])
    page = win._result_page
    # val_pool 을 함께 넘겨야 `[매치 실패 사진 검토]` 가 실제 사용 때처럼 활성화된다
    # (없으면 비활성 상태로 찍혀 설명서가 잘못된 화면을 가르친다).
    val_pool = {n: list(sr.slots[n].val_images) for n in sr.common_slot_names}
    page.show_result(res, target_path=Path(r"C:\AOI_Verify\결과\AOI 18호기 검증 (17호기 기준).xlsx"),
                     val_pool=val_pool, coord_mode=True, tolerance=500.0)
    win._stack.setCurrentWidget(page)
    _pump(30)
    return {
        "요약카드": getattr(page, "_summary_card", None),
        "원본화질": getattr(page, "original_quality_chk", None),
        "전체양식": getattr(page, "full_template_chk", None),
        "새검증": getattr(page, "new_btn", None),
        "매칭결과검토": getattr(page, "review_btn", None),
        "실패검토": getattr(page, "review_unmatched_btn", None),
        "엑셀저장": getattr(page, "export_btn", None),
    }


# ---------------------------------------------------------------------------
def main() -> int:
    if not SAMPLE_ROOT.exists():
        raise SystemExit(f"예시 자료가 없습니다: {SAMPLE_ROOT}")

    app = QApplication(sys.argv)
    from aoi_verification.app import i18n
    from aoi_verification.app.ui import theme

    theme.load_app_fonts()
    theme.apply_to_app(app)

    ref_root, val_root = _build_stage_tree(_TMP / "자료")
    sr = _scan(ref_root, val_root)
    print(f"스테이징 슬롯: {', '.join(sr.common_slot_names)}")

    sh = Shooter(app, OUT_DIR)
    win = _make_window()

    # --- 셋업 ---------------------------------------------------------------
    print("셋업 화면")
    sh.shoot("01_셋업_빈화면", win, rects=_setup_rects(win))
    _fill_setup(win, ref_root, val_root)
    _cosmetic_paths(win)
    sh.shoot("02_셋업_입력완료", win, rects=_setup_rects(win))

    sp = win._setup_page
    sp.legacy_switch.set_on(True, emit=True)
    sp._howto_section.set_expanded(True, animate=False)
    sh.pump(30)
    sh.shoot("03_셋업_구형엔진", win, rects=_setup_rects(win))
    sp.legacy_switch.set_on(False, emit=True)
    sp._howto_section.set_expanded(False, animate=False)
    sh.pump(20)

    m = sh.manifest["02_셋업_입력완료"]["rects"]
    sh.crop("04_크롭_폴더카드", "02_셋업_입력완료", m["기준장비카드"])
    sh.crop("05_크롭_판정기준배지", "02_셋업_입력완료", m["판정기준배지"], pad=16)
    if "검증시작" in m:
        bar = m["검증시작"]
        sh.crop("06_크롭_액션바", "02_셋업_입력완료",
                [8, bar[1] - 12, WIN_W - 16, bar[3] + 24], pad=4)

    # --- 로딩 ---------------------------------------------------------------
    print("로딩 오버레이")
    win._loading.show_overlay(i18n.KO.LOAD_SCAN)
    sh.pump(20)
    sh.shoot("07_로딩_스캔중", win,
             rects={"로딩패널": win._loading.findChild(QWidget, "")})
    win._loading.set_progress(9, 24, i18n.KO.LOAD_SCAN_FMT.format(done=9, total=24))
    sh.pump(20)
    sh.shoot("08_로딩_진행률", win)
    win._loading._finish_hide()
    sh.pump(20)

    # --- 시트 ---------------------------------------------------------------
    print("창 안 시트")
    win._stack.setCurrentWidget(win._setup_page)
    sh.pump(10)
    sh.sheet("09_시트_KLA확인", win, win._ask_kla_side,
             rects_fn=lambda: _sheet_rects(win, (i18n.KO.KLA_SIDE_REF, i18n.KO.KLA_SIDE_VAL,
                                                 i18n.KO.KLA_SIDE_BOTH, i18n.KO.KLA_SIDE_NONE)))

    from aoi_verification.app.ui.widgets.slot_select_dialog import SlotSelectDialog
    names = sr.common_slot_names + ["W7548311XYB1", "W7548315XYC3",
                                    "W7548318XYD7", "W7548322XYE1"]
    dlg = SlotSelectDialog(names, preselected=set(names))
    # ★ offscreen 가상 화면은 **400×400** 이다.  이 다이얼로그는 크기를 화면에 맞춰
    #   줄이므로(`min(g.width()*…)`) 그대로 찍으면 실제 모니터에서보다 훨씬 작게 나온다.
    #   시트 배치는 `minimumWidth/Height` 를 존중하므로 실제 크기를 직접 준다.
    dlg.setMinimumSize(736, 620)
    sh.sheet("10_시트_슬롯선택", win, lambda: win._sheets.run(dlg),
             rects_fn=lambda: _sheet_rects(win, (i18n.KO.SLOT_SELECT_ALL,
                                                 i18n.KO.SLOT_SELECT_NONE,
                                                 i18n.KO.BTN_OK, i18n.KO.BTN_CANCEL)))

    # --- 1단계 --------------------------------------------------------------
    print("1단계 후보 선별")
    rects = _stage_select(win, sr)
    sh.shoot("11_1단계_후보선별", win, rects=rects)

    from aoi_verification.app.ui.widgets.bulk_select_dialog import BulkSelectDialog
    slot0 = sr.common_slot_names[0]
    groups = {n: list(sr.slots[n].ref_images) for n in sr.common_slot_names}
    bulk = BulkSelectDialog(i18n.KO.PANEL_LEFT_CANDIDATES, groups,
                            [("batch_verify", i18n.KO.BTN_BATCH_VERIFY, "primary"),
                             ("batch_exclude", i18n.KO.BTN_BATCH_EXCLUDE, "danger")],
                            parent=win)
    sh.sheet("12_시트_선택모드", win,
             lambda: win._sheets.run(bulk, full_bleed=True))

    # --- 2단계 --------------------------------------------------------------
    print("2단계 매칭")
    rects = _stage_match(win, sr)
    sh.shoot("13_2단계_매칭", win, rects=rects)
    if "후보격자" in sh.manifest["13_2단계_매칭"]["rects"]:
        sh.crop("14_크롭_후보그리드", "13_2단계_매칭",
                sh.manifest["13_2단계_매칭"]["rects"]["후보격자"])

    # --- 검토 ---------------------------------------------------------------
    print("검토 화면")
    rects = _stage_review(win, sr)
    sh.shoot("15_검토_시트", win, rects=rects)
    r = sh.manifest["15_검토_시트"]["rects"]
    # 허용 초과 행 — 판정 칩·거리·차순위 후보가 한 컷에 다 들어간다.
    sh.crop("16_크롭_검토행", "15_검토_시트", r["허용초과행"], pad=6)

    from aoi_verification.app.ui.widgets.side_by_side_viewer import SideBySideViewer
    s0 = sr.slots[sr.common_slot_names[0]]
    viewer = SideBySideViewer(s0.ref_images[0].path,
                              [(v, f"{120 + i * 90} µm") for i, v in enumerate(s0.val_images[:3])],
                              parent=win)
    sh.sheet("17_시트_좌우비교", win,
             lambda: win._sheets.run(viewer, full_bleed=True))

    # --- 결과 ---------------------------------------------------------------
    print("결과 화면")
    rects = _stage_result(win, sr)
    sh.shoot("18_결과", win, rects=rects)

    from aoi_verification.app.ui.widgets.matches_review import MatchesReviewDialog
    matches, _ = _review_data(sr)
    mr = MatchesReviewDialog(matches, coord_mode=True, tolerance=500.0, parent=win)
    sh.sheet("19_시트_매칭결과검토", win,
             lambda: win._sheets.run(mr, full_bleed=True))

    from aoi_verification.app.ui.widgets.image_info_dialog import ImageInfoDialog
    info_dlg = ImageInfoDialog(parent=win, image_path=str(s0.ref_images[0].path))
    sh.sheet("20_시트_사진정보", win, lambda: win._sheets.run(info_dlg))

    sh.sheet("21_시트_업데이트", win,
             lambda: win._on_update_found({"version": "2026.08.05", "branch": "main"}))

    # --- 흑연(다크) — 위젯을 새로 만들어야 색이 전부 바뀐다 -------------------
    print("흑연(다크) 모드")
    win.hide()
    win.deleteLater()
    sh.pump(20)
    theme.set_color_mode("dark")
    theme.apply_to_app(app)
    win2 = _make_window()
    _fill_setup(win2, ref_root, val_root)
    _cosmetic_paths(win2)
    sh.shoot("22_셋업_흑연", win2, rects=_setup_rects(win2))

    sh.optimize()
    sh.verify_colors()
    sh.finish()
    shutil.rmtree(_TMP, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
