import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import aa  # noqa: E402
import build  # noqa: E402

FX = Path(__file__).resolve().parent / "fixtures"
_, AXES, SETTINGS = aa.load_config()
TOL = SETTINGS["tolerance"]


def v(i, c, t):
    return {"intelligence": i, "cost": c, "time": t}


class Dominance(unittest.TestCase):
    def test_clear_domination(self):
        self.assertTrue(build.dominates(v(42, 0.2, 7), v(41, 0.7, 9), TOL))

    def test_within_tolerance_is_not_domination(self):
        self.assertFalse(build.dominates(v(43, 0.69, 8.9), v(42, 0.7, 9), TOL))

    def test_worse_on_one_measure(self):
        self.assertFalse(build.dominates(v(50, 0.2, 10), v(41, 0.7, 9), TOL))

    def test_missing_value_never_dominates(self):
        self.assertFalse(build.dominates(v(60, 0.1, None), v(41, 0.7, 9), TOL))
        self.assertFalse(build.dominates(v(60, 0.1, 1), v(41, 0.7, None), TOL))


def mv(slug, effort, i, c):
    return {"slug": slug, "effort": effort, "status": "ok", "intelligence": i, "cost": c, "time": 1.0,
            "merged_into": None, "merged": []}


class MergeClose(unittest.TestCase):
    def test_close_scores_merge_into_cheaper(self):
        vs = [mv("a-high", "high", 54.8, 3.24), mv("a-xhigh", "xhigh", 55.3, 3.66), mv("a-max", "max", 56.9, 6.24)]
        build.merge_close(vs, TOL)
        self.assertEqual(vs[1]["merged_into"], "a-high")
        self.assertEqual(vs[0]["merged"], ["xhigh"])
        self.assertIsNone(vs[2]["merged_into"])  # 2.1 points above the kept High: not close

    def test_cheaper_lower_effort_absorbs_pricier(self):
        vs = [mv("l-none", "non-reasoning", 9.4, 0.0714), mv("l-low", "low", 9.7, 0.0051)]
        build.merge_close(vs, TOL)
        self.assertEqual(vs[0]["merged_into"], "l-low")

    def test_missing_cost_or_score_never_merges(self):
        vs = [mv("f-low", "low", 30.0, None), mv("f-med", "medium", 30.1, 6.2), mv("f-x", "high", None, 7.0)]
        build.merge_close(vs, TOL)
        self.assertTrue(all(v["merged_into"] is None for v in vs))


class QuickPicks(unittest.TestCase):
    def test_merges_and_has_reasons(self):
        snaps = build.load_snapshots(FX / "snapshots")
        latest = build.build_latest(snaps, {}, aa.read_json(FX / "slate.json"), AXES, SETTINGS)
        qp = latest["quick_picks"]
        ids = [p["id"] for e in qp for p in e["picks"]]
        self.assertIn("smartest", ids)
        self.assertEqual(len({e["slug"] for e in qp}), len(qp))        # one entry per variant
        self.assertTrue(all(p["why"] for e in qp for p in e["picks"]))

    def test_knee_needs_three_frontier_points(self):
        m = {"key": "m", "variants": [mv("a", "low", 10, 0.1), mv("b", "high", 20, 1.0)]}
        self.assertIsNone(build._knee([(m, v) for v in m["variants"]], "cost", TOL))


class Changed(unittest.TestCase):
    def test_tolerances(self):
        self.assertFalse(build.changed("intelligence", 50, 52, TOL))
        self.assertTrue(build.changed("intelligence", 50, 53, TOL))
        self.assertFalse(build.changed("cost", 1.0, 1.09, TOL))
        self.assertTrue(build.changed("cost", 1.0, 0.85, TOL))
        self.assertFalse(build.changed("time", 5.0, 5.3, TOL))
        self.assertTrue(build.changed("time", None, 5.0, TOL))


