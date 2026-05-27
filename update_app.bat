@echo off
REM AOI 검증 업데이트 — 무거운 python\ 런타임은 그대로 두고, 작은 app\ 소스만 교체.
REM   (a) app\ 이 git 저장소면  git pull
REM   (b) 아니면 새 app 폴더 경로를 입력받아 덮어쓰기
chcp 65001 >nul
cd /d "%~dp0"

if exist "app\.git" (
    echo [git] app\ 저장소를 최신으로 갱신합니다...
    pushd app
    git pull
    popd
    echo 완료.
    pause
    exit /b 0
)

echo app\ 이 git 저장소가 아닙니다.
set "SRC="
set /p "SRC=새 소스가 들어있는 폴더 경로를 입력하세요(취소: 그냥 Enter): "
if not defined SRC (
    echo 취소되었습니다.
    pause
    exit /b 0
)
if not exist "%SRC%\aoi_verification" (
    echo [오류] "%SRC%" 안에 aoi_verification 폴더가 없습니다.
    pause
    exit /b 1
)
echo "%SRC%" 의 소스를 app\ 으로 복사합니다...
xcopy /E /I /Y "%SRC%\aoi_verification" "app\aoi_verification"
if exist "%SRC%\main.py" copy /Y "%SRC%\main.py" "app\main.py" >nul
if exist "%SRC%\양식.xlsx" copy /Y "%SRC%\양식.xlsx" "app\양식.xlsx" >nul
echo 완료.
pause
