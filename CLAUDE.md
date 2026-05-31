# CLAUDE.md — 이 저장소에서 작업할 때 지켜야 할 규칙

> 세션이 바뀌어도 일관성을 유지하기 위한 **자동 참조 규칙**입니다. 작업 전 한 번 읽고,
> 아래 규칙과 충돌하는 변경은 하지 마세요. (Claude Code 가 세션 시작 시 자동으로 읽습니다.)

## 프로젝트 한 줄 요약
Intel CPU·GPU·NPU 를 쓰는 AOI(반도체 광학검사) 이미지 **매칭 검증** 데스크톱 앱
(Python/PyQt6, OpenVINO). 핵심 과제: **매칭(Stage 2) 속도 개선 — 정확도는 보존.**

## 브랜치 / 커밋
- 개발 브랜치: **`claude/matching-npu-gpu-modes-GwTRB`** (별도 지시 없으면 여기서 작업·커밋·푸시).
- 커밋 메시지는 한국어로 '무엇을·왜' 중심. PR 은 사용자가 명시적으로 요청할 때만 생성.
- **금지**: 커밋/코드/문서 등 저장소 산출물에 내부 모델 식별자(model id)나 그 추정 이름을 적지 않는다.

## 로딩바(LoadingOverlay) 규칙 — 오작동 방지 (중요)
로딩 진행을 바꾸거나 새 장시간 작업을 추가할 때 **반드시** 아래를 지킨다. 안 지키면
"0 에서 안 움직이다 갑자기 완료" 같은 오작동이 난다.
- 진행 표시는 항상 `LoadingOverlay.set_progress(done, total, message)` 로 한다.
  - `total > 0` → 결정형(determinate). 값이 **증가**하면 부드럽게 tween, 감소/범위변경은 즉시 스냅.
  - `total <= 0` → busy(무한 진행). **진행량을 모를 때도 0 에 멈추지 말고** `set_progress(0, 0, msg)`
    로 busy 를 띄운다(움직이는 표시).
- **장시간 작업은 백그라운드 스레드/프로세스**에서 돌리고, 진행은 **pyqtSignal 로 메인 스레드에
  전달**해 `set_progress` 를 호출한다. UI 스레드에서 직접 블로킹 금지(바가 멈춘다).
- 코어 함수(예: `updater.download_and_apply`)는 `progress(done, total, phase)` **콜백을 받게**
  설계하고, 단계가 바뀌면(다운로드→압축해제→적용) 단계별로 진행을 보고한다.
- 참조 구현: `updater.download_and_apply(..., progress=...)` →
  `main_window._update_progress`(시그널) → `_on_update_progress` → `LoadingOverlay.set_progress`.

## 자동 업데이트 규칙
- 업데이트는 **앱 구동에 필요한 것을 전부** 받아 앱 폴더로 **미러링**한다(새 모듈/리소스 누락 방지).
  - 제외: 개발 전용·대용량 데이터(`tests/`, `기준/`, `bench결과/`), VCS/캐시(`.git`,
    `.pytest_cache`, `__pycache__`), 그리고 무거운 `python/` 런타임. 목록은
    `updater._UPDATE_SKIP_TOP` 에서 관리한다.
  - 새 최상위 폴더/파일을 추가하면, 그것이 **구동에 필요하면 자동 포함**된다(별도 작업 불필요).
    구동에 불필요한 대용량/개발 전용이면 `_UPDATE_SKIP_TOP` 에 추가한다.
- **의존성 패키지는 자동 재설치하지 않는다.** `requirements.txt` 변경은 감지(`deps_changed()`)
  해 사용자에게 '수동 갱신' 안내만 한다(`run_this_before.py` 재실행 / 번들 python 에 pip).
  자세한 동작은 `docs/업데이트_동작.md`.
- 자동 적용은 **포터블 빌드에서만**(개발/git 작업트리는 `is_git_checkout` 으로 차단).

## 개발자 벤치마크(매칭 속도 실험) 규칙
- 레시피 **실행은 자식 프로세스로 격리**한다(`benchmark.drive_isolated_suite`). OpenVINO/NPU
  네이티브 크래시·멈춤이 GUI 를 죽이지 않게 — 파이썬 예외/타임아웃으론 못 막는다. 자식이
  죽으면 범인 레시피를 기록하고 살아남은 것만 이어서 측정(부분 `result.json` 으로 복구).
- 측정은 항상 **유사도 캐시 우회**(`bench_no_cache`)로 '처음 매칭처럼'.
- 정확도가 운영(`gpu_fusion_b16`)보다 낮으면 **더 빨라도 추천하지 않는다**(`recommend`).
- 실측 결론(`docs/진행상황_매칭속도개선.md`): 병목은 **CPU 재채점(~57s)**, 임베딩 장치
  교체는 속도 이득 거의 없음(×1.02). 3배의 레버는 **CPU 재채점 축소**(`fast-rerank`/`cpu_rr_*`).
- 새 레시피는 **실제 채점 경로에 배선**해 동작하게 한다(스캐폴드면 그 사실을 desc/문서에 명시).

## 파일 구성 관습
- 문서는 `docs/`. 빌드/설치 스크립트는 `scripts/`. 테스트는 `tests/`.
- **단일 리소스 파일**(예: `양식.xlsx`)은 그 파일 하나만을 위한 폴더를 새로 만들지 않는다.
  단, 코드가 폴더형(`양식/양식.xlsx`)을 우선 탐색하므로 경로 변경 시 `paths.template_path()` 확인.
- 표준 위치 파일은 루트에 둔다: `README.md`, `main.py`, `requirements.txt`, `pytest.ini`,
  `.gitignore`, `.vscode/`.
- 샘플/실측 데이터(`기준/`, `bench결과/`)는 코드가 경로로 참조한다
  (`paths.resource_path("기준")`, `benchmark.iter_history` 의 `bench결과`). 옮기면 참조도 함께 고친다.

## 보안 가드 (회사 정책)
- 회사에서 **금지한 외부 패키지 매니저/IDE 계열 도구**를 코드·문서·의존성에 도입하지 않는다
  (구체 목록·패턴은 `scripts/internal/verify_no_forbidden.py` 와 테스트 `tests/test_no_spyder_conda.py`
  가 보유). 금지 키워드를 문서에 적기만 해도 가드가 실패하니, 이름을 직접 쓰지 말고 가드를 참조한다.
- 커밋 전 `python scripts/internal/verify_no_forbidden.py` 가 통과해야 한다(`run_this_before.py` 도 실행).

## 테스트
- 커밋 전 전체 테스트 통과 확인: `QT_QPA_PLATFORM=offscreen python -m pytest -q`.
- 무거운 의존성(cv2/openvino/torch/PyQt6)은 환경에 없을 수 있어 `pytest.importorskip` 으로
  게이트한다. **순수 로직은 무거운 의존성 없이** 단위 테스트되게 설계(예: 벤치마크 격리
  드라이버는 spawn 주입으로 헤드리스 테스트).
- 동작/리소스(로딩바·업데이트·레시피 배선)를 바꾸면 그에 대응하는 테스트를 추가/갱신한다.
