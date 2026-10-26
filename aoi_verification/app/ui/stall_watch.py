"""UI 스레드가 멈춘 구간을 **재기만** 한다 — 무엇이라고 적을지는 호출부가 정한다.

왜 필요한가.  '몇 초 멈춘다' 는 신고가 이어지는데 앱이 아무 말도 하지 않으면, 다음
후보를 세울 근거가 로그밖에 없다.  폴더 선택 렉은 이미 세 번 고쳤다(die 안내 스캔을
워커로, 폴더 확인을 워커로, 표본을 슬롯 하나로).  계측상 그 경로에는 UI 스레드가
건드리는 파일이 **한 개도 남아 있지 않은데**(회귀 가드:
`test_picking_a_folder_touches_no_files_on_the_ui_thread`) 신고는 남았다.  즉 원인이
우리 코드 밖(OS 파일 브라우저·무거운 import 의 GIL 점유 등)일 가능성이 큰데, 그걸
가리려면 '언제 · 얼마나 · 그때 무엇이 살아 있었나' 가 필요하다.

방식.  짧은 주기로 도는 하트비트 타이머의 **지각**을 잰다.  이벤트 루프가 막히면
타이머는 막힌 만큼 늦게 오므로, 그 지각이 곧 UI 정지시간이다.  임계값을 넘은 구간만
호출부에 알린다 — 정상일 때는 아무 일도 일어나지 않는다(로그를 채우지 않는다).

★ 로그 문구를 여기서 만들지 않는다.  `_LOG` 호출은 호출부에 두어, 화면 문구 가드
  (`test_no_hardcoded_korean`)가 보는 그대로 '로거 인자' 로 남게 한다 — 이 문자열들은
  화면에 나가지 않고 `app.log` 로만 간다.
★ 감시는 **구간 한정**이다.  창이 사는 내내 도는 타이머를 하나 더 만들지 않는다.
  의심되는 조작 뒤 몇 초만 켜고 스스로 꺼진다.
★ 타이머는 **부모를 갖고, 스스로 지우지 않는다.**  정적 ``QTimer.singleShot`` 으로
  두면 죽은 위젯으로 콜백이 들어가 세그폴트가 난다(전례 있음).  ⚠ 반대로
  다 쓴 뒤 ``deleteLater`` 로 자기를 지우게 두면 **부모도 지워지는 순간 이중 삭제**가
  된다 — 이것도 파이썬 예외가 아니라 세그폴트다(실제로 이 파일에서 냈다).  수명은
  부모 하나만 쥔다.
★ 감시자는 **부모당 하나**를 돌려쓴다(`pages/setup_page._schedule_validate` 와 같은
  관습).  조작할 때마다 새로 만들면 멈춘 타이머가 페이지에 쌓인다.
"""

from __future__ import annotations

import sys
import threading
import time
import traceback
from typing import Callable, Optional

from PyQt6.QtCore import QTimer

__all__ = ["watch", "running_jobs"]

# ★ '얼마나' 만으로는 다음 수정을 고를 수 없었다 — 다섯 번째 신고.  그래서 멈춘
#   **그 순간 UI 스레드가 무엇을 하고 있었는지**(파이썬 호출 스택)를 보조 스레드가
#   찍는다.  하트비트(`_tick`)가 갱신하는 시각을 데몬 스레드가 100ms 마다 보고, 임계값
#   넘게 안 움직이면 ``sys._current_frames()`` 로 메인 스레드 스택을 떠서 호출부에
#   준다.  맨 위가 ``app.exec`` 뿐이면 Qt/Windows 안(우리 파이썬 밖), 그 아래 우리
#   파일이 있으면 그 줄이 범인이다.  같은 정지 안에서는 2초마다 다시 찍는다(길어지는
#   정지에서 스택이 바뀌는지 보려고).  감시 구간 밖에서는 아무것도 하지 않는다.
_SAMPLE_EVERY_S = 2.0
_SAMPLER_TICK_S = 0.1
_hb_lock = threading.Lock()
_hb_at: float = 0.0                 # 마지막 하트비트(UI 스레드가 살아 있던 시각)
_hb_until: float = 0.0              # 이 시각까지만 샘플러가 본다
_hb_threshold_s: float = 0.3
_hb_on_stack: Optional[Callable[[float, str], None]] = None
_sampler: Optional[threading.Thread] = None
_main_tid = threading.main_thread().ident


def _ui_stack() -> str:
    frame = sys._current_frames().get(_main_tid)
    if frame is None:
        return ""
    return "".join(traceback.format_stack(frame)).rstrip()


def _sampler_loop() -> None:
    last_report = 0.0
    while True:
        time.sleep(_SAMPLER_TICK_S)
        now = time.perf_counter()
        with _hb_lock:
            hb, until, thr, cb = _hb_at, _hb_until, _hb_threshold_s, _hb_on_stack
        if cb is None or now > until:
            continue
        stale = now - hb
        if stale < thr:
            last_report = 0.0           # 정지가 풀렸다 — 다음 정지는 처음부터
            continue
        if now - last_report < _SAMPLE_EVERY_S:
            continue
        last_report = now
        try:
            cb(stale, _ui_stack())
        except Exception:
            pass                        # 진단이 앱을 멈추게 하지 않는다


