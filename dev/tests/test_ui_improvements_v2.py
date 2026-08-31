"""UI 개선 비교 시안(`AOI_UI_개선_비교시안.dc.html`) 구현분의 회귀 가드.

시안이 고친 것들은 대부분 '화면이 사실을 말하지 않는다' 는 결함이라, 지키지 않으면
조용히 옛 모습으로 되돌아간다(테두리가 아니라 **문구·활성 상태·자리**가 계약이다).
여기서는 그 계약 중 **되돌아가면 사용자가 손해를 보는 것들**만 못 박는다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QWidget                        # noqa: E402

from aoi_verification.app import i18n                       # noqa: E402
from aoi_verification.app.models.slot import ImageItem      # noqa: E402


@pytest.fixture()
def qapp(styled_qapp):
    """★ 테마가 적용된 앱을 쓴다(conftest 의 `styled_qapp`).

    이 파일의 계약 중 상당수는 **QSS 가 만든다** — 예컨대 상단 진행 눈금의 4px 높이는
    `max-height` 규칙이고, 스타일시트 없는 맨 QApplication 에서는 기본 20px 이 나온다.
    테마를 직접 적용하지 않는 이유는 `setStyleSheet` 이 부를수록 비싸기 때문이다."""
    return styled_qapp


# ── 5. 슬롯 매핑 [묶기 ↔] 의 '먹은 클릭' ──────────────────────────────────
def test_pair_button_opens_only_when_both_sides_are_chosen(qapp):
    """양쪽에서 하나씩 고르기 전에는 [묶기] 가 **비활성**이다.

    예전엔 활성으로 보이는데 눌러도 아무 일도 없었다 — 벌크 선택에서 이미 고친
    결함이 이 창에만 남아 있었다."""
    from aoi_verification.app.ui.widgets.slot_mapping_dialog import SlotMappingDialog

    dlg = SlotMappingDialog(["R1", "R2"], ["V1", "V2"], parent=None)
    try:
        assert dlg._pair_btn.isEnabled() is False, "선택 0 개인데 [묶기] 가 열려 있다"

        dlg._ref_sel = "R1"
        dlg._sync_pair_btn()
        assert dlg._pair_btn.isEnabled() is False, "한쪽만 골랐는데 [묶기] 가 열렸다"

        dlg._val_sel = "V1"
        dlg._sync_pair_btn()
        assert dlg._pair_btn.isEnabled() is True, "양쪽을 골랐는데 [묶기] 가 잠겨 있다"

        dlg._on_clear_sel()
        assert dlg._pair_btn.isEnabled() is False, "선택 해제 뒤에도 [묶기] 가 열려 있다"
    finally:
        dlg.deleteLater()


# ── 21. 벌크 선택: 화면 밖 선택 수 ────────────────────────────────────────
def test_summary_warns_about_selection_outside_this_page(qapp):
    """페이지네이션 중에는 '이 페이지 밖 n 장 포함' 이 요약에 붙는다.

    실행 직전에 화면 밖 수백 장이 함께 처리된다는 사실을 화면이 말해야 한다."""
    from aoi_verification.app.ui.widgets.bulk_select_dialog import (
        BulkSelectDialog, _PAGINATE_THRESHOLD)

    n = _PAGINATE_THRESHOLD + 200
    data = {"S1": [ImageItem(slot="S1", path=Path(f"/tmp/S1_{i}.jpg"), side="ref")
                   for i in range(n)]}
    dlg = BulkSelectDialog("t", data, actions=[("x", "X", "primary")])
    try:
        assert dlg._paginated, "이 표본은 페이지네이션 조건을 만족해야 한다"
        dlg._select_all()                       # 모든 페이지 선택
        text = dlg._summary_label.text()
        off = len(dlg._selected_keys) - len(dlg._page_slice())
        assert text == i18n.KO.BULK_SELECT_SUMMARY_OFFPAGE_FMT.format(
            n=len(dlg._selected_keys), m=off), text

        # 이 페이지 안에서만 고르면 덧말은 붙지 않는다(없는 사실을 적지 않는다).
        dlg._clear_selection()
        first = dlg._page_slice()[0][1]
        dlg._on_tile_toggle(first, True)
        assert dlg._summary_label.text() == \
            i18n.KO.BULK_SELECT_SUMMARY_FMT.format(n=1)
    finally:
        dlg.deleteLater()


# ── 31. 로딩 오버레이 — 타이틀블록 ────────────────────────────────────────
class _FakeNow:
    """테스트가 손으로 돌리는 벽시계 — 여러 `_StepClock` 이 공유한다."""

    def __init__(self) -> None:
        self.ms = 0


class _StepClock:
    """제어 가능한 `QElapsedTimer` 대역 — 벽시계 대신 테스트가 ms 를 준다.

    ★ `time.sleep(3ms)` 로 표본을 만들면 `-n auto` 로 부하가 걸린 워커나 sleep
      해상도가 거친 플랫폼에서 `elapsed()` 가 0 으로 떨어져 표본이 하나도 안 쌓인다 —
      검증하려는 코드와 무관한 이유로 깜빡이는 테스트가 된다.  시간을 주입하면 같은
      불변식을 결정적으로 잰다(`LoadingOverlay._feed_eta` 가 쓰는 세 메서드뿐이다).

    ★ ``now`` 를 주면 **진짜 타이머처럼** 자기 원점을 기억하고 그 차이를 돌려준다 —
      한 시계를 restart 해도 다른 시계의 경과는 그대로다.  ETA 는 단계 시작점 시계와
      표본 눈금 두 개를 **동시에** 쓰므로 이게 있어야 실제와 같은 순서로 흐른다."""

    def __init__(self, now: "_FakeNow | None" = None) -> None:
        self._valid = False
        self._now = now
        self._origin = 0
        self.ms = 0

    def isValid(self) -> bool:
        return self._valid

    def start(self) -> None:
        self._valid = True
        self._origin = self._now.ms if self._now is not None else 0
        self.ms = 0

    def restart(self) -> int:
        prev = self.elapsed()
        self._origin = self._now.ms if self._now is not None else 0
        self.ms = 0
        return prev

    def elapsed(self) -> int:
        if self._now is not None:
            return self._now.ms - self._origin
        return self.ms


def _overlay(qapp, *, show_host: bool = False):
    """오버레이 + 그 부모.

    ★ 기하(폭·배치)를 재는 테스트는 `show_host=True` 로 부모를 **실제로 띄워야**
      한다.  Qt 는 보이지 않는 위젯의 레이아웃 활성화를 미루므로, 부모가 숨어 있으면
      자식이 기본 100×30 에 머물러 있어 '레이아웃이 깨졌다' 는 거짓 실패가 난다."""
    from aoi_verification.app.ui.widgets.loading_overlay import LoadingOverlay
    host = QWidget()
    host.resize(800, 600)
    if show_host:
        host.show()
    return host, LoadingOverlay(host)


def test_stage_line_is_absent_unless_the_caller_asks(qapp):
    """단계 정보를 주지 않은 호출부의 화면은 하나도 바뀌지 않는다(하위 호환).

    오버레이 하나를 20여 곳이 공유하므로 **기본은 숨김**이어야 한다."""
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("설치 중")
        assert ov._stage_label.isHidden(), "단계 줄을 요청하지 않았는데 떴다"
        assert ov._steps.isHidden(), "여정 행을 요청하지 않았는데 떴다"

        ov.show_overlay("스캔 중", step=(2, 3), steps=("스캔", "썸네일", "준비"))
        assert not ov._stage_label.isHidden()
        assert ov._stage_label.text() == i18n.KO.LOADING_STAGE_FMT.format(
            idx=2, total=3)
        assert not ov._steps.isHidden()
        assert ov._steps._index == 1, "현재 단계는 0-based 로 1(=2단계)이어야 한다"
    finally:
        ov.hide()
        host.deleteLater()


def test_set_stage_advances_without_restarting_the_entrance(qapp):
    """여정 중간의 단계 전환은 `set_stage` 로 한다 — `show_overlay` 를 다시 부르면
    등장 모션과 최소표시 래치가 되감긴다."""
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("스캔", step=(1, 3), steps=("스캔", "썸네일", "준비"))
        token = ov._show_token
        ov.set_stage((3, 3), ("스캔", "썸네일", "준비"))
        assert ov._stage_label.text() == i18n.KO.LOADING_STAGE_FMT.format(
            idx=3, total=3)
        assert ov._show_token == token, "단계만 바꿨는데 표시가 되감겼다"
    finally:
        ov.hide()
        host.deleteLater()


def _eta_clocks(ov):
    """ETA 가 쓰는 두 시계를 **공유 벽시계**로 갈아 끼운다 — `now.ms` 를 돌리면 된다."""
    now = _FakeNow()
    ov._eta_clock = _StepClock(now)
    ov._eta_sample_clock = _StepClock(now)
    ov._eta_clock.start()
    ov._eta_sample_clock.start()
    ov._eta_start_done = 0
    return now


def _feed(ov, now, *, total, steps):
    """(경과 ms, done) 목록을 먹인다 — 벽시계 대신 테스트가 시간을 준다."""
    for ms, done in steps:
        now.ms = ms
        ov.set_progress(done, total, "작업")


def test_eta_resets_when_the_total_changes(qapp):
    """총량 변경 = **다른 일이 시작됐다** — 이전 단계의 처리율을 물려주면 추정치가
    조용히 거짓말을 한다(바를 스냅하는 것과 같은 이유)."""
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업")
        ov.set_progress(0, 100, "작업")            # 총량 확정 → ETA 시계 새로
        now = _eta_clocks(ov)
        _feed(ov, now, total=100, steps=[(300 * i, i * 5) for i in range(1, 9)])
        assert ov._eta_samples > 0

        ov.set_progress(0, 40, "다음 단계")       # 총량 변경
        assert ov._eta_start_done is None, "총량이 바뀌었는데 출발점이 남았다"
        assert ov._eta_samples == 0
        assert ov._eta_label.text() == "", "총량이 바뀌었는데 옛 추정이 남았다"
    finally:
        ov.hide()
        host.deleteLater()


# ---------------------------------------------------------------------------
# ★ 남은 시간은 **전체 기준**이다 — 순간 처리율이 아니다
#
# 실제 신고: "썸네일 생성 중 로딩창에서 남은 시간 계산이 이상함. Slot별로 남은
# 시간을 계산해서 나온 건가..? 전체 process 의 남은 시간이 나와야 하는데 그냥 몇
# 초씩만 나와서 의미없는 시간이 뜸."  원인은 `ThumbnailPool` 이 워커 8개로 **사진
# 한 장마다** 보고한다는 것이었다 — 두 신호 사이가 1ms, 델타가 1 이면 순간
# 처리율이 1,000장/초로 잡혀 남은 시간이 몇 초로 주저앉는다.
# ---------------------------------------------------------------------------
def test_eta_is_not_fooled_by_a_burst_of_dense_reports(qapp):
    """워커 8개가 **한꺼번에** 보고해도 남은 시간은 실제 속도를 따른다.

    `ThumbnailPool` 은 사진 한 장마다 보고하는데, 워커 8개가 거의 동시에 끝나면
    1ms 안에 8건이 몰리고(순간 1,000장/초) 그다음 한참 조용하다.  순간 처리율을
    지수평활하면 몰린 쪽이 표본 수로 압도해 추정이 몇 초로 주저앉는다 — 사용자가
    본 "그냥 몇 초씩만 나와서 의미없는 시간" 이 그것이다.

    여기서는 8건 몰이(각 1ms) + 1초 정적을 12번 반복한다.  실제 속도는
    96장 / 12.1초 ≈ 7.9장/초 → 남은 904장은 약 1분 54초다."""
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업")
        ov.set_progress(0, 1000, "작업")
        now = _eta_clocks(ov)
        steps, t, done = [], 0, 0
        for _burst in range(12):
            for _i in range(8):              # 몰이 — 1ms 간격 8건
                t += 1
                done += 1
                steps.append((t, done))
            t += 1000                        # 정적
        _feed(ov, now, total=1000, steps=steps)

        text = ov._eta_label.text()
        # ★ 빈 문자열도 실패다.  옛 모델은 남은 904장을 2초 이내로 추정해
        #   `ETA_HUSH_S`("곧 끝난다") 에 걸려 **아무것도 안 적었다** — 사용자가 본
        #   '의미없는 시간' 의 극단이다.
        assert text and text != i18n.KO.LOADING_ETA_UNKNOWN, \
            f"몰이 보고에 속아 추정이 무너졌다(빈칸이면 2초 이내로 봤다는 뜻): {text!r}"
        assert "분" in text, f"몇 초짜리로 무너졌다 — 전체 기준이 아니다: {text!r}"
    finally:
        ov.hide()
        host.deleteLater()


def test_eta_samples_are_counted_by_time_not_by_signal(qapp):
    """'신호 5개' 로 세면 1ms 안에 다섯 개가 쌓여 최소 표본이 아무것도 보증하지 못한다."""
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업")
        ov.set_progress(0, 1000, "작업")
        now = _eta_clocks(ov)
        # 같은 ms 안에 100번 — 표본은 하나도 쌓이지 않아야 한다.
        _feed(ov, now, total=1000, steps=[(0, i) for i in range(1, 101)])
        assert ov._eta_samples == 0, "잴 수 없는 구간에서 표본을 지어냈다"
        assert ov._eta_label.text() == i18n.KO.LOADING_ETA_UNKNOWN
    finally:
        ov.hide()
        host.deleteLater()


def test_eta_says_nothing_until_it_has_enough_samples(qapp):
    """표본이 모자라면 없는 정보를 지어내지 않는다."""
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업")
        ov.set_progress(1, 1000, "작업")
        ov.set_progress(2, 1000, "작업")
        assert ov._eta_label.text() == i18n.KO.LOADING_ETA_UNKNOWN
    finally:
        ov.hide()
        host.deleteLater()


def test_busy_clears_the_numbers_it_cannot_know(qapp):
    """총량을 모르는 구간에서는 수치·퍼센트·추정을 전부 비운다(자리는 남긴다)."""
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업")
        ov.set_progress(30, 60, "작업")
        assert ov._pct_label.text() == "50%"
        ov.set_progress(0, 0, "총량 미상")
        assert ov._pct_label.text() == ""
        assert ov._count_label.text() == ""
        assert ov._eta_label.text() == ""
    finally:
        ov.hide()
        host.deleteLater()


def test_labels_are_written_only_when_the_value_changed(qapp):
    """같은 문자열을 다시 넣어도 Qt 는 레이아웃 갱신과 리페인트를 예약한다 —
    초당 수십 번 불리는 경로라 그것이 곧 렉이다."""
    from aoi_verification.app.ui.widgets.loading_overlay import LoadingOverlay

    host, ov = _overlay(qapp)
    try:
        writes = []
        lbl = ov._count_label
        orig = lbl.setText
        lbl.setText = lambda t: (writes.append(t), orig(t))[1]   # type: ignore
        LoadingOverlay._set_text(lbl, "같은 값")
        LoadingOverlay._set_text(lbl, "같은 값")
        LoadingOverlay._set_text(lbl, "다른 값")
        assert writes == ["같은 값", "다른 값"], writes
    finally:
        ov.hide()
        host.deleteLater()


def test_no_always_on_animation_while_determinate(qapp):
    """결정형일 때 이 패널의 상시 애니메이션은 **0 개**다.

    예전 회전 링은 62.5Hz 타이머로 상시 돌아, UI 스레드가 바쁠 때 가장 먼저 끊기며
    '로딩 표현이 버벅거린다' 로 보였다 — 그것을 없앤 것이 이 개편의 핵심이다."""
    from aoi_verification.app.ui.widgets import loading_overlay as lo

    assert not hasattr(lo, "_SpinnerDot"), "회전 링이 되살아났다"
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업")
        ov.set_progress(5, 10, "작업")           # 결정형으로 승격
        assert ov._busy.isHidden(), "결정형인데 busy 스윕이 남아 돌고 있다"
        assert ov._busy._anim.state() == ov._busy._anim.State.Stopped
    finally:
        ov.hide()
        host.deleteLater()


def test_a_long_message_does_not_stretch_the_panel(qapp):
    """긴 메시지가 패널을 옆으로 늘리면 안 된다.

    실제 호출부가 있다 — `main_window._start_openvino_install` 은 pip 출력 80자를
    그대로 실어 보낸다.  표제가 20px 이라 한 줄만으로도 패널 폭을 훌쩍 넘긴다."""
    from aoi_verification.app.ui.widgets.loading_overlay import LoadingOverlay

    host, ov = _overlay(qapp, show_host=True)
    try:
        ov.show_overlay("짧은 문구")
        ov.show()
        qapp.processEvents()
        narrow = ov._panel.width()

        ov.show_overlay("설치 중\n" + "x" * 80)
        ov.show()
        qapp.processEvents()
        qapp.processEvents()
        assert ov._panel.width() == narrow, (
            f"긴 메시지에 패널 폭이 {narrow} → {ov._panel.width()} 로 늘었다")
        assert ov._panel.width() <= LoadingOverlay.PANEL_W
    finally:
        ov.hide()
        host.deleteLater()


def test_busy_sweep_spans_the_same_rule_as_the_determinate_fill(qapp):
    """busy 와 결정형은 패널 상단의 **같은 자리**를 나눠 쓴다.

    busy 폭을 상수로 고정해 두면 패널이 클램프되거나 넓어질 때 스윕이 눈금의 일부만
    덮어, 자리를 나눠 쓴다는 계약이 조용히 깨진다.

    ★ 기준은 패널 폭에서 **모서리 반지름을 뺀 폭**이다 — 눈금은 둥근 모서리의 곡선
      구간에 들어가지 않도록 좌우로 그만큼 들어가 있다(각진 끝이 곡선 밖으로
      삐져나오던 것을 그렇게 고쳤다: `test_loading_panel` 의 픽셀 가드 참조)."""
    host, ov = _overlay(qapp, show_host=True)
    try:
        ov.show_overlay("작업")
        ov.show()
        qapp.processEvents()
        # 바깥 레이아웃의 테두리 여백 + 눈금 행의 들임을 양쪽에서 뺀 폭.
        inner = ov._panel.width() - 2 * (ov.PANEL_BORDER_PX + ov.rule_inset_px())
        assert not ov._busy.isHidden()
        assert ov._busy.width() == inner, (
            f"busy 스윕({ov._busy.width()})이 눈금 폭({inner})과 다르다")

        ov.set_progress(3, 10, "작업")
        qapp.processEvents()
        assert ov._progress.width() == inner, "결정형 눈금 폭이 들임과 안 맞는다"
        assert ov._progress.width() == ov._busy.width(), \
            "busy 와 결정형이 같은 자리를 나눠 쓰지 않는다"
        assert ov._progress.height() == 4, "눈금 높이가 4px 가 아니다"
    finally:
        ov.hide()
        host.deleteLater()


def test_duration_is_not_more_precise_than_the_estimate(qapp):
    """추정치를 초 단위까지 적으면 숫자가 계속 흔들려 신뢰를 잃는다."""
    from aoi_verification.app.ui.widgets.loading_overlay import _fmt_duration

    assert _fmt_duration(45) == i18n.KO.DURATION_SEC_FMT.format(s=45)
    assert _fmt_duration(80) == i18n.KO.DURATION_MIN_FMT.format(m=1, s=20)
    assert _fmt_duration(3700) == i18n.KO.DURATION_HOUR_FMT.format(h=1, m=1)
    assert _fmt_duration(-5) == i18n.KO.DURATION_SEC_FMT.format(s=0)
def test_set_stage_makes_room_for_what_it_adds(qapp):
    """`set_stage` 는 내용만 바꾸는 게 아니라 **패널을 다시 재야** 한다.

    ★ 패널 크기는 `_place_panel` 의 `setGeometry` 가 sizeHint 로 정한다 — 단계 줄과
      여정 행(≈50px)이 나중에 붙으면 옛 높이 안으로 눌려 들어가, 맨 아래 줄
      (퍼센트 · 남은 시간 · 수치)이 잘린다.  `show_overlay` 로 이미 단계를 준 흐름은
      `_cover_parent` 가 재 주지만, 문구만으로 띄운 뒤 여정을 붙이는 경로가 남는다."""
    host, ov = _overlay(qapp, show_host=True)
    try:
        ov.show_overlay("작업")                     # 단계 없이 시작
        qapp.processEvents()
        before = ov._panel.height()
        ov.set_stage((1, 3), i18n.KO.LOAD_JOURNEY_STEPS)
        qapp.processEvents()
        assert ov._panel.height() >= ov._panel.sizeHint().height(), (
            "패널이 내용보다 작다 — 하단 줄이 잘린다")
        assert ov._panel.height() > before, "여정 행이 붙었는데 높이가 그대로다"
    finally:
        ov.hide()
        host.deleteLater()


def test_confirm_toast_does_not_move_the_header_buttons(qapp):
    """확정 토스트가 떠도 헤더 버튼은 **제자리**다.

    ★ 토스트를 '늘어나는 여백' 뒤(버튼 바로 앞)에 두면 문구가 붙는 순간 그 폭만큼
      버튼 넷이 통째로 왼쪽으로 밀린다.  연속 확정을 하는 사용자에겐 커서 아래에서
      [확정]이 [닫기]로 바뀌는 셈이라 오클릭을 만든다(폭 예약으로는 못 막는다 —
      확정 문구가 예약폭의 배에 가깝다)."""
    from aoi_verification.app.models.result import MissEntry
    from aoi_verification.app.ui.widgets.unmatched_review_dialog import (
        UnmatchedReviewDialog)

    entry = MissEntry(slot="S1", path=Path("/tmp/S1_r.jpg"), side="ref")
    dlg = UnmatchedReviewDialog([entry], {("S1", "ref"): []}, parent=None)
    try:
        dlg.resize(1200, 800)
        dlg.show()
        qapp.processEvents()
        before = [w.x() for w in (dlg.btn_prev, dlg.btn_skip,
                                  dlg.btn_confirm, dlg.btn_close)]
        text = i18n.KO.UNMATCHED_REVIEW_DONE_FMT.format(n=3)
        dlg._show_toast(text)
        dlg.layout().activate()
        qapp.processEvents()
        after = [w.x() for w in (dlg.btn_prev, dlg.btn_skip,
                                 dlg.btn_confirm, dlg.btn_close)]
        assert before == after, f"토스트에 버튼이 밀렸다: {before} → {after}"
        # 문구가 잘리지도 않는다(자리를 좁게 예약해 두면 그쪽으로 망가진다).
        assert dlg._toast.width() >= dlg._toast.sizeHint().width()
    finally:
        # ★ 토스트 타이머(1.8초)를 **여기서 끈다.**  창을 지운 뒤에도 살아 있으면
        #   한참 뒤 다른 테스트의 이벤트 루프에서 발화해 이미 사라진 라벨을 만진다
        #   (실측: 그 시점에 프로세스가 통째로 죽었다).  앱에서는 창이 닫히며 부모와
        #   함께 죽지만, 테스트는 `deleteLater` 라 그 보장이 없다.
        if dlg._toast_timer is not None:
            dlg._toast_timer.stop()
        dlg.hide()
        dlg.deleteLater()
        qapp.processEvents()


def test_fullscreen_viewer_fits_even_if_it_draws_before_layout(qapp, tmp_path):
    """'크게 보기' 는 **표시 전에 한 번 그려도** 창에 꽉 찬다.

    ★ 배치되지 않은 자식 위젯의 크기는 Qt 기본값 100×30 이고 그 값도 `> 1` 을
      통과한다 — 크기를 모르는 상태가 '안다' 로 읽혔다.  원본 디코드가 표시보다
      먼저 끝나면(작은 원본·캐시 적중) 그 100×30 에 맞춘 배율이 굳어 전체화면
      뷰어에 썸네일만 한 사진이 남았다."""
    pytest.importorskip("PIL")
    from PIL import Image
    from aoi_verification.app.ui.widgets.zoom_window import FullscreenViewer

    src = tmp_path / "probe.png"
    Image.new("RGB", (800, 600), (200, 30, 30)).save(src)
    view = FullscreenViewer(src)
    try:
        assert view._label.width() <= 100, "이 표본은 '배치 전' 이어야 의미가 있다"
        view._redraw()                       # 표시 전에 도착한 원본 디코드와 같은 경로
        vw, vh = view._view_size()
        assert vw > 100 and vh > 30, f"라벨 기본 크기를 진짜 크기로 믿었다({vw}×{vh})"
        assert view._scale > 0.5, f"썸네일 크기로 굳었다(배율 {view._scale:.3f})"
    finally:
        view.deleteLater()
        qapp.processEvents()


def test_the_two_pages_share_one_progress_contract(qapp):
    """선별·매칭 화면의 진행 표시 갱신은 **한 구현**이어야 한다.

    ★ 같은 12 줄(문구 두 개 · 클램프 · 노출 규칙)이 두 파일에 복제돼 있었고, 한쪽
      docstring 이 다른 쪽을 "같은 규약" 이라 가리키고 있었다 — 규약이라면 코드가
      하나여야 한다(한쪽만 고치는 사고를 막는다)."""
    from aoi_verification.app.ui.pages.match_page import MatchPage
    from aoi_verification.app.ui.pages.select_page import SelectPage
    from aoi_verification.app.ui.pages.progress_row import ProgressRowMixin

    for page_cls in (MatchPage, SelectPage):
        assert issubclass(page_cls, ProgressRowMixin)
        assert page_cls._set_progress is ProgressRowMixin._set_progress
        assert page_cls._clear_progress is ProgressRowMixin._clear_progress
def test_kla_steps_do_not_erase_the_journey():
    """KLA 해석 중에도 여정 표시(단계 서수 + 점 행)가 남아 있어야 한다.

    ★ `show_overlay` 를 step 없이 부르면 `_apply_stage(None, None)` 이 단계 줄과 점
      행을 **지운다.**  KLA 정보파일 읽기·WaferID OCR 은 스캔(1단계)의 뒷부분인데
      모달(`_ask_kla_side`) 때문에 오버레이를 다시 띄우게 되고, 그때 step 을 빠뜨리면
      가장 오래 걸리는 구간(OCR)에서 여정이 사라졌다가 2단계에서 되살아난다 —
      i18n `LOAD_JOURNEY_STEPS` 주석이 "단계 수를 바꾸지 않는다" 고 적어 둔 바로 그
      구간이다.  Qt 없이도 도는 소스 계약이라 여기서 값싸게 못 박는다."""
    import inspect
    import re
    from aoi_verification.app.ui import main_window as mw

    src = inspect.getsource(mw.MainWindow._kla_resolve_impl)
    calls = re.findall(r"show_overlay\((?:[^()]|\([^()]*\))*\)", src)
    assert calls, "이 함수는 오버레이를 다시 띄운다 — 표본이 없으면 계약이 무의미하다"
    for call in calls:
        assert "step=" in call and "steps=" in call, (
            f"단계 없이 오버레이를 띄운다(여정이 지워진다): {call}")
