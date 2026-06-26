# CHANGELOG v78 - AIO Test / P/R 병합 / 글자수 ERR 보정

## 수정 항목

### Fix 1: get_issue_core / get_issues_core_batch - `id` 필드 누락
- `fields` 파라미터에 `id` 미포함으로 `issue_id`가 항상 빈 문자열이었음
- PR 검증 시 issue_id 재조회 이중 호출 → 간헐적 ERR 원인
- `"description,issuelinks"` → `"id,description,issuelinks"` 로 수정

### Fix 2: looks_like_pr 백슬래시 오타 (★ PR ERR 핵심 원인)
- `if not isinstance(d, dict):\` 에 백슬래시 line-continuation 오타
- looks_like_pr이 항상 False 반환 → walk에서 PR을 하나도 못 잡음
- any_error=True, total_pr=0 → "ERR" 반환되던 문제 수정

### Fix 3: looks_like_pr 조건 강화
- branch/repository dict를 PR로 오탐하던 문제 차단
- PR 고유 단서(pull URL, pull type, pullRequestId 키) 필수화

### Fix 4: AIO html fallback 조건 확장
- `best <= 0` → `best < min(DIFFICULTY_MIN_CASES.values())` (=2)
- API에서 약한 값(cycle_total=1 등)이 잡혀도 html 재확인

### Fix 5: timeout 항목별 분리
- tc, pr_merge → 45초 / 나머지 → 15초 (기존 10초 일괄)

### Fix 6: _extract_aio_cycle_totals_from_payload 전체 후보 반환
- best 1개만 반환 → 양수 후보 전부 반환, 상위에서 최대값 채택

### Fix 7: GitLab merge_request 계열 키 추가
- walk에서 mergerequests / mergerequest / mrs / mr 키 처리

### Fix 8: 글자수 ERR → "확인불가" 표시
- get_body_length_string_from_ui가 "" 반환 시 `or 'ERR'`에서 ERR 출력되던 문제
- 빈 문자열 = API 조회 실패이므로 "확인불가"로 구분 표시

### Fix 9: PR None vs ERR 구분
- _safe_call 예외 시 None → 기존 `pr_res or 'ERR'`는 NONE 문자열도 ERR로 만들 수 있음
- `pr_res if pr_res is not None else 'ERR'` 로 변경
- PR 없음(NONE)과 조회 실패(ERR)를 명확히 구분
