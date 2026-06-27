# exe 빌드·배포·설치 가이드

> 파이썬이 없는 사용자에게 AOI 검증 앱을 전달하는 방법입니다. 배포 방식 3가지와,
> 각 방식의 **빌드(전달하는 사람)** · **설치/실행(받는 사람)** 절차를 정리합니다.
> (자동 업데이트 동작은 `docs/업데이트_동작.md` 참고.)

## 0. 어떤 방식을 고를까

| 방식 | 산출물 | 용량 | 인터넷 | 추천 상황 |
|---|---|---|---|---|
| **A. 온라인 launcher exe** | `AOI_Verify_Online.exe` 1개 | 수십 MB | **첫 실행 시 필요** | 기본 권장 — 전달이 가장 간단 |
| **B. 포터블 폴더** | `dist_portable\` 폴더 | ~1.3~2.0GB | 불필요 | 폐쇄망(인터넷 차단) PC |
| **C. 단독 exe(PyInstaller)** | `dist\AOI_Verify\` 폴더 | ~1.5GB | 불필요 | 폐쇄망 + 단일 exe 형태가 꼭 필요할 때 |

- **공통 전제(빌드하는 PC)**: Windows + 파이썬 3.9+ 설치 + 인터넷. 빌드는 저장소 루트에서 실행.
- **세 방식 모두 자동 업데이트가 동작**한다(앱이 쓰기 가능한 폴더에 설치되므로). 상세는 4절.

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

## C. 단독 exe — PyInstaller(전부 동봉)

의존성까지 모두 포함한 단독 실행형이 꼭 필요할 때(폐쇄망 + 단일 exe 형태 요구 등).

### C-1. 빌드 (전달하는 사람, 1회)
```powershell
python scripts\build.py windows
REM 또는: scripts\internal\build_windows.bat
```
- 산출물: **`dist\AOI_Verify\`** 폴더(통째로 zip 배포, ~1.5GB). 진입 exe 는 `AOI_Verify.exe`.
- 설정 스펙: `scripts\internal\aoi_verification.spec`(스타일시트 · `dev\양식.xlsx` 자동 동봉).

### C-2. 설치/실행 (받는 사람)
1. 받은 zip 을 압축 해제.
2. `AOI_Verify\AOI_Verify.exe` **더블클릭**.
3. 첫 실행 경고(미서명) 시 *추가 정보 → 실행*.

> 참고: PyInstaller 부트로더가 회사 백신/보안 정책에 막혀 빌드·실행이 안 되는 경우가 있다.
> 그럴 땐 **A(온라인)** 또는 **B(포터블)** 를 쓴다.

---

## 4. 자동 업데이트 (세 방식 공통)

- 앱은 **쓰기 가능한 폴더**에 설치된다(A: `%LOCALAPPDATA%\AOI Recipe Verification`, B/C: 배포 폴더의 `app\`).
  그래서 **앱 내 자동 업데이트가 그 폴더를 갱신**한다 — exe/폴더를 **다시 배포할 필요가 없다**.
- 동작: 앱 시작 시 GitHub 브랜치 HEAD 와 동봉된 `VERSION` 을 비교해 새 버전이 있으면 받아
  앱 폴더에 미러링하고 재시작을 안내한다(개발 전용 `dev/` 는 제외, `dev\양식.xlsx` 만 앱 루트로 복사).
- **의존성(requirements.txt) 변경**은 자동 재설치하지 않고 안내만 한다.
  - A(온라인): 다음 실행 때 launcher 가 바뀐 requirements 를 감지해 자동으로 pip 재설치한다.
  - B/C(동봉): 무거운 런타임은 그대로라, 패키지 추가가 필요하면 새 빌드를 한 번 더 배포한다.
- 자세한 규칙·제외 목록은 `docs/업데이트_동작.md`.

---

## 5. 빌드/배포 파일 위치 요약

| 파일 | 역할 |
|---|---|
| `scripts\build.py` | 빌드 진입점(파이썬) — `online`/`portable`/`windows` |
| `scripts\internal\build_online.bat` · `online.spec` | A. 온라인 launcher exe 빌드(.bat 은 build.py 래퍼) |
| `scripts\launcher.py` | A. exe 진입점(앱 다운로드+pip 설치+실행) |
| `scripts\internal\portable_build.py` · `make_portable.bat` | B. 포터블 폴더 빌드 |
| `scripts\internal\build_windows.bat` · `aoi_verification.spec` | C. 단독 exe 빌드 |
| `aoi_verification\app\utils\bootstrap.py` | A. 부트스트랩 핵심 로직(데이터 폴더·의존성 판단) |
| `scripts\run_aoi.bat` · `update_app.bat` | B/C. 사용자 실행·수동 갱신 |

> 빌드·내부 도구는 `scripts\internal\` 에, 사용자가 직접 실행하는 것은 `scripts\` 에 둔다.
