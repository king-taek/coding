# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---
---

> 위 **일반 코딩 지침**은 [multica-ai/andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills)
> 의 `CLAUDE.md` 원문(MIT)을 **수정 없이** 그대로 옮긴 것입니다. 아래는 이 저장소
> **고유의 규칙**으로, 위 지침의 "Merge with project-specific instructions as needed"
> 에 해당하는 프로젝트별 세부 사항입니다.

# CLAUDE.md — 이 저장소에서 작업할 때 지켜야 할 규칙

> 세션이 바뀌어도 일관성을 유지하기 위한 **자동 참조 규칙**입니다. 작업 전 한 번 읽고,
> 아래 규칙과 충돌하는 변경은 하지 마세요. (Claude Code 가 세션 시작 시 자동으로 읽습니다.)

## 프로젝트 한 줄 요약
Intel CPU·GPU 를 쓰는 AOI(반도체 광학검사) 이미지 **매칭 검증** 데스크톱 앱
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
- 업데이트는 **앱 구동에 필요한 것을 전부** 받아 **새 트리를 통째로 만들어 교체**한다
  (덮어쓰기 미러링이 아니다 — 그래야 상류에서 지운 파일이 실제로 사라진다).
  - 제외: 개발 전용·대용량 데이터(`dev/` 등), VCS/캐시(`.git`·`.pytest_cache`·`__pycache__`),
    무거운 `python/` 런타임. 목록은 `updater._UPDATE_SKIP_TOP` 에서 관리한다.
  - 새 최상위 폴더/파일이 **구동에 필요하면 자동 포함**된다(별도 작업 불필요). 구동에 불필요한
    대용량/개발 전용이면 `_UPDATE_SKIP_TOP` 에 추가한다.
  - ⚠ **`docs/`·`scripts/` 두 폴더만은 반대다** — `_UPDATE_KEEP_ONLY` 에 적은 것만 나간다
    (docs 는 사용자 설명서 3개, scripts 는 사용자용 bat 3개). 이 둘은 개발이 진행될수록
    파일이 느는데 대부분이 개발 산출물이라, '빼는 목록' 으로 두면 **새로 추가한 문서·빌드
    도구가 조용히 사용자에게 나간다**(실제로 개발 기록 14개·좌표 샘플 36개·빌드 스크립트
    20개가 나가고 있었다). 여기에 사용자용 파일을 새로 추가했다면 `_UPDATE_KEEP_ONLY` 에도
    적어야 전달된다 — 안 적으면 조용히 빠진다.
  - 트리 **깊은 곳**의 개별 제외는 `_UPDATE_SKIP_PATHS`(저장소 기준 상대경로)로 한다.
    지금은 미등록 글꼴 6개(2.8 MB)가 여기 있다 — 저장소에는 두고 배포에서만 뺀다.
  - **설명서 PDF 는 자동 업데이트가 유일한 전달 경로다.** 빌드는 `docs/` 를 담지 않는다
    (`portable_build` 는 `aoi_verification`·`main.py`·`requirements.txt`·`양식.xlsx`·IR 만).
    `docs/사용설명서.pdf`·`docs/상세설명서.pdf` 를 `_UPDATE_KEEP_ONLY` 에서 빼면 사용자에게
    설명서가 영영 가지 않는다. 회귀 가드: `dev/tests/test_update_payload.py`.
- **깨지 않아야 할 불변식 3개** (각각 실제 사고에서 나왔다 → `docs/규칙_배경.md`):
  - **런처 exe(`scripts/exe_launcher.py`)에는 앱 코드를 한 줄도 넣지 않는다.** PyInstaller 의
    `FrozenImporter` 가 exe 안 사본으로 디스크의 최신 사본을 **가린다**. `exe_launcher.spec` 의
    `hiddenimports` 는 비어 있고, `aoi_verification` 은 `excludes` 에 있고, `Analysis(pathex=[])`
    로 저장소 루트를 주지 않는다. `build.py` 의 verify 가 재확인한다.
  - **`app.new` 는 완성된 트리일 때만 존재한다.** 만드는 중에는 `app.new.part` 이고, 검증을
    통과한 뒤 rename 한다 — 그 rename 이 곧 '준비 완료' 신호다(별도 marker 파일을 쓰지 마라).
  - **`_write_version` 은 스테이징 트리에 쓴다**(`_write_version_to`). 살아있는 `app/VERSION` 에
    미리 쓰면 교체가 보류·실패했을 때 앱이 '최신입니다' 라며 구버전을 영원히 실행한다.
