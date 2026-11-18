"""Download the AA model list and save a trimmed snapshot if displayed values changed.

Always writes data/last_checked.json on success. Writes skip=true to $GITHUB_OUTPUT
when the last check was less than settings.min_hours_between_checks ago and --force
is not given (so a code push right after a scheduled run doesn't spend API calls).
Snapshots are named by local date; a second change on the same day overwrites that day's file.

Usage: AA_API_KEY=... python scripts/fetch.py [--force]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aa


def set_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def run(force: bool, data_dir: Path, config_dir: Path, key: str | None,
        now: datetime | None = None, fetcher=aa.fetch_all) -> int:
    slate, _axes, settings = aa.load_config(config_dir)
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo(settings["timezone"])).date()
    last_path = data_dir / "last_checked.json"
    last = aa.read_json(last_path, {}) or {}

    gap = timedelta(hours=settings.get("min_hours_between_checks", 2))
    checked_at = last.get("checked_at")
    if checked_at and not force:
        since = now - datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
        if since < gap:
            print(f"Checked {since.total_seconds() / 3600:.1f} h ago ({checked_at}); nothing to do. "
                  "Use --force to re-fetch.")
            set_output("skip", "true")
            return 0
    if not key:
        print("::error::AA_API_KEY is not set")
        return 1

    try:
        records, meta, rate = fetcher(key)
    except aa.ApiError as e:
        msg = str(e) + (f" (Retry-After: {e.retry_after})" if e.retry_after else "")
        print(f"::error::{msg}")
        return 1

    kept = aa.trim(records, slate, settings, today)
    version = meta.get("intelligence_index_version")
    fp = aa.fingerprint(kept, version)

    snaps_dir = data_dir / "snapshots"
    existing = aa.snapshot_paths(snaps_dir)
    prev_fp = (aa.read_json(existing[-1]) or {}).get("fingerprint") if existing else None

    written = None
    if fp != prev_fp:
        snap_path = snaps_dir / f"{today.isoformat()}.json"
        aa.write_json(snap_path, {
            "schema": 1,
            "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tier": meta.get("tier"),
            "intelligence_index_version": version,
            "fingerprint": fp,
            "records": kept,
        })
        written = snap_path.name

    aa.write_json(last_path, {
        "date": today.isoformat(),
        "checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tier": meta.get("tier"),
        "intelligence_index_version": version,
        "fingerprint": fp,
        "snapshot_written": written,
        "records_total": len(records),
        "records_kept": len(kept),
        "rate_limit": rate,
    })
    print(f"Fetched {len(records)} records, kept {len(kept)}; "
          + (f"wrote {written}" if written else "no displayed values changed"))
    set_output("skip", "false")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="re-fetch even if checked recently")
    ap.add_argument("--data-dir", type=Path, default=aa.DATA_DIR)
    ap.add_argument("--config-dir", type=Path, default=aa.CONFIG_DIR)
    args = ap.parse_args(argv)
    return run(args.force, args.data_dir, args.config_dir, os.environ.get("AA_API_KEY"))


if __name__ == "__main__":
    sys.exit(main())
