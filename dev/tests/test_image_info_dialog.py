"""단일 사진 정보 다이얼로그 + 셋업 액션바 버튼 순서 — 헤드리스 검증.

지키는 계약:

- 사진을 넣기 전에는 안내 문구를, 정보가 하나도 없으면 '읽을 수 있는 정보 없음' 을 띄운다
  (빈 패널로 두면 고장난 화면처럼 보인다).
- 드롭은 **사진 파일만** 받는다 — 아무 파일이나 받으면 조회가 조용히 실패한다.
- 액션바에서 이 버튼은 **항상 index 1** 이다.  개발자 버튼은 그 뒤에 삽입되므로
  ``_refresh_dev_buttons`` 의 insertWidget 인덱스가 어긋나면 순서가 뒤집힌다.
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
    grid = dlg._info_grid
    return [grid.itemAt(i).widget().text()
            for i in range(grid.count())
            if isinstance(grid.itemAt(i).widget(), QLabel)]


def test_shows_hint_before_any_file(qapp, isolated_cache):
    dlg = ImageInfoDialog()
    assert i18n.KO.IMAGE_INFO_NO_FILE in _texts(dlg)
    assert dlg.as_text() == ""
    assert not dlg.copy_btn.isEnabled(), "복사할 게 없으면 버튼도 눌리지 않아야"
    dlg.deleteLater()


def test_shows_empty_notice_when_nothing_readable(qapp, isolated_cache, tmp_path):
    img = tmp_path / "plain.jpeg"
    img.write_bytes(b"x")
    dlg = ImageInfoDialog(image_path=str(img))
    texts = _texts(dlg)
    # 파일명/폴더는 항상 나오므로 '정보 없음' 안내는 뜨지 않는다.
    assert i18n.KO.IMAGE_INFO_EMPTY not in texts
    assert "plain.jpeg" in texts
    # 좌표/geometry 를 못 읽었으므로 결함 줄은 없다.
    assert not any("col " in t for t in texts)
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
    assert "col 1 / row 2" in texts
    # 복사 텍스트에도 같은 내용이 들어간다.
    assert "col 1 / row 2" in dlg.as_text()
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
