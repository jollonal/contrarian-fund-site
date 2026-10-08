"""Read gallery newsletters from the Gmail "vernissage" label and extract openings.

Privacy rules (the repo and its Actions logs are public):
- only the label is opened, read-only, so nothing is marked as read;
- nothing from an email is logged or stored except extracted exhibition facts;
- message ids are stored as hashes; addresses, subjects and links are never kept;
- events from email link to the gallery's homepage, never to links in the email.
"""
from __future__ import annotations

import datetime as dt
import email
import email.policy
import email.utils
import hashlib
import imaplib
import logging
import os
import re
import urllib.parse

from . import llm as L
from .clean import html_to_text

log = logging.getLogger("vernissage")

IMAP_HOST = "imap.gmail.com"
LABEL = os.environ.get("GMAIL_LABEL", "vernissage")
LOOKBACK_DAYS = 45      # how far back to look for unread-by-us newsletters
KEEP_DAYS = 120         # how long to remember an email's extraction
MAX_CHARS = 12_000
NO_LINKS_BASE = "https://email.invalid/"  # no link in an email shares this host, so all links are dropped


def _host(url_or_domain: str) -> str:
    h = urllib.parse.urlsplit(url_or_domain).netloc if "//" in url_or_domain else url_or_domain
    return h.lower().removeprefix("www.")


def venue_domains(venues: list[dict]) -> dict[str, str]:
    """Map sender domain -> venue id: the website's domain plus any listed mail_domains."""
    out: dict[str, str] = {}
    for v in venues:
        for d in [_host(v.get("homepage", ""))] + [_host(x) for x in v.get("mail_domains", [])]:
            if d:
                out[d] = v["id"]
    return out


def match_venue(sender: str, text: str, venues: list[dict], domains: dict[str, str]) -> str | None:
    """Identify the gallery: by sender domain (including subdomains), else by its name in the text."""
    addr = email.utils.parseaddr(sender or "")[1].lower()
    dom = addr.rpartition("@")[2]
    while dom:
        if dom in domains:
            return domains[dom]
        dom = dom.partition(".")[2] if dom.count(".") > 1 else ""
    low = (text or "")[:3000].lower()
    hits = [v["id"] for v in venues if v.get("name") and v["name"].lower() in low]
    return hits[0] if len(hits) == 1 else None


def message_text(msg: email.message.EmailMessage) -> str:
    """Plain text of a message: the HTML part reduced to text, else the plain part."""
    html = plain = None
    for part in msg.walk():
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        ctype = part.get_content_type()
        try:
            body = part.get_content()
        except (LookupError, ValueError):
            continue
        if ctype == "text/html" and html is None:
            html = body
        elif ctype == "text/plain" and plain is None:
            plain = body
    if html:
        text = html_to_text(html, NO_LINKS_BASE)
    else:
        text = re.sub(r"https?://\S+", "", plain or "")
    text = re.sub(r"\S+@\S+\.\S+", "", text)  # drop any email addresses
    return text[:MAX_CHARS]


def _key(msg_id: str) -> str:
    return hashlib.sha256(msg_id.encode()).hexdigest()[:16]


def read_newsletters(venues: list[dict], cache: dict, today: dt.date, llm: L.LLM) -> dict[str, list[dict]]:
    """Return {venue_id: [exhibition, ...]} from new and remembered newsletters; updates cache."""
    # excluded venues (closed, moved away, no programme) are ignored unless marked newsletter: true
    venues = [v for v in venues if v.get("status") != "excluded" or v.get("newsletter")]
    store = cache.setdefault("mail", {})
    cutoff = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
    for k in [k for k, e in store.items() if e.get("date", "") < cutoff]:
        del store[k]

    address, password = os.environ.get("GMAIL_ADDRESS"), os.environ.get("GMAIL_APP_PASSWORD")
    if address and password:
        try:
            _fetch_new(address, password.replace(" ", ""), venues, store, today, llm)
        except (imaplib.IMAP4.error, OSError) as e:
            log.warning("newsletter inbox unavailable: %s", type(e).__name__)
    else:
        log.info("newsletters: GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set, using remembered results only")

    found: dict[str, list[dict]] = {}
    for e in store.values():
        if e.get("venue"):
            found.setdefault(e["venue"], []).extend(e.get("result", []))
    return found


def _fetch_new(address: str, password: str, venues: list[dict], store: dict, today: dt.date, llm: L.LLM) -> None:
    domains = venue_domains(venues)
    by_id = {v["id"]: v for v in venues}
    since = (today - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")
    seen = new = unmatched = 0

    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        imap.login(address, password)
        status, _ = imap.select(f'"{LABEL}"', readonly=True)
        if status != "OK":
            log.warning("newsletters: label not found")
            return
        status, data = imap.search(None, "SINCE", since)
        ids = data[0].split() if status == "OK" and data and data[0] else []
        for num in ids:
            status, parts = imap.fetch(num, "(BODY.PEEK[])")
            if status != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            msg = email.message_from_bytes(parts[0][1], policy=email.policy.default)
            seen += 1
            key = _key(str(msg.get("Message-ID") or msg.get("Date", "")) + str(num))
            if key in store:
                continue
            text = message_text(msg)
            venue_id = match_venue(str(msg.get("From", "")), text, venues, domains)
            sent = email.utils.parsedate_to_datetime(msg["Date"]).date().isoformat() if msg.get("Date") else today.isoformat()
            if not venue_id:
                unmatched += 1
                store[key] = {"date": sent, "venue": None, "result": []}
                continue
            if not llm.available:
                break  # retry tomorrow; nothing recorded for this message
            v = by_id[venue_id]
            try:
                out = llm.json(L.LISTING_SYSTEM, L.LISTING_USER.format(
                    today=today.isoformat(), venue=v["name"],
                    url=f"newsletter email sent {sent}", text=text))
            except L.LLMUnavailable as e:
                log.warning("newsletters: model unavailable (%s)", type(e).__name__)
                break
            result = []
            for x in out.get("exhibitions", []):
                if isinstance(x, dict):
                    x["detail_url"] = None           # never publish links from emails
                    x["source"] = "newsletter"
                    result.append(x)
            store[key] = {"date": sent, "venue": venue_id, "result": result}
            new += 1
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    log.info("newsletters: %d in label, %d newly read, %d from unknown senders", seen, new, unmatched)
