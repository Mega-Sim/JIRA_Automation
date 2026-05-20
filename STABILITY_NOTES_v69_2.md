# v69.2 안정화판

## 반영 내용
- AIO Test 전건 ERR 회귀 수정
- `python -m sccb.app` 실행 호환 복구
- `sccb_app` 기존 구조는 유지하면서 `sccb` 호환 패키지 추가

## 원인
- `jira_client.find_field_id()`가 삭제된 `utils.py`를 계속 import하고 있었음
- AIO Test 검증은 시작 시 `난이도` 필드 id 조회를 타므로 즉시 예외 발생
- UI 쪽 안전 호출에서 예외가 삼켜져 결과가 `ERR`로만 보였음
