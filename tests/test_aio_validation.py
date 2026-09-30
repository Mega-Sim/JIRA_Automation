import unittest

from sccb_app.jira_client import JiraClient
from sccb_app.ui import JiraSccbApp


def make_client(*, actual=None, difficulty=""):
    client = JiraClient.__new__(JiraClient)
    client._get_issue_meta_for_aio = lambda issue_key: ("12345", 10000)
    client._get_aio_actual_count = lambda issue_key, issue_id, project_id: (
        actual,
        {"mock": actual} if actual is not None else {},
    )
    client.get_issue_difficulty = lambda issue_key: difficulty
    return client


class AioValidationTests(unittest.TestCase):
    def test_no_level_and_no_count_is_na_not_error(self):
        client = make_client(actual=None, difficulty="")

        result = client.get_aio_test_validation("AMVCS30-56")

        self.assertEqual("N/A", result["verdict"])
        self.assertEqual("N/A(NO LEVEL)", result["status"])
        self.assertFalse(result["ok"])

    def test_api_unavailable_with_known_level_is_na_not_error(self):
        client = make_client(actual=None, difficulty="B")

        result = client.get_aio_test_validation("AMVCS30-56")

        self.assertEqual("N/A", result["verdict"])
        self.assertEqual("N/A(API/-/6)", result["status"])
        self.assertEqual(6, result["required"])
        self.assertFalse(result["ok"])

    def test_no_level_but_existing_testcases_is_ok(self):
        client = make_client(actual=3, difficulty="")

        result = client.get_aio_test_validation("AMVCS30-56")

        self.assertEqual("OK", result["verdict"])
        self.assertEqual("OK(3/-)", result["status"])
        self.assertTrue(result["ok"])

    def test_known_level_and_enough_testcases_is_ok(self):
        client = make_client(actual=6, difficulty="B")

        result = client.get_aio_test_validation("AMVCS30-56")

        self.assertEqual("OK", result["verdict"])
        self.assertEqual("OK(6/6)", result["status"])
        self.assertTrue(result["ok"])

    def test_known_level_and_insufficient_testcases_is_fail(self):
        client = make_client(actual=5, difficulty="B")

        result = client.get_aio_test_validation("AMVCS30-56")

        self.assertEqual("FAIL", result["verdict"])
        self.assertEqual("FAIL(5/6)", result["status"])
        self.assertFalse(result["ok"])


class AioUiGateTests(unittest.TestCase):
    def calc(self, aio_status):
        return JiraSccbApp._calc_row_result(
            None,
            "OK",
            "OK",
            "OK",
            "OK",
            aio_status,
            "MERGED(1) / 리뷰승인 OK(2/2)",
        )

    def test_aio_na_propagates_as_na_not_fail(self):
        self.assertEqual("N/A", self.calc("N/A(NO LEVEL)"))
        self.assertEqual("N/A", self.calc("N/A(API/-/6)"))

    def test_aio_real_fail_remains_fail(self):
        self.assertEqual("FAIL", self.calc("FAIL(5/6)"))

    def test_aio_ok_allows_row_ok(self):
        self.assertEqual("OK", self.calc("OK(6/6)"))


if __name__ == "__main__":
    unittest.main()
