# exe 빌드·배포·설치 가이드

> 파이썬이 없는 사용자에게 AOI 검증 앱을 전달하는 방법입니다. 배포 방식 네 가지와,
> 각 방식의 **빌드(전달하는 사람)** · **설치/실행(받는 사람)** 절차를 정리합니다.
> (자동 업데이트 동작은 `docs/업데이트_동작.md` 참고.)

## 0. 어떤 방식을 고를까

| 방식 | 산출물 | 용량 | 사용자 PC 인터넷 | 추천 상황 |
|---|---|---|---|---|
| **C. exe + app 폴더** | `dist\AOI_Verify\` 폴더 | 큼 | 불필요 | **기본 권장** — 파이썬 미설치 PC, 자동 업데이트 완전 동작 |
| **D. exe-lite** | `dist\AOI_Verify_Lite\` 폴더 | 훨씬 작음 | **첫 실행 시 필요** | 전달 파일을 줄이고 싶을 때. 내용은 C 와 같고 라이브러리만 첫 실행 때 받는다 |
| **B. 포터블 폴더** | `dist_portable\` 폴더 | 큼 | 불필요 | exe 가 회사 백신에 막힐 때(`.bat` 로 실행) |
| **A. 온라인 launcher exe** | `AOI_Verify_Online.exe` 1개 | **가장 작음** | **첫 실행 시 필요** | 전달 파일을 최소로. 첫 실행에 파이썬·앱·라이브러리를 **전부** 받는다 |

> **A 를 쓰기 전 전제**: 백본 IR 이 저장소에 커밋돼 있어야 한다(`runtime/ir/`).
> 이 방식은 아무것도 동봉하지 않으므로 IR 도 앱과 함께 내려받아야 하기 때문이다.
> 커밋돼 있지 않으면 `build.py online` 이 **빌드를 거부하고** 만드는 방법을 알려준다.
>
> 전달 파일 크기 순서: **A < D < C**.  사용자 PC 가 받는 양은 A·D 가 비슷하다
> (A 가 파이썬 런타임 수십 MB 를 더 받을 뿐이다).

- **공통 전제(빌드하는 PC)**: Windows + 파이썬 3.9+ 설치 + 인터넷. 빌드는 저장소 루트에서 실행.
- **자동 업데이트는 B·C·D 에서 동작**한다. 상세는 4절.
- **설치 경로는 짧게** 잡는 것을 권한다(예: `C:\AOI_Verify`). 한글 이름이 섞인 깊은 경로는
  Windows 의 260자 제한에 걸릴 수 있다.

> **왜 '앱 코드를 exe 안에 넣지 않는가'** — 예전 단독 exe 는 앱 전체를 exe 안에 얼려서 넣었다.
> 그러면 자동 업데이트가 새 `.py` 를 디스크에 잘 써 넣어도 **실행 중인 파이썬은 exe 안의 옛
> 코드를 읽는다**. 그래서 "업데이트가 적용되었습니다" 라고 하고도 바뀌지 않거나, 새로 추가된
> 모듈만 반영돼 **새 코드가 옛 코드를 호출**하는 상태가 됐다. 지금 구조는 exe 에 앱 코드가
> 한 줄도 없고, 바뀌는 것은 전부 `app\` 폴더에 파일로 놓여 있다.

---

## A. 온라인 launcher exe — exe 하나만 전달

작은 exe 하나만 전달하면 되는 방식. exe 에는 **아무것도 들어 있지 않고**, 사용자가 처음
실행할 때 인터넷에서 **자체 포함 파이썬 · 앱 · 라이브러리 · 백본 IR 을 전부** 받아
`%LOCALAPPDATA%\AOI Recipe Verification` 에 설치한다. **사용자 PC 에 파이썬이 필요 없다.**

### A-1. 빌드 (전달하는 사람, 1회)
```powershell
REM 저장소 루트에서 (둘 중 편한 것)
python scripts\build.py online
REM 또는: scripts\internal\build_online.bat (위 명령을 부르는 얇은 래퍼)
```
> VS Code 라면 `scripts/build.py` 를 열고 **▶ Run Python File** 을 눌러도 됩니다 —
> 인자 없이 실행되면 빌드 종류를 번호로 고르는 메뉴가 뜹니다.
- 산출물: **`dist\AOI_Verify_Online.exe`** — 이 파일 **하나만** 배포(메일·USB·공유 폴더 등).
- 빌드가 하는 일: **커밋된 IR 확인** → 가상환경 준비 → PyInstaller 설치 → 보안 가드 →
  `scripts\internal\online.spec` 으로 작은 onefile exe 생성.
- IR 이 저장소에 없으면 여기서 **중단**한다. 한 번만 만들어 커밋하면 된다:
  ```powershell
  pip install torch torchvision openvino
  python scripts\internal\make_ir.py
  git add runtime/ir && git commit -m "백본 IR 추가" && git push
  ```

### A-2. 설치/실행 (받는 사람)
1. 받은 `AOI_Verify_Online.exe` 를 원하는 위치에 두고 **더블클릭**.
2. **첫 실행만** 인터넷으로 파이썬·앱·라이브러리를 내려받아 설치한다(수백 MB — 수 분
   걸릴 수 있음). 검은 창에 진행이 표시되고, 끝나면 앱 창이 뜬다. **창을 닫지 말 것.**
3. 두 번째 실행부터는 이미 설치된 것을 바로 써서 빠르게 켜진다.
4. 설치 위치: `%LOCALAPPDATA%\AOI Recipe Verification` (예: `C:\Users\<사용자>\AppData\Local\AOI Recipe Verification`).
   지우고 싶으면 이 폴더를 삭제하면 처음 상태로 돌아간다.

### A-3. 주의
- **첫 실행에 인터넷이 필요**하다. 닿지 못하는 PC 에는 **C(전체 배포본)** 를 준다.
- 받는 곳은 두 군데다 — `github.com`(파이썬 런타임·앱)과 PyPI(라이브러리). 둘 다 열려
  있어야 한다.
- 첫 실행은 콘솔 창이 뜬 채 몇 분 걸린다(받는 양이 가장 많다). 두 번째부터는 바로 켜진다.
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
    python\          ← 자체 포함 CPython + 의존성(PyQt6/openvino 등) [무거움, 거의 불변]
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

> **빌드는 30분 이상 걸린다**(의존성 설치 + CPython 런타임 다운로드).
> 백본 IR 이 저장소에 커밋돼 있으면 복사만 하므로 변환 단계(torch 임시 설치)는 건너뛴다 —
> 아직이면 `python scripts\internal\make_ir.py` 로 한 번 만들어 커밋해 두는 것을 권한다.
> 화면 버퍼를 넘겨 원인을 놓치기 쉬우니 **로그를 파일로 남기는 것을 권한다**:
> ```powershell
> python scripts\build.py exe 2>&1 | Tee-Object -FilePath dist\build.log
> ```
> 실패하면 이 파일만 보내면 어느 단계에서 멈췄는지 알 수 있다.
> (로그를 `dist\AOI_Verify\` **안에는 두지 말 것** — 배포 zip 에 딸려 들어간다.)
>
> 옛 방식(단독 exe)으로 빌드한 적이 있어도 괜찮다 — 빌드가 시작할 때 옛 산출물
> (`_internal\` 등)을 자동으로 정리한다. 무거운 `python\`·`runtime\` 은 재사용한다.
- 산출물: **`dist\AOI_Verify\`** 폴더(통째로 zip 배포).
  ```
  AOI_Verify\
    AOI_Verify.exe     ← 얇은 런처(수 MB). 앱 코드 0줄, 업데이트되지 않는다.
    python\            ← 번들 CPython + 모든 의존성 (무겁고 거의 안 바뀜)
    app\               ← ★ 자동 업데이트가 바꾸는 전부
      runtime\ir\      ←   백본 OpenVINO IR — 덕분에 배포본에 torch 가 없다
      (앱 코드·리소스·양식.xlsx·VERSION 도 여기)
    결과\               ← 엑셀 출력 (app\ 바깥이라 업데이트에 안 쓸린다)
    run_aoi.bat        ← exe 가 백신에 막힐 때의 대체 실행
    run_aoi_debug.bat  ← 오류 진단용(콘솔 + traceback)
  ```
- 빌드가 하는 일: 런처 exe 를 얼리고(`scripts\internal\exe_launcher.spec`) → 번들 CPython 에
  의존성 설치 → 앱 소스 복사 → **백본 IR 배치**(커밋된 것 복사, 없으면 변환) →
  `VERSION` 스탬프 → **산출물 검증**.
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
  `app\` 안의 `__pycache__`, IR 변환용 임시 트리(`.irbuild`).
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

## D. exe-lite — 라이브러리를 첫 실행 때 받는다

C 와 **내용도 구조도 같다.** 다른 것은 하나뿐: 번들 파이썬 안에 라이브러리(PyQt6·
OpenVINO·OpenCV …)를 넣지 않고, 사용자 PC 가 **첫 실행 때 pip 로 받는다.** 전달하는
파일이 크게 줄어든다.

파이썬 런타임과 **백본 IR 은 그대로 동봉**하므로, 사용자 PC 에 파이썬이 필요 없고
GPU 가속도 C 와 똑같이 동작한다.

### D-1. 빌드
```powershell
python scripts\build.py exe-lite
python scripts\make_release_zip.py --lite
```
- 산출물: **`dist\AOI_Verify_Lite\`** → zip 은 `dist\AOI_Verify_<날짜>_<커밋>.zip`
- C 와 **다른 폴더**에 만들어지므로 둘을 함께 유지할 수 있다.

### D-2. 사용자 첫 실행
1. exe 를 더블클릭하면 **검은 창(콘솔)이 뜨고** 설치 진행이 표시된다 — 몇 분 걸린다.
2. 끝나면 앱 창이 저절로 뜬다. **두 번째 실행부터는 콘솔 없이** 바로 켜진다.
3. `설치방법.txt` 의 lite 판에 이 내용이 눈에 띄게 적혀 있다(창을 닫지 말라는 안내 포함).

> 첫 실행에 콘솔을 띄우는 것은 런처가 **표식(`.deps_installed`) 유무**로 판단한다.
> 설치가 끝나면 표식이 생겨 조용한 `pythonw` 로 돌아간다.

### ⚠️ D 를 쓸 때 반드시 감수해야 하는 것
- **각 PC 가 첫 실행 때 PyPI 에 닿아야 한다.** 닿지 못하면 그 PC 는 앱을 아예 못 켠다.
- 총 전송량이 주는 게 아니라 **'들고 가는 파일' 이 작아지는 것**이다. 라이브러리는
  각 PC 가 따로 받는다.
- 그래서 **C(전체 배포본)를 없애지 않는다.** 인터넷이 막힌 PC, 설치가 실패한 PC 에는
  C 를 준다.

---

## 4. 자동 업데이트

- 바뀌는 것은 전부 **`app\` 폴더**에 파일로 있다(C·D·B 공통). exe/런타임/IR 은 그대로 두고
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
| `scripts\build.py` | 빌드 진입점(파이썬) — `exe`/`exe-lite`/`portable`/`online`/`verify` |
| `scripts\make_release_zip.py` | **C·D. 배포용 zip 생성** — 검증 + `설치방법.txt` 동봉 (`--lite`) |
| `scripts\exe_launcher.py` | **C. 런처 진입점** — 대기 중 업데이트 교체 + 앱 실행. 앱 코드 0줄 |
| `scripts\internal\build_exe.bat` · `exe_launcher.spec` | C. exe + app 폴더 빌드 |
| `scripts\internal\portable_build.py` · `make_portable.bat` | B·C·D. 런타임·앱 배치(`install_deps` 로 D 구분) |
| `scripts\internal\build_online.bat` · `online.spec` | A. 온라인 launcher exe 빌드(.bat 은 build.py 래퍼) |
| `scripts\launcher.py` | A. exe 진입점(앱 다운로드+pip 설치+실행) |
| `aoi_verification\app\utils\bootstrap.py` | A. 부트스트랩 핵심 로직(데이터 폴더·의존성 판단) |
| `scripts\run_aoi.bat` · `run_aoi_debug.bat` | B/C. 사용자 실행·진단 |
| `scripts\update_app.bat` | B. 수동 갱신(병합식이라 C 에는 동봉하지 않는다) |

> 빌드·내부 도구는 `scripts\internal\` 에, 사용자가 직접 실행하는 것은 `scripts\` 에 둔다.
