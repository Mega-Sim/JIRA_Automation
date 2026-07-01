# v94 - SCCB Handy Date 매크로 주간 범위 갱신

## 변경 내용

- 전주 SCCB 페이지를 Storage XML로 복제할 때, `사전SCCB 검토 의견` 제목의 **Handy Date 매크로 2개**도 다음 주 기간으로 옮기도록 보완했습니다.
  - 예: `2026. 6. 22. ~ 2026. 6. 29.` → `2026. 6. 29. ~ 2026. 7. 5.`
- 렌더링 HTML의 `time.handy-date-time` 형태와 Confluence Storage XML의 `ac:structured-macro` Date 계열 형태를 모두 처리합니다.
- 일반 본문/표에 적힌 단일 이슈 일정은 변경하지 않습니다.

## 검증

- 기존 주차 제목/연도 전환/중복 방지/Storage 복제 테스트를 유지했습니다.
- Handy Date 시작일·종료일 갱신 및 일반 개별 일정 미변경 테스트를 추가했습니다.
