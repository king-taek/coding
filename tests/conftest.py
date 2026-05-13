"""테스트 공통 설정.

- 캐시 디렉토리를 임시 폴더로 격리해서 실제 사용자 디렉토리를 더럽히지 않는다.
- 테스트는 PyQt6/torch/cv2 같은 무거운 의존성을 거의 쓰지 않도록 설계.
"""

import os
import sys
import tempfile
from pathlib import Path

# 패키지 import 가능하도록
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    """모든 테스트가 임시 HOME 의 캐시 디렉토리를 쓰도록 강제."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # paths.cache_root() 는 Path.home() 으로 시작하니, HOME 만 바꿔도 동작.
    yield tmp_path
