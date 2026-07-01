@echo off
setlocal

REM Switch the console code page to UTF-8 so the Korean text in this
REM script displays correctly and cmd.exe does not misparse the
REM UTF-8 bytes under the default (CP949) Korean code page, which can
REM break "if (...)" blocks and cause double-click execution to fail.
chcp 65001 >nul

REM Always run from the folder that contains this batch file.
REM This makes double-click execution work even if Windows starts the
REM script with a different current working directory.
cd /d "%~dp0"

set "PY_CMD="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3"
if not defined PY_CMD (
    python --version >nul 2>&1
    if not errorlevel 1 set "PY_CMD=python"
)

echo =============================================
echo   JiraSCCB Windows exe 빌드
echo =============================================
echo   작업 폴더: %CD%
echo.

REM Python 확인
if not defined PY_CMD (
    echo [오류] Python이 설치되지 않았습니다.
    echo https://www.python.org/downloads/ 에서 Python 3.10 이상을 설치하세요.
    pause
    exit /b 1
)

echo [정보] Python 실행 명령: %PY_CMD%
echo.

echo [1/4] 필수 패키지 설치 중...
%PY_CMD% -m pip install -r requirements.txt
if errorlevel 1 (
    echo [오류] 패키지 설치 실패
    pause
    exit /b 1
)

echo [2/4] PyInstaller 설치 중...
%PY_CMD% -m pip install pyinstaller
if errorlevel 1 (
    echo [오류] PyInstaller 설치 실패
    pause
    exit /b 1
)

echo [3/4] Windows 시간대 데이터(tzdata) 설치 중...
%PY_CMD% -m pip install tzdata
if errorlevel 1 (
    echo [오류] tzdata 설치 실패
    pause
    exit /b 1
)

echo [4/4] 실행파일 빌드 중...
%PY_CMD% -m PyInstaller jira_sccb.spec --clean --noconfirm
if errorlevel 1 (
    echo [오류] 빌드 실패
    pause
    exit /b 1
)

if not exist "dist\JiraSCCB.exe" (
    echo [오류] dist\JiraSCCB.exe 파일을 찾을 수 없습니다.
    pause
    exit /b 1
)

if not exist releases mkdir releases
copy /Y dist\JiraSCCB.exe releases\JiraSCCB.exe >nul
if errorlevel 1 (
    echo [오류] releases 폴더로 실행파일 복사 실패
    pause
    exit /b 1
)

echo.
echo =============================================
echo   빌드 완료!
echo   실행파일 위치: releases\JiraSCCB.exe
echo   Python 없는 사용자도 이 exe를 더블클릭해서 실행할 수 있습니다.
echo =============================================
pause
