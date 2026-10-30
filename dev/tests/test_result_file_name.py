"""결과 엑셀 제목 규칙 + 저장 직전 확인창 (UI 관련 PDF ⑥·⑧).

사용자 요청(처음):

    "Excel 파일제목의 형식을 고정하고자 함 … 그래서 `4F-AOI-03 RDL4_GFW
     검증(AOI-24 기준)` 이런 식으로 고정하자. '장비Layer_자재검증(장비기준)' …
     이 규칙으로 추천 파일제목을 작성해주고 사용자가 추천 파일제목에서 수정할 수
     있도록 창을 띄워줘."

사용자 결정(2026-10-30): 자재는 LOT 이름 앞글자(GFW)가 아니라 **제품 이름(TB500)** 이다.
Camtek Job 이름·제품코드, KLA DeviceID·StepID 에서 추천하고, 못 읽은 제품은 빈 칸으로
두어 저장 창에서 채운다(제품코드 → 이름 표는 만들지 않는다).  저장 창 이름 아래에 칸을
늘어놓고 [제목 자동 생성] 버튼으로 이름을 다시 만든다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from aoi_verification.app.models.lot_info import (    # noqa: E402
    TitleParts, parse_title_parts, read_title_parts)

_REPO = Path(__file__).resolve().parents[2]
_SAMPLES = _REPO / "dev" / "좌표 확인"


# ---------------------------------------------------------------------------
# 장비 문자열 → 자재·Layer (순수 함수)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("job, want", [
    # 저장소 샘플의 실제 Camtek Job 이름(Params_WaferInfo.ini SetupPath, 관측).
    ("TB500_RDL4 - Multi", ("TB500", "RDL4")),
    ("TB500_RDL4 - Multi - Swelling", ("TB500", "RDL4")),
    ("Test_TB500_RDL2 - Multi", ("TB500", "RDL2")),
    ("R_TB500_LIVE_PI4", ("TB500", "PI4")),
    ("R_T254 LIVE_0858711PD_FS UBM", ("T254", "")),
    # 자재 앞의 레시피 접두(R5·A01)는 Layer 로 잡지 않는다.
    ("2D@R5-AS-T254-A01_0857122AY-0B", ("T254", "")),
    # 제품 이름이 Job 에 없는 제품은 비워 둔다 — 틀린 값보다 빈 칸(사용자가 채움).
    ("2D@R2-15966TA0-BW2_0859654PD-0C", ("", "")),
    ("2D@RE-W97218SGB1K16_0860463PD-0A", ("", "")),
    ("TestProduct", ("", "")),
    # Layer 처럼 생긴 레시피 토큰은 Layer 가 아니다(아는 Layer 이름만).
    ("TB500_ECO3 - Multi", ("TB500", "")),
    ("TB500_PIDS3", ("TB500", "PIDS3")),
])
def test_camtek_job_name(job, want):
    tp = parse_title_parts(job=job)
    assert (tp.material, tp.layer) == want


def test_kla_device_and_step():
    """KLA 정보파일 헤더 — StepID 는 첫 토큰만(`RDL4_AUTO_RBB` → RDL4)."""
    tp = parse_title_parts(device="4DT-TB500-H-M1", step="RDL4_AUTO_RBB")
    assert (tp.material, tp.layer) == ("TB500", "RDL4")
    tp = parse_title_parts(device="4DT-T254-H-M1", step="RDL2_NEW(MULTI)")
    assert (tp.material, tp.layer) == ("T254", "RDL2")
    assert parse_title_parts(device="4DT-TB500-H-M1", step="AUTO1_RDL4").layer == ""


def test_job_name_wins_over_device_code():
    """Camtek 제품코드(`AST254-AG6S2-6CAA`)는 자재 토큰이 없어도 Job 이름으로 채운다."""
    tp = parse_title_parts(job="2D@R5-AS-T254-A01_0857122AY-0B",
                           device="AST254-AG6S2-6CAA")
    assert tp.material == "T254"


def test_lot_name_is_not_used(tmp_path):
    """LOT 이름(`InputLot=GFW-RDL4`)은 더 이상 쓰지 않는다 — GFW 는 자재가 아니다."""
    slot = tmp_path / "S1"
    slot.mkdir()
    (slot / "WaferInfo.ini").write_text("[AutoCycleInfo]\nInputLot=GFW-RDL4\n",
                                        encoding="utf-8")
    assert read_title_parts(tmp_path) == TitleParts()


# ---------------------------------------------------------------------------
# 폴더에서 읽기
# ---------------------------------------------------------------------------
def _camtek_slot(folder: Path, job: str, dev: str | None = None,
                 encoding: str = "utf-8") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Params_WaferInfo.ini").write_text(
        f"[Path]\nSetupPath=c:\\job\\{job}\\Setup1\\Recipes\\x20\\\n",
        encoding=encoding)
    if dev:
        (folder / "ProductInfo.ini").write_text(
            f"[GENERAL]\nJobDesc=DEV={dev}~LOT=X@1~ID=#FI\n", encoding=encoding)


def test_camtek_folder_is_read(tmp_path):
    _camtek_slot(tmp_path / "19246095EWB7", "TB500_RDL4 - Multi")
    assert read_title_parts(tmp_path) == TitleParts("TB500", "RDL4")


def test_utf16_ini_is_read(tmp_path):
    """장비가 UTF-16 으로 쓴 INI 도 읽는다(`coords.ini_text` 를 쓰는 이유)."""
    _camtek_slot(tmp_path / "S1", "TB500_RDL4 - Multi", encoding="utf-16")
    assert read_title_parts(tmp_path).layer == "RDL4"


def test_kla_folder_is_read(tmp_path):
    slot = tmp_path / "2026-05-18-11-24_5"
    slot.mkdir()
    (slot / "abc_TB500INT.271@6324_W6459081XYE1.001").write_text(
        'FileVersion 1 2;\nLotID "TB500INT.271@6324";\n'
        'DeviceID "4DT-TB500-H-M1";\nStepID "RDL2_NEW(MULTI)";\n'
        'WaferID "W6459081XYE1";\nSlot 5;\n', encoding="utf-8")
    assert read_title_parts(tmp_path) == TitleParts("TB500", "RDL2")


def test_system_folder_is_not_the_sample_slot(tmp_path):
    """`$RECYCLE.BIN` 은 이름순으로 슬롯보다 앞이라 표본이 되면 칸이 비었다(리뷰 지적)."""
    (tmp_path / "$RECYCLE.BIN").mkdir()
    _camtek_slot(tmp_path / "a", "TB500_RDL4 - Multi")
    assert read_title_parts(tmp_path) == TitleParts("TB500", "RDL4")


def test_unreadable_is_empty(tmp_path):
    assert read_title_parts(tmp_path) == TitleParts()          # 빈 폴더
    assert read_title_parts(tmp_path / "없는폴더") == TitleParts()


# 저장소 샘플 폴더(맨 위 이름) → 기대값.  샘플 폴더 이름표와 장비 파일(SetupPath·DeviceID)
# 로 확인한 값(관측, 2026-10-30).  읽은 값은 **이 중 하나**여야 한다 — 빈 칸은 허용.
_SAMPLE_EXPECT = {
    "KLA": {("TB500", "RDL2")},
    "KLA_Camtek(TB500 RDL4)": {("TB500", "RDL4")},
    "die맵": {("", ""), ("T254", ""), ("TB500", "RDL4")},
    "die목록(DieLut)": {("", ""), ("T254", ""), ("TB500", "RDL2")},
    "장비격자(ExportData)": {("", ""), ("T254", "")},
    "화면시프트(T254 FS UBM)": {("T254", "")},
}


def test_real_samples_give_only_known_values():
    """저장소 실물 샘플 전부 — 틀린 값은 불허, 못 읽는 것(빈 칸)만 허용."""
    from aoi_verification.app.models.lot_info import _read_folder

    if not _SAMPLES.is_dir():
        pytest.skip("샘플 없음")
    seen = 0
    for d in sorted({p.parent for p in _SAMPLES.rglob("*")
                     if p.name in ("Params_WaferInfo.ini", "ProductInfo.ini")
                     or p.suffix == ".001"}):
        top = d.relative_to(_SAMPLES).parts[0]
        tp = _read_folder(d)
        assert top in _SAMPLE_EXPECT, f"새 샘플 폴더 — 기대값을 적어라: {top}"
        assert (tp.material, tp.layer) in _SAMPLE_EXPECT[top], (str(d), tp)
        seen += bool(tp.material)
    assert seen >= 80, f"샘플에서 자재를 거의 못 읽었다: {seen}"


# ---------------------------------------------------------------------------
# 제목 조립
# ---------------------------------------------------------------------------
def test_compose_title():
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets.save_name_dialog import compose_title

    f = {"val": "4F-AOI-03", "layer": "RDL4", "material": "TB500", "ref": "AOI-24"}
    assert compose_title(f) == "4F-AOI-03 RDL4_TB500 검증(AOI-24 기준).xlsx"
    assert compose_title({**f, "layer": ""}) == "4F-AOI-03 TB500 검증(AOI-24 기준).xlsx"
    assert compose_title({**f, "layer": " ", "material": ""}) == \
        "AOI 4F-AOI-03 검증 (AOI-24 기준).xlsx"
    ex = {"machine": "17", "layer": "RDL4", "material": "TB500"}
    assert compose_title(ex) == "17 RDL4_TB500 Defect 추출.xlsx"
    assert compose_title({**ex, "layer": "", "material": ""}) == "AOI 17 Defect 추출.xlsx"
    # 장비 칸을 비워도 이름에 이중 공백이 남지 않는다.
    assert "  " not in compose_title({**f, "val": ""})


def _inp(ref, val):
    from aoi_verification.app.ui.pages.setup_page import SetupInput
    return SetupInput(mode="single", ref_root=ref, val_root=val,
                      ref_machine="AOI-24", val_machine="4F-AOI-03",
                      threshold=0.55)


def test_the_suggested_title_reads_the_val_side_first(tmp_path):
    """검증 대상 폴더가 먼저, 못 읽은 Layer 는 **같은 제품일 때만** 기준 폴더에서."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.main_window import MainWindow

    ref, val = tmp_path / "ref", tmp_path / "val"
    _camtek_slot(ref / "S1", "TB500_RDL2 - Multi")
    _camtek_slot(val / "S1", "R_TB500_LIVE")                     # Layer 없음
    assert MainWindow._suggest_result_name(_inp(ref, val)) == \
        "4F-AOI-03 RDL2_TB500 검증(AOI-24 기준).xlsx"
    assert MainWindow._title_fields(_inp(ref, val)) == {
        "val": "4F-AOI-03", "layer": "RDL2", "material": "TB500", "ref": "AOI-24"}


