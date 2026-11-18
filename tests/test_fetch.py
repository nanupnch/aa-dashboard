import contextlib
import copy
import io
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import aa  # noqa: E402
import fetch  # noqa: E402

FX = Path(__file__).resolve().parent / "fixtures"
SETTINGS = aa.read_json(aa.CONFIG_DIR / "settings.json")


def quiet(fn, *args, **kwargs):
    """Run without printing: fetch.py's "::error::" lines would become CI error annotations."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def response():
    return aa.read_json(FX / "sample_response.json")


class Trim(unittest.TestCase):
    def setUp(self):
        self.slate = aa.read_json(FX / "slate.json")
        self.kept = aa.trim(response()["data"], self.slate, SETTINGS, date(2026, 11, 11))
        self.names = {r["name"] for r in self.kept}

    def test_keeps_slate_and_recent_tracked(self):
        self.assertIn("Claude Opus 5.5 (Max)", self.names)
        self.assertIn("Gemini 4.1 Neon (High)", self.names)
        self.assertIn("Gemini 3.1 Pro (High)", self.names)  # recent enough for the window

    def test_drops_untracked_creator_and_fields(self):
        self.assertNotIn("GLM-5.3 (High)", self.names)
        self.assertTrue(all("unused_field" not in r and "id" not in r for r in self.kept))

    def test_window_excludes_old_models(self):
        kept = aa.trim(response()["data"], self.slate, SETTINGS, date(2027, 6, 1))
        self.assertNotIn("Gemini 4.1 Neon (High)", {r["name"] for r in kept})
        self.assertIn("Claude Opus 5.5 (Max)", {r["name"] for r in kept})  # slate always kept


class Fingerprint(unittest.TestCase):
    def test_stable_under_jitter_changes_beyond_precision(self):
        recs = response()["data"][:3]
        fp = aa.fingerprint(recs, "5.0.1")
        jitter = copy.deepcopy(recs)
        jitter[0]["performance_v3"]["mean_time_to_first_answer_token_seconds"] += 0.01
        self.assertEqual(fp, aa.fingerprint(jitter, "5.0.1"))
        moved = copy.deepcopy(recs)
        moved[0]["performance_v3"]["mean_time_to_first_answer_token_seconds"] *= 2
        self.assertNotEqual(fp, aa.fingerprint(moved, "5.0.1"))
        self.assertNotEqual(fp, aa.fingerprint(recs, "5.1.0"))


class FetchRun(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = self.tmp / "config"
        shutil.copytree(aa.CONFIG_DIR, self.cfg)
        shutil.copy(FX / "slate.json", self.cfg / "slate.json")
        self.data = self.tmp / "data"
        self.calls = 0

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fetcher(self, key):
        self.calls += 1
        r = response()
        return r["data"], {"tier": r["tier"], "intelligence_index_version": r["intelligence_index_version"]}, {}

    def run_at(self, iso, force=False):
        now = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
        return quiet(fetch.run, force, self.data, self.cfg, "key", now=now, fetcher=self.fetcher)

    def test_snapshot_only_on_change_and_interval_guard(self):
        self.assertEqual(self.run_at("2026-11-11T21:43:00"), 0)
        self.assertEqual(len(list((self.data / "snapshots").glob("*.json"))), 1)
        # Under 2 hours later (e.g. a code push): skipped, no API call.
        self.assertEqual(self.run_at("2026-11-11T23:30:00"), 0)
        self.assertEqual(self.calls, 1)
        # Next 3-hourly run, same data: checked, but no new snapshot.
        self.assertEqual(self.run_at("2026-11-12T00:43:00"), 0)
        self.assertEqual(self.calls, 2)
        self.assertEqual(len(list((self.data / "snapshots").glob("*.json"))), 1)
        last = aa.read_json(self.data / "last_checked.json")
        self.assertEqual(last["date"], "2026-11-11")   # still Nov 11 in Los Angeles
        self.assertIsNone(last["snapshot_written"])
        # Force re-runs inside the gap.
        self.assertEqual(self.run_at("2026-11-12T01:00:00", force=True), 0)
        self.assertEqual(self.calls, 3)

    def test_same_day_change_overwrites_that_days_snapshot(self):
        self.run_at("2026-11-11T18:43:00")
        orig = self.fetcher
        def changed(key):
            recs, meta, rate = orig(key)
            recs[0]["performance_v3"]["mean_time_to_first_answer_token_seconds"] *= 2
            return recs, meta, rate
        quiet(fetch.run, False, self.data, self.cfg, "key",
              now=datetime(2026, 11, 11, 21, 43, tzinfo=timezone.utc), fetcher=changed)
        snaps = list((self.data / "snapshots").glob("*.json"))
        self.assertEqual([p.name for p in snaps], ["2026-11-11.json"])
        self.assertEqual(aa.read_json(snaps[0])["fetched_at"], "2026-11-11T21:43:00Z")

    def test_pacific_date_used(self):
        # 03:00 UTC on Nov 12 is still Nov 11 in Los Angeles.
        self.run_at("2026-11-12T03:00:00")
        self.assertEqual(aa.read_json(self.data / "last_checked.json")["date"], "2026-11-11")

    def test_api_error_writes_nothing(self):
        def bad(key):
            raise aa.ApiError(401, "nope")
        rc = quiet(fetch.run, False, self.data, self.cfg, "key",
                   now=datetime(2026, 11, 11, 21, 43, tzinfo=timezone.utc), fetcher=bad)
        self.assertEqual(rc, 1)
        self.assertFalse(self.data.exists())

    def test_missing_key(self):
        rc = quiet(fetch.run, False, self.data, self.cfg, None,
                   now=datetime(2026, 11, 11, 21, 43, tzinfo=timezone.utc), fetcher=self.fetcher)
        self.assertEqual(rc, 1)
        self.assertEqual(self.calls, 0)


if __name__ == "__main__":
    unittest.main()
