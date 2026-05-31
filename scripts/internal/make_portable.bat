@echo off
REM ===========================================================================
REM AOI 검증 - 포터블 파이썬 배포 빌드 (PyInstaller 대체)
REM ---------------------------------------------------------------------------
REM 파이썬이 없는 PC 에서도 더블클릭 실행되도록, '자체 포함 CPython 런타임' 을
REM 폴더로 동봉한다.  이 스크립트는 'Windows + 인터넷' 환경에서 1회만 실행하면 됨.
REM 결과: dist_portable\  (python\ + app\ + run_aoi.bat)
REM ===========================================================================
setlocal enabledelayedexpansion
chcp 65001 >nul
REM 이 스크립트는 scripts\internal\ 안에 있으므로 저장소 루트에서 동작하도록 두 단계 위로.
cd /d "%~dp0..\.."

REM --- 베이스 런타임: python-build-standalone 의 'install_only' Windows x86_64 ---
REM   ※ 아래 URL 이 404 면, 최신 'install_only' Windows x86_64 .tar.gz 링크를
REM     https://github.com/astral-sh/python-build-standalone/releases 에서 복사해
REM     PY_URL 을 교체하세요. (표준 venv 는 비파이썬 PC 로 이식 불가하여 사용 안 함)
set "PY_URL=https://github.com/astral-sh/python-build-standalone/releases/download/20250115/cpython-3.11.11+20250115-x86_64-pc-windows-msvc-install_only.tar.gz"

set "OUT=dist_portable"
if not exist "%OUT%" mkdir "%OUT%"

echo [1/4] 자체 포함 CPython 준비...
if exist "%OUT%\python\python.exe" (
    echo   - 기존 %OUT%\python 재사용 (다시 받으려면 폴더 삭제 후 재실행)
) else (
    echo   - 다운로드: %PY_URL%
    curl -L -o "%OUT%\python.tar.gz" "%PY_URL%" || goto :fail
    REM 다운로드 검증 — 파일이 너무 작으면(=오류 페이지) 중단.
    for %%A in ("%OUT%\python.tar.gz") do set "SZ=%%~zA"
    if !SZ! LSS 1000000 (
        echo [실패] 다운로드 파일이 너무 작습니다(!SZ! bytes). PY_URL 을 확인하세요.
        goto :fail
    )
    echo   - 압축 해제...
    pushd "%OUT%" && tar -xf python.tar.gz && del python.tar.gz && popd || goto :fail
)
if not exist "%OUT%\python\python.exe" (
    echo [실패] %OUT%\python\python.exe 가 없습니다. 압축 구조를 확인하세요.
    goto :fail
)

echo [2/4] 의존성 설치 (torch/openvino 포함 — 시간이 걸립니다)...
"%OUT%\python\python.exe" -m pip install --upgrade pip || goto :fail
"%OUT%\python\python.exe" -m pip install -r requirements.txt || goto :fail
REM 회사 보안 정책 가드 — 설치된 패키지/환경에 금지 도구가 끼어 있으면 즉시 중단.
"%OUT%\python\python.exe" "%~dp0verify_no_forbidden.py" || goto :fail

echo [3/4] 앱 소스 복사 (app\)...
if not exist "%OUT%\app" mkdir "%OUT%\app"
xcopy /E /I /Y "aoi_verification" "%OUT%\app\aoi_verification" || goto :fail
copy /Y "main.py" "%OUT%\app\main.py" >nul || goto :fail
REM 엑셀 템플릿(양식.xlsx)은 dev\ 로 정리됨 → 배포 app\ 루트로 복사(template_path 가 찾음).
copy /Y "dev\양식.xlsx" "%OUT%\app\양식.xlsx" >nul || goto :fail
REM 런처/업데이트 스크립트는 상위 scripts\ 폴더(%~dp0..)에 있고, 배포 폴더 최상위로 복사.
copy /Y "%~dp0..\run_aoi.bat" "%OUT%\run_aoi.bat" >nul
copy /Y "%~dp0..\run_aoi_debug.bat" "%OUT%\run_aoi_debug.bat" >nul
copy /Y "%~dp0..\update_app.bat" "%OUT%\update_app.bat" >nul

REM 자동 업데이트용 버전 스탬프 — 현재 커밋 SHA + 브랜치를 app\VERSION 에 기록.
REM (git 이 없으면 건너뜀 → 앱은 '개발 모드' 로 업데이트 확인 안 함)
set "SHA="
set "BR="
for /f %%i in ('git rev-parse HEAD 2^>nul') do set "SHA=%%i"
for /f %%i in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "BR=%%i"
if defined SHA if defined BR (
    > "%OUT%\app\VERSION" echo {"sha":"!SHA!","branch":"!BR!","repo":"king-taek/coding"}
    echo   - VERSION 기록: !BR! @ !SHA!
)

echo [4/4] 완료!
echo   배포: %OUT% 폴더 전체를 zip 으로 묶어 전달 → 대상 PC 에서 압축 해제 후
echo         run_aoi.bat 더블클릭(파이썬 불필요).
echo   업데이트: app\ 소스만 교체(update_app.bat) — 무거운 python\ 은 그대로.
echo.
pause
goto :eof

:fail
echo.
echo [실패] 위 메시지를 확인하세요. (네트워크/URL/권한/회사 보안 정책 등)
echo  - PY_URL 404 면 releases 페이지에서 최신 install_only Windows x86_64 링크로 교체.
echo.
pause
exit /b 1
