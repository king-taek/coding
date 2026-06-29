# CLAUDE.md — 이 저장소에서 작업할 때 지켜야 할 규칙

> 세션이 바뀌어도 일관성을 유지하기 위한 **자동 참조 규칙**입니다. 작업 전 한 번 읽고,
> 아래 규칙과 충돌하는 변경은 하지 마세요. (Claude Code 가 세션 시작 시 자동으로 읽습니다.)

## 프로젝트 한 줄 요약
Intel CPU·GPU·NPU 를 쓰는 AOI(반도체 광학검사) 이미지 **매칭 검증** 데스크톱 앱
(Python/PyQt6, OpenVINO). 흐름: **스캔 → Setup → (후보 선별) → 매칭 → 검토 → 결과(엑셀)**.
표준 작업 영역: 매칭 속도/정확도, 좌표 기반 매칭·검토, KLA(WaferID) OCR, 자동 업데이트,
UI 사용성. **공통 원칙: 정확도(검증 신뢰성)는 절대 깨지 않는다.**

## 브랜치 / 커밋
- **통합(기본) 브랜치 = 저장소 GitHub 기본 브랜치 `claude/aoi-verification-app-LAXpX`.**
  모든 작업은 결국 여기로 합류하고, 자동 업데이트도 이 브랜치를 추적한다(아래 자동 업데이트 규칙).
- 세션마다 별도 **기능 브랜치**가 작업 지시로 지정될 수 있다. 지정되면 거기서 작업·커밋·푸시하고,
  PR 로 기본 브랜치에 머지한다. 별도 지시가 없으면 기본 브랜치 기준으로 판단한다.
- 커밋 메시지는 **한국어로 '무엇을·왜'** 중심. PR 은 사용자가 요청할 때 생성한다.
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
- **추적 대상은 저장소의 GitHub 기본 브랜치**다. 포터블 빌드의 `app/VERSION` 에 박힌 브랜치가
  옛(삭제된) 브랜치여도 기본 브랜치로 합류해야 한다. 관련 불변식(깨지 않게 유지):
  - `updater.DEFAULT_BRANCH` 상수는 **현재 살아있는 기본 브랜치와 같게** 유지한다
    (api.github.com 차단 시 폴백으로 쓰임). 기본 브랜치를 바꾸면 이 상수도 함께 고친다.
  - `updater._resolve_branch` 는 빈 값·`claude/` 접두·옛 기본(`main`/`master`)을 기본 브랜치로
    정규화한다. `updater._latest_self_healing` 은 추적 브랜치가 404 면 기본 브랜치로 한 번 더
    시도하고 '실제 사용한 브랜치'를 반환한다(다운로드도 그 브랜치로 가게). 이 자기교정을 깨지 마라.
- 업데이트는 **앱 구동에 필요한 것을 전부** 받아 앱 폴더로 **미러링**한다(새 모듈/리소스 누락 방지).
  - 제외: 개발 전용·대용량 데이터(`dev/` 등), VCS/캐시(`.git`·`.pytest_cache`·`__pycache__`),
    무거운 `python/` 런타임. 목록은 `updater._UPDATE_SKIP_TOP` 에서 관리한다.
  - 새 최상위 폴더/파일이 **구동에 필요하면 자동 포함**된다(별도 작업 불필요). 구동에 불필요한
    대용량/개발 전용이면 `_UPDATE_SKIP_TOP` 에 추가한다.
- **의존성 패키지는 자동 재설치하지 않는다.** `requirements.txt` 변경은 감지(`deps_changed()`)
  해 사용자에게 '수동 갱신' 안내만 한다(`run_this_before.py` 재실행 / 번들 python 에 pip).
  자세한 동작은 `docs/업데이트_동작.md`.
- 자동 적용은 **포터블 빌드에서만**(개발/git 작업트리는 `is_git_checkout` 으로 차단).

