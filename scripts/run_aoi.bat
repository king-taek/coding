@echo off
REM AOI Recipe Verification - run (GUI only, no console window; uses pythonw).
REM Sits in the install folder next to python\ and app\; double-click it (or the
REM "AOI" shortcut the app creates next to it, which carries the app icon).
REM
REM This bat IS the launcher (same job as AOI_Verify.exe / exe_launcher.py, which
REM company security may delete):
REM   1) apply a staged update: app.new -> app (keeps the old app on any failure)
REM   2) tell the app a launcher exists (AOI_APP_HOME), so the next update is
REM      staged into app.new instead of being written over the running app\
REM Keep it in step with scripts/exe_launcher.py (swap_pending / launch_cmd).
REM Every failure path below ends in "run the old version as is".
REM   run_aoi.bat debug  -> same, but in this console with errors visible
REM                         (that is what run_aoi_debug.bat does).
cd /d "%~dp0"
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

REM -- 1) swap ---------------------------------------------------------------
REM A previous swap was cut off between the two renames: recover first.
if not exist "app\" (
    if exist "app.new\" (
        ren "app.new" "app"
    ) else if exist "app.old\" (
        ren "app.old" "app"
    )
) else if exist "app.new\" (
    if exist "app.old\" rd /s /q "app.old"
    REM Could not clear the old backup (AV / open handle): try again next run.
    if not exist "app.old\" (
        ren "app" "app.old" && (ren "app.new" "app" || ren "app.old" "app")
    )
)
if exist "app\" if exist "app.old\" rd /s /q "app.old"

REM -- 2) launch -------------------------------------------------------------
REM Use ONLY the bundled packages. Without this, a same-version Python on the PC
REM lets its user site-packages win over python\, so the app runs on that PC's
REM packages instead of the bundled ones.
set "PYTHONNOUSERSITE=1"
set "AOI_APP_HOME=%ROOT%"
if not exist "%ROOT%\python\pythonw.exe" (
    echo [ERROR] python\pythonw.exe not found.
    echo         Unzip the delivered archive again, or run the portable build.
    pause
    exit /b 1
)
if /i "%~1"=="debug" (
    "%ROOT%\python\python.exe" "%ROOT%\app\main.py"
    echo.
    echo [EXITED] If there is an error ^(traceback^) above, please report it.
    pause
    exit /b 0
)
REM Lite build, first run: no .deps_installed yet -> the app installs libraries
REM with pip. Show a console (python.exe) so the user sees it is working.
if not exist "%ROOT%\.deps_installed" if exist "%ROOT%\python\python.exe" (
    start "" "%ROOT%\python\python.exe" "%ROOT%\app\main.py"
    exit /b 0
)
start "" "%ROOT%\python\pythonw.exe" "%ROOT%\app\main.py"
