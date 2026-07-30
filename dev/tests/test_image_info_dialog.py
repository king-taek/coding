"""단일 사진 정보 다이얼로그 + 셋업 액션바 버튼 순서 — 헤드리스 검증.

지키는 계약:

- 사진을 넣기 전에는 안내 문구를, 계측을 하나도 못 읽으면 '읽을 수 있는 정보 없음' 을
  **실제로** 띄운다(그 안내가 도달 불가였던 회귀가 있다).
- 수치는 모노, 측정정보 유무는 판정 스탬프 — 앱의 '도면' 언어를 실제로 쓴다.
- 공백 없는 긴 경로가 가로 스크롤을 만들지 않는다.
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

from PyQt6.QtCore import QMimeData, QUrl                    # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel            # noqa: E402

from aoi_verification.app import i18n                       # noqa: E402
from aoi_verification.app.ui.widgets.image_info_dialog import (  # noqa: E402
    ImageInfoDialog)

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

    ``setWordWrap`` 은 끊을 곳이 없어 듣지 않는다 — 값 라벨이 가운데 생략해야 한다."""
    deep = tmp_path / ("n_" + "a" * 60) / ("s_" + "b" * 60)
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
    # 표는 라벨/값 2열이다 — 엑셀 한 줄("col 1 / row 2")과 모양이 다르고 수치는 같다.
    assert i18n.KO.DEFECT_COLROW_LABEL in texts
    assert "1 / 2" in texts
    # 복사 텍스트는 라벨\t값 — 엑셀에 붙여넣으면 값이 한 열로 선다.
    assert f"{i18n.KO.DEFECT_COLROW_LABEL}\t1 / 2" in dlg.as_text()
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
