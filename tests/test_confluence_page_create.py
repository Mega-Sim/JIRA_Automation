import sys
import re
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sccb_app.jira_client import JiraClient


class FakeJiraClient(JiraClient):
    def __init__(self, source_page, existing_by_title=None, json_post_responses=None):
        super().__init__("https://jira.example.com", "user", "password")
        self.source_page = source_page
        self.existing_by_title = existing_by_title or {}
        self.json_post_responses = list(json_post_responses or [])
        self.json_posts = []
        self.puts = []
        self.get_calls = []

    def _get_json_absolute(self, url, params=None):
        self.get_calls.append((url, params))
        if re.search(r"/rest/api/content/\d+$", url):
            return self.source_page
        if url.endswith("/rest/api/content"):
            title = (params or {}).get("title")
            return {"results": self.existing_by_title.get(title, [])}
        raise AssertionError(f"unexpected GET: {url} / {params}")

    def _post_json_absolute(self, url, json_body):
        self.json_posts.append((url, json_body))
        if self.json_post_responses:
            return self.json_post_responses.pop(0)
        title = json_body.get("title") or ""
        if title.endswith("사전 SCCB 기록") or re.fullmatch(r"\d{2}년 회의록 \(SCCB\)", title):
            return {"id": "300", "_links": {"webui": "/spaces/AMHSSW/pages/300/year-parent"}}
        return {"id": "9001", "_links": {"webui": "/spaces/AMHSSW/pages/9001/copied-week"}}

    def _put_json_absolute(self, url, json_body):
        self.puts.append((url, json_body))
        raise AssertionError("v93은 생성 후 PUT으로 본문을 재작성하면 안 됩니다.")


