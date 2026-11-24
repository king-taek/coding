"""화면 낱말은 한 개념에 하나 — 문구 전수 검수(#i18n)에서 나온 불변식.

실측: 짝을 못 찾은 사진 하나를 화면이 **낱말 6개**로 불렀다 —
``매치 없음`` / ``매치 실패`` / ``매칭 없음`` / ``매칭없음`` / ``매치 아님`` / ``미매칭``.
화면은 이미 '**매치 없음**(사람이 되돌린 것) vs **매치 실패**(자동이 못 찾은 것)' 로 잘
갈라 놓았으므로 나머지 넷을 그 둘에 흡수시켰다.

예외 하나는 **의도**이며 여기서 못 박는다:
  · 엑셀 시트 이름의 ``Slot``(SLOT_MISMATCH_SHEET) — 화면은 전부 '슬롯' 이다
    (`utils/excel_ref` 가 이 이름으로 시트를 건너뛰어 바꾸면 옛 결과 파일을 못 읽는다).
엑셀 메모·시트의 ``미매칭`` 도 화면과 같은 ``매치 없음`` 으로 바꿨다(현택님 2026-11-24).

낱말 규약: **매칭 = 과정/기능**(설정·중단·엔진), **매치 = 결과 한 건**(검토·확정·성공).
"""
from __future__ import annotations

from aoi_verification.app import i18n


def _screen_strings() -> dict[str, str]:
    """사용자 노출 문자열 — 엑셀 표기 전용 키는 뺀다(위 예외)."""
    excel_only = {"SLOT_MISMATCH_SHEET"}
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
    assert i18n.KO.CHIP_OVER == i18n.KO.TALLY_OVER_FMT.format(n="").strip() == "허용 초과"


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


def test_excel_notes_use_the_screen_word() -> None:
    """엑셀 메모·시트도 화면과 같은 '매치 없음' — 다리 문구가 필요 없어졌다."""
    for name in ("NOTE_UNMATCHED", "NOTE_UNMATCHED_BY_USER", "NOTE_ONLY_VAL",
                 "SHEET_UNMATCHED"):
        assert getattr(i18n.KO, name).startswith("매치 없음"), name
    assert "미매칭" not in i18n.KO.TALLY_TOOLTIP


def _all_texts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _all_texts(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _all_texts(v)


def test_screen_says_defect_and_zone_in_english() -> None:
    """화면은 '결함' 대신 'Defect', Zone 개념은 '구역' 대신 'Zone' (현택님 2026-11-24).

    ※ '구역' 은 뷰어의 '드래그한 구역'(잘라 복사할 범위)처럼 Zone 이 아닌 뜻으로도 쓰여
    Zone 을 가리키는 키만 본다."""
    offenders = [name for name in dir(i18n.KO) if name.isupper()
                 for text in _all_texts(getattr(i18n.KO, name)) if "결함" in text]
    assert offenders == [], f"'결함' 이 남아 있다: {sorted(set(offenders))}"
    assert i18n.KO.OVERKILL_PICK_ZONE_TIP == "Zone"


# ── 통일성 점검 2차(현택님 2026-11-24) — 낱말 · 단위 · 문장 꼴 가드 ──────────────
# 엑셀 D열 한 줄 표기 — `utils/excel_ref` 가 ㎛ 로 되읽으므로 화면 단위(µm)로 바꾸지 않는다.
_EXCEL_UM_KEYS = {"EXPORT_KLA_NATIVE_FMT", "DEFECT_XY_FMT", "DEFECT_ABS_XY_FMT",
                  "DEFECT_HEIGHT_FMT", "DEFECT_AREA_FMT", "DEFECT_WIDTH_FMT",
                  "DEFECT_LENGTH_FMT"}
# 엑셀 시트 이름 'Wafer Map' — `utils/excel_ref` 가 이 이름으로 시트를 건너뛴다.
_WAFER_MAP_SHEET_KEYS = {"WAFER_MAP_SHEET", "EXTRACT_WAFER_MAP_SWITCH"}

_BANNED = [
    # (무엇, 정규식, 예외 키)
    ("'미매칭'·'미매치' → '매치 없음'", r"미매칭|미매치", set()),
    ("'일치' → '매치'('슬롯 불일치' 는 다른 뜻)", r"(?<!불)일치", set()),
    ("화면 단위는 µm", r"㎛", _EXCEL_UM_KEYS),
    ("소문자 defect → Defect", r"(?<![A-Za-z_{])defect(?![A-Za-z_])", set()),
    ("화면 이름은 'Defect Map'", r"Wafer [Mm]ap", _WAFER_MAP_SHEET_KEYS),
    ("'재 분류' → '재분류'", r"재 분류", set()),
    ("col / row 한 꼴", r"[Cc]ol, [Rr]ow|Col / Row|col \{[^}]*\} · row", set()),
    ("개수는 '{n}건'·'{n}개' 붙여 쓰기", r"\} 건|\d 건", set()),
    ("% 붙여 쓰기", r"[\d}] %", set()),
    ("' · ' 는 한 칸", r"  ·|·  ", set()),
    ("확인 질문은 '~할까요?'", r"하시겠", set()),
    ("저장 실패는 '저장 실패 · 이유'", r"저장하지 못했|저장 실패:", set()),
    ("진행 문구는 '~하는 중'(점 없이)", r"중…|중입니다…", set()),
    ("더 보기는 '+n'", r"\(\+\{", set()),
    ("뺀 알림은 '{name} 뺌'", r"뺐습니다", set()),
    ("슬롯 모름은 '—'", r"#\?", set()),
]


def _ko_texts():
    for name in dir(i18n.KO):
        if name.isupper():
            for text in _all_texts(getattr(i18n.KO, name)):
                yield name, text


def test_ui_wording_guard() -> None:
    import re
    offenders = []
    for what, pattern, allowed in _BANNED:
        rx = re.compile(pattern)
        offenders += [f"{what}: {name} = {text[:50]!r}" for name, text in _ko_texts()
                      if name not in allowed and rx.search(text)]
    assert offenders == [], "화면 문구 규칙 위반:\n  " + "\n  ".join(offenders)


def test_excel_um_keys_still_say_the_old_unit() -> None:
    """예외 목록이 거꾸로 µm 로 바뀌면 옛 결과 엑셀을 되읽는 정규식이 깨진다."""
    for name in _EXCEL_UM_KEYS:
        assert "㎛" in getattr(i18n.KO, name), name
    assert i18n.KO.WAFER_MAP_SHEET == "Wafer Map", "엑셀 시트 이름은 그대로(excel_ref 가 이 이름을 본다)"


def test_save_failures_share_one_form() -> None:
    for name in ("SAVE_FAIL_FMT", "THRESH_EXPORT_FAILED", "GL_SAVE_FAILED_FMT"):
        assert getattr(i18n.KO, name).startswith("저장 실패 · {"), name
