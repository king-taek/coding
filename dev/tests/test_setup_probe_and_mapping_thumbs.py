"""폴더 확인을 워커로 (P-15) + 슬롯 매핑 미리보기의 반복 디코드 제거 (P-14).

P-15 배경: `Path.is_dir()` 는 값싸 보이지만 네트워크 폴더(NAS)에서는 응답이 초
단위로 늦고, 연결이 끊긴 경로에서는 OS 타임아웃까지 통째로 멈춘다.  이 확인은
경로를 **한 글자 칠 때마다**(250ms 디바운스) 돌기 때문에 UI 스레드에서 하면
타이핑 내내 창이 끊긴다.

P-14 배경: 60px 아이콘 하나를 만들려고 AOI 원본(수 MB)을 전량 디코드했고, 같은
사진을 목록과 묶은 쌍에서 다시 그리느라 그 디코드를 쌍당 네 번까지 반복했다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app import i18n                                # noqa: E402
import aoi_verification.app.ui.pages.setup_page as SP                # noqa: E402
import aoi_verification.app.ui.widgets.slot_mapping_dialog as SM     # noqa: E402


# ---------------------------------------------------------------------------
# P-15 — 폴더 확인 워커
# ---------------------------------------------------------------------------
def test_headless_stays_synchronous(styled_qapp, tmp_path):
    """헤드리스는 동기 — `_validate()` 의 반환값을 그 자리에서 믿을 수 있어야 한다."""
    page = SP.SetupPage()
    assert page._sync_probe, "offscreen 인데 비동기로 확인한다"
    page.ref_path_edit.setText(str(tmp_path))
    page.val_path_edit.setText(str(tmp_path))
    assert page._validate() is True
    page.deleteLater()


def test_unknown_path_reads_as_checking_not_missing(styled_qapp, tmp_path):
    """아직 모르는 것을 '없음' 으로 칠하면 안 된다 — 멀쩡한 폴더가 빨갛게 뜬다."""
    page = SP.SetupPage()
    page._sync_probe = False              # 워커 경로 강제
    page.ref_path_edit.setText(str(tmp_path))
    page.val_path_edit.setText(str(tmp_path))

    assert page._validate() is False, "확인이 끝나기 전에 시작이 열렸다"
    assert page._start_hint.text() == i18n.KO.START_CHECKING_HINT
    assert page.ref_path_edit.property("state") != "invalid", \
        "확인 중인 경로를 '잘못됨' 으로 칠했다"
    page.deleteLater()


def test_probe_result_unlocks_start(styled_qapp, tmp_path):
    """워커 결과가 들어오면 판정이 다시 그려지고 시작이 열린다."""
    page = SP.SetupPage()
    page._sync_probe = False
    page.ref_path_edit.setText(str(tmp_path))
    page.val_path_edit.setText(str(tmp_path))
    page._validate()

    page._on_dir_probe_done(page._probe_token, str(tmp_path), "",
                            str(tmp_path), "")
    assert page.start_btn.isEnabled(), "확인이 끝났는데 시작이 잠겨 있다"
    assert page._start_hint.text() == ""
    page.deleteLater()


def test_stale_probe_is_ignored(styled_qapp, tmp_path):
    """타이핑이 이어지면 확인이 겹친다 — 옛 결과가 새 경로의 판정을 덮으면 안 된다."""
    page = SP.SetupPage()
    page._sync_probe = False
    page.ref_path_edit.setText(str(tmp_path))
    page.val_path_edit.setText(str(tmp_path))
    page._validate()
    stale = page._probe_token - 1

    page._on_dir_probe_done(stale, str(tmp_path), "", str(tmp_path), "")
    assert not page.start_btn.isEnabled(), "옛 세대 결과가 반영됐다"
    page.deleteLater()


def test_missing_folder_still_marks_the_field(styled_qapp, tmp_path):
    """확인이 끝나 '없음' 이면 예전처럼 빨간 테두리 + 문구가 나와야 한다."""
    page = SP.SetupPage()
    page._sync_probe = False
    bad = str(tmp_path / "없는폴더")
    page.ref_path_edit.setText(bad)
    page.val_path_edit.setText(str(tmp_path))
    page._validate()

    page._on_dir_probe_done(page._probe_token, bad, "missing",
                            str(tmp_path), "")
    assert page.ref_path_edit.property("state") == "invalid"
    assert page._start_hint.text() == i18n.KO.START_BLOCKED_HINT
    assert not page.start_btn.isEnabled()
    page.deleteLater()


def test_probe_rechecks_even_when_cached(styled_qapp, tmp_path):
    """캐시는 '멈추지 않기' 위한 것이지 '다시 안 보기' 위한 것이 아니다.

    사용자가 안내를 보고 폴더를 **그 사이에 만들었다면** 다음 확인에서 풀려야 한다.
    """
    page = SP.SetupPage()
    page._sync_probe = False
    page._probed[str(tmp_path)] = "missing"
    page.ref_path_edit.setText(str(tmp_path))
    page.val_path_edit.setText(str(tmp_path))

    before = page._probe_token
    page._validate()
    assert page._probe_token > before, "캐시가 있다고 재확인을 건너뛰었다"
    page.deleteLater()


def test_no_second_probe_while_one_is_in_flight(styled_qapp, tmp_path):
    """같은 쌍을 확인하는 중이면 워커를 새로 띄우지 않는다.

    `_validate` 는 두 입력란·허용 오차·디바운스가 모두 부르는 자리다.  끊긴 NAS 에서
    `is_dir()` 은 OS 타임아웃까지(수십 초) 안 돌아오는데, 그동안 부를 때마다 스레드를
    새로 띄우면 답도 없는 확인만 쌓인다.  끝나면 풀린다(아래 마지막 단언)."""
    page = SP.SetupPage()
    page._sync_probe = False
    page.ref_path_edit.setText(str(tmp_path))
    page.val_path_edit.setText(str(tmp_path))

    page._validate()
    token = page._probe_token
    page._validate()                      # 확인이 도는 중에 다시 왔다
    assert page._probe_token == token, "같은 쌍을 확인 중인데 워커를 또 띄웠다"

    # 확인이 끝나면 다시 열린다 — '매번 다시 확인' 관습은 그대로다.
    page._on_dir_probe_done(token, str(tmp_path), "", str(tmp_path), "")
    page._validate()
    assert page._probe_token > token, "확인이 끝났는데 재확인이 막혀 있다"
    page.deleteLater()


def test_new_path_probes_even_while_one_is_in_flight(styled_qapp, tmp_path):
    """경로가 바뀌면 확인 중이어도 새로 띄운다 — 옛 쌍을 기다리면 안 된다."""
    page = SP.SetupPage()
    page._sync_probe = False
    page.ref_path_edit.setText(str(tmp_path))
    page.val_path_edit.setText(str(tmp_path))
    page._validate()
    token = page._probe_token

    page.val_path_edit.setText(str(tmp_path / "다른폴더"))
    page._validate()
    assert page._probe_token > token, "경로가 바뀌었는데 옛 확인을 기다렸다"
    page.deleteLater()


# ---------------------------------------------------------------------------
# P-14 — 슬롯 매핑 미리보기
# ---------------------------------------------------------------------------
def test_preview_uses_the_prebuilt_downscale_not_the_original(styled_qapp,
                                                              tmp_path,
                                                              monkeypatch):
    """원본 경로로 QPixmap 을 만들면 수 MB 를 통째로 디코드한다."""
    photo = tmp_path / "shot.jpg"
    photo.write_bytes(b"")
    thumb = tmp_path / "shot_thumb.jpg"

    seen: list = []
    monkeypatch.setattr(SM_image_io(), "get_thumb_path",
                        lambda p, **k: seen.append(Path(p)) or thumb)

    dlg = SM.SlotMappingDialog(
        ["A1"], ["B1"],
        ref_meta={"A1": {"image": str(photo), "method": "name"}},
        val_meta={"B1": {"image": str(photo), "method": "name"}},
    )
    seen.clear()                          # 생성 중의 목록 채우기는 빼고 센다
    dlg._thumb_pix("A1", dlg._ref_meta)
    assert seen == [photo], "축소본을 거치지 않고 원본을 바로 읽었다"
    dlg.deleteLater()


def test_preview_is_decoded_once_per_slot(styled_qapp, tmp_path, monkeypatch):
    """목록·묶은 쌍에서 같은 사진을 다시 그려도 디코드는 한 번뿐이어야 한다."""
    photo = tmp_path / "shot.jpg"
    photo.write_bytes(b"")
    calls: list = []

    def _fake_thumb_path(p, **k):
        calls.append(Path(p))
        return photo                      # 존재하는 경로(디코드는 실패해도 무방)

    monkeypatch.setattr(SM_image_io(), "get_thumb_path", _fake_thumb_path)
    # 스케일까지 도달하도록 유효한 픽스맵을 흉내 낸다.
    from PyQt6.QtGui import QPixmap
    real_pix = QPixmap(8, 8)
    real_pix.fill()
    monkeypatch.setattr(SM, "QPixmap", lambda *a, **k: real_pix)

    dlg = SM.SlotMappingDialog(
        ["A1"], ["B1"],
        ref_meta={"A1": {"image": str(photo), "method": "name"}},
        val_meta={"B1": {"image": str(photo), "method": "name"}},
    )
    # 생성 중 목록을 채우며 슬롯당 한 번씩(A1·B1) 읽는다 — 그 뒤로는 늘어나면 안 된다.
    assert len(calls) == 2, f"목록 채우기에만 {len(calls)} 번 디코드했다"
    for _ in range(4):
        dlg._thumb_pix("A1", dlg._ref_meta)
    assert len(calls) == 2, \
        f"같은 사진을 다시 그릴 때마다 디코드했다 (총 {len(calls)} 회)"
    dlg.deleteLater()


def test_kla_preview_crops_the_mid_size_not_the_original(styled_qapp, tmp_path,
                                                         monkeypatch):
    """KLA 헤더는 썸네일로는 글자가 뭉개진다 — 중간 크기에서 크롭한다."""
    photo = tmp_path / "kla.jpg"
    photo.write_bytes(b"")
    mid = tmp_path / "kla_mid.jpg"
    seen: list = []
    monkeypatch.setattr(SM_image_io(), "get_mid_path",
                        lambda p, **k: seen.append(Path(p)) or mid)

    import aoi_verification.app.utils.wafer_id as W
    cropped: list = []
    monkeypatch.setattr(W, "header_crop_image",
                        lambda p, **k: cropped.append(Path(p)) or None)

    dlg = SM.SlotMappingDialog(
        ["A1"], [],
        ref_meta={"A1": {"image": str(photo), "method": "info"}},
    )
    seen.clear()
    cropped.clear()
    dlg._thumb_pix("A1", dlg._ref_meta)
    assert seen == [photo], "KLA 미리보기가 중간 크기를 거치지 않았다"
    assert cropped == [mid], "크롭이 원본을 대상으로 돌았다"
    dlg.deleteLater()


def SM_image_io():
    from aoi_verification.app.utils import image_io
    return image_io