class WeeklyConfluencePageTests(unittest.TestCase):
    @staticmethod
    def _source(title="26W (06/22~06/29)", parent_id="200", parent_title="26년 사전 SCCB 기록"):
        return {
            "id": "504216057",
            "title": title,
            "space": {"key": "AMHSSW"},
            "ancestors": [{"id": "100"}, {"id": parent_id, "title": parent_title}],
            "body": {
                "storage": {
                    "value": (
                        "<h1>26W (06/22~06/29)</h1>"
                        "<p>사전SCCB 검토 의견 2026. 6. 22. ~ 2026. 6. 29.</p>"
                        "<table><tbody><tr><td colspan=\"2\" style=\"background-color: #deebff;\">"
                        "<ac:structured-macro ac:name=\"jira\" />"
                        "</td></tr></tbody></table>"
                        "<p>이슈 희망일: 2026. 6. 25.</p>"
                    ),
                    "representation": "storage",
                }
            },
            "_links": {"webui": "/spaces/AMHSSW/pages/504216057/source-week"},
        }

    def test_next_week_title_is_monday_to_sunday(self):
        title, info = JiraClient._make_next_week_title(
            "26W (06/22~06/29)", reference_date=date(2026, 6, 26)
        )
        self.assertEqual("27W (06/29~07/05)", title)
        self.assertEqual(date(2026, 6, 22), info["start"])
        self.assertEqual(date(2026, 6, 29), info["end"])
        self.assertEqual(date(2026, 6, 29), info["next_start"])
        self.assertEqual(date(2026, 7, 5), info["next_end"])
        self.assertEqual(27, info["next_week"])

    def test_year_boundary_uses_1w_and_monday_to_sunday_range(self):
        title, info = JiraClient._make_next_week_title(
            "53W (12/28~01/03)", reference_date=date(2026, 12, 30)
        )
        self.assertEqual("1W (01/04~01/10)", title)
        self.assertEqual(date(2026, 12, 28), info["start"])
        self.assertEqual(date(2027, 1, 4), info["next_start"])
        self.assertEqual(date(2027, 1, 10), info["next_end"])
        self.assertEqual("27년 사전 SCCB 기록", JiraClient._make_yearly_sccb_parent_title(2027))

    def test_body_week_range_replacement_is_limited_to_matching_week_range(self):
        body = (
            "<p>26W (06/22~06/29)</p>"
            "<p>사전SCCB 검토 의견 2026. 6. 22. ~ 2026. 6. 29.</p>"
            "<p>2026-06-22 ~ 2026-06-29</p>"
            "<p>06/22 ~ 06/29</p>"
            "<p>이슈 희망일: 2026. 6. 25.</p>"
        )
        result = JiraClient._replace_weekly_date_ranges_in_body(
            body=body,
            old_title="26W (06/22~06/29)",
            new_title="27W (06/29~07/05)",
            old_start=date(2026, 6, 22),
            old_end=date(2026, 6, 29),
            new_start=date(2026, 6, 29),
            new_end=date(2026, 7, 5),
        )
        self.assertIn("27W (06/29~07/05)", result)
        self.assertIn("2026. 6. 29. ~ 2026. 7. 5.", result)
        self.assertIn("2026-06-29 ~ 2026-07-05", result)
        self.assertIn("06/29~07/05", result)
        self.assertIn("이슈 희망일: 2026. 6. 25.", result)

    def test_handy_date_macro_range_moves_to_next_week_without_touching_plain_issue_dates(self):
        body = (
            '<h1>사전SCCB 검토 의견 '
            '<span hd-id="0"><time datetime="2026-06-22" class="date-past handy-date-time">'
            '<span class="handy-date-value">2026. 6. 22.</span></time></span> ~ '
            '<span hd-id="1"><time datetime="2026-06-29" class="date-upcoming handy-date-time">'
            '<span class="handy-date-value">2026. 6. 29.</span></time></span></h1>'
            '<ac:structured-macro ac:name="handy-date" ac:schema-version="1">'
            '<ac:parameter ac:name="date">2026-06-22</ac:parameter>'
            '</ac:structured-macro>'
            '<p>이슈 희망일: 2026-06-22.</p>'
        )
        result = JiraClient._replace_weekly_date_ranges_in_body(
            body=body,
            old_title="26W (06/22~06/29)",
            new_title="27W (06/29~07/05)",
            old_start=date(2026, 6, 22),
            old_end=date(2026, 6, 29),
            new_start=date(2026, 6, 29),
            new_end=date(2026, 7, 5),
        )
        self.assertIn('datetime="2026-06-29"', result)
        self.assertIn('datetime="2026-07-05"', result)
        self.assertIn('handy-date-value">2026. 6. 29.</span>', result)
        self.assertIn('handy-date-value">2026. 7. 5.</span>', result)
        self.assertIn('<ac:parameter ac:name="date">2026-06-29</ac:parameter>', result)
        # 일반 본문/표의 단일 일정은 이번 주 범위 표시가 아니므로 유지한다.
        self.assertIn('이슈 희망일: 2026-06-22.', result)

    def test_create_page_clones_source_storage_and_preserves_formatting(self):
        client = FakeJiraClient(
            self._source(),
            existing_by_title={"27W (06/29~07/05)": []},
        )
        result = client.create_next_week_confluence_page_from_url(
            "https://conf-stms.semes.com:18090/spaces/AMHSSW/pages/504216057/26W"
        )

        self.assertTrue(result["created"])
        self.assertEqual("storage_clone", result["copy_mode"])
        self.assertEqual(1, len(client.json_posts))
        _, payload = client.json_posts[0]
        self.assertEqual("27W (06/29~07/05)", payload["title"])
        self.assertEqual({"key": "AMHSSW"}, payload["space"])
        self.assertEqual([{"id": "200"}], payload["ancestors"])
        storage = payload["body"]["storage"]
        self.assertEqual("storage", storage["representation"])
        self.assertIn("2026. 6. 29. ~ 2026. 7. 5.", storage["value"])
        self.assertIn('colspan="2"', storage["value"])
        self.assertIn('style="background-color: #deebff;"', storage["value"])
        self.assertIn('<ac:structured-macro ac:name="jira" />', storage["value"])
        self.assertIn("이슈 희망일: 2026. 6. 25.", storage["value"])
        self.assertEqual([], client.puts)
        self.assertIn("/spaces/AMHSSW/pages/9001/copied-week", result["url"])

    def test_created_page_url_falls_back_to_page_id_when_api_link_is_missing(self):
        client = FakeJiraClient(
            self._source(),
            existing_by_title={"27W (06/29~07/05)": []},
            json_post_responses=[{"id": "9001"}],
        )
        result = client.create_next_week_confluence_page_from_url(
            "https://conf-stms.semes.com:18090/spaces/AMHSSW/pages/504216057/26W"
        )
        self.assertEqual(
            "https://conf-stms.semes.com:18090/pages/viewpage.action?pageId=9001",
            result["url"],
        )

    def test_existing_same_sibling_is_returned_without_creating_duplicate(self):
        client = FakeJiraClient(
            self._source(),
            existing_by_title={
                "27W (06/29~07/05)": [
                    {
                        "id": "already-created",
                        "ancestors": [{"id": "200"}],
                        "_links": {"webui": "/spaces/AMHSSW/pages/already-created/27W"},
                    },
                ]
            },
        )
        result = client.create_next_week_confluence_page_from_url(
            "https://conf-stms.semes.com:18090/spaces/AMHSSW/pages/504216057/26W"
        )
        self.assertFalse(result["created"])
        self.assertEqual("already-created", result["id"])
        self.assertEqual([], client.json_posts)
        self.assertEqual([], client.puts)

    def test_year_change_creates_year_parent_then_clones_1w_below_it(self):
        source = self._source("53W (12/28~01/03)")
        source["body"]["storage"]["value"] = (
            "<p>53W (12/28~01/03)</p>"
            "<p>2026. 12. 28. ~ 2027. 1. 3.</p>"
            "<table><tbody><tr><td><ac:structured-macro ac:name=\"jira\" /></td></tr></tbody></table>"
        )
        client = FakeJiraClient(
            source,
            existing_by_title={"27년 사전 SCCB 기록": [], "1W (01/04~01/10)": []},
            json_post_responses=[
                {"id": "300", "_links": {"webui": "/spaces/AMHSSW/pages/300/year-parent"}},
                {"id": "9001", "_links": {"webui": "/spaces/AMHSSW/pages/9001/1W"}},
            ],
        )
        result = client.create_next_week_confluence_page_from_url(
            "https://conf-stms.semes.com:18090/spaces/AMHSSW/pages/504216057/53W"
        )

        self.assertTrue(result["created"])
        self.assertTrue(result["yearly_parent_created"])
        self.assertEqual("27년 사전 SCCB 기록", result["yearly_parent_title"])
        self.assertEqual(2, len(client.json_posts))
        _, year_parent_payload = client.json_posts[0]
        self.assertEqual("27년 사전 SCCB 기록", year_parent_payload["title"])
        self.assertEqual([{"id": "100"}], year_parent_payload["ancestors"])
        _, weekly_payload = client.json_posts[1]
        self.assertEqual("1W (01/04~01/10)", weekly_payload["title"])
        self.assertEqual([{"id": "300"}], weekly_payload["ancestors"])
        self.assertIn("2027. 1. 4. ~ 2027. 1. 10.", weekly_payload["body"]["storage"]["value"])
        self.assertIn('<ac:structured-macro ac:name="jira" />', weekly_payload["body"]["storage"]["value"])

    def test_year_change_reuses_existing_year_parent_before_cloning(self):
        source = self._source("53W (12/28~01/03)")
        client = FakeJiraClient(
            source,
            existing_by_title={
                "27년 사전 SCCB 기록": [
                    {
                        "id": "300",
                        "title": "27년 사전 SCCB 기록",
                        "ancestors": [{"id": "100"}],
                        "_links": {"webui": "/year-parent"},
                    },
                ],
                "1W (01/04~01/10)": [],
            },
        )
        result = client.create_next_week_confluence_page_from_url(
            "https://conf-stms.semes.com:18090/spaces/AMHSSW/pages/504216057/53W"
        )

        self.assertTrue(result["created"])
        self.assertFalse(result["yearly_parent_created"])
        self.assertEqual(1, len(client.json_posts))
        _, weekly_payload = client.json_posts[0]
        self.assertEqual([{"id": "300"}], weekly_payload["ancestors"])