- **의존성 패키지**는 모드별로 동작이 다르다:
  - **exe 배포**: 새 패키지가 필요하면 동봉된 파이썬에 **직접 설치**한다(`_ensure_deps`).
    **설치가 성공해야만 코드를 적용**한다 — 코드만 새것이 되면 `ImportError` 로 앱이 안 켜진다.
    실패하면 적용하지 않고(`deps_blocked()`) 구버전을 유지한 채 안내한다.
    · 재설치 폭주를 막는 장치: 빌드가 남기는 `.deps_installed` 표식과 비교해 **바뀐 경우에만**
      돌린다. 주석·빈 줄 변경은 `bootstrap.req_lines` 가 정규화해 무시한다.
    · `--upgrade` 를 주지 마라(`pip_install_cmd(..., upgrade=False)`). requirements 가 전부
      `>=` 라 잘 돌던 무거운 패키지까지 최신으로 끌어올려 대용량을 받고 회귀 위험을 만든다.
  - 포터블/온라인: `requirements.txt` 변경을 감지(`deps_changed()`)해 '수동 갱신' 안내만 한다.
  - 자세한 동작은 `docs/업데이트_동작.md`.
- **테스트는 절대 진짜 `pip` 을 돌리지 않는다.** `dev/tests/test_updater.py` 의
  `_no_real_subprocess` 오토유즈 픽스처가 `subprocess.call` 을 막는다. pip 을 검증하는
  테스트만 `_fake_pip` 로 덮어쓴다.
- **lite 배포(`build.py exe-lite`)의 불변식**: 라이브러리를 번들에 넣지 않고 사용자 PC 의
  첫 실행 때 받는다. **`.deps_installed` 표식이 없다는 사실 자체가 유일한 '설치하라' 신호**다
  (`bootstrap.ensure_deps`). 빌드가 표식을 미리 찍으면 사용자 PC 가 설치를 건너뛰고
  ImportError 로 죽는다 — `verify_checks(lite=True)` 가 표식 **부재**를 검사한다.
  · 설치 자체는 **앱 코드**가 한다. 런처(`scripts/exe_launcher.py`)는 표식 유무로
    `python.exe`(콘솔 보임)/`pythonw.exe` 만 고른다 — 네트워크·pip 는 업데이트되지 않는
    런처에 넣지 않는다는 그 파일의 계약을 지킨다.
  · IR 은 lite 에서도 **동봉**한다. 저장소에 없어서 나중에 받아올 수 없다.
- 자동 적용은 **포터블·exe 빌드에서만**(개발/git 작업트리는 `is_git_checkout` 으로 차단).
  exe 빌드에서는 앱이 스테이징만 하고, **다음 실행 때 런처가 교체**한다(실행 중인 앱은 자기가
  돌아가는 폴더를 안전하게 바꿀 수 없다). 런처의 모든 실패 경로는 '구버전 그대로 실행' 이다.

### ⚠️ `requirements.txt` 를 바꿀 때 — 반드시 강조해서 알릴 것
패키지 변경은 **업데이트가 통째로 실패할 수 있는 유일한 지점**이다. 앱이 설치를 시도하지만
그 순간 인터넷/프록시가 막히면 업데이트가 적용되지 않는다. 그래서 `requirements.txt` 를
조금이라도 바꿨다면(패키지 추가·제거·버전 변경) 작업 요약에 **반드시** 아래를 눈에 띄게 적는다.

> **[중요] 이번 변경은 새 패키지가 필요합니다.**
> 사용자 PC 가 인터넷에 닿으면 앱이 자동으로 설치합니다. 설치가 실패하면 업데이트가
> 적용되지 않으므로, 그때는
> **`dist/AOI_Verify` 폴더 전체를 다시 빌드해 zip 으로 전달**해야 합니다
> (`python scripts/build.py exe`). **`app` 폴더만 전달하면 앱이 실행되지 않습니다.**

주석·빈 줄만 바꾼 경우는 해당 없다(`bootstrap.req_lines` 가 정규화해 무시한다).
테스트 전용 패키지는 여기가 아니라 `dev/requirements-dev.txt` 에 넣는다(배포·업데이트와 무관).

> 사내망 PyPI 차단은 **전언이었고 실측은 열려 있었다** → `docs/규칙_배경.md`.
> 검증되지 않은 전언을 설계 전제로 삼지 마라.