## 매칭 / 좌표 검토 규칙
- **정확도 우선**: 운영 기본(`gpu_fusion_b16`)보다 정확도가 낮으면 더 빨라도 채택/추천하지 않는다.
- 좌표 기반 매칭(`workers/coord_matcher.py`)의 후보 게이트는 **(col,row) ±1 이내**다(정답 도구
  AOI Data Viewer VBA `Module_Compare`: `Abs(col차)<=1 And Abs(row차)<=1`). KLA↔Camtek 처럼
  두 장비의 die 인덱스가 1 어긋날 수 있어 정확 일치만 하면 매칭이 전멸한다. 순수 헬퍼
  `_match_neighbors` 로 분리해 헤드리스 테스트한다.
- 좌표 기반 매칭의 검토 후보 노출 규칙:
  - (col,row) ±1 이내 val 후보 중 **최소 거리 ≤ `CONFIDENT_DIST`(=20)** 면 '거의 정확히 일치'로 보고
    **1장만**, 그 외에는 **3×tol 이내 후보를 전부**(거리 오름차순=점수 내림차순) 차순위로 보여준다.
  - score 인코딩은 검토 타일 역산과 round-trip 되게 유지한다: `dist≤tol → 1-dist/tol`(양수),
    `tol<dist≤3tol → -(dist/tol)`(음수='허용범위 초과'). 후보 선택 로직은 순수 헬퍼
    `_select_coord_candidates` 로 분리해 헤드리스 테스트한다.
- KLA(WaferID) 장비 쪽 판정은 **기준/검증/둘다/KLA 아님**(`ref`/`val`/`both`/`None`) 네 경우를
  모두 지원한다(`main_window._ask_kla_side`·`_kla_resolve_impl`). 한쪽만 추가하지 말 것.

## 개발자 벤치마크(매칭 속도 실험) 규칙
- 레시피 **실행은 자식 프로세스로 격리**한다(`benchmark.drive_isolated_suite`). OpenVINO/NPU
  네이티브 크래시·멈춤이 GUI 를 죽이지 않게 — 파이썬 예외/타임아웃으론 못 막는다. 자식이
  죽으면 범인 레시피를 기록하고 살아남은 것만 이어서 측정(부분 `result.json` 으로 복구).
- 측정은 항상 **유사도 캐시 우회**(`bench_no_cache`)로 '처음 매칭처럼'.
- 실측 결론(`docs/진행상황_매칭속도개선.md`): 병목은 **CPU 재채점(~57s)**, 임베딩 장치
  교체는 속도 이득 거의 없음(×1.02). 3배의 레버는 **CPU 재채점 축소**(`fast-rerank`/`cpu_rr_*`).
- 새 레시피는 **실제 채점 경로에 배선**해 동작하게 한다(스캐폴드면 그 사실을 desc/문서에 명시).

## UI 사용성 관습
- **클릭 대상은 크고 명확하게.** 작은 기본 체크박스(예: `QListWidgetItem` 체크) 대신
  **타일/카드 전체가 클릭영역**인 토글을 쓴다. 선택 상태는 네온 보더+배경으로 강조하고
  손가락 커서를 준다. 참조 패턴: `widgets/bulk_select_dialog.py`(`_SelectTile`/`_relayout_grids`),
  `widgets/slot_select_dialog.py`(`_SlotTile`). 그리드는 viewport 폭 기반으로 열 수를 동적
  계산해 **가로 스크롤이 생기지 않게** 한다.
- 공통 버튼은 `widgets/neon_button.py`(`NeonButton`, role=primary/ghost) 를 쓴다.
- 사용자 노출 문자열은 `app/i18n/ko.py` 에 모은다(한국어). 위젯에 직접 하드코딩하지 않는다.

## 파일 구성 관습
- 문서는 `docs/`. 사용자가 실행하는 스크립트는 `scripts/`(`run_aoi*.bat`·`run_this_before.py`·
  `update_app.bat`·`build.py`·`launcher.py`). 빌드/내부 도구는 `scripts/internal/`
  (`*.spec`·`build_windows.bat`·`build_online.bat`·`make_portable.bat`·`portable_build.py`·
  `verify_no_forbidden.py`). 경로를 바꾸면 이를 호출하는 곳(run_this_before·빌드 bat·README·문서)도
  함께 고친다.
