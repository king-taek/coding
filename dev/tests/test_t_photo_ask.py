"""``.t.`` 사진을 이번 검증에 넣을지 **묻는다** — 코드가 정하지 않는다.

신고(UI 관련 PDF ⑤): "폴더 안에 `.t.` 가 이름에 들어가 있는 사진들은 뺄지 말지
결정해야 하는데, 이런 사진들이 있을 때에는 검증 시작을 눌렀을 때 사용자에게 예시
사진 하나만 보여주면서 이런 사진들도 포함할 것인지 물어보도록 해줘. No 누르면 다
제외하고 진행."

⚠ 이 자리는 '항상 뺀다'(옛 `is_ignored_name`) → '항상 넣는다'(커밋 `0b676d1`,
사용자 요청) → '묻는다'(지금) 로 **세 번 바뀌었다**.  정답이 자재마다 다르다는 것이
결론이므로, 다음 사람이 다시 한쪽으로 굳히지 않도록 그 사실을 테스트에 적어 둔다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app.models.slot import (ImageItem,      # noqa: E402
                                              ScanResult, Slot,
                                              has_t_token, is_ignored_name)
from aoi_verification.app.ui import main_window as mw          # noqa: E402


# ---------------------------------------------------------------------------
# 판정 자체 — **부분 문자열이 아니라 점 토큰**이다
# ---------------------------------------------------------------------------
def test_only_a_lone_t_token_counts():
    assert has_t_token("-86955.68631.t.1.jpg") is True
    assert has_t_token("t.1.jpg") is True
    # 실물 다수가 같은 자리에 'c' 를 쓴다 — 그건 해당하지 않는다.
    assert has_t_token("272646.165679.c.1000203959.2.jpeg") is False
    assert has_t_token("W6459076XYG1_2_0_23_2.jpg") is False


def test_a_t_inside_a_word_is_not_a_token():
    """이름 어딘가에 t 가 있다고 걸리면 멀쩡한 사진이 통째로 빠진다."""
    for name in ("test.jpg", "target.1.jpg", "1.tt.2.jpg", "1.T2.3.jpg"):
        assert has_t_token(name) is False, name


def test_enumeration_still_keeps_them():
    """열거(`is_ignored_name`)는 여전히 거르지 않는다 — 뺄지는 **세션마다** 정한다.

    여기서 다시 거르면 사용자가 '포함' 을 골라도 사진이 오지 않는다."""
    assert is_ignored_name("-86955.68631.t.1.jpg") is False
    assert is_ignored_name("CognexInSight17xx_Bottom.jpg") is True


# ---------------------------------------------------------------------------
# 흐름 — 있을 때만 묻고, '제외' 면 스캔 결과에서 바로 뺀다
# ---------------------------------------------------------------------------
def _scan(with_t: bool) -> ScanResult:
    def item(slot, name, side):
        return ImageItem(slot=slot, path=Path(f"/tmp/{slot}/{name}"), side=side)

    ref = [item("S1", "1.2.c.9.jpeg", "ref")]
    val = [item("S1", "1.2.c.9.jpeg", "val")]
    if with_t:
        ref.append(item("S1", "-86955.68631.t.1.jpg", "ref"))
        val.append(item("S1", "-11.22.t.3.jpg", "val"))
    slot = Slot(name="S1", ref_images=ref, val_images=val,
                ref_dir=Path("/tmp/ref/S1"), val_dir=Path("/tmp/val/S1"))
    return ScanResult(slots={"S1": slot}, ref_only=[], val_only=[])


def _window(monkeypatch):
    monkeypatch.setattr(mw.MainWindow, "_start_backend_import_async",
                        lambda self: None)
    return mw.MainWindow()


class _Dlg:
    """다이얼로그 대역 — 답만 정해 준다."""

    made: list = []

    def __init__(self, include):
        self._include = include

    def __call__(self, sample, total, parent=None):
        _Dlg.made.append((Path(sample), total))
        self.include = self._include
        return self


def _wire(monkeypatch, include):
    _Dlg.made = []
    dlg = _Dlg(include)
    import aoi_verification.app.ui.widgets.t_photo_ask_dialog as mod
    monkeypatch.setattr(mod, "TPhotoAskDialog", dlg)
    monkeypatch.setattr(mw.sheets, "run", lambda *a, **k: 1)
    return dlg


def test_no_t_photos_asks_nothing(qapp, monkeypatch):
    """없으면 아무것도 묻지 않는다 — 정상 흐름에 클릭을 더하지 않는다."""
    win = _window(monkeypatch)
    try:
        _wire(monkeypatch, include=True)
        sr = _scan(with_t=False)
        win._ask_about_t_photos(sr)
        assert _Dlg.made == [], "없는데 물었다"
        assert len(sr.slots["S1"].ref_images) == 1
    finally:
        win.close()


def test_no_keeps_them_all_out(qapp, monkeypatch):
    """'제외' → 기준·검증 양쪽에서 **바로** 빠진다(이후 단계가 자동으로 따라온다)."""
    win = _window(monkeypatch)
    try:
        _wire(monkeypatch, include=False)
        sr = _scan(with_t=True)
        win._ask_about_t_photos(sr)
        assert len(_Dlg.made) == 1, "있는데 묻지 않았다"
        assert _Dlg.made[0][1] == 2, f"장수가 틀렸다: {_Dlg.made[0][1]}"
        names = [i.filename for i in sr.slots["S1"].ref_images]
        assert names == ["1.2.c.9.jpeg"], f"기준에서 안 빠졌다: {names}"
        names = [i.filename for i in sr.slots["S1"].val_images]
        assert names == ["1.2.c.9.jpeg"], f"검증에서 안 빠졌다: {names}"
    finally:
        win.close()


def test_yes_keeps_them(qapp, monkeypatch):
    win = _window(monkeypatch)
    try:
        _wire(monkeypatch, include=True)
        sr = _scan(with_t=True)
        win._ask_about_t_photos(sr)
        assert len(_Dlg.made) == 1
        assert len(sr.slots["S1"].ref_images) == 2, "포함을 골랐는데 빠졌다"
        assert len(sr.slots["S1"].val_images) == 2
    finally:
        win.close()


def test_the_sample_shown_is_actually_a_t_photo(qapp, monkeypatch):
    """예시로 보여 주는 사진이 판단 근거다 — 엉뚱한 사진을 보여주면 안 된다."""
    win = _window(monkeypatch)
    try:
        _wire(monkeypatch, include=False)
        sr = _scan(with_t=True)
        win._ask_about_t_photos(sr)
        assert has_t_token(_Dlg.made[0][0].name), \
            f"예시가 `.t.` 사진이 아니다: {_Dlg.made[0][0].name}"
    finally:
        win.close()


def test_a_broken_prompt_does_not_stop_the_run(qapp, monkeypatch):
    """물음이 깨져도 검증은 계속돼야 한다 — 그때는 **포함**한 채로 간다."""
    win = _window(monkeypatch)
    try:
        import aoi_verification.app.ui.widgets.t_photo_ask_dialog as mod

        def boom(*a, **k):
            raise RuntimeError("미리보기가 터졌다")

        monkeypatch.setattr(mod, "TPhotoAskDialog", boom)
        sr = _scan(with_t=True)
        win._ask_about_t_photos(sr)         # 예외가 새면 여기서 터진다
        assert len(sr.slots["S1"].ref_images) == 2, "실패했는데 사진을 버렸다"
    finally:
        win.close()


# ---------------------------------------------------------------------------
# 다이얼로그 자체 — 예시 사진이 실제로 보이는가
# ---------------------------------------------------------------------------
def _png(path: Path) -> bool:
    """작은 사진 한 장.  Pillow 가 없는 환경이면 건너뛴다."""
    Image = pytest.importorskip("PIL.Image")
    Image.new("RGB", (600, 400), (90, 120, 160)).save(path, "JPEG")
    return True


def test_the_dialog_actually_shows_the_photo(styled_qapp, tmp_path,
                                             isolated_cache):
    """글자만으로는 판단할 수 없다 — 사진이 보여야 이 창이 하는 일을 한다."""
    from aoi_verification.app.ui.widgets.t_photo_ask_dialog import TPhotoAskDialog

    img = tmp_path / "-86955.68631.t.1.jpg"
    _png(img)
    dlg = TPhotoAskDialog(img, 42)
    dlg.show()
    for _ in range(6):
        styled_qapp.processEvents()
    pix = dlg._preview.pixmap()
    assert pix is not None and not pix.isNull(), "예시 사진이 안 보인다"
    # 몇 장이 걸렸는지 · 어느 파일인지도 함께 말해야 판단할 수 있다.
    from PyQt6.QtWidgets import QLabel
    texts = " ".join(w.text() for w in dlg.findChildren(QLabel))
    assert "42" in texts, f"몇 장인지 안 적혀 있다: {texts!r}"
    assert img.name in texts, f"어느 파일인지 안 적혀 있다: {texts!r}"
    dlg.deleteLater()


def test_the_two_buttons_are_the_answer(styled_qapp, tmp_path, isolated_cache):
    """[포함하고 진행] / [제외하고 진행] — 각각 `include` 를 정한다."""
    from aoi_verification.app.ui.widgets.t_photo_ask_dialog import TPhotoAskDialog

    img = tmp_path / "1.2.t.3.jpg"
    _png(img)
    yes = TPhotoAskDialog(img, 1)
    yes.show()
    styled_qapp.processEvents()
    assert yes.include is False, "묻기도 전에 포함으로 기울어 있다"
    yes.btn_include.click()
    assert yes.include is True

    no = TPhotoAskDialog(img, 1)
    no.show()
    styled_qapp.processEvents()
    no.btn_exclude.click()
    assert no.include is False
    yes.deleteLater()
    no.deleteLater()


def test_closing_the_dialog_means_exclude(styled_qapp, tmp_path, isolated_cache):
    """닫기/Esc 를 '포함' 으로 해석하면 묻지 않은 것과 같아진다."""
    from aoi_verification.app.ui.widgets.t_photo_ask_dialog import TPhotoAskDialog

    img = tmp_path / "1.2.t.3.jpg"
    _png(img)
    dlg = TPhotoAskDialog(img, 1)
    dlg.show()
    styled_qapp.processEvents()
    dlg.reject()                            # Esc 와 같은 경로
    assert dlg.include is False
    dlg.deleteLater()


def test_an_unreadable_photo_still_lets_you_answer(styled_qapp, tmp_path,
                                                   isolated_cache):
    """미리보기를 못 읽어도 **묻는 것이 본체**다 — 창은 떠야 한다."""
    from aoi_verification.app.ui.widgets.t_photo_ask_dialog import TPhotoAskDialog

    bad = tmp_path / "1.2.t.3.jpg"
    bad.write_bytes(b"not an image")
    dlg = TPhotoAskDialog(bad, 1)
    dlg.show()
    for _ in range(4):
        styled_qapp.processEvents()
    assert dlg._preview.text(), "미리보기 실패를 말하지 않는다"
    dlg.btn_include.click()
    assert dlg.include is True
    dlg.deleteLater()


def test_the_prompt_is_actually_wired_into_the_scan_flow(qapp, monkeypatch,
                                                         tmp_path):
    """★ 메서드가 있어도 **불리지 않으면** 아무 일도 안 한다.

    실제로 이 가드를 쓰기 전에는 `_on_scan_done` 에서 호출을 지워도 다른 테스트가
    전부 통과했다 — 호출부를 직접 부르는 테스트만 있었기 때문이다."""
    from aoi_verification.app.ui.pages.setup_page import SetupInput

    monkeypatch.setattr(mw.MainWindow, "_start_backend_import_async",
                        lambda self: None)
    win = mw.MainWindow()
    try:
        called: list = []
        monkeypatch.setattr(mw.MainWindow, "_ask_about_t_photos",
                            lambda self, sr: called.append(sr))
        # 스캔 뒤 단계는 이 테스트와 무관하다 — 여기서 끊는다.
        monkeypatch.setattr(mw.MainWindow, "_after_slot_resolved",
                            lambda self, sr: None)
        win._input = SetupInput(
            mode="single", ref_root=tmp_path / "ref", val_root=tmp_path / "val",
            ref_machine="1호기", val_machine="2호기", threshold=0.55)
        sr = _scan(with_t=True)
        win._on_scan_done(win._scan_token, sr)
        assert called and called[0] is sr, \
            "스캔이 끝났는데 `.t.` 사진을 묻지 않는다(호출이 배선돼 있지 않다)"
    finally:
        win.close()


def test_the_prompt_runs_before_thumbnails_are_built(qapp, monkeypatch,
                                                     tmp_path):
    """제외할 사진의 썸네일을 헛되이 굽지 않는다 — 묻는 것이 **먼저**다."""
    from aoi_verification.app.ui.pages.setup_page import SetupInput

    monkeypatch.setattr(mw.MainWindow, "_start_backend_import_async",
                        lambda self: None)
    win = mw.MainWindow()
    try:
        order: list = []
        real = mw.MainWindow._ask_about_t_photos
        monkeypatch.setattr(
            mw.MainWindow, "_ask_about_t_photos",
            lambda self, sr: (order.append("ask"), real(self, sr))[0])
        monkeypatch.setattr(mw.MainWindow, "_continue_start_after_scan",
                            lambda self, common: order.append("thumbs"))
        _wire(monkeypatch, include=False)
        win._input = SetupInput(
            mode="single", ref_root=tmp_path / "ref", val_root=tmp_path / "val",
            ref_machine="1호기", val_machine="2호기", threshold=0.55)
        win._on_scan_done(win._scan_token, _scan(with_t=True))
        assert order[:2] == ["ask", "thumbs"], f"순서가 뒤집혔다: {order}"
    finally:
        win.close()
