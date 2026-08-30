"""상단 진행 표시(슬롯명 · 수치 · 눈금)의 **갱신 규약** — 선별/매칭 화면 공용.

두 화면은 같은 세 위젯을 각자 자기 레이아웃 자리에 만들어 두고(자리는 화면마다
다르다), 갱신은 여기 한 곳만 쓴다.  예전엔 같은 12 줄이 두 파일에 따로 있었고
한쪽 docstring 이 다른 쪽을 "같은 규약" 이라 가리키고 있었다 — 규약이라면 코드가
하나여야 한다(클램프나 노출 규칙을 한쪽만 고치는 사고를 막는다).

**계약**: 섞어 쓰는 화면은 `progress_label`(보조 문구) · `progress_count`(모노 수치)
· `progress_bar`(눈금) 세 속성을 만들어 둔다.
"""

from __future__ import annotations

from ... import i18n


class ProgressRowMixin:
    """진행 표시 갱신/초기화 — QWidget 과 함께 상속해 쓴다(자체 위젯은 만들지 않는다)."""

    def _set_progress(self, slot: str, done: int, total: int) -> None:
        """진행 표시 갱신 — 슬롯명(보조) · 수치(모노 본문) · 눈금(스냅 채움).

        ★ 눈금 채움에 애니메이션을 걸지 않는다.  진행률은 정보이므로 값이 곧바로
          보여야 한다(로딩 오버레이 `set_progress` 의 스냅 원칙과 같은 판단)."""
        self.progress_label.setText(
            i18n.KO.PROGRESS_SLOT_ONLY_FMT.format(slot=slot))
        self.progress_count.setText(
            i18n.KO.PROGRESS_COUNT_FMT.format(done=done, total=total))
        self.progress_bar.setMaximum(max(1, total))
        self.progress_bar.setValue(max(0, min(done, total)))
        self.progress_bar.setVisible(total > 0)

    def _clear_progress(self) -> None:
        self.progress_label.setText("")
        self.progress_count.setText("")
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
