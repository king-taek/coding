"""테스트 공통 설정.

- 캐시 디렉토리를 임시 폴더로 격리해서 실제 사용자 디렉토리를 더럽히지 않는다.
- 테스트는 PyQt6/torch/cv2 같은 무거운 의존성을 거의 쓰지 않도록 설계.
"""

import sys
from pathlib import Path

# 패키지 import 가능하도록 저장소 루트를 path 에 추가.
# (tests 는 dev/tests/ 로 정리됨 → 루트는 parents[2])
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    """모든 테스트가 임시 HOME 의 캐시 디렉토리를 쓰도록 강제."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # exe launcher 가 설정하는 캐시 위치 override 가 테스트에 새지 않도록 제거.
    monkeypatch.delenv("AOI_DATA_HOME", raising=False)
    # 'exe + app 폴더' 설치 신호도 마찬가지 — 새면 updater 가 스테이징 모드로 빠지고
    # results_dir() 이 엉뚱한 곳을 가리킨다.
    monkeypatch.delenv("AOI_APP_HOME", raising=False)
    # paths.cache_root() 는 Path.home() 으로 시작하니, HOME 만 바꿔도 동작.
    # 세션 간 mtime 메모이즈가 새지 않도록 테스트마다 초기화(#5).
    try:
        from aoi_verification.app.utils import cache as _cache
        _cache.reset_mtime_cache()
    except Exception:
        pass
    yield tmp_path


# ---------------------------------------------------------------------------
# Qt — 테마 적용은 **세션당 1회**
# ---------------------------------------------------------------------------
# ★ `QApplication.setStyleSheet` 은 부를수록 비싸진다.  Qt 내부 스타일 캐시가 프로세스
#   수명 동안 누적돼, 같은 호출이 세션 초반 1.4초에서 후반 5.9초까지 늘어난다(실측).
#   살아있는 위젯 수와는 **무관**하다 — 위젯 0개에서도 5.9초가 나온다.
#   그래서 모듈마다 테마를 다시 적용하면 그 비용만 스위트 전체 시간의 절반을 먹는다
#   (실측: 21회 호출 = 82초 / 전체 181초).  테마가 필요한 모듈은 자기 픽스처를 만들지 말고
#   `styled_qapp` 을 받아 쓴다.
#
# QSS **문자열만** 검사하는 테스트는 이것조차 쓰지 마라 — `theme.render_qss` 로 렌더해
# 문자열을 직접 보면 setStyleSheet 이 아예 필요 없다(참조: test_sheet_size_and_edge).
@pytest.fixture(scope="session")
def _qt_app():
    """QApplication 싱글톤 — 프로세스당 하나.

    ★ 이름에 밑줄이 붙은 이유: 테스트 모듈들이 `qapp` 을 자기 스코프로 **재정의**한다.
    아래 두 픽스처가 `qapp` 에 의존하면 그 재정의를 집어 ScopeMismatch 가 난다
    (session 픽스처가 module 픽스처를 요구하는 꼴).  재정의되지 않는 이름을 따로 둔다.
    """
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="session")
def qapp(_qt_app):
    """QApplication — 테마는 적용하지 않는다."""
    return _qt_app


@pytest.fixture(scope="session")
def styled_qapp(_qt_app):
    """테마가 적용된 QApplication — `setStyleSheet` 을 세션당 1회만 부른다."""
    from aoi_verification.app.ui import theme
    theme.apply_to_app(_qt_app)
    return _qt_app


def pytest_collection_modifyitems(items):
    """Qt 를 쓰는 테스트에 `ui` 마커를 자동 부여 — 41개 파일을 건드리지 않는다.

    빠른 레인: ``python -m pytest -m "not ui"`` (순수 로직만, 몇 초).
    """
    for item in items:
        if {"qapp", "styled_qapp"} & set(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.ui)