class MeetingMinutesConfluencePageTests(unittest.TestCase):
    @staticmethod
    def _source(
        title="25W (06/22일) - 5건(A0, B3, C2)",
        parent_id="500",
        parent_title="26년 회의록 (SCCB)",
    ):
        return {
            "id": "504216938",
            "title": title,
            "space": {"key": "AMHSSW"},
            "ancestors": [{"id": "100"}, {"id": parent_id, "title": parent_title}],
            "body": {
                "storage": {
                    "value": (
                        "<h1>25W (06/22일) - 5건(A0, B3, C2)</h1>"
                        "<table><tbody><tr><td>날짜/시간</td><td>"
                        "<span hd-id=\"0\"><time datetime=\"2026-06-22\" "
                        "class=\"date-past handy-date-time\">"
                        "<span class=\"handy-date-value\">2026. 6. 22.</span>"
                        "</time></span>&nbsp; 10:00 ~ 11:00 AM"
                        "</td></tr></tbody></table>"
                        "<h1>3. SCCB 리스트</h1>"
                        "<ac:structured-macro ac:name=\"jira\" ac:schema-version=\"1\">"
                        "<ac:parameter ac:name=\"jqlQuery\">"
                        "(category = AMHS_SW OR category = AMHS) "
                        "AND (&quot;SCCB 완료일&quot; &gt;= 2026-06-22) "
                        "AND (&quot;SCCB 완료일&quot; &lt;= 2026-06-25) "
                        "AND created &gt;= 2023-01-01 AND status = &quot;Complete&quot;"
                        "</ac:parameter>"
                        "<ac:parameter ac:name=\": = | RAW | = :\">"
                        "jqlQuery=(&quot;SCCB 완료일&quot; &gt;= 2026-06-22) "
                        "AND (&quot;SCCB 완료일&quot; &lt;= 2026-06-25)"
                        "</ac:parameter>"
                        "</ac:structured-macro>"
                        "<p>과거 이슈 일정: 2026. 6. 22.</p>"
                    ),
                    "representation": "storage",
                }
            },
            "_links": {"webui": "/spaces/AMHSSW/pages/504216938/source-meeting"},
        }

    def test_next_meeting_title_moves_date_and_keeps_suffix(self):
        title, info = JiraClient._make_next_meeting_minutes_title(
            "25W (06/22일) - 5건(A0, B3, C2)",
            parent_title="26년 회의록 (SCCB)",
        )
        self.assertEqual("26W (06/29일) - 5건(A0, B3, C2)", title)
        self.assertEqual(date(2026, 6, 22), info["date"])
        self.assertEqual(date(2026, 6, 29), info["next_date"])
        self.assertEqual(26, info["next_week"])

    def test_meeting_body_moves_datetime_and_only_sccb_completion_dates_in_jql(self):
        source = self._source()
        body = source["body"]["storage"]["value"]
        result = JiraClient._replace_meeting_minutes_body(
            body=body,
            old_title="25W (06/22일) - 5건(A0, B3, C2)",
            new_title="26W (06/29일) - 5건(A0, B3, C2)",
            old_meeting_date=date(2026, 6, 22),
            new_meeting_date=date(2026, 6, 29),
        )
        self.assertIn("26W (06/29일) - 5건(A0, B3, C2)", result)
        self.assertIn('datetime="2026-06-29"', result)
        self.assertIn('handy-date-value">2026. 6. 29.</span>', result)
        # jqlQuery와 RAW 파라미터 양쪽에서 SCCB 완료일 범위가 7일 이동한다.
        self.assertEqual(2, result.count("SCCB 완료일&quot; &gt;= 2026-06-29"))
        self.assertEqual(2, result.count("SCCB 완료일&quot; &lt;= 2026-07-02"))
        # SCCB 완료일이 아닌 고정 기준/일반 표 일정은 바꾸지 않는다.
        self.assertIn("created &gt;= 2023-01-01", result)
        self.assertIn("과거 이슈 일정: 2026. 6. 22.", result)

    def test_meeting_page_clones_storage_with_date_and_jql_updates(self):
        client = FakeJiraClient(
            self._source(),
            existing_by_title={"26W (06/29일) - 5건(A0, B3, C2)": []},
        )
        result = client.create_next_week_meeting_minutes_page_from_url(
            "https://conf-stms.semes.com:18090/spaces/AMHSSW/pages/504216938/25W"
        )
        self.assertTrue(result["created"])
        self.assertEqual("storage_clone", result["copy_mode"])
        self.assertEqual(1, len(client.json_posts))
        _, payload = client.json_posts[0]
        self.assertEqual("26W (06/29일) - 5건(A0, B3, C2)", payload["title"])
        self.assertEqual([{"id": "500"}], payload["ancestors"])
        storage = payload["body"]["storage"]["value"]
        self.assertIn('datetime="2026-06-29"', storage)
        self.assertIn("SCCB 완료일&quot; &gt;= 2026-06-29", storage)
        self.assertIn("SCCB 완료일&quot; &lt;= 2026-07-02", storage)

    def test_meeting_year_change_creates_new_year_parent_and_1w(self):
        source = self._source(
            title="52W (12/29일) - 1건(C)",
            parent_title="26년 회의록 (SCCB)",
        )
        source["body"]["storage"]["value"] = (
            "<h1>52W (12/29일) - 1건(C)</h1>"
            "<table><tbody><tr><td>날짜/시간</td><td>"
            '<time datetime="2026-12-29" class="handy-date-time">'
            '<span class="handy-date-value">2026. 12. 29.</span></time>'
            "</td></tr></tbody></table>"
        )
        client = FakeJiraClient(
            source,
            existing_by_title={
                "27년 회의록 (SCCB)": [],
                "1W (01/05일) - 1건(C)": [],
            },
            json_post_responses=[
                {"id": "300", "_links": {"webui": "/spaces/AMHSSW/pages/300/year-parent"}},
                {"id": "9001", "_links": {"webui": "/spaces/AMHSSW/pages/9001/1W"}},
            ],
        )
        result = client.create_next_week_meeting_minutes_page_from_url(
            "https://conf-stms.semes.com:18090/spaces/AMHSSW/pages/504216938/52W"
        )
        self.assertTrue(result["created"])
        self.assertTrue(result["yearly_parent_created"])
        self.assertEqual("27년 회의록 (SCCB)", result["yearly_parent_title"])
        self.assertEqual(2, len(client.json_posts))
        _, parent_payload = client.json_posts[0]
        self.assertEqual("27년 회의록 (SCCB)", parent_payload["title"])
        _, page_payload = client.json_posts[1]
        self.assertEqual("1W (01/05일) - 1건(C)", page_payload["title"])
        self.assertEqual([{"id": "300"}], page_payload["ancestors"])
        self.assertIn('datetime="2027-01-05"', page_payload["body"]["storage"]["value"])


if __name__ == "__main__":
    unittest.main()