def _arm_sampler(until: float, threshold_s: float,
                 on_stack: Optional[Callable[[float, str], None]]) -> None:
    global _sampler, _hb_at, _hb_until, _hb_threshold_s, _hb_on_stack
    with _hb_lock:
        _hb_at = time.perf_counter()
        _hb_until, _hb_threshold_s, _hb_on_stack = until, threshold_s, on_stack
    if on_stack is not None and _sampler is None:
        _sampler = threading.Thread(target=_sampler_loop, name="stall-sampler",
                                    daemon=True)
        _sampler.start()


def _heartbeat(now: float) -> None:
    global _hb_at
    with _hb_lock:
        _hb_at = now

_TICK_MS = 50          # 하트비트 주기 — 이보다 늦으면 그 차이가 정지시간이다.
# 감시자를 부모에 매달아 두는 이름 — 조작마다 새 타이머를 만들지 않는다.
_TIMER_ATTR = "_stall_watch_timer"

# 앱이 백그라운드에서 돌리는 파이썬 스레드 이름(`ui/main_window.py` 가 지어 준다).
# QThread(폴더 확인·die 스캔·폴더 스캔)는 여기 안 잡히므로 호출부가 따로 덧붙인다.
_JOB_THREADS = ("backend-import", "accel-warmup", "update-check", "cache-prune")


def running_jobs() -> str:
    """지금 살아 있는 백그라운드 파이썬 스레드 이름 — 없으면 빈 문자열.

    무거운 import(cv2·openvino)와 GPU 워밍업은 C 확장을 올리는 동안 GIL 을 길게
    쥔다.  UI 가 멈춘 순간 이것들이 살아 있었는지가 곧 단서다."""
    alive = [t.name for t in threading.enumerate() if t.name in _JOB_THREADS]
    return ", ".join(sorted(alive))


def watch(parent, on_stall: Callable[[float, str], None], *,
          seconds: float = 6.0, threshold_ms: float = 300.0,
          on_stack: Optional[Callable[[float, str], None]] = None) -> QTimer:
    """``seconds`` 동안 UI 정지를 감시한다.

    임계값을 넘을 때마다 ``on_stall(정지_초, 그때_살아있던_작업)`` 을 부른다.
    ``on_stack(멈춘_지_초, UI_스레드_스택)`` 을 주면 **멈춰 있는 도중에** 보조
    스레드가 부른다(위 주석) — ``on_stall`` 은 풀린 뒤에야 오기 때문이다.
    구간이 끝나면 타이머는 스스로 **멈춘다**(지우지는 않는다 — 위 주석) — 호출부가
    보관할 필요는 없고, 다시 부르면 같은 타이머를 다시 켠다."""
    timer = getattr(parent, _TIMER_ATTR, None)
    if timer is None:
        timer = QTimer(parent)             # 부모 있는 타이머 — 위 주석 참조
        timer.setInterval(_TICK_MS)
        setattr(parent, _TIMER_ATTR, timer)
    else:
        # ★ 다시 걸기 전에 **진행 중이던 감시의 밀린 정지를 먼저 보고한다.**
        #   정지 구간 안에서 호출부가 다시 걸면(폴더 창이 닫히며 UI 가 막힌 그 슬롯
        #   안에서 `closed` 가 와 6초 감시로 바꿔 거는 경우) 지각한 하트비트는 아직
        #   오지 않았는데 기준 시각이 리셋돼 — 재려던 바로 그 구간이 사라진다.
        _flush(timer)
        try:
            timer.timeout.disconnect()     # 지난 감시의 콜백을 떼고 다시 건다
        except TypeError:
            pass                           # 연결이 없었다

    last = [time.perf_counter()]
    deadline = last[0] + seconds
    _arm_sampler(deadline, threshold_ms / 1000.0, on_stack)

    def _report(now: float) -> None:
        late_ms = (now - last[0]) * 1000.0 - _TICK_MS
        last[0] = now
        if late_ms >= threshold_ms:
            try:
                on_stall(late_ms / 1000.0, running_jobs())
            except Exception:
                pass                       # 진단이 앱을 멈추게 하지 않는다

    def _tick() -> None:
        now = time.perf_counter()
        _heartbeat(now)
        _report(now)
        if now >= deadline:
            timer.stop()                   # ⚠ 지우지 않는다 — 수명은 부모가 쥔다

    timer.timeout.connect(_tick)
    timer._stall_report = _report          # `_flush` 가 쓴다 — 감시 중일 때만 의미
    timer.start()
    return timer


def _flush(timer: QTimer) -> None:
    """돌고 있는 감시가 있으면 지금까지의 지각을 그 감시의 콜백으로 보고한다."""
    report = getattr(timer, "_stall_report", None)
    if report is not None and timer.isActive():
        report(time.perf_counter())
