# v76 AIO/PR 실제 오검출 보정

## 핵심 수정

1. AIO endpoint 응답을 첫 번째 non-empty에서 멈추지 않고 모든 후보를 조회합니다.
   - requirement 응답이 cycle=0만 갖고 있고 associations 쪽에 Test Case가 있는 경우를 보정합니다.

2. AIO Test Case 카운트 로직을 보강했습니다.
   - testCases 배열
   - testCases.total/count 구조
   - associatedTestCases/linkedCases 계열
   - testCaseCount/totalTestCases 계열
   - TC 키 패턴
   - cycle totalTests fallback
   위 후보 중 최대값을 실제 TC 개수로 사용합니다.

3. PR 검출 로직을 보강했습니다.
   - bitbucketserver 후보 추가
   - dev-status detail 내부 pullRequests를 재귀 탐색
   - repository/branch/detail 하위 pullRequests 누락 방지
   - get_pr_merge_ok는 get_pr_merge_status 결과를 기준으로 단일화했습니다.

4. 난이도 정규식 제어문자 버그를 수정했습니다.
   - r'\b([ABCD])\b' 사용

## 검증

```bash
python3 -m py_compile jira_client.py ui.py
```

추가 단위 시뮬레이션:
- testCases 6개 + cycle 0개 => 6
- testCases.total = 6 => 6
- associatedTestCases 3개 => 3
- testCaseCount = 6 => 6
- cycle totalTests fallback => 4
- nested pullRequests => MERGED/OPEN 추출
