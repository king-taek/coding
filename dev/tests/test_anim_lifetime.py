"""Qt 애니메이션 수명 — **삭제된 객체의 핸들을 들고 있으면 프로세스가 죽는다.**

실사용에서 강제종료 2건이 났고 원인은 하나였다:

    QVariantAnimation.start(DeletionPolicy.DeleteWhenStopped)

로 시작한 애니메이션은 자연 종료 시 C++ 객체가 삭제되는데, 파이썬 쪽 ``self._anim`` 은
여전히 그 껍데기를 들고 있다.  **두 번째** 호출에서 ``anim.stop()`` 이

    RuntimeError: wrapped C/C++ object of type QVariantAnimation has been deleted

를 내고, PyQt6 는 슬롯 안의 미처리 예외를 ``qFatal()`` 로 처리하므로 **앱이 그대로
죽는다**(파이썬 traceback 만 남고 종료).  재현된 두 경로:

- ``ToggleSwitch`` 두 번째 토글        → 구형 모드 on/off 시 강제종료
- ``LoadingOverlay`` 두 번째 show_overlay → 실패목록 두 번째 클릭 시 강제종료

★ 이 테스트는 **모션을 켜야** 재현된다.  ``motion.enabled()`` 는 offscreen 에서 False 를
돌려주고, False 면 애니메이션 경로를 아예 타지 않는다 — 모션을 끄고 짠 테스트는 통과하면서
버그를 놓친다.
"""

from __future__ import annotations

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QElapsedTimer                        # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget             # noqa: E402

from aoi_verification.app.ui import motion                    # noqa: E402
from aoi_verification.app.ui.widgets.loading_overlay import (  # noqa: E402
    LoadingOverlay)
from aoi_verification.app.ui.widgets.switch_row import (       # noqa: E402
    SwitchRow, ToggleSwitch)

_UI_DIR = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui")


