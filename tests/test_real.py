"""Checks against trimmed real AA data (captured in the step-0 spike), so field paths and naming stay honest."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import aa  # noqa: E402
import build  # noqa: E402

FX = Path(__file__).resolve().parent / "fixtures"
SLATE, AXES, SETTINGS = aa.load_config()


class RealSample(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        blob = aa.read_json(FX / "real_sample.json")
        cls.meta, cls.records = blob["meta"], blob["records"]
        cls.slugs = {r["slug"] for r in cls.records}

    def test_every_slate_model_and_pinned_slug_present(self):
        for m in SLATE["models"]:
            self.assertTrue(m["slugs"], f"{m['display_name']} has no pinned slugs")
            for s in m["slugs"]:
                self.assertIn(s, self.slugs)

    def test_field_paths_resolve(self):
        missing = [(r["slug"], k) for r in self.records for k, v in aa.measures_of(r).items() if v is None]
        # Only known gap at capture time: Gemini 3.8 Flash (Low) has no cost per task.
        self.assertEqual(missing, [("gemini-3-8-flash-low", "cost")])
        for r in self.records:
            self.assertIsNotNone(aa.creator_of(r))
            self.assertIsNotNone(aa.get_path(r, aa.FIELDS["price_input"]))

    def test_effort_labels_known(self):
        known = set(SETTINGS["effort_order"]) | {"default"}
        unknown = {aa.parse_name(r["name"], SETTINGS)[1] for r in self.records} - known
        self.assertEqual(unknown, set())

    def test_builds_within_axes(self):
        snap = {"intelligence_index_version": self.meta["intelligence_index_version"], "tier": "free",
                "fetched_at": "2026-11-12T19:00:00Z", "records": self.records}
        latest = build.build_latest([("2026-11-12", snap)], {}, SLATE, AXES, SETTINGS)
        build.validate(latest)
        self.assertEqual(len(latest["models"]), len(SLATE["models"]))
        self.assertFalse([w for w in latest["health"]["warnings"] if "off scale" in w])
        self.assertEqual(latest["health"]["unconfirmed"], [])


    def test_close_variants_merged_on_real_data(self):
        snap = {"intelligence_index_version": self.meta["intelligence_index_version"], "tier": "free",
                "fetched_at": "2026-11-12T19:00:00Z", "records": self.records}
        latest = build.build_latest([("2026-11-12", snap)], {}, SLATE, AXES, SETTINGS)
        merged = {v["slug"]: v["merged_into"] for m in latest["models"] for v in m["variants"] if v["merged_into"]}
        self.assertEqual(merged["gpt-6-astra-xhigh"], "gpt-6-astra-high")         # 55.3 vs 54.8
        self.assertEqual(merged["claude-opus-5-5"], "claude-opus-5-5-xhigh")       # 59.1 vs 57.4
        self.assertEqual(merged["gemini-3-8-flash"], "gemini-3-8-flash-medium")    # 30.8 vs 30.1
        self.assertEqual(merged["gpt-6-luna-non-reasoning"], "gpt-6-luna-low")     # 9.4 vs 9.7, Low cheaper
        self.assertNotIn("gpt-6-1-sol", merged)                                    # steps of 2.1+ points
        for m in latest["models"]:
            for v in m["variants"]:
                if v["dominated_by"]:
                    self.assertFalse(v["merged_into"])

    def test_quick_picks_on_real_data(self):
        snap = {"intelligence_index_version": self.meta["intelligence_index_version"], "tier": "free",
                "fetched_at": "2026-11-12T19:00:00Z", "records": self.records}
        latest = build.build_latest([("2026-11-12", snap)], {}, SLATE, AXES, SETTINGS)
        picks = {p["id"]: e["slug"] for e in latest["quick_picks"] for p in e["picks"]}
        self.assertEqual(picks["smartest"], "claude-opus-5-5-xhigh")   # 57.4; Max (59.1) merged into it
        self.assertEqual(picks["value"], "gpt-6-1-sol-medium")         # flat cost knee; cheapest within noise
        self.assertEqual(picks["fast"], "gpt-6-1-sol-medium")
        self.assertEqual(picks["dominant"], "gpt-6-1-sol-medium")
        self.assertEqual(picks["budget"], "gpt-6-1-terra-high")
        sol = next(e for e in latest["quick_picks"] if e["slug"] == "gpt-6-1-sol-medium")
        self.assertEqual(len(sol["picks"]), 3)                          # one card, three labels
        self.assertGreater(sol["beats"], 0)

if __name__ == "__main__":
    unittest.main()
