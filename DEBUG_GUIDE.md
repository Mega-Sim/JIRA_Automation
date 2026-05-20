# 디버그 모드 사용 가이드

## 개요

이 버전은 "사유 작성" 판정이 왜 나오는지 추적할 수 있도록 **상세한 디버깅 로그**를 출력하는 버전입니다.

## 디버그 로그가 출력되는 위치

### 1. `get_error_table_status` 함수
- 연관 에러 검증 과정 전체를 추적
- 표에 데이터가 있는지 확인
- 사유 텍스트 검증 과정

### 2. `get_design_rollout_status` 함수
- 설계 횡전개 대상 검증 과정 전체를 추적
- 표에 데이터가 있는지 확인
- 사유 텍스트 검증 과정

### 3. `_has_reason_text_around_table` 함수
- **가장 상세한 디버그 로그 출력**
- ADF(JSON) 파싱 vs HTML 파싱 경로 추적
- 각 블록/텍스트 처리 과정 단계별 출력

## 디버그 로그 출력 예시

```
================================================================================
[DEBUG STATUS] get_error_table_status 시작
[DEBUG STATUS] issue_key: PROJ-1234
================================================================================
[DEBUG STATUS] get_error_table_ok 결과: False
[DEBUG STATUS] 표가 비어있음. 사유 텍스트 확인 중...

================================================================================
[DEBUG] _has_reason_text_around_table 시작
[DEBUG] heading_candidates: ['연관 에러', '연관에러']
[DEBUG] desc 타입: <class 'dict'>
[DEBUG] desc는 ADF(JSON) 형식입니다
================================================================================

[DEBUG] is_error_section: True
[DEBUG] end_candidates: ['설계 횡전개 대상', '설계횡전개대상']

[DEBUG] ADF(JSON) 파싱 시도
[DEBUG ADF] 전체 블록 수: 15
[DEBUG ADF] start_norms: ['연관에러', '연관에러']
[DEBUG ADF] end_norms: ['설계횡전개대상', '설계횡전개대상']
[DEBUG ADF] 섹션 범위: 블록[3] ~ 블록[7]

[DEBUG ADF] (1) 헤딩 라인 검사:
  - head_raw: '.연관 에러'
  - head_raw2 (불릿 제거): '연관 에러'
  - head_norm (정규화): '연관에러'
  - rem (키워드 제거 후): ''
  - rem (특수문자 제거 후): ''
  → 헤딩에는 의미있는 텍스트 없음

[DEBUG ADF] (2) 섹션 범위 내 블록 검사:

  블록[4] 타입: table
    → 표 블록이므로 건너뜀

  블록[5] 타입: paragraph
    raw: '   '
    → 빈 텍스트, 건너뜀

  블록[6] 타입: paragraph
    raw: '해당사항 없음'
    raw2 (불릿 제거): '해당사항 없음'
    norm (정규화): '해당사항없음'
    cleaned (특수문자 제거): '해당사항없음'
    → 의미있는 텍스트 발견! '사유 작성' 반환

[DEBUG] ADF 파싱 결과: True

[DEBUG STATUS] _has_reason_text_around_table 결과: True
[DEBUG STATUS] 최종 결과: '사유 작성'
```

## 사용 방법

### 방법 1: 테스트 스크립트 사용 (권장)

1. `test_debug.py` 스크립트 실행:
```bash
python test_debug.py PROJ-1234
```

2. Jira 접속 정보 입력:
```
Jira URL (예: https://jira.company.com): https://jira.yourcompany.com
사용자 ID: your.username
비밀번호: ********
```

3. 테스트 선택:
```
1. 연관 에러 검증
2. 설계 횡전개 대상 검증
3. 둘 다 실행
q. 종료
```

### 방법 2: 코드에서 직접 호출

```python
from sccb_app.jira_client import JiraClient

client = JiraClient(
    base_url="https://jira.yourcompany.com",
    user="your.username",
    password="your.password"
)

# 디버그 모드로 호출
status = client.get_error_table_status("PROJ-1234", debug=True)
```

