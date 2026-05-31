@echo off
REM ===========================================================================
REM AOI 검증 - (선택) Windows 단독 실행형(.exe) 빌드 스크립트
REM ---------------------------------------------------------------------------
REM ※ 권장 배포는 포터블 파이썬(make_portable.bat) 입니다. PyInstaller 부트로더는
REM   회사 백신/보안 정책에 막혀 빌드/실행이 안 되는 경우가 있어 '선택' 으로 둡니다.
REM 반드시 Windows 에서, 파이썬(3.9+)이 설치된 상태로 실행하세요.
REM PyInstaller 는 크로스컴파일이 안 되므로 Windows exe 는 Windows 에서만 만듭니다.
REM
REM 사용법: 이 파일을 더블클릭하거나, 명령 프롬프트에서  build_windows.bat  실행.
REM 산출물: dist\AOI_Verify\AOI_Verify.exe  (dist\AOI_Verify 폴더 통째로 배포)
REM 오류로 창이 바로 닫히던 문제 → 끝에 pause 추가로 메시지를 확인할 수 있게 함.
REM ===========================================================================
setlocal
chcp 65001 >nul
REM 이 스크립트는 scripts\internal\ 안에 있으므로 저장소 루트에서 동작하도록 두 단계 위로.
cd /d "%~dp0..\.."

echo [1/4] 가상환경 준비...
if not exist ".venv" (
    python -m venv .venv || goto :fail
)
call .venv\Scripts\activate || goto :fail

echo [2/4] 의존성 설치 (시간이 걸릴 수 있습니다: torch/openvino 포함)...
python -m pip install --upgrade pip || goto :fail
pip install -r requirements.txt || goto :fail
pip install "pyinstaller>=6" || goto :fail
REM 회사 보안 정책 가드 — 설치된 패키지/환경에 금지 도구가 끼어 있으면 즉시 중단.
python "%~dp0verify_no_forbidden.py" || goto :fail

echo [3/4] PyInstaller 빌드 (onedir)...
pyinstaller --noconfirm scripts\internal\aoi_verification.spec || goto :fail

echo [4/4] 완료!
echo   실행 파일: dist\AOI_Verify\AOI_Verify.exe
echo   배포 시 dist\AOI_Verify 폴더를 통째로 zip 으로 묶어 전달하세요.
echo   (고효율 모드 포함 빌드라 폴더 용량은 대략 1.3~2.0GB 입니다.)
echo.
pause
goto :eof

:fail
echo.
echo [실패] 위 오류 메시지를 확인하세요. (파이썬 설치/네트워크/권한 등)
echo  - 창이 바로 닫히면 이 메시지를 못 봅니다 → 명령 프롬프트에서 실행해도 됩니다.
echo.
pause
exit /b 1
