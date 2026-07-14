import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sccb_app.jira_client import JiraClient


def make_client():
    client = JiraClient.__new__(JiraClient)
    client.base_url = "https://jira.example.com"
    return client


# 실제 주간 페이지처럼 2행 헤더(rowspan/colspan 병합) + 데이터 행 구조의 표
REVIEW_BODY = (
    "<h1>사전SCCB 검토 의견 2026. 7. 6. ~ 2026. 7. 12.</h1>"
    "<table><tbody>"
    "<tr>"
    '<th rowspan="2"><p>#</p></th>'
    '<th colspan="7"><p>o자동 구성</p></th>'
    '<th colspan="2"><p>유관 부서 변경 부분</p></th>'
    '<th rowspan="2"><p>반영여부</p></th>'
    "</tr>"
    "<tr>"
    "<th><p>유형</p></th><th><p>키</p></th><th><p>요약</p></th><th><p>담당자</p></th>"
    "<th><p>SCCB 등급</p></th><th><p>SCCB 등급 사유</p></th><th><p>Hot Fix</p></th>"
    "<th><p>품질 담당자</p></th><th><p>제조 담당자</p></th>"
    "</tr>"
    "<tr>"
    "<td><p>01</p></td><td><p>개선</p></td><td><p>AMVCS30-79</p></td>"
    "<td><p>MapUpdate 이력 조회 기능</p></td><td><p>한지훈/Jihun Han</p></td>"
    "<td><p>B</p></td><td><p>UI 기능 신규 추가</p></td><td><p>NO</p></td>"
    "<td><p>-</p></td><td><p>특이점 없음</p></td><td><p>반영</p></td>"
    "</tr>"
    "<tr>"
    "<td><p>02</p></td><td><p>신규</p></td><td><p>AMVCS30-76</p></td>"
    "<td><p>VCS Task Step 변경</p></td><td><p>한지훈/Jihun Han</p></td>"
    "<td><p>B</p></td><td><p>기능 개선</p></td><td><p>NO</p></td>"
    "<td><p>Task 자동으로 선택할 수 있는 정보나 센서가 없어서</p></td>"
    "<td><p>특이점 없음</p></td><td><p>반영</p></td>"
    "</tr>"
    "</tbody></table>"
    "<p>이후 본문</p>"
)

REFERENCE_VIEW = (
    "<h2>참조 지라 이슈 티켓</h2>"
    "<table><tbody>"
    "<tr>"
    "<th>유형</th><th>키</th><th>요약</th><th>담당자</th>"
    "<th>SCCB 등급</th><th>SCCB 등급 사유</th><th>Hot Fix</th>"
    "</tr>"
    "<tr>"
    "<td>개선</td><td><a href=\"https://jira.example.com/browse/AMOHTV80F-2287\">AMOHTV80F-2287</a></td>"
    "<td>설비 Data 취합에 대한 편의성 확보</td><td>신재효/Jaehyo Shin</td>"
    "<td>B</td><td>편의성 확보</td><td>NO</td>"
    "</tr>"
    "<tr>"
    "<td>신규</td><td>AMOHTV80F-2302</td>"
    "<td>설비 Data 취합</td><td>김철수/Chulsoo Kim</td>"
    "<td>A</td><td>UI 신규 추가</td><td>YES</td>"
    "</tr>"
    "</tbody></table>"
)

REFERENCE_VIEW_THREE_ROWS = REFERENCE_VIEW.replace(
    "</tbody></table>",
    "<tr>"
    "<td>개선</td><td>AMOHTV70S-520</td>"
    "<td>추가 이슈</td><td>박영희/Younghee Park</td>"
    "<td>C</td><td>기타</td><td>NO</td>"
    "</tr>"
    "</tbody></table>",
)


