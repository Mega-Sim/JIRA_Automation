# CHANGELOG v70 - Missing SW_VOC 판정 수정

## 수정 내용
- Jira 이슈 링크 검증에서 SW_VOC 판정 조건을 수정했습니다.
- 기존 로직은 SW_VOC를 `inwardIssue` + `is child of` 관계에서만 인정했습니다.
- 실제 Jira 화면에서는 SW_VOC가 `relates to` 관계로 연결되어도 정상 연결로 표시됩니다.
- 따라서 `inwardIssue` / `outwardIssue` 양쪽 모두 확인하고, 링크 관계명보다 연결된 상대 이슈의 `issuetype.name` 및 `key`를 기준으로 판정하도록 변경했습니다.

## 보완 판정 기준
- `issuetype.name`에 `SW_VOC`, `SW VOC`, `SW` + `VOC` 포함
- issue key가 `AMSWV-*`, `SWVOC-*`, `SW_VOC-*` 형태

## 영향 범위
- `sccb_app/jira_client.py::get_link_validation()`만 수정
- UI 로직 및 Complete 처리 로직은 변경 없음
