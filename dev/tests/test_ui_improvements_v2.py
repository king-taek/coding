"""UI 개선 비교 시안(`AOI_UI_개선_비교시안.dc.html`) 구현분의 회귀 가드.

시안이 고친 것들은 대부분 '화면이 사실을 말하지 않는다' 는 결함이라, 지키지 않으면
조용히 옛 모습으로 되돌아간다(테두리가 아니라 **문구·활성 상태·자리**가 계약이다).
여기서는 그 계약 중 **되돌아가면 사용자가 손해를 보는 것들**만 못 박는다.
"""

from __future__ import annotations

import os
import time

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


def test_eta_resets_when_the_total_changes(qapp):
    """총량 변경 = **다른 일이 시작됐다** — 이전 단계의 처리율을 물려주면 추정치가
    조용히 거짓말을 한다(바를 스냅하는 것과 같은 이유)."""
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업")
        # ★ 실제 시간이 흘러야 처리율을 잴 수 있다 — 같은 ms 안의 연속 보고는
        #   표본이 되지 못한다(그때 진행 델타가 유실되지 않는지는 아래 전용 테스트).
        for i in range(1, 9):
            time.sleep(0.003)
            ov.set_progress(i, 100, "작업")
        assert ov._eta_samples > 0 and ov._eta_rate is not None

        ov.set_progress(0, 40, "다음 단계")       # 총량 변경
        assert ov._eta_rate is None, "총량이 바뀌었는데 옛 처리율이 남았다"
        assert ov._eta_samples == 0
        assert ov._eta_label.text() == "", "총량이 바뀌었는데 옛 추정이 남았다"
    finally:
        ov.hide()
        host.deleteLater()


def test_dense_updates_do_not_lose_the_progress_delta(qapp):
    """같은 ms 안에 여러 번 보고해도 진행분이 유실되지 않는다.

    ★ 잰 시간이 0ms 일 때 기준점을 옮겨 버리면 그 사이 진행분이 통째로 사라져,
      갱신이 촘촘한 작업에서는 표본이 영영 쌓이지 않고 남은 시간이 "—" 로 굳는다."""
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업")
        ov.set_progress(0, 1000, "작업")
        for i in range(1, 60):               # 같은 ms 안에 몰아친다
            ov.set_progress(i, 1000, "작업")
        time.sleep(0.005)
        ov.set_progress(60, 1000, "작업")
        assert ov._eta_samples >= 1, "촘촘한 갱신에서 표본이 하나도 안 쌓였다"
        # 기준점이 매번 밀렸다면 마지막 델타가 1 이 돼 처리율이 60배 낮게 잡힌다.
        assert ov._eta_rate is not None and ov._eta_rate > 0
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
    덮어, 자리를 나눠 쓴다는 계약이 조용히 깨진다."""
    host, ov = _overlay(qapp, show_host=True)
    try:
        ov.show_overlay("작업")
        ov.show()
        qapp.processEvents()
        assert not ov._busy.isHidden()
        assert ov._busy.width() == ov._panel.width(), (
            f"busy 스윕({ov._busy.width()})이 패널({ov._panel.width()})을 다 덮지 않는다")

        ov.set_progress(3, 10, "작업")
        qapp.processEvents()
        assert ov._progress.width() == ov._panel.width(), "결정형 눈금이 전폭이 아니다"
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
