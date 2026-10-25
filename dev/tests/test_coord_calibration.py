"""KLA↔Camtek 좌표 보정(`coords.calibration`) — 순수 로직 + 매칭 반영 검증."""

from __future__ import annotations

from pathlib import Path

import pytest

from aoi_verification.app.coords import calibration as cal
from aoi_verification.app.coords.models import DefectCoord


def _k(col, row, x, y):
    return DefectCoord(col=col, row=row, x=x, y=y, source="kla",
                       native_x=x + 1, native_y=y + 2)


def _c(col, row, x, y):
    return DefectCoord(col=col, row=row, x=x, y=y, source="camtek_ini")


def test_pair_offset_is_cam_minus_kla():
    assert cal.pair_offset(_k(1, 1, 100, 200), _c(1, 1, 112, 82)) == (12, -118)


def test_estimate_uses_median_so_one_bad_pair_does_not_drag():
    pairs = [(10, -120), (12, -118), (11, -119), (900, 900)]
    dx, dy = cal.estimate_offset(pairs)
    assert 10 <= dx <= 12 + 1e-9 and -120 <= dy <= -118
    assert cal.estimate_offset([]) == cal.ZERO


def test_residuals():
    assert cal.residuals([(3, 4), (0, 0)], (0, 0)) == [5.0, 0.0]


def test_apply_offset_shifts_only_kla_and_leaves_input_untouched():
    k = _k(2, 3, 100.0, 200.0)
    c = _c(2, 3, 50.0, 60.0)
    src = {"k": k, "c": c, "none": None}
    out = cal.apply_offset(src, (10.0, -20.0))
    assert out["k"].x == 110.0 and out["k"].y == 180.0
    assert (out["k"].col, out["k"].row) == (2, 3)
    # KLA 원본값(엑셀 표기용)은 그대로
    assert (out["k"].native_x, out["k"].native_y) == (101.0, 202.0)
    assert out["c"] is c and out["none"] is None
    # 입력 dict·객체는 바뀌지 않는다 — 보정이 엑셀·화면 좌표로 새지 않는다
    assert src["k"] is k and k.x == 100.0


def test_apply_zero_offset_is_identity_copy():
    src = {"k": _k(0, 0, 1, 2)}
    out = cal.apply_offset(src, (0.0, 0.0))
    assert out == src and out is not src