class ForceUpdateHeadingDatesTests(unittest.TestCase):
    NEW_START = date(2026, 7, 13)
    NEW_END = date(2026, 7, 19)

    def test_storage_date_macros_are_overwritten_even_when_dates_mismatch_title(self):
        # 제목 옆 날짜가 제목 주차(7/6~7/12)와 어긋나게 저장된 경우
        body = (
            "<h1>사전SCCB 검토 의견 "
            '<ac:structured-macro ac:name="handy-date" ac:schema-version="1">'
            '<ac:parameter ac:name="date">2026-07-01</ac:parameter>'
            "</ac:structured-macro> ~ "
            '<ac:structured-macro ac:name="handy-date" ac:schema-version="1">'
            '<ac:parameter ac:name="date">2026-07-07</ac:parameter>'
            "</ac:structured-macro></h1>"
            "<table><tbody><tr><td><p>이슈 희망일: 2026-07-01</p></td></tr></tbody></table>"
        )
        result = JiraClient._force_update_sccb_review_heading_dates(body, self.NEW_START, self.NEW_END)
        self.assertIn('<ac:parameter ac:name="date">2026-07-13</ac:parameter>', result)
        self.assertIn('<ac:parameter ac:name="date">2026-07-19</ac:parameter>', result)
        # 표 안의 날짜는 건드리지 않는다
        self.assertIn("이슈 희망일: 2026-07-01", result)

    def test_rendered_time_elements_are_overwritten_in_order(self):
        body = (
            "<h1>사전SCCB 검토 의견 "
            '<time datetime="2026-07-06" class="date-past handy-date-time">'
            '<span class="handy-date-value">2026. 7. 6.</span></time> ~ '
            '<time datetime="2026-07-12" class="date-upcoming handy-date-time">'
            '<span class="handy-date-value">2026. 7. 12.</span></time></h1>'
            "<table><tbody><tr><td><p>2026. 7. 6.</p></td></tr></tbody></table>"
        )
        result = JiraClient._force_update_sccb_review_heading_dates(body, self.NEW_START, self.NEW_END)
        self.assertIn('datetime="2026-07-13"', result)
        self.assertIn('datetime="2026-07-19"', result)
        self.assertIn('handy-date-value">2026. 7. 13.</span>', result)
        self.assertIn('handy-date-value">2026. 7. 19.</span>', result)
        # 표 안의 날짜는 유지된다
        self.assertIn("<td><p>2026. 7. 6.</p></td>", result)

    def test_plain_text_range_is_overwritten_when_no_macro(self):
        body = "<p>사전SCCB 검토 의견 2026. 7. 6. ~ 2026. 7. 12.</p><table><tbody></tbody></table>"
        result = JiraClient._force_update_sccb_review_heading_dates(body, self.NEW_START, self.NEW_END)
        self.assertIn("사전SCCB 검토 의견 2026. 7. 13. ~ 2026. 7. 19.", result)

    def test_body_without_marker_is_unchanged(self):
        body = "<p>다른 제목 2026. 7. 6. ~ 2026. 7. 12.</p>"
        self.assertEqual(
            body,
            JiraClient._force_update_sccb_review_heading_dates(body, self.NEW_START, self.NEW_END),
        )


class ExtractReferenceJiraRowsTests(unittest.TestCase):
    def test_extracts_rows_with_header_mapping(self):
        rows = JiraClient._extract_reference_jira_issue_rows(REFERENCE_VIEW)
        self.assertEqual(2, len(rows))
        self.assertEqual("AMOHTV80F-2287", rows[0]["키"])
        self.assertEqual("신재효/Jaehyo Shin", rows[0]["담당자"])
        self.assertEqual("B", rows[0]["sccb등급"])
        self.assertEqual("YES", rows[1]["hotfix"])

    def test_empty_when_macro_missing_or_no_rows(self):
        self.assertEqual([], JiraClient._extract_reference_jira_issue_rows("<p>다른 본문</p>"))
        empty_macro = (
            "<h2>참조 지라 이슈 티켓</h2>"
            "<table><tbody><tr><th>유형</th><th>키</th></tr></tbody></table>"
        )
        self.assertEqual([], JiraClient._extract_reference_jira_issue_rows(empty_macro))


