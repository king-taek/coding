"""CollapsibleSection 위젯 — 토글/시그널 동작."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

from aoi_verification.app.ui.widgets.collapsible_section import (  # noqa: E402
    CollapsibleSection,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_default_collapsed(qapp):
    c = CollapsibleSection(expanded=False)
    assert c.is_expanded() is False


def test_default_expanded(qapp):
    c = CollapsibleSection(expanded=True)
    assert c.is_expanded() is True


def test_set_expanded_no_animation_emits_signal(qapp):
    c = CollapsibleSection(expanded=False)
    fired: list[bool] = []
    c.toggled.connect(lambda b: fired.append(b))
    c.set_expanded(True, animate=False)
    assert c.is_expanded() is True
    assert fired == [True]
    c.set_expanded(False, animate=False)
    assert c.is_expanded() is False
    assert fired == [True, False]


def test_set_expanded_idempotent(qapp):
    c = CollapsibleSection(expanded=True)
    fired: list[bool] = []
    c.toggled.connect(lambda b: fired.append(b))
    c.set_expanded(True, animate=False)
    assert fired == []  # 변경 없음 → 시그널 emit 안 함


def test_add_content_widget(qapp):
    c = CollapsibleSection(expanded=False)
    inner = QLabel("hello")
    c.add_content_widget(inner)
    # 내부에 추가됐는지 확인 — 부모가 본문 컨테이너인지.
    assert inner.parent() is c._content
