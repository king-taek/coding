@echo off
REM AOI 검증 실행 — 콘솔 창 없이 GUI 만 띄운다(pythonw).
REM 배포 폴더(dist_portable) 안에서 python\ 과 app\ 옆에 두고 더블클릭.
chcp 65001 >nul
cd /d "%~dp0"
if not exist "%~dp0python\pythonw.exe" (
    echo [오류] python\pythonw.exe 가 없습니다. 포터블 빌드(make_portable.bat)를 먼저 실행하세요.
    pause
    exit /b 1
)
start "" "%~dp0python\pythonw.exe" "%~dp0app\main.py"
