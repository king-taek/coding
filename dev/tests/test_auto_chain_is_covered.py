"""자동 처리 중에는 **사용자 조작이 결과에 닿지 못한다** — 실측으로 확인된 구멍 3건.

배경(전부 실측):

1. **2단계 자동 매치 사슬이 덮개 없이 돈다.**  `_on_precompute_finished` 가
   `hide_overlay()` 를 부른 뒤 사슬을 시작하는데, 그 다음부터는 `set_progress` 만
   부른다 — `LoadingOverlay.set_progress` 는 **숨은 오버레이를 다시 띄우지 않는다.**
   ref 400장 기준 오버레이는 479ms 에 사라지고 사슬은 3.0초에 끝나 **88.6%(356장)**
   가 무방비로 처리됐다.  그 구간에서 `childAt([되돌리기] 중심)` 은 `NeonButton`
   (오버레이가 아니다)이고 버튼은 활성이며 `Ctrl+Z` 가 그대로 발화한다.

2. **피해가 '미탐 하나' 보다 크다.**  `Ctrl+Z` 한 번이면 되돌린 ref 가 큐 맨 앞으로
   들어가고, 그 전에 이미 예약돼 있던 `singleShot(0, _on_pick)` 이 **바뀐 `_current`**
   에 적용된다.  그때부터 ref↔val 이 한 칸씩 밀려 끝까지 복구되지 않는다 — 실측
   200쌍 중 **157쌍이 엉뚱한 val 로 확정**됐다.  개수·중복·누락은 정상이라 엑셀에서는
   티가 나지 않는다.

3. **페이지 전환 스냅샷이 마우스만 흡수하고 키는 통과시킨다.**  전환 246ms 동안
   나가는 `MatchPage` 는 스택의 current 라 진짜로 `isVisible()=True` 이므로
   '각 페이지의 isVisible 가드' 라는 위임은 구조적으로 성립하지 않는다.  실측:
   전환 중 `Ctrl+Z` 한 번 → `finished` 가 **2회** → `_on_match_finished` 가
   `_skipped_a` 를 중복 없이 쌓지 않아 **엑셀 미탐 5행 → 10행**(같은 사진 5장 중복).

★ 이 파일의 테스트는 `motion.enabled` 를 **True 로 몽키패치한다.**  offscreen 기본은
  False 라 전환도 오버레이 페이드도 일어나지 않아, 패치하지 않으면 위 셋 중 어느
  것도 재현되지 않는다(그래서 오래 발견되지 않았다).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QPoint, Qt, QTimer                        # noqa: E402
from PyQt6.QtGui import QKeySequence, QShortcut                    # noqa: E402
from PyQt6.QtTest import QTest                                     # noqa: E402
from PyQt6.QtWidgets import (QApplication, QLabel,                 # noqa: E402
                             QStackedWidget, QWidget)

from aoi_verification.app.models.slot import ImageItem             # noqa: E402
from aoi_verification.app.ui import motion                         # noqa: E402


# ---------------------------------------------------------------------------
def _items(n, side="ref"):
    return [ImageItem("S1", Path(f"/tmp/aoi_cov/S1_{side}{i}.png"), side)
            for i in range(n)]


def _auto_page(monkeypatch, n_ref: int, *, per_ref_ms: float = 0.0):
    """자동 매치 사슬을 **실제 경로 그대로** 돌릴 수 있는 MatchPage.

    선계산 워커만 대역으로 바꾼다(진짜 워커는 이미지·GPU 가 필요하다) — 대신 실제
    코드와 같은 순서로 오버레이를 띄우고 `_on_precompute_finished` 를 부른다.
    `per_ref_ms` 는 사진 1장 디코딩에 걸리는 시간을 흉내 낸다: 사슬이 오버레이
    퇴장(최소 표시 래치 350ms + 페이드)보다 길어야 구멍이 드러난다.
    """
    from aoi_verification.app.ui.pages.match_page import MatchPage

    mp = MatchPage()
    mp.resize(1280, 800)
    monkeypatch.setattr(mp, "_start_precompute", lambda: None)

    def _fake_center(item):
        if per_ref_ms:
            time.sleep(per_ref_ms / 1000.0)

    monkeypatch.setattr(mp, "_show_center", _fake_center)

    refs = _items(n_ref, "ref")
    vals = _items(n_ref, "val")          # ref 마다 서로 다른 val 을 기대짝으로
    mp.show()
    mp.load_state(queue=list(refs), val_pool_by_slot={"S1": vals},
                  threshold=-1e9, auto_mode=True)
    # 실제 자동 선계산과 같은 상태 — 전체를 하나의 차단 오버레이로 덮고 시작한다.
    mp._loading.show_overlay("점수 계산 중", cancelable=True)
    mp._fast_mode = True
    mp._streaming_precompute = False
    mp._precompute_worker = None
    for i, r in enumerate(refs):
        mp._fast_results[(r.slot, r.path)] = [(vals[i].path, float(i))]
    return mp, refs, vals


def _run_chain(app, mp, *, timeout_s: float = 30.0):
    done = {"v": False}
    mp.finished.connect(lambda: done.__setitem__("v", True))
    mp._on_precompute_finished()
    t0 = time.perf_counter()
    while not done["v"] and time.perf_counter() - t0 < timeout_s:
        app.processEvents()
    assert done["v"], "자동 사슬이 끝나지 않았다"


def _covered(mp) -> bool:
    """지금 덮개가 **살아 있는가** — 보이기만 해서는 안 된다.

    최소 표시 래치·퇴장 페이드 중에는 `isVisible()` 이 아직 True 인데 곧 사라진다.
    그 상태로 작업을 계속하면 몇 백 ms 뒤 그대로 무방비가 된다.
    """
    return bool(mp._loading.isVisible() and not mp._loading.is_retiring())


# ---------------------------------------------------------------------------
# ① 사슬이 도는 **전 구간** 덮개가 있고, [되돌리기] 좌표는 오버레이가 먹는다
# ---------------------------------------------------------------------------
def test_auto_chain_is_covered_for_its_entire_run(styled_qapp, monkeypatch):
    monkeypatch.setattr(motion, "enabled", lambda: True)
    mp, refs, _vals = _auto_page(monkeypatch, 100, per_ref_ms=6.0)

    samples: list[tuple[bool, bool, str]] = []
    orig_pick = mp._on_pick

    def spy(entry):
        btn = mp.undo_btn
        c = btn.mapTo(mp, QPoint(btn.width() // 2, btn.height() // 2))
        hit = mp.childAt(c)
        blocked = hit is mp._loading or (hit is not None
                                         and mp._loading.isAncestorOf(hit))
        samples.append((_covered(mp), blocked,
                        type(hit).__name__ if hit is not None else "None"))
        orig_pick(entry)

    monkeypatch.setattr(mp, "_on_pick", spy)
    _run_chain(styled_qapp, mp)

    assert len(samples) == len(refs)
    no_cover = [s for s in samples if not s[0]]
    clickable = [s for s in samples if not s[1]]
    assert not no_cover and not clickable, (
        f"자동 매치 {len(refs)}장 중 덮개가 살아 있지 않은 채 처리된 것 "
        f"{len(no_cover)}장({len(no_cover) / len(samples) * 100:.1f}%), "
        f"[되돌리기] 좌표가 그대로 노출된 것 {len(clickable)}장"
        + (f"(그때 그 자리의 위젯: {clickable[0][2]})" if clickable else "")
        + ". set_progress 는 숨은 오버레이를 다시 띄우지 않는다 — "
        "'작업 중에는 덮는다' 를 지켜야 한다"
    )
    # 끝난 뒤에는 반드시 걷힌다 — 해제 지점은 `_advance` 의 빈-큐 분기 한 곳이다.
    t0 = time.perf_counter()
    while mp._loading.isVisible() and time.perf_counter() - t0 < 3:
        styled_qapp.processEvents()
    assert not mp._loading.isVisible(), "사슬이 끝났는데 덮개가 남았다"
    mp.deleteLater()
    styled_qapp.processEvents()


def test_auto_chain_progress_keeps_the_loading_contract(styled_qapp, monkeypatch):
    """진행은 `set_progress(done, total, msg)` 로 보고한다 — 0 에 멈추지 않는다."""
    monkeypatch.setattr(motion, "enabled", lambda: True)
    mp, refs, _vals = _auto_page(monkeypatch, 20)

    seen: list[tuple[int, int]] = []
    orig = mp._loading.set_progress

    def spy(done, total, message=""):
        seen.append((int(done), int(total)))
        orig(done, total, message)

    monkeypatch.setattr(mp._loading, "set_progress", spy)
    _run_chain(styled_qapp, mp)

    assert seen, "자동 사슬이 진행을 한 번도 보고하지 않았다"
    assert all(t > 0 for _d, t in seen), (
        f"총량 미상 보고가 섞였다(그 경우 busy 여야 한다): {seen[:5]}")
    # 0 에 멈추지 않는다 — 매 확정마다 한 칸씩 올라간다(마지막 확정 뒤에는 큐가
    # 비어 오버레이가 내려가므로 마지막 보고는 total-1 이다).
    assert [d for d, _t in seen] == sorted(d for d, _t in seen), (
        f"진행이 뒤로 갔다: {seen[:8]}")
    assert seen[0][0] == 0 and seen[-1][0] == len(refs) - 1, (
        f"진행이 처음부터 끝까지 흐르지 않았다: {seen[0]} → {seen[-1]}")
    mp.deleteLater()
    styled_qapp.processEvents()


# ---------------------------------------------------------------------------
# ② 사슬 중 Ctrl+Z 가 발화해도 ref↔val 짝이 밀리지 않는다
# ---------------------------------------------------------------------------
def test_ctrl_z_during_auto_chain_cannot_shift_the_pairs(styled_qapp,
                                                          monkeypatch):
    """200쌍 실측 사고(157쌍 오확정)의 축소판 — 여기서는 150쌍.

    ref i 의 기대 val 은 `vals[i]` 이고 점수도 i 다.  한 칸이라도 밀리면 점수가
    어긋나므로 '엉뚱한 val 로 확정' 을 개수로 셀 수 있다(중복·누락은 0인 채로
    틀리는 것이 이 사고의 특징이라, 개수만 보는 검사로는 잡히지 않는다).

    ★ 두 수치는 **함께 정해야 한다**: 사슬 길이(150 × 6ms ≈ 0.9초)가 오버레이
      퇴장(최소 표시 350ms + 페이드 112ms)보다 길고, 누르는 시점(110장째 ≈ 0.66초)
      이 그 뒤여야 한다.  짧게 잡으면 **수리 전에도 통과한다** — 덮개가 아직 안
      걷혀서 우연히 막히기 때문이다(실제로 60장 시나리오가 그랬다).
      이 배치의 실측: 수리 전 undo 5회 발화·오확정 38쌍 / 수리 후 0회·0쌍.

    ★ 누르는 주체는 `_on_pick` 안이 아니라 **독립 타이머**다.  사고의 핵심은
      "예약된 `singleShot(0, _on_pick)` 과 되돌리기가 **엇갈리는**" 것이라,
      `_on_pick` 안에서 누르면 그 엇갈림이 재현되지 않는다.
    """
    monkeypatch.setattr(motion, "enabled", lambda: True)
    mp, refs, vals = _auto_page(monkeypatch, 150, per_ref_ms=6.0)
    expect = {r.path: float(i) for i, r in enumerate(refs)}

    fired = {"n": 0, "covered": 0}

    def press():
        if fired["n"] >= 5 or not (mp._state and mp._state.queue):
            return
        if len(mp._state.matches) < 110:      # 덮개가 걷힐 시간을 지난 뒤부터
            QTimer.singleShot(1, press)
            return
        if _covered(mp):
            fired["covered"] += 1
        QTest.keyClick(mp, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        fired["n"] += 1
        QTimer.singleShot(3, press)

    QTimer.singleShot(0, press)
    _run_chain(styled_qapp, mp)

    assert fired["n"] == 5, "Ctrl+Z 를 계획대로 발사하지 못했다(시나리오 오류)"
    ms = mp._state.matches
    wrong = [m for m in ms if float(m.score) != expect[m.ref_path]]
    seen = [m.ref_path for m in ms]
    assert len(ms) == len(refs)
    assert not wrong, (
        f"{len(wrong)}/{len(refs)} 쌍이 엉뚱한 val 로 확정됐다 — "
        "예약돼 있던 _on_pick 이 되돌리기로 바뀐 _current 에 적용됐다. "
        f"(중복 {len(seen) - len(set(seen))} · 누락 {len(refs) - len(set(seen))} "
        "— 개수로는 티가 나지 않는다)")
    assert fired["covered"] == fired["n"], (
        "덮개가 없는 순간에 눌렸는데도 짝이 안 밀렸다 — 우연일 수 있으니 "
        "① 을 먼저 확인하라")
    mp.deleteLater()
    styled_qapp.processEvents()


def test_stop_during_auto_chain_actually_stops_it(styled_qapp, monkeypatch):
    """덮개를 이어받으면 [중지] 가 사슬 내내 보인다 — 그러면 **실제로 멈춰야** 한다.

    자동 매치는 워커가 아니라 `QTimer.singleShot(0, _on_pick)` 사슬로 돈다.  워커만
    멈추던 예전 코드에서는 중지 뒤에도 예약된 콜백이 계속 배달돼, 실측으로 150장 중
    41장에서 중지했는데도 끝까지 돌아 `finished` 까지 나갔다 — 상위는 `_phase` 를
    그대로 두므로 **버린 세션의 검토 화면이 열린다.**
    확인창 숫자도 함께 본다: 선계산 총량을 그대로 쓰면 이미 100% 인 '150/150' 이 뜬다.
    """
    from aoi_verification.app.ui.pages import match_page as mp_mod
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(motion, "enabled", lambda: True)
    mp, refs, _vals = _auto_page(monkeypatch, 150, per_ref_ms=6.0)
    mp._precompute_done = mp._precompute_total = len(refs)   # 선계산 100% 상태
    asked: list = []
    monkeypatch.setattr(mp_mod.sheets, "ask",
                        lambda *a, **k: (asked.append(a[2]),
                                         QMessageBox.StandardButton.Yes)[1])

    fins = {"n": 0}
    mp.finished.connect(lambda: fins.__setitem__("n", fins["n"] + 1))
    seen: dict = {}

    def stop():
        if len(mp._state.matches) < 40:
            QTimer.singleShot(1, stop)
            return
        seen["at"] = len(mp._state.matches)
        seen["stop_btn"] = mp._loading._cancel_btn.isVisible()
        seen["progress"] = mp._cancel_progress()
        mp._loading.cancel_requested.emit()

    QTimer.singleShot(0, stop)
    mp._on_precompute_finished()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 3:
        styled_qapp.processEvents()

    assert seen.get("stop_btn"), "사슬이 도는 동안 [중지] 가 보이지 않는다"
    assert seen["progress"] == (seen["at"], len(refs)), (
        f"확인창이 매칭 진행이 아닌 값을 쓴다: {seen['progress']} "
        f"(기대 {(seen['at'], len(refs))}) — 선계산 총량은 이미 100% 라 "
        "'150 / 150 까지 진행' 처럼 읽힌다")
    assert asked, "확인 없이 버렸다"
    assert fins["n"] == 0, (
        "중지했는데 finished 가 나갔다 — 상위가 버린 세션의 검토 화면을 연다")
    assert len(mp._state.matches) < len(refs), (
        f"중지 뒤에도 사슬이 계속 돌아 {len(mp._state.matches)}장까지 확정됐다 "
        f"(중지 시점 {seen['at']}장) — 예약된 _on_pick 이 그대로 배달된다")
    mp.deleteLater()
    styled_qapp.processEvents()


# ---------------------------------------------------------------------------
# ③ 페이지 전환 스냅샷이 **키도** 흡수한다 + finished 는 한 번만 반영된다
# ---------------------------------------------------------------------------
def test_transition_snapshot_swallows_shortcuts(styled_qapp, monkeypatch):
    """`transition_in` 이 떠 있는 동안 QShortcut 은 발화하지 않는다.

    ★ `QEvent.Shortcut` 이 목록에서 빠지면 `KeyPress`·`ShortcutOverride` 를 다 버려도
      QShortcut 은 그대로 발화한다 — 그래서 목록은 `motion.BLOCKED_INPUT_EVENTS`
      한 곳에서만 관리한다(`LoadingOverlay._BLOCKED` 도 이걸 쓴다).
    """
    from aoi_verification.app.ui.widgets.loading_overlay import LoadingOverlay
    from PyQt6.QtCore import QEvent

    assert LoadingOverlay._BLOCKED is motion.BLOCKED_INPUT_EVENTS, (
        "덮개마다 차단 목록이 갈라졌다 — 한쪽만 갱신되면 조용히 통과한다")
    assert QEvent.Type.Shortcut in motion.BLOCKED_INPUT_EVENTS

    monkeypatch.setattr(motion, "enabled", lambda: True)
    host = QWidget()
    host.resize(400, 300)
    host.show()
    fired: list[str] = []
    QShortcut(QKeySequence("Ctrl+Z"), host, activated=lambda: fired.append("z"))
    styled_qapp.processEvents()

    committed = {"v": False}
    motion.transition_in(host, motion.snapshot(host), forward=True,
                         on_commit=lambda: committed.__setitem__("v", True))
    QTest.keyClick(host, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    styled_qapp.processEvents()
    assert not fired, "전환 스냅샷이 단축키를 통과시킨다 — 나가는 화면이 눌린다"

    t0 = time.perf_counter()
    while not committed["v"] and time.perf_counter() - t0 < 3:
        styled_qapp.processEvents()
    assert committed["v"], "전환이 끝나지 않았다"
    styled_qapp.processEvents()
    # 전환이 끝나면 **반드시 풀린다** — 앱 전역 키 잠금이 남으면 앱이 죽은 것과 같다.
    QTest.keyClick(host, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    styled_qapp.processEvents()
    assert fired, "전환이 끝났는데도 키가 막혀 있다 — 필터가 해제되지 않았다"
    host.deleteLater()
    styled_qapp.processEvents()


def test_ctrl_z_during_page_transition_does_not_emit_finished_twice(
        styled_qapp, monkeypatch):
    """`main_window._show_page` 와 **같은 절차**로 전환하는 동안 Ctrl+Z 한 번."""
    monkeypatch.setattr(motion, "enabled", lambda: True)
    mp, refs, _vals = _auto_page(monkeypatch, 8)

    stack = QStackedWidget()
    stack.resize(1280, 800)
    nxt = QLabel("검토 화면")
    stack.addWidget(mp)
    stack.addWidget(nxt)
    stack.show()
    styled_qapp.processEvents()

    fins = {"n": 0}
    committed = {"v": False}
    during: dict = {}

    def _show_next():
        old = stack.currentWidget()
        stack.setCurrentWidget(nxt)
        pix = motion.snapshot(nxt)
        stack.setCurrentWidget(old)          # ← 나가는 페이지가 다시 current 다
        motion.transition_in(
            stack, pix, forward=True,
            on_commit=lambda: (stack.setCurrentWidget(nxt),
                               committed.__setitem__("v", True)))
        # 이 구간의 전제 — 나가는 MatchPage 는 **진짜로 보이고** 스택의 current 다.
        # 그래서 '각 페이지의 isVisible 가드' 로는 이 키를 막을 수 없다.
        during["visible"] = mp.isVisible()
        during["is_current"] = stack.currentWidget() is mp
        # 전환 중간에 사용자가 Ctrl+Z 를 누른다.
        QTest.keyClick(mp, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)

    def on_fin():
        fins["n"] += 1
        if fins["n"] == 1:
            _show_next()

    mp.finished.connect(on_fin)
    mp._on_precompute_finished()
    t0 = time.perf_counter()
    while not committed["v"] and time.perf_counter() - t0 < 10:
        styled_qapp.processEvents()
    styled_qapp.processEvents()

    assert during.get("visible") and during.get("is_current"), (
        f"전제가 깨졌다 — 나가는 페이지는 전환 내내 보이고 current 다: {during}")
    assert fins["n"] == 1, (
        f"finished 가 {fins['n']}회 나갔다 — 전환 중 되돌리기가 _advance 를 다시 "
        "태웠다.  상위는 그때마다 미탐을 다시 쌓고 검토 화면을 다시 연다")
    assert len(mp._state.matches) == len(refs), "전환 중 매치가 되돌려졌다"
    stack.deleteLater()
    styled_qapp.processEvents()


def test_on_match_finished_is_idempotent(qapp, monkeypatch, tmp_path):
    """받는 쪽도 멱등하다 — `finished` 가 두 번 와도 미탐 행이 늘지 않는다.

    전환 구간의 키를 막아 원인 하나는 닫혔지만, 신호가 두 번 오는 경로는 또 생길 수
    있다.  실측(수리 전): 두 번째 `finished` 만으로 엑셀 미탐 시트가 5행 → 10행이
    됐고 같은 사진 이름이 5개 중복됐다.
    """
    from aoi_verification.app.ui import main_window as mw

    monkeypatch.setattr(mw.MainWindow, "_start_backend_import_async",
                        lambda self: None)
    win = mw.MainWindow()
    try:
        no_match = {"S1": _items(5, "ref")}

        class _State:
            def __init__(self):
                self.no_match = no_match

        class _Page:
            def get_state(self):
                return _State()

        win._match_page = _Page()
        win._phase = mw.PHASE_A_MATCH
        monkeypatch.setattr(win, "_proceed_to_review_or_finish", lambda: None)

        win._on_match_finished()
        first = [m.path.name for m in win._compute_unmatched_refs()]
        win._on_match_finished()
        second = [m.path.name for m in win._compute_unmatched_refs()]

        assert len(first) == 5
        assert second == first, (
            f"finished 두 번에 미탐 행이 {len(first)} → {len(second)} 로 늘었다 "
            f"(중복 {len(second) - len(set(second))}건) — 엑셀 미탐 시트에 같은 "
            "사진이 두 번 찍힌다")
    finally:
        win.close()
        qapp.processEvents()
