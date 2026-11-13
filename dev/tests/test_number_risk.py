"""번호를 확실히 정하지 못한 웨이퍼의 큰 경고 — 순수 로직(사용자 지시 2026-11-13).

"경고가 나오는 웨이퍼를 진행할 때에는 경고를 크게 / 특히 wafer map 에 defect 그리는
모드에서는 경고를 잘 보이게 — 틀릴 확률이 매우 높음!!" 을 화면이 지키는지, 화면이 쓰는
판정·문구를 Qt 없이 못 박는다.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from aoi_verification.app import i18n
from aoi_verification.app.coords import number_risk as nr
from aoi_verification.app.coords import wafer_map as wm
from aoi_verification.app.coords.models import NUMBER_WARN_KLA_GUESS, NUMBER_WARN_NO_TABLE


def _coords(**warn_by_name):
    return {Path(k): SimpleNamespace(number_warn=v) for k, v in warn_by_name.items()}


def test_count_by_slot_counts_only_warned_photos_and_sums_ref_and_val():
    c = _coords(a1=NUMBER_WARN_NO_TABLE, a2="", b1=NUMBER_WARN_KLA_GUESS, b2=NUMBER_WARN_KLA_GUESS)
    groups = [("A", [Path("a1"), Path("a2"), Path("missing")]),
              ("B", [Path("b1")]), ("B", [Path("b2")]),     # 같은 슬롯 기준·검증
              ("C", [Path("a2")])]
    assert nr.count_by_slot(groups, c.get) == (("A", 1), ("B", 2))


def test_count_by_slot_empty_when_every_number_is_trusted():
    c = _coords(a="", b="")
    assert nr.count_by_slot([("A", [Path("a"), Path("b")])], c.get) == ()


def test_merge_takes_the_larger_count_per_slot():
    # 다중 장비: 구간마다 같은 기준 사진을 다시 센다 — 더하지 않는다.
    assert nr.merge((("A", 3),), (("A", 5), ("B", 1)), ()) == (("A", 5), ("B", 1))


def test_banner_text_is_loud_and_names_the_slots():
    assert nr.banner_text(()) == ""
    text = nr.banner_text((("GX1", 3), ("GX2", 12)))
    head, body, slots = text.split("\n")
    assert head == i18n.KO.NUMBER_RISK_HEAD and "틀릴 확률이 매우 높습니다" in head
    assert body == i18n.KO.NUMBER_RISK_BODY and "장비 화면" in body
    assert slots == "GX1 (3장) · GX2 (12장)"


def test_banner_text_folds_many_slots():
    risk = tuple((f"S{i}", 1) for i in range(9))
    last = nr.banner_text(risk).split("\n")[-1]
    assert last.startswith("S0 (1장)") and "S5 (1장)" in last and "S6" not in last
    assert last.endswith(i18n.KO.NUMBER_RISK_MORE_FMT.format(n=3))


def test_high_risk_codes_turn_the_map_banner_red():
    # 번호 계산 · die 없는 칸 · 격자 불일치 → 빨간 큰 배너
    assert wm.is_high_risk(wm.MapData(None, (), (), guessed_points=2))
    assert wm.is_high_risk(wm.MapData(None, (), (), mixed_frames=True))
    # LIVE 당부 · 좌표 없는 사진만 있으면 주황 한 줄 그대로 — 늘 빨가면 무시하게 된다
    assert not wm.is_high_risk(wm.MapData(None, (), (), live_points=5))
    assert not wm.is_high_risk(wm.MapData(None, (), (Path("x.jpg"),)))
    assert not wm.is_high_risk(None)
    assert set(wm.DANGER_CODES) == {wm.WARN_NUMBER_GUESS, wm.WARN_OFF_DIE, wm.WARN_MIXED_FRAMES}


def test_danger_strings_say_it_loudly():
    for s in (i18n.KO.WAFER_MAP_DANGER_HEAD, i18n.KO.WAFER_MAP_DANGER_SHORT):
        assert s.startswith("⚠") and "틀릴 확률이 매우 높" in s and "장비 화면" in s


def test_qss_defines_the_danger_banner_role():
    qss = (Path(__file__).resolve().parents[2]
           / "aoi_verification/app/ui/style.qss").read_text(encoding="utf-8")
    block = qss.split('QLabel[role="dangerBanner"]', 1)[1].split("}", 1)[0]
    assert "$danger" in block and "border: 2px" in block and "font-weight: 800" in block
