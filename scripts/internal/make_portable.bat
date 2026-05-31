@echo off
REM ===========================================================================
REM AOI Recipe Verification - Portable Python build (PyInstaller alternative)
REM ---------------------------------------------------------------------------
REM Bundles a self-contained CPython runtime in a folder so the app runs by
REM double-click on a PC WITHOUT Python. Run once on Windows + internet.
REM Output: dist_portable\  (python\ + app\ + run_aoi.bat)
REM ===========================================================================
setlocal enabledelayedexpansion
REM This script lives in scripts\internal\ -> go up two levels to the repo root.
cd /d "%~dp0..\.."

REM --- Base runtime: python-build-standalone 'install_only' Windows x86_64 ---
REM   If the URL below 404s, copy the latest 'install_only' Windows x86_64
REM   .tar.gz link from
REM   https://github.com/astral-sh/python-build-standalone/releases
REM   and replace PY_URL. (A plain venv is not portable to non-Python PCs.)
set "PY_URL=https://github.com/astral-sh/python-build-standalone/releases/download/20250115/cpython-3.11.11+20250115-x86_64-pc-windows-msvc-install_only.tar.gz"

set "OUT=dist_portable"
if not exist "%OUT%" mkdir "%OUT%"

echo [1/4] Preparing self-contained CPython...
if exist "%OUT%\python\python.exe" (
    echo   - Reusing existing %OUT%\python (delete the folder to re-download)
) else (
    echo   - Downloading: %PY_URL%
    curl -L -o "%OUT%\python.tar.gz" "%PY_URL%" || goto :fail
    REM Validate download: if too small (=error page), abort.
    for %%A in ("%OUT%\python.tar.gz") do set "SZ=%%~zA"
    if !SZ! LSS 1000000 (
        echo [FAILED] Downloaded file too small (!SZ! bytes). Check PY_URL.
        goto :fail
    )
    echo   - Extracting...
    pushd "%OUT%" && tar -xf python.tar.gz && del python.tar.gz && popd || goto :fail
)
if not exist "%OUT%\python\python.exe" (
    echo [FAILED] %OUT%\python\python.exe not found. Check archive layout.
    goto :fail
)

echo [2/4] Installing dependencies (torch/openvino included - takes a while)...
"%OUT%\python\python.exe" -m pip install --upgrade pip || goto :fail
"%OUT%\python\python.exe" -m pip install -r requirements.txt || goto :fail
REM Company security guard.
"%OUT%\python\python.exe" "%~dp0verify_no_forbidden.py" || goto :fail

echo [3/4] Copying app source (app\)...
if not exist "%OUT%\app" mkdir "%OUT%\app"
xcopy /E /I /Y "aoi_verification" "%OUT%\app\aoi_verification" || goto :fail
copy /Y "main.py" "%OUT%\app\main.py" >nul || goto :fail
REM Excel template lives under dev\ now. Copy every .xlsx in dev\ to app\ root
REM (template_path looks there). Wildcard avoids a non-ASCII filename literal.
copy /Y "dev\*.xlsx" "%OUT%\app\" >nul || goto :fail
REM Launcher/update scripts live in the parent scripts\ folder; copy to the top.
copy /Y "%~dp0..\run_aoi.bat" "%OUT%\run_aoi.bat" >nul
copy /Y "%~dp0..\run_aoi_debug.bat" "%OUT%\run_aoi_debug.bat" >nul
copy /Y "%~dp0..\update_app.bat" "%OUT%\update_app.bat" >nul

REM Version stamp for auto-update: write current commit SHA + branch to app\VERSION.
REM (If git is missing, skip -> app runs in 'dev mode' and skips update check.)
set "SHA="
set "BR="
for /f %%i in ('git rev-parse HEAD 2^>nul') do set "SHA=%%i"
for /f %%i in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "BR=%%i"
if defined SHA if defined BR (
    > "%OUT%\app\VERSION" echo {"sha":"!SHA!","branch":"!BR!","repo":"king-taek/coding"}
    echo   - VERSION written: !BR! @ !SHA!
)

echo [4/4] Done.
echo   Distribute: zip the whole %OUT% folder. On the target PC, unzip and
echo   double-click run_aoi.bat (no Python needed).
echo   Update: replace only app\ source (update_app.bat); keep heavy python\.
echo.
pause
goto :eof

:fail
echo.
echo [FAILED] Check the message above (network/URL/permissions/security policy).
echo  - On PY_URL 404, replace it with the latest install_only Windows x86_64 link.
echo.
pause
exit /b 1
