"""매치 검토 진입 — 행을 **나눠서** 만든다(P-01).

배경(실측): `load_state` 가 전 행을 한 루프로 만들면 600행 진입에 4.0초 동안 창이
통째로 굳었고, 그 구간엔 로딩 표시조차 없어 '죽은 것처럼' 보였다.  행 하나가 위젯
~15개(라벨·버튼·지연 썸네일 2장·차순위 타일)라 개수에 그대로 비례한다.

여기서 지키는 계약:
  1. 배치가 끝나면 행 수가 매치 수와 정확히 같다(빠짐/중복 없음).
  2. 생성 중에는 오버레이가 떠서 입력을 잠근다 — 못 본 행을 두고 [검토 완료] 를
     누르면 사용자가 보지 못한 매치가 전부 '유지' 로 확정된다.
  3. 배치 도중 `load_state` 가 다시 들어오면 옛 세대는 스스로 멈춘다(행이 섞이지 않는다).
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app.models.result import MatchResult      # noqa: E402
from aoi_verification.app.ui.pages.match_review_page import (   # noqa: E402
    MatchReviewPage)


def _matches(n: int, tmp_path) -> list[MatchResult]:
    out = []
    for i in range(n):
        ref = tmp_path / f"ref_{i:03d}.jpg"
        val = tmp_path / f"val_{i:03d}.jpg"
        ref.write_bytes(b"")
        val.write_bytes(b"")
        out.append(MatchResult(slot="S1", ref_path=ref, val_path=val,
                               score=0.9 - i * 0.001))
    return out


def _drain(page) -> None:
    """배치 타이머를 끝까지 강제로 돌린다(테스트는 이벤트 루프를 안 돌린다)."""
    guard = 0
    while page._pending_rows and guard < 500:
        page._make_next_rows()
        guard += 1


def test_batched_creation_produces_every_row_exactly_once(styled_qapp, tmp_path):
    page = MatchReviewPage()
    ms = _matches(95, tmp_path)          # 40 배치 × 2 + 나머지
    page.load_state(ms)
    assert len(page._rows) < len(ms), "첫 배치에서 전부 만들어 버렸다(배치화 미동작)"
    _drain(page)
    assert len(page._rows) == len(ms)
    assert len(page._rows_by_key) == len(ms)
    assert page._list_layout.count() == len(ms)
    page.deleteLater()


def test_overlay_locks_input_while_rows_are_being_built(styled_qapp, tmp_path):
    page = MatchReviewPage()
    page.resize(1200, 800)
    page.show()                          # 부모가 숨어 있으면 자식도 isVisible=False
    page.load_state(_matches(95, tmp_path))
    assert page._loading.isVisible(), "행 생성 중 진행 표시가 없다"
    assert page._loading._input_locked, "생성 중 입력이 잠기지 않았다"
    _drain(page)
    page._loading.hide()                 # hide_overlay 는 최소표시 래치를 탄다
    assert not page._loading.isVisible()
    assert not page._loading._input_locked
    page.deleteLater()


def test_small_list_finishes_in_one_batch_without_overlay(styled_qapp, tmp_path):
    """흔한 경우(수십 건)는 한 번에 끝나므로 오버레이가 뜨지 않는다."""
    page = MatchReviewPage()
    page.resize(1200, 800)
    page.show()
    ms = _matches(12, tmp_path)
    page.load_state(ms)
    assert page._pending_rows == []
    assert len(page._rows) == len(ms)
    assert not page._loading.isVisible()
    page.deleteLater()


def test_reentrant_load_state_discards_the_old_generation(styled_qapp, tmp_path):
    """결과 → 검토 복귀처럼 다시 들어오면 옛 배치가 새 목록에 행을 섞지 않는다."""
    page = MatchReviewPage()
    first = _matches(95, tmp_path)
    page.load_state(first)
    stale_gen = page._row_batch_gen
    second = _matches(10, tmp_path / "b") if (tmp_path / "b").mkdir() is None else None
    page.load_state(second)
    assert page._load_gen != stale_gen
    page._row_batch_gen = stale_gen      # 옛 배치가 뒤늦게 발화한 상황을 재현
    page._make_next_rows()
    assert len(page._rows) == len(second), "옛 세대가 새 목록에 행을 섞었다"
    page.deleteLater()


def test_review_page_has_a_title_and_no_logo_inside_the_scroll(styled_qapp, tmp_path):
    """로고가 컬럼 헤더와 첫 행 사이에 끼어 표를 가르지 않는다(V-10) + 표제 존재(U-10)."""
    from PyQt6.QtWidgets import QLabel
    page = MatchReviewPage()
    page.load_state(_matches(3, tmp_path))
    assert page.title.text(), "검토 화면에 표제가 없다"
    inside = [w for w in page._scroll_host.findChildren(QLabel)
              if w.property("role") == "appLogo"]
    assert inside == [], "로고가 스크롤 안에 있어 표를 가른다"
    outside = [w for w in page.findChildren(QLabel)
               if w.property("role") == "appLogo"]
    assert len(outside) == 1, "페이지당 로고는 하나여야 한다"
    page.deleteLater()
