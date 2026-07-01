# v69 최적화 사항

## 목표
- 전체 기능 유지
- 코드 단순화
- 변수/파일 수 축소

## 실제 반영
- `constants.py`, `presets.py`, `utils.py` 제거
- 상수/JQL/간단 유틸을 실제 사용 파일로 통합
  - `ui.py` : Base URL, TZ, SCCB preset JQL 통합
  - `workflow.py` : `norm_field_name()` 통합
  - `jira_client.py` : field name normalize 내부 메서드화
- 테스트용 `test_debug.py` 제거
- 배포본에서 `__pycache__` 제외

## 결과
- 소스 파일 수: **7개 -> 4개**
  - 유지: `app.py`, `ui.py`, `jira_client.py`, `workflow.py`
- 전체 기능 로직은 유지하고, 분산된 작은 파일만 정리

## 주의
- 기능 추가/삭제 없이 구조만 단순화한 버전입니다.
