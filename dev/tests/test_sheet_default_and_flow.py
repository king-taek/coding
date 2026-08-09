"""확인창의 강조가 안전 기본값과 같은 곳을 가리키는지(U-11), 그리고 흐름 손질들.

배경: `ask()` 는 기본값이 '아니오' 인 경고에서도 [예] 를 언제나 채운 파란 버튼으로
그렸다.  "정말 같은 폴더를 비교할까요?" 같은 창에서 **시선은 [예] 로 가는데 Enter 는
[아니오] 를 눌렀다** — 시각 강조와 안전 기본값이 정반대를 가리켰다.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QMessageBox                       # noqa: E402
from PyQt6.QtCore import Qt                                    # noqa: E402
from PyQt6.QtTest import QTest                                 # noqa: E402

from aoi_verification.app.ui.widgets import sheet_host as sheets  # noqa: E402

_SB = QMessageBox.StandardButton


def _sheet(buttons, default):
    return sheets._MessageSheet(None, "제목", "본문", buttons, default, "question")


def test_negative_default_removes_the_filled_emphasis(styled_qapp):
    s = _sheet(_SB.Yes | _SB.No, _SB.No)
    roles = {sb: b.property("role") for sb, b in s._buttons.items()}
    assert "primary" not in roles.values(), \
        f"기본값이 '아니오' 인데 채운 강조 버튼이 남아 있다: {roles}"
    s.deleteLater()


def test_affirmative_default_keeps_the_emphasis(styled_qapp):
    s = _sheet(_SB.Yes | _SB.No, _SB.Yes)
    assert s._buttons[_SB.Yes].property("role") == "primary"
    s.deleteLater()


def test_cancel_default_also_demotes_ok(styled_qapp):
    """규칙을 Yes 에만 걸면 [확인]/[취소] 조합에서 같은 모순이 되살아난다."""
    s = _sheet(_SB.Ok | _SB.Cancel, _SB.Cancel)
    assert s._buttons[_SB.Ok].property("role") != "primary"
    s.deleteLater()


def test_escape_still_answers_no(styled_qapp):
    """강등이 ESC 계약을 건드리면 안 된다 — ESC = 안전한 쪽."""
    s = _sheet(_SB.Yes | _SB.No, _SB.No)
    QTest.keyClick(s, Qt.Key.Key_Escape)
    assert s.answer() == _SB.No
    s.deleteLater()


def test_page_transition_overlay_absorbs_clicks(styled_qapp, monkeypatch):
    """전환 240ms 동안 스택이 담고 있는 것은 아직 **옛 페이지**다(P-13).

    마우스를 통과시키면 눈에 보이는 새 화면이 아니라 방금 떠난 화면의 그 좌표
    위젯이 눌린다.
    """
    from PyQt6.QtWidgets import QLabel, QWidget
    from aoi_verification.app.ui import motion

    monkeypatch.setattr(motion, "enabled", lambda: True)
    container = QWidget()
    container.resize(400, 300)
    pix = container.grab()
    motion.transition_in(container, pix, forward=True, on_commit=lambda: None)
    overlay = container.findChild(QLabel, "_pageFadeOverlay")
    assert overlay is not None
    assert not overlay.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents), \
        "전환 스냅샷이 클릭을 통과시킨다"
    # ★ 돌고 있는 애니메이션을 **먼저 멈추고** 파괴한다.  그냥 `deleteLater` 만 걸고
    #   나가면 fade/slide 가 계속 돌다가, 다음 테스트가 이벤트를 돌리는 순간
    #   `motion._finish` 가 이미 죽은 오버레이를 만져 워커 프로세스가 통째로 죽는다
    #   (실측: 이 파일 + `test_sheet_enter_motion.py` → 세그폴트).
    from PyQt6.QtCore import QPropertyAnimation
    for anim in overlay.findChildren(QPropertyAnimation):
        anim.stop()
    overlay.setParent(None)
    overlay.deleteLater()
    container.deleteLater()
    styled_qapp.processEvents()


def test_resume_text_no_longer_promises_to_continue(styled_qapp):
    """'이어하기' 는 실제로 결정을 복원하지 않는다 — 문구가 사실이어야 한다."""
    from aoi_verification.app import i18n
    body = i18n.KO.INFO_RESUME_BODY
    assert "이어서" not in body, "복원하지 않는 것을 '이어서 한다' 고 말하고 있다"
    assert "처음부터" in body


def test_phase_transition_modal_is_gone(styled_qapp):
    """선택지가 없는 안내가 매 세션 클릭 1회를 강제하지 않는다(U-14)."""
    import pathlib
    repo = pathlib.Path(__file__).resolve().parents[2]
    src = (repo / "aoi_verification/app/ui/main_window.py").read_text(encoding="utf-8")
    assert "INFO_PHASE_A_TO_MATCH" not in src


def test_slot_mismatch_question_is_a_sentence(styled_qapp):
    """버튼 라벨에 ' ?' 를 붙여 만든 비문("… 열기 ?")을 쓰지 않는다(U-23)."""
    from aoi_verification.app import i18n
    assert i18n.KO.SLOT_MAP_ASK.endswith("?")
    assert " ?" not in i18n.KO.SLOT_MAP_ASK