### 방법 3: 기존 코드에 debug 파라미터 추가

기존 코드에서 `get_error_table_status` 또는 `get_design_rollout_status`를 호출하는 부분에 `debug=True`를 추가:

```python
# 기존 코드
status = client.get_error_table_status(issue_key)

# 디버그 모드
status = client.get_error_table_status(issue_key, debug=True)
```

## 디버그 로그 분석 방법

### 1. "사유 작성"이 나오는 경우

로그를 보고 **어느 단계에서 True를 반환했는지** 확인:

- **(1) 헤딩 라인 검사**에서 True?
  → 헤딩에 키워드 외에 다른 텍스트가 있음
  → 예: `.연관 에러 해당사항 없음`

- **(2) 섹션 범위 내 블록 검사**에서 True?
  → 표 이후에 의미있는 텍스트가 있음
  → 로그에서 `블록[N]`의 내용 확인

### 2. 잘못된 "사유 작성" 판정 찾기

**FAIL이어야 하는데 "사유 작성"이 나오는 경우:**

1. 디버그 로그에서 True를 반환한 블록 찾기
2. 해당 블록의 `raw`, `raw2`, `cleaned` 값 확인
3. 어떤 텍스트가 "의미있는 텍스트"로 오인되었는지 파악

**예시:**
```
블록[5] 타입: paragraph
  raw: '...'
  raw2 (불릿 제거): '...'
  norm (정규화): ''
  cleaned (특수문자 제거): ''
  → 특수문자만 있음, 건너뜀  ← 정상
```

vs

```
블록[5] 타입: paragraph
  raw: '해당'
  raw2 (불릿 제거): '해당'
  norm (정규화): '해당'
  cleaned (특수문자 제거): '해당'
  → 의미있는 텍스트 발견! '사유 작성' 반환  ← 문제!
```

### 3. HTML 파싱 경로 확인

ADF가 아닌 HTML 파싱 경로를 타는 경우:

```
[DEBUG] HTML/Wiki 파싱 시도
[DEBUG HTML] plain 텍스트 길이: 1234
[DEBUG HTML] plain 미리보기 (처음 200자): ...
[DEBUG HTML] section_cleaned: '...'
```

`section_cleaned`에 어떤 텍스트가 남아있는지 확인

## 로그 파일로 저장

터미널 출력을 파일로 저장하려면:

```bash
python test_debug.py PROJ-1234 > debug_log.txt 2>&1
```

또는 Python 코드에서:

```python
import sys

# 로그를 파일로 리다이렉트
with open('debug_log.txt', 'w', encoding='utf-8') as f:
    sys.stdout = f
    sys.stderr = f
    
    status = client.get_error_table_status(issue_key, debug=True)
    
    # 원래대로 복원
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
```

## 주의사항

1. **debug=True는 프로덕션 환경에서 사용 금지**
   - 로그가 매우 많이 출력되어 성능에 영향
   - 테스트/디버깅 용도로만 사용

2. **민감한 정보**
   - 로그에 이슈 내용이 출력될 수 있음
   - 로그 파일 공유 시 주의

3. **성능**
   - debug 모드는 일반 모드보다 느림
   - 대량 검증 시 debug=False 사용

## 문제 해결 프로세스

1. **문제 재현**
   - 문제가 되는 이슈 키로 `test_debug.py` 실행
   - 디버그 로그 전체를 파일로 저장

2. **로그 분석**
   - True를 반환한 정확한 위치 찾기
   - 해당 블록의 원본 텍스트 확인

3. **코드 수정**
   - 문제가 되는 조건 파악
   - 필터링 로직 추가/수정

4. **재테스트**
   - 수정 후 동일 이슈로 다시 테스트
   - FAIL이 나오는지 확인

## 문의

디버그 로그를 보고도 문제를 파악하기 어려운 경우:
1. 디버그 로그 전체를 저장
2. 문제가 되는 이슈의 스크린샷
3. 기대하는 결과 (OK/FAIL/사유 작성)

위 정보와 함께 문의해주세요.