class BuildLatest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.slate = aa.read_json(FX / "slate.json")
        cls.snaps = build.load_snapshots(FX / "snapshots")
        cls.latest = build.build_latest(cls.snaps, {}, cls.slate, AXES, SETTINGS)
        cls.models = {m["display_name"]: m for m in cls.latest["models"]}
        build.validate(cls.latest)

    def variant(self, model, effort):
        return next(x for x in self.models[model]["variants"] if x["effort"] == effort)

    def test_all_models_and_effort_order(self):
        self.assertEqual(len(self.models), 10)
        self.assertEqual([x["effort"] for x in self.models["Claude Opus 5.5"]["variants"]],
                         ["low", "medium", "high", "max"])

    def test_duplicate_effort_keeps_unnoted_row(self):
        self.assertEqual(self.variant("Claude Opus 5.5", "max")["slug"], "claude-opus-5-5-max")
        self.assertEqual(self.latest["health"]["duplicates"][0]["dropped"], "claude-opus-5-5-max-default-fallback")
        self.assertNotIn("claude-opus-5-5-max-default-fallback",
                         [u["slug"] for u in self.latest["health"]["unconfirmed"]])

    def test_unconfirmed_variant(self):
        x = self.variant("GPT-6.1 Terra", "xhigh")
        self.assertEqual(x["status"], "unconfirmed")
        self.assertEqual(self.latest["health"]["unconfirmed"][0]["slug"], "gpt-6-1-terra-xhigh")

    def test_missing_variant_keeps_last_known(self):
        x = self.variant("GPT-6 Astra", "medium")
        self.assertEqual((x["status"], x["as_of"]), ("stale", "2026-11-10"))

    def test_dominance_within_subscription(self):
        self.assertEqual(self.variant("Gemini 4 Argon", "low")["dominated_by"]["label"], "Gemini 3.8 Flash (medium)")
        self.assertIsNone(self.variant("Gemini 3.8 Flash", "medium")["dominated_by"])

    def test_candidates(self):
        names = [c["name"] for c in self.latest["health"]["candidates"]]
        self.assertEqual(names, ["Gemini 4.1 Neon"])

    def test_capabilities_exclude_headline_index(self):
        caps = self.variant("Claude Opus 5.5", "max")["capabilities"]
        self.assertNotIn(aa.INTELLIGENCE_KEY, caps)
        self.assertEqual(len(caps), 6)

    def test_ignored_models_are_not_candidates(self):
        slate = copy.deepcopy(self.slate)
        slate["ignored"] = ["Gemini 4.1 Neon"]
        latest = build.build_latest(self.snaps, {}, slate, AXES, SETTINGS)
        self.assertEqual(latest["health"]["candidates"], [])

    def test_not_tracked_model_is_omitted(self):
        slate = copy.deepcopy(self.slate)
        slate["models"].append({"key": "ghost", "display_name": "Ghost 1", "subscription": "Claude Pro",
                                "tier_role": "Small", "aa_name": "Ghost 1", "slugs": []})
        latest = build.build_latest(self.snaps, {}, slate, AXES, SETTINGS)
        self.assertEqual(latest["health"]["not_tracked"], ["Ghost 1"])
        self.assertNotIn("Ghost 1", [m["display_name"] for m in latest["models"]])

    def test_out_of_range_is_clamped_and_warned(self):
        axes = copy.deepcopy(AXES)
        axes["5"]["cost"]["max"] = 5
        latest = build.build_latest(self.snaps, {}, self.slate, axes, SETTINGS)
        astra = next(m for m in latest["models"] if m["display_name"] == "GPT-6 Astra")
        self.assertIn("cost", next(x for x in astra["variants"] if x["effort"] == "xhigh")["clamped"])
        self.assertTrue(any("off scale" in w for w in latest["health"]["warnings"]))

    def test_unknown_index_major_fails(self):
        snaps = copy.deepcopy(self.snaps)
        snaps[-1][1]["intelligence_index_version"] = "6.0.0"
        with self.assertRaises(build.BuildError):
            build.build_latest(snaps, {}, self.slate, AXES, SETTINGS)


class Changes(unittest.TestCase):
    def setUp(self):
        self.slate = aa.read_json(FX / "slate.json")
        self.snaps = build.load_snapshots(FX / "snapshots")

    def test_events(self):
        ev = build.build_changes(self.snaps, self.slate, SETTINGS)
        kinds = sorted((e["type"], e.get("slug") or e.get("model")) for e in ev)
        self.assertEqual(kinds, [
            ("measure_changed", "claude-sonnet-5-5-high"),
            ("new_model", "Gemini 4.1 Neon"),
            ("variant_added", "gpt-6-1-terra-xhigh"),
            ("variant_removed", "gpt-6-astra-medium"),
        ])  # Luna's +6% wait is within tolerance: no event

    def test_version_change_suppresses_intelligence_diffs(self):
        snaps = copy.deepcopy(self.snaps)
        snaps[-1][1]["intelligence_index_version"] = "5.1.0"
        for r in snaps[-1][1]["records"]:
            r["evaluations"]["artificial_analysis_intelligence_index"] += 5
        ev = build.build_changes(snaps, self.slate, SETTINGS)
        self.assertIn("index_version", [e["type"] for e in ev])
        self.assertFalse(any(e.get("measure") == "intelligence" for e in ev))


if __name__ == "__main__":
    unittest.main()
