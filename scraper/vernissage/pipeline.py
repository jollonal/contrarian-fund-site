"""Fetch venue pages, extract exhibitions (cached by content hash), enrich openings from detail pages."""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import urllib.parse

from . import llm as L
from .clean import html_to_text, opening_snippets
from .fetch import FetchError, Fetcher

log = logging.getLogger("vernissage")
STALE_DAYS = 14  # reuse a cached extraction this long if a page stops responding


def _hash(text: str) -> str:
    return hashlib.sha256((L.PROMPT_VERSION + text).encode()).hexdigest()[:16]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _fresh(entry: dict, today: dt.date) -> bool:
    ok = entry.get("ok_at")
    return bool(ok) and (today - dt.date.fromisoformat(ok[:10])).days <= STALE_DAYS


def scrape(venues: list[dict], cache: dict, today: dt.date, window: int,
           fetcher: Fetcher, llm: L.LLM, dry_run: bool = False) -> dict[str, list[dict]]:
    """Return {venue_id: [exhibition, ...]}, updating cache in place."""
    pages, details = cache.setdefault("pages", {}), cache.setdefault("details", {})
    found: dict[str, list[dict]] = {}

    for v in venues:
        items: list[dict] = []
        for url in v.get("exhibitions_urls", []):
            entry = pages.get(url, {})
            try:
                html = fetcher.render(url) if v.get("adapter") == "headless" else fetcher.get(url)
                text = html_to_text(html, url)
            except FetchError as e:
                log.warning("fetch failed %s", e)
                if _fresh(entry, today):
                    items += entry.get("result", [])
                continue

            h = _hash(text)
            if dry_run:
                log.info("%-22s %6d chars  %s", v["id"], len(text), url)
                continue
            if entry.get("hash") == h and "result" in entry:
                entry["ok_at"] = _now()
                items += entry["result"]
                continue
            try:
                out = llm.json(L.LISTING_SYSTEM, L.LISTING_USER.format(
                    today=today.isoformat(), venue=v["name"], url=url, text=text))
                result = [x for x in out.get("exhibitions", []) if isinstance(x, dict)]
            except L.LLMUnavailable as e:
                log.warning("llm unavailable for %s: %s", url, e)
                if _fresh(entry, today):
                    items += entry.get("result", [])
                continue
            pages[url] = {"hash": h, "ok_at": _now(), "result": result}
            items += result
            log.info("%-22s %2d items  %s", v["id"], len(result), url)

        if not dry_run:
            _enrich_openings(v, items, details, today, window, fetcher, llm)
        found[v["id"]] = items
    return found


def _enrich_openings(v: dict, items: list[dict], details: dict, today: dt.date, window: int,
                     fetcher: Fetcher, llm: L.LLM) -> None:
    """For shows starting in the window with no opening stated, look on the show's own page."""
    host = urllib.parse.urlsplit(v["homepage"]).netloc.removeprefix("www.")
    horizon = today + dt.timedelta(days=window)
    for x in items:
        if (x.get("opening") or {}).get("date"):
            continue
        url = x.get("detail_url")
        try:
            start = dt.date.fromisoformat(x.get("start_date") or "")
        except ValueError:
            continue
        if not url or not (today <= start <= horizon):
            continue
        if urllib.parse.urlsplit(url).netloc.removeprefix("www.") != host:
            continue

        entry = details.get(url, {})
        try:
            html = fetcher.render(url) if v.get("adapter") == "headless" else fetcher.get(url)
        except FetchError as e:
            log.warning("detail fetch failed %s", e)
            if entry.get("result"):
                x.update(entry["result"])
            continue
        snippets = opening_snippets(html_to_text(html, url))
        h = _hash(snippets)
        if entry.get("hash") == h:
            if entry.get("result"):
                x.update(entry["result"])
            continue
        result = None
        if snippets:  # spend an LLM call only when the page mentions an opening
            try:
                out = llm.json(L.LISTING_SYSTEM, L.DETAIL_USER.format(
                    today=today.isoformat(), title=x.get("title"), venue=v["name"],
                    start=x.get("start_date"), end=x.get("end_date"), text=snippets), max_tokens=400)
                if (out.get("opening") or {}).get("date"):
                    result = {"opening": out["opening"], "access": out.get("access", "unknown")}
            except L.LLMUnavailable as e:
                log.warning("llm unavailable for detail %s: %s", url, e)
                continue
        details[url] = {"hash": h, "result": result}
        if result:
            x.update(result)
