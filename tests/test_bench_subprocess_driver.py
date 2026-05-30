"""프로세스 격리 드라이버 — 자식 크래시 복원/합본 로직(순수, cv2 불필요).

레시피 '실행'을 자식 프로세스로 돌리는 이유는 OpenVINO/NPU 네이티브 크래시(segfault)가
파이썬 예외/타임아웃으로 안 막혀 앱 전체를 죽이기 때문이다.  여기서는 자식 spawn 을
가짜로 주입해, 드라이버가 (a) 정상 완료, (b) 중간 크래시 → 범인 제외 후 이어서 측정,
(c) 첫 키 즉사 무한루프 방지, (d) 합본 추천 재계산을 올바로 하는지 검증한다.
"""

from __future__ import annotations

from aoi_verification.app.dev import benchmark as bm
from aoi_verification.app.dev import recipes as rx


def _payload(*run_specs, **meta):
    """(key, ok, total_sec, recall1) 들로 result.json payload dict 를 만든다."""
    runs = []
    for key, ok, total, rec in run_specs:
        runs.append({"key": key, "name": key, "ok": ok, "skipped": False,
                     "total_sec": total, "recall1": rec})
    base = {"baseline_key": rx.BASELINE_ACCURACY_KEY,
            "production_key": rx.PRODUCTION_SPEED_KEY,
            "has_ground_truth": True, "devices": ["NPU"], "runs": runs}
    base.update(meta)
    return base


def test_reconstruct_run_ignores_extra_keys():
    run = bm.reconstruct_run({"key": "k", "name": "n", "ok": True,
                              "total_sec": 1.0, "unknown_field": 123})
    assert run.key == "k" and run.ok is True and run.total_sec == 1.0


def test_merge_suite_dedupes_and_prefers_measured():
    # 같은 키가 '스킵'과 '측정'으로 두 번 나오면 측정 쪽을 남긴다.
    p1 = _payload(("cpu_classical_full", True, 200.0, 1.0))
    skipped = bm.RecipeRun(key="cpu_classical_full", name="x", ok=False, skipped=True)
    suite = bm.merge_suite([p1], [skipped])
    runs = {r.key: r for r in suite.runs}
    assert runs["cpu_classical_full"].ok is True
    assert runs["cpu_classical_full"].total_sec == 200.0


def test_driver_normal_completion_single_spawn():
    keys = ["cpu_classical_full", "gpu_fusion_b16"]
    calls = []

    def spawn(ks):
        calls.append(list(ks))
        return bm.ChildOutcome(returncode=0, last_started_key="",
                               payload=_payload(("cpu_classical_full", True, 200.0, 1.0),
                                                ("gpu_fusion_b16", True, 60.0, 0.97)))

    suite = bm.drive_isolated_suite(keys, spawn=spawn)
    assert len(calls) == 1                       # 크래시 없으면 한 번만
    assert {r.key for r in suite.runs} == set(keys)
    assert all(r.ok for r in suite.runs)


def test_driver_recovers_from_midrun_crash():
    keys = ["cpu_classical_full", "gpu_fusion_b16", "npu_mbnet_cpu_fuse",
            "rr_parallel"]
    calls = []

    def spawn(ks):
        calls.append(list(ks))
        if "npu_mbnet_cpu_fuse" in ks:
            # npu 에서 크래시 — 그 전(cpu_classical_full)까지는 부분 저장됨.
            return bm.ChildOutcome(
                returncode=-11, last_started_key="npu_mbnet_cpu_fuse",
                payload=_payload(("cpu_classical_full", True, 200.0, 1.0)))
        # 재실행: 남은 키(npu 제외) 정상 완료.
        runs = [("cpu_classical_full", True, 200.0, 1.0)]
        for k in ks:
            if k != "cpu_classical_full":
                runs.append((k, True, 50.0, 0.97))
        return bm.ChildOutcome(returncode=0, last_started_key="",
                               payload=_payload(*runs))

    suite = bm.drive_isolated_suite(keys, spawn=spawn)
    runs = {r.key: r for r in suite.runs}
    # 크래시 범인은 실패로 기록되고, 나머지는 측정된다.
    assert runs["npu_mbnet_cpu_fuse"].ok is False
    assert "종료" in runs["npu_mbnet_cpu_fuse"].note
    assert runs["gpu_fusion_b16"].ok is True
    assert runs["rr_parallel"].ok is True
    # 재실행 시 범인과 이미 끝난 baseline 은 빠진다.
    assert calls[1] == ["gpu_fusion_b16", "rr_parallel"]


def test_driver_handles_first_key_insta_crash_without_infinite_loop():
    keys = ["a", "b", "c"]
    calls = []

    def spawn(ks):
        calls.append(list(ks))
        # 항상 첫 키에서 즉사 + 부분 저장 없음(payload None).
        return bm.ChildOutcome(returncode=-6, last_started_key=ks[0], payload=None)

    suite = bm.drive_isolated_suite(keys, spawn=spawn, max_respawns=10)
    # 무한루프 없이 모든 키를 하나씩 떨궈가며 끝난다(전부 크래시 기록).
    assert len(calls) <= 10
    assert {r.key for r in suite.runs} == set(keys)
    assert all(not r.ok for r in suite.runs)


def test_driver_stops_on_request():
    def spawn(ks):
        raise AssertionError("stop 이면 spawn 하지 않아야 한다")

    suite = bm.drive_isolated_suite(["a", "b"], spawn=spawn, stop=lambda: True)
    assert suite.runs == []
