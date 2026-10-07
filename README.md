# contrarian.fund

Static site for [www.contrarian.fund](https://www.contrarian.fund), plus **Stockholm Vernissages**: a self-updating list of exhibition openings at Stockholm galleries over the next 60 days, served at `/projects/vernissage/`.

## How the vernissage pipeline works

```
venues.yaml ──► fetch (robots.txt, 4 s/host) ──► HTML to text ──► content hash
                                                                     │ unchanged: reuse cached extraction
                                                                     ▼ changed
                                                   LLM extraction to JSON (GitHub Models, free)
                                                                     │
          show page mentions "vernissage"/"opening"? ──► second, smaller extraction
                                                                     ▼
                            validate, dedupe, 60-day window ──► events.json, .ics, index.html
```

- **Registry:** `scraper/venues.yaml` is hand-curated. Each venue has its exhibitions URLs, address, district, and an `adapter` (`headless` means it is rendered with Playwright first).
- **Extraction:** page text goes to a small model with a strict JSON schema. Results are cached by content hash in `scraper/state/cache.json`, so an unchanged page costs nothing. A typical night makes a handful of model calls.
- **Openings:** most galleries publish only date ranges. The page shows every exhibition opening in the window and marks those with a published vernissage time as confirmed.
- **Safety:** low-confidence or inconsistent items go to `scraper/state/review.json` instead of the page. If a site or the model is down, the last good extraction is reused for up to 14 days.
- **Output:** static files only. No server, no database, no client-side data fetching.

## Nightly run

`.github/workflows/nightly.yml` runs at 02:17 UTC. It tests, scrapes, commits refreshed data and deploys `site/` to Cloudflare Pages with Wrangler. A push that changes `site/` deploys immediately without scraping.

Repository secrets required:

| Secret | Value |
|---|---|
| `CLOUDFLARE_API_TOKEN` | API token with Account, Cloudflare Pages, Edit |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID |

The model call uses the workflow's built-in `GITHUB_TOKEN` (`permissions: models: read`). No model API key is needed.

## Local use

```bash
cd scraper
pip install -r requirements.txt && python -m playwright install chromium
python -m pytest -q                       # unit tests
python -m vernissage --dry-run            # fetch only, report page sizes
GITHUB_TOKEN=<fine-grained PAT with Models: read> python -m vernissage
python -m vernissage --no-fetch           # rebuild page from cache
```

## Adding a venue

Add an entry to `scraper/venues.yaml`, then run `python -m vernissage --dry-run --only <id>` to check the page fetches and fits the model's input limit.
