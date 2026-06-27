"""후보 선별 렉 개선 검증 — 결정 1건이 전체 재생성을 일으키지 않고(증분),
사진이 많을 때 현재 슬롯만 표시하는지 확인."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication          # noqa: E402

from aoi_verification.app import config            # noqa: E402
from aoi_verification.app.models.slot import ImageItem          # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _items(slot, n):
    return [ImageItem(slot, Path(f"/tmp/{slot}_{i}.jpg"), "ref") for i in range(n)]


def _build_queue(per_slot):
    q = []
    for slot, n in per_slot:
        q.extend(_items(slot, n))
    return q


# ===========================================================================
# 증분: 결정 1건이 '영향 안 받은 슬롯' 섹션을 재생성하지 않는다(전체 재로딩 제거)
# ===========================================================================
def test_decide_does_not_rebuild_unaffected_slots(qapp, isolated_cache):
    from aoi_verification.app.ui.pages.select_page import SelectPage
    sp = SelectPage()
    sp.show()
    QApplication.processEvents()
    # 총 15장(<300) → 전 슬롯 표시 모드
    sp.load_state(queue=_build_queue([("S1", 5), ("S2", 5), ("S3", 5)]))
    assert sp._is_single_slot_mode() is False
    # 비영향 슬롯(S2, S3)의 섹션 객체 id 기록
    left = sp.left_panel
    before = {s: id(left._sections[s]) for s in ("S2", "S3")}

    # 현재 사진(S1 첫 장) 검증 — 다음도 S1 → 영향 슬롯 = {S1}
    assert sp._current.slot == "S1"
    sp._decide("verify")

    # S2/S3 섹션은 같은 객체 그대로(재생성 안 됨) = 전체 재로딩 없음
    after = {s: id(left._sections[s]) for s in ("S2", "S3")}
    assert after == before
    # 검증한 사진은 우측(검증 대상) S1 섹션에 반영
    assert "S1" in sp.right_panel._sections
    sp.deleteLater()


def test_decide_updates_affected_slot_counts(qapp, isolated_cache):
    from aoi_verification.app.ui.pages.select_page import SelectPage
    sp = SelectPage()
    sp.show()
    QApplication.processEvents()
    sp.load_state(queue=_build_queue([("S1", 4), ("S2", 4)]))
    # S1 좌측 표시 = 큐의 S1(4) − 현재(S1_0) = 3장
    assert len(sp.left_panel._cached.get("S1", [])) == 3
    sp._decide("exclude")     # S1_0 제외, 현재=S1_1
    # 여전히 S1, 좌측 = 큐의 S1(3) − 현재(S1_1) = 2장
    assert len(sp.left_panel._cached.get("S1", [])) == 2
    # 제외 카운트 버튼 반영
    assert sp.btn_view_excluded.isEnabled()
    sp.deleteLater()


# ===========================================================================
# one-slot 모드(≥300): 좌·우 패널에 현재 슬롯만 표시
# ===========================================================================
def test_single_slot_mode_shows_only_current(qapp, isolated_cache):
    from aoi_verification.app.ui.pages.select_page import SelectPage
    sp = SelectPage()
    sp.show()
    QApplication.processEvents()
    total = 350
    assert total >= config.SELECT_SINGLE_SLOT_THRESHOLD
    sp.load_state(queue=_build_queue([("S1", 150), ("S2", 150), ("S3", 50)]))
    assert sp._is_single_slot_mode() is True
    # 좌 패널엔 현재 슬롯(S1)만
    assert set(sp.left_panel._sections.keys()) == {"S1"}
    assert sp._current.slot == "S1"
    sp.deleteLater()


def test_single_slot_mode_switches_on_slot_change(qapp, isolated_cache):
    from aoi_verification.app.ui.pages.select_page import SelectPage
    sp = SelectPage()
    sp.show()
    QApplication.processEvents()
    # S1 3장, S2 다수 → 총 ≥300 으로 one-slot. S1 을 소진하면 S2 로 전환.
    sp.load_state(queue=_build_queue([("S1", 3), ("S2", 300)]))
    assert sp._is_single_slot_mode() is True
    # S1_0 결정 중 → 좌측엔 다른 S1 후보(S1_1, S1_2)
    assert set(sp.left_panel._sections.keys()) == {"S1"}
    sp._decide("verify")      # S1_0 → 현재 S1_1 (좌측 S1_2)
    assert sp._current.slot == "S1"
    assert set(sp.left_panel._sections.keys()) == {"S1"}
    sp._decide("verify")      # S1_1 → 현재 S1_2 (마지막, 다른 S1 후보 없음)
    assert sp._current.slot == "S1"
    sp._decide("verify")      # S1_2 → 현재 S2_0, 패널 S2 로 전환
    assert sp._current.slot == "S2"
    assert set(sp.left_panel._sections.keys()) == {"S2"}
    sp.deleteLater()
