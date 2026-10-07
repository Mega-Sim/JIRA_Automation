import threading
import unittest

from sccb_app.jira_client import JiraClient


def make_client():
    client = JiraClient.__new__(JiraClient)
    client.base_url = "https://jira.example.com"
    client.timeout = 30
    client._tls = threading.local()
    return client


class LinkValidationTests(unittest.TestCase):
    def test_function_requirement_accepts_separator_variants(self):
        client = make_client()
        links = [{
            "outwardIssue": {
                "key": "REQ-10",
                "fields": {
                    "issuetype": {"name": "Function-Requirement"},
                    "summary": "기능 요구사항",
                },
            }
        }]

        result = client.get_link_validation("AMVCS30-56", links=links)

        self.assertTrue(result["function_requirement_ok"])
        self.assertNotIn("Function Requirement", result["missing"])

    def test_missing_embedded_issuetype_is_refetched_by_linked_issue_key(self):
        client = make_client()
        calls = []

        def fake_get(path, params=None):
            calls.append((path, params))
            if path == "/rest/api/2/issue/REQ-20":
                return {
                    "key": "REQ-20",
                    "fields": {
                        "issuetype": {"name": "Function Requirement"},
                        "summary": "실제 Function Requirement",
                    },
                }
            return {}

        client.get = fake_get
        links = [{
            "type": {"name": "Relates", "inward": "relates to", "outward": "relates to"},
            "outwardIssue": {
                "key": "REQ-20",
                "fields": {"summary": "embedded 응답에는 issuetype 없음"},
            },
        }]

        result = client.get_link_validation("AMVCS30-56", links=links)

        self.assertTrue(result["function_requirement_ok"])
        self.assertNotIn("Function Requirement", result["missing"])
        self.assertTrue(any(x["source"] == "issue-detail" for x in result["checked"]))
        self.assertTrue(any(path == "/rest/api/2/issue/REQ-20" for path, _ in calls))

    def test_link_trace_contains_final_reason(self):
        client = make_client()
        links = [{
            "outwardIssue": {
                "key": "REQ-30",
                "fields": {"issuetype": {"name": "Function Requirement"}},
            }
        }]
        client.get = lambda path, params=None: {}

        client.clear_trace()
        client.get_link_validation("AMVCS30-56", links=links)
        trace = client.pop_trace()

        self.assertTrue(any("Function Requirement=OK" in line for line in trace))
        self.assertTrue(any("[링크]" in line for line in trace))


if __name__ == "__main__":
    unittest.main()
