@echo off
REM ===========================================================================
REM AOI 검증 - 온라인 다운로드형 작은 launcher exe 빌드
REM ---------------------------------------------------------------------------
REM 파이썬 없는 사용자에게 줄 **작은 exe**(수십 MB)를 만든다.  exe 는 앱/무거운
REM 의존성을 포함하지 않고, 처음 실행 시 GitHub 에서 앱을 받아 %LOCALAPPDATA%\AOI_Verify
REM 에 풀고 인터넷으로 pip 설치한 뒤 실행한다(이후 앱 자동 업데이트가 그 폴더를 갱신).
REM
REM ※ 인터넷이 막힌 폐쇄망이면 이 방식 대신 make_portable.bat(전부 동봉)을 쓰세요.
REM 반드시 Windows + 파이썬(3.9+) + 인터넷 환경에서, 저장소 루트 기준으로 실행.
REM 사용법: 이 파일을 더블클릭하거나 명령 프롬프트에서 실행.
REM 산출물: dist\AOI_Verify_Online.exe  (이 파일 하나만 배포)
REM ===========================================================================
setlocal
chcp 65001 >nul
REM 이 스크립트는 scripts\internal\ 안 → 저장소 루트로 두 단계 위.
cd /d "%~dp0..\.."

echo [1/3] 가상환경 준비...
if not exist ".venv" (
    python -m venv .venv || goto :fail
)
call .venv\Scripts\activate || goto :fail
python -m pip install --upgrade pip || goto :fail
pip install "pyinstaller>=6" || goto :fail
REM 회사 보안 정책 가드.
python "%~dp0verify_no_forbidden.py" || goto :fail

echo [2/3] 작은 launcher 빌드 (onefile, 앱/의존성 미포함)...
pyinstaller --noconfirm scripts\internal\online.spec || goto :fail

echo [3/3] 완료!
echo   실행 파일: dist\AOI_Verify_Online.exe  (이 파일 하나만 배포)
echo   - 사용자는 더블클릭하면 됩니다. 첫 실행은 인터넷으로 앱+패키지를 받느라 시간이
echo     걸리고(수백 MB), 이후 실행은 빠릅니다. 앱은 %%LOCALAPPDATA%%\AOI_Verify 에 설치됩니다.
echo.
pause
goto :eof

:fail
echo.
echo [실패] 위 오류 메시지를 확인하세요. (파이썬 설치/네트워크/권한 등)
echo.
pause
exit /b 1
