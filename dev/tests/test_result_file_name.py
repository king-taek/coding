"""결과 엑셀 제목 규칙 + 저장 직전 확인창 (UI 관련 PDF ⑥·⑧).

사용자 요청:

    "Excel 파일제목의 형식을 고정하고자 함 … 그래서 `4F-AOI-03 RDL4_GFW
     검증(AOI-24 기준)` 이런 식으로 고정하자. '장비Layer_자재검증(장비기준)'
     Layer명과 자재명은 WaferInfo.ini 에 있음(slot 중 1개에서만 읽으면 나머진 동일함).
     WaferInfo.ini 에 InputLot=GFW-RDL4 이렇게 적혀있음 — 여기서 GFW 가 자재고,
     RDL4 가 Layer 임.  이 규칙으로 추천 파일제목을 작성해주고 사용자가 추천
     파일제목에서 수정할 수 있도록 창을 띄워줘.  근데 파일 저장은 (검증과) 동시에
     진행하고, 파일제목을 수정하거나 결정하는 동안 파일은 이미 저장중인 거로 하자."

⚠ `WaferInfo.ini` 는 `Params_WaferInfo.ini` 와 **다른 파일**이다 — 이름이 비슷하지만
그쪽은 die pitch 가 들어 있고 `InputLot` 이 없다.  헷갈리면 조용히 아무것도 못 읽는다.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from aoi_verification.app.models.lot_info import read_lot_info    # noqa: E402

_REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# WaferInfo.ini 읽기
# ---------------------------------------------------------------------------
def _lot_ini(folder: Path, line: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "WaferInfo.ini").write_text(
        f"[Recipe]\nName=PI_Bubble\n[AutoCycleInfo]\nAutoCycleScan=1\n{line}\n"
        "InputWaferID=25195007EWF6\n", encoding="utf-8")


def test_input_lot_splits_into_material_and_layer(tmp_path):
    """`InputLot=GFW-RDL4` → 자재 GFW · Layer RDL4 (사용자가 준 예시 그대로)."""
    _lot_ini(tmp_path / "Slot_01", "InputLot=GFW-RDL4")
    lot = read_lot_info(tmp_path)
    assert lot is not None and (lot.material, lot.layer) == ("GFW", "RDL4")


def test_it_reads_the_real_sample_in_the_repo(tmp_path):
    """저장소의 실물 샘플(`docs/WaferInfo.ini`)로 확인한다 — 합성값만 믿지 않는다."""
    sample = _REPO / "docs" / "WaferInfo.ini"
    assert sample.is_file(), "실물 샘플이 사라졌다"
    slot = tmp_path / "Slot_01"
    slot.mkdir(parents=True)
    shutil.copy(sample, slot / "WaferInfo.ini")
    lot = read_lot_info(tmp_path)
    assert lot is not None, "실물 샘플에서 아무것도 못 읽었다"
    assert (lot.material, lot.layer) == ("TBD", "PIDS3")


def test_params_wafer_info_is_a_different_file(tmp_path):
    """`Params_WaferInfo.ini` 만 있으면 못 읽는다 — 둘을 헷갈리지 않게 못 박는다."""
    sample = _REPO / "docs" / "Params_WaferInfo.ini"
    assert sample.is_file()
    slot = tmp_path / "Slot_01"
    slot.mkdir(parents=True)
    shutil.copy(sample, slot / "Params_WaferInfo.ini")
    assert read_lot_info(tmp_path) is None


def test_only_one_slot_is_read(tmp_path, monkeypatch):
    """슬롯 하나만 읽는다 — 같은 로트는 자재·Layer 가 같다(사용자 확인).

    전부 읽으면 폴더 수만큼 왕복이 늘 뿐이다(설정 화면 die 안내가 겪은 그 비용)."""
    import aoi_verification.app.models.lot_info as mod

    for i in range(5):
        _lot_ini(tmp_path / f"Slot_{i + 1:02d}", "InputLot=GFW-RDL4")
    opened: list = []
    real = mod.read_ini_text
    monkeypatch.setattr(mod, "read_ini_text",
                        lambda p: (opened.append(p), real(p))[1])
    assert read_lot_info(tmp_path) is not None
    assert len(opened) == 1, f"INI 를 {len(opened)}번 읽었다: {opened}"


def test_hidden_folders_are_not_the_sample(tmp_path):
    """점 폴더가 이름순 첫 자리를 차지해도 표본은 진짜 슬롯이어야 한다."""
    (tmp_path / ".aoi_verification_cache").mkdir()
    _lot_ini(tmp_path / "Slot_01", "InputLot=GFW-RDL4")
    lot = read_lot_info(tmp_path)
    assert lot is not None and lot.material == "GFW"


def test_unreadable_lot_is_none_not_a_crash(tmp_path):
    """전 구간 fail-safe — 못 읽으면 None(호출부가 폴백 제목을 쓴다)."""
    assert read_lot_info(tmp_path) is None                 # 빈 폴더
    _lot_ini(tmp_path / "S1", "InputLot=하이픈없음")
    assert read_lot_info(tmp_path) is None                 # 형식이 다르다
    assert read_lot_info(tmp_path / "없는폴더") is None


def test_utf16_ini_is_read(tmp_path):
    """장비가 UTF-16 으로 쓴 INI 도 읽는다(`coords.ini_text` 를 쓰는 이유)."""
    slot = tmp_path / "S1"
    slot.mkdir(parents=True)
    (slot / "WaferInfo.ini").write_text(
        "[AutoCycleInfo]\nInputLot=GFW-RDL4\n", encoding="utf-16")
    lot = read_lot_info(tmp_path)
    assert lot is not None and lot.layer == "RDL4"


# ---------------------------------------------------------------------------
# 제목 조립
# ---------------------------------------------------------------------------
def test_the_suggested_title_matches_the_users_example(tmp_path):
    """`4F-AOI-03 RDL4_GFW 검증(AOI-24 기준).xlsx` — 요청서의 예시 그대로."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.main_window import MainWindow
    from aoi_verification.app.ui.pages.setup_page import SetupInput

    _lot_ini(tmp_path / "Slot_01", "InputLot=GFW-RDL4")
    inp = SetupInput(mode="single", ref_root=tmp_path, val_root=tmp_path,
                     ref_machine="AOI-24", val_machine="4F-AOI-03",
                     threshold=0.55)
    assert MainWindow._suggest_result_name(inp) == \
        "4F-AOI-03 RDL4_GFW 검증(AOI-24 기준).xlsx"


