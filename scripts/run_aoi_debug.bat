@echo off
REM AOI Recipe Verification - run (debug) with a console so errors are visible.
REM Use this if the app does not start / the window closes instantly.
cd /d "%~dp0"
if not exist "%~dp0python\python.exe" (
    echo [ERROR] python\python.exe not found. Run the portable build first
    echo         (scripts\internal\make_portable.bat).
    pause
    exit /b 1
)
"%~dp0python\python.exe" "%~dp0app\main.py"
echo.
echo [EXITED] If there is an error (traceback) above, please report it.
pause
