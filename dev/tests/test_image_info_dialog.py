"""단일 사진 정보 다이얼로그 + 셋업 액션바 버튼 순서 — 헤드리스 검증.

지키는 계약:

- 사진을 넣기 전에는 안내 문구를, 계측을 하나도 못 읽으면 '읽을 수 있는 정보 없음' 을
  **실제로** 띄운다(그 안내가 도달 불가였던 회귀가 있다).
- 수치는 모노, 측정정보 유무는 판정 스탬프 — 앱의 '도면' 언어를 실제로 쓴다.
- 공백 없는 긴 경로가 가로 스크롤을 만들지 않는다.  좁은 창의 **안내 문구**도 마찬가지다.
- 값은 **라벨 열 바로 오른쪽 한 기준선**에서 시작한다(오른쪽 끝에 붙이지 않는다).
- Enter 가 마지막으로 눌린 버튼을 재발동하지 않는다(autoDefault 함정).
- 드롭은 **사진 파일만** 받는다 — 아무 파일이나 받으면 조회가 조용히 실패한다.
- 액션바 인덱스 계약은 ``test_setup_controls`` 가 지킨다.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")
pytest.importorskip("numpy")

from PyQt6.QtCore import QMimeData, Qt, QUrl                # noqa: E402
from PyQt6.QtWidgets import QApplication, QFrame, QLabel    # noqa: E402

from aoi_verification.app import i18n                       # noqa: E402
from aoi_verification.app.ui.widgets.image_info_dialog import (  # noqa: E402
    _LABEL_COL_MIN, ImageInfoDialog)

_KLA_INFO = """FileVersion 1 2;
DiePitch 3.7247930000e+004 4.4905340000e+004;
WaferID "W6459076XYG1";
TiffFileName W6459076XYG1_2_0_23_2.jpg
 1 100.0 200.0 1234.5 2345.6 -2 -1 0
