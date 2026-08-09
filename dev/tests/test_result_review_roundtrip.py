"""결과 ↔ 검토 왕복 무결성(U-10)과 결과 화면의 안전장치(U-06/U-07/U-16).

배경: '검토' 가 세 이름·세 화면으로 갈려 있었다.  전용 검토 페이지가 이미 상위 기능을
갖고 있는데 결과 화면은 기능이 더 적은 별도 다이얼로그를 또 권했다.  그 다이얼로그를
없애고 **검토 화면으로 되돌아가는** 경로로 바꾸면서, 왕복해도 검토 결과가 사라지지
않는다는 것을 여기서 못 박는다.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app.models.result import (FinalResult,      # noqa: E402
                                                MatchResult, MissEntry)
from aoi_verification.app.ui.pages.match_review_page import (      # noqa: E402
    MatchReviewPage)
from aoi_verification.app.ui.pages.result_page import ResultPage   # noqa: E402


def _matches(n, tmp_path):
    out = []
    for i in range(n):
        r = tmp_path / f"r{i}.jpg"
        v = tmp_path / f"v{i}.jpg"
        r.write_bytes(b"")
        v.write_bytes(b"")
        out.append(MatchResult(slot="S1", ref_path=r, val_path=v, score=0.9))
    return out


def _result(tmp_path, n=4, misses=1):
    ms = _matches(n, tmp_path)
    miss = [MissEntry(slot="S1", side="ref", path=tmp_path / f"m{i}.jpg", note="미매칭")
            for i in range(misses)]
    return FinalResult(mode="coord", ref_machine="TB-15", val_machine="K-6",
                       matches=ms, slot_only_ref=[], slot_only_val=[],
                       unmatched_refs=miss, kla_folders={})


# ── U-10 ──────────────────────────────────────────────────────────────────
def test_dialog_module_is_gone_and_nothing_references_it():
    """부분집합 기능의 다이얼로그를 지웠으면 참조도 남으면 안 된다."""
    import pathlib
    repo = pathlib.Path(__file__).resolve().parents[2]
    assert not (repo / "aoi_verification/app/ui/widgets/matches_review.py").exists()
    hits = []
    for f in list(repo.glob("aoi_verification/**/*.py")) + \
            list(repo.glob("scripts/**/*.py")) + list(repo.glob("dev/**/*.py")):
        if f.name == "test_result_review_roundtrip.py":
            continue
        if "matches_review" in f.read_text(encoding="utf-8"):
            hits.append(str(f.relative_to(repo)))
    assert hits == [], f"지운 모듈을 아직 참조한다: {hits}"


def test_review_page_restores_unmatched_marks_on_reentry(styled_qapp, tmp_path):
    """되돌아갔을 때 '매치 없음' 표시가 살아 있어야 한다 — 예전엔 전멸했다."""
    page = MatchReviewPage()
    ms = _matches(4, tmp_path)
    page.load_state(ms)
    key = ms[1].key
    page._unmatched_keys.add(key)
    saved = page.unmatched_keys()
    assert key in saved

    page.load_state(ms)                       # 인자 없이 재진입 → 표시가 비워진다
    assert page.unmatched_keys() == set()

    page.load_state(ms, unmatched_keys=saved)  # 복귀 경로 → 되살아난다
    assert page.unmatched_keys() == saved
    page.deleteLater()


def test_reentry_ignores_keys_that_no_longer_exist(styled_qapp, tmp_path):
    """매치 목록이 바뀐 뒤 옛 키가 섞여 들어와도 조용히 무시한다."""
    page = MatchReviewPage()
    ms = _matches(3, tmp_path)
    page.load_state(ms, unmatched_keys={("S1", "없는파일.jpg", "없는파일.jpg")})
    assert page.unmatched_keys() == set()
    page.deleteLater()


def test_result_page_offers_going_back_to_review(styled_qapp, tmp_path):
    page = ResultPage()
    page.show_result(_result(tmp_path))
    seen = []
    page.back_to_review_requested.connect(lambda: seen.append(True))
    page.review_btn.click()
    assert seen == [True]
    page.deleteLater()


# ── U-06 ──────────────────────────────────────────────────────────────────
def test_new_session_asks_before_discarding_unsaved_result(styled_qapp, tmp_path,
                                                           monkeypatch):
    from aoi_verification.app.ui.widgets import sheet_host as sheets
    from PyQt6.QtWidgets import QMessageBox

    page = ResultPage()
    page.show_result(_result(tmp_path))
    assert page.has_unsaved_result()

    asked = []
    monkeypatch.setattr(sheets, "ask",
                        lambda *a, **k: (asked.append(True),
                                         QMessageBox.StandardButton.No)[1])
    fired = []
    page.new_session_requested.connect(lambda: fired.append(True))
    page._on_new_session()
    assert asked == [True] and fired == [], "확인 없이 결과를 버렸다"

    monkeypatch.setattr(sheets, "ask", lambda *a, **k: QMessageBox.StandardButton.Yes)
    page._on_new_session()
    assert fired == [True]
    page.deleteLater()


def test_saved_result_does_not_ask_again(styled_qapp, tmp_path):
    page = ResultPage()
    page.show_result(_result(tmp_path))
    page._exported = True
    assert not page.has_unsaved_result()
    page.deleteLater()


# ── U-07 / U-16 ───────────────────────────────────────────────────────────
def test_permission_error_gets_an_actionable_message(styled_qapp, tmp_path,
                                                     monkeypatch):
    """OS 원문만 던지면 무엇을 해야 할지 알 수 없다 — 다음 행동을 먼저 말한다."""
    from aoi_verification.app.ui.widgets import sheet_host as sheets
    from aoi_verification.app import i18n

    page = ResultPage()
    page.show_result(_result(tmp_path))
    shown = {}
    monkeypatch.setattr(sheets, "choose",
                        lambda parent, title, body, opts, **k: (
                            shown.update(body=body, opts=opts), "close")[1])
    page._on_export_failed("[Errno 13] Permission denied: 'C:/결과/x.xlsx'")
    assert "엑셀에서 열려 있으면" in shown["body"]
    assert any(o[0] == "retry" for o in shown["opts"]), "[다시 시도] 가 없다"

    page._on_export_failed("디스크 공간 부족")
    assert "엑셀에서 열려 있으면" not in shown["body"], "관계없는 오류에 잘못된 안내"
    page.deleteLater()


def test_save_success_offers_to_open_the_file(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.widgets import sheet_host as sheets

    page = ResultPage()
    page.show_result(_result(tmp_path))
    opened = []
    monkeypatch.setattr(ResultPage, "_open_in_os",
                        staticmethod(lambda p: opened.append(p)))
    monkeypatch.setattr(sheets, "choose", lambda *a, **k: "open_file")
    page._pending_saved_path = tmp_path / "결과.xlsx"
    page._offer_open_saved_file()
    assert opened == [tmp_path / "결과.xlsx"]

    monkeypatch.setattr(sheets, "choose", lambda *a, **k: "open_dir")
    page._offer_open_saved_file()
    assert opened[-1] == tmp_path
    page.deleteLater()


def test_stat_tiles_show_the_key_numbers(styled_qapp, tmp_path):
    """핵심 수치가 문장 속에 묻히지 않고 타일로 나온다(V-07)."""
    from PyQt6.QtWidgets import QLabel
    page = ResultPage()
    page.show_result(_result(tmp_path, n=4, misses=2), coord_mode=True)
    values = [w.text() for w in page.findChildren(QLabel)
              if w.property("role") == "statValue"]
    assert "4" in values, f"매칭 성공 수가 타일에 없다: {values}"
    assert "2" in values, f"매치 없음 수가 타일에 없다: {values}"
    page.deleteLater()