class RefreshReviewTableTests(unittest.TestCase):
    def test_no_reference_rows_clears_from_type_column_and_keeps_numbering(self):
        client = make_client()
        result = client._refresh_sccb_review_table(REVIEW_BODY, [])

        # 표 구조와 1열(# 번호)은 유지된다
        self.assertIn("<td><p>01</p></td>", result)
        self.assertIn("<td><p>02</p></td>", result)
        self.assertIn("유관 부서 변경 부분", result)
        self.assertIn("반영여부", result)
        self.assertIn("품질 담당자", result)
        # '유형' 열부터 끝 열까지 지난 주 내용은 모두 지워진다
        self.assertNotIn("AMVCS30-79", result)
        self.assertNotIn("MapUpdate 이력 조회 기능", result)
        self.assertNotIn("한지훈", result)
        self.assertNotIn("UI 기능 신규 추가", result)
        self.assertNotIn("특이점 없음", result)
        self.assertNotIn("Task 자동으로 선택할 수 있는 정보나 센서가 없어서", result)
        self.assertNotIn("<td><p>반영</p></td>", result)
        self.assertNotIn("<td><p>NO</p></td>", result)

    def test_reference_rows_fill_existing_rows_in_place(self):
        client = make_client()
        rows = JiraClient._extract_reference_jira_issue_rows(REFERENCE_VIEW)
        result = client._refresh_sccb_review_table(REVIEW_BODY, rows)

        # 기존 행 자리(번호 유지)에 In-Verification 이슈가 채워진다
        self.assertIn("<td><p>01</p></td>", result)
        self.assertIn("<td><p>02</p></td>", result)
        self.assertNotIn("AMVCS30-79", result)
        self.assertIn("설비 Data 취합에 대한 편의성 확보", result)
        self.assertIn("<td><p>YES</p></td>", result)
        # 키는 Jira 링크로 들어간다
        self.assertIn('<a href="https://jira.example.com/browse/AMOHTV80F-2287">AMOHTV80F-2287</a>', result)
        # 담당자는 '/'와 영문 이름을 제외한다
        self.assertIn("<td><p>신재효</p></td>", result)
        self.assertIn("<td><p>김철수</p></td>", result)
        self.assertNotIn("Jaehyo", result)
        # 유관 부서 변경 부분/반영여부 열은 빈 칸으로 남는다
        self.assertNotIn("특이점 없음", result)
        self.assertIn("<td><p /></td>", result)

    def test_more_reference_rows_than_existing_appends_numbered_rows(self):
        client = make_client()
        rows = JiraClient._extract_reference_jira_issue_rows(REFERENCE_VIEW_THREE_ROWS)
        self.assertEqual(3, len(rows))
        result = client._refresh_sccb_review_table(REVIEW_BODY, rows)

        # 기존 2행을 채우고, 3번째 이슈는 번호를 이어 새 행으로 추가된다
        self.assertIn("<td><p>03</p></td>", result)
        self.assertIn('<a href="https://jira.example.com/browse/AMOHTV70S-520">AMOHTV70S-520</a>', result)
        self.assertIn("<td><p>박영희</p></td>", result)
        self.assertNotIn("Younghee", result)

    def test_fewer_reference_rows_leaves_remaining_rows_empty(self):
        client = make_client()
        rows = JiraClient._extract_reference_jira_issue_rows(REFERENCE_VIEW)[:1]
        result = client._refresh_sccb_review_table(REVIEW_BODY, rows)

        # 1행만 채워지고 2행은 번호만 남은 빈 행이 된다
        self.assertIn("<td><p>01</p></td>", result)
        self.assertIn("<td><p>02</p></td>", result)
        self.assertIn("AMOHTV80F-2287", result)
        self.assertNotIn("AMOHTV80F-2302", result)
        self.assertNotIn("AMVCS30-76", result)
        self.assertNotIn("VCS Task Step 변경", result)

    def test_body_without_review_table_is_unchanged(self):
        client = make_client()
        body = "<p>사전SCCB 검토 의견</p><p>표 없음</p>"
        self.assertEqual(body, client._refresh_sccb_review_table(body, []))

    def test_table_without_headers_is_unchanged(self):
        client = make_client()
        body = (
            "<p>사전SCCB 검토 의견</p>"
            '<table><tbody><tr><td colspan="2">'
            '<ac:structured-macro ac:name="jira" /></td></tr></tbody></table>'
        )
        self.assertEqual(body, client._refresh_sccb_review_table(body, []))


if __name__ == "__main__":
    unittest.main()
