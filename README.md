# contrarian.fund

Static site for [www.contrarian.fund](https://www.contrarian.fund), plus **Stockholm Vernissages**: a self-updating list of exhibition openings at Stockholm galleries over the next 60 days, served at `/projects/vernissage/`.

## How the vernissage pipeline works

```
venues.yaml ──► fetch (robots.txt, 4 s/host) ──► HTML to text ──► content hash
                                                                     │ unchanged: reuse cached extraction
                                                                     ▼ changed
                                                   LLM extraction to JSON (Cloudflare Workers AI, free tier)
                                                                     │
          show page mentions "vernissage"/"opening"? ──► second, smaller extraction
                                                                     ▼
                            validate, dedupe, 60-day window ──► events.json, .ics, index.html
```

- **Registry:** `scraper/venues.yaml` is hand-curated. Each venue has its exhibitions URLs, address, district, and an `adapter` (`headless` means it is rendered with Playwright first; `next_data` reads a Next.js page's embedded data, choosing entries with `next_data_keys`).
- **Extraction:** page text goes to a small model with a strict JSON schema. Results are cached by content hash in `scraper/state/cache.json`, so an unchanged page costs nothing. A typical night makes a handful of model calls.
- **Openings:** most galleries publish only date ranges. The page shows every exhibition opening in the window and marks those with a published vernissage time as confirmed.
- **Safety:** low-confidence or inconsistent items go to `scraper/state/review.json` instead of the page. If a site or the model is down, the last good extraction is reused for up to 14 days.
- **Output:** static files only. No server, no database, no client-side data fetching.

## Nightly run

`.github/workflows/nightly.yml` runs at 02:17 UTC. It tests, scrapes, commits refreshed data and deploys `site/` to Cloudflare Pages with Wrangler. A push that changes `site/` deploys immediately without scraping.

Repository secrets required:

| Secret | Value |
|---|---|
| `CLOUDFLARE_API_TOKEN` | API token with Cloudflare Pages Edit, Workers AI Read, Workers AI Edit |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID |
| `MAIL_ADDRESS` | Dedicated mailbox that receives the gallery newsletters (GMX) |
| `MAIL_PASSWORD` | Password for that mailbox, used nowhere else |
| `MAIL_HOST` | Optional. IMAP server; default `imap.gmx.com` (Yahoo: `imap.mail.yahoo.com`) |

The same Cloudflare token also calls Workers AI (`@cf/meta/llama-3.3-70b-instruct-fp8-fast`, JSON mode). It needs Workers AI Read and Edit in addition to Pages Edit. The free allocation is 10,000 neurons per UTC day. Each run counts the neurons it spends (from the token counts Workers AI returns) and stops at 9,500 (`LLM_NEURON_BUDGET`). At roughly 110 neurons per call, that is about 85 calls; once pages are cached, a typical night needs only a few.

## Local use

```bash
cd scraper
pip install -r requirements.txt && python -m playwright install chromium
python -m pytest -q                       # unit tests
python -m vernissage --dry-run            # fetch only, report page sizes
CF_ACCOUNT_ID=<id> CF_API_TOKEN=<token> python -m vernissage
python -m vernissage --no-fetch           # rebuild page from cache
```

## Gallery newsletters

Many galleries announce vernissage times only by email. They are sent to a dedicated GMX mailbox that holds nothing else, so losing it would cost only newsletters. Only the scheduled nightly logs in (once, read-only, over IMAP with the `MAIL_ADDRESS` and `MAIL_PASSWORD` secrets); a manual run reads the mailbox only when its "read_mail" box is ticked. Each run fetches only messages that arrived since the last one (tracked by IMAP UID in `scraper/state/cache.json`), matches each email to a gallery by sender domain (`mail_domains` in `venues.yaml`) or gallery name, and extracts exhibitions with the same model call as the web pages. A newsletter's opening time is merged into the website's listing of the same show. Messages sent from the mailbox's own address are skipped. Emails from senders that match no gallery are re-checked on every run (matching costs no AI), so adding a `mail_domains` entry later picks them up. If the log says "login refused", check the password and that POP3/IMAP access is still switched on in GMX (GMX switches it off after a long period without use). Never connect a mailbox that holds anything else: if a provider objects to logins from GitHub's servers, only this mailbox is at stake.

Because the repo and its logs are public, nothing from an email is logged or committed except the extracted exhibition facts: message ids are hashed, and senders, subjects and links are dropped. Events found only by email link to the gallery's homepage.

## English titles

Titles stay in the original language. If the gallery publishes its own English title, it is shown after a slash. Otherwise the model's English translation is shown in [brackets], marking it as a machine gloss. Glosses are cleaned before display (stray quotes and punctuation, notes in parentheses, alternatives after a slash) and set in title case, Chicago style.

`scraper/state/glosses.md` lists every English title currently on the page. To correct one, add the original title and the replacement to `scraper/overrides.yaml` (or `""` to hide the gloss). Saving that file on GitHub rebuilds and redeploys the page from cache in about two minutes, with no AI calls. The same file's `events:` list adds shows by hand when a gallery announces one the scraper cannot read; the format is described at the top of the file.

## Map

The page embeds an OpenStreetMap map (Leaflet) with one numbered pin per gallery; filled pins have an opening in the window, and each pin's pop-up lists them. Addresses are geocoded once with Nominatim (one request per second, cached in `scraper/state/geocode.json`; misses are retried after 7 days). If a location is wrong, set `coords: [lat, lon]` on the venue in `venues.yaml`. Every address also links to Google Maps.

## Adding a venue

Add an entry to `scraper/venues.yaml`, then run `python -m vernissage --dry-run --only <id>` to check the page fetches and its text size.
