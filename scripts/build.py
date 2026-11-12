"""Turn snapshots + config into the JSON files the page reads.

  python scripts/build.py                     # data/ + config/ -> site/data/
  python scripts/build.py --demo              # synthetic fixtures -> site/data/ (local preview only)
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
from pathlib import Path

import aa


LABEL = {"intelligence": "intelligence", "cost": "cost", "time": "wait"}


class BuildError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def major_of(version) -> str | None:
    if not version:
        return None
    return str(version).lstrip("vV").split(".")[0]


def better_eq(measure, a, b) -> bool:
    return a >= b if measure == "intelligence" else a <= b


def clearly_better(measure, a, b, tol) -> bool:
    if measure == "intelligence":
        return a - b > tol["intelligence_abs"]
    rel = tol["cost_rel"] if measure == "cost" else tol["time_rel"]
    return a < b * (1 - rel)


def dominates(a: dict, b: dict, tol: dict) -> bool:
    """a dominates b: no worse on all three measures and clearly better on one."""
    for m in aa.MEASURES:
        if a[m] is None or b[m] is None:
            return False
    return all(better_eq(m, a[m], b[m]) for m in aa.MEASURES) and any(
        clearly_better(m, a[m], b[m], tol) for m in aa.MEASURES)


def merge_close(variants: list, tol: dict) -> None:
    """Within one model, fold a variant into a cheaper one whose intelligence is within tolerance.

    Variants are visited cheapest first; each either becomes a kept representative or is merged
    into the cheapest kept variant with a score within ±intelligence_abs. Variants without a score
    or a cost can't be judged and are always kept.
    """
    judged = [v for v in variants if v["status"] != "unconfirmed"
              and v["intelligence"] is not None and v["cost"] is not None]
    kept: list = []
    for v in sorted(judged, key=lambda v: v["cost"]):  # stable: equal cost keeps the lower effort
        rep = next((k for k in kept if abs(k["intelligence"] - v["intelligence"]) <= tol["intelligence_abs"]), None)
        if rep is None:
            kept.append(v)
        else:
            v["merged_into"] = rep["slug"]
            rep["merged"].append(v["effort"])


def _knee(pool: list, key: str, tol: dict):
    """Knee of the intelligence-vs-`key` Pareto frontier (Kneedle-style: biggest gap above the line between
    the frontier's ends, on normalised intelligence and log `key`). Points within the score tolerance of the
    best knee count as tied; the one lowest on `key` wins."""
    pts = [p for p in pool if p[1]["intelligence"] is not None and p[1][key] is not None and p[1][key] > 0]
    front = [p for p in pts if not any(
        q is not p and q[1]["intelligence"] >= p[1]["intelligence"] and q[1][key] <= p[1][key]
        and (q[1]["intelligence"] > p[1]["intelligence"] or q[1][key] < p[1][key]) for q in pts)]
    front.sort(key=lambda p: p[1][key])
    if len(front) < 3:
        return None
    lo_i, hi_i = front[0][1]["intelligence"], front[-1][1]["intelligence"]
    lo_x, hi_x = math.log(front[0][1][key]), math.log(front[-1][1][key])
    if hi_i <= lo_i or hi_x <= lo_x:
        return None
    score = lambda p: ((p[1]["intelligence"] - lo_i) / (hi_i - lo_i)
                       - (math.log(p[1][key]) - lo_x) / (hi_x - lo_x))
    inner = front[1:-1]
    best = max(score(p) for p in inner)
    eps = tol["intelligence_abs"] / (hi_i - lo_i)
    return min((p for p in inner if best - score(p) <= eps), key=lambda p: p[1][key])


def quick_picks(models: list, tol: dict) -> list:
    """Variants that clearly win a common trade-off, merged into one entry per variant."""
    pool = [(m, v) for m in models for v in m["variants"] if v["status"] != "unconfirmed" and not v["merged_into"]]
    full = [p for p in pool if all(p[1][k] is not None for k in aa.MEASURES)]
    beats = {p[1]["slug"]: sum(dominates(p[1], q[1], tol) for q in full if q is not p) for p in full}
    found = []

    scored = [p for p in pool if p[1]["intelligence"] is not None and p[1]["cost"] is not None]
    if scored:
        top = max(p[1]["intelligence"] for p in scored)
        found.append(("smartest", "Smartest", "Highest score; the cheapest within noise of the top",
                      min((p for p in scored if top - p[1]["intelligence"] <= tol["intelligence_abs"]),
                          key=lambda p: p[1]["cost"])))
    k = _knee(pool, "cost", tol)
    if k:
        found.append(("value", "Best value", "Where paying more stops buying much more score (cost knee)", k))
    k = _knee(pool, "time", tol)
    if k:
        found.append(("fast", "Fast & smart", "Where waiting longer stops buying much more score (wait knee)", k))
    if scored:
        med = statistics.median(p[1]["intelligence"] for p in scored)
        found.append(("budget", "Budget", f"Cheapest that scores above the median ({med:.0f})",
                      min((p for p in scored if p[1]["intelligence"] >= med), key=lambda p: p[1]["cost"])))

    out: dict = {}
    for pid, label, why, (m, v) in found:
        e = out.setdefault(v["slug"], {"slug": v["slug"], "model": m["key"], "effort": v["effort"],
                                       "picks": [], "beats": beats.get(v["slug"], 0)})
        e["picks"].append({"id": pid, "label": label, "why": why})
    return list(out.values())


def changed(measure, old, new, tol) -> bool:
    if old is None or new is None:
        return old != new
    if measure == "intelligence":
        return abs(new - old) > tol["intelligence_abs"]
    rel = tol["cost_rel"] if measure == "cost" else tol["time_rel"]
    return old == 0 or abs(new - old) / old > rel


def model_for(name: str, slug: str, slate: dict):
    for m in slate["models"]:
        if slug in m.get("slugs", []) or aa.matches(name, m["aa_name"]):
            return m
    return None


def label_of(record, slate, settings) -> tuple[str, str | None]:
    base, effort, _ = aa.parse_name(record.get("name"), settings)
    m = model_for(record.get("name"), record.get("slug"), slate)
    return (m["display_name"] if m else base), effort


# ---------------------------------------------------------------- variants

def make_variant(rec: dict, status: str, as_of: str, pinned_index, settings: dict) -> dict:
    _, effort, note = aa.parse_name(rec.get("name"), settings)
    ms = aa.measures_of(rec)
    evals = rec.get("evaluations") or {}
    caps = {k: aa.number(v) for k, v in evals.items()
            if k != aa.INTELLIGENCE_KEY and aa.number(v) is not None}
    return {
        "slug": rec.get("slug"),
        "name": rec.get("name"),
        "effort": effort,
        "note": note,
        **ms,
        "status": status,
        "as_of": as_of,
        "dominated_by": None,
        "merged_into": None,
        "merged": [],
        "clamped": [],
        "capabilities": caps,
        "prices": {
            "input": aa.number(aa.get_path(rec, aa.FIELDS["price_input"])),
            "output": aa.number(aa.get_path(rec, aa.FIELDS["price_output"])),
        },
        "_pin": pinned_index,
    }


def last_known(slug: str, snapshots: list, major: str):
    """Most recent record for slug in earlier snapshots of the same index major version."""
    for date, snap in reversed(snapshots[:-1]):
        if major_of(snap.get("intelligence_index_version")) != major:
            break
        for r in snap.get("records", []):
            if r.get("slug") == slug:
                return r, date
    return None


# ---------------------------------------------------------------- build

def build_latest(snapshots: list, last_checked: dict, slate: dict, axes: dict,
                 settings: dict, demo: bool = False) -> dict:
    if not snapshots:
        raise BuildError("no snapshots found; run scripts/fetch.py first")
    data_date, latest = snapshots[-1]
    version = latest.get("intelligence_index_version")
    major = major_of(version)
    if major not in axes:
        raise BuildError(f"config/axes.json has no entry for index major version {major!r} "
                         f"(version {version!r}); add one before building")
    ax = axes[major]
    tol = settings["tolerance"]
    recs = {r.get("slug"): r for r in latest.get("records", [])}
    health = {"not_tracked": [], "unconfirmed": [], "missing": [], "duplicates": [],
              "candidates": [], "warnings": []}
    models = []

    for m in slate["models"]:
        pinned = m.get("slugs", [])
        matches = [r for r in latest.get("records", [])
                   if aa.matches(r.get("name"), m["aa_name"])]
        variants = []
        for i, slug in enumerate(pinned):
            rec = recs.get(slug)
            if rec is not None:
                variants.append(make_variant(rec, "ok", data_date, i, settings))
                continue
            found = last_known(slug, snapshots, major)
            if found:
                variants.append(make_variant(found[0], "stale", found[1], i, settings))
                health["missing"].append({"model": m["display_name"], "slug": slug, "last_seen": found[1]})
            else:
                health["missing"].append({"model": m["display_name"], "slug": slug, "last_seen": None})
        for r in matches:
            if r.get("slug") not in pinned:
                variants.append(make_variant(r, "unconfirmed", data_date, None, settings))
                health["unconfirmed"].append({"model": m["display_name"], "name": r.get("name"),
                                              "slug": r.get("slug"), "key": m["key"]})
        if not variants:
            health["not_tracked"].append(m["display_name"])
            continue

        # One variant per effort: pinned before unconfirmed, no note before note, pin order.
        by_effort: dict = {}
        for v in sorted(variants, key=lambda v: (v["status"] == "unconfirmed", v["note"] is not None,
                                                 v["_pin"] if v["_pin"] is not None else 999)):
            if v["effort"] in by_effort:
                health["duplicates"].append({"model": m["display_name"], "kept": by_effort[v["effort"]]["slug"],
                                             "dropped": v["slug"]})
                if v["status"] == "unconfirmed":
                    health["unconfirmed"] = [u for u in health["unconfirmed"] if u["slug"] != v["slug"]]
            else:
                by_effort[v["effort"]] = v
        variants = sorted(by_effort.values(), key=lambda v: aa.effort_rank(v["effort"], settings))

        for v in variants:
            for k in aa.MEASURES:
                val = v[k]
                if val is None:
                    health["warnings"].append(f"{m['display_name']} {v['effort']}: no {LABEL[k]}")
                elif val < ax[k]["min"] or val > ax[k]["max"]:
                    v["clamped"].append(k)
                    health["warnings"].append(
                        f"{m['display_name']} {v['effort']}: {LABEL[k]} {val:g} off scale "
                        f"({ax[k]['min']}–{ax[k]['max']} in config/axes.json)")
            del v["_pin"]
        merge_close(variants, tol)
        models.append({
            "key": m["key"], "display_name": m["display_name"], "subscription": m["subscription"],
            "tier_role": m["tier_role"], "notes": m.get("my_notes", ""), "variants": variants,
        })

    # Dominance, within one subscription only (allowances don't convert across providers).
    for sub in {mm["subscription"] for mm in models}:
        pool = [(mm, v) for mm in models if mm["subscription"] == sub
                for v in mm["variants"] if v["status"] != "unconfirmed" and not v["merged_into"]]
        for _, b in pool:
            best = None
            for ma, a in pool:
                if a is not b and dominates(a, b, tol):
                    if best is None or a["intelligence"] > best[1]["intelligence"]:
                        best = (ma, a)
            if best:
                b["dominated_by"] = {"slug": best[1]["slug"], "label": f"{best[0]['display_name']} ({best[1]['effort']})"}

    health["candidates"] = find_candidates(latest.get("records", []), slate, settings)

    return {
        "schema": 1,
        "demo": demo,
        "data_date": data_date,
        "fetched_at": latest.get("fetched_at"),
        "last_checked": (last_checked or {}).get("checked_at") or latest.get("fetched_at"),
        "index_version": version,
        "tier": latest.get("tier"),
        "axes": ax,
        "settings": {k: settings[k] for k in ("effort_order", "tolerance", "stale_hours", "timezone")},
        "models": models,
        "health": health,
        "quick_picks": quick_picks(models, tol),
        # The same picks within each plan, in slate order (allowances are per plan).
        "plan_picks": [{"plan": sub, "picks": quick_picks([mm for mm in models if mm["subscription"] == sub], tol)}
                       for sub in dict.fromkeys(mm["subscription"] for mm in models)],
    }


def find_candidates(records: list, slate: dict, settings: dict) -> list:
    """New models from tracked creators released after the newest slate model from that creator."""
    creators = set(settings.get("tracked_creators", []))
    bases = [m["aa_name"] for m in slate["models"]]
    ignored = slate.get("ignored", [])
    pinned = {s for m in slate["models"] for s in m.get("slugs", [])}
    newest: dict = {}
    for r in records:
        if aa.matches_any(r.get("name"), bases) or r.get("slug") in pinned:
            c, rd = aa.creator_of(r), r.get("release_date")
            if c and isinstance(rd, str) and rd > newest.get(c, ""):
                newest[c] = rd
    out: dict = {}
    for r in records:
        c, rd, name = aa.creator_of(r), r.get("release_date"), r.get("name") or ""
        if c not in creators or aa.matches_any(name, bases) or r.get("slug") in pinned \
                or aa.matches_any(name, ignored):
            continue
        if not isinstance(rd, str) or rd <= newest.get(c, ""):
            continue
        base = aa.parse_name(name, settings)[0]
        entry = out.setdefault(base, {"name": base, "creator": c, "release_date": rd,
                                      "suggested_name": base, "slugs": []})
        entry["slugs"].append(r.get("slug"))
    return sorted(out.values(), key=lambda e: e["release_date"], reverse=True)


def build_changes(snapshots: list, slate: dict, settings: dict) -> list:
    tol = settings["tolerance"]
    bases = [m["aa_name"] for m in slate["models"]]
    pinned = {s for m in slate["models"] for s in m.get("slugs", [])}

    def relevant(r):
        return aa.matches_any(r.get("name"), bases) or r.get("slug") in pinned

    events = []
    for (_, prev), (date, cur) in zip(snapshots, snapshots[1:]):
        pv, cv = prev.get("intelligence_index_version"), cur.get("intelligence_index_version")
        version_changed = pv != cv
        if version_changed:
            events.append({"date": date, "type": "index_version", "old": pv, "new": cv})
        p = {r.get("slug"): r for r in prev.get("records", []) if relevant(r)}
        c = {r.get("slug"): r for r in cur.get("records", []) if relevant(r)}
        for slug in sorted(c.keys() - p.keys()):
            model, effort = label_of(c[slug], slate, settings)
            events.append({"date": date, "type": "variant_added", "model": model, "effort": effort, "slug": slug})
        for slug in sorted(p.keys() - c.keys()):
            model, effort = label_of(p[slug], slate, settings)
            events.append({"date": date, "type": "variant_removed", "model": model, "effort": effort, "slug": slug})
        for slug in sorted(p.keys() & c.keys()):
            old, new = aa.measures_of(p[slug]), aa.measures_of(c[slug])
            model, effort = label_of(c[slug], slate, settings)
            for k in aa.MEASURES:
                if k == "intelligence" and version_changed:
                    continue
                if changed(k, old[k], new[k], tol):
                    events.append({"date": date, "type": "measure_changed", "model": model, "effort": effort,
                                   "slug": slug, "measure": k, "old": old[k], "new": new[k]})
        prev_cands = {e["name"] for e in find_candidates(prev.get("records", []), slate, settings)}
        for e in find_candidates(cur.get("records", []), slate, settings):
            if e["name"] not in prev_cands:
                events.append({"date": date, "type": "new_model", "model": e["name"],
                               "creator": e["creator"], "release_date": e["release_date"]})
    events.sort(key=lambda e: e["date"], reverse=True)
    return events


def validate(latest: dict) -> None:
    for key in ("data_date", "index_version", "axes", "models", "health"):
        if key not in latest:
            raise BuildError(f"latest.json missing {key!r}")
    for m in latest["models"]:
        if not m["variants"]:
            raise BuildError(f"{m['display_name']} has no variants")
        for v in m["variants"]:
            for k in aa.MEASURES:
                if v[k] is not None and not isinstance(v[k], float):
                    raise BuildError(f"{v['slug']}.{k} is not a number")


def load_snapshots(snapshots_dir: Path) -> list:
    return [(p.stem, aa.read_json(p)) for p in aa.snapshot_paths(snapshots_dir)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--demo", action="store_true", help="build from tests/fixtures (synthetic data)")
    ap.add_argument("--data-dir", type=Path, default=aa.DATA_DIR)
    ap.add_argument("--config-dir", type=Path, default=aa.CONFIG_DIR)
    ap.add_argument("--out", type=Path, default=aa.SITE_DATA_DIR)
    args = ap.parse_args(argv)

    slate, axes, settings = aa.load_config(args.config_dir)
    data_dir = args.data_dir
    if args.demo:
        fx = aa.ROOT / "tests" / "fixtures"
        data_dir = fx
        slate = aa.read_json(fx / "slate.json")
    last_checked = aa.read_json(data_dir / "last_checked.json")
    if args.demo:  # demo data is synthetic; show it as just checked so the badge reads normally
        from datetime import datetime, timezone
        last_checked = {"checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        snapshots = load_snapshots(data_dir / "snapshots")
        latest = build_latest(snapshots, last_checked, slate, axes, settings, demo=args.demo)
        validate(latest)
        changes = build_changes(snapshots, slate, settings)
    except BuildError as e:
        print(f"::error::{e}")
        return 1
    aa.write_json(args.out / "latest.json", latest)
    aa.write_json(args.out / "changes.json", {"events": changes})
    h = latest["health"]
    print(f"Built {len(latest['models'])} models, {sum(len(m['variants']) for m in latest['models'])} variants, "
          f"{len(changes)} change events; health: "
          + ", ".join(f"{k}={len(v)}" for k, v in h.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