## 백본 모델(IR) 규칙 — torch 는 배포본에 없다
- 추론은 전부 OpenVINO 다. **변환 결과(IR)만 저장소 `runtime/ir/` 에 커밋해 두고** 앱은
  읽기만 한다. 런타임에 변환 수단은 없다.
- **조회 규칙은 하나**: `paths.bundled_ir_dir()` = `resource_path("runtime/ir")`.
  개발·포터블·exe·온라인 네 배포가 같은 자리를 본다. 설치 루트 기반 판정
  (`_exe_install_root`)을 여기 끌어다 쓰지 마라 — 온라인 배포가 못 찾는다.
- **백본을 바꾸면 `python scripts/internal/make_ir.py` 를 돌려 다시 굽고 커밋한다.**
  `portable_build.IR_MODELS` 와 이름이 같아야 한다. 앱 코드에만 추가하면 사용자 PC 에는
  IR 이 없어 그 유닛이 조용히 CPU 폴백으로 떨어진다.
- 빌드는 커밋된 IR 을 **복사만** 한다(`_place_ir`). 커밋 전이면 그 자리에서 변환하지만,
  **온라인 배포(`build.py online`)는 아무것도 동봉하지 않으므로 커밋을 요구**한다.
- `ov.save_model` 의 **`compress_to_fp16` 기본값은 True** 다. 반드시 `False` 를 준다 —
  FP16 반올림은 임베딩 값을 바꾼다("정확도는 절대 깨지 않는다"). 변환 전 `.eval()` 도 필수
  (BatchNorm 폴딩). 회귀 가드: `dev/tests/test_ir_bundle.py`.
- IR 은 모델당 1개면 된다. 배치·해상도는 `_force_static_shape` 의 `reshape` 로 맞춘다.
- **임베딩 산출 방식을 바꾸면 `embedder_openvino._EMB_VERSION` 을 올린다.** 캐시 키
  (`_emb_signature`)에 그 토큰이 없으면 옛 `.npy` 가 적중해 옛 벡터와 새 벡터를 비교한다 —
  느려지는 게 아니라 **매칭이 틀린다** → `docs/규칙_배경.md`.
- torch 는 **빌드 전용**이다(`_BUILD_ONLY_REQS`). 임시 폴더(`.irbuild`)에만 설치하고 끝나면
  지운다. `requirements.txt` 에 다시 넣지 마라 — 배포 번들에서 가장 큰 덩어리다.
- **OpenVINO 심볼은 위치가 버전마다 바뀐다 — `try: import ... except: 폴백` 을 그냥 두지 마라.**
  **최신 위치를 먼저 보고 옛 위치를 폴백**하며, 둘 다 없으면 **경고를 남긴다**
  (`embedder_openvino._async_infer_queue_cls`). 조용한 폴백이 비동기 추론을 죽인 적이 있다
  (→ `docs/규칙_배경.md`). 회귀 가드 `dev/tests/test_async_infer_queue.py` 는 심볼 존재만
  보지 않고 **실제로 비동기 경로를 태운다**.

## 매칭 / 좌표 검토 규칙
- **정확도 우선**: 지금 운영 조합(고효율 모드 = GPU MobileNetV3 임베딩으로 후보 추림 +
  CPU 고전 ORB·중앙가중 재채점)보다 정확도가 낮으면 더 빨라도 채택/추천하지 않는다.
  실측 회귀 가드: `dev/tests/test_efficiency_contract.py` 가 (모델·장치·배치)와 임베딩
  캐시 키를 못 박아 둔다.
- ⚠ **좌표 계산을 고칠 때의 절대 기준은 골든 <u>두 클래스 모두</u>다**(`test_wafer_geometry.py`).
  합격선: **`col`·`row` 는 오차 0 으로 정확히 일치**, `x`·`y` 는 ±100 µm 까지 인정.
  `col`/`row` 가 하나라도 어긋나면 **그 수정은 틀린 것**이다 — die 인덱스는 장비 화면
  표기이자 매칭 버킷 키라 1 만 어긋나도 다른 die 가 된다. **기대값을 고쳐서 통과시키지 마라.**
  - `TestGoldenEquipmentExamples` — 실측 4사례(3 device). ⚠ **이건 폴백 경로만 태운다**
    (그 사례들엔 `Center_*` 가 안 딸려 와 픽스처가 중심 없는 INI 를 합성한다).
    4사례가 전부 '폴백이 우연히 맞는 자재' 라서 통과하는 것이고,
    `test_golden_actually_runs_the_fallback_path` 가 그 사실 자체를 단언한다.
  - `TestDerivedPathGolden` — **앱이 실제로 타는 주경로**(중심 → 원점) 6사례.
    경쟁 가설 배제 · 폴백이 PI2 에서 틀리다는 것 · 경계 근접 표본이 0건이라는 것까지
    함께 못 박는다. **하나만 통과했다고 안심하지 마라.**
