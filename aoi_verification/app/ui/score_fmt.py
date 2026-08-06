"""점수 → 표시 문자열 (좌표 모드는 µm 거리, 구형 엔진은 유사도 %).

``workers/coord_matcher`` 가 만든 score 인코딩을 그대로 역산한다 — CLAUDE.md 좌표
규칙의 round-trip 계약이다:

- ``score >= 0`` → ``dist = (1 - score) × tol``   (허용 오차 내)
- ``score < 0``  → ``dist = (-score) × tol``      (허용범위 초과)

★ 경계값 ``dist == tol`` 은 ``score == 0.0`` 이고 **허용 내**다.  판정을 ``> 0`` 으로
  바꾸면 그 한 점이 '초과' 로 뒤집혀 계약이 깨진다 — 반드시 ``>= 0`` 이어야 한다.

``verbose=False`` 는 '허용범위 초과' 접미어를 빼고 거리만 돌려준다 — 초과 여부를
색이나 칩이 따로 말해 주는 자리(차순위 타일 라벨·metric 컬럼)에서 쓴다.

※ 여기(``ui/`` 최상위)에 두는 이유: 위젯과 페이지 **양쪽**이 쓰는 비-위젯 헬퍼라
  ``ui/theme.py``·``ui/motion.py`` 와 같은 자리다.  ``i18n/ko.py`` 는 문자열 전용
  모듈(함수가 하나도 없다)이라 넣지 않는다.  Qt 를 import 하지 않아 순수 테스트가 된다.
"""

from __future__ import annotations

from .. import i18n
from .. import config as _config

# 허용 오차 폴백 — 값이 안 들어왔을 때만 쓴다.  **단일 출처는 config** 다
# (예전엔 리터럴 500 이 곳곳에 박혀 있어 기본값을 바꿔도 옛 값이 되살아났다).
_DFLT_TOL = _config.DEFAULT_COORD_TOLERANCE


def fmt_score(score: float, coord_mode: bool, tolerance: float,
              *, verbose: bool = True) -> str:
    if coord_mode:
        tol = float(tolerance) if tolerance > 0 else _DFLT_TOL
        dist = (1.0 - score) * tol if score >= 0 else (-score) * tol
        fmt = (i18n.KO.SCORE_DIST_OVER_FMT if score < 0 and verbose
               else i18n.KO.SCORE_DIST_FMT)
        return fmt.format(dist=dist)
    return i18n.KO.SCORE_SIMILARITY_FMT.format(pct=score * 100)
