"""계층 가드 — 하위 계층은 UI 를 import 하지 않는다.

``workers``·``models``·``coords``·``similarity``·``utils``·``learning``·``i18n`` 은
헤드리스로 단위 테스트할 수 있어야 한다(CLAUDE.md: "순수 로직은 무거운 의존성 없이").
이 중 한 모듈이라도 ``app.ui`` 를 import 하면 PyQt6 가 딸려 들어와 그 계약이 깨진다 —
지금은 **0건**이고, 이 테스트가 그 0건을 못 박는다.

정규식이 아니라 **AST** 로 본다: 주석·독스트링에 적힌 ``from ..ui`` 는 위반이 아니고,
함수 안에 숨은 지역 import 는 ``ast.walk`` 가 똑같이 잡는다.  Qt 를 전혀 쓰지 않으므로
빠른 레인(``-m "not ui"``)에서 돈다.

※ 반대 방향(UI → 하위 계층)은 정상이다.  UI 가 워커·모델을 부르는 것이 이 앱의 흐름
  이고, 그중 무거운 것들은 **함수 안에서** 부른다(시작 속도 —
  ``dev/tests/test_startup_light_import.py``).
"""

from __future__ import annotations

import ast
from pathlib import Path

# tests 는 dev/tests/ 로 정리됨 → 루트는 parents[2].
_ROOT = Path(__file__).resolve().parents[2]
_APP = _ROOT / "aoi_verification" / "app"
_LOWER = ("workers", "models", "coords", "similarity", "utils", "learning", "i18n")


def _imported_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if isinstance(node, ast.ImportFrom):
        # 상대 import 는 module 이 None 일 수 있다(`from .. import ui`).
        return [f"{node.module or ''}.{a.name}" for a in node.names]
    return []


def _imports_of(pkg: str, target: str) -> list[str]:
    """``app/<pkg>`` 안에서 ``target`` 패키지를 import 하는 자리들."""
    hits: list[str] = []
    for py in sorted((_APP / pkg).rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), str(py))
        for node in ast.walk(tree):
            for name in _imported_names(node):
                # 경로 **구성요소**로 비교 — gui·ui_helpers 같은 이름에 안 걸린다.
                if target in name.split("."):
                    hits.append(
                        f"{py.relative_to(_ROOT)}:{node.lineno}: {name}")
    return hits


def _ui_imports() -> list[str]:
    return [h for pkg in _LOWER for h in _imports_of(pkg, "ui")]


def test_lower_layers_do_not_import_ui():
    # 패키지 이름이 바뀌면 rglob 이 빈 결과를 내 가드가 조용히 무력해진다 — 먼저 막는다.
    missing = [p for p in _LOWER if not (_APP / p).is_dir()]
    assert not missing, f"계층 패키지가 사라졌다 — 가드가 헛돈다: {missing}"

    hits = _ui_imports()
    assert not hits, (
        "계층 위반 — 아래 모듈이 app.ui 를 import 한다:\n  "
        + "\n  ".join(hits)
        + "\n(UI 가 하위 계층을 부르는 것은 되지만 그 반대는 안 된다 — 필요한 값은"
          " 인자나 시그널로 넘기세요.)"
    )


def test_utils_does_not_import_models():
    """``models`` → ``utils`` 는 되지만 그 반대는 안 된다 (순환 방지).

    ``models.session`` 이 ``utils.paths`` 를 최상위에서 import 하므로, ``utils`` 가
    ``models`` 를 부르면 곧바로 순환이다.  예전에는 ``utils.wafer_id`` 가 그것을
    **함수 안 지연 import** 로 피해 갔다 — 동작은 했지만 '어느 쪽이 위인지' 가
    사라져 다음 사람이 최상위에 옮겨 적으면 그 자리에서 ImportError 가 난다.
    그 함수(``merge_unmatched_by_wafer_id``)는 ScanResult/Slot/ImageItem 만
    다루므로 ``models.slot`` 으로 옮겼고, 여기서 그 방향을 못 박는다.
    """
    hits = _imports_of("utils", "models")
    assert not hits, (
        "순환 위험 — utils 가 models 를 import 한다:\n  " + "\n  ".join(hits)
        + "\n(models 가 utils 를 쓰는 방향이 정방향이다. 모델 데이터를 다루는"
          " 함수라면 models 쪽으로 옮기세요.)"
    )