def test_other_products_are_not_mixed(tmp_path):
    """자재가 다르면 기준 폴더의 Layer 를 끌어오지 않는다 — 'RDL2_T254' 같은 없는 조합."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.main_window import MainWindow

    ref, val = tmp_path / "ref", tmp_path / "val"
    _camtek_slot(ref / "S1", "TB500_RDL2 - Multi")
    _camtek_slot(val / "S1", "R_T254 LIVE_0858711PD_FS UBM")
    assert MainWindow._suggest_result_name(_inp(ref, val)) == \
        "4F-AOI-03 T254 검증(AOI-24 기준).xlsx"


def test_ref_side_is_used_when_val_side_has_nothing(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.main_window import MainWindow

    ref, val = tmp_path / "ref", tmp_path / "val"
    _camtek_slot(ref / "S1", "TB500_RDL4 - Multi")
    (val / "S1").mkdir(parents=True)
    assert MainWindow._suggest_result_name(_inp(ref, val)) == \
        "4F-AOI-03 RDL4_TB500 검증(AOI-24 기준).xlsx"


def test_parent_of_the_chosen_folder_is_not_read(tmp_path):
    """고른 폴더의 **부모**는 관계없는 공용 폴더일 수 있다 — 거기 INI 를 읽지 않는다."""
    _camtek_slot(tmp_path, "TB500_RDL4 - Multi")                 # 부모에 놓인 INI
    (tmp_path / "LOT" / "S1").mkdir(parents=True)
    assert read_title_parts(tmp_path / "LOT") == TitleParts()


def test_the_title_falls_back_when_nothing_is_read(tmp_path):
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


# ---------------------------------------------------------------------------
# 이름 확인창
# ---------------------------------------------------------------------------
def test_name_rules(tmp_path):
    """순수 함수 — 파일시스템을 보지 않는다."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets.save_name_dialog import sanitize_name

    ok, err = sanitize_name("4F-AOI-03 RDL4_TB500 검증(AOI-24 기준).xlsx")
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
# 결과 파일은 [검증 시작] 때 만들지 않는다 — 저장할 때 생긴다
#
# 사용자 신고: "지금은 검증 시작하자마자 양식 파일 생성되는데, 마지막에 저장버튼
# 누르면 그때 저장하도록.  안그러면 빈 파일이 너무 많아짐."  저장까지 안 간 세션
# (도중에 접거나 그냥 닫은 경우)마다 양식 그대로인 xlsx 가 결과 폴더에 남았다.
# ---------------------------------------------------------------------------
def _stub_window(monkeypatch, tmp_path: Path):
    """`MainWindow` 를 통째로 띄우지 않고 `_prepare_working_file` 만 떼어 시험한다."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui import main_window as mw
    from aoi_verification.app.utils import paths

    results = tmp_path / "결과"
    results.mkdir()
    monkeypatch.setattr(paths, "results_dir", lambda: results)
    win = mw.MainWindow.__new__(mw.MainWindow)
    win._working_xlsx = None
    win._template_used = None
    win._result_page = _FakeResultPage()
    return win, results


class _FakeResultPage:
    """저장 창에 넘길 제목 칸을 받아 두기만 한다."""
    fields = None

    def set_title_fields(self, fields):
        self.fields = fields


def _input(tmp_path: Path):
    from aoi_verification.app.ui.pages.setup_page import SetupInput

    _camtek_slot(tmp_path / "val" / "Slot_01", "TB500_RDL4 - Multi")
    return SetupInput(mode="single", ref_root=tmp_path / "ref",
                      val_root=tmp_path / "val", ref_machine="AOI-24",
                      val_machine="4F-AOI-03", threshold=0.55)


def test_starting_a_run_creates_no_file(monkeypatch, tmp_path, isolated_cache):
    """★ [검증 시작] 뒤 결과 폴더는 **비어 있다** — 경로만 정해진다."""
    from aoi_verification.app.ui import main_window as mw

    win, results = _stub_window(monkeypatch, tmp_path)
    mw.MainWindow._prepare_working_file(win, _input(tmp_path))
    assert list(results.iterdir()) == [], \
        f"검증 시작만 했는데 파일이 생겼다: {[p.name for p in results.iterdir()]}"
    assert win._working_xlsx == \
        results / "4F-AOI-03 RDL4_TB500 검증(AOI-24 기준).xlsx"
    assert win._template_used is not None and win._template_used.exists(), \
        "양식 원본은 저장 때 필요하다 — 경로를 잃으면 안 된다"
    assert win._result_page.fields == {"val": "4F-AOI-03", "layer": "RDL4",
                                       "material": "TB500", "ref": "AOI-24"}


def test_the_name_still_avoids_last_sessions_file(monkeypatch, tmp_path,
                                                  isolated_cache):
    """지난 세션의 결과가 같은 이름으로 있으면 덮지 않는다 — 타임스탬프를 붙인다."""
    from aoi_verification.app.ui import main_window as mw

    win, results = _stub_window(monkeypatch, tmp_path)
    (results / "4F-AOI-03 RDL4_TB500 검증(AOI-24 기준).xlsx").write_bytes(b"old")
    mw.MainWindow._prepare_working_file(win, _input(tmp_path))
    assert win._working_xlsx.parent == results
    assert win._working_xlsx.name.startswith("4F-AOI-03 RDL4_TB500 검증(AOI-24 기준)_")
    assert not win._working_xlsx.exists(), "여전히 파일을 미리 만든다"


def test_the_file_appears_only_when_saving(monkeypatch, tmp_path, isolated_cache):
    """실제 저장기(exporter)가 그 경로에 파일을 **처음** 만든다 — 미리 만든 사본에
    기대지 않는다."""
    pytest.importorskip("openpyxl")
    from aoi_verification.app.models.result import FinalResult
    from aoi_verification.app.utils import paths
    from aoi_verification.app.workers.exporter import ExcelExporter

    dst = tmp_path / "결과" / "없던 파일.xlsx"
    dst.parent.mkdir()
    result = FinalResult(mode="single", ref_machine="1호기", val_machine="2호기")
    assert not dst.exists()
    ExcelExporter(result, dst_path=dst, template_path=paths.template_path()).run()
    assert dst.exists(), "저장이 파일을 만들지 못했다(미리 복사한 사본에 기대고 있었다)"


# ---------------------------------------------------------------------------
# 저장 흐름 — 창은 **이름만** 정하고, 저장을 기다리게 하지 않는다
# ---------------------------------------------------------------------------
def _result_page(qapp, monkeypatch, target: Path, chosen):
    """결과 페이지 + 이름 확인창 대역.  `chosen=None` 이면 취소한 것.

    ★ `target` 에 파일을 만들지 않는다 — 검증 시작 때 파일은 없다(위 참조)."""
    from aoi_verification.app.ui.pages import result_page as rp

    page = rp.ResultPage()
    page._result = object()                 # `_on_export` 는 None 여부만 본다
    page._target_path = target
    page._exported = False

    asked: list = []

    class _Dlg:
        def __init__(self, suggested, folder, parent=None, fields=None):
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
    target = tmp_path / "4F-AOI-03 RDL4_TB500 검증(AOI-24 기준).xlsx"
    page, asked, started = _result_page(styled_qapp, monkeypatch, target,
                                        chosen="내가 고친 이름.xlsx")
    page._on_export()
    assert asked, "이름을 묻지 않고 저장했다"
    assert asked[0][0] == target.name, f"추천 이름이 아니다: {asked[0][0]}"
    assert asked[0][1] == tmp_path, "저장 폴더를 잘못 보여 준다"
    assert page._save_path == tmp_path / "내가 고친 이름.xlsx"
    assert started, "이름을 정했는데 저장이 시작되지 않았다"
    page.deleteLater()


def test_a_new_name_is_just_a_new_path(styled_qapp, monkeypatch, tmp_path,
                                       isolated_cache):
    """이름을 고치면 그 이름이 곧 저장 경로다 — 옮길 파일도, 남는 파일도 없다.

    (예전에는 검증 시작 때 만든 작업 파일을 새 이름으로 옮겼다.  파일을 미리 만들지
    않으므로 그 단계 자체가 사라졌다.)"""
    results = tmp_path / "결과"
    results.mkdir()
    target = results / "추천.xlsx"
    page, _asked, started = _result_page(styled_qapp, monkeypatch, target,
                                         chosen="확정.xlsx")
    page._on_export()
    assert page._save_path == results / "확정.xlsx"
    assert page._target_path == results / "확정.xlsx", \
        "다음 '다시 저장' 이 옛 이름으로 간다"
    assert started[0][1] == results / "확정.xlsx", "저장기가 다른 경로로 간다"
    assert list(results.iterdir()) == [], \
        "저장기(대역)가 돌기 전인데 결과 폴더에 파일이 생겼다"
    page.deleteLater()


def test_cancelling_the_name_cancels_the_save(styled_qapp, monkeypatch,
                                              tmp_path, isolated_cache):
    """취소는 **아무것도 하지 않는다** — 파일도 생기지 않는다."""
    results = tmp_path / "결과"
    results.mkdir()
    target = results / "추천.xlsx"
    page, _asked, started = _result_page(styled_qapp, monkeypatch, target,
                                         chosen=None)
    page._on_export()
    assert started == [], "취소했는데 저장이 시작됐다"
    assert list(results.iterdir()) == [], "취소했는데 파일이 생겼다"
    assert page._target_path == target, "취소했는데 저장 경로가 바뀌었다"
    page.deleteLater()


def test_saving_again_does_not_ask_twice(styled_qapp, monkeypatch, tmp_path,
                                         isolated_cache):
    """'다시 저장' 은 같은 파일에 덮어쓰는 것이 약속이다 — 매번 묻지 않는다."""
    target = tmp_path / "확정.xlsx"
    target.write_bytes(b"saved")            # 첫 저장이 만든 파일
    page, asked, started = _result_page(styled_qapp, monkeypatch, target,
                                        chosen="다른 이름.xlsx")
    page._exported = True                   # 이미 한 번 저장했다
    page._on_export()
    assert asked == [], "다시 저장인데 또 물었다"
    assert page._save_path == target
    assert started
    page.deleteLater()


def test_the_auto_button_rebuilds_the_name_from_the_fields(styled_qapp, tmp_path,
                                                           isolated_cache):
    """이름 아래 칸은 추천값으로 차 있고, 고친 뒤 [제목 자동 생성] 이 이름을 다시 만든다."""
    from aoi_verification.app.ui.widgets.save_name_dialog import SaveNameDialog

    fields = {"val": "4F-AOI-03", "layer": "", "material": "TB500", "ref": "AOI-24"}
    dlg = SaveNameDialog("4F-AOI-03 TB500 검증(AOI-24 기준).xlsx", tmp_path,
                         fields=fields)
    dlg.show()
    styled_qapp.processEvents()
    assert dlg._fields["material"].text() == "TB500"
    assert dlg._fields["layer"].text() == ""
    dlg._fields["layer"].setText("RDL4")
    dlg.btn_auto.click()
    assert dlg.edit.text() == "4F-AOI-03 RDL4_TB500 검증(AOI-24 기준).xlsx"
    # 같은 이름의 파일이 이미 있으면 덮지 않게 시각을 붙인다.
    (tmp_path / dlg.edit.text()).write_bytes(b"")
    dlg.btn_auto.click()
    assert dlg.edit.text() != "4F-AOI-03 RDL4_TB500 검증(AOI-24 기준).xlsx"
    assert dlg.edit.text().startswith("4F-AOI-03 RDL4_TB500 검증(AOI-24 기준)_")
    # 칸에 금지 문자를 넣으면 확인이 잠긴다(이름 검사를 그대로 탄다).
    dlg._fields["material"].setText("TB/500")
    dlg.btn_auto.click()
    assert not dlg.btn_ok.isEnabled()
    dlg.deleteLater()


def test_no_fields_no_auto_button(styled_qapp, tmp_path, isolated_cache):
    """칸 정보가 없으면(세션 없이 결과만 보인 경우) 예전 창 그대로다."""
    from aoi_verification.app.ui.widgets.save_name_dialog import SaveNameDialog

    dlg = SaveNameDialog("결과.xlsx", tmp_path)
    assert not hasattr(dlg, "btn_auto") and dlg._fields == {}
    dlg.deleteLater()