- ⚠ **좌표 판단은 13차까지 오면서 같은 구조의 오류를 반복했다** → `docs/좌표_판단오류_회고.md`.
  값을 채택하기 전에 그 문서의 R1~R7 을 적용한다. 특히:
  **R1** 경쟁 가설을 하나 이상 세우고 표본이 둘을 구분하는지 계산한다(구분 못 해도 채택은
  가능 — "미구분" 을 코드·문서·요청목록에 적으면 된다). **R3** 모든 수치에 출처 등급
  (`관측`/`파일`/`유도`/`가정`/`합성`)을 붙인다. **R4** 합성값을 골든에 넣지 않는다.
- **die 인덱스는 좌표에서 유도한다** — `floor(X/DieStep_X)`, `floor(Y/DieStep_Y)`.
  `ColorImageGrabingInfo.ini` 의 `Col`/`Row` 필드를 변환에 쓰지 마라.
  **레시피마다 `Row` 원점이 다를 수 있다**(실측 15호기: 같은 결함을 두 레시피가 찍으면
  `Col` 은 같은데 `Row` 만 1 어긋난다 → 그 필드를 쓰면 die 내부 y 가 −15349 같은 음수).
  그 필드는 pitch 검산의 참조값으로만 쓴다 — 검산도 **X 는 등호, Y 는 레시피별 상수**다
  (`wafer_geometry._grid_check`). **X 등호는 완화하지 마라** — 항목이 1건인 폴더에서
  '상수' 는 공허하게 참이 돼 다른 자재에 TB500 상수가 채택된다(→ `docs/규칙_배경.md`).
- **LIVE 파일명(`camtek_live`)은 정규식이 아니라 `_` 토큰으로 읽는다.** 배치가 셋이라
  (`x_y_이름` · `이름_x_y` · `이름_x_y_크기_면적`) 정규식으로는 서로 잡아먹는다 —
  실제로 greedy `.+`+`$` 가 **맨 뒤 두 수치(DYSize·DArea)를 x·y 로** 집어 30,212 µm
  오차를 냈다(R-0, §6-O). 불변식 셋을 깨지 마라:
  - `col`/`row` = **처음 등장하는 연속한 두 정수 토큰**. `x`/`y` = 그 **뒤에서 처음** 두
    수치 토큰. 남는 수치는 크기·면적이며 **좌표로 쓰지 않는다**(`LiveName.extra`).
  - `col`/`row` 앞에 **식별자 토큰이 2개 이상**이어야 한다 — KLA 이미지 파일명
    (`{WaferID}_2_0_23_2`)은 앞이 1개뿐이라 이 규칙으로 걸러진다.
  - **정수 토큰 판정에 `-?` 를 유지한다.** 빼면 `00MEU018XYG1_-1_4_23_1` 에서 쌍이
    `(4,23)` 으로 밀려 KLA 파일명이 `col=4,row=23` 으로 오인된다.
  - ⚠ 크기·면적 배치는 **실물 미확보**(근거는 참고 저장소 주석뿐). 순서가 반대인
    경쟁 가설을 표본이 구분하지 못한다 — 테스트에 그 사실이 적혀 있으니 지우지 마라.
- **LIVE 파일명의 col/row 토큰은 보정하지 않는다** — `camtek_ini` 와 같은
  규약이다. 예전에 row 를 −1 하던 코드는 되돌려진 변경(`CAMTEK_ROW_TOTAL` 7→6)의
  잔재였고, INI↔LIVE 가 1 어긋난 채 ±1 게이트에 가려져 있다가 실측에서 터졌다.
  **±1 게이트의 여유는 장비 간 규약 차이용 예산이지 우리 소스 간 불일치를 덮는 용도가
  아니다** — 소스 간에는 차이 0 으로 맞추고 테스트로 못 박는다(→ `docs/규칙_배경.md`).
