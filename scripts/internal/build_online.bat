@echo off
REM ===========================================================================
REM AOI Recipe Verification - Online launcher exe build (small downloader)
REM ---------------------------------------------------------------------------
REM Builds a SMALL exe (tens of MB) for users without Python. The exe does NOT
REM bundle the app or heavy deps; on first run it downloads the app from GitHub
REM into %LOCALAPPDATA%\AOI Recipe Verification and pip-installs deps online,
REM then launches. The in-app auto-update keeps that folder up to date.
REM
REM If the target PC has NO internet, use make_portable.bat instead (bundles all).
REM Run on Windows + Python 3.9+ + internet. Double-click this file, or run it
REM from a command prompt. It auto-changes to the repo root.
REM Output: dist\AOI_Verify_Online.exe  (ship this single file)
REM ===========================================================================
setlocal
REM This script lives in scripts\internal\ -> go up two levels to the repo root.
cd /d "%~dp0..\.."

echo [1/3] Preparing virtual environment...
if not exist ".venv" (
    python -m venv .venv || goto :fail
)
call .venv\Scripts\activate || goto :fail
python -m pip install --upgrade pip || goto :fail
pip install "pyinstaller>=6" || goto :fail
REM Company security guard.
python "%~dp0verify_no_forbidden.py" || goto :fail

echo [2/3] Building small launcher (onefile, no app/deps bundled)...
pyinstaller --noconfirm scripts\internal\online.spec || goto :fail

echo [3/3] Done.
echo   Output: dist\AOI_Verify_Online.exe  (ship this single file)
echo   Users just double-click it. First run downloads app+packages
echo   (a few hundred MB); later runs are fast. Installed under
echo   %%LOCALAPPDATA%%\AOI Recipe Verification.
echo.
pause
goto :eof

:fail
echo.
echo [FAILED] Check the error above (Python install / network / permissions).
echo.
pause
exit /b 1
