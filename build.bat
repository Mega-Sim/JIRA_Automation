@echo off
setlocal

echo =============================================
echo   JiraSCCB 실행파일 빌드 (Windows)
echo =============================================

REM Python 확인
python --version >nul 2>&1
if errorlevel 1 (
    echo [오류] Python이 설치되지 않았습니다.
    echo https://www.python.org/downloads/ 에서 Python 3.10 이상을 설치하세요.
    pause
    exit /b 1
)

echo [1/4] 필수 패키지 설치 중...
pip install -r requirements.txt
if errorlevel 1 (
    echo [오류] 패키지 설치 실패
    pause
    exit /b 1
)

echo [2/4] PyInstaller 설치 중...
pip install pyinstaller
if errorlevel 1 (
    echo [오류] PyInstaller 설치 실패
    pause
    exit /b 1
)

echo [3/4] Windows 시간대 데이터(tzdata) 설치 중...
pip install tzdata

echo [4/4] 실행파일 빌드 중...
pyinstaller jira_sccb.spec --clean --noconfirm
if errorlevel 1 (
    echo [오류] 빌드 실패
    pause
    exit /b 1
)

echo.
echo =============================================
echo   빌드 완료!
echo   실행파일 위치: dist\JiraSCCB.exe
echo =============================================
pause