- **한 매칭 실행 안에서 Camtek 좌표 프레임은 하나여야 한다**(`coords.resolve_batch`).
  pitch 검산은 웨이퍼 폴더마다 독립이라 ref 는 통과하고 val 은 실패할 수 있는데, 그러면
  row 규약이 달라(`row_total−y_index` vs `−Row`) `(col,row) ±1` 게이트가 절대 안 맞아
  **그 슬롯이 통째로 '매치 실패'** 가 된다. 섞이면 전부 절대좌표로 내린다.
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
- KLA 폴더의 slot명(WaferID)은 **정보파일이 1순위, OCR 이 폴백**이다. 정보파일은 폴더 안의
  비-사진 파일(`.001` 이거나 **확장자가 아예 없을 수 있음**, 1~2개)이고 헤더에
  `WaferID "XXXX";` 가 명시돼 있다 — `coords.kla_info.read_wafer_id` 가 후보를 전부 훑는다.
  **사진 파일명 prefix 로 WaferID 를 추측하지 마라**(되돌린 방식 → `docs/규칙_배경.md`).
  정보파일은 사진과 무관하므로 **사진 0장 폴더도 식별**된다 — 그래서 `drop_empty_unmatched` 는
  KLA 해석 **뒤**(`_after_slot_resolved`)에 돌고, 짝은 찾았지만 한쪽 사진이 0장인 슬롯은
  `push_one_sided_to_unmatched` 로 '기준/검증 전용' 에 되돌려 결과에 남긴다
  (그냥 두면 결과에서 통째로 사라진다).

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
  `update_app.bat`·`build.py`·`make_release_zip.py`·`launcher.py`·`exe_launcher.py`).
  빌드/내부 도구는 `scripts/internal/`
  (`*.spec`·`build_exe.bat`·`build_online.bat`·`make_portable.bat`·`portable_build.py`·
  `verify_no_forbidden.py`). 경로를 바꾸면 이를 호출하는 곳(run_this_before·빌드 bat·README·문서)도
  함께 고친다.
- **`aoi_verification/app/`** 가 앱 본체: `ui/`(pages·widgets), `workers/`(매칭·OCR·내보내기 등
  백그라운드), `coords/`(좌표 파서), `models/`, `similarity/`, `utils/`(`updater`·`paths`·`image_io`),
  `i18n/`, `learning/`.
- **`dev/` = 사용자가 직접 건드리지 않는 개발 전용 모음.** `dev/tests/`(테스트)·
  `dev/requirements-dev.txt`(테스트 도구)·`dev/양식.xlsx`(엑셀 출력 템플릿).
  옮길 때 함께 고칠 참조:
  - 테스트 경로: `pytest.ini` 의 `testpaths = dev/tests` (※ `pytest.ini` 는 루트 앵커라 이동 금지 —
    `python -m pytest` 가 루트에서 testpaths 로 찾는다).
  - `dev/tests/conftest.py`·`dev/tests/test_no_spyder_conda.py` 는 루트를 `parents[2]` 로 잡는다.
  - 양식 템플릿: `paths.template_path()` 가 `dev/양식.xlsx` 를 1순위로 찾는다. 포터블 빌드는
    `portable_build.py`/`*.spec`/`updater` 가 `dev/양식.xlsx` → 앱 루트 `양식.xlsx` 로 복사한다.
  - 자동 업데이트: `updater._UPDATE_SKIP_TOP` 가 `dev/` 를 통째로 건너뛰되 `dev/양식.xlsx` 만 앱
    루트로 따로 복사한다(구동 필수). `dev/` 에 새 개발 데이터를 넣어도 자동으로 제외된다.
- **루트 앵커(이동 금지)**: `README.md`·`main.py`·`requirements.txt`·`pytest.ini`·`.gitignore`·
  `.vscode/`(VS Code 가 `${workspaceFolder}/.vscode/` 만 읽음)·`CLAUDE.md`·`aoi_verification/`
  (`main.py` 가 `from aoi_verification …` 로 import — 루트에 있어야 함).
- **단일 리소스 파일**은 그 파일 하나만을 위한 폴더를 새로 만들지 않는다.

## 사진 파일 정책 — 저장소에 이미지를 쌓지 않는다
저장소에 남기는 것은 **파일명뿐**이다.  실제 사진은 지우고 이름만 그 폴더의
`사진목록.txt` 에 한 줄씩 남긴다(주석은 `#`).  앱 아이콘·로고(`ui/assets/`)만 예외다 —
구동에 필요해서 남긴다.

