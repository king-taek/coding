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
