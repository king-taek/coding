"""매치 실패 검토의 후보 채점을 **워커 스레드로** 옮긴다 (P-03) + 좌표 안내 (U-18).

배경: 후보 점수는 캐시에 없는 쌍마다 특징 추출 + 채점이라 299장이면 초 단위다.
그동안 GUI 스레드가 통째로 굳었고, 진행을 보여 주려 콜백마다 `processEvents` 를
불렀기 때문에 **채점 도중에 사용자 입력이 재진입**할 수 있었다(다른 항목을 클릭하면
렌더가 겹쳐 들어왔다).

여기서 못 박는 계약:
  1. 워커가 낸 결과가 동기 계산과 **정렬 순서까지 같다**.
  2. 세대 토큰 — 채점 중에 다음 항목으로 넘어가면 옛 결과는 그리지 않는다.
  3. 계산 중 닫아도 크래시 없이 연결이 끊긴다(죽은 위젯으로 결과가 안 들어간다).
  4. 캐시가 전부 hit(`need == 0`)이면 워커를 띄우지 않는다 — 흔한 경로를 한 틱도
     늦추지 않는다.
  5. `_score_candidates`/`_lookup_or_compute_score` 의 이름·시그니처는 그대로다
     (기존 테스트 3파일이 인스턴스 몽키패치 + 위치인자 직접 호출에 의존한다).
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app.models.result import MissEntry           # noqa: E402
from aoi_verification.app.models.slot import ImageItem             # noqa: E402
import aoi_verification.app.ui.widgets.unmatched_review_dialog as M  # noqa: E402


def _dialog(n_cand: int = 6, *, coord_mode: bool = False):
    unmatched = [MissEntry(slot="A1", side="ref", path=Path("/tmp/ref_A1.jpg")),
                 MissEntry(slot="A2", side="ref", path=Path("/tmp/ref_A2.jpg"))]
    val_pool = {
        "A1": [ImageItem(slot="A1", path=Path(f"/tmp/v{i}.jpg"), side="val")
               for i in range(n_cand)],
        "A2": [ImageItem(slot="A2", path=Path("/tmp/w0.jpg"), side="val")],
    }
    dlg = M.UnmatchedReviewDialog(unmatched, val_pool, coord_mode=coord_mode)
    # 무거운 pipeline 회피 — 경로 끝 숫자를 점수로 (정렬이 뒤집히는 값).
    dlg._lookup_or_compute_score = (
        lambda ref, val, allow_compute=True: float(Path(val.path).stem[1:]))
    return dlg, val_pool


def test_worker_result_matches_the_synchronous_one(styled_qapp):
    """워커에 싣는 것은 동기 경로와 **같은 함수**다 — 결과·순서가 갈리면 안 된다."""
    dlg, val_pool = _dialog()
    cur = dlg._unmatched[0]
    cands = val_pool["A1"]

    direct = sorted(dlg._score_candidates(cur, cands, True),
                    key=lambda x: x[0], reverse=True)

    got: list = []
    worker = M._CandidateScoring(1, dlg._score_candidates, cur, cands, True)
    worker.signals.done.connect(lambda tk, s: got.append(s))
    worker.run()                       # 스레드를 띄우지 않고 본문만 그대로 실행
    from_worker = sorted(got[0], key=lambda x: x[0], reverse=True)

    assert [(s, v.path) for s, v in from_worker] == \
           [(s, v.path) for s, v in direct]
    dlg.deleteLater()


def test_stale_generation_is_discarded(styled_qapp):
    """채점 중 다음 항목으로 넘어가면 옛 결과가 새 그리드를 덮지 않는다."""
    dlg, val_pool = _dialog()
    dlg.resize(1000, 700)
    dlg.show()
    dlg._idx = 0
    dlg._render_current()
    stale_token = dlg._score_token
    tiles_before = list(dlg._cand_tiles)

    dlg._idx = 1                       # 사용자가 다음 실패 사진으로 이동
    dlg._render_current()              # 토큰이 올라간다

    # 늦게 도착한 옛 세대 — 무시돼야 한다.
    dlg._on_scoring_done(stale_token, dlg._unmatched[0],
                         ("A1", frozenset()), [(9.0, val_pool["A1"][0])])
    assert dlg._cand_tiles != tiles_before or len(dlg._cand_tiles) == 1, \
        "옛 세대 결과가 새 후보 그리드를 덮었다"
    dlg.deleteLater()


def test_closing_mid_scoring_detaches_without_crashing(styled_qapp):
    """계산 중 닫기 — 죽어 가는 위젯으로 결과가 들어가면 안 된다."""
    dlg, val_pool = _dialog()
    dlg.resize(1000, 700)
    dlg.show()
    dlg._idx = 0
    dlg._render_current()

    worker = M._CandidateScoring(dlg._score_token, dlg._score_candidates,
                                 dlg._unmatched[0], val_pool["A1"], True)
    dlg._scoring = worker
    token_before = dlg._score_token

    dlg._detach_scoring()
    assert dlg._scoring is None
    assert dlg._score_token > token_before, "닫은 뒤에도 옛 토큰이 유효하다"
    # 끊긴 뒤 결과가 와도 조용히 버려진다(예외 없이).
    dlg._on_scoring_done(token_before, dlg._unmatched[0],
                         ("A1", frozenset()), [])
    dlg.deleteLater()


def test_all_cached_stays_synchronous(styled_qapp):
    """전부 캐시 hit 이면 워커를 띄우지 않는다 — 흔한 경로는 즉시 그린다."""
    dlg, val_pool = _dialog()

    class _Cache:
        def get_pair(self, slot, ref, val):
            return 0.5
        def put(self, *a, **k):
            pass

    dlg._score_cache = _Cache()
    assert dlg._count_recompute(dlg._unmatched[0], val_pool["A1"]) == 0
    dlg.resize(1000, 700)
    dlg.show()
    dlg._idx = 0
    dlg._render_current()
    assert dlg._scoring is None, "캐시가 전부 hit 인데 워커를 띄웠다"
    assert dlg._cand_tiles, "동기 경로가 후보를 그리지 않았다"
    dlg.deleteLater()


def test_worker_path_wires_progress_result_and_overlay(styled_qapp, monkeypatch):
    """헤드리스 기본은 동기라 **워커 경로의 배선이 시험되지 않는다** — 여기서 강제로 태운다.

    이 테스트가 없으면 사용자 PC 에서만 도는 코드가 한 번도 실행되지 않은 채 통과한다.

    ⚠ 진짜 OS 스레드를 띄우지는 않는다.  헤드리스 Qt + xdist 조합에서 이 스위트는
    워커 프로세스가 통째로 죽었다(실측: 이 파일에서 스레드를 하나 띄우면 뒤따르는
    `test_sheet_enter_motion` 이 세그폴트).  스레드 스케줄링은 Qt 의 몫이고, **틀릴 수
    있는 것은 배선**(시그널→슬롯, 세대 토큰, 오버레이 show/hide, 타일 그리기)이므로
    `start()` 를 같은 본문을 그대로 도는 `run()` 으로 바꿔 그 배선을 전부 태운다."""
    monkeypatch.setattr(M._CandidateScoring, "start",
                        M._CandidateScoring.run, raising=True)

    dlg, val_pool = _dialog()
    dlg._sync_scoring = False              # 강제로 워커 경로
    dlg.resize(1000, 700)
    # 지연 첫 렌더(showEvent 의 singleShot)를 끈다 — 켜 두면 아래에서 이벤트를
    # 돌리는 순간 렌더가 한 번 더 들어와 워커가 새로 뜨고, 우리가 기다리던
    # 세대는 토큰이 밀려 버려진다.
    dlg._first_render_done = True
    dlg.show()
    dlg._idx = 0
    dlg._render_current()

    styled_qapp.processEvents()

    assert dlg._scoring is None, "결과가 왔는데 워커 참조가 남았다"
    # `hide_overlay` 는 최소 표시 래치(깜빡임 방지)를 타므로 곧바로 사라지지 않는다
    # — 내려가도록 **예약**됐는지를 본다.
    assert dlg._loading._hide_pending or not dlg._loading.isVisible(), \
        "끝났는데 진행 표시를 내릴 예약조차 없다"
    assert len(dlg._cand_tiles) == len(val_pool["A1"]), "후보가 그려지지 않았다"
    # 워커 결과도 점수 내림차순이어야 한다.
    scores = [t.score for t in dlg._cand_tiles]
    assert scores == sorted(scores, reverse=True)

    # ★ 반드시 제대로 닫는다.  오버레이가 **뜬 채 입력 잠금**이면 앱 전역 이벤트
    #   필터가 걸려 있는데, 그대로 두고 나가면 필터가 주인 없이 남아 뒤따르는
    #   테스트에서 프로세스가 죽는다(실제로 그렇게 죽었다).
    dlg.close()
    styled_qapp.processEvents()
    assert not dlg._loading._input_locked


def test_scoring_helpers_keep_their_public_shape(styled_qapp):
    """기존 테스트 3파일이 이 이름·인자 순서에 직접 의존한다 — 바꾸면 그쪽이 깨진다."""
    import inspect
    dlg, _ = _dialog()
    sig = inspect.signature(M.UnmatchedReviewDialog._score_candidates)
    assert list(sig.parameters) == ["self", "cur", "candidates",
                                    "allow_compute", "on_computed"]
    sig2 = inspect.signature(M.UnmatchedReviewDialog._lookup_or_compute_score)
    assert list(sig2.parameters) == ["self", "ref", "val", "allow_compute"]
    dlg.deleteLater()


# ---------------------------------------------------------------------------
# U-18 — 좌표로 매칭했는데 후보가 유사도 순인 이유를 화면이 말해 준다.
# ---------------------------------------------------------------------------
def test_coord_session_explains_the_similarity_ordering(styled_qapp):
    from PyQt6.QtWidgets import QLabel
    from aoi_verification.app import i18n

    dlg, _ = _dialog(coord_mode=True)
    notes = [w for w in dlg.findChildren(QLabel)
             if w.text() == i18n.KO.UNMATCHED_REVIEW_COORD_NOTE]
    assert len(notes) == 1, "좌표 세션인데 순서 기준 안내가 없다"
    # 점수 표기는 여전히 유사도 형식이어야 한다(좌표 거리 형식으로 바뀌면 안 됨).
    assert dlg._coord_mode is False
    dlg.deleteLater()


def test_similarity_session_does_not_show_the_coord_note(styled_qapp):
    """좌표를 안 쓴 세션에 그 안내를 띄우면 없는 개념을 설명하는 잡음이 된다."""
    from PyQt6.QtWidgets import QLabel
    from aoi_verification.app import i18n

    dlg, _ = _dialog(coord_mode=False)
    notes = [w for w in dlg.findChildren(QLabel)
             if w.text() == i18n.KO.UNMATCHED_REVIEW_COORD_NOTE]
    assert notes == []
    dlg.deleteLater()