- **결함 사진(좌표 확인용 샘플)** — `dev/좌표 확인/`, `docs/…좌표 예시…/`,
  `docs/RDL4 LOT files…/`.  좌표는 **파일명과 옆의 INI/`.001` 에만** 있고 픽셀은 쓰지
  않는다.  파서(`camtek_ini`·`camtek_live`·`kla_info`)의 `resolve` 는 경로만 받아
  `stem` 으로 조회하므로 사진 실물이 없어도 그대로 돈다.  ⚠ **이름을 고치면 좌표
  해석이 달라진다** — 원본 그대로 유지할 것.  골든 테스트는 폴더를 glob 하지 말고
  `사진목록.txt` 를 읽는다(`test_wafer_geometry._photo_names`).
- **설명서 화면 캡처** — `dev/사용설명서_자료/*.png`·`*.jpg`.  PDF 를 굽고 나면
  `make_manual_pdf.py` 가 **자동으로 치운다**(`sweep_captures`).  PDF 는 자기완결적이라
  그림이 이미 안에 박혀 있고, 캡처는 `capture_manual_shots.py` 가 같은 이름으로 다시
  찍어 주는 재생성물이다.  그래서 설명서를 고칠 때는 **늘 캡처 스크립트부터** 돌린다
  (건너뛰고 인쇄만 하면 `_check_images` 가 막는다).  `shots.json`·HTML·CSS 는 캡처가
  아니라 **원본**이므로 지우지 않는다.  회귀 가드: `dev/tests/test_manual_pdf_sweep.py`.
- 새로 사진이 필요해지면 같은 규칙을 따른다 — 커밋 전에 지우고 이름만 남긴다.
  `Thumbs.db` 처럼 **썸네일이 박혀 있는 부산물도 사진으로 친다**(실제로 지운 결함 사진
  7장이 그 안에 남아 있었다).

## 보안 가드 (회사 정책)
- 회사에서 **금지한 외부 패키지 매니저/IDE 계열 도구**를 코드·문서·의존성에 도입하지 않는다
  (구체 목록·패턴은 `scripts/internal/verify_no_forbidden.py` 와 테스트
  `dev/tests/test_no_spyder_conda.py` 가 보유). 금지 키워드를 문서에 적기만 해도 가드가 실패하니,
  이름을 직접 쓰지 말고 가드를 참조한다.
- 커밋 전 `python scripts/internal/verify_no_forbidden.py` 가 통과해야 한다(`run_this_before.py` 도 실행).

## 테스트
- 테스트 도구 설치(1회): `python -m pip install -r dev/requirements-dev.txt`.
- 커밋 전 전체 통과 확인: **`QT_QPA_PLATFORM=offscreen python -m pytest -q -n auto`** (약 21초).
  `-n auto` 는 파일 단위 병렬(pytest-xdist). 빼면 같은 스위트가 2분 가까이 걸린다.
- 작업 중 빠른 확인: `-m "not ui"` 를 더하면 Qt 테스트를 빼고 순수 로직만 (약 10초).
  `ui` 마커는 conftest 가 `qapp`·`styled_qapp` 사용 여부로 **자동 부여**한다(파일에 안 적는다).
- **Qt 테마는 세션당 1회만 적용한다.** 모듈마다 `theme.apply_to_app` 을 부르는 `qapp` 픽스처를
  새로 만들지 말고 conftest 의 **`styled_qapp`** 을 받아 쓴다 — `setStyleSheet` 은 부를수록
  비싸져서, 21회 호출이 스위트 시간의 절반(82초)을 먹은 적이 있다(→ `docs/규칙_배경.md`).
  QSS **문자열만** 검사하는 테스트는 `theme.render_qss` 로 렌더해 문자열을 직접 본다
  (앱에 적용할 필요가 없다 — 참조: `test_sheet_size_and_edge`).
- 무거운 의존성(cv2/openvino/torch/PyQt6)은 환경에 없을 수 있어 `pytest.importorskip` 으로
  게이트한다(모듈 단위 import 도 포함). **순수 로직은 무거운 의존성 없이** 단위 테스트되게 설계
  (예: 좌표 후보 선택 `_select_coord_candidates`, 업데이트 브랜치 정규화/자기교정).
- UI 동작은 `QT_QPA_PLATFORM=offscreen` + `pytest.importorskip("PyQt6.QtWidgets")` 로 헤드리스
  검증한다(참조: `dev/tests/test_match_review_clamp.py`·`test_slot_select_dialog.py`).
- 동작/리소스(로딩바·자동 업데이트·매칭/좌표 규칙·레시피 배선·UI 토글)를 바꾸면 그에 대응하는
  테스트를 추가/갱신한다.
