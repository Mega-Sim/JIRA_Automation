import unittest

from sccb_app.jira_client import JiraClient


def make_client():
    client = JiraClient.__new__(JiraClient)
    client.timeout = 30
    return client


def reviewer(user_id, *, approved=None, status=None, role="REVIEWER", slug=None):
    item = {
        "role": role,
        "user": {
            "id": user_id,
            "slug": slug or f"user{user_id}",
            "name": slug or f"user{user_id}",
        },
    }
    if approved is not None:
        item["approved"] = approved
    if status is not None:
        item["status"] = status
    return item


class PrReviewApprovalTests(unittest.TestCase):
    def test_two_distinct_approved_reviewers_are_ok(self):
        client = make_client()
        pr = {
            "status": "MERGED",
            "reviewers": [
                reviewer(101, approved=True),
                reviewer(202, status="APPROVED"),
            ],
        }

        result = client._review_approval_for_pr(pr, required_count=2)

        self.assertTrue(result["complete"])
        self.assertTrue(result["ok"])
        self.assertEqual(2, result["count"])

    def test_duplicate_same_user_across_containers_counts_once(self):
        client = make_client()
        same = reviewer(101, approved=True)
        pr = {
            "status": "MERGED",
            "participants": [same],
            "reviewers": [dict(same)],
        }

        result = client._review_approval_for_pr(pr, required_count=2)

        self.assertTrue(result["complete"])
        self.assertFalse(result["ok"])
        self.assertEqual(1, result["count"])

    def test_unapproved_is_not_misread_as_approved(self):
        client = make_client()
        pr = {
            "status": "MERGED",
            "reviewers": [
                reviewer(101, approved=False, status="UNAPPROVED"),
                reviewer(202, approved=False, status="UNAPPROVED"),
            ],
        }

        result = client._review_approval_for_pr(pr, required_count=2)

        self.assertTrue(result["complete"])
        self.assertFalse(result["ok"])
        self.assertEqual(0, result["count"])

    def test_approved_author_is_not_counted_as_reviewer(self):
        client = make_client()
        pr = {
            "status": "MERGED",
            "participants": [
                reviewer(101, approved=True, status="APPROVED", role="AUTHOR"),
                reviewer(202, approved=True, status="APPROVED", role="REVIEWER"),
            ],
        }

        result = client._review_approval_for_pr(pr, required_count=2)

        self.assertTrue(result["complete"])
        self.assertFalse(result["ok"])
        self.assertEqual(1, result["count"])

    def test_pr_identity_does_not_collide_across_repositories(self):
        pr_a = {
            "id": 56,
            "status": "MERGED",
            "repository": {"slug": "repo-a", "project": {"key": "AMHS"}},
        }
        pr_b = {
            "id": 56,
            "status": "MERGED",
            "repository": {"slug": "repo-b", "project": {"key": "AMHS"}},
        }

        self.assertNotEqual(
            JiraClient._pr_identity_key(pr_a),
            JiraClient._pr_identity_key(pr_b),
        )

    def test_direct_bitbucket_reads_require_same_result_twice(self):
        client = make_client()
        pr = {
            "id": 56,
            "status": "MERGED",
            "url": "https://bitbucket.example.com/projects/AMHS/repos/vcs/pull-requests/56/overview",
        }
        values = [
            reviewer(101, approved=True, status="APPROVED"),
            reviewer(202, approved=True, status="APPROVED"),
        ]
        calls = []

        def fake_fetch(_pr):
            calls.append(1)
            return values, None

        client._fetch_bitbucket_participants_once = fake_fetch

        result = client._review_approval_for_pr(pr, required_count=2)

        self.assertEqual(2, len(calls))
        self.assertTrue(result["complete"])
        self.assertTrue(result["ok"])
        self.assertEqual("bitbucket", result["source"])
        self.assertEqual(2, result["count"])

    def test_unstable_direct_reads_are_unknown_not_fail(self):
        client = make_client()
        pr = {
            "id": 56,
            "status": "MERGED",
            "url": "https://bitbucket.example.com/projects/AMHS/repos/vcs/pull-requests/56/overview",
        }
        samples = [
            [],
            [reviewer(101, approved=True, status="APPROVED")],
            [
                reviewer(101, approved=True, status="APPROVED"),
                reviewer(202, approved=True, status="APPROVED"),
            ],
        ]

        def fake_fetch(_pr):
            return samples.pop(0), None

        client._fetch_bitbucket_participants_once = fake_fetch

        result = client._review_approval_for_pr(pr, required_count=2)

        self.assertFalse(result["complete"])
        self.assertFalse(result["ok"])
        self.assertEqual("bitbucket_unstable", result["source"])

    def test_summary_reports_two_of_two_as_ok(self):
        client = make_client()
        pr = {
            "status": "MERGED",
            "reviewers": [
                reviewer(101, approved=True),
                reviewer(202, approved=True),
            ],
        }

        self.assertEqual(
            "리뷰승인 OK(2/2)",
            client._summarize_pr_review_approval([pr], required_count=2),
        )

    def test_missing_reviewer_data_is_unknown(self):
        client = make_client()
        pr = {"status": "MERGED", "id": 56}

        result = client._review_approval_for_pr(pr, required_count=2)

        self.assertFalse(result["complete"])
        self.assertFalse(result["ok"])
        self.assertEqual(
            "리뷰승인 N/A(확인불가)",
            client._summarize_pr_review_approval([pr], required_count=2),
        )


if __name__ == "__main__":
    unittest.main()
