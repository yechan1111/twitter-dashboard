import unittest
from briefing.verification import verify_post, rank_post, select_candidates


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.post = {"url": "https://x.com/example/status/1234567890123456789",
                     "id": "1234567890123456789", "created_at": "2026-10-13T10:00:00Z",
                     "views": 100000, "likes": 120, "reposts": 15, "replies": 105}

    def test_verified_threshold(self):
        result = verify_post(self.post)
        self.assertTrue(result["verified_100k"])
        self.assertEqual(result["metrics"]["views"], 100000)

    def test_missing_metrics_are_unknown(self):
        post = {**self.post, "views": None, "likes": "120"}
        result = verify_post(post)
        self.assertIsNone(result["metrics"]["views"])
        self.assertIsNone(result["metrics"]["likes"])
        self.assertFalse(result["verified_100k"])

    def test_reject_search_and_article_urls(self):
        for url in ("https://x.com/search?q=bitcoin", "https://example.com/article",
                    "https://x.com/a/status/1234567890123456789?fake=1"):
            self.assertFalse(verify_post({**self.post, "url": url})["verified_url"])

    def test_reject_id_mismatch(self):
        self.assertFalse(verify_post({**self.post, "id": "987654321"})["verified_url"])

    def test_ranking_prioritizes_replies_and_small_accounts(self):
        popular = {**self.post, "followers": 200, "views": 150000}
        quiet = {**self.post, "followers": 20000, "views": 150000, "replies": 1}
        self.assertGreater(rank_post(popular, 200), rank_post(quiet, 20000))

    def test_deduplicates(self):
        self.assertEqual(len(select_candidates([self.post, self.post])), 1)


if __name__ == "__main__":
    unittest.main()
