"""기준 1 ↔ 검증 여러 장비 — `MainWindow` 의 흐름 조각(믹스인).

규칙·결정은 `models/multi_val.py` docstring.  여기서는 **구간(leg) 바꿔 끼우기**만 한다:

  · 구간 = 검증 장비 하나.  창의 실행 상태(`_LEG_ATTRS`)를 구간마다 따로 들고 있다가
    필요한 구간을 창에 다시 끼운다(`_load_leg`).  그래서 스캔·KLA 해석·좌표 보정·매칭·
    교차검증 2차·결과 편집은 **단일 실행 코드를 그대로** 탄다 — 장비 수만큼 두 벌 만들지
    않는다.
  · 흐름: 구간 1 [스캔 → 선별 → 매칭] → 구간 2 [스캔 → (선별 재사용) → 매칭] → …
    → 합친 검토 1번 → (교차검증이면 구간마다 2차) → 결과(장비 버튼) → 엑셀 1개.
  · MatchPage 는 하나뿐이고 다음 구간을 실으면 앞 구간의 선계산이 지워진다 — 그래서
    매칭이 끝날 때마다 검토·결과에 필요한 것을 구간에 떠 둔다(`_snapshot_leg_ctx`).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import replace
from types import SimpleNamespace

from .. import i18n
from ..models import multi_val

_LOG = logging.getLogger("aoi.ui")

# 구간마다 따로인 창 상태.  ★ 새 실행 상태를 창에 추가했고 그것이 (기준, 검증) 쌍에
#   매인 값이면 여기에도 적는다 — 안 적으면 다음 구간이 앞 구간의 값을 물려받는다.
_LEG_ATTRS = (
    "_input", "_scan", "_kla_folders", "_slot_meta_ref", "_slot_meta_val",
    "_kla_broken", "_coord_offset", "_calib_side",
    "_matches_a", "_skipped_a", "_reviewed_matches", "_a_reviewed",
    "_reviewed_unmatched", "_reviewed_unmatched_keys", "_reviewed_all_matches",
    "_stage1_a_snapshot",
    "_cross_stage", "_ctx_a", "_matches_b", "_skipped_b", "_b_reviewed",
    "_b_kept", "_b_misses", "_b_all", "_b_unmatched_keys",
    "_leg_ctx",
)


class MultiValFlow:
    """`MainWindow` 에 섞는다.  단일 실행(검증 장비 1대)이면 아무것도 하지 않는다."""

    # 클래스 기본값 — `_on_start` 전에 읽혀도 안전하게(`_cross_stage` 와 같은 방식).
    _legs: list = []
    _leg_idx = 0
    _leg_ctx = None
    _multi_result_idx = 0
    _multi_showing_result = False
    _leg_detached = False             # 창 상태가 뺀 구간의 것 — 보관하지 않는다

    # ------------------------------------------------------------------
    def _multi(self) -> bool:
        return len(self._legs) > 1

    def _init_legs(self, inp) -> None:
        """실행 시작 — 추가 검증 장비가 있으면 구간을 만든다(없으면 비운다)."""
        extra = list(getattr(inp, "extra_vals", None) or [])
        self._leg_idx = 0
        self._leg_ctx = None
        self._leg_detached = False
        self._multi_result_idx = 0
        if not extra or getattr(inp, "extract", False):
            self._legs = []
            return
        inputs = [inp] + [replace(inp, val_root=root, val_machine=machine,
                                  extra_vals=None)
                          for root, machine in extra]
        self._legs = [{"input": li, "state": None, "b_done": False}
                      for li in inputs]
        _LOG.info("검증 장비 %d대: %s", len(inputs),
                  ", ".join(li.val_machine for li in inputs))

    def _leg_labels(self) -> list[str]:
        return [leg["input"].val_machine for leg in self._legs]

    def _leg_message(self, msg: str) -> str:
        """로딩 문구 앞에 '검증 장비 2/3 (AOI-5)' — 여러 장비일 때만."""
        if not self._multi():
            return msg
        return i18n.KO.MULTI_VAL_LEG_FMT.format(
            n=self._leg_idx + 1, total=len(self._legs),
            machine=self._legs[self._leg_idx]["input"].val_machine, msg=msg)

    # -- 구간 바꿔 끼우기 ------------------------------------------------
    def _save_leg(self) -> None:
        if self._leg_detached:
            return
        if self._legs:
            self._legs[self._leg_idx]["state"] = {
                a: getattr(self, a, None) for a in _LEG_ATTRS}

    def _load_leg(self, i: int) -> None:
        """구간 ``i`` 를 창에 끼운다(지금 구간은 먼저 보관)."""
        if (i == self._leg_idx and not self._leg_detached
                and self._legs[i]["state"] is None):
            return
        self._save_leg()
        state = self._legs[i]["state"]
        self._leg_idx = i
        self._leg_detached = False
        for a, v in state.items():
            setattr(self, a, v)

    def _fresh_leg_state(self) -> None:
        """새 구간 — 쌍에 매인 상태를 **새 객체로** 비운다.

        ★ `.clear()` 로 비우면 보관해 둔 앞 구간의 목록까지 같이 비워진다(같은 객체)."""
        self._scan = None
        self._kla_folders = {}
        self._slot_meta_ref = None
        self._slot_meta_val = None
        self._kla_broken = []
        self._coord_offset = None
        self._calib_side = None
        self._matches_a = []
        self._skipped_a = defaultdict(list)
        self._reviewed_matches = []
        self._a_reviewed = False
        self._reviewed_unmatched = []
        self._reviewed_unmatched_keys = set()
        self._reviewed_all_matches = []
        self._stage1_a_snapshot = None
        self._leg_ctx = None
        cross = self._cross_stage           # 교차검증 여부는 실행 전체가 같다
        self._reset_cross_state()
        self._cross_stage = "A" if cross else ""

    # -- 매칭이 끝났다 → 다음 구간 또는 합친 검토 -------------------------
    def _snapshot_leg_ctx(self) -> None:
        """MatchPage 의 선계산을 구간에 떠 둔다(다음 구간이 MatchPage 를 덮는다)."""
        mp = self._match_page
        ctx = mp.review_context()
        st = mp.get_state()
        base = (list(self._matches_b) if self._cross_stage == "B"
                else self._merge_matches())
        try:
            cbr = mp.build_candidates_by_ref(base)
        except Exception:
            _LOG.warning("구간 후보 목록을 만들지 못했다", exc_info=True)
            cbr = None
        self._leg_ctx = SimpleNamespace(
            ctx=replace(ctx, fast_results=dict(ctx.fast_results)),
            val_pool=({k: list(v) for k, v in st.val_pool.items()}
                      if st is not None else {}),
            candidates_by_ref=cbr,
        )

    def _review_ctx(self):
        """검토·결과가 쓰는 맥락 — 여러 장비면 끼워진 구간의 사본, 아니면 MatchPage."""
        if self._multi() and self._leg_ctx is not None:
            return self._leg_ctx.ctx
        return self._match_page.review_context()

    def _multi_after_match(self) -> None:
        """구간의 1차 매칭 완료 — 떠 두고 다음 구간을 시작하거나, 다 했으면 합친 검토."""
        self._snapshot_leg_ctx()
        self._save_leg()
        nxt = self._leg_idx + 1
        if nxt < len(self._legs):
            self._start_next_leg(nxt)
            return
        self._show_multi_review()

    def _start_next_leg(self, i: int) -> None:
        self._save_leg()
        self._leg_idx = i
        self._leg_detached = False
        self._fresh_leg_state()
        self._input = self._legs[i]["input"]
        self._thumbs_handled = False
        self._launch_scan(self._input)

    def _multi_leg_targets(self):
        """둘째 구간부터 — 첫 구간에서 고른 기준 사진을 이 구간의 선별 결과로."""
        first = self._legs[0]["state"] or {}
        snap = first.get("_stage1_a_snapshot") or {}
        paths = [it.path for items in (snap.get("targets") or {}).values()
                 for it in items]
        sr = self._scan
        return multi_val.leg_targets(paths, sr.slots, sr.common_slot_names)

    def _multi_skip_select(self) -> bool:
        """둘째 구간부터는 선별을 다시 하지 않는다 — 처리했으면 True."""
        if not self._multi() or self._leg_idx == 0:
            return False
        targets = self._multi_leg_targets()
        self._stage1_a_snapshot = {"targets": targets, "excluded": {}}
        if not any(targets.values()):
            # 이 장비와 짝지은 슬롯에 고른 기준 사진이 없다 — 매칭할 것이 없는 구간.
            _LOG.info("검증 장비 %s: 매칭할 기준 사진 없음",
                      self._input.val_machine)
            self._leg_ctx = None
            self._multi_after_match_empty()
            return True
        self._enter_stage2_phase_a()
        return True

    def _multi_after_match_empty(self) -> None:
        """매칭할 것이 없는 구간을 넘긴다.

        스캔이 실패했거나 짝지은 슬롯이 없으면 결과에 실을 것도 없어 구간을 **뺀다**
        (결과 화면·엑셀에 그 장비가 안 나온다 — 그 사실은 방금 경고로 알렸다).
        고른 기준 사진만 없는 구간은 남긴다(슬롯 짝 정보는 결과에 있다)."""
        sr = self._scan
        if sr is None or not sr.common_slot_names:
            _LOG.info("검증 장비 %s 를 이번 실행에서 뺀다(스캔 실패 또는 짝 슬롯 없음)",
                      self._input.val_machine)
            nxt = self._leg_idx            # 뒤 구간이 이 자리로 당겨진다
            del self._legs[nxt]
            # ★ 창에 끼워진 상태는 이제 어느 구간 것도 아니다 — 보관하면 남의 자리를 덮는다.
            self._leg_detached = True
            if nxt < len(self._legs):
                self._start_next_leg(nxt)
            else:
                self._show_multi_review()
            return
        self._save_leg()
        nxt = self._leg_idx + 1
        if nxt < len(self._legs):
            self._start_next_leg(nxt)
        else:
            self._show_multi_review()

    # -- 합친 검토 ------------------------------------------------------
    def _multi_review_inputs(self):
        """(태그 붙은 매치, lanes, 표시할 '매치 없음' 키, 실패 수 합)."""
        matches, lanes, marked = [], [], set()
        failed = 0
        for k, leg in enumerate(self._legs):
            st = leg["state"] or {}
            lc = st.get("_leg_ctx")
            if st.get("_a_reviewed"):
                base = list(st.get("_reviewed_all_matches") or [])
                keys = st.get("_reviewed_unmatched_keys") or set()
            else:
                base = list(st.get("_matches_a") or [])
                keys = set()
            tagged = multi_val.tag(base, k)
            matches.extend(tagged)
            marked.update(t.key for t, m in zip(tagged, base) if m.key in keys)
            missing = {it.path for items in (st.get("_skipped_a") or {}).values()
                       for it in items}
            lanes.append({
                "label": leg["input"].val_machine,
                "val_pool": lc.val_pool if lc else {},
                # 키가 (슬롯, 기준 파일명)이라 장비 번호가 필요 없다 — 장비별 lane 이 가른다.
                "candidates_by_ref": lc.candidates_by_ref if lc else None,
                "missing": missing,
            })
            if lc is not None:
                failed += lc.ctx.coord_failed_count
        return matches, lanes, marked, failed

    def _show_multi_review(self) -> None:
        """구간 전부의 1차 매칭 → 검토 화면 한 번(기준 사진 1장 + 장비별 줄)."""
        self._save_leg()
        self._load_leg(0)
        matches, lanes, marked, failed = self._multi_review_inputs()
        ctx = next((leg["state"]["_leg_ctx"].ctx for leg in self._legs
                    if leg["state"] and leg["state"].get("_leg_ctx")), None)
        if ctx is None:
            ctx = self._match_page.review_context()
        self._match_review_page.set_reverse(False)
        self._match_review_page.load_state(
            matches, score_cache=ctx.score_cache, val_pool=None,
            review_scores=ctx.review_scores, candidates_by_ref=None,
            coord_mode=ctx.coord_mode, tolerance=ctx.tolerance,
            coord_failed_count=failed, unmatched_keys=marked, lanes=lanes)
        self._notify_ready_for_review()
        self._show_page(self._match_review_page)

    def _multi_review_done(self, kept: list, unmatched: list) -> None:
        """합친 검토 완료 → 구간마다 단일 실행과 같은 자리에 나눠 담는다."""
        page = self._match_review_page
        all_rows = page.all_matches()
        marked = page.unmatched_keys()
        parts = multi_val.split(kept, unmatched, len(self._legs))
        for k, (k_kept, k_miss) in enumerate(parts):
            self._load_leg(k)
            rows = [m for m in all_rows if m.equip == k]
            self._reviewed_matches = k_kept
            self._a_reviewed = True
            self._reviewed_unmatched = k_miss
            self._reviewed_unmatched_keys = {
                replace(m, equip=0).key for m in rows if m.key in marked}
            self._reviewed_all_matches = [replace(m, equip=0) for m in rows]
            self._legs[k]["b_done"] = False
        self._save_leg()
        if self._cross_stage:
            self._multi_next_cross_b()
            return
        self._multi_show_result(0)

    # -- 교차검증 2차 — 구간마다 한 번씩 ---------------------------------
    def _multi_next_cross_b(self) -> None:
        for k, leg in enumerate(self._legs):
            if not leg["b_done"]:
                self._load_leg(k)
                self._enter_cross_b()
                return
        self._multi_show_result(0)

    def _multi_leg_finished(self) -> bool:
        """`_finish_session` 입구 — 여러 장비면 다음 2차 또는 결과로 돌리고 True."""
        if not self._multi() or self._multi_showing_result:
            return False
        if self._cross_stage:
            self._legs[self._leg_idx]["b_done"] = True
            self._save_leg()
            if not all(leg["b_done"] for leg in self._legs):
                self._multi_next_cross_b()
                return True
        self._multi_show_result(self._multi_result_idx)
        return True

    # -- 결과 ------------------------------------------------------------
    def _multi_show_result(self, i: int) -> None:
        """결과 화면에 장비 ``i`` 를 싣는다(위 장비 버튼이 이걸 부른다)."""
        self._load_leg(i)
        self._multi_result_idx = i
        self._multi_showing_result = True
        try:
            self._finish_session()
        finally:
            self._multi_showing_result = False
        self._result_page.set_equipments(self._leg_labels(), i,
                                         self._multi_all_results)

    def _on_equip_requested(self, i: int) -> None:
        """결과 화면의 장비 버튼 — 저장 여부·경로는 장비를 바꿔도 이어 간다(파일 하나)."""
        if not self._multi() or not (0 <= i < len(self._legs)):
            return
        kept = self._result_page.export_state()
        self._multi_show_result(i)
        self._result_page.restore_export_state(kept)

    def _multi_all_results(self) -> list:
        """엑셀 저장용 — 장비마다의 FinalResult(결과 화면의 편집 반영)."""
        cur = self._leg_idx
        out = []
        for k in range(len(self._legs)):
            self._load_leg(k)
            out.append(self._build_final_result())
        self._load_leg(cur)
        return out

    def _multi_reenter_review(self) -> bool:
        """결과 → 검토 복귀.  여러 장비면 True(처리함).

        교차검증이 아니면 합친 검토로, 교차검증이면 지금 보는 장비의 2차 검토로 간다
        (단일 실행과 같은 규칙 — 1차 검토는 2차가 MatchPage 를 덮어 다시 못 연다)."""
        if not self._multi():
            return False
        if not self._cross_stage:
            self._show_multi_review()
            return True
        lc = self._leg_ctx
        if self._b_reviewed:
            from ..models import cross_check
            base = list(self._b_all or [cross_check.swap(m) for m in self._b_kept])
        else:
            base = list(self._matches_b)
        ctx = self._review_ctx()
        self._match_review_page.set_reverse(True)
        self._match_review_page.load_state(
            base, score_cache=ctx.score_cache,
            val_pool=lc.val_pool if lc else None,
            review_scores=ctx.review_scores,
            candidates_by_ref=lc.candidates_by_ref if lc else None,
            coord_mode=ctx.coord_mode, tolerance=ctx.tolerance,
            coord_failed_count=ctx.coord_failed_count,
            unmatched_keys=self._b_unmatched_keys)
        self._legs[self._leg_idx]["b_done"] = False
        self._show_page(self._match_review_page)
        return True