def test_guess_offset_finds_shared_shift_among_noise():
    shift = (15.0, -120.0)
    kla = [_k(i % 3, i // 3, 1000.0 + 300 * i, 2000.0 + 170 * i) for i in range(8)]
    cam = [_c(k.col, k.row, k.x + shift[0], k.y + shift[1]) for k in kla]
    # 우연한 이웃(흩어진 어긋남)
    cam += [_c(0, 0, 1400.0, 1700.0), _c(1, 0, 1800.0, 2900.0)]
    g = cal.guess_offset([(kla, cam)], src_is_kla=True)
    assert g is not None
    assert abs(g[0] - shift[0]) < 1e-6 and abs(g[1] - shift[1]) < 1e-6
    # 기준이 Camtek 이어도 부호 규약(cam − kla)은 같다
    g2 = cal.guess_offset([(cam[:8], kla)], src_is_kla=False)
    assert abs(g2[0] - shift[0]) < 1e-6 and abs(g2[1] - shift[1]) < 1e-6


def test_guess_offset_none_without_votes():
    assert cal.guess_offset([([_k(0, 0, 0, 0)], [])], src_is_kla=True) is None


def _shifted(n, shift, *, start=0):
    # 1000 µm(후보 반경)보다 멀리 떨어뜨린다 — 서로 남의 짝으로 표를 주지 않게.
    kla = [_k(i % 3, i // 3, 1000.0 + 1500 * i, 2000.0 + 1100 * i) for i in range(start, start + n)]
    return kla, [_c(k.col, k.row, k.x + shift[0], k.y + shift[1]) for k in kla]


def test_auto_offset_is_confident_with_enough_agreeing_pairs():
    kla, cam = _shifted(10, (87.0, -91.0))
    a = cal.auto_offset([(kla, cam)], src_is_kla=True)
    assert a.offset == pytest.approx((87.0, -91.0))
    assert a.support == 10 and a.runner_up == 0
    assert cal.is_confident(a)


def test_auto_offset_is_not_confident_with_few_pairs():
    """근거가 적으면(실측: 한 웨이퍼 짝 2~5개) 자동값을 내밀지 않는다."""
    kla, cam = _shifted(cal.MIN_AUTO_SUPPORT - 1, (87.0, -91.0))
    a = cal.auto_offset([(kla, cam)], src_is_kla=True)
    assert a is not None and not cal.is_confident(a)


def test_auto_offset_is_not_confident_when_a_rival_cluster_is_close_in_size():
    """결함이 몰린 웨이퍼처럼 다른 어긋남 무리가 비슷하게 크면 믿지 않는다
    (실측 31266524EWG5: (117, −72) 를 5표 대 3표로 냈다)."""
    kla, cam = _shifted(10, (87.0, -91.0))
    k2, c2 = _shifted(6, (300.0, 250.0), start=20)
    a = cal.auto_offset([(kla + k2, cam + c2)], src_is_kla=True)
    assert a.offset == pytest.approx((87.0, -91.0))
    assert a.runner_up == 6
    assert not cal.is_confident(a)


def test_auto_offset_sees_a_rival_just_outside_the_support_radius():
    """30~60 µm 떨어진 무리도 경쟁자로 센다(예전엔 그 구간이 빈칸이라 못 봤다 — 검토 지적)."""
    kla, cam = _shifted(10, (87.0, -91.0))
    k2, c2 = _shifted(9, (87.0 + 45.0, -91.0), start=20)
    a = cal.auto_offset([(kla + k2, cam + c2)], src_is_kla=True)
    assert not cal.is_confident(a)


def test_support_counts_distinct_defects_not_votes():
    """한 결함이 이웃 여럿에 표를 던져도 근거는 1개다(결함 무리·긁힘)."""
    k = _k(1, 1, 5000.0, 5000.0)
    cams = [_c(1, 1, 5000.0 + 87 + i, 5000.0 - 91) for i in range(10)]   # 같은 어긋남 10표
    a = cal.auto_offset([([k], cams)], src_is_kla=True)
    assert a.votes == 10 and a.support == 1
    assert not cal.is_confident(a)


def test_exactly_twice_the_rival_is_not_enough():
    assert not cal.is_confident(cal.AutoOffset((0.0, 0.0), support=8, runner_up=4, votes=12))
    assert cal.is_confident(cal.AutoOffset((0.0, 0.0), support=9, runner_up=4, votes=13))


def test_auto_offset_accepts_a_one_shot_iterable():
    kla, cam = _shifted(10, (87.0, -91.0))
    a = cal.auto_offset(iter([(kla, cam)]), src_is_kla=True)
    assert a is not None and a.support == 10


def test_is_confident_none():
    assert not cal.is_confident(None)


def test_near_candidates_orders_by_hint_and_reports_raw_distance():
    k = _k(5, 5, 1000.0, 1000.0)
    true_mate = _c(5, 5, 1015.0, 880.0)       # 어긋남 (15, −120) — 거리 ≈ 121
    decoy = _c(5, 5, 1000.0, 1050.0)          # 보정 전엔 더 가깝다(50)
    far_die = _c(7, 5, 1000.0, 1000.0)        # die 게이트(±1) 밖
    too_far = _c(5, 5, 3000.0, 3000.0)        # 반경 밖
    others = [("m", true_mate), ("d", decoy), ("f", far_die), ("t", too_far)]
    no_hint = cal.near_candidates(k, others, src_is_kla=True)
    assert [key for key, _ in no_hint] == ["d", "m"]
    hinted = cal.near_candidates(k, others, src_is_kla=True, hint=(15.0, -120.0))
    assert [key for key, _ in hinted] == ["m", "d"]
    assert hinted[0][1] == pytest.approx(120.93, abs=0.01)   # 표시는 보정 전 거리


def test_build_entries_keeps_slots_apart_and_puts_clear_cases_first():
    k1 = _k(1, 1, 1000.0, 1000.0)
    k2 = _k(1, 1, 5000.0, 5000.0)
    near_k1 = [("a", _c(1, 1, 1010.0, 990.0)), ("b", _c(1, 1, 1200.0, 1000.0))]
    near_k2 = [("c", _c(1, 1, 5010.0, 4990.0))]
    groups = [
        ([("k1", k1), ("none", None)], near_k1 + [("x", None)]),
        ([("k2", k2)], near_k2),
        # 다른 슬롯의 같은 자리 사진은 섞이지 않는다
        ([("lonely", _k(1, 1, 1000.0, 1000.0))], []),
    ]
    entries = cal.build_entries(groups, src_is_kla=True)
    assert [e[0] for e in entries] == ["k2", "k1"]
    assert {c[0] for c in entries[1][2]} == {"a", "b"}


def test_coord_scheduler_applies_offset_only_to_matching(monkeypatch):
    """보정값이 있으면 어긋남이 큰 쌍도 '확정' 이 되고, resolve 결과는 그대로다."""
    pytest.importorskip("PyQt6.QtCore")
    from aoi_verification.app import config
    from aoi_verification.app.models.slot import ImageItem
    from aoi_verification.app.workers import coord_matcher as cm

    ref = ImageItem("s", Path("/r/a.jpg"), "ref")
    val = ImageItem("s", Path("/v/b.jpg"), "val")
    base = {ref.path: _k(1, 1, 1000.0, 1000.0), val.path: _c(1, 1, 1015.0, 880.0)}
    snapshot = dict(base)
    monkeypatch.setattr(cm, "_resolve_batch", lambda paths, cb=None: dict(base))

    def run(offset):
        cfg = config.SimilarityConfig(engine="coordinate", coord_offset=offset)
        res: dict = {}
        sch = cm.CoordScheduler([("s", [ref], [val])], cfg=cfg, results=res)
        sch._run()
        return res[("s", ref.path)]

    plain = run((0.0, 0.0))
    fixed = run((15.0, -120.0))
    assert plain and plain[0][1] < 0.5          # 121 µm 떨어짐 → 낮은 점수
    assert fixed == [(val.path, 1.0)]           # 보정 뒤 거리 0 → 확정
    assert base == snapshot                     # 원본 좌표는 그대로


# ---------------------------------------------------------------------------
# 실측 쌍 — Camtek 예시1 ↔ KLA 예시1 (같은 결함, 약 120 µm 어긋남)
_COORD_DATA = Path(__file__).resolve().parents[2] / "dev" / "좌표 확인"
_CAM1 = _COORD_DATA / "Camtek" / "예시1" / "272646.165679.c.1000203959.2.jpeg"
_KLA1 = _COORD_DATA / "KLA" / "예시1" / "W6459076XYG1_2_0_23_2.jpg"


@pytest.mark.skipif(not _COORD_DATA.exists(), reason="실측 데이터 폴더 없음")
def test_real_pair_is_offered_and_its_offset_makes_it_exact():
    from aoi_verification.app.coords import camtek_ini, kla_info
    camtek_ini.load_folder.cache_clear()
    kla_info.load_folder.cache_clear()
    kla_info.load_folder_raw.cache_clear()
    kla = kla_info.resolve(_KLA1)
    cam = camtek_ini.resolve(_CAM1)
    entries = cal.build_entries([([("k", kla)], [("c", cam)])], src_is_kla=True)
    assert [e[0] for e in entries] == ["k"]
    (_key, _c, found), = entries
    assert found[0][0] == "c" and 100 < found[0][2] < 130     # 보정 전 거리
    off = cal.pair_offset(kla, cam)
    fixed = cal.apply_offset({"k": kla}, off)["k"]
    assert (fixed.x, fixed.y) == (cam.x, cam.y)
    # 원래 좌표(엑셀·화면용)는 그대로
    assert kla_info.resolve(_KLA1) == kla


# ---------------------------------------------------------------------------
# 화면 — 쌍을 고르면 보정값이 나오고, 건너뛰면 None
def _make_entries(tmp_path):
    from PIL import Image
    from aoi_verification.app.models.slot import ImageItem
    shift = (15.0, -120.0)
    entries = []
    for i in range(3):
        rp = tmp_path / f"W1_{i}.jpg"
        Image.new("RGB", (64, 64), (40 * i, 80, 120)).save(rp)
        ref = ImageItem("s", rp, "ref")
        kc = _k(1, 1, 1000.0 + 500 * i, 2000.0)
        found = []
        for j, (dx, dy) in enumerate([(shift[0] + i, shift[1] - i), (300.0, 200.0)]):
            vp = tmp_path / f"cam_{i}_{j}.jpeg"
            Image.new("RGB", (64, 64), (120, 40 * j, 80)).save(vp)
            vc = _c(1, 1, kc.x + dx, kc.y + dy)
            found.append((ImageItem("s", vp, "val"), vc,
                          ((vc.x - kc.x) ** 2 + (vc.y - kc.y) ** 2) ** 0.5))
        entries.append((ref, kc, found))
    return entries, shift


def test_dialog_pairs_give_median_offset(styled_qapp, tmp_path):
    from aoi_verification.app.ui.widgets.coord_calibration_dialog import (
        CoordCalibrationDialog)
    entries, shift = _make_entries(tmp_path)
    dlg = CoordCalibrationDialog(entries, kla_side="ref", hint=shift)
    assert not dlg._apply.isEnabled()                 # 쌍이 없으면 적용 불가
    for ref, _kc, _found in entries:
        dlg._on_src_clicked(ref)
        # 힌트 덕분에 진짜 짝이 첫 후보다
        assert dlg._cand_tiles[0].key == _found[0][0]
        dlg._on_cand_clicked(dlg._cand_tiles[0].key)
    assert dlg._apply.isEnabled()
    dlg._on_apply()
    assert dlg.offset == pytest.approx((shift[0] + 1, shift[1] - 1))
    dlg.deleteLater()


def test_dialog_reclick_cancels_and_skip_gives_none(styled_qapp, tmp_path):
    from aoi_verification.app.ui.widgets.coord_calibration_dialog import (
        CoordCalibrationDialog)
    entries, _shift = _make_entries(tmp_path)
    dlg = CoordCalibrationDialog(entries, kla_side="val")
    ref, kc, found = entries[0]
    dlg._on_src_clicked(ref)
    dlg._on_cand_clicked(found[0][0])
    # 기준이 Camtek(검증이 KLA)이면 부호가 뒤집힌다 — 늘 Camtek − KLA
    assert dlg.current_offset() == pytest.approx((kc.x - found[0][1].x,
                                                  kc.y - found[0][1].y))
    dlg._on_cand_clicked(found[0][0])                 # 같은 사진 다시 → 취소
    assert dlg.current_offset() is None
    dlg.reject()
    assert dlg.offset is None
    dlg.deleteLater()


def test_dialog_offers_a_confident_auto_value_without_pairs(styled_qapp, tmp_path):
    from aoi_verification.app.ui.widgets.coord_calibration_dialog import (
        CoordCalibrationDialog)
    entries, shift = _make_entries(tmp_path)
    auto = cal.AutoOffset(offset=(87.0, -91.0), support=20, runner_up=2, votes=40)
    dlg = CoordCalibrationDialog(entries, kla_side="ref", hint=auto.offset, auto=auto)
    assert dlg._apply.isEnabled()                     # 쌍을 안 골라도 자동값으로 적용
    assert "+87" in dlg._result.text() and "20" in dlg._result.text()
    # 쌍을 고르면 쌍이 우선이다
    ref, _kc, found = entries[0]
    dlg._on_src_clicked(ref)
    dlg._on_cand_clicked(found[0][0])
    assert dlg.current_offset() == pytest.approx(shift)
    dlg._remove_pair(ref)                             # 지우면 다시 자동값
    dlg._on_apply()
    assert dlg.offset == pytest.approx((87.0, -91.0))
    dlg.deleteLater()


def test_dialog_warns_when_a_single_pair_disagrees_with_the_auto_value(styled_qapp, tmp_path):
    """쌍 하나는 남은 오차가 0 이라 경고가 안 뜬다 — 믿을 만한 자동값과 크게 다르면 알린다."""
    from aoi_verification.app.ui.widgets.coord_calibration_dialog import (
        CoordCalibrationDialog)
    entries, shift = _make_entries(tmp_path)                 # 쌍 어긋남 ≈ (15, −120)
    auto = cal.AutoOffset(offset=(87.0, -91.0), support=20, runner_up=2, votes=40)
    dlg = CoordCalibrationDialog(entries, kla_side="ref", auto=auto, tolerance=100.0)
    ref, _kc, found = entries[0]
    dlg._on_src_clicked(ref)
    dlg._on_cand_clicked(found[0][0])
    assert "자동 계산값" in dlg._warn.text()
    assert dlg._apply.isEnabled()                            # 막지는 않는다
    dlg.deleteLater()


def test_dialog_weak_auto_value_still_needs_pairs(styled_qapp, tmp_path):
    from aoi_verification.app.ui.widgets.coord_calibration_dialog import (
        CoordCalibrationDialog)
    entries, _shift = _make_entries(tmp_path)
    weak = cal.AutoOffset(offset=(117.0, -72.0), support=5, runner_up=3, votes=184)
    dlg = CoordCalibrationDialog(entries, kla_side="ref", hint=weak.offset, auto=weak)
    assert not dlg._apply.isEnabled()
    assert dlg._warn.text()                           # 근거 부족을 알린다
    assert dlg.current_offset() is None
    dlg.deleteLater()


def test_auto_offset_stops_at_the_deadline_and_judges_what_it_has(monkeypatch):
    """기한에 걸리면 모인 표로 판단한다 — 결함을 다 볼 필요는 없다(사용자 지시 2026-10-25)."""
    slots = [_shifted(10, (87.0, -91.0), start=10 * i) for i in range(4)]
    seen = []
    monkeypatch.setattr(cal.time, "monotonic", lambda: seen.append(1) or (0.0 if len(seen) <= 24 else 99.0))
    a = cal.auto_offset(slots, src_is_kla=True, deadline=1.0)
    assert a.partial and cal.is_confident(a)
    assert a.offset == pytest.approx((87.0, -91.0), abs=1.0)
    assert a.support == 24                            # 40 개 중 기한 안에 본 24 개만


def test_votes_alternate_slots_so_a_cut_covers_every_slot():
    """앞 슬롯만 보고 멈추지 않게 슬롯을 번갈아 훑는다."""
    a = _shifted(3, (87.0, -91.0))
    b = _shifted(3, (87.0, -91.0), start=3)
    votes, partial = cal._collect_votes([a, b], src_is_kla=True, radius=cal.SEARCH_RADIUS_UM)
    assert not partial
    firsts = [sid for _v, sid in votes][:2]
    assert firsts == [0, 1]                           # 슬롯 a 첫 결함, 슬롯 b 첫 결함


def test_confident_auto_value_is_applied_without_the_dialog(monkeypatch):
    """믿을 만한 자동값은 보정 화면 없이 바로 적용한다(사용자 결정 2026-10-25)."""
    pytest.importorskip("PyQt6.QtWidgets")
    from types import SimpleNamespace
    from aoi_verification.app.ui.main_window import MainWindow
    from aoi_verification.app.ui.widgets import coord_calibration_dialog as ccd

    def no_dialog(*a, **kw):
        raise AssertionError("믿을 만한데 보정 화면을 띄웠다")
    monkeypatch.setattr(ccd, "CoordCalibrationDialog", no_dialog)
    started = []
    worker = object()
    fake = SimpleNamespace(sender=lambda: worker, _calib_worker=worker,
                           _loading=SimpleNamespace(hide_overlay=lambda: None),
                           _input=object(), _scan=object(), _coord_offset=None,
                           _calib_side="ref", _start_stage2=lambda: started.append(1))
    auto = cal.AutoOffset(offset=(87.0, -91.0), support=20, runner_up=2, votes=40)
    MainWindow._on_calib_ready(fake, ([], auto))
    assert fake._coord_offset == (87.0, -91.0)
    assert started == [1]


def test_stage2_title_shows_the_applied_offset():
    pytest.importorskip("PyQt6.QtWidgets")
    from dataclasses import replace
    from aoi_verification.app import config
    from aoi_verification.app.utils.prefs import EngineMode
    from aoi_verification.app.ui.pages import match_page as mp
    cfg = replace(config.DEFAULT_SIM_CONFIG, engine=EngineMode.COORDINATE,
                  coord_offset=(87.0, -91.0))
    assert "+87" in mp._stage2_title(cfg) and "-91" in mp._stage2_title(cfg)


def test_kla_camtek_side_reads_folder_headers(tmp_path):
    """KLA 정보파일이 있는 쪽이 KLA — 양쪽 다/둘 다 아니면 보정하지 않는다."""
    pytest.importorskip("PyQt6.QtWidgets")
    import shutil
    from aoi_verification.app.coords import kla_info
    from aoi_verification.app.models.slot import ImageItem, ScanResult, Slot
    from aoi_verification.app.ui.main_window import MainWindow

    kla_dir = tmp_path / "kla"
    cam_dir = tmp_path / "cam"
    kla_dir.mkdir()
    cam_dir.mkdir()
    shutil.copy(next((_COORD_DATA / "KLA" / "예시1").glob("*.001")), kla_dir)
    shutil.copy(_COORD_DATA / "Camtek" / "예시1" / "ColorImageGrabingInfo.ini", cam_dir)
    kla_info.read_header_ids.cache_clear()

    def side(ref_dir, val_dir):
        slot = Slot("s", [ImageItem("s", ref_dir / "a.jpg", "ref")],
                    [ImageItem("s", val_dir / "b.jpg", "val")])
        fake = type("W", (), {"_scan": ScanResult({"s": slot}, [], [])})()
        return MainWindow._kla_camtek_side(fake)

    assert side(kla_dir, cam_dir) == "ref"
    assert side(cam_dir, kla_dir) == "val"
    assert side(cam_dir, cam_dir) is None
    assert side(kla_dir, kla_dir) is None


# ---------------------------------------------------------------------------
# 실물 골든 — TB500 RDL4 LOT 2개(2026-10-24, 현택님 수집, dev/collect_kla_lot.py)
# ---------------------------------------------------------------------------
_KLA_CAMTEK = (Path(__file__).resolve().parents[1] / "좌표 확인" / "KLA_Camtek(TB500 RDL4)")


def _real_groups(lot: str):
    """웨이퍼마다 (KLA 좌표들, Camtek 좌표들) — 사진 실물 없이 사진목록.txt 의 이름으로."""
    import re
    from aoi_verification.app.coords import resolve_batch

    def listed(folder):
        return [folder / ln.strip() for ln in (folder / "사진목록.txt")
                .read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")]
    groups = []
    for kdir in sorted((_KLA_CAMTEK / lot / "KLA").iterdir()):
        wid = re.search(r'WaferID\s+"([^"]+)"',
                        next(kdir.glob("*.001")).read_text(errors="ignore")).group(1)
        kp, cp = listed(kdir), listed(_KLA_CAMTEK / lot / "Camtek" / wid)
        co = resolve_batch(kp + cp)
        groups.append(([co[p] for p in kp if co.get(p) and co[p].source == "kla"],
                       [co[p] for p in cp if co.get(p) and co[p].source == "camtek_ini"]))
    return groups


@pytest.mark.parametrize("lot", ["VPK", "LSU"])
def test_real_lot_auto_offset_is_confident_and_matches_the_analysis(lot):
    """관측: 웨이퍼 중심 기준 절대좌표로 따로 짝지은 분석(LSU 156쌍)에서 die 안쪽
    Camtek − KLA ≈ (+86, −92) µm, 웨이퍼별 ±4 µm.  실행 전체로 추정하면 그 값이 나와야 하고
    자동 적용할 만큼 믿을 만해야 한다.  (원인: 두 레시피의 die 기준점 정의 차이 —
    ``docs/좌표_KLA_Camtek_오프셋.md``.)"""
    a = cal.auto_offset(_real_groups(lot), src_is_kla=True)
    assert cal.is_confident(a), a
    assert a.offset[0] == pytest.approx(87.0, abs=5)
    assert a.offset[1] == pytest.approx(-91.0, abs=5)


def test_real_single_clustered_wafer_is_not_trusted():
    """한 웨이퍼만 보면 결함이 몰린 곳에서 짝이 섞인다 — 31266524EWG5 는 (117, −72) 를
    냈다.  그런 값은 '근거 부족' 으로 걸러져야 한다(그래서 실행 전체로 추정한다)."""
    groups = _real_groups("VPK")
    names = sorted(p.name for p in (_KLA_CAMTEK / "VPK" / "KLA").iterdir())
    import re
    for kdir_name, g in zip(names, groups):
        txt = next((_KLA_CAMTEK / "VPK" / "KLA" / kdir_name).glob("*.001")).read_text(
            errors="ignore")
        if re.search(r'WaferID\s+"31266524EWG5"', txt):
            a = cal.auto_offset([g], src_is_kla=True)
            assert a is not None and abs(a.offset[0] - 87) > 15   # 실제로 틀린 값을 낸다
            assert not cal.is_confident(a)
            return
    pytest.fail("31266524EWG5 가 자료에 없다")
