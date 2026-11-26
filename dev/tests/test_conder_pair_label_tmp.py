"""[Conder짝수집-임시] Conder 짝 확인 버튼 — 버튼을 지울 때 이 파일도 함께 지운다.

지키는 것: ① 답 CSV 저장 · 불러오기 · 이어하기 · 다시 답하기, ② zip 내용(답이 없으면 안 만듦),
③ 경로 붙이기, ④ 화면(키 1/2 · 뒤로 · 없는 사진 문구 · 끝나면 zip), ⑤ 표시·문구 목록.
"""

from __future__ import annotations

import re
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "aoi_verification" / "app"
NOW = datetime(2026, 11, 26, 9, 5, 7)


def _mod():
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui import conder_pair_label_tmp as mod
    return mod


def _cands(n=3):
    return [{"id": i + 1, "lot": f"A/B/{i}. LOT (S)", "kla_step": "3. RDL3", "camtek_step": "6. RDL1",
             "wafer": f"W{i}", "kla": f"k{i}.jpg", "camtek": f"c{i}.jpg"} for i in range(n)]


# ── 순수 ──────────────────────────────────────────────────────────────
def test_save_load_round_trip_and_overwrite(tmp_path):
    mod = _mod()
    csv_path = tmp_path / mod.CSV_NAME
    ans = {2: mod.make_row(2, "매치", False, True, NOW)}
    mod.save_answers(csv_path, ans)
    got = mod.load_answers(csv_path)
    assert got[2]["answer"] == "매치" and got[2]["camtek_missing"] == "1" and got[2]["kla_missing"] == "0"
    assert got[2]["answered_at"] == "2026-11-26T09:05:07"
    got[2] = mod.make_row(2, "모름", False, False, NOW)           # 같은 id 는 덮어쓴다
    mod.save_answers(csv_path, got)
    assert mod.load_answers(csv_path)[2]["answer"] == "모름" and len(mod.load_answers(csv_path)) == 1
    assert not list(tmp_path.glob("*.tmp"))
    assert mod.load_answers(tmp_path / "없음.csv") == {}


def test_next_unanswered_resumes_and_wraps():
    mod = _mod()
    c = _cands(4)
    assert mod.next_unanswered(c, {}) == 0
    assert mod.next_unanswered(c, {1: {}, 2: {}}) == 2
    assert mod.next_unanswered(c, {1: {}, 3: {}, 4: {}}, after=2) == 1      # 끝에서 처음으로
    assert mod.next_unanswered(c, {1: {}, 2: {}, 3: {}, 4: {}}) is None


def test_zip_contents_and_nothing_answered(tmp_path):
    mod = _mod()
    csv_path = tmp_path / mod.CSV_NAME
    assert mod.write_zip(csv_path, tmp_path, NOW) is None
    mod.save_answers(csv_path, {})
    assert mod.write_zip(csv_path, tmp_path, NOW) is None
    assert not (tmp_path / "20261126").exists()
    mod.save_answers(csv_path, {1: mod.make_row(1, "매치", False, False, NOW)})
    out = mod.write_zip(csv_path, tmp_path, NOW)
    assert out == tmp_path / "20261126" / "conder짝_확인_0905.zip"
    with zipfile.ZipFile(out) as z:
        assert z.namelist() == [mod.CSV_NAME]
        assert "매치" in z.read(mod.CSV_NAME).decode("utf-8-sig")


def test_photo_path_joins_with_root(tmp_path):
    mod = _mod()
    assert mod.photo_path(str(tmp_path), "a/b/c.jpg") == tmp_path / "a" / "b" / "c.jpg"
    assert mod.photo_path(str(tmp_path), "a\\b.jpg") == tmp_path / "a" / "b.jpg"


# ── 화면 ──────────────────────────────────────────────────────────────
def _dialog(styled_qapp, tmp_path, n=3):
    from PyQt6.QtGui import QColor, QImage
    mod = _mod()
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    for i in range(n):
        for pre in ("k", "c"):
            if (pre, i) == ("c", 1):
                continue                                              # 두 번째 쌍의 Camtek 사진은 없다
            img = QImage(40, 20, QImage.Format.Format_RGB32)
            img.fill(QColor(200, 10, 10))
            assert img.save(str(root / f"{pre}{i}.jpg"))
    base = tmp_path / "out"
    dlg = mod.PairLabelDialog(None, _cands(n), str(root), base, now=lambda: NOW)
    return mod, dlg, base


