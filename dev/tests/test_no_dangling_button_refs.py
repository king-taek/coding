"""안내 문구가 **없는 버튼**을 가리키지 못하게 막는다.

배경(실제 사고): [매칭 결과 검토] 다이얼로그를 코드째 지우면서(U-10) 심볼 참조는
전 저장소 grep 으로 0건까지 확인했는데, **다른 문구 안에서 그 버튼을 이름으로
부르는 것**은 놓쳤다.  설정 화면에 늘 떠 있는 `RUN_OPTIONS_HINT` 가

    "… 종료 후 결과 화면에서 [매칭 결과 검토]로 잘못된 매치를 제거할 수 있습니다."

라고 안내하고 있었다 — 사용자에게 **존재하지 않는 버튼을 누르라고 말하는** 상태로
남아 있었던 것이다.  심볼을 지우는 것과 그 심볼을 설명하던 문장을 고치는 것은 별개다.

여기서 세우는 불변식: **i18n 문구 안의 `[…]` 는 실제로 존재하는 화면 라벨이어야 한다.**
버튼 하나를 지우면 그것을 부르던 안내가 자동으로 드러난다.
"""
from __future__ import annotations

import re

from aoi_verification.app import i18n

# 버튼이 아니라 **표식**으로 대괄호를 쓰는 자리 — 문구를 그대로 적어 의도를 남긴다.
# 여기 추가하려면 "이건 정말 버튼이 아닌가?" 를 먼저 물어라.
_NOT_BUTTONS = {
    "원인",      # 예외 원인을 덧붙일 때의 머리말
    "중요",      # 업데이트 안내의 강조 표식
    "검토 완료",  # 실제 라벨은 동적이다: BTN_FINISH_REVIEW_KEPT_FMT = "검토 완료 · 유지 {n}"
}

# 라벨에 붙는 장식(화살표·체크·중점 등)은 같은 버튼으로 본다.
_ORNAMENT = re.compile(r"[←→✕✓↩▾▴·\s]")


def _strings() -> dict[str, str]:
    out = {}
    for name in dir(i18n.KO):
        if not name.isupper():
            continue
        value = getattr(i18n.KO, name)
        if isinstance(value, str):
            out[name] = value
    return out


def _label_set(strings: dict[str, str]) -> set[str]:
    """화면 라벨로 쓰일 만한 짧은 한 줄 문자열 전부."""
    return {v.strip() for v in strings.values()
            if len(v) <= 24 and "\n" not in v}


def test_every_bracketed_reference_names_a_real_label() -> None:
    strings = _strings()
    labels = _label_set(strings)
    normalized = {_ORNAMENT.sub("", l) for l in labels}

    dangling = []
    for key, value in strings.items():
        for ref in re.findall(r"\[([^\[\]]{1,20})\]", value):
            text = ref.strip()
            if text in _NOT_BUTTONS:
                continue
            if text in labels:
                continue
            if _ORNAMENT.sub("", text) in normalized:
                continue
            dangling.append(f"{key}: [{text}]")

    assert dangling == [], (
        "안내 문구가 존재하지 않는 버튼을 가리킨다 — 버튼을 지웠다면 그것을 설명하던\n"
        "문장도 함께 고쳐야 한다(사용자에게 없는 버튼을 누르라고 말하게 된다):\n  "
        + "\n  ".join(dangling))


def test_the_guard_would_catch_a_removed_button() -> None:
    """가드가 늘 통과하는 빈 껍데기가 아님을 스스로 증명한다."""
    strings = dict(_strings())
    strings["_FAKE_HINT"] = "결과 화면에서 [있지도 않은 버튼] 을 누르세요."
    labels = _label_set(strings)
    normalized = {_ORNAMENT.sub("", l) for l in labels}

    found = [
        ref for ref in re.findall(r"\[([^\[\]]{1,20})\]", strings["_FAKE_HINT"])
        if ref.strip() not in _NOT_BUTTONS
        and ref.strip() not in labels
        and _ORNAMENT.sub("", ref.strip()) not in normalized
    ]
    assert found == ["있지도 않은 버튼"]


def test_removed_review_dialog_is_not_named_anywhere() -> None:
    """U-10 으로 사라진 [매칭 결과 검토] 를 어떤 문구도 다시 부르지 않는다."""
    offenders = [k for k, v in _strings().items() if "매칭 결과 검토" in v]
    assert offenders == [], (
        f"삭제된 [매칭 결과 검토] 다이얼로그를 아직 안내하는 문구: {offenders}")
