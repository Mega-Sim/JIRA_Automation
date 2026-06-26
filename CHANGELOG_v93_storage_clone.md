# v93: SCCB Page 생성 — 실제 Confluence 9.2 복사 UI에 맞춘 포맷 복제

## 수정 배경

SEMES Confluence 9.2의 실제 페이지 복사 창을 확인한 결과, 복사 창은 서버 제출 form이 아니라 아래 구조의 **클라이언트 측 다이얼로그**입니다.

- 복사 창: `#copy-page-dialog`
- form action: `#`
- 실행 버튼: `#copy-dialog-next`
- 공간 입력: `#copy-destination-space`
- 상위 페이지 입력: `#copy-destination-page`

따라서 v92의 `copypage.action -> docopypage.action` form 제출 방식은 이 Confluence 화면 구조와 맞지 않아, 복사 화면의 hidden form을 추정해 호출하는 방식은 제거했습니다.

## v93 동작

1. 이번 주 SCCB 원본 페이지의 Confluence **storage format** 본문을 조회합니다.
2. 표, 셀 병합, 색상, 레이아웃, Jira 매크로 등 원본 storage 구조를 그대로 유지합니다.
3. 본문에서 원본 주간 제목 및 정확히 일치하는 주간 기간만 다음 주 기준으로 교체합니다.
4. 새 제목/새 상위 페이지 아래에 storage 본문을 한 번의 Confluence REST 페이지 생성 요청으로 복제합니다.
5. 복사 후 본문을 다시 PUT으로 덮어쓰지 않습니다. 즉, 생성 시점에 포맷과 다음 주 날짜가 함께 반영됩니다.

## 적용 범위

- 첨부파일이 없는 SCCB 주간 페이지를 대상으로 합니다.
- 유지: 표, 셀 병합, 색상, 페이지 레이아웃, Jira/Confluence 매크로, 원본 본문 구조
- 유지하지 않음: 첨부파일, 댓글, 이력, watcher, 페이지 제한, 개별 페이지 메타데이터

## 주차/상위 페이지 규칙

- 주간 범위는 월요일~일요일입니다.
  - `26W (06/22~06/29)` -> `27W (06/29~07/05)`
- 연도 경계에서는 `YY년 사전 SCCB 기록` 상위 페이지를 기존 연도 페이지와 같은 레벨에 생성 또는 재사용합니다.
  - 예: `53W (12/28~01/03)` -> `27년 사전 SCCB 기록` 아래 `1W (01/04~01/10)`
- 같은 상위 페이지 아래 같은 주차 제목이 이미 있으면 새 페이지를 만들지 않고 기존 URL을 반환합니다.

## 검증

`python -m unittest discover -s tests -v`

- 8건 통과
- 주간 월~일 계산
- 연도 전환 및 상위 페이지 생성/재사용
- 원본 storage 복제
- 표/셀 병합/스타일/Jira 매크로 유지
- 본문 날짜 최소 치환
- 중복 생성 방지
- 생성 URL fallback
