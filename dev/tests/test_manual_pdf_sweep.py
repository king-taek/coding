"""make_manual_pdf.sweep_captures — 인쇄 뒤 캡처를 치우고 이름만 남긴다.

PDF 는 자기완결적이라 인쇄가 끝난 캡처는 재생성 가능한 중간 산출물이다.  여기서
못 박는 것은 **무엇을 지우고 무엇을 남기는가**다 — shots.json·HTML·CSS 를 함께
지워 버리면 설명서를 다시 만들 수 없다.

Chromium 도 Qt 도 필요 없는 순수 파일 조작이라 헤드리스로 검증한다.
"""

from __future__ import annotations

import importlib.util as _u
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = _u.spec_from_file_location(name, str(_ROOT / rel))
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mpdf = _load("make_manual_pdf", "scripts/internal/make_manual_pdf.py")

_LISTING = "사진목록.txt"


def _assets(tmp_path: Path) -> Path:
    """캡처 3장 + 원본(shots.json·HTML·CSS)이 있는 자료 폴더를 흉내 낸다."""
    d = tmp_path / "사용설명서_자료"
    d.mkdir()
    (d / "01_셋업_빈화면.png").write_bytes(b"png")
    (d / "11_1단계_후보선별.jpg").write_bytes(b"jpg")
    (d / "18_결과.png").write_bytes(b"png")
    (d / "shots.json").write_text("{}", encoding="utf-8")
    (d / "사용설명서.html").write_text("<html>", encoding="utf-8")
    (d / "설명서_공통.css").write_text("body{}", encoding="utf-8")
    return d


def test_sweep_deletes_captures_and_records_names(tmp_path):
    d = _assets(tmp_path)

    got = mpdf.sweep_captures(d)

    assert got == ["01_셋업_빈화면.png", "11_1단계_후보선별.jpg", "18_결과.png"]
    assert not [p for p in d.iterdir() if p.suffix in (".png", ".jpg")]
    listed = [ln for ln in (d / _LISTING).read_text(encoding="utf-8").splitlines()
              if ln and not ln.startswith("#")]
    assert listed == got, "지운 이름이 그대로 목록에 남아야 한다"


def test_sweep_keeps_the_sources(tmp_path):
    """★ shots.json·HTML·CSS 는 캡처가 아니라 **원본**이다 — 지우면 복구가 안 된다."""
    d = _assets(tmp_path)

    mpdf.sweep_captures(d)

    for keep in ("shots.json", "사용설명서.html", "설명서_공통.css"):
        assert (d / keep).exists(), f"{keep} 은 원본이라 남아야 한다"


def test_sweep_is_idempotent(tmp_path):
    """두 번 돌아도 목록을 빈 목록으로 덮어쓰지 않는다(이름을 잃으면 끝이다)."""
    d = _assets(tmp_path)
    first = mpdf.sweep_captures(d)

    assert mpdf.sweep_captures(d) == []

    listed = [ln for ln in (d / _LISTING).read_text(encoding="utf-8").splitlines()
              if ln and not ln.startswith("#")]
    assert listed == first


def test_main_sweeps_only_after_both_pdfs_pass(tmp_path, monkeypatch):
    """★ 인쇄가 실패하면 캡처를 지우지 않는다 — 지우면 다시 만들 수 없다."""
    d = _assets(tmp_path)
    monkeypatch.setattr(mpdf, "ASSETS", d)
    monkeypatch.setattr(mpdf, "DOCS", [(d / "사용설명서.html", tmp_path / "없음.pdf")])
    monkeypatch.setattr(mpdf, "_check_images", lambda: None)
    monkeypatch.setattr(mpdf, "_check_stylesheet", lambda: None)
    monkeypatch.setattr(mpdf, "_check_shared", lambda: None)
    monkeypatch.setattr(mpdf, "_chromium", lambda: "/bin/true")

    class _Proc:
        stderr = ""

    monkeypatch.setattr(mpdf.subprocess, "run", lambda *a, **k: _Proc())

    try:
        mpdf.main()
    except SystemExit:
        pass                      # PDF 가 안 만들어져 실패하는 게 정상이다

    assert (d / "01_셋업_빈화면.png").exists(), "인쇄 실패 시 캡처가 남아야 한다"
    assert not (d / _LISTING).exists()
