#!/bin/bash
set -e

echo "============================================="
echo "  JiraSCCB 실행파일 빌드 (Mac/Linux)"
echo "============================================="

# Python 확인
if ! command -v python3 &>/dev/null; then
    echo "[오류] python3이 설치되지 않았습니다."
    exit 1
fi

echo "[1/3] 필수 패키지 설치 중..."
pip3 install -r requirements.txt

echo "[2/3] PyInstaller 설치 중..."
pip3 install pyinstaller

echo "[3/3] 실행파일 빌드 중..."
pyinstaller jira_sccb.spec --clean --noconfirm

echo ""
echo "============================================="
echo "  빌드 완료!"
echo "  실행파일 위치: dist/JiraSCCB"
echo "============================================="
