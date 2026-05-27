@echo off
REM AOI 검증 실행(디버그) — 콘솔을 띄워 오류 메시지를 그대로 보여준다.
REM '앱이 안 켜져요/창이 바로 닫혀요' 진단용.  종료 후 창이 닫히지 않도록 pause.
chcp 65001 >nul
cd /d "%~dp0"
if not exist "%~dp0python\python.exe" (
    echo [오류] python\python.exe 가 없습니다. 포터블 빌드(make_portable.bat)를 먼저 실행하세요.
    pause
    exit /b 1
)
"%~dp0python\python.exe" "%~dp0app\main.py"
echo.
echo [종료됨] 위에 오류(traceback)가 있으면 그대로 알려주세요.
pause
