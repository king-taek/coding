@echo off
REM AOI Recipe Verification - run (GUI only, no console window; uses pythonw).
REM Place inside the portable folder (dist_portable) next to python\ and app\,
REM then double-click.
cd /d "%~dp0"
REM Use ONLY the bundled packages. Without this, a same-version Python on the PC
REM lets its user site-packages win over python\, so the app runs on that PC's
REM packages instead of the bundled ones. (Do NOT set AOI_APP_HOME here: the
REM portable build ships this same file and has no launcher to apply updates.)
set "PYTHONNOUSERSITE=1"
if not exist "%~dp0python\pythonw.exe" (
    echo [ERROR] python\pythonw.exe not found.
    echo         Unzip the delivered archive again, or run the portable build.
    pause
    exit /b 1
)
start "" "%~dp0python\pythonw.exe" "%~dp0app\main.py"
