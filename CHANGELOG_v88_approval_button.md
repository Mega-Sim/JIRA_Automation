# v88: 선택 이슈 Approval 버튼 추가

## 추가 기능
- 버튼 행에 "선택 이슈 Approval" (WARNING/노란색) 버튼 추가
  - 위치: Open Selected 와 선택 이슈 Complete 사이
- TransitionWorkflow.process_issue_to_approval() 신규
  - In Verification -> Approval(또는 Approver) 전이만 수행, Complete까지 가지 않음
  - 전이 후 최대 6초(0.6s x 10) 폴링으로 상태 반영 확인
  - 이미 Approval/Approver면 스킵 로그
  - In Verification이 아닌 상태(Open, Complete 등)는 스킵 로그
  - Approval 전이가 워크플로우에 없으면 스킵 로그
- UI: 전이 성공 시 issue_status 캐시와 그리드 STATUS 컬럼 즉시 갱신
  - 바로 이어서 "선택 이슈 Complete"를 눌러도 캐시 상태가 Approval로 잡힘

## 검증
- mock 단위 테스트 5케이스 통과 (전이 성공/이미 Approval/타 상태 스킵/전이 없음/Approver 변형)
