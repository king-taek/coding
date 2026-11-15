"""i18n 패키지 — 사용자에게 노출되는 문자열은 모두 ko.py 에 모아둡니다."""

from . import ko as KO  # noqa: F401


def direction_suffix(reverse: bool, cross: bool) -> str:
    """선별·매칭·검토 표제 뒤에 붙는 교차검증 방향 — 교차검증이 아니면 빈 글자."""
    if reverse:
        return KO.CROSS_REVERSE_SUFFIX
    return KO.CROSS_FORWARD_SUFFIX if cross else ""
