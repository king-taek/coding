@echo off
REM AOI Recipe Verification - run (GUI only, no console window; uses pythonw).
REM Place inside the portable folder (dist_portable) next to python\ and app\,
REM then double-click.
cd /d "%~dp0"
if not exist "%~dp0python\pythonw.exe" (
    echo [ERROR] python\pythonw.exe not found. Run the portable build first
    echo         (scripts\internal\make_portable.bat).
    pause
    exit /b 1
)
start "" "%~dp0python\pythonw.exe" "%~dp0app\main.py"
