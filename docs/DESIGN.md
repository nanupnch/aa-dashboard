# AI Model Dashboard — Project Design (v2)

Nov 12, 2026 · supersedes the v1 design doc

## 1. Purpose

**The decision this serves:** "For this task, which model and effort should I use without wasting my subscription allowance?"

Everything on the page exists to answer that question, for the 10 models unlocked by ChatGPT Plus, Claude Pro and Google AI Pro. Data comes from the Artificial Analysis (AA) free API. The page is a static site on GitHub Pages, refreshed by a GitHub Actions job every day before 3 pm local time. It costs nothing.

### Design principles

1. **Decision first.** A feature earns its place only if it changes which model you pick.
2. **Start simple and grow** (Gall's law). Ship the smallest dashboard that works, then extend it.
3. **Honest measurement.** Differences smaller than the noise are shown as ties. Missing values are never shown as zero. Stand-in measures are labelled as stand-ins.
4. **Evidence before code.** Every API field is checked against a real response (step 0) before anything depends on it.

### Changes from v1, and why

| v1 | v2 | Reason |
| --- | --- | --- |
| 4 lanes, including output size | 3 lanes: Smarter, Cheaper, Faster | Output size mostly repeats cost (for allowance) and time (for waiting). It needs a US$417/mo key and would sit empty. |
| Free now, Pro later | Free key only | Removes two open questions and a code path |
| Rows sorted globally | One flat list in tier order (Largest → Small); each model is marked by plan colour × tier shape (◆ ● ■ ▲); sorting ranks across all models | No group headers to read. Colour and shape together identify every model in both the lineup and the chart, without text labels. Dominance (hollow dots) stays within one plan, because allowances are metered per provider. |
| Pareto marking in the trade-off chart only | Dominated variants are hollow dots in the Lineup | Shows "never worth picking" without reading across lanes |
| Snapshot every day | Check daily; snapshot only when a displayed value changes | Daily snapshots would be near-duplicates. Speed is already a 5-day average. |
| History line chart | Change log (line chart deferred) | Index scores for a fixed variant are flat, so lines would carry no information. The question you actually check for is "what's new?" |
| Axes fitted to each day's data | Fixed axis ranges per index major version | Dots must not move unless the data moved. |
| Exact ranking | Ties within a noise tolerance | A gap of 1–2 index points is within sampling error. |
| Drift checks on prefixes only | Also detects new models from the three providers | The most common churn is a new model, not a new effort level. |
| `site/data` committed | `site/data` built in CI and not committed | Derived data; the snapshots are the source of truth |

## 2. Scope

**In scope:** 10 language models (the table below), every effort variant AA lists, and three measures: intelligence, cost per task, and time.

| Subscription | Model | Tier |
| --- | --- | --- |
| ChatGPT Plus | GPT-6 Astra | Largest |
| ChatGPT Plus | GPT-6.1 Sol | Flagship |
| ChatGPT Plus | GPT-6.1 Terra | Mid |
| ChatGPT Plus | GPT-6 Luna | Small |
| Claude Pro | Claude Opus 5.5 | Flagship |
| Claude Pro | Claude Sonnet 5.5 | Mid |
| Claude Pro | Claude Haiku 5.5 | Small |
| Google AI Pro | Gemini 4 Argon | Flagship |
| Google AI Pro | Gemini 4 Hydrogen | Mid (added Nov 12: Flash-class, "Everyday help" in the Gemini app picker) |
| Google AI Pro | Gemini 3.8 Flash | Mid |
| Google AI Pro | Gemini 3.5 Flash-Lite | Small |

**Out of scope:** non-text models; measuring your own usage or limits; privacy (the site and repository are public); output size; the Pro key.

**Assumption:** every effort level AA lists for a model is available to you in its app.

## 3. Measures

| Lane | Meaning | AA field (free key) | Better | Scale |
| --- | --- | --- | --- | --- |
| **Smarter** | Intelligence Index, 0–100 | `evaluations.artificial_analysis_intelligence_index` | higher | linear, fixed range |
| **Cheaper** | Cost per task, US$ | `artificial_analysis_intelligence_index_cost.cost_per_task.total_cost` | lower | log, fixed range |
| **Faster** | Wait before answer, s (stand-in for time per task) | `performance_v3.mean_time_to_first_answer_token_seconds` | lower | log, fixed range |

Every lane runs better toward the right. The "Faster" lane header reads "Faster (wait before answer)" so the stand-in is never mistaken for time per task.

**Detail view only:** the six capability indexes and token prices (input and output, per million tokens).

**Noise tolerances**, stored in `config/settings.json` and used for ties, dominance and the change log:

| Measure | Tie / "no change" tolerance | Reasoning |
| --- | --- | --- |
| Intelligence | ±2 points | Sampling error of the composite index, roughly √(p(1−p)/n), averaged over the component benchmarks |
| Cost per task | ±10% | Price and token-count rounding |
| Wait before answer | ±20% | Day-to-day load variation on top of the 5-day average |

All field paths are **unverified until step 0** (§10).

## 4. Architecture

```
GitHub Actions (daily)                                GitHub Pages
┌──────────────────────────────────────────────┐     ┌─────────────────────┐
│ fetch.py ──► data/snapshots/DATE.json        │     │ index.html          │
│   │          (only when changed)             │     │ app.js  style.css   │
│   └────────► data/last_checked.json (always) │     │ data/latest.json ◄──┼─ loaded on open
│ build.py ──► site/data/latest.json           │──►  │ data/changes.json   │
│              site/data/changes.json          │     └─────────────────────┘
│ git commit data/ → deploy site/              │
└──────────────────────────────────────────────┘
        ▲ AA_API_KEY (repo secret)
        │
 artificialanalysis.ai/api/v2/language/models/free
```

The key exists only as a repository secret, and the browser loads only pre-built JSON. Dependencies: the Python 3.12 standard library, plus vanilla HTML, CSS, JS and inline SVG. Nothing needs installing.

### Repository layout

```
aa-dashboard/
  .github/workflows/daily.yml
  config/
    slate.json          10 models, pinned slugs, notes
    axes.json           fixed axis ranges per index major version
    settings.json       tolerances, tracked creators, effort order
  data/
    snapshots/2026-11-12.json   written only when a displayed value changes
    last_checked.json           written every successful run (keeps the repo active)
  scripts/
    aa.py               shared: HTTP with retries, pagination, record trimming
    fetch.py            API → snapshot
    build.py            snapshots + config → site/data/*.json
    discover.py         step-0 check; prints matching rows for each slate model
  tests/
    fixtures/sample_response.json   trimmed real response from step 0
    test_parse.py  test_build.py  test_change.py
  site/
    index.html  app.js  style.css
    data/       (generated in CI, git-ignored)
  docs/DESIGN.md
```

## 5. Configuration

### `config/slate.json`

```json
{
  "models": [
    {
      "key": "claude-opus-5-5",
      "display_name": "Claude Opus 5.5",
      "subscription": "Claude Pro",
      "tier_role": "Flagship",
      "aa_name": "Claude Opus 5.5",
      "slugs": ["claude-opus-5-5-max", "claude-opus-5-5-high"],
      "my_notes": ""
    }
  ]
}
```

- **Pinned `slugs` are authoritative** for what is displayed. `aa_name` is used only for drift detection.
- A row matches when its name equals `aa_name` or starts with `aa_name (`, so "GPT-6 Sol" cannot match "GPT-6.1 Sol (High)". The step-0 spike showed AA lists some single-setting models with no bracket ("Gemini 3.5 Flash-Lite"); those get effort `default`.

### `config/axes.json`

```json
{
  "5": {
    "intelligence": { "min": 10, "max": 70 },
    "cost":         { "min": 0.01, "max": 10 },
    "time":         { "min": 1, "max": 600 }
  }
}
```

Ranges are keyed by index **major** version. They are set once from step-0 data, with headroom. A value outside its range is drawn at the edge with a ▸ marker and raises a health warning. A new major version with no entry stops the build with a clear message.

### `config/settings.json`

```json
{
  "effort_order": ["minimal", "low", "medium", "high", "xhigh", "max"],
  "tolerance": { "intelligence_abs": 2, "cost_rel": 0.10, "time_rel": 0.20 },
  "tracked_creators": ["OpenAI", "Anthropic", "Google"],
  "candidate_window_days": 120,
  "stale_hours": { "amber": 36, "red": 72 },
  "timezone": "America/Los_Angeles"
}
```

## 6. Data pipeline

### 6.1 `fetch.py`

1. If `last_checked.json` already shows today's date (in the configured time zone) and `--force` is not set, write `skip=true` to `$GITHUB_OUTPUT` and exit 0. This makes the safety-net run free on normal days.
2. Request every page of `/api/v2/language/models/free` with header `x-api-key`.
   - Retry 5xx responses and timeouts 3 times (waits of 2 s, 8 s and 32 s).
   - Exit 1 on 401.
   - Exit 1 on 429, logging `Retry-After`.
3. If any page fails, discard the whole run.
4. **Trim:** keep only records whose name matches a slate `aa_name`, or whose creator is in `tracked_creators` and which were released within the last `candidate_window_days` (needed to detect new models). From each, keep only the fields listed in §3 plus `name`, `slug`, `release_date`, `model_creator`, the capability indexes and the prices. This minimises how much of AA's data is republished.
5. Compute a **display fingerprint**: a SHA-256 hash of each tracked record's displayed values, rounded to display precision, plus the index version. Compare it with the latest snapshot's fingerprint. If they differ, write `data/snapshots/<date>.json`.
6. Always write `data/last_checked.json` with `checked_at`, `tier`, `index_version`, `fingerprint`, `snapshot_written` and the rate-limit headers.

**Snapshot schema**

```json
{
  "schema": 1,
  "fetched_at": "2026-11-12T19:43:10Z",
  "tier": "free",
  "intelligence_index_version": "5.0.1",
  "fingerprint": "sha256:…",
  "records": [ { "slug": "...", "name": "...", "...": "trimmed fields" } ]
}
```

### 6.2 `build.py`

Input: every snapshot, `last_checked.json` and the three config files. Output: `site/data/latest.json` and `site/data/changes.json`.

**Variant parsing.** Split `name` at the first ` (`. The left part is the model name. The text in brackets, split on `,`, gives the effort label (first part, lowercased) and an optional note (the rest). A label not in `effort_order` keeps its text and sorts last.

**Duplicate efforts.** If two pinned slugs parse to the same effort (for example "(Max)" and "(Max, Default Fallback)"), keep the one without a note. Otherwise keep the first in `slugs` order. The other is listed in health.

**Drift detection** (results go to `health`):

| Check | Rule | Shown as |
| --- | --- | --- |
| Unconfirmed variant | Prefix matches but the slug is not pinned | Grey dot, plus a health line with the slug to paste in |
| Missing variant | Pinned slug absent from the latest snapshot | Last known values, labelled "as of <date>" |
| Candidate new model | Creator tracked, release date later than the newest slate model from that creator, matches no `aa_name` | Health line: "New from Anthropic: Claude X (2026-12-01) — add to slate?" |
| Out-of-range value | Outside the `axes.json` range | ▸ edge marker plus a health line |
| Missing field | An expected field is absent or null | "not measured" plus a health line |

**Dominance.** Compared within one subscription only, because allowances don't convert between providers. Variant A dominates variant B when:

- A is no worse than B on all three measures (higher intelligence; lower cost; lower time), **and**
- A is better than B by more than the tolerance on at least one measure.

A measure missing on either side counts as "no worse" for neither variant, so the pair can't dominate each other. Dominated variants get `"dominated_by": "<slug>"`.

**Merging close variants.** Within one model, two efforts whose intelligence scores are within the tolerance (±2 points) count as the same result, so only the cheaper one is shown. Variants are visited cheapest first. Each one either becomes a kept representative, or is merged into the cheapest kept variant whose score is within tolerance (`merged_into` on the folded variant, `merged` on the one kept). Selecting a merged effort shows the cheaper one, tagged with its effort. Merged variants don't take part in dominance and appear greyed in the detail table. Variants with no score or no cost are never merged. On the first real data this folded Astra XHigh into High, Opus and Haiku Max into XHigh, Flash High into Medium, and Terra and Luna "No reasoning" into Low.

**Ranking with ties.** When sorting by a lane, values within the tolerance of the value ranked just above share its rank. Ties are broken alphabetically and drawn with an "≈" before the rank.

**Change log.** Compare consecutive snapshots and emit events:

| Event | Trigger |
| --- | --- |
| `variant_added` / `variant_removed` | Slug appears or disappears (tracked or candidate) |
| `index_version` | Version string changes; also starts a new history segment |
| `measure_changed` | A displayed value moves beyond its tolerance |

`changes.json` keeps every event: date, model, effort, measure, old and new values.

### 6.3 `latest.json` schema (what the page reads)

```json
{
  "schema": 1,
  "data_date": "2026-11-12",
  "last_checked": "2026-11-12T19:43:10Z",
  "index_version": "5.0.1",
  "tier": "free",
  "axes": { "intelligence": {...}, "cost": {...}, "time": {...} },
  "models": [
    {
      "key": "claude-opus-5-5",
      "display_name": "Claude Opus 5.5",
      "subscription": "Claude Pro",
      "tier_role": "Flagship",
      "notes": "",
      "variants": [
        {
          "slug": "claude-opus-5-5-max", "effort": "max", "note": null,
          "intelligence": 58, "cost": 1.84, "time": 41.2,
          "dominated_by": null, "status": "ok",
          "as_of": "2026-11-12",
          "capabilities": { "...": 0 }, "prices": { "input": 0, "output": 0 }
        }
      ]
    }
  ],
  "health": { "unconfirmed": [], "missing": [], "candidates": [], "warnings": [] }
}
```

`status` is one of `ok`, `unconfirmed`, `stale` or `missing_field`.

## 7. The page

### 7.1 Layout (top to bottom, one scrolling page)

```
┌ AI Models · data 12 Nov · index v5.0.1 · [● fresh] ─────────────────────┐
│ Effort: [Low] [Med] [High] [XHigh] [Max]      Group: [By plan ✓]         │
├──────────────────────┬──────────────┬──────────────┬─────────────────────┤
│                      │  Smarter →   │  Cheaper →   │ Faster → (wait)     │
├ Claude Pro ──────────┼──────────────┼──────────────┼─────────────────────┤
│ Opus 5.5  Flagship   │   ·──·──● 58 │ ●──·──·  $1.8│ ●──·──·   41 s      │
│ Sonnet 5.5  Mid      │  ·──·──● 51  │   ·──● $0.40 │   ·──●  12 s        │
│ Haiku 5.5  Small     │ ·──○ 38      │      ·──●$.05│      ·──● 3 s       │
├ ChatGPT Plus ────────┼──────────────┼──────────────┼─────────────────────┤
│ …                                                                         │
└──────────────────────────────────────────────────────────────────────────┘
  Changes (last 30 days) · Trade-off chart · Data health · Footer
```

- **Row:** model name and tier role. In each lane there is one small dot per effort, joined low → max by a thin line. The selected effort is a large dot with its value printed. **A hollow dot means dominated.** A grey dot means unconfirmed.
- **Beaten variants are hidden by default.** The "○ beaten N" chip in the key toggles them back on, as hollow dots. While they're hidden, a row whose selected effort is beaten shows its closest effort that isn't, tagged with that effort's name. A row with every effort beaten still shows its closest one, dimmed. The detail table always lists every variant.
- **Wait threshold:** a second control, "Wait ≤ … s", identical in shape (slider, − / +, number box in seconds, ✕). Both thresholds combine: each row shows its cheapest effort meeting *every* active threshold. Its slider runs from fastest (left) to "any" (right), so both controls read as number lines. Each control's stops are computed independently (the other threshold off) and never shift. Wait stops trigger when *any* model's suggested effort changes (11 on the first real data). The overall-pick rule used for score gave only 3, because cheap small models win regardless of wait. The limit is drawn in the Faster lane and, in the Wait view, as a vertical line on the trade-off chart.
- **Good-enough threshold:** a "Score" control next to the effort buttons: a slider and − / + steppers that move only between **stops** (the scores where the suggested cheapest or fastest model changes, 18 on the first real data instead of ~60 integer steps), a number box for typing any exact value (kept as typed; the slider shows its equivalent stop; capped at the highest stop; Esc clears) and a ✕ reset. Stops are computed in the browser from the loaded data. Per-row picks were considered as the trigger but change at almost every score (34 of 35), so they wouldn't reduce the steps. Off by default. When it's set, each row shows that model's *cheapest* effort scoring at least the threshold, tagged with the effort. Models that never reach it drop to the bottom, dimmed, showing their best effort. Good-enough rows are ordered by cost. The cheapest and fastest are marked $ and ⚡. A dashed line marks the threshold in the Smarter lane and across the trade-off chart, where points below it fade and the furthest-right point above it is ringed. Choosing an effort button turns the threshold off. This replaces composite intelligence-per-$ axes: those are dominated by cost's 2,000× range, need ratio-scale scores, and hide a weighting that varies by task. A threshold matches how the choice is actually made (cheapest model that's good enough).
- **Effort selector:** Low to Max (no "None": non-reasoning variants are merged into Low where scores match, and listed in the detail panel). The default is **Max**. If a model lacks the selected effort, the highest effort below it is used and printed with its label ("High").
- **Lane header tap:** expands that lane to full width with finer ticks, hides the other two, and sorts all rows by it (best first, ties marked "≈"). Tapping ✕ brings back all three.
- **Row tap:** opens the detail panel. **Dot tap or hover:** shows effort, exact value and rank.
- **Colour** means subscription only, using Okabe-Ito colours (blue, vermillion, bluish green) that are colour-blind safe. The colour is always paired with the group label.

### 7.1a Quick picks (top of the page)

Cards for the variants that clearly win a common trade-off. They are computed in `build.py` (`quick_picks`) from the data alone, with no hand-picked winners:

| Pick | Rule |
| --- | --- |
| Smartest | Highest score; among variants within the score tolerance of the top, the cheapest |
| Best value | Knee of the global intelligence-vs-log-cost Pareto frontier (Kneedle-style: largest normalised gap above the line joining the frontier's ends). Points within the score tolerance of the best knee, as a fraction of the frontier's score range, count as tied; the cheapest wins. |
| Fast & smart | Same knee on intelligence vs log wait; among ties, the fastest |
| Budget | Cheapest variant scoring at or above the median |
| Most dominant | Beats the most other variants on all three measures (all plans, with tolerances) |

A variant that wins several picks gets one row with all its labels. The section is a compact list, one row per variant: short labels (Smartest, Value, Fast+smart, Budget, Dominant) | model + effort | score · cost · wait, in lane order. On phones the labels move onto their own line. The reasons and the "beats N" count are in the row's hover text; tapping a row opens the detail panel. On the first real data: Smartest = Opus 5.5 XHigh; GPT-6.1 Sol Medium = Best value, Fast & smart and Most dominant (beats 6); Budget = GPT-6.1 Terra High.

### 7.2 Below the Lineup

1. **Changes:** the `changes.json` events from the last 30 days, newest first, with "show all".
2. **Trade-off chart:** intelligence (up) against cost or time (right, log scale; one tap switches). One dot per variant; each model's dots joined in effort order. Dominated variants are hollow, as in the Lineup. (A per-plan frontier step line was tried in the first build and dropped: with three plans the overlapping staircases hid the dots.) A shaded **golden quadrant** marks variants smarter than the median variant *and* better than the median on the x measure; dashed median lines bound it. Markers use the model's tier shape in its plan colour, with no text labels (hover for the name). A soft grey **Pareto line** joins the variants (across all plans) that no other variant beats on both shown axes.
3. **Data health:**
   - data date and age, last check, index version and tier
   - unconfirmed and missing variants, candidate new models and warnings
   - each item with the exact line to paste into `slate.json`
   - Shown as **Needs attention** (only when something is listed). Each item is a one-line summary that expands to explain what it means and what to do, with ready-to-paste snippets (for a new model: a slate entry pre-filled with its plan, the `discover.py --write` command, and the `ignored` alternative).
4. **Footer:** "Data: Artificial Analysis" with a link, the data date, the index version, and a "How to read this" link to the caveats.

### 7.3 Detail panel

A table with one row per effort and columns for intelligence, cost per task, wait before answer, input and output price, and a dominance note. It also shows the six capability indexes as a small dot strip and your `my_notes`.

### 7.4 Freshness badge

| Badge | Condition |
| --- | --- |
| Green | `last_checked` less than 36 h ago |
| Amber | `last_checked` 36–72 h ago |
| Red | `last_checked` more than 72 h ago, or `latest.json` failed to load |

The badge follows `last_checked`, not `data_date`. Unchanged data still counts as fresh.

### 7.5 Responsive and accessibility

- **Width ≥ 720 px:** the name column is 200 px and the lanes share the remainder.
- **Below 720 px:** the name moves to its own line, and the three lanes run full width at about 110 px each on a 375 px phone, with 16 px gutters.
- **Light mode only**, by choice; colours are CSS variables on `:root`.
- **Keyboard:** each row is one tab stop with an `aria-label` that summarises the row ("Claude Opus 5.5, max effort: intelligence 58, $1.80 per task, 41 s wait"). Enter opens the detail table with every value. This replaces about 90 per-dot tab stops.
- **Tables:** the detail panel is a real `<table>`; there is no separate all-variants table.
- **No JavaScript:** the page shows a static message linking to `data/latest.json`.

### 7.6 How to read this (caveats shown on the page)

1. Cost per task approximates how fast you use up an allowance, and only within one subscription. It is not your bill.
2. "Wait before answer" is AA's API speed test, run from us-central1. It is useful for ranking models, not for predicting your wait in the app.
3. Speed values are 5-day averages. Single-day moves are noise.
4. Ties (≈) mean the difference is within measurement noise.
5. Index versions are not comparable. A score change at a version change is the test changing, not the model.
6. Benchmarks are not your work. Use `my_notes`.

## 8. Workflow (`.github/workflows/daily.yml`)

```yaml
name: daily-refresh
on:
  schedule:
    # UTC crons that cover both Pacific offsets; fetch.py skips if already checked today (Pacific).
    # PDT: 14:43, 15:43, 16:43   PST: 13:43, 14:43, 15:43
    - cron: '43 21 * * *'
    - cron: '43 22 * * *'
    - cron: '43 23 * * *'
  workflow_dispatch:
    inputs:
      force: { type: boolean, default: false, description: 'Re-fetch even if checked today' }
permissions: { contents: write, pages: write, id-token: write }
concurrency: { group: daily-refresh, cancel-in-progress: false }
jobs:
  refresh:
    runs-on: ubuntu-latest
    environment: github-pages
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - id: fetch
        run: python scripts/fetch.py ${{ inputs.force && '--force' || '' }}
        env: { AA_API_KEY: '${{ secrets.AA_API_KEY }}' }
      - if: steps.fetch.outputs.skip != 'true'
        run: python -m unittest discover -s tests && python scripts/build.py
      - if: steps.fetch.outputs.skip != 'true'
        run: |
          git config user.name 'github-actions[bot]'
          git config user.email 'github-actions[bot]@users.noreply.github.com'
          git add data
          git commit -m "data: $(date -u +%F)" || echo 'no changes'
          git pull --rebase && git push
      - if: steps.fetch.outputs.skip != 'true'
        uses: actions/configure-pages@v5
      - if: steps.fetch.outputs.skip != 'true'
        uses: actions/upload-pages-artifact@v3
        with: { path: site }
      - if: steps.fetch.outputs.skip != 'true'
        uses: actions/deploy-pages@v4
```

**As built** (see `.github/workflows/daily.yml`, which supersedes the sketch above): when today's data is already in, the guard skips only the fetch and the data commit. Tests, build and deploy always run, so manual runs and code pushes always reach the site. A `push` to `main` also triggers it; the bot's own data commits use `GITHUB_TOKEN`, so they don't re-trigger it. Action versions: checkout v7, setup-python v7, configure-pages v6, upload-pages-artifact v5, deploy-pages v5. The first deploy showed why: the snapshot committed on day one marked the day as checked, so the original guard skipped the deploy entirely.

**Why three UTC crons and no `timezone:` key.** The three times cover both Pacific offsets, so the schedule doesn't depend on GitHub honouring a time-zone setting. Daylight-saving changes need no edits. In any season, the first run lands before 3 pm Pacific and at least one more follows as a safety net. The "already checked today (Pacific)" guard makes the later runs free.

**Deploys within the same job** because commits pushed with `GITHUB_TOKEN` do not trigger other workflows.

**60-day rule:** `last_checked.json` changes on every successful run, so the repository always has a commit within the last day.

### Error handling

| Event | Job | You see |
| --- | --- | --- |
| 401 (bad or missing key) | Fails before writing anything | GitHub email; badge turns amber after 36 h |
| 429 (quota used) | Fails; logs `Retry-After` | Email; the safety-net run retries |
| 5xx or timeout | 3 retries, then fails | Email if all retries fail |
| Partial pagination | Discards the run | Nothing changes |
| Unknown index major version | Build fails with "add axes for v6" | Email; the page keeps the previous deploy |
| Unit test failure | No deploy | Email; the page keeps the previous deploy |
| Run delayed or dropped | Next cron an hour later | Badge stays green unless every run that day misses |

**Security.**

- The key exists only in `secrets.AA_API_KEY` and is never printed.
- There is no `pull_request` trigger.
- The site holds no key.
- Actions are pinned to major versions; pinning to commit SHAs would be safer if you prefer.

## 9. Testing

| Test | Covers |
| --- | --- |
| `test_parse.py` | Name → (model, effort, note); unknown labels; name collisions (GPT-6 Sol vs GPT-6.1 Sol); bare names |
| `test_build.py` | Dominance with tolerances and missing values; tie ranking; duplicate-effort choice; drift checks (unconfirmed, missing, candidate); out-of-range values |
| `test_change.py` | Fingerprint is stable under sub-precision speed jitter; events fire beyond tolerance only; a version change starts a new segment |
| Fixture | `tests/fixtures/sample_response.json`, trimmed from the real step-0 response |

The page gets a manual check at 375 px and 1280 px, in light and dark mode, and by keyboard only. `build.py` validates `latest.json` against the schema before writing it.

## 10. Build plan

| Step | Work | Done when |
| --- | --- | --- |
| **0. Spike** (≈30 min) | Get a free key; `curl` both pages; save the JSON locally | Confirmed: all 10 models present; effort labels; every §3 field on the free tier; creator and release-date field names; how many pages. Whether GitHub honours `timezone:` is confirmed on the first scheduled run. |
| 1. Repo | Public repo; `AA_API_KEY` secret; Pages source set to "GitHub Actions" | Secret set |
| 2. Slate | `discover.py`; pin slugs; set `axes.json` from the spike data | `slate.json` has slugs for all 10 models |
| 3. Pipeline | `aa.py`, `fetch.py`, `build.py` and the tests | Tests pass; `latest.json` looks right locally |
| 4. Page | Lineup, effort selector, sorting, freshness badge, health panel, footer | Usable on a phone with local data |
| 5. Live | `daily.yml`; run it manually once | Site is live; next day's scheduled run succeeds |
| 6. Extend | Detail panel, change log, trade-off chart | All §7 views working |
| Later | History line chart, only if the change log shows it would earn its place | — |

## 11. Decisions and remaining checks

- [x] **Time zone:** Pacific (`America/Los_Angeles`, so daylight saving is handled).
- [x] **Public site:** acceptable.
- [x] **Models AA doesn't track:** left off the page. The health panel lists them in one line so their absence is visible.
- [ ] **Step-0 outcomes** that may change this design:
  - a §3 field missing from the free tier
  - effort labels outside `effort_order`
  - the pagination shape

  All field paths live in one table, `FIELDS` in `scripts/aa.py`, so a correction is a one-line change.

## Glossary

| Term | Meaning |
| --- | --- |
| Variant | One model at one effort level; one row in the API |
| Dominated | Another variant from the same subscription is no worse on all three measures and clearly better on at least one |
| Tolerance | The difference below which two values count as equal |
| Fingerprint | Hash of the displayed values; a snapshot is written only when it changes |
| Lane | One of the three measure strips in the Lineup |
| Slug | A variant's stable ID in the AA API |
| Snapshot | A saved copy of the trimmed data, written when it changes |
