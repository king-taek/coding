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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")
pytest.importorskip("numpy")

from PyQt6.QtCore import QMimeData, Qt, QUrl                # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel            # noqa: E402

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
    assert "colHead" in roles, "그룹마다 타이틀블록 컬럼 머리가 있어야 한다"
    assert chips == ["none"], "측정정보 미지원은 스탬프 하나로"
    dlg.deleteLater()


def test_enter_does_not_refire_the_last_button(qapp, isolated_cache):
    """Enter 를 autoDefault 에 맡기지 않는다 — slot_select_dialog 와 같은 함정.

    맡기면 포커스가 '전체 복사'/'닫기' 에 있을 때 Enter 가 그 동작을 재발동한다."""
    dlg = ImageInfoDialog()
    assert dlg.pick_btn.isDefault() is True
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


def test_drop_accepts_only_image_files(qapp, isolated_cache, tmp_path):
    img = tmp_path / "a.jpeg"
    img.write_bytes(b"x")
    other = tmp_path / "a.txt"
    other.write_text("x", encoding="utf-8")

    class _Evt:
        def __init__(self, md):
            self._md = md

        def mimeData(self):
            return self._md

    md_img = QMimeData()
    md_img.setUrls([QUrl.fromLocalFile(str(other)), QUrl.fromLocalFile(str(img))])
    assert ImageInfoDialog._dropped_image(_Evt(md_img)) == img

    md_none = QMimeData()
    md_none.setUrls([QUrl.fromLocalFile(str(other))])
    assert ImageInfoDialog._dropped_image(_Evt(md_none)) is None


def test_setup_page_opens_this_dialog(qapp, isolated_cache):
    """셋업 액션바 버튼이 이 다이얼로그로 연결돼 있다.

    액션바 인덱스 계약(개발자 버튼과의 순서)은
    ``test_setup_controls.test_action_bar_index_contract`` 가 지킨다.
    """
    import inspect

    from aoi_verification.app.ui.pages import setup_page as sp

    src = inspect.getsource(sp.SetupPage._open_image_info)
    assert "ImageInfoDialog" in src
    assert "full_bleed=True" in src


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
