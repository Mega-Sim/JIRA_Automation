import sys
import unittest
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


class ClearReviewOpinionColumnsTests(unittest.TestCase):
    def test_clears_opinion_columns_only_in_data_rows(self):
        result = JiraClient._clear_sccb_review_opinion_columns(REVIEW_BODY)

        # 데이터 행의 '유관 부서 변경 부분'/'반영여부' 열 텍스트는 지워진다
        self.assertNotIn("특이점 없음", result)
        self.assertNotIn("Task 자동으로 선택할 수 있는 정보나 센서가 없어서", result)
        self.assertNotIn("<td><p>반영</p></td>", result)
        # 헤더와 나머지 열(#~Hot Fix)은 그대로 유지된다
        self.assertIn("유관 부서 변경 부분", result)
        self.assertIn("반영여부", result)
        self.assertIn("품질 담당자", result)
        self.assertIn("AMVCS30-79", result)
        self.assertIn("한지훈/Jihun Han", result)
        self.assertIn("UI 기능 신규 추가", result)
        self.assertIn("<td><p>NO</p></td>", result)

    def test_body_without_review_table_is_unchanged(self):
        body = "<p>사전SCCB 검토 의견</p><p>표 없음</p>"
        self.assertEqual(body, JiraClient._clear_sccb_review_opinion_columns(body))

    def test_table_without_opinion_headers_is_unchanged(self):
        body = (
            "<p>사전SCCB 검토 의견</p>"
            '<table><tbody><tr><td colspan="2">'
            '<ac:structured-macro ac:name="jira" /></td></tr></tbody></table>'
        )
        self.assertEqual(body, JiraClient._clear_sccb_review_opinion_columns(body))


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


class FillReviewTableFromReferenceTests(unittest.TestCase):
    def test_replaces_data_rows_with_reference_issues(self):
        client = make_client()
        rows = JiraClient._extract_reference_jira_issue_rows(REFERENCE_VIEW)
        result = client._fill_sccb_review_table_from_reference(REVIEW_BODY, rows)

        # 기존 데이터 행은 교체된다
        self.assertNotIn("AMVCS30-79", result)
        self.assertNotIn("MapUpdate 이력 조회 기능", result)
        # 새 행: 01부터 자동 번호, 유형~Hot Fix 열 채움
        self.assertIn("<td><p>01</p></td>", result)
        self.assertIn("<td><p>02</p></td>", result)
        self.assertIn("설비 Data 취합에 대한 편의성 확보", result)
        self.assertIn("<td><p>YES</p></td>", result)
        # 키는 Jira 링크로 들어간다
        self.assertIn('<a href="https://jira.example.com/browse/AMOHTV80F-2287">AMOHTV80F-2287</a>', result)
        # 담당자는 '/'와 영문 이름을 제외한다
        self.assertIn("<td><p>신재효</p></td>", result)
        self.assertIn("<td><p>김철수</p></td>", result)
        self.assertNotIn("Jaehyo", result)
        # 유관 부서 변경 부분/반영여부 열은 빈 칸으로 남는다
        self.assertIn("<td><p /></td>", result)
        # 헤더는 유지된다
        self.assertIn("유관 부서 변경 부분", result)
        self.assertIn("SCCB 등급 사유", result)

    def test_no_reference_rows_leaves_body_unchanged(self):
        client = make_client()
        self.assertEqual(REVIEW_BODY, client._fill_sccb_review_table_from_reference(REVIEW_BODY, []))


if __name__ == "__main__":
    unittest.main()