def test_the_title_falls_back_when_the_lot_is_unknown(tmp_path):
    """못 읽어도 저장은 되어야 한다 — 예전 이름으로 폴백한다."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app import i18n
    from aoi_verification.app.ui.main_window import MainWindow
    from aoi_verification.app.ui.pages.setup_page import SetupInput

    inp = SetupInput(mode="single", ref_root=tmp_path, val_root=tmp_path,
                     ref_machine="AOI-24", val_machine="4F-AOI-03",
                     threshold=0.55)
    assert MainWindow._suggest_result_name(inp) == \
        i18n.KO.RESULT_FILE_TITLE_FALLBACK_FMT.format(
            val="4F-AOI-03", ref="AOI-24")


def test_the_lot_is_read_from_the_val_side(tmp_path):
    """Layer·자재는 **검증 대상 폴더**에서 읽는다(사용자 결정) — 기준이 아니다."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.main_window import MainWindow
    from aoi_verification.app.ui.pages.setup_page import SetupInput

    ref, val = tmp_path / "ref", tmp_path / "val"
    _lot_ini(ref / "S1", "InputLot=REF-LAYERA")
    _lot_ini(val / "S1", "InputLot=VAL-LAYERB")
    inp = SetupInput(mode="single", ref_root=ref, val_root=val,
                     ref_machine="AOI-24", val_machine="4F-AOI-03",
                     threshold=0.55)
    name = MainWindow._suggest_result_name(inp)
    assert "LAYERB_VAL" in name, f"기준 폴더에서 읽었다: {name}"


