# JiraSCCB 실행파일

Python이 설치되어 있지 않은 사용자도 바로 실행할 수 있도록 빌드된 실행파일을 이 폴더에 보관합니다.

## 포함된 파일

| 파일 | 대상 OS | 실행 방법 |
|------|---------|-----------|
| `JiraSCCB.exe` | Windows | 파일을 더블클릭 |
| `JiraSCCB-linux` | Linux | `chmod +x releases/JiraSCCB-linux && ./releases/JiraSCCB-linux` |

## Windows 실행파일

Windows용 실행파일은 Windows 환경에서 `build.bat`을 실행해 생성합니다. 생성 결과는 `releases\JiraSCCB.exe`에 저장되며, 사용자는 Python 설치 없이 이 파일을 더블클릭해 실행할 수 있습니다.

> 참고: PyInstaller 실행파일은 빌드한 운영체제와 같은 운영체제용으로 생성됩니다. 따라서 Linux 환경에서 Windows `.exe`를 직접 생성하지 않습니다.

## 새 버전 빌드 후 저장 절차

1. Linux/Mac: `./build.sh`, Windows: `build.bat` 실행
2. 생성된 실행파일을 `releases/` 폴더로 복사
   - Linux: `cp dist/JiraSCCB releases/JiraSCCB-linux`
   - Windows: `build.bat`이 `releases\JiraSCCB.exe`로 자동 복사
3. `git add releases/` 후 커밋
