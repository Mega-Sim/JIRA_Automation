# v72 - LLM TC 생성 / AIO Test 판정 보정

## 수정 항목

1. LLM TC 생성 판정 보정
   - `CheckTCStatus` 응답이 JSON이 아니거나 `CompleteCount=0`으로 내려오는 경우에도 History의 `결과 확인`, `생성 완료 N건` 문구를 재검증합니다.
   - 명시적인 `생성중/오류/실패`가 있으면 fallback으로 덮어쓰지 않습니다.
   - History API가 비어 있어도 AIO TestCase가 실제 생성되어 있으면 TC 생성 완료로 보정합니다.

2. AIO Test 판정 보정
   - 기존에는 난이도 필드 추출 실패 시 테스트 케이스가 있어도 `ERR(NO LEVEL)`로 처리했습니다.
   - 이제 AIO TestCase가 실제 존재하면 `OK(실제개수/-)`로 표시합니다.
   - 난이도가 정상 추출되면 기존처럼 `OK/FAIL(실제개수/필요개수)`로 비교합니다.

3. AIO TestCase 개수 추출 강화
   - `testCycle.summary.totalTests`, `testCases`, `tests`, `rows`, `items`, `results`, `testCaseCount` 등 응답 변형을 재귀적으로 처리합니다.
   - Browse HTML의 `테스트 케이스 (10)` 또는 `AMAMRAPP-TC-777` 같은 TestCase 키도 fallback으로 계산합니다.
   - cycle total과 중첩 testcase list를 중복 합산하지 않도록 보정했습니다.

4. UI 정렬 보정
   - `OK(10/-)` 형식도 AIO Test 컬럼 정렬에서 정상 처리합니다.
