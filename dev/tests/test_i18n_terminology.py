"""화면 낱말은 한 개념에 하나 — 문구 전수 검수(#i18n)에서 나온 불변식.

실측: 짝을 못 찾은 사진 하나를 화면이 **낱말 6개**로 불렀다 —
``매치 없음`` / ``매치 실패`` / ``매칭 없음`` / ``매칭없음`` / ``매치 아님`` / ``미매칭``.
화면은 이미 '**매치 없음**(사람이 되돌린 것) vs **매치 실패**(자동이 못 찾은 것)' 로 잘
갈라 놓았으므로 나머지 넷을 그 둘에 흡수시켰다.

예외 두 가지는 **의도**이며 여기서 못 박는다:
  · 엑셀 표기 ``미매칭``(NOTE_UNMATCHED·SHEET_UNMATCHED) — 현장 양식 호환 확인 전까지
    건드리지 않는다.  대신 TALLY_TOOLTIP 이 화면↔엑셀 다리를 놓는다.
  · 엑셀 시트 이름의 ``Slot``(SLOT_MISMATCH_SHEET) — 화면은 전부 '슬롯' 이다.

낱말 규약: **매칭 = 과정/기능**(설정·중단·엔진), **매치 = 결과 한 건**(검토·확정·성공).
"""
from __future__ import annotations

from aoi_verification.app import i18n


def _screen_strings() -> dict[str, str]:
    """사용자 노출 문자열 — 엑셀 표기 전용 키는 뺀다(위 예외)."""
    excel_only = {"NOTE_UNMATCHED", "NOTE_UNMATCHED_BY_USER", "SHEET_UNMATCHED",
                  "SLOT_MISMATCH_SHEET"}
    out = {}
    for name in dir(i18n.KO):
        if not name.isupper() or name in excel_only:
            continue
        value = getattr(i18n.KO, name)
        if isinstance(value, str):
            out[name] = value
    return out


def test_no_match_has_exactly_one_screen_word() -> None:
    """'매칭 없음' · '매칭없음' · '매치 아님' 은 화면에서 사라졌다."""
    banned = ("매칭 없음", "매칭없음", "매치 아님")
    offenders = [f"{k}: {w}" for k, v in _screen_strings().items()
                 for w in banned if w in v]
    assert offenders == [], (
        "한 개념을 여러 낱말로 부른다 — 화면은 '매치 없음'(사람이 되돌린 것) /\n"
        "'매치 실패'(자동이 못 찾은 것) 둘만 쓴다:\n  " + "\n  ".join(offenders))


def test_both_surviving_words_are_still_used() -> None:
    """흡수시키다가 개념 자체를 지우지 않았는지 — 둘 다 살아 있어야 한다."""
    joined = "\n".join(_screen_strings().values())
    assert "매치 없음" in joined
    assert "매치 실패" in joined


def test_over_tolerance_has_one_word() -> None:
    """'허용범위 초과' 는 '허용 초과' 로 통일했다(툴팁만 달랐다)."""
    offenders = [k for k, v in _screen_strings().items() if "허용범위" in v]
    assert offenders == [], f"'허용범위 초과' 가 남아 있다: {offenders}"
    assert "허용 초과" in i18n.KO.SCORE_DIST_OVER_FMT
    assert i18n.KO.STAT_OVER_TOLERANCE == i18n.KO.CHIP_OVER == "허용 초과"


def test_one_action_has_one_button_name() -> None:
    """같은 동작(이 후보를 매치로 확정)을 세 화면이 **한 이름**으로 부른다.

    확대 보기 '매치' / 좌우 비교 '이 후보로 매치' / 줌 윈도우 '이 사진으로 매칭' 이
    갈려 있었다.  이름을 코드에서 읽어 대조한다(문자열을 손으로 굳히지 않는다)."""
    names = {i18n.KO.BTN_CONFIRM_AS_MATCH,
             i18n.KO.BTN_MATCH_THIS,
             i18n.KO.ZOOM_BTN_PICK_MATCH}
    assert len(names) == 1, f"같은 동작인데 버튼 이름이 갈렸다: {sorted(names)}"


def test_screen_says_slot_in_korean() -> None:
    """화면 문구는 '슬롯' 이다 — 엑셀 표기(SLOT_MISMATCH_SHEET)만 'Slot' 을 남긴다."""
    # `{slot}` 은 포맷 자리표시자(코드 식별자)이지 화면에 보이는 낱말이 아니다.
    offenders = [f"{k}: {v[:40]}" for k, v in _screen_strings().items()
                 if "Slot" in v or "slot" in v.replace("{slot}", "")]
    assert offenders == [], (
        "화면 문구에 영문 Slot 이 남아 있다(엑셀 시트 이름만 예외):\n  "
        + "\n  ".join(offenders))
    assert "Slot" in i18n.KO.SLOT_MISMATCH_SHEET, "엑셀 예외까지 바꾸면 양식이 깨진다"


def test_no_bare_english_skip_on_screen() -> None:
    """화면에 남은 마지막 영어 'Skip' 을 없앴다 — 그 자리는 '매치 없음' 이다."""
    offenders = [k for k, v in _screen_strings().items() if "Skip" in v]
    assert offenders == [], f"화면 문구에 'Skip' 이 있다: {offenders}"


def test_tooltip_bridges_screen_and_excel_wording() -> None:
    """화면('매치 없음')과 엑셀('미매칭')이 다른 유일한 자리에 다리를 놓아 둔다."""
    assert i18n.KO.NOTE_UNMATCHED in i18n.KO.TALLY_TOOLTIP