- **`aoi_verification/app/`** 가 앱 본체: `ui/`(pages·widgets), `workers/`(매칭·OCR·내보내기 등
  백그라운드), `coords/`(좌표 파서), `models/`, `similarity/`, `utils/`(`updater`·`paths`·`image_io`),
  `i18n/`, `learning/`, `dev/`(앱 내 벤치마크).
- **`dev/` = 사용자가 직접 건드리지 않는 개발 전용 모음.** `dev/tests/`(테스트)·`dev/bench결과/`
  (실측 데이터)·`dev/양식.xlsx`(엑셀 출력 템플릿). 옮길 때 함께 고칠 참조:
  - 테스트 경로: `pytest.ini` 의 `testpaths = dev/tests` (※ `pytest.ini` 는 루트 앵커라 이동 금지 —
    `python -m pytest` 가 루트에서 testpaths 로 찾는다).
  - `dev/tests/conftest.py`·`dev/tests/test_no_spyder_conda.py` 는 루트를 `parents[2]` 로 잡는다.
  - 양식 템플릿: `paths.template_path()` 가 `dev/양식.xlsx` 를 1순위로 찾는다. 포터블 빌드는
    `portable_build.py`/`*.spec`/`updater` 가 `dev/양식.xlsx` → 앱 루트 `양식.xlsx` 로 복사한다.
  - 실측 데이터: `benchmark.iter_history` 가 `dev/bench결과` 를 본다.
  - 자동 업데이트: `updater._UPDATE_SKIP_TOP` 가 `dev/` 를 통째로 건너뛰되 `dev/양식.xlsx` 만 앱
    루트로 따로 복사한다(구동 필수). `dev/` 에 새 개발 데이터를 넣어도 자동으로 제외된다.
- **루트 앵커(이동 금지)**: `README.md`·`main.py`·`requirements.txt`·`pytest.ini`·`.gitignore`·
  `.vscode/`(VS Code 가 `${workspaceFolder}/.vscode/` 만 읽음)·`CLAUDE.md`·`aoi_verification/`
  (`main.py` 가 `from aoi_verification …` 로 import — 루트에 있어야 함).
- **단일 리소스 파일**은 그 파일 하나만을 위한 폴더를 새로 만들지 않는다.

## 보안 가드 (회사 정책)
- 회사에서 **금지한 외부 패키지 매니저/IDE 계열 도구**를 코드·문서·의존성에 도입하지 않는다
  (구체 목록·패턴은 `scripts/internal/verify_no_forbidden.py` 와 테스트
  `dev/tests/test_no_spyder_conda.py` 가 보유). 금지 키워드를 문서에 적기만 해도 가드가 실패하니,
  이름을 직접 쓰지 말고 가드를 참조한다.
- 커밋 전 `python scripts/internal/verify_no_forbidden.py` 가 통과해야 한다(`run_this_before.py` 도 실행).

## 테스트
- 커밋 전 전체 테스트 통과 확인: `QT_QPA_PLATFORM=offscreen python -m pytest -q`.
- 무거운 의존성(cv2/openvino/torch/PyQt6)은 환경에 없을 수 있어 `pytest.importorskip` 으로
  게이트한다(모듈 단위 import 도 포함). **순수 로직은 무거운 의존성 없이** 단위 테스트되게 설계
  (예: 좌표 후보 선택 `_select_coord_candidates`, 업데이트 브랜치 정규화/자기교정, 벤치마크 격리
  드라이버의 spawn 주입 헤드리스 테스트).
- UI 동작은 `QT_QPA_PLATFORM=offscreen` + `pytest.importorskip("PyQt6.QtWidgets")` 로 헤드리스
  검증한다(참조: `dev/tests/test_match_review_clamp.py`·`test_slot_select_dialog.py`).
- 동작/리소스(로딩바·자동 업데이트·매칭/좌표 규칙·레시피 배선·UI 토글)를 바꾸면 그에 대응하는
  테스트를 추가/갱신한다.
