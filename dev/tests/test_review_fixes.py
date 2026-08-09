"""마일스톤 코드 리뷰에서 나온 결함들을 못 박는다.

전부 이번 60건 작업이 **새로 만든** 결함이라 각각 재발 방지 장치를 남긴다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app.models.result import MatchResult, MissEntry  # noqa: E402
from aoi_verification.app.models.slot import ImageItem, scan           # noqa: E402


# ---------------------------------------------------------------------------
# 1. 확대창 후보 타일이 그려지기만 해도 앱이 죽던 문제
# ---------------------------------------------------------------------------
def test_mid_tile_can_actually_paint(styled_qapp, tmp_path):
    """지연 로드가 `QTimer` 를 import 없이 썼다 — Qt 가상 함수 안의 NameError 는
    예외로 끝나지 않고 **프로세스를 죽인다**.  헤드리스 테스트가 타일을 한 번도
    그리지 않아 아무도 몰랐다.  그리는 것 자체를 시험한다."""
    from aoi_verification.app.ui.widgets import zoom_window as Z

    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"")
    item = ImageItem(slot="S1", path=photo, side="val")
    tile = Z._MidTile(item, parent=None)
    tile.resize(tile.TILE_W, tile.TILE_H)
    tile.grab()                       # paintEvent 를 실제로 태운다
    assert tile._image_loaded, "첫 페인트에서 지연 로드가 걸리지 않았다"
    tile.deleteLater()


# ---------------------------------------------------------------------------
# 2. 결과 → 검토 왕복에서 '매치 없음' 사진이 통째로 사라지던 문제
# ---------------------------------------------------------------------------
def _matches(n: int, tmp_path) -> list[MatchResult]:
    out = []
    for i in range(n):
        ref = tmp_path / f"r{i}.jpg"
        val = tmp_path / f"v{i}.jpg"
        ref.write_bytes(b"")
        val.write_bytes(b"")
        out.append(MatchResult(slot="S1", ref_path=ref, val_path=val,
                               score=0.9))
    return out


def test_all_matches_keeps_the_rows_that_kept_drops(styled_qapp, tmp_path):
    """`finished` 의 kept 에는 '매치 없음' 행이 빠져 있다.

    그걸 기반으로 검토에 되돌아가면 복원할 행이 없어 표시가 사라지고, 다음
    [검토 완료] 에서 그 사진들이 매치에도 미매칭에도 없는 상태가 된다 —
    엑셀에서 통째로 증발한다.  왕복의 기반은 `all_matches()` 여야 한다.
    """
    from aoi_verification.app.ui.pages.match_review_page import MatchReviewPage

    page = MatchReviewPage()
    ms = _matches(3, tmp_path)
    page.load_state(ms)
    page._unmatched_keys = {ms[0].key}          # 한 행을 '매치 없음' 으로

    seen: list = []
    page.finished.connect(lambda kept, un: seen.append((kept, un)))
    page._on_done()
    kept, unmatched = seen[0]

    assert len(kept) == 2, "kept 에 표시한 행이 남아 있다"
    assert len(unmatched) == 1
    assert len(page.all_matches()) == 3, \
        "all_matches 가 '매치 없음' 행을 빠뜨렸다 — 왕복에서 사진이 사라진다"
    page.deleteLater()


def test_round_trip_restores_the_unmatched_marks(styled_qapp, tmp_path):
    """전체 목록 + 키를 주면 표시가 되살아나고, 재완료해도 같은 결과가 나온다."""
    from aoi_verification.app.ui.pages.match_review_page import MatchReviewPage

    page = MatchReviewPage()
    ms = _matches(3, tmp_path)
    page.load_state(ms)
    page._unmatched_keys = {ms[0].key}
    full, keys = page.all_matches(), page.unmatched_keys()

    page.load_state(full, unmatched_keys=keys)   # 결과 → 검토 복귀
    seen: list = []
    page.finished.connect(lambda kept, un: seen.append((kept, un)))
    page._on_done()
    kept, unmatched = seen[0]

    assert len(kept) == 2 and len(unmatched) == 1, \
        f"왕복 뒤 결과가 달라졌다: kept={len(kept)} unmatched={len(unmatched)}"
    page.deleteLater()


# ---------------------------------------------------------------------------
# 3. 좌우 비교 뷰어 — 기준 패널이 영영 원본으로 안 바뀌던 문제
# ---------------------------------------------------------------------------
def test_each_pane_has_its_own_upgrade_token(styled_qapp, tmp_path):
    """세대 번호를 공유하면 기준 패널의 교체가 후보 패널에 밀려 항상 버려진다."""
    from aoi_verification.app.ui.widgets import side_by_side_viewer as S

    ref = tmp_path / "ref.jpg"
    ref.write_bytes(b"")
    v = tmp_path / "v0.jpg"
    v.write_bytes(b"")
    dlg = S.SideBySideViewer(
        ref, [(ImageItem(slot="S1", path=v, side="val"), "90%")], 0)

    # 두 패널을 차례로 올려도 서로의 세대를 밀지 않는다.
    dlg._ref_pane._upgrade_token = 5
    dlg._cand_pane._upgrade_token = 5
    assert dlg._ref_pane is not dlg._cand_pane
    assert not hasattr(dlg, "_upgrade_token"), \
        "세대 번호가 아직 창 단위로 공유되고 있다"
    dlg.close()


def test_viewer_disconnects_loaders_on_close(styled_qapp, tmp_path):
    """WA_DeleteOnClose 인 창이라, 남은 로더가 파괴된 패널을 만지면 죽는다."""
    from aoi_verification.app.ui.widgets import side_by_side_viewer as S

    ref = tmp_path / "ref.jpg"
    ref.write_bytes(b"")
    dlg = S.SideBySideViewer(ref, [], 0)

    class _Sig:
        def __init__(self): self.n = 0
        def disconnect(self, *a): self.n += 1

    class _Loader:
        def __init__(self): self.signals = type("S", (), {"loaded": _Sig()})()

    ld = _Loader()
    dlg._loaders.append((ld, lambda img: None))
    dlg.close()
    assert ld.signals.loaded.n == 1, "닫을 때 로더 연결을 끊지 않았다"


# ---------------------------------------------------------------------------
# 4. 스캔 [중지] 가 실제로는 아무것도 멈추지 못하던 문제
# ---------------------------------------------------------------------------
def test_stop_signal_survives_the_progress_guard(styled_qapp, tmp_path):
    """`slot.scan` 은 진행 콜백을 `except Exception: pass` 로 감싼다.

    중지 신호가 보통 예외면 거기서 **통째로 삼켜져** [중지] 가 무의미해진다.
    """
    import aoi_verification.app.ui.main_window as MW

    assert issubclass(MW._FolderScan._Stopped, BaseException)
    assert not issubclass(MW._FolderScan._Stopped, Exception), \
        "중지 신호가 Exception 이라 scan 의 콜백 가드에 삼켜진다"

    for i in range(4):
        (tmp_path / "ref" / f"S{i}").mkdir(parents=True)
        (tmp_path / "val" / f"S{i}").mkdir(parents=True)

    seen = {"n": 0}

    def _stop_after_one(done, total):
        seen["n"] += 1
        raise MW._FolderScan._Stopped()

    with pytest.raises(MW._FolderScan._Stopped):
        scan(tmp_path / "ref", tmp_path / "val", progress=_stop_after_one)
    assert seen["n"] == 1, "중지 뒤에도 스캔이 계속 돌았다"


# ---------------------------------------------------------------------------
# 5. 저장한 뒤 실패 검토로 결과를 바꿔도 '저장 안 함' 경고가 안 뜨던 문제
# ---------------------------------------------------------------------------
def test_editing_after_export_marks_the_result_unsaved(styled_qapp, tmp_path):
    """실패 검토는 결과 객체를 **제자리에서** 바꾼다 — 객체 교체 기준 리셋에 안 걸린다."""
    from aoi_verification.app.ui.pages.result_page import ResultPage
    from aoi_verification.app.models.result import FinalResult

    page = ResultPage()
    res = FinalResult(mode="single", ref_machine="1호기", val_machine="3호기",
                      matches=_matches(1, tmp_path))
    page.show_result(res)
    page._exported = True

    # ★ 같은 객체로 다시 그리는 것만으로는 리셋되지 않는다(그게 원래 의도다) —
    #   그래서 **제자리 수정을 하는 쪽**이 직접 무효화해야 한다.
    page.show_result(res)
    assert page.has_unsaved_result() is False, \
        "같은 결과를 다시 그렸을 뿐인데 '저장 안 함' 이 됐다"

    # 실패 검토(`_on_review_unmatched`)가 그 무효화를 실제로 하는지 코드로 확인한다.
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "aoi_verification/app/ui/pages/result_page.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_on_review_unmatched")
    assigns = [ast.unparse(n) for n in ast.walk(fn) if isinstance(n, ast.Assign)]
    assert any("_exported = False" in a for a in assigns), \
        "실패 검토가 결과를 바꾸고도 '저장했음' 을 그대로 뒀다"
    page.deleteLater()


# ---------------------------------------------------------------------------
# 6. Stage 1 [← 설정으로] 만 절전 억제를 안 풀던 문제
# ---------------------------------------------------------------------------
def test_every_return_to_setup_releases_the_wakelock() -> None:
    """한 경로만 빠뜨리면 검증을 접었는데 화면보호기가 세션 내내 막힌다."""
    import ast
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[2]
           / "aoi_verification/app/ui/main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_on_select_cancelled")
    calls = {ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)}
    assert "wakelock.release" in calls, \
        "_on_select_cancelled 이 절전 억제를 풀지 않는다"


# ---------------------------------------------------------------------------
# 7. 결과 통계 타일이 세로로 잘려 읽을 수 없던 문제 (검사관 B, V-07)
# ---------------------------------------------------------------------------
def test_stat_tiles_are_not_clipped(styled_qapp, tmp_path):
    """`addWidget(..., alignment=AlignLeft)` 는 위젯을 **sizeHint 크기 그대로** 놓는다.

    그러면 카드가 maximumWidth 까지 늘지 못해(실측 폭 306px) 안쪽 줄바꿈 라벨이
    좁아지고, 줄 수가 늘어난 만큼의 높이는 이미 정해진 뒤라 통계 숫자와 파일
    경로가 세로로 잘렸다(실측 타일 35px / 필요 69px).

    앱이 실제로 만드는 결과 파일 경로는 항상 길어서 이 경로를 늘 탄다.
    """
    from PyQt6.QtWidgets import QFrame, QLabel
    from aoi_verification.app import i18n
    from aoi_verification.app.models.result import FinalResult
    from aoi_verification.app.ui.pages.result_page import ResultPage

    target = (Path("C:/Users/hong/Desktop/AOI_Verify/결과")
              / i18n.KO.RESULT_FILE_TITLE_FMT.format(val="K-6", ref="TB-15"))
    res = FinalResult(mode="single", ref_machine="TB-15", val_machine="K-6",
                      matches=_matches(1, tmp_path),
                      slot_only_ref=["PGEE48", "PGEE49", "PGEE50"],
                      unmatched_refs=[MissEntry(slot="S1", side="ref",
                                                path=tmp_path / "r0.jpg")])
    for w_px, h_px in ((1280, 800), (1600, 950)):
        page = ResultPage()
        page.show_result(res, target_path=target)
        page.resize(w_px, h_px)
        page.show()
        styled_qapp.processEvents()

        clipped = [(l.property("role"), l.text(), l.height(), l.sizeHint().height())
                   for l in page.findChildren(QLabel)
                   if l.property("role") in ("statValue", "statCaption")
                   and l.height() < l.sizeHint().height()]
        tiles = [(f.height(), f.minimumSizeHint().height())
                 for f in page.findChildren(QFrame)
                 if f.property("role") == "statTile"]
        assert not clipped, \
            f"{w_px}x{h_px} 통계 글자가 잘린다: {clipped} / 타일(h,필요)={tiles}"
        # 카드는 여전히 **왼쪽**에 붙어 있어야 한다(가운데로 밀리면 안 된다).
        assert page._summary_card.x() < 80, \
            f"{w_px}x{h_px} 요약 카드가 왼쪽에 붙어 있지 않다 (x={page._summary_card.x()})"
        page.deleteLater()


# ---------------------------------------------------------------------------
# 8. 실패 검토로 확정한 매치가 왕복에서 사라지던 문제 (검사관 A, U-10)
# ---------------------------------------------------------------------------
def test_result_edit_updates_the_round_trip_base(styled_qapp, tmp_path):
    """`_reenter_review` 는 `_reviewed_all_matches` 를 먼저 본다.

    실패 검토로 신규 매치를 확정해도 그 목록을 갱신하지 않으면 **옛 목록이 항상
    이겨** 신규 매치가 검토 화면에 실리지 않고, 재완료 때 결과에서 사라지며 그
    기준 사진이 '미매칭' 으로 되돌아간다 — 사용자가 눈으로 확인해 확정한 것이
    조용히 없어지는 것이다.
    """
    import aoi_verification.app.ui.main_window as MW

    win = MW.MainWindow.__new__(MW.MainWindow)
    base = _matches(3, tmp_path)
    marked = base[2]                       # 한 건은 '매치 없음' 으로 표시돼 있었다
    win._reviewed_matches = [base[0], base[1]]
    win._reviewed_all_matches = list(base)
    win._reviewed_unmatched_keys = {marked.key}
    win._reviewed_unmatched = [MissEntry(slot=marked.slot, side="ref",
                                         path=marked.ref_path)]

    # 실패 검토가 그 표시된 기준 사진에 짝을 찾아 줬다 → 결과가 바뀐다.
    newly = MatchResult(slot=marked.slot, ref_path=marked.ref_path,
                        val_path=tmp_path / "v_new.jpg", score=0.8)
    MW.MainWindow._on_result_edited(win, [base[0], base[1], newly], [])

    keys = {m.key for m in win._reviewed_all_matches}
    assert newly.key in keys, "실패 검토로 확정한 매치가 왕복 기반에 없다"
    assert marked.ref_path not in {
        m.ref_path for m in win._reviewed_all_matches if m.key != newly.key}, \
        "짝을 찾은 기준 사진의 옛 '매치 없음' 행이 남아 중복된다"
    assert win._reviewed_unmatched_keys == set(), \
        "짝을 찾았는데 '매치 없음' 표시가 그대로다"