def test_dialog_keys_advance_save_and_back(styled_qapp, tmp_path):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    mod, dlg, base = _dialog(styled_qapp, tmp_path)
    assert dlg._progress.text() == "1 / 3" and "0. LOT (S)" in dlg._meta.text() and "37" not in dlg._meta.text()
    assert not dlg.back_btn.isEnabled()
    QTest.keyClick(dlg, Qt.Key.Key_1)
    assert dlg._progress.text() == "2 / 3"
    assert dlg._views[1].text() == "사진을 찾지 못했습니다" and dlg._views[0].text() == ""
    rows = mod.load_answers(base / mod.CSV_NAME)
    assert rows[1]["answer"] == "매치" and rows[1]["camtek_missing"] == "0"
    QTest.keyClick(dlg, Qt.Key.Key_2)
    rows = mod.load_answers(base / mod.CSV_NAME)
    assert rows[2]["answer"] == "모름" and rows[2]["camtek_missing"] == "1"
    assert dlg._progress.text() == "3 / 3"
    QTest.keyClick(dlg, Qt.Key.Key_Backspace)
    assert dlg._progress.text() == "2 / 3"
    dlg.match_btn.click()                                              # 다시 답하면 덮어쓰고 미응답으로 간다
    assert mod.load_answers(base / mod.CSV_NAME)[2]["answer"] == "매치"
    assert dlg._progress.text() == "3 / 3"
    assert dlg.zip_path is None


def test_dialog_finishing_writes_zip_and_resume(styled_qapp, tmp_path):
    mod, dlg, base = _dialog(styled_qapp, tmp_path)
    dlg.open()
    dlg.answer("매치")
    dlg.answer("모름")
    dlg.answer("매치")                                                 # 마지막 → 끝
    assert dlg.all_done and dlg.zip_path == base / "20261126" / "conder짝_확인_0905.zip"
    assert dlg.zip_path.is_file()
    # 일부만 답하고 닫으면 이어서 한다
    (base / mod.CSV_NAME).unlink()
    mod2, dlg2, _ = _dialog(styled_qapp, tmp_path)
    dlg2.open()
    dlg2.answer("매치")
    dlg2.reject()
    assert dlg2.zip_path is not None and not dlg2.all_done
    dlg3 = mod.PairLabelDialog(None, _cands(3), str(tmp_path / "root"), base, now=lambda: NOW)
    assert dlg3.index == 1 and dlg3._progress.text() == "2 / 3"


def test_closing_without_answers_writes_nothing(styled_qapp, tmp_path):
    mod, dlg, base = _dialog(styled_qapp, tmp_path)
    dlg.open()
    dlg.reject()
    assert dlg.zip_path is None and not base.exists()


def test_open_dialog_warns_without_tool_or_candidates(styled_qapp, monkeypatch):
    mod = _mod()
    said = []
    monkeypatch.setattr(mod, "_load_tool", lambda: None)
    monkeypatch.setattr(mod.sheets, "warn", lambda parent, title, text: said.append(text))
    mod.open_dialog(None)
    assert said and "conder_pair_candidates.json" in said[0]


# ── 표시 · 문구 ───────────────────────────────────────────────────────
def test_label_strings_all_exist_and_candidates_file_is_valid():
    ko = (APP / "i18n" / "ko.py").read_text(encoding="utf-8")
    used = set(re.findall(r"KO\.(CPAIR_LABEL_\w+)",
                          (APP / "ui" / "conder_pair_label_tmp.py").read_text(encoding="utf-8")))
    defined = set(re.findall(r"^(CPAIR_LABEL_\w+)\s*=", ko, flags=re.M))
    assert used and used == defined
    import json
    data = json.loads((ROOT / "dev" / "conder_pair_candidates.json").read_text(encoding="utf-8"))
    assert len(data) == len({d["id"] for d in data}) and all(d["kla"] and d["camtek"] for d in data)
