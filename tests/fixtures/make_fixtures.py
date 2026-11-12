"""Generate SYNTHETIC fixtures (invented numbers) for tests and the --demo preview.

Replace sample_response.json with a trimmed real response after the step-0 spike.
Run: python tests/fixtures/make_fixtures.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "scripts"))
import aa  # noqa: E402

CREATOR = {"GPT": "OpenAI", "Claude": "Anthropic", "Gemini": "Google"}
RELEASE = {
    "GPT-6 Astra": "2026-09-30", "GPT-6.1 Sol": "2026-10-21", "GPT-6.1 Terra": "2026-10-28",
    "GPT-6 Luna": "2026-08-12", "Claude Opus 5.5": "2026-10-07", "Claude Sonnet 5.5": "2026-10-07",
    "Claude Haiku 5.5": "2026-10-15", "Gemini 4 Argon": "2026-10-13", "Gemini 3.8 Flash": "2026-10-13",
    "Gemini 3.5 Flash-Lite": "2026-07-22",
}
# model -> [(effort label, intelligence, cost per task $, wait s)]
DAY1 = {
    "GPT-6 Astra": [("Medium", 52, 2.8, 30), ("High", 57, 5.5, 70), ("XHigh", 60, 9.0, 140)],
    "GPT-6.1 Sol": [("Low", 44, 0.6, 8), ("Medium", 51, 1.3, 20), ("High", 55, 2.6, 45), ("XHigh", 58, 4.4, 95)],
    "GPT-6.1 Terra": [("Low", 38, 0.15, 5), ("Medium", 44, 0.35, 12), ("High", 48, 0.7, 26)],
    "GPT-6 Luna": [("Low", 30, 0.03, 2.5), ("Medium", 34, 0.06, 5.0), ("High", 36, 0.12, 9)],
    "Claude Opus 5.5": [("Low", 35, 0.9, 6), ("Medium", 47, 1.5, 15), ("High", 54, 2.4, 32), ("Max", 58, 3.9, 70)],
    "Claude Sonnet 5.5": [("Low", 33, 0.25, 4), ("Medium", 45, 0.5, 10), ("High", 50, 0.9, 22), ("Max", 52, 1.4, 40)],
    "Claude Haiku 5.5": [("Low", 24, 0.04, 1.8), ("High", 34, 0.12, 6), ("Max", 35, 0.2, 11)],
    "Gemini 4 Argon": [("Low", 41, 0.7, 9), ("High", 50, 2.0, 38)],
    "Gemini 3.8 Flash": [("Low", 36, 0.08, 3), ("Medium", 42, 0.2, 7), ("High", 45, 0.3, 14)],
    "Gemini 3.5 Flash-Lite": [(None, 22, 0.012, 0.8)],  # real AA lists it with no effort bracket
}
EXTRA_DAY1 = [  # (name, creator, release, I, $, t)
    ("Claude Opus 5.5 (Max, Default Fallback)", "Anthropic", "2026-10-07", 57, 3.8, 68),
    ("Gemini 3.1 Pro (High)", "Google", "2026-09-01", 40, 1.1, 25),   # older than slate: not a candidate
    ("GLM-5.3 (High)", "Zhipu AI", "2026-10-30", 49, 0.4, 20),        # untracked creator: trimmed out
]


def slugify(name):
    return "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-").replace("--", "-").replace("--", "-")


def record(name, creator, release, i, cost, t):
    return {
        "id": "x-" + slugify(name),
        "slug": slugify(name),
        "name": name,
        "release_date": release,
        "model_creator": {"name": creator, "slug": creator.lower()},
        "evaluations": {
            "artificial_analysis_intelligence_index": i,
            "artificial_analysis_coding_index": None, "artificial_analysis_agentic_index": None,
            "artificial_analysis_economics_index": round(i * 0.95, 1),
            "artificial_analysis_engineering_index": round(i * 0.9, 1),
            "artificial_analysis_finance_and_accounting_index": round(i * 1.02, 1),
            "artificial_analysis_healthcare_and_medical_index": round(i * 0.88, 1),
            "artificial_analysis_legal_index": round(i * 0.93, 1),
            "artificial_analysis_strategy_and_ops_index": round(i * 0.97, 1),
        },
        "artificial_analysis_intelligence_index_cost": {"cost_per_task": {"total_cost": cost}},
        "performance_v3": {"mean_time_to_first_answer_token_seconds": t, "median_output_speed": 120},
        "pricing": {"price_1m_input_tokens": round(cost * 2, 2), "price_1m_output_tokens": round(cost * 8, 2)},
        "unused_field": "dropped by trim",
    }


def day_records(day):
    out = []
    for model, rows in DAY1.items():
        creator = CREATOR[model.split()[0].split("-")[0]]
        for effort, i, c, t in rows:
            name = f"{model} ({effort})" if effort else model
            if day == 2:
                if name == "GPT-6 Astra (Medium)":
                    continue                       # disappears: stale
                if name == "Claude Sonnet 5.5 (High)":
                    c = 0.75                       # -17%: change event
                if name == "GPT-6 Luna (Medium)":
                    t = 5.3                        # +6%: within tolerance, no event
            out.append(record(name, creator, RELEASE[model], i, c, t))
    for name, creator, rel, i, c, t in EXTRA_DAY1:
        out.append(record(name, creator, rel, i, c, t))
    if day == 2:
        out.append(record("GPT-6.1 Terra (XHigh)", "OpenAI", "2026-10-28", 50, 1.3, 50))  # unconfirmed
        out.append(record("Gemini 4.1 Neon (High)", "Google", "2026-11-10", 53, 1.6, 30))  # candidate
    return out


def main():
    slate, _axes, settings = aa.load_config()
    for m in slate["models"]:
        m["slugs"] = [slugify(f"{m['display_name']} ({e})" if e else m["display_name"]) for e, *_ in DAY1[m["display_name"]]]
    aa.write_json(HERE / "slate.json", slate)

    meta = {"tier": "free", "intelligence_index_version": "5.0.1"}
    aa.write_json(HERE / "sample_response.json",
                  {**meta, "pagination": {"page": 1, "total_pages": 1}, "data": day_records(2)})

    from datetime import date
    for day, d in ((1, "2026-11-10"), (2, "2026-11-11")):
        kept = aa.trim(day_records(day), slate, settings, date.fromisoformat(d))
        aa.write_json(HERE / "snapshots" / f"{d}.json", {
            "schema": 1, "fetched_at": f"{d}T21:43:10Z", **meta,
            "fingerprint": aa.fingerprint(kept, meta["intelligence_index_version"]), "records": kept,
        })
    aa.write_json(HERE / "last_checked.json", {"date": "2026-11-11", "checked_at": "2026-11-11T21:43:10Z"})
    print("Wrote synthetic fixtures to", HERE)


if __name__ == "__main__":
    main()
