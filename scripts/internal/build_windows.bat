@echo off
REM ===========================================================================
REM AOI Recipe Verification - (optional) Windows standalone .exe build
REM ---------------------------------------------------------------------------
REM Recommended distribution is the portable Python build (make_portable.bat).
REM PyInstaller's bootloader can be blocked by corporate antivirus/security, so
REM this standalone exe is "optional". Run on Windows with Python 3.9+ installed.
REM PyInstaller cannot cross-compile, so a Windows exe must be built on Windows.
REM
REM Usage: double-click this file, or run build_windows.bat from a prompt.
REM Output: dist\AOI_Verify\AOI_Verify.exe  (ship the whole dist\AOI_Verify folder)
REM ===========================================================================
setlocal
REM This script lives in scripts\internal\ -> go up two levels to the repo root.
cd /d "%~dp0..\.."

echo [1/4] Preparing virtual environment...
if not exist ".venv" (
    python -m venv .venv || goto :fail
)
call .venv\Scripts\activate || goto :fail

echo [2/4] Installing dependencies (may take a while: torch/openvino)...
python -m pip install --upgrade pip || goto :fail
pip install -r requirements.txt || goto :fail
pip install "pyinstaller>=6" || goto :fail
REM Company security guard.
python "%~dp0verify_no_forbidden.py" || goto :fail

echo [3/4] PyInstaller build (onedir)...
pyinstaller --noconfirm scripts\internal\aoi_verification.spec || goto :fail

echo [4/4] Done.
echo   Output: dist\AOI_Verify\AOI_Verify.exe
echo   Ship the whole dist\AOI_Verify folder as a zip.
echo   (Efficiency mode included, so the folder is roughly 1.3-2.0 GB.)
echo.
pause
goto :eof

:fail
echo.
echo [FAILED] Check the error above (Python install / network / permissions).
echo  - If the window closes instantly, run it from a command prompt instead.
echo.
pause
exit /b 1