def _code_lines(path: Path) -> list[tuple[int, str]]:
    """주석과 **문자열/독스트링을 지운** 코드 줄만 (번호, 내용) 으로 돌려준다.

    ★ 이 헬퍼가 필요한 이유: 이 가드들은 '이 패턴을 쓰지 마라'를 검사하는데, 그 금지를
    **설명하는 독스트링**이 같은 단어를 담는다(실제로 `sheet_host.py` 의 설계 규칙 주석이
    걸렸다).  설명을 위반으로 세면 옳은 문서를 지우게 되므로, 코드만 본다."""
    import io
    import tokenize
    src = path.read_text(encoding="utf-8")
    kept: dict[int, list[str]] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING,
                            tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                            tokenize.DEDENT, tokenize.ENDMARKER):
                continue
            kept.setdefault(tok.start[0], []).append(tok.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # 토큰화가 안 되면 원문으로 폴백한다(가드를 조용히 끄지 않는다).
        return [(i + 1, ln) for i, ln in enumerate(src.splitlines())
                if not ln.strip().startswith("#")]
    return [(no, " ".join(parts)) for no, parts in sorted(kept.items())]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def motion_on(monkeypatch):
    """모션을 강제로 켠다 — 이게 없으면 애니메이션 경로를 타지 않아 재현이 안 된다."""
    monkeypatch.setattr(motion, "enabled", lambda: True)
    yield


def _spin(qapp, ms: int) -> None:
    """실제 시간을 ms 만큼 흘리며 이벤트 루프를 돈다(애니메이션이 끝나게)."""
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        qapp.processEvents()


# ── 재현 1: 구형 모드 스위치를 두 번 토글 ────────────────────────────────
def test_toggle_switch_survives_second_toggle(qapp, motion_on):
    sw = ToggleSwitch(False)
    sw.resize(48, 28)
    try:
        sw._toggle()                       # 1회 — 애니메이션 시작
        _spin(qapp, 400)                   # dur(140) 을 충분히 넘겨 자연 종료
        sw._toggle()                       # 2회 — 여기서 죽었다
        _spin(qapp, 200)
        assert sw.is_on() is False          # off → on → off
    finally:
        sw.deleteLater()


def test_switch_row_survives_repeated_toggles(qapp, motion_on):
    """구형 모드 카드가 실제로 쓰는 형태(SwitchRow)로도 확인."""
    row = SwitchRow("구형(유사도) 모드", description="설명", checked=False)
    seen: list[bool] = []
    row.toggled.connect(seen.append)
    try:
        for _ in range(4):
            row.switch._toggle()
            _spin(qapp, 300)
        assert seen == [True, False, True, False]
    finally:
        row.deleteLater()


# ── 재현 2: 로딩 오버레이를 두 번 표시 ──────────────────────────────────
def test_loading_overlay_survives_second_show(qapp, motion_on):
    host = QWidget()
    host.resize(900, 600)
    host.show()
    ov = LoadingOverlay(host)
    try:
        ov.show_overlay("점수 계산 중")
        # BAR_STAGGER_MS(210) + BAR_SLIDE_MS(160) 을 넘겨 바 애니메이션이 자연 종료되게.
        _spin(qapp, 700)
        ov.show_overlay("점수 계산 중")     # 실패목록 두 번째 클릭 = 여기서 죽었다
        _spin(qapp, 200)
        assert ov.isVisible()
    finally:
        # ★ 지우기 전에 **숨긴다.**  `hideEvent` 가 돌던 애니메이션(특히 busy 스트라이프의
        #   무한 루프)을 전부 멈춘다.  visible 인 채로 deleteLater 하면 파괴 도중에도
        #   tick 이 남아 다음 테스트의 processEvents 에서 죽은 객체를 건드린다.
        ov.hide()
        qapp.processEvents()
        ov.deleteLater()
        host.deleteLater()
        qapp.processEvents()


# ── 패턴 가드: **자기 삭제 애니메이션을 아예 쓰지 않는다** ────────────────────
def test_no_self_deleting_animations_in_ui():
    """★ ``DeletionPolicy.DeleteWhenStopped`` 를 UI 전체에서 금지한다.

    이 저장소의 애니메이션은 모두 대상 위젯(또는 자신이 만든 오버레이)을 부모로 갖는다 —
    부모가 죽으면 함께 죽으므로 **자기 삭제로 얻는 것이 없다.**  반면 잃는 것이 크다:
    자연 종료 시점에 C++ 객체가 사라지므로 그 객체를 가리키는 참조가 전부 dangling 이
    되고, 그 뒤 쓰이면 파이썬 예외가 아니라 **세그폴트**다.

    이 규칙은 사고 **세 건**을 하나로 덮는다:
      1. `self._anim` 에 핸들 보관 → 두 번째 호출의 `stop()` 이 RuntimeError → qFatal
         (구형 모드 토글·실패목록 두 번째 클릭 강제종료)
      2. 람다로 연결한 tick 이 파괴된 위젯으로 들어감 → 세그폴트
      3. 위 1·2 를 막으려 넣은 `destroyed.connect(anim.stop)` 이, 애니메이션이 먼저
         자기 삭제된 뒤 발화 → **또** 세그폴트

    3번이 교훈이다: 가드를 덧붙이는 것으로는 부족했고, **원인(자기 삭제)을 없애야** 했다.
    """
    offenders: list[str] = []
    for path in sorted(_UI_DIR.rglob("*.py")):
        for no, line in _code_lines(path):     # 주석·독스트링의 설명은 위반이 아니다
            if "DeleteWhenStopped" in line:
                offenders.append(f"{path.relative_to(_UI_DIR.parents[2])}:{no}")
    assert not offenders, (
        "자기 삭제 애니메이션(DeleteWhenStopped)을 썼다 — 부모가 이미 소유하므로 얻는 것이 "
        "없고, 남은 참조가 dangling 이 되어 세그폴트를 낸다.  그냥 start() 를 쓰라: "
        + ", ".join(offenders)
    )


def test_animation_ticks_do_not_outlive_their_widget():
    """애니메이션 tick 의 receiver 는 애니메이션과 **함께 죽어야** 한다.

    람다 슬롯이어도, 애니메이션이 대상의 자식이면 대상과 함께 파괴돼 tick 자체가 불가능
    하다.  그래서 검사할 것은 '람다인가'가 아니라 **애니메이션의 부모가 있는가**다 —
    `QVariantAnimation()` 을 부모 없이 만들면 아무도 소유하지 않아 위젯보다 오래 산다."""
    offenders: list[str] = []
    for path in sorted(_UI_DIR.rglob("*.py")):
        for no, line in _code_lines(path):
            if re.search(r"Q(?:Variant|Property)Animation\s*\(\s*\)", line):
                offenders.append(f"{path.relative_to(_UI_DIR.parents[2])}:{no}")
    assert not offenders, (
        "부모 없는 애니메이션 — 위젯이 죽어도 살아남아 죽은 객체로 tick 한다.  "
        "대상(또는 오버레이)을 부모로 주라: " + ", ".join(offenders)
    )


def test_static_singleshot_is_not_used_for_widget_callbacks():
    """★ ``QTimer.singleShot`` 정적 호출로 위젯 콜백을 예약하지 않는다.

    정적 타이머는 위젯의 자식이 아니라서, 지연 시간 안에 위젯이 파괴되면 콜백이 죽은
    위젯으로 들어간다.  ``QTimer(self)`` 는 위젯과 함께 파괴되므로 발화 자체가 불가능하다.

    ※ 예외적으로 '레이아웃이 확정된 뒤 한 번' 같은 **자기 파괴와 무관한** 용도는 허용
    범위가 넓지만, 애니메이션/오버레이처럼 **수명이 짧은** 것에는 쓰지 않는다.  그래서
    이 가드는 로딩 오버레이에만 적용한다(실제로 여기서 세그폴트가 났다)."""
    code = "\n".join(ln for _no, ln in
                     _code_lines(_UI_DIR / "widgets" / "loading_overlay.py"))
    assert "QTimer.singleShot" not in code, \
        "loading_overlay 가 정적 singleShot 으로 자기 콜백을 예약한다"
