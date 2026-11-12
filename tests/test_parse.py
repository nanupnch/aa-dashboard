import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import aa  # noqa: E402

SETTINGS = aa.read_json(aa.CONFIG_DIR / "settings.json")


class ParseName(unittest.TestCase):
    def test_effort_and_note(self):
        self.assertEqual(aa.parse_name("Claude Opus 5.5 (Max, Default Fallback)", SETTINGS),
                         ("Claude Opus 5.5", "max", "Default Fallback"))

    def test_plain_effort(self):
        self.assertEqual(aa.parse_name("Gemini 4 Argon (High)", SETTINGS), ("Gemini 4 Argon", "high", None))

    def test_alias(self):
        self.assertEqual(aa.parse_name("GPT-6.1 Sol (X-High)", SETTINGS)[1], "xhigh")

    def test_no_brackets_is_default_effort(self):
        self.assertEqual(aa.parse_name("Gemini 3.5 Flash-Lite", SETTINGS), ("Gemini 3.5 Flash-Lite", "default", None))

    def test_real_labels(self):
        self.assertEqual(aa.parse_name("GPT-6.1 Terra (Xhigh)", SETTINGS)[1], "xhigh")
        self.assertEqual(aa.parse_name("GPT-6 Luna (Non-reasoning)", SETTINGS)[1], "non-reasoning")

    def test_unknown_label_sorts_last(self):
        efforts = ["max", "adaptive", "low", "non-reasoning", "high"]
        ranked = sorted(efforts, key=lambda e: aa.effort_rank(e, SETTINGS))
        self.assertEqual(ranked, ["non-reasoning", "low", "high", "max", "adaptive"])

    def test_matching_cannot_collide(self):
        self.assertFalse(aa.matches("GPT-6.1 Sol (High)", "GPT-6 Sol"))
        self.assertTrue(aa.matches("GPT-6 Sol (High)", "GPT-6 Sol"))
        self.assertTrue(aa.matches("Gemini 3.5 Flash-Lite", "Gemini 3.5 Flash-Lite"))
        self.assertFalse(aa.matches("Gemini 3.5 Flash-Lite", "Gemini 3.5 Flash"))
        self.assertTrue(aa.matches("Gemini 3.5 Flash (High)", "Gemini 3.5 Flash"))


class Numbers(unittest.TestCase):
    def test_sig(self):
        self.assertEqual(aa.sig(0.01234), 0.012)
        self.assertEqual(aa.sig(41.27), 41.0)
        self.assertEqual(aa.sig(1234), 1200.0)
        self.assertIsNone(aa.sig(None))

    def test_number_rejects_non_numbers(self):
        self.assertIsNone(aa.number(True))
        self.assertIsNone(aa.number("3"))
        self.assertIsNone(aa.number(float("nan")))
        self.assertEqual(aa.number(3), 3.0)


class Pagination(unittest.TestCase):
    def test_shapes(self):
        self.assertTrue(aa.has_next({"total_pages": 2}, 1, 200))
        self.assertFalse(aa.has_next({"total_pages": 2}, 2, 150))
        self.assertTrue(aa.has_next({"has_more": True}, 1, 200))
        self.assertFalse(aa.has_next({"next": None}, 1, 200))
        self.assertTrue(aa.has_next({"total": 401, "per_page": 200}, 2, 200))
        self.assertTrue(aa.has_next({}, 1, 200))
        self.assertFalse(aa.has_next({}, 2, 37))

    def test_fetch_all_pages_and_partial_failure(self):
        pages = {1: {"tier": "free", "intelligence_index_version": "5.0.1",
                     "pagination": {"total_pages": 2}, "data": [{"slug": "a"}]},
                 2: {"pagination": {"total_pages": 2}, "data": [{"slug": "b"}]}}

        def getter(url, key):
            return pages[int(url.rsplit("=", 1)[1])], {"X-RateLimit-Remaining": "98", "Other": "x"}

        recs, meta, rate = aa.fetch_all("k", getter=getter)
        self.assertEqual([r["slug"] for r in recs], ["a", "b"])
        self.assertEqual(meta["tier"], "free")
        self.assertEqual(rate, {"X-RateLimit-Remaining": "98"})

        def failing(url, key):
            if url.endswith("=2"):
                raise aa.ApiError(500, "boom")
            return getter(url, key)

        with self.assertRaises(aa.ApiError):
            aa.fetch_all("k", getter=failing)


if __name__ == "__main__":
    unittest.main()
