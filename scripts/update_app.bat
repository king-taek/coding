@echo off
REM AOI Recipe Verification - update: keep the heavy python\ runtime, replace
REM only the small app\ source.
REM   (a) if app\ is a git repo  -> git pull
REM   (b) otherwise              -> ask for a new source folder and overwrite
cd /d "%~dp0"

if exist "app\.git" (
    echo [git] Updating app\ repository to latest...
    pushd app
    git pull
    popd
    echo Done.
    pause
    exit /b 0
)

echo app\ is not a git repository.
set "SRC="
set /p "SRC=Enter the folder that contains the new source (Enter = cancel): "
if not defined SRC (
    echo Cancelled.
    pause
    exit /b 0
)
if not exist "%SRC%\aoi_verification" (
    echo [ERROR] "%SRC%" has no aoi_verification folder.
    pause
    exit /b 1
)
echo Copying source from "%SRC%" into app\ ...
xcopy /E /I /Y "%SRC%\aoi_verification" "app\aoi_verification"
if exist "%SRC%\main.py" copy /Y "%SRC%\main.py" "app\main.py" >nul
REM Excel template (wildcard avoids a non-ASCII filename literal).
copy /Y "%SRC%\*.xlsx" "app\" >nul 2>nul
echo Done.
pause
