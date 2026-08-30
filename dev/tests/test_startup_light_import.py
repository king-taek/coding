"""메인 창 모듈은 **가벼워야** 한다 — 무거운 것을 끌고 오면 창이 늦게 뜬다.

사용자 지적: "시스템 로딩 과정 간소화 가능할지? -> 지금 좀 느림 -> 구동에 필수적인거
먼저 하고나서 나머지는 메인 화면 켜지고 나서 로딩".

시작이 느렸던 이유는 ``ui.main_window`` 를 import 하는 것만으로 cv2 · OpenVINO ·
numpy · PIL 이 줄줄이 딸려 왔기 때문이다(매칭/선별/결과 페이지 → 유사도 → 임베더).
첫 화면(SetupPage)은 그중 무엇도 쓰지 않는데도 그 전부가 끝나야 창이 떴다.

지금은 그 넷을 창이 뜬 **뒤** 백그라운드에서 불러온다.  이 가드가 없으면 누군가
편의상 최상위 import 를 하나 되살리는 순간 조용히 원래대로 돌아간다 — 화면은
똑같이 동작하므로 아무도 눈치채지 못한다.

★ 서브프로세스에서 검사한다.  같은 프로세스에서는 다른 테스트가 이미 cv2 등을
  올려 뒀을 수 있어 ``sys.modules`` 검사가 무의미해진다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

_ROOT = Path(__file__).resolve().parents[2]

# 첫 화면에 필요 없는 무거운 것들.  하나라도 올라오면 그만큼 창이 늦게 뜬다.
_HEAVY = ("cv2", "openvino", "numpy", "PIL", "torch", "openpyxl")

_PROBE = """
import sys
import aoi_verification.app.ui.main_window   # noqa: F401
heavy = [m for m in {heavy!r} if m in sys.modules]
print("HEAVY:" + ",".join(heavy))
"""


def _run_probe(code: str) -> str:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = str(_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    # ★ 인코딩은 **로케일 그대로** 둔다(`text=True`).  여기 자식은 `sys.executable`,
    #   즉 파이썬이고 파이프에 묶인 파이썬은 **로케일로 쓴다** — 이 머신 실측:
    #   자식의 `sys.stdout.encoding` = cp949, '한글출력' 이 `b'ÇÑ±Û...'`
    #   (cp949)로 나온다.  여기에 `encoding="utf-8"` 을 주면 실패 시 보여 주려던
    #   한국어 트레이스백이 통째로 치환문자가 된다(실측으로 확인).
    #   ※ git 처럼 **외부 C 프로그램**은 반대다 — UTF-8 로 쓰므로 그쪽은 utf-8 을
    #     명시해야 한다(`test_manual_captures_never_committed` 참조).  출처마다 답이
    #     다르니 한 벌로 통일하지 마라.
    # ★ `errors="replace"` 만 더한다.  이게 없으면 디코드 실패가 리더 스레드를 죽여
    #   stdout/stderr 가 None 이 되고, 아래 assert 가 진짜 원인 대신
    #   AttributeError 를 뱉는다.
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, errors="replace",
                         cwd=str(_ROOT), env=env, timeout=300)
    assert out.returncode == 0, f"probe 실패:\n{out.stdout}\n{out.stderr}"
    for line in out.stdout.splitlines():
        if line.startswith("HEAVY:"):
            return line[len("HEAVY:"):]
    raise AssertionError(f"probe 출력이 없다:\n{out.stdout}\n{out.stderr}")


def test_main_window_import_pulls_no_heavy_module():
    loaded = [m for m in _run_probe(_PROBE.format(heavy=list(_HEAVY))).split(",") if m]
    assert not loaded, (
        "main_window 를 import 하는 것만으로 " + ", ".join(loaded) + " 가 올라온다 — "
        "창이 그만큼 늦게 뜬다.  무거운 모듈은 함수 안에서 부르거나 "
        "MainWindow._start_backend_import_async 에 맡겨라.")


def test_setup_page_alone_is_light():
    """첫 화면 자체도 가벼워야 한다 — 여기가 무거워지면 위 가드가 무의미해진다."""
    code = _PROBE.replace("aoi_verification.app.ui.main_window",
                          "aoi_verification.app.ui.pages.setup_page")
    loaded = [m for m in _run_probe(code.format(heavy=list(_HEAVY))).split(",") if m]
    assert not loaded, "SetupPage 가 " + ", ".join(loaded) + " 를 끌고 온다"
