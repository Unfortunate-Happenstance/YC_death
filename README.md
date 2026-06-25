# YC Death Watch

Dashboard of Y Combinator batch vulnerability scores from [deathbyclawd.com](https://deathbyclawd.com).

## Quick start

Uses [uv](https://docs.astral.sh/uv/) for Python + dependencies (`.venv` is created automatically).

```bash
# Install deps
uv sync

# 1. Fetch YC companies (last 4 years)
uv run python scripts/fetch_yc.py

# 2. Scrape one batch first — preview dashboard while rest runs
uv run python scripts/scrape_death.py --batch "Fall 2026"

# 3. Preview locally
uv run python -m http.server 8080
# → http://localhost:8080

# 4. Scrape remaining batches (resumable, run overnight in another terminal)
uv run python scripts/scrape_death.py --remaining
```

After each batch finishes, `data/dashboard.json` is regenerated — refresh the browser to see new data.

## Parallel workflow

| Step | What | Time |
|------|------|------|
| fetch_yc | All ~2,750 companies + issues export | ~2 min |
| scrape --batch "Fall 2026" | 4 companies, seed dashboard | ~1 min |
| Build/deploy dashboard | GitHub Pages | instant |
| scrape --remaining | Rest of batches | ~12–14 hrs |

## Issues for manual review

After `fetch_yc.py`, check:

- `data/issues/issues.json`
- `data/issues/issues.md`

During scraping:

- `data/issues/api_failed.json` — API errors
- `data/issues/api_garbage.json` — suspicious ALREADY DEAD / score 99 results

Manual fixes live in `data/manual_overrides.json` (domain overrides + exclusions). Re-run `fetch_yc.py` after editing.

## GitHub Pages

1. Push to GitHub
2. Settings → Pages → Source: **GitHub Actions**
3. Push triggers deploy via `.github/workflows/deploy.yml`

## Data files

```
data/
├── companies.json       # All YC companies (from Algolia)
├── dashboard.json       # Merged data for the static site
├── manifest.json        # Per-batch scrape progress
├── batches/             # One JSON file per batch
└── issues/              # Manual review queues
```