"""


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _clear_caches():
    from aoi_verification.app.coords import camtek_ini, kla_info
    for fn in (kla_info.load_folder, kla_info.read_wafer_id,
               camtek_ini.load_abs_folder):
        fn.cache_clear()
    yield


def _texts(dlg) -> list[str]:
    """계측 표 안의 모든 라벨 문자열.  값 라벨은 폭에 따라 생략되므로 원문을 본다."""
    host = dlg._scroll.widget()
    out = []
    for w in host.findChildren(QLabel):
        out.append(w.text_full() if hasattr(w, "text_full") else w.text())
    return out


def _values(widget) -> list:
    """``_ElidingValue`` 만 — 라벨·컬럼 머리와 구분한다."""
    return [w for w in widget.findChildren(QLabel) if hasattr(w, "text_full")]


def _kla_image(tmp_path):
    (tmp_path / "info.001").write_text(_KLA_INFO, encoding="utf-8")
    img = tmp_path / "W6459076XYG1_2_0_23_2.jpg"
    img.write_bytes(b"x")
    return img


def test_shows_hint_before_any_file(qapp, isolated_cache):
    dlg = ImageInfoDialog()
    assert i18n.KO.IMAGE_INFO_NO_FILE in _texts(dlg)
    assert dlg.as_text() == ""
    assert not dlg.copy_btn.isEnabled(), "복사할 게 없으면 버튼도 눌리지 않아야"
    dlg.deleteLater()


def test_shows_empty_notice_when_nothing_readable(qapp, isolated_cache, tmp_path):
    """계측을 하나도 못 읽으면 **복구 안내가 실제로 뜬다**.

    ★ 회귀 계약: 이전 구현은 '행이 하나도 없을 때'만 안내를 띄웠는데, 파일명·폴더
      행은 경로만 있으면 항상 생겨서 이 안내가 **영원히 도달 불가**였다.  정보파일이
      없는 폴더 — 안내가 가장 필요한 바로 그 순간 — 에 화면이 침묵했다.
    """
    img = tmp_path / "plain.jpeg"
    img.write_bytes(b"x")
    dlg = ImageInfoDialog(image_path=str(img))
    texts = _texts(dlg)
    assert i18n.KO.IMAGE_INFO_EMPTY in texts
    assert "plain.jpeg" in texts
    # 계측이 없으므로 '결함 계측' 덩이 자체가 없다.
    assert i18n.KO.IMAGE_INFO_GROUP_DEFECT not in texts
    dlg.deleteLater()


def test_numeric_rows_are_mono_and_status_is_a_stamp(qapp, isolated_cache,
                                                     tmp_path):
    """수치는 모노, 측정정보 유무는 판정 스탬프 — 도면 언어를 실제로 쓴다."""
    (tmp_path / "info.001").write_text(_KLA_INFO, encoding="utf-8")
    img = tmp_path / "W6459076XYG1_2_0_23_2.jpg"
    img.write_bytes(b"x")

    dlg = ImageInfoDialog(image_path=str(img))
    host = dlg._scroll.widget()
    roles = [w.property("role") for w in host.findChildren(QLabel)]
    chips = [w.property("chip") for w in host.findChildren(QLabel)
             if w.property("chip")]
    assert "mono" in roles, "좌표·계측 값이 모노로 찍혀야 한다"
    assert "infoGroupTitle" in roles, "그룹마다 제목이 있어야 한다"
    boxes = [f for f in host.findChildren(QFrame) if f.property("role") == "infoBox"]
    assert len(boxes) == roles.count("infoGroupTitle"), "그룹마다 음영 상자 하나"
    assert chips == ["none"], "측정정보 미지원은 스탬프 하나로"
    dlg.deleteLater()


def test_enter_does_not_refire_the_last_button(qapp, isolated_cache):
    """Enter 를 autoDefault 에 맡기지 않는다 — slot_select_dialog 와 같은 함정.

    맡기면 포커스가 '전체 복사'/'닫기' 에 있을 때 Enter 가 그 동작을 재발동한다."""
    dlg = ImageInfoDialog()
    assert not hasattr(dlg, "pick_btn"), "위 왼쪽 [사진 선택] 은 뺐다(2026-11-25)"
    assert dlg.copy_btn.autoDefault() is False
    dlg.deleteLater()


def test_long_path_never_scrolls_horizontally(qapp, isolated_cache, tmp_path):
    """공백 없는 긴 경로가 가로 스크롤을 만들면 안 된다(CLAUDE.md UI 관습).

    ``setWordWrap`` 은 끊을 곳이 없어 듣지 않는다 — 값 라벨이 가운데 생략해야 한다.

    ★ 폴더 이름은 **짧게** 둔다.  예전엔 60자 폴더를 두 겹 파서 전체 경로가
    Windows 의 260자(MAX_PATH) 한계를 넘었고, `mkdir` 자체가 실패해 검사가 시작도
    못 했다 — 정작 이 앱이 배포되는 OS 에서만 꺼져 있었다.  이 검사가 보는 것은
    **끊을 곳 없는 긴 값 문자열**이고 그건 파일 이름 하나로 충분하다."""
    deep = tmp_path / ("n_" + "a" * 12) / ("s_" + "b" * 12)
    deep.mkdir(parents=True)
    img = deep / ("f_" + "c" * 90 + ".jpeg")
    img.write_bytes(b"x")

    dlg = ImageInfoDialog(image_path=str(img))
    dlg.resize(900, 560)
    dlg.show()
    qapp.processEvents()
    assert dlg._scroll.horizontalScrollBar().maximum() == 0
    assert (dlg._scroll.widget().sizeHint().width()
            <= dlg._scroll.viewport().width())
    # 생략해도 전체 문자열은 잃지 않는다(툴팁·복사용).
    assert img.name in _texts(dlg)
    dlg.deleteLater()


def test_renders_kla_defect_rows(qapp, isolated_cache, tmp_path):
    (tmp_path / "info.001").write_text(_KLA_INFO, encoding="utf-8")
    img = tmp_path / "W6459076XYG1_2_0_23_2.jpg"
    img.write_bytes(b"x")

    # 빈 상태에서 사진을 넣는 전환 — 이전 안내 문구가 남아 겹쳐 보이던 회귀.
    # 위젯을 걷어내는 대신 패널을 통째로 갈아끼우므로 호스트 자체가 바뀌어야 한다.
    dlg = ImageInfoDialog()
    old_host = dlg._scroll.widget()
    dlg.show_image(img)
    assert dlg._scroll.widget() is not old_host
    texts = _texts(dlg)
    assert i18n.KO.IMAGE_INFO_NO_FILE not in texts
    assert i18n.KO.IMAGE_INFO_ROW_WAFER_ID in texts
    assert "W6459076XYG1" in texts
    # 표는 라벨/값 2열이다 — 엑셀 한 줄("col 1 / row 3")과 모양이 다르고 수치는 같다.
    assert i18n.KO.DEFECT_COLROW_LABEL in texts
    assert "1 / 3" in texts
    # 복사 텍스트는 라벨\t값 — 엑셀에 붙여넣으면 값이 한 열로 선다.
    assert f"{i18n.KO.DEFECT_COLROW_LABEL}\t1 / 3" in dlg.as_text()
    dlg.deleteLater()


def test_drop_opens_photos_and_folders_only(qapp, isolated_cache, tmp_path, monkeypatch):
    """드롭은 사진 · 폴더만 받는다 — 여러 개면 전부 넘겨 보기 목록으로."""
    img = tmp_path / "a.jpeg"
    img.write_bytes(b"x")
    other = tmp_path / "a.txt"
    other.write_text("x", encoding="utf-8")

    class _Evt:
        def __init__(self, md):
            self._md = md
            self.accepted = None

        def mimeData(self):
            return self._md

        def acceptProposedAction(self):
            self.accepted = True

        def ignore(self):
            self.accepted = False

    dlg = ImageInfoDialog()
    opened = []
    monkeypatch.setattr(dlg, "open_many", opened.append)
    try:
        md_img = QMimeData()
        md_img.setUrls([QUrl.fromLocalFile(str(other)), QUrl.fromLocalFile(str(img)),
                        QUrl.fromLocalFile(str(tmp_path))])
        evt = _Evt(md_img)
        dlg.dropEvent(evt)
        assert evt.accepted is True
        assert [Path(p) for p in opened[0]] == [other, img, tmp_path]

        md_none = QMimeData()
        md_none.setUrls([QUrl.fromLocalFile(str(other))])
        evt = _Evt(md_none)
        dlg.dropEvent(evt)
        assert evt.accepted is False and len(opened) == 1
    finally:
        dlg.deleteLater()


def test_setup_page_opens_this_dialog(qapp, isolated_cache):
    """왼쪽 메뉴의 [사진 정보 보기] 가 이 다이얼로그를 **화면으로** 연다(팝업 아님).

    메뉴 순서 계약은 ``test_setup_controls.test_action_bar_index_contract`` 가 지킨다.
    """
    from aoi_verification.app.ui.pages import setup_page as sp
    from aoi_verification.app.ui.widgets.image_info_dialog import ImageInfoDialog

    page = sp.SetupPage()
    try:
        page.image_info_btn.click()
        assert page.current_page() == "image_info"
        assert isinstance(page.tool_dialog("image_info"), ImageInfoDialog)
    finally:
        page.deleteLater()


def test_values_start_at_one_line_left_of_the_gap(styled_qapp, isolated_cache,
                                                 tmp_path):
    """값은 라벨 열 바로 오른쪽 **한 기준선**에서 시작한다.

    ★ 회귀 계약: 값을 ``AlignRight`` 로 오른쪽 끝에 붙이면 창이 넓어질수록 라벨과
      값 사이가 벌어져 항목마다 눈이 좌우로 왕복한다(항목 12개 = 왕복 12번).
    """
    dlg = ImageInfoDialog(image_path=str(_kla_image(tmp_path)))
    dlg.resize(700, 620)
    dlg.show()
    styled_qapp.processEvents()

    vals = _values(dlg._scroll.widget())
    assert vals, "값 라벨이 하나도 없다"
    assert all(v.alignment() & Qt.AlignmentFlag.AlignLeft for v in vals)
    starts = {v.x() for v in vals}
    assert len(starts) == 1, f"값의 시작선이 갈렸다: {sorted(starts)}"
    assert starts.pop() >= _LABEL_COL_MIN, "라벨 열이 하한보다 좁다"
    dlg.deleteLater()


def test_long_labels_do_not_break_the_start_line(styled_qapp, isolated_cache,
                                                 tmp_path):
    """하한(``_LABEL_COL_MIN``)을 넘는 라벨이 섞여도 값의 시작선은 하나다.

    ★ 회귀 계약: 한 행이 곧 눈금 ``QFrame`` 하나라 행마다 grid 가 따로다.  라벨 열
      폭을 상수로 못 박으면 하한을 넘는 라벨이 있는 행만 값이 오른쪽으로 밀린다.
      라벨 폭은 서체에 따라 달라지므로(동봉 글꼴 실패 → 폴백은 두 배 가까이 넓다)
      상수로는 이 약속을 지킬 수 없다.
    """
    from aoi_verification.app.coords.single_info import InfoGroup, InfoRow

    dlg = ImageInfoDialog(image_path=str(_kla_image(tmp_path)))
    dlg._groups = [InfoGroup("계측", (
        InfoRow("col / row", "12 / 7", mono=True),
        InfoRow("아주 아주 아주 긴 라벨 이름", "55.00 ㎛²", mono=True),
        InfoRow("area", "8.20 ㎛", mono=True),
    ))]
    dlg._render()
    dlg.resize(700, 620)
    dlg.show()
    styled_qapp.processEvents()

    host = dlg._scroll.widget()
    starts = {v.mapTo(host, v.rect().topLeft()).x() for v in _values(host)}
    assert len(starts) == 1, f"값의 시작선이 갈렸다: {sorted(starts)}"
    dlg.deleteLater()


def test_notice_never_scrolls_horizontally(styled_qapp, isolated_cache,
                                           tmp_path):
    """좁은 창에서 안내 문구가 가로 스크롤을 만들면 안 된다.

    ★ 회귀 계약: ``setWordWrap`` 라벨은 최소 폭을 0 까지 내리지 않고 '적당한
      가로세로 비'를 유지하려 든다(실측 220px).  그 최소치가 뷰포트보다 넓으면
      스크롤 영역이 가로로 넘친다 — 값 라벨(`_ElidingValue`)만 폭 정책을 낮추고
      안내 문구를 빼먹으면, 정작 **표가 하나도 없어 여백이 가장 많은 화면**에서
      가로 스크롤이 난다.

    560px 은 고친 뒤 두 안내 상태가 모두 깨끗한 폭이자, 고치기 전에는 둘 다
    넘치던 폭이다(실측 발생 상한 580px).
    """
    img = tmp_path / "plain.jpeg"          # 정보파일이 없어 계측을 못 읽는다
    img.write_bytes(b"x")
    for dlg in (ImageInfoDialog(), ImageInfoDialog(image_path=str(img))):
        dlg.resize(560, 560)
        dlg.show()
        styled_qapp.processEvents()
        notice = [w for w in dlg._scroll.widget().findChildren(QLabel)
                  if w.wordWrap() and w.property("role") == "muted"]
        assert notice, "안내 문구가 떠 있어야 하는 상태다"
        assert dlg._scroll.horizontalScrollBar().maximum() == 0
        # 폭을 낮춘 대신 아래로 늘어난다 — 문구가 잘리지 않는다.
        label = notice[0]
        assert label.height() >= label.heightForWidth(label.width())
        dlg.deleteLater()


def test_paste_opens_image_and_flashes_notices(qapp, isolated_cache, tmp_path, monkeypatch):
    """Ctrl+V — 사진 경로는 열고, 사진 아닌 것·없는 경로·그림 픽셀은 짧은 안내."""
    img = tmp_path / "a.jpeg"
    img.write_bytes(b"x")
    dlg = ImageInfoDialog()
    shown, flashed = [], []
    monkeypatch.setattr(dlg, "_load_photo", shown.append)
    monkeypatch.setattr(dlg, "_flash", flashed.append)
    try:
        dlg._on_paste_paths([str(img)])
        assert shown == [img] and not flashed
        dlg._on_paste_paths([str(tmp_path / "a.txt"), str(img)])   # 사진만 추린다
        assert shown == [img, img]
        shown.clear()
        dlg._on_paste_paths([str(tmp_path / "a.txt")])
        dlg._on_paste_paths([str(tmp_path / "nope.jpeg")])
        dlg._on_paste_no_path(True)
        dlg._on_paste_no_path(False)
        assert shown == []
        assert flashed == [i18n.KO.DROP_WRONG_KIND_IMAGE, i18n.KO.IMAGE_INFO_NO_PHOTOS,
                           i18n.KO.DROP_NO_PATH_IMAGE, i18n.KO.DROP_NOTHING_TO_PASTE]
    finally:
        dlg.deleteLater()


def test_list_photos_reads_folders_and_files(tmp_path):
    """폴더는 바로 안 사진만 이름순, 사진 아닌 것 · 없는 것은 빼고, 같은 파일은 한 번만."""
    from aoi_verification.app.ui.widgets.image_info_dialog import list_photos
    d = tmp_path / "slot"
    (d / "sub").mkdir(parents=True)
    for name in ("b.jpg", "A.jpeg", "note.txt"):
        (d / name).write_bytes(b"x")
    (d / "sub" / "c.jpg").write_bytes(b"x")
    loose = tmp_path / "z.jpg"
    loose.write_bytes(b"x")
    got = list_photos([str(d), str(loose), str(d / "b.jpg"), str(tmp_path / "nope.jpg"),
                       str(d / "note.txt")])
    assert [p.name for p in got] == ["A.jpeg", "b.jpg", "z.jpg"]


def test_many_photos_step_through_with_a_strip(qapp, isolated_cache, tmp_path, monkeypatch):
    """여러 장 — 띠가 보이고 ◀ ▶ · 칸 클릭으로 넘긴다(끝에서 멈춘다).  한 장을 열면 띠가 사라진다."""
    for i in range(20):
        (tmp_path / f"p{i:02d}.jpg").write_bytes(b"x")
    dlg = ImageInfoDialog()
    shown = []
    monkeypatch.setattr(dlg, "_load_photo", lambda p: (shown.append(p.name),
                                                       setattr(dlg, "_path", p)))
    try:
        dlg.open_many([str(tmp_path)])
        assert shown == ["p00.jpg"]
        assert not dlg.strip_bar.isHidden()
        assert dlg.photo_pos.text() == i18n.KO.IMAGE_INFO_PHOTO_POS_FMT.format(i=1, n=20)
        assert not hasattr(dlg, "photo_prev") and not hasattr(dlg, "photo_next")   # 방향키만
        dlg.step_photo(-1)                      # 맨 앞 — 멈춘다
        assert shown == ["p00.jpg"]
        dlg.step_photo(1)
        dlg.go_photo(19)
        dlg.step_photo(1)                       # 맨 뒤 — 멈춘다
        assert shown == ["p00.jpg", "p01.jpg", "p19.jpg"]
        # 띠는 지금 사진 앞뒤만 — 위젯 수가 사진 수와 무관하다.
        assert len(dlg._strip_map) <= 13
        assert str(tmp_path / "p19.jpg") in dlg._strip_map
        tile = dlg._strip_map[str(tmp_path / "p15.jpg")]
        tile.clicked.emit()
        assert shown[-1] == "p15.jpg"
        dlg.show_image(tmp_path / "p03.jpg")
        assert dlg.strip_bar.isHidden() and dlg._paths == [tmp_path / "p03.jpg"]
    finally:
        dlg.deleteLater()


def _lot(tmp_path):
    """LOT 폴더 — 사진은 웨이퍼 하위 폴더에만 있다(실제 NAS 모양)."""
    lot = tmp_path / "LOT1"
    for name, slot, n in (("W_b", 5, 2), ("w_a", 3, 3)):
        w = lot / name
        w.mkdir(parents=True)
        for i in range(n):
            (w / f"{name}_{i}.jpg").write_bytes(b"x")
        (w / "WaferInfo.ini").write_text(f"[AutoCycleInfo]\nActiveSlot={slot}\n")
    (lot / "deep" / "deeper").mkdir(parents=True)
    (lot / "deep" / "deeper" / "z.jpg").write_bytes(b"x")        # 두 단계 아래는 보지 않는다
    return lot


def test_list_photo_groups_lot_folder_looks_one_level_down(tmp_path):
    from aoi_verification.app.ui.widgets.image_info_dialog import (
        list_photo_groups, list_photos)
    lot = _lot(tmp_path)
    groups = list_photo_groups([str(lot)])
    assert [(f.name, [p.name for p in ps]) for f, ps in groups] == [
        ("w_a", ["w_a_0.jpg", "w_a_1.jpg", "w_a_2.jpg"]),        # 이름순(대소문자 무시)
        ("W_b", ["W_b_0.jpg", "W_b_1.jpg"])]
    assert len(list_photos([str(lot)])) == 5
    # 사진이 바로 든 폴더는 하위 폴더를 보지 않는다 · 사진 파일은 부모 폴더별
    w = lot / "w_a"
    (w / "sub").mkdir()
    (w / "sub" / "q.jpg").write_bytes(b"x")
    assert [f.name for f, _ in list_photo_groups([str(w)])] == ["w_a"]
    got = list_photo_groups([str(lot / "W_b" / "W_b_1.jpg"), str(lot / "w_a" / "w_a_0.jpg")])
    assert [(f.name, [p.name for p in ps]) for f, ps in got] == [
        ("W_b", ["W_b_1.jpg"]), ("w_a", ["w_a_0.jpg"])]


def test_lot_folder_opens_and_group_chooser_picks_a_folder(qapp, isolated_cache, tmp_path,
                                                           monkeypatch):
    """LOT 폴더를 열면 두 웨이퍼 사진이 다 잡히고, 위 버튼으로 한 폴더만 골라 볼 수 있다."""
    from aoi_verification.app.ui.widgets.image_info_dialog import _PhotoGroup
    lot = _lot(tmp_path)
    dlg = ImageInfoDialog()
    shown = []
    monkeypatch.setattr(dlg, "_load_photo", lambda p: (shown.append(p.name),
                                                       setattr(dlg, "_path", p)))
    try:
        assert dlg.group_btn.isHidden()
        dlg.open_many([str(lot)])
        assert len(dlg._paths) == 5 and shown == ["w_a_0.jpg"]
        assert not dlg.group_btn.isHidden()
        assert dlg.group_btn.text() == i18n.KO.IMAGE_INFO_GROUP_BTN_FMT.format(
            label=i18n.KO.IMAGE_INFO_GROUP_ALL_FMT.format(n=5))
        g = dlg._photo_groups
        assert [x.slot for x in g] == ["3", "5"] and all(x.lot == lot for x in g)
        assert i18n.KO.IMAGE_INFO_GROUP_SLOT_FMT.format(slot="5", name="W_b", n=2) in \
            [i18n.KO.IMAGE_INFO_GROUP_SLOT_FMT.format(slot=x.slot, name=x.folder.name,
                                                      n=len(x.photos)) for x in g]
        dlg.choose_group(1)
        assert [p.name for p in dlg._paths] == ["W_b_0.jpg", "W_b_1.jpg"]
        assert dlg._pos == 0 and shown[-1] == "W_b_0.jpg"
        assert len(dlg._strip_map) == 2
        assert dlg.group_btn.text() == i18n.KO.IMAGE_INFO_GROUP_BTN_FMT.format(
            label=i18n.KO.IMAGE_INFO_GROUP_SLOT_FMT.format(slot="5", name="W_b", n=2))
        dlg.choose_group(-1)
        assert len(dlg._paths) == 5
        # 메뉴 — 전체 · LOT 머리줄(누를 수 없음) · 폴더마다 한 줄, 현재 것에 체크
        dlg._open_group_menu()
        acts = dlg._group_menu.actions()
        assert [a.text() for a in acts][1] == "LOT1" and not acts[1].isEnabled()
        assert len(acts) == 4 and acts[0].isChecked()
        dlg._group_menu.close()
        dlg.show_image(lot / "w_a" / "w_a_0.jpg")          # 한 장 · 한 폴더면 버튼이 사라진다
        assert dlg.group_btn.isHidden()
        dlg.open_many([str(lot / "w_a")])                  # 폴더 하나 — 숨김
        assert dlg.group_btn.isHidden() and len(dlg._paths) == 3
        assert isinstance(dlg._photo_groups[0], _PhotoGroup)
    finally:
        dlg.deleteLater()


def test_no_photos_message_is_visible_on_the_empty_screen(qapp, isolated_cache, tmp_path):
    """비어 있는 동안은 빈 화면이 호스트를 덮는다 — 안내가 그 위(보이는 곳)에 떠야 한다."""
    empty = tmp_path / "empty"
    empty.mkdir()
    dlg = ImageInfoDialog()
    dlg.resize(900, 600)
    dlg.show()
    try:
        assert dlg._empty_open.isVisibleTo(dlg)
        dlg.open_many([str(empty)])
        assert dlg._empty_open.isVisibleTo(dlg)            # 여전히 빈 화면
        assert dlg._empty_open.note.text() == i18n.KO.IMAGE_INFO_NO_PHOTOS
        assert dlg._empty_open.note.isVisibleTo(dlg._empty_open)
        assert dlg._toast.text() == ""                     # 가려진 토스트로 보내지 않는다
        # 사진이 있으면 토스트 경로(복사 안내)는 그대로
        img = _kla_image(tmp_path)
        dlg.show_image(img)
        dlg._flash("x")
        assert dlg._toast.text() == "x"
    finally:
        dlg.close()
        dlg.deleteLater()


def test_stack_badge_count_and_tile(qapp):
    from aoi_verification.app.ui.widgets.image_info_dialog import (
        _STACK_OFF, _STRIP_PX, _StripTile, stack_badge_count)
    assert stack_badge_count(True, 3) == 3 and stack_badge_count(True, 2) == 2
    assert stack_badge_count(True, 1) == 0 and stack_badge_count(True, 0) == 0
    assert stack_badge_count(False, 5) == 0              # 못 읽은 Scan 은 평범한 칸
    t = _StripTile()
    assert t.stack_n == 0 and t.width() != _STRIP_PX + 2 * _STACK_OFF
    t.set_stack(3)
    assert t.stack_n == 3 and t.width() == _STRIP_PX + 2 * _STACK_OFF
    assert not t.grab().isNull()                         # 직접 그리기가 예외 없이 돈다
    t.set_stack(1)
    assert t.stack_n == 0 and t.width() == _STRIP_PX


def test_strip_thumb_ready_marks_multi_defect_scan(qapp, isolated_cache, tmp_path):
    for i in range(3):
        (tmp_path / f"-1.{i}.t.jpeg").write_bytes(b"x")
    dlg = ImageInfoDialog()
    dlg._paths, dlg._pos = sorted(tmp_path.glob("*.jpeg")), 0
    dlg._render_strip()
    try:
        key = str(dlg._paths[1])
        dlg._on_thumb_ready(key, 4)
        assert dlg._strip_map[key].stack_n == 4
        assert "4" in dlg._strip_map[key].toolTip()
        dlg._render_strip()                                # 띠를 다시 만들어도 기억한다
        assert dlg._strip_map[key].stack_n == 4
    finally:
        dlg.deleteLater()


def test_dividers_only_between_clusters(qapp, isolated_cache, tmp_path):
    """구분선은 묶음이 바뀌는 곳에만 — 묶음 안 행은 ``rule="none"``(사용자 지시 2026-11-23).

    묶음이 없는 행(``cluster=""``)은 옛 모양 그대로 행마다 선을 긋는다."""
    from PyQt6.QtWidgets import QFrame

    from aoi_verification.app.coords.single_info import InfoGroup, InfoRow
    rows = (InfoRow("a", "1", cluster="x"), InfoRow("b", "2", cluster="x"),
            InfoRow("c", "3", cluster="y"), InfoRow("d", "4", cluster="y"),
            InfoRow("e", "5"), InfoRow("f", "6"))
    dlg = ImageInfoDialog(image_path=str(_kla_image(tmp_path)))
    dlg._groups = [InfoGroup("g", rows)]
    dlg._render()
    frames = [f for f in dlg._scroll.widget().findChildren(QFrame)
              if f.property("role") == "row"]
    assert [f.property("rule") == "none" for f in frames] == \
        [True, False, True, False, False, True]   # 마지막 행은 상자 테두리가 대신한다
    dlg.deleteLater()


def test_qss_hides_divider_inside_cluster():
    """``rule="none"`` 행은 아래 눈금을 투명한 행 테두리 색으로 — 두께는 같아 높이가 안 바뀐다."""
    from pathlib import Path

    from aoi_verification.app.ui import theme
    src = Path(theme.__file__).with_name("style.qss").read_text(encoding="utf-8")
    qss = theme.render_qss(src)
    head = 'QFrame[role="row"][rule="none"]'
    assert head in qss
    body = qss.split(head, 1)[1].split("}", 1)[0]
    assert "border-bottom: 1px solid" in body
    assert theme.TOKENS["row_border"] in body


def test_action_buttons_float_bottom_right_and_sheet_uses_full_height(qapp, isolated_cache):
    """[GL 히스토그램]·[전체 복사] 는 레이아웃 밖에 떠 있다 — 가로 줄이 시트 높이를 깎지 않는다."""
    dlg = ImageInfoDialog()
    try:
        dlg.resize(1000, 700)
        dlg.show()
        qapp.processEvents()
        f = dlg._float
        assert f.parentWidget() is dlg
        assert dlg.copy_btn.parentWidget() is f and dlg._toast.parentWidget() is f
        assert f not in [dlg.layout().itemAt(i).widget() for i in range(dlg.layout().count())]
        assert not dlg.strip_bar.isVisible()
        assert f.geometry().right() + 1 == dlg.width() - 16
        assert f.geometry().bottom() + 1 == dlg.height() - 16
        # 시트 영역이 바닥 여백 바로 위까지 내려온다(예전엔 버튼 줄 높이만큼 위에서 끝났다).
        scroll_bottom = dlg._scroll.mapTo(dlg, dlg._scroll.rect().bottomLeft()).y() + 1
        assert scroll_bottom >= dlg.height() - 16 - 2
        assert dlg.copy_btn.autoDefault() is False
    finally:
        dlg.deleteLater()
