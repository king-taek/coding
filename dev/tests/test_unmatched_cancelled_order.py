"""‘매칭 취소’ 항목의 표시 순서 — 매치 실패 검토 목록 (#14).

★ 왜 테스트가 필요한가: 이 기능의 유일한 입력이 ``MissEntry.note`` 인데,
``_is_cancelled`` 가 그것을 ``getattr(entry, "note", "")`` 로 읽는다.  즉 필드가
사라지거나 생산 쪽 문자열이 바뀌어도 **예외가 나지 않고 정렬만 조용히 망가진다** —
사용자가 '취소한 매칭이 목록 뒤에 안 모인다' 고 말해 주기 전에는 아무도 모른다.

사슬:  `result_page` 가 note 에 표시를 남김 → `_is_cancelled` 가 판정 →
`_display_order` 가 두 그룹으로 나눔 → `_populate_list` 가 구분선을 끼워 채움.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt                                    # noqa: E402

from aoi_verification.app import i18n                          # noqa: E402
from aoi_verification.app.models.result import MissEntry       # noqa: E402
from aoi_verification.app.models.slot import ImageItem         # noqa: E402
from aoi_verification.app.ui.widgets import (                  # noqa: E402
    unmatched_review_dialog as ur)


@pytest.fixture(scope="module")
def qapp(styled_qapp):
    """테마 적용된 앱 — conftest 가 `setStyleSheet` 을 세션당 1회만 부른다."""
    return styled_qapp


@pytest.fixture
def spy_render(monkeypatch):
    """무거운 첫 렌더(원본 디코드 + 후보 채점)를 막는다 — 여기선 순서만 본다."""
    monkeypatch.setattr(ur.UnmatchedReviewDialog, "_render_current",
                        lambda self: None, raising=True)


def _entry(slot: str, name: str, note: str = "") -> MissEntry:
    return MissEntry(slot=slot, side="ref", path=Path(f"/tmp/{name}.png"),
                     note=note)


def _dialog(unmatched: list[MissEntry]) -> ur.UnmatchedReviewDialog:
    """`test_unmatched_dialog_first_render` 와 같은 생성 패턴 (파일은 없어도 된다)."""
    slots = {e.slot for e in unmatched}
    pool = {s: [ImageItem(s, Path(f"/tmp/{s}_v1.png"), "val")] for s in slots}
    return ur.UnmatchedReviewDialog(
        unmatched=unmatched, val_pool=pool, already_used_vals=set(),
        score_cache=None, fast_results=None, parent=None,
        coord_mode=False, tolerance=500.0,
    )


def test_cancelled_entries_go_last(qapp, spy_render):
    """‘매칭 취소’ 는 일반 매치 실패 **뒤로** 모인다 (원본 리스트는 재정렬 안 함)."""
    entries = [
        _entry("A", "cancelled1", i18n.KO.CANCELLED_NOTE),
        _entry("B", "normal1"),
        _entry("C", "cancelled2", i18n.KO.CANCELLED_NOTE),
        _entry("D", "normal2"),
    ]
    dlg = _dialog(entries)
    try:
        normal, cancelled = dlg._display_order()
        assert normal == [1, 3], "일반 항목이 원래 순서대로 먼저 와야 한다"
        assert cancelled == [0, 2], "‘매칭 취소’ 항목이 뒤로 모여야 한다"
        # 인덱스는 self._unmatched 를 그대로 가리킨다(재정렬 금지).
        assert [e.slot for e in dlg._unmatched] == ["A", "B", "C", "D"]
    finally:
        dlg.close()


def test_resolved_entries_drop_out_of_the_list(qapp, spy_render):
    """매치 확정된 항목은 두 그룹 어디에도 남지 않는다."""
    entries = [
        _entry("A", "normal1"),
        _entry("B", "cancelled1", i18n.KO.CANCELLED_NOTE),
    ]
    dlg = _dialog(entries)
    try:
        dlg.resolved_refs.append(entries[0])          # 일반 항목을 확정
        assert dlg._display_order() == ([], [1])
        dlg.resolved_refs.append(entries[1])          # 취소 항목까지 확정
        assert dlg._display_order() == ([], [])
    finally:
        dlg.close()


def test_populate_list_inserts_separator_between_groups(qapp, spy_render):
    """목록은 일반 → 구분선 → 매칭 취소 순서로 채워진다."""
    entries = [
        _entry("A", "cancelled1", i18n.KO.CANCELLED_NOTE),
        _entry("B", "normal1"),
    ]
    dlg = _dialog(entries)
    try:
        dlg._populate_list()
        rows = [dlg.fail_list.item(r).text()
                for r in range(dlg.fail_list.count())]
        assert rows == ["[B]", i18n.KO.UNMATCHED_CANCELLED_SEPARATOR, "[A]"]
        # 구분선은 선택 불가 — 클릭해도 사진이 바뀌면 안 된다.
        sep = dlg.fail_list.item(1)
        assert sep.flags() == Qt.ItemFlag.NoItemFlags
        # 행↔인덱스 매핑에 구분선 행은 없다(있으면 잘못된 사진으로 점프한다).
        assert dlg._row_to_idx == {0: 1, 2: 0}
    finally:
        dlg.close()


def test_no_separator_without_cancelled_entries(qapp, spy_render):
    """취소 항목이 없으면 구분선도 없다."""
    dlg = _dialog([_entry("A", "normal1"), _entry("B", "normal2")])
    try:
        dlg._populate_list()
        rows = [dlg.fail_list.item(r).text()
                for r in range(dlg.fail_list.count())]
        assert rows == ["[A]", "[B]"]
    finally:
        dlg.close()


def test_consumer_recognises_the_shared_note_string(qapp, spy_render):
    """소비자가 공유 상수(CANCELLED_NOTE)를 인식한다 — 두 상수가 갈라지면 잡힌다.

    ⚠ 이 테스트의 이름과 독스트링을 고쳤다.  예전엔 "생산자 `result_page._on_review_
    matches` 가 note 를 만드는 자리를 그대로 흉내낸다" 고 적혀 있었는데 **그 함수는
    저장소에 없다** — 손으로 만든 note 를 판정에 먹이면서 '생산자–소비자 결합을 검증
    한다' 고 말하고 있었고, 그래서 아래 `test_..._has_no_producer_today` 가 잡아내는
    단절을 이 테스트는 통과시켰다.  지금 이것이 검증하는 것은 **소비자 쪽 계약**뿐이다.
    """
    produced = MissEntry(slot="A", side="ref", path=Path("/tmp/x.png"),
                         note=i18n.KO.CANCELLED_NOTE)
    assert ur.UnmatchedReviewDialog._is_cancelled(produced)


def test_the_cancelled_path_has_no_producer_today() -> None:
    """★ 이 기능은 **휴면**이다 — 그 사실 자체를 못 박는다.

    `MissEntry.note` 에 CANCELLED_NOTE_MARK 를 넣는 앱 코드가 하나도 없다.  그래서
    `_is_cancelled` 는 실행 중 절대 True 가 되지 않고 UNMATCHED_CANCELLED_SEPARATOR 도
    그려지지 않는다.  위 테스트들은 note 를 **손으로 만들어** 먹이므로 이 단절을 못 잡는다
    — 그 자리를 여기서 메운다.

    생산자를 다시 만들면 이 테스트가 실패한다.  그때 할 일은 테스트를 지우는 것이 아니라
    **`i18n/ko.py`·`_is_cancelled` 의 '생산자가 없다' 주석을 사실에 맞게 고치고** 여기서
    실제 생산자를 가리키는 것이다(`dev/tests/test_no_dead_i18n_keys.py` 의 _DORMANT
    항목도 함께 지운다).
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "aoi_verification"
    producers = []
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" in str(p) or p.parts[-2] == "i18n":
            continue
        src = p.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"note\s*=\s*([A-Za-z0-9_.]+)", src):
            if "CANCELLED" in m.group(1):
                producers.append(f"{p.name}: {m.group(0)}")
    assert producers == [], (
        "‘매칭 취소’ note 를 남기는 생산자가 생겼다 — 휴면이 아니게 됐으니 "
        f"주석·목록을 사실에 맞게 고쳐라: {producers}")


def test_other_notes_are_not_treated_as_cancelled(qapp, spy_render):
    """note 가 없거나 다른 값이면 일반 매치 실패다 (‘미매칭’ 계열과 섞이지 않게)."""
    cls = ur.UnmatchedReviewDialog
    assert not cls._is_cancelled(_entry("A", "a"))                 # 기본값 ""
    assert not cls._is_cancelled(_entry("A", "a", "미매칭"))
    assert not cls._is_cancelled(_entry("A", "a", "미매칭 (사용자 검토)"))


def test_missing_note_attribute_is_survivable(qapp, spy_render):
    """note 필드가 없는 객체가 와도 죽지 않는다 (`getattr` 기본값 계약)."""
    class _NoNote:
        slot, side, path = "A", "ref", Path("/tmp/a.png")

    assert not ur.UnmatchedReviewDialog._is_cancelled(_NoNote())
