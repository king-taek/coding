"""업데이트 안내 팝업 — 업데이트 후 첫 실행 때 '바뀐 점' 을 한 번(사용자 요청, A. 짧은 목록형)."""
from __future__ import annotations

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from aoi_verification.app import i18n
from aoi_verification.app.i18n import whats_new as notes
from aoi_verification.app.utils import whats_new as wn

E = [{"id": "c", "date": "2026-10-03", "items": ["셋"]},
     {"id": "b", "date": "2026-10-02", "items": ["둘"]},
     {"id": "a", "date": "2026-10-01", "items": ["하나", "넷"]}]


def test_pending_shows_everything_newer_than_seen():
    assert [e["id"] for e in wn.pending("a", E)] == ["c", "b"]
    assert wn.pending("c", E) == []


@pytest.mark.parametrize("seen", ["", "지워진-id"])
def test_unknown_or_first_time_shows_only_latest(seen):
    assert [e["id"] for e in wn.pending(seen, E)] == ["c"]


def test_render_is_date_then_one_line_items():
    assert wn.render(E[2:]) == "2026-10-01\n· 하나\n· 넷"
    assert wn.render(E[:2]) == "2026-10-03\n· 셋\n\n2026-10-02\n· 둘"


def test_entries_are_well_formed_and_newest_first():
    ids = [e["id"] for e in notes.ENTRIES]
    assert ids and len(ids) == len(set(ids))
    dates = [e["date"] for e in notes.ENTRIES]
    assert dates == sorted(dates, reverse=True)
    for e in notes.ENTRIES:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", e["date"])
        assert e["items"] and all(t.strip() and "\n" not in t for t in e["items"])


def test_startup_shows_once_then_records_seen(qapp, monkeypatch):
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui import main_window as mw
    from aoi_verification.app.utils import prefs

    store = {"seen": ""}
    monkeypatch.setattr(prefs, "load", lambda: prefs.UiPrefs(whats_new_seen=store["seen"]))
    monkeypatch.setattr(prefs, "patch",
                        lambda **kw: store.update(seen=kw["whats_new_seen"]))
    shown = []
    monkeypatch.setattr(mw.sheets, "info", lambda _p, title, text: shown.append((title, text)))

    class _Stub:
        _maybe_show_whats_new = mw.MainWindow._maybe_show_whats_new

    _Stub()._maybe_show_whats_new()
    assert shown == [(i18n.KO.WHATS_NEW_TITLE, wn.render(notes.ENTRIES[:1]))]
    assert store["seen"] == notes.ENTRIES[0]["id"]
    _Stub()._maybe_show_whats_new()               # 두 번째 실행 — 다시 뜨지 않는다
    assert len(shown) == 1
