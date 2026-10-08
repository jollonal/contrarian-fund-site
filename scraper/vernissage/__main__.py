"""Run: python -m vernissage [--dry-run] [--no-fetch] [--only ID ...] [--window 60]

  --dry-run   fetch pages and report text sizes; no LLM calls, no outputs
  --no-fetch  rebuild outputs from the cache only (no network)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .build import gloss_report, normalize, write_outputs
from .fetch import Fetcher
from .llm import LLM

ROOT = Path(__file__).resolve().parents[1]          # scraper/
SITE = ROOT.parent / "site" / "projects" / "vernissage"


def main() -> int:
    ap = argparse.ArgumentParser(prog="vernissage")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--window", type=int, default=60)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("vernissage")

    venues = yaml.safe_load((ROOT / "venues.yaml").read_text(encoding="utf-8"))["venues"]
    active = [v for v in venues if v.get("status") == "active" and (not a.only or v["id"] in a.only)]
    cache_path = ROOT / "state" / "cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    today = dt.datetime.now(ZoneInfo("Europe/Stockholm")).date()

    if a.no_fetch:
        found = {v["id"]: [x for u in v.get("exhibitions_urls", [])
                           for x in cache["pages"].get(u, {}).get("result", [])] for v in active}
        for e in cache.get("mail", {}).values():
            if e.get("venue"):
                found.setdefault(e["venue"], []).extend(e.get("result", []))
        llm = None
    else:
        from .pipeline import scrape

        llm = LLM()
        if not llm.token and not a.dry_run:
            log.error("CF_ACCOUNT_ID / CF_API_TOKEN not set; use --dry-run or --no-fetch")
            return 2
        fetcher = Fetcher()
        try:
            found = scrape(active, cache, today, a.window, fetcher, llm, dry_run=a.dry_run)
        finally:
            fetcher.close()
        if a.dry_run:
            return 0
        if not a.only:
            from .mail import read_newsletters
            for vid, items in read_newsletters(venues, cache, today, llm).items():
                found.setdefault(vid, []).extend(items)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    ov_path = ROOT / "overrides.yaml"
    overrides = (yaml.safe_load(ov_path.read_text(encoding="utf-8")) or {}).get("glosses") if ov_path.exists() else None
    events, review = normalize(found, venues, today, a.window, overrides or {})
    (ROOT / "state" / "glosses.md").write_text(gloss_report(events), encoding="utf-8")
    (ROOT / "state" / "review.json").write_text(json.dumps(review, ensure_ascii=False, indent=1), encoding="utf-8")
    write_outputs(events, venues, SITE, today, a.window, os.environ.get("REPO_URL"))
    log.info("%d events (%d confirmed openings), %d for review, %s LLM calls, ~%s neurons",
             len(events), sum(e["opening"]["confirmed"] for e in events), len(review),
             llm.calls if llm else 0, f"{llm.neurons:,.0f}" if llm else 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
