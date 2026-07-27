# exe 빌드·배포·설치 가이드

> 파이썬이 없는 사용자에게 AOI 검증 앱을 전달하는 방법입니다. 배포 방식 3가지와,
> 각 방식의 **빌드(전달하는 사람)** · **설치/실행(받는 사람)** 절차를 정리합니다.
> (자동 업데이트 동작은 `docs/업데이트_동작.md` 참고.)

## 0. 어떤 방식을 고를까

| 방식 | 산출물 | 용량 | 인터넷 | 추천 상황 |
|---|---|---|---|---|
| **C. exe + app 폴더** | `dist\AOI_Verify\` 폴더 | ~1.5GB | 불필요 | **기본 권장** — 파이썬 미설치 PC, 자동 업데이트 완전 동작 |
| **B. 포터블 폴더** | `dist_portable\` 폴더 | ~1.3~2.0GB | 불필요 | exe 가 회사 백신에 막힐 때(`.bat` 로 실행) |
| **A. 온라인 launcher exe** | `AOI_Verify_Online.exe` 1개 | 수십 MB | **첫 실행 시 필요** | ⚠️ **사내망에서는 사용 불가** — 첫 실행에 PyPI 접속이 필요한데 막혀 있다 |

- **공통 전제(빌드하는 PC)**: Windows + 파이썬 3.9+ 설치 + 인터넷. 빌드는 저장소 루트에서 실행.
- **자동 업데이트는 B·C 에서 동작**한다. 상세는 4절.
- **설치 경로는 짧게** 잡는 것을 권한다(예: `C:\AOI_Verify`). 한글 이름이 섞인 깊은 경로는
  Windows 의 260자 제한에 걸릴 수 있다.

> **왜 '앱 코드를 exe 안에 넣지 않는가'** — 예전 단독 exe 는 앱 전체를 exe 안에 얼려서 넣었다.
> 그러면 자동 업데이트가 새 `.py` 를 디스크에 잘 써 넣어도 **실행 중인 파이썬은 exe 안의 옛
> 코드를 읽는다**. 그래서 "업데이트가 적용되었습니다" 라고 하고도 바뀌지 않거나, 새로 추가된
> 모듈만 반영돼 **새 코드가 옛 코드를 호출**하는 상태가 됐다. 지금 구조는 exe 에 앱 코드가
> 한 줄도 없고, 바뀌는 것은 전부 `app\` 폴더에 파일로 놓여 있다.

---

## A. 온라인 launcher exe (권장)

작은 exe 하나만 전달하면 되는 방식. exe 에는 앱·무거운 의존성(torch·openvino)이 **없고**,
사용자가 처음 실행할 때 인터넷에서 앱과 패키지를 받아 `%LOCALAPPDATA%\AOI Recipe Verification` 에 설치한다.

### A-1. 빌드 (전달하는 사람, 1회)
```powershell
REM 저장소 루트에서 (둘 중 편한 것)
python scripts\build.py online
REM 또는: scripts\internal\build_online.bat (위 명령을 부르는 얇은 래퍼)
```
> VS Code 라면 `scripts/build.py` 를 열고 **▶ Run Python File** 을 눌러도 됩니다 —
> 인자 없이 실행되면 빌드 종류(online/portable/windows)를 번호로 고르는 메뉴가 뜹니다.
- 산출물: **`dist\AOI_Verify_Online.exe`** — 이 파일 **하나만** 배포(메일·USB·공유 폴더 등).
- 빌드가 하는 일: 가상환경 준비 → PyInstaller 설치 → 보안 가드 통과 확인 →
  `scripts\internal\online.spec` 으로 작은 onefile exe 생성.

### A-2. 설치/실행 (받는 사람)
1. 받은 `AOI_Verify_Online.exe` 를 원하는 위치에 두고 **더블클릭**.
2. **첫 실행만** 인터넷으로 앱과 패키지를 내려받아 설치한다(수백 MB — 수 분 걸릴 수 있음).
   진행 메시지가 표시되고, 끝나면 앱 창이 뜬다.
3. 두 번째 실행부터는 이미 설치된 것을 바로 써서 빠르게 켜진다.
4. 설치 위치: `%LOCALAPPDATA%\AOI Recipe Verification` (예: `C:\Users\<사용자>\AppData\Local\AOI Recipe Verification`).
   지우고 싶으면 이 폴더를 삭제하면 처음 상태로 돌아간다.

### A-3. 주의
- **첫 실행에 인터넷이 필요**하다. 인터넷이 막힌 폐쇄망이면 첫 설치가 안 되므로 **B(포터블)** 를 쓴다.
- 회사 SSL 검사(인터셉트) 프록시 환경이라도 앱은 OS 신뢰 인증서를 쓰도록 돼 있어 보통 동작한다
  (그래도 막히면 B/C 로 전달).
- 미서명 exe 라 SmartScreen/Defender 가 “알 수 없는 게시자” 경고를 띄울 수 있다 →
  *추가 정보 → 실행*. (경고 제거가 필요하면 코드 서명(EV) 권장.)

---

## B. 포터블 폴더 (인터넷 없는 PC)

자체 포함 CPython 런타임 + 앱 + 모든 의존성을 폴더로 묶어 전달. 인터넷 없이 더블클릭 실행.

### B-1. 빌드 (전달하는 사람, 1회)
```powershell
python scripts\build.py portable
REM 또는: scripts\internal\make_portable.bat
```
- 산출물 `dist_portable\` 구조:
  ```
  dist_portable\
    python\          ← 자체 포함 CPython + 의존성(torch/openvino 등) [무거움, 거의 불변]
    app\             ← aoi_verification 소스 + main.py + 양식.xlsx        [업데이트 대상]
    run_aoi.bat      ← 콘솔 없이 GUI 실행
    run_aoi_debug.bat← 오류 진단용(콘솔 + traceback)
    update_app.bat   ← app\ 소스만 교체
  ```
- 배포: `dist_portable` 폴더 전체를 zip 으로 묶어 전달(약 0.6~1.0GB zip).

### B-2. 설치/실행 (받는 사람)
1. 받은 zip 을 원하는 위치에 **압축 해제**.
2. 폴더 안의 **`run_aoi.bat` 더블클릭**(파이썬 설치 불필요).
3. 앱이 안 켜지면 `run_aoi_debug.bat` 로 콘솔의 오류 메시지를 확인.

---

## C. exe + app 폴더 (권장)

얇은 런처 exe 하나와, 업데이트되는 것들을 담은 `app\` 폴더를 함께 전달한다.
**exe 에는 앱 코드가 들어 있지 않다** — 그래서 자동 업데이트가 온전히 동작한다.

### C-1. 빌드 (전달하는 사람, 1회)
```powershell
python scripts\build.py exe
REM 또는: scripts\internal\build_exe.bat
```
- 산출물: **`dist\AOI_Verify\`** 폴더(통째로 zip 배포, ~1.5GB).
  ```
  AOI_Verify\
    AOI_Verify.exe     ← 얇은 런처(수 MB). 앱 코드 0줄, 업데이트되지 않는다.
    python\            ← 번들 CPython + 모든 의존성 (무겁고 거의 안 바뀜)
    runtime\torch\     ← 모델 가중치(동봉) — 사내망에서 다운로드가 막혀도 매칭이 된다
    app\               ← ★ 자동 업데이트가 바꾸는 전부 (앱 코드·리소스·양식.xlsx·VERSION)
    결과\               ← 엑셀 출력 (app\ 바깥이라 업데이트에 안 쓸린다)
    run_aoi.bat        ← exe 가 백신에 막힐 때의 대체 실행
    run_aoi_debug.bat  ← 오류 진단용(콘솔 + traceback)
  ```
- 빌드가 하는 일: 런처 exe 를 얼리고(`scripts\internal\exe_launcher.spec`) → 번들 CPython 에
  의존성 설치 → 모델 가중치 동봉 → 앱 소스 복사 → `VERSION` 스탬프 → **산출물 검증**.
- 검증은 특히 **`_internal\` 폴더가 없는지**(=앱이 exe 안에 얼려지지 않았는지)와, 번들
  파이썬으로 **실제 앱 import 가 되는지**를 확인한다.

### C-1-b. 배포용 zip 만들기
```powershell
python scripts\make_release_zip.py
```
- 산출물: **`dist\AOI_Verify_<날짜>_<커밋7자리>.zip`** — **이 파일 하나만 전달**하면 된다.
  (이름에 날짜·커밋이 박혀 있어 누구에게 어느 버전을 줬는지 나중에 추적할 수 있다.)
- 하는 일: 산출물을 **다시 검증** → `설치방법.txt` 작성 → zip.
  **검증을 통과하지 못하면 zip 을 만들지 않는다** — 깨진 배포본(특히 앱이 exe 안에 얼려
  들어간 것)을 사용자에게 보내는 사고를 여기서 막는다.
- 제외되는 것: 업데이트 찌꺼기(`app.new`·`app.old`), 빌드 머신에서 시험 실행한 `결과\`,
  `app\` 안의 `__pycache__`.
- 압축을 풀면 `AOI_Verify\` 폴더 하나가 생기고, 그 안에 `설치방법.txt` 가 들어 있다.

### C-2. 설치/실행 (받는 사람)
1. 받은 zip 을 **짧은 경로**에 압축 해제(예: `C:\AOI_Verify`).
2. `AOI_Verify\AOI_Verify.exe` **더블클릭**(파이썬 설치 불필요).
3. 첫 실행 경고(미서명) 시 *추가 정보 → 실행*.
4. 앱이 안 켜지면 `run_aoi_debug.bat` 로 콘솔의 오류 메시지를 확인.

> 참고: PyInstaller 부트로더가 회사 백신/보안 정책에 막히는 경우가 있다. 그럴 땐 **B(포터블)**
> 를 쓴다(같은 폴더 구조에서 `run_aoi.bat` 으로 실행). 참고로 이 exe 는 **작고 다시 빌드해도
> 잘 바뀌지 않으므로**, 백신 예외 등록이 업데이트마다 무효화되지 않는다.

### C-3. 기존 '단독 exe' 사용자 이관
옛 배포본은 앱이 exe 안에 얼려 있어 **제자리 이관이 불가능하다.** 새 zip 을 받아야 한다.

1. 새 zip 을 **다른 폴더**에 압축 해제(예: `C:\AOI_Verify`).
2. **옛 결과 파일 회수** — 옛 결과는 `…\AOI_Verify\_internal\결과\` 에 있다.
   이 폴더 내용을 새 폴더의 `결과\` 로 옮긴다. ← 안 옮기면 잃는다.
3. 설정·캐시는 옮길 것이 없다(`%USERPROFILE%\.aoi_verification_cache` 에 그대로 있어 이어진다).
4. 옛 `AOI_Verify\` 폴더를 삭제한다.

---

## 4. 자동 업데이트

- 바뀌는 것은 전부 **`app\` 폴더**에 파일로 있다(C·B 공통). exe/런타임/가중치는 그대로 두고
  이 폴더만 갱신하므로 — **평소에는 폴더를 다시 배포할 필요가 없다.**
- 동작: 앱 시작 시 GitHub 브랜치 HEAD 와 동봉된 `VERSION` 을 비교해 새 버전이 있으면 받아
  **새 트리를 통째로 만들고, 검증을 통과해야만 적용**한다(개발 전용 `dev/` 는 제외,
  `dev\양식.xlsx` 만 앱 루트로 복사). 상류에서 지운 파일은 사용자 폴더에서도 사라진다.
- **C(exe)**: 실행 중인 `app\` 을 건드리지 않고 `app.new\` 로 받아두기만 하며, **다음 실행 때
  런처가 교체**한다. 교체가 중간에 끊겨도 런처가 복구하고, **어떤 실패든 구버전이 살아남는다.**
- **B(포터블)**: 런처가 없으므로 제자리에 항목 단위로 적용한다(중간 실패 시 전부 되돌림).
- 실패하면 `VERSION` 도 바뀌지 않아 다음 실행에 다시 시도한다.
- `VERSION` 이 없는 배포본이라도 앱이 초기 파일을 만들어 **자동 업데이트가 항상 동작**한다
  (첫 회는 '현재 버전 미상' 안내 후 최신을 받고, 그 뒤로는 정상 비교).
- **의존성(requirements.txt) 변경**:
  - **C(exe)**: 새 패키지가 필요하면 동봉된 파이썬에 **직접 설치**하고, **설치가 성공해야만**
    코드를 적용한다. 실패하면 구버전을 유지한 채 '인터넷을 확인하고 다시 시도하거나, 새 배포본
    `AOI_Verify` 폴더 **전체**를 받으라' 고 안내한다. `app\` 만 바꾸면 앱이 깨진다.
  - **B(포터블)**: 안내만 한다 — 새 빌드를 한 번 더 배포해야 한다.
- 자세한 규칙·제외 목록은 `docs/업데이트_동작.md`.

---

## 5. 빌드/배포 파일 위치 요약

| 파일 | 역할 |
|---|---|
| `scripts\build.py` | 빌드 진입점(파이썬) — `exe`/`portable`/`online`/`verify` |
| `scripts\make_release_zip.py` | **C. 배포용 zip 생성** — 검증 + `설치방법.txt` 동봉 |
| `scripts\exe_launcher.py` | **C. 런처 진입점** — 대기 중 업데이트 교체 + 앱 실행. 앱 코드 0줄 |
| `scripts\internal\build_exe.bat` · `exe_launcher.spec` | C. exe + app 폴더 빌드 |
| `scripts\internal\portable_build.py` · `make_portable.bat` | B·C. 런타임·앱 배치(C 가 재사용) |
| `scripts\internal\build_online.bat` · `online.spec` | A. 온라인 launcher exe 빌드(.bat 은 build.py 래퍼) |
| `scripts\launcher.py` | A. exe 진입점(앱 다운로드+pip 설치+실행) |
| `aoi_verification\app\utils\bootstrap.py` | A. 부트스트랩 핵심 로직(데이터 폴더·의존성 판단) |
| `scripts\run_aoi.bat` · `run_aoi_debug.bat` | B/C. 사용자 실행·진단 |
| `scripts\update_app.bat` | B. 수동 갱신(병합식이라 C 에는 동봉하지 않는다) |

> 빌드·내부 도구는 `scripts\internal\` 에, 사용자가 직접 실행하는 것은 `scripts\` 에 둔다.
