"""업데이트 안내 팝업 — 업데이트 후 첫 실행 때 '바뀐 점' 을 한 번(사용자 결정: 하이라이트형)."""
from __future__ import annotations

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from aoi_verification.app.i18n import whats_new as notes
from aoi_verification.app.utils import paths
from aoi_verification.app.utils import whats_new as wn

E = [{"id": "c", "date": "2026-10-03", "headline": "새 기능\n셋", "summary": "설명",
      "others": [("엑셀", "열 추가")], "notice": "칸이 옮겨짐"},
     {"id": "b", "date": "2026-10-02", "headline": "둘", "others": []},
     {"id": "a", "date": "2026-10-01", "headline": "하나", "others": [("맵", "안내")],
      "notice": "옛 주의"}]


def test_pending_shows_everything_newer_than_seen():
    assert [e["id"] for e in wn.pending("a", E)] == ["c", "b"]
    assert wn.pending("c", E) == []


@pytest.mark.parametrize("seen", ["", "지워진-id"])
def test_unknown_or_first_time_shows_only_latest(seen):
    assert [e["id"] for e in wn.pending(seen, E)] == ["c"]


def test_merge_keeps_latest_as_highlight_and_folds_older_ones():
    v = wn.merge(E)
    assert (v.date, v.headline, v.summary, v.image) == ("2026.10.03", "새 기능\n셋", "설명", "")
    assert v.others == (("엑셀", "열 추가"), ("", "둘"), ("", "하나"), ("맵", "안내"))
    assert v.notices == ("칸이 옮겨짐", "옛 주의")


def test_entries_are_well_formed_and_newest_first():
    ids = [e["id"] for e in notes.ENTRIES]
    assert ids and len(ids) == len(set(ids))
    dates = [e["date"] for e in notes.ENTRIES]
    assert dates == sorted(dates, reverse=True)
    for e in notes.ENTRIES:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", e["date"])
        assert e["headline"].strip() and e["headline"].count("\n") <= 1
        assert all(area.strip() and line.strip() for area, line in e.get("others", ()))
        if e.get("image"):                       # 그림은 ui/assets 에 실제로 있어야 한다
            assert paths.logo_path(e["image"]).is_file()


def test_dialog_shows_highlight_others_and_notice(styled_qapp):
    from PyQt6.QtWidgets import QLabel

    from aoi_verification.app.ui.widgets.whats_new_dialog import WhatsNewDialog
    dlg = WhatsNewDialog(wn.merge(notes.ENTRIES[:1]))
    try:
        texts = [lb.text() for lb in dlg.findChildren(QLabel)]
        e = notes.ENTRIES[0]
        assert dlg.headline.text() == e["headline"]
        assert all(line in texts for _a, line in e["others"])
        if e.get("notice"):
            assert e["notice"] in texts
        if e.get("image"):
            assert dlg.image is not None and not dlg.image.pixmap().isNull()
    finally:
        dlg.deleteLater()


def test_startup_shows_once_then_records_seen(qapp, monkeypatch):
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui import main_window as mw
    from aoi_verification.app.ui.widgets.whats_new_dialog import WhatsNewDialog
    from aoi_verification.app.utils import prefs

    store = {"seen": ""}
    monkeypatch.setattr(prefs, "load", lambda: prefs.UiPrefs(whats_new_seen=store["seen"]))
    monkeypatch.setattr(prefs, "patch",
                        lambda **kw: store.update(seen=kw["whats_new_seen"]))
    shown = []
    monkeypatch.setattr(mw.sheets, "run", lambda w, **k: shown.append(w))

    from PyQt6.QtWidgets import QWidget

    class _Stub(QWidget):                          # 창 전체 대신 부모 위젯 하나
        _maybe_show_whats_new = mw.MainWindow._maybe_show_whats_new

    host = _Stub()
    host._maybe_show_whats_new()
    assert len(shown) == 1 and isinstance(shown[0], WhatsNewDialog)
    assert store["seen"] == notes.ENTRIES[0]["id"]
    host._maybe_show_whats_new()                   # 두 번째 실행 — 다시 뜨지 않는다
    assert len(shown) == 1
