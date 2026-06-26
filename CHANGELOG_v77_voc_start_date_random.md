# CHANGELOG v77 - VOC 완료처리 Start Date 랜덤 보정

## 변경 사항

- VOC 완료처리 시 `End Date`는 처리일(오늘)로 설정합니다.
- `Start Date`는 `End Date`보다 13일, 14일, 15일 중 하나를 랜덤으로 앞당긴 날짜로 설정합니다.
- 랜덤 차이는 VOC 이슈별로 적용합니다.
- transition screen에 `Start Date`가 포함된 경우 transition payload에도 동일 값을 넣습니다.
- transition screen에 없더라도 Jira field 목록에서 `Start Date` 필드를 찾으면 Complete 전 edit API로 먼저 저장합니다.
- 처리 로그에 적용 날짜와 차이를 출력합니다.

예시:

```text
PARENT-1 -> VOC-1: Done 처리 완료 (Start Date=2026-05-12, End Date=2026-05-27, 차이=15일)
```
