"""Step-0 spike and slate helper.

Shows, for each slate model, the AA rows whose name is its aa_name or aa_name + ' (…)', checks
that every field the dashboard needs is present, and suggests axis ranges.

  AA_API_KEY=... python scripts/discover.py --save spike/response.json   # 1-3 API calls
  python scripts/discover.py --from-file spike/response.json             # offline re-runs
  python scripts/discover.py --from-file spike/response.json --write     # pin all matched slugs
  python scripts/discover.py --from-file spike/response.json --search "opus"

spike/ is git-ignored: the full response must not be published.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import aa


def load_records(args):
    if args.from_file:
        blob = aa.read_json(args.from_file)
        return blob["records"], blob.get("meta", {}), blob.get("first_page_keys", [])
    key = os.environ.get("AA_API_KEY")
    if not key:
        sys.exit("Set AA_API_KEY or use --from-file.")
    first_keys = []

    def getter(url, k):
        body, headers = aa._get(url, k)
        if not first_keys:
            first_keys.extend(sorted(body.keys()))
            print("Top-level keys:", first_keys)
            print("Pagination block:", json.dumps(body.get("pagination")))
        return body, headers

    records, meta, rate = aa.fetch_all(key, getter=getter)
    print("Rate-limit headers:", rate)
    if args.save:
        aa.write_json(args.save, {"meta": meta, "first_page_keys": first_keys, "records": records})
        print(f"Saved {len(records)} records to {args.save}")
    return records, meta, first_keys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Discover AA rows for the slate.")
    ap.add_argument("--save", type=Path)
    ap.add_argument("--from-file", type=Path)
    ap.add_argument("--write", action="store_true", help="pin every matched slug into config/slate.json")
    ap.add_argument("--search", help="case-insensitive substring to list matching names")
    args = ap.parse_args(argv)

    slate, _axes, settings = aa.load_config()
    records, meta, _ = load_records(args)
    print(f"\n{len(records)} records · tier={meta.get('tier')} · index version={meta.get('intelligence_index_version')}")

    if args.search:
        q = args.search.lower()
        for r in sorted(records, key=lambda r: r.get("name") or ""):
            if q in (r.get("name") or "").lower():
                print(f"  {r.get('name'):<55} {r.get('slug')}")
        return 0

    missing_fields = {f: 0 for f in aa.FIELDS}
    values = {m: [] for m in aa.MEASURES}
    not_tracked = []
    for m in slate["models"]:
        rows = [r for r in records if aa.matches(r.get("name"), m["aa_name"])]
        rows.sort(key=lambda r: aa.effort_rank(aa.parse_name(r["name"], settings)[1], settings))
        print(f"\n{m['display_name']}  ({m['subscription']}, {m['tier_role']})")
        if not rows:
            print("  — no rows: AA does not track this model (it will be left off the page)")
            not_tracked.append(m["display_name"])
            continue
        for r in rows:
            _, effort, note = aa.parse_name(r["name"], settings)
            ms = aa.measures_of(r)
            for f, path in aa.FIELDS.items():
                if aa.get_path(r, path) is None and not (f == "creator" and aa.creator_of(r)):
                    missing_fields[f] += 1
            for k, v in ms.items():
                if v is not None:
                    values[k].append(v)
            flag = "" if effort in settings["effort_order"] else "  <- effort label not in effort_order"
            print(f"  {r['name']:<50} slug={r.get('slug'):<35} effort={effort} note={note} "
                  f"I={ms['intelligence']} $={ms['cost']} t={ms['time']}{flag}")
        if args.write:
            m["slugs"] = [r["slug"] for r in rows]

    print("\nField coverage across matched rows (count missing):")
    for f, n in missing_fields.items():
        print(f"  {f:<14} {aa.FIELDS[f]:<70} missing={n}")
    print("\nObserved ranges (for config/axes.json, add headroom):")
    for k, vs in values.items():
        if vs:
            print(f"  {k:<13} min={min(vs):.4g} max={max(vs):.4g}")
    if not_tracked:
        print("\nNot tracked by AA:", ", ".join(not_tracked))
    if args.write:
        aa.write_json(aa.CONFIG_DIR / "slate.json", slate)
        print("\nWrote pinned slugs to config/slate.json — review the diff before committing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
