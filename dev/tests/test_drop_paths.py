"""끌어다 놓기·붙여넣기 공통 도구(ui/drop_paths.py) 검증."""

import pytest

from aoi_verification.app.ui.drop_paths import (clean_path_text, local_paths,
                                                split_paths)


@pytest.mark.parametrize("raw,want", [
    ('  "C:\\a b\\c"  ', "C:\\a b\\c"),
    ("file:///C:/x/y%20z", "C:/x/y z"),
    ("file://server/share/d", "//server/share/d"),
    ("\\\\srv\\share", "\\\\srv\\share"),
    ("/home/u", "/home/u"),
    ("file:///home/u/a%20b", "/home/u/a b"),
    ("relative/x", ""),
    ("hello", ""),
    ("", ""),
])
def test_clean_path_text(raw, want):
    assert clean_path_text(raw) == want


def test_split_paths_order_and_dedupe():
    ps = ["/d1", "/a.png", "/d1/", "/b.txt", "/a.png", "/c.xlsx"]
    f, i, o = split_paths(ps, is_dir=lambda p: p.startswith("/d"),
                          is_image=lambda p: p.endswith(".png"),
                          is_xlsx=lambda p: p.endswith(".xlsx"))
    assert f == ["/d1"] and i == ["/a.png"] and o == ["/b.txt", "/c.xlsx"]


def test_local_paths_urls_and_text():
    pytest.importorskip("PyQt6.QtCore")
    from PyQt6.QtCore import QMimeData, QUrl
    m = QMimeData()
    m.setUrls([QUrl.fromLocalFile("/tmp/a.png"), QUrl("http://x/y")])
    assert local_paths(m) == ["/tmp/a.png"]
    t = QMimeData()
    t.setText('"/tmp/a"\n\nnot a path\nfile:///tmp/b')
    assert local_paths(t) == ["/tmp/a", "/tmp/b"]
    assert local_paths(QMimeData()) == []


def test_install_paste(styled_qapp):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QMimeData, QUrl
    from PyQt6.QtGui import QImage
    from PyQt6.QtWidgets import QApplication, QLineEdit, QWidget
    from aoi_verification.app.ui.drop_paths import install_paste

    got, none = [], []
    w = QWidget()
    edit = QLineEdit(w)
    sc = install_paste(w, got.append, none.append)
    cb = QApplication.clipboard()

    m = QMimeData()
    m.setUrls([QUrl.fromLocalFile("/tmp/a.png")])
    cb.setMimeData(m)
    w.show()
    w.setFocus()
    sc.activated.emit()
    assert got == [["/tmp/a.png"]]

    m = QMimeData()
    m.setImageData(QImage(2, 2, QImage.Format.Format_RGB32))
    cb.setMimeData(m)
    sc.activated.emit()
    assert none == [True]

    m = QMimeData()
    m.setText("그냥 글자")
    cb.setMimeData(m)
    sc.activated.emit()
    assert none == [True, False]

    edit.setFocus()
    QApplication.processEvents()
    if QApplication.focusWidget() is edit:      # 입력칸 포커스면 글자가 붙는다
        sc.activated.emit()
        assert edit.text() == "그냥 글자" and len(got) == 1 and len(none) == 2
    w.close()


def test_ctrl_v_with_focused_lineedit_routes_files(styled_qapp):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QEvent, QMimeData, Qt, QUrl
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtWidgets import QApplication, QLineEdit, QWidget
    from aoi_verification.app.ui.drop_paths import install_paste
    got, none = [], []
    w = QWidget()
    edit = QLineEdit(w)
    edit.setText("keep")
    install_paste(w, got.append, none.append)
    w.show()
    edit.setFocus()
    QApplication.processEvents()
    if QApplication.focusWidget() is not edit:
        pytest.skip("offscreen 포커스 없음")
    cb = QApplication.clipboard()
    m = QMimeData()
    m.setUrls([QUrl.fromLocalFile("/tmp/a.png")])
    cb.setMimeData(m)
    ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    QApplication.sendEvent(edit, ev)
    assert got == [["/tmp/a.png"]] and edit.text() == "keep"

    t = QMimeData()
    t.setText("글자")
    cb.setMimeData(t)
    edit.setText("")
    sc_ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    QApplication.sendEvent(edit, sc_ev)
    assert len(got) == 1                    # 글자 클립보드는 필터가 건드리지 않는다
    assert edit.text() == "글자"            # 평소 붙여넣기가 그대로 동작
    w.close()


def test_guess_kinds_by_name_only():
    from aoi_verification.app.ui.drop_paths import guess_kinds
    is_img = lambda p: p.lower().endswith((".jpg", ".png"))        # noqa: E731
    assert guess_kinds(["/x/a.JPG"], is_img) == {"image"}
    assert guess_kinds(["/x/r.xlsx", "/x/r2.XLSM"], is_img) == {"xlsx"}
    assert guess_kinds(["/x/LOT", "/x/a.png", "/x/n.txt"], is_img) == {"folder", "image", "other"}
    # 점 든 폴더 이름은 잘못 거절하지 않는다 — 모르는 확장자는 folder 쪽
    assert guess_kinds(["/x/TB500.RDL4", "/x/00NUR.001"], is_img) == {"folder"}
