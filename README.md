# AI Model Lineup

A one-page dashboard comparing the models in ChatGPT Plus, Claude Pro and Google AI Pro on three measures: intelligence, cost per task, and wait before answer. It covers every effort level, uses [Artificial Analysis](https://artificialanalysis.ai/) data, and refreshes every 3 hours. Hosting and scheduling are free (GitHub Pages and GitHub Actions).

Design: [docs/DESIGN.md](docs/DESIGN.md).

## Setup

1. **Get a free API key** at [Artificial Analysis](https://artificialanalysis.ai/api-key-management-redirect).
2. **Run the step-0 spike.** It uses 1–3 of the 100 daily API calls and saves the full response to the git-ignored `spike/` folder:
   ```bash
   AA_API_KEY=your-key python3 scripts/discover.py --save spike/response.json
   ```
   Check the output:
   - every model has rows
   - effort labels are recognised
   - every field shows `missing=0`

   If a field path is wrong, fix it in `FIELDS` in `scripts/aa.py`.
3. **Pin slugs and set the axes.** Run this, then review the diff of `config/slate.json`:
   ```bash
   python3 scripts/discover.py --from-file spike/response.json --write
   ```
   Then set `config/axes.json` from the "Observed ranges" the script printed, leaving some headroom.
4. **Try it locally:**
   ```bash
   AA_API_KEY=your-key python3 scripts/fetch.py && python3 scripts/build.py && python3 -m http.server -d site 8765
   ```
5. **Create a public GitHub repository** and push this folder to it. Then:
   - Under **Settings → Secrets and variables → Actions**, add `AA_API_KEY`.
   - Under **Settings → Pages**, set **Source** to "GitHub Actions".
6. **First run.** Under **Actions → daily-refresh → Run workflow**, tick **force** and run it. The site address appears in the run summary.

## Day to day

- The job runs every 3 hours, at 43 minutes past 00:00, 03:00, 06:00 … 21:00 UTC. A run that comes less than 2 hours after the last check (a code push, say) skips the fetch, so it spends no API calls. About 8 checks a day use roughly 8–24 of the 100 daily calls.
- A snapshot is committed only when a displayed value changes. Snapshots are one file per Pacific day, so a second change on the same day overwrites that day's file. `data/last_checked.json` is committed on every run, which keeps the scheduled workflow from being disabled after 60 days without activity.
- The **Data health** panel lists anything that needs you, each with the exact line to paste:
  - new effort variants
  - pinned variants that went missing
  - new models from OpenAI, Anthropic or Google
  - values outside the axis range
- The badge turns amber when the last check is more than 9 hours old (two missed runs), and red after 24 hours.

## Development

```bash
python3 -m unittest discover -s tests             # 50 tests, standard library only
python3 scripts/build.py --demo                   # synthetic data -> site/data (preview only)
python3 -m http.server -d site 8765
python3 tests/fixtures/make_fixtures.py           # regenerate synthetic fixtures
```

The fixtures are **synthetic**. After the spike, replace `tests/fixtures/sample_response.json` with a trimmed real response so the parsing tests run against real field shapes.

Data: [Artificial Analysis](https://artificialanalysis.ai/). Attribution is required, and the page footer provides it.