# ---------------------------------------------------------------------------
# 이름 확인창
# ---------------------------------------------------------------------------
def test_name_rules(tmp_path):
    """순수 함수 — 파일시스템을 보지 않는다."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets.save_name_dialog import sanitize_name

    ok, err = sanitize_name("4F-AOI-03 RDL4_GFW 검증(AOI-24 기준).xlsx")
    assert err == "" and ok.endswith(".xlsx")
    # 확장자를 지웠으면 되살린다 — 없으면 엑셀이 열지 못한다.
    assert sanitize_name("이름만") == ("이름만.xlsx", "")
    assert sanitize_name("이름.XLSX")[0] == "이름.XLSX"     # 대문자도 확장자다
    for bad in ("", "   ", ".xlsx"):
        assert sanitize_name(bad)[1], f"빈 이름을 통과시켰다: {bad!r}"
    for bad in ('a/b', 'a\\b', 'a:b', 'a*b', 'a?b', 'a"b', 'a<b', 'a>b', 'a|b'):
        assert sanitize_name(bad)[1], f"금지 문자를 통과시켰다: {bad!r}"


def test_the_dialog_locks_confirm_on_a_bad_name(styled_qapp, tmp_path,
                                                isolated_cache):
    """누르기 **전에** 이유를 말한다 — OS 오류로 저장이 실패하고 나서가 아니라."""
    from aoi_verification.app.ui.widgets.save_name_dialog import SaveNameDialog

    dlg = SaveNameDialog("결과.xlsx", tmp_path)
    dlg.show()
    styled_qapp.processEvents()
    assert dlg.btn_ok.isEnabled()
    dlg.edit.setText("a/b")
    styled_qapp.processEvents()
    assert not dlg.btn_ok.isEnabled(), "쓸 수 없는 이름인데 확인이 열려 있다"
    assert dlg._err.text(), "이유를 말하지 않는다"
    dlg.deleteLater()


def test_the_dialog_preselects_the_stem_not_the_extension(styled_qapp, tmp_path,
                                                          isolated_cache):
    """바로 타이핑해도 `.xlsx` 는 살아남아야 한다."""
    from aoi_verification.app.ui.widgets.save_name_dialog import SaveNameDialog

    dlg = SaveNameDialog("결과 파일.xlsx", tmp_path)
    dlg.show()
    styled_qapp.processEvents()
    assert dlg.edit.selectedText() == "결과 파일", \
        f"선택 범위가 확장자를 먹었다: {dlg.edit.selectedText()!r}"
    dlg.deleteLater()


def test_confirming_returns_the_name(styled_qapp, tmp_path, isolated_cache):
    from aoi_verification.app.ui.widgets.save_name_dialog import SaveNameDialog

    dlg = SaveNameDialog("결과.xlsx", tmp_path)
    dlg.show()
    styled_qapp.processEvents()
    dlg.edit.setText("내 결과")
    dlg.btn_ok.click()
    assert dlg.chosen == "내 결과.xlsx"
    dlg.deleteLater()


def test_cancelling_returns_nothing(styled_qapp, tmp_path, isolated_cache):
    from aoi_verification.app.ui.widgets.save_name_dialog import SaveNameDialog

    dlg = SaveNameDialog("결과.xlsx", tmp_path)
    dlg.show()
    styled_qapp.processEvents()
    dlg.btn_cancel.click()
    assert dlg.chosen is None
    dlg.deleteLater()


# ---------------------------------------------------------------------------
# 저장 흐름 — 창은 **이름만** 정하고, 저장을 기다리게 하지 않는다
# ---------------------------------------------------------------------------
def _result_page(qapp, monkeypatch, target: Path, chosen):
    """결과 페이지 + 이름 확인창 대역.  `chosen=None` 이면 취소한 것."""
    from aoi_verification.app.ui.pages import result_page as rp

    page = rp.ResultPage()
    page._result = object()                 # `_on_export` 는 None 여부만 본다
    page._target_path = target
    page._exported = False

    asked: list = []

    class _Dlg:
        def __init__(self, suggested, folder, parent=None):
            asked.append((suggested, Path(folder)))
            self.chosen = chosen

    import aoi_verification.app.ui.widgets.save_name_dialog as mod
    monkeypatch.setattr(mod, "SaveNameDialog", _Dlg)
    monkeypatch.setattr(rp.sheets, "run", lambda *a, **k: 1)

    started: list = []
    monkeypatch.setattr(rp, "ExcelExporter",
                        lambda *a, **k: started.append(a) or _NoExporter())
    return page, asked, started


class _NoExporter:
    """내보내기 워커 대역 — 시그널만 흉내 낸다."""

    class _S:
        def __init__(self):
            for n in ("progress", "done", "failed"):
                setattr(self, n, _Sig())

    def __init__(self):
        self.signals = self._S()

    def start(self):
        pass


class _Sig:
    def connect(self, *a, **k):
        pass


def test_export_asks_for_the_name_first(styled_qapp, monkeypatch, tmp_path,
                                        isolated_cache):
    """저장 직전에 추천 이름을 보여주고 고칠 기회를 준다(사용자 결정: 마지막 저장 직전)."""
    target = tmp_path / "4F-AOI-03 RDL4_GFW 검증(AOI-24 기준).xlsx"
    target.write_bytes(b"working")
    page, asked, started = _result_page(styled_qapp, monkeypatch, target,
                                        chosen="내가 고친 이름.xlsx")
    page._on_export()
    assert asked, "이름을 묻지 않고 저장했다"
    assert asked[0][0] == target.name, f"추천 이름이 아니다: {asked[0][0]}"
    assert asked[0][1] == tmp_path, "저장 폴더를 잘못 보여 준다"
    assert page._save_path == tmp_path / "내가 고친 이름.xlsx"
    assert started, "이름을 정했는데 저장이 시작되지 않았다"
    page.deleteLater()


def test_the_working_file_is_moved_not_left_behind(styled_qapp, monkeypatch,
                                                   tmp_path, isolated_cache):
    """검증 시작 때 만든 사본이 결과 폴더에 유령으로 남으면 안 된다."""
    target = tmp_path / "추천.xlsx"
    target.write_bytes(b"working")
    page, _asked, _started = _result_page(styled_qapp, monkeypatch, target,
                                          chosen="확정.xlsx")
    page._on_export()
    assert not target.exists(), "옛 이름 파일이 남았다"
    assert (tmp_path / "확정.xlsx").read_bytes() == b"working", \
        "작업 파일이 새 이름으로 옮겨지지 않았다"
    page.deleteLater()


def test_cancelling_the_name_cancels_the_save(styled_qapp, monkeypatch,
                                              tmp_path, isolated_cache):
    """취소는 **아무것도 하지 않는다** — 파일 이름도 그대로다."""
    target = tmp_path / "추천.xlsx"
    target.write_bytes(b"working")
    page, _asked, started = _result_page(styled_qapp, monkeypatch, target,
                                         chosen=None)
    page._on_export()
    assert started == [], "취소했는데 저장이 시작됐다"
    assert target.exists(), "취소했는데 파일 이름이 바뀌었다"
    page.deleteLater()


def test_saving_again_does_not_ask_twice(styled_qapp, monkeypatch, tmp_path,
                                         isolated_cache):
    """'다시 저장' 은 같은 파일에 덮어쓰는 것이 약속이다 — 매번 묻지 않는다."""
    target = tmp_path / "확정.xlsx"
    target.write_bytes(b"working")
    page, asked, started = _result_page(styled_qapp, monkeypatch, target,
                                        chosen="다른 이름.xlsx")
    page._exported = True                   # 이미 한 번 저장했다
    page._on_export()
    assert asked == [], "다시 저장인데 또 물었다"
    assert page._save_path == target
    assert started
    page.deleteLater()


def test_a_failed_rename_still_saves(styled_qapp, monkeypatch, tmp_path,
                                     isolated_cache):
    """파일이 열려 있어 못 옮겨도 저장 자체는 되어야 한다 — 이름이 저장을 막지 않는다."""
    import aoi_verification.app.ui.pages.result_page as rp

    target = tmp_path / "추천.xlsx"
    target.write_bytes(b"working")
    page, _asked, started = _result_page(styled_qapp, monkeypatch, target,
                                         chosen="확정.xlsx")

    def boom(*a, **k):
        raise OSError("다른 프로그램이 사용 중입니다")

    monkeypatch.setattr(rp.os, "replace", boom)
    page._on_export()
    assert page._save_path == tmp_path / "확정.xlsx", "새 경로로 저장하지 않았다"
    assert started, "이름 변경이 실패했다고 저장을 포기했다"
    page.deleteLater()
