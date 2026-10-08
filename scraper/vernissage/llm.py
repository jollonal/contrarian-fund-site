"""Structured extraction via Cloudflare Workers AI (free tier: 10,000 neurons/day, resets 00:00 UTC)."""
from __future__ import annotations

import json
import os
import re
import time

import requests

ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
API_TOKEN = os.environ.get("CF_API_TOKEN", "")
MODEL = os.environ.get("LLM_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
ENDPOINT = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{MODEL}"
# About 250 neurons per listing call on this model, so ~35 calls stays inside the free 10,000.
# A first run may need two nights to fill the cache; after that only changed pages cost anything.
MAX_CALLS = int(os.environ.get("LLM_MAX_CALLS", "200"))  # backstop only; the neuron budget governs
# Free Workers AI allocation is 10,000 neurons per UTC day. Stop each run at this many.
NEURON_BUDGET = int(os.environ.get("LLM_NEURON_BUDGET", "9500"))
# Published rates for llama-3.3-70b-instruct-fp8-fast, neurons per million tokens.
# Update both if LLM_MODEL changes.
NEURONS_PER_M_IN = 26_668
NEURONS_PER_M_OUT = 204_805
RESERVE = 400  # headroom kept for the next call (about 4,000 tokens in, 1,500 out)
SPACING = 1.0

PROMPT_VERSION = "2026-10-09.2"  # bump to invalidate cached extractions

_S = {"type": ["string", "null"]}
OPENING_SCHEMA = {"type": "object", "properties": {
    "date": _S, "start_time": _S, "end_time": _S, "text": _S}}
LISTING_SCHEMA = {"type": "object", "properties": {"exhibitions": {"type": "array", "items": {
    "type": "object",
    "properties": {
        "title": {"type": "string"}, "title_en": _S, "title_gloss": _S,
        "artists": {"type": "array", "items": {"type": "string"}},
        "start_date": _S, "end_date": _S, "city": _S,
        "kind": {"type": "string", "enum": ["exhibition", "fair", "event", "other"]},
        "detail_url": _S, "opening": OPENING_SCHEMA,
        "access": {"type": "string", "enum": ["public", "invite", "unknown"]},
        "confidence": {"type": "number"},
    },
    "required": ["title", "start_date", "end_date", "kind", "opening", "confidence"],
}}}, "required": ["exhibitions"]}
DETAIL_SCHEMA = {"type": "object", "properties": {
    "opening": OPENING_SCHEMA,
    "access": {"type": "string", "enum": ["public", "invite", "unknown"]},
    "confidence": {"type": "number"}}, "required": ["opening"]}

LISTING_SYSTEM = """You extract art exhibition listings from the text of a Stockholm gallery web page.
Return a single JSON object and nothing else. Never invent facts that are not in the text."""

LISTING_USER = """Today is {today} (Europe/Stockholm).
Gallery: {venue}
Page URL: {url}

Return JSON of the form:
{{"exhibitions": [{{
  "title": string,                     // the exhibition title, copied exactly
  "title_en": string | null,           // the gallery's OWN English title, only if printed on the page
  "title_gloss": string | null,        // your plain English translation of a non-English title
  "artists": [string],
  "start_date": "YYYY-MM-DD" | null,
  "end_date": "YYYY-MM-DD" | null,
  "city": string | null,               // city of the venue showing it, if stated
  "kind": "exhibition" | "fair" | "event" | "other",
  "detail_url": string | null,         // the <url> given next to this item, copied exactly
  "opening": {{"date": "YYYY-MM-DD" | null, "start_time": "HH:MM" | null,
               "end_time": "HH:MM" | null, "text": string | null}},
  "access": "public" | "invite" | "unknown",
  "confidence": number                 // 0 to 1, your certainty the dates are right
}}]}}

Rules:
- "title" is the exhibition's own title, copied character for character with the page's spelling, spacing and capitalisation. Many pages print the artist name on one line and the title on the next: the title is the second line, not the artist. Use the artist name as the title only when the page gives no title at all.
- "title_en": fill only when the page itself prints an English version of the title (for example "Nya målningar / New Paintings" gives title_en "New Paintings"). Never invent it.
- "title_gloss": when the title is not in English and title_en is null, give a short, plain English translation that tells a reader what the show is about. Keep names untranslated. Null if the title is already English or is only a name.
- Include only exhibitions that end on or after {today}, or start after it, or whose dates are unknown.
- "opening" is the vernissage / opening reception / "Öppning" / "Reception". Leave all its fields null unless the text states it. Do not assume the opening is on the start date.
- Text may be Swedish or English. Swedish months: januari februari mars april maj juni juli augusti september oktober november december. Weekdays: måndag tisdag onsdag torsdag fredag lördag söndag. "kl. 17-19" means 17:00 to 19:00. "t o m" means until.
- If a year is missing, choose the year that puts the date closest to today.
- Art fairs, talks, screenings and book launches are kind "fair" or "event", not "exhibition".
- "text" is the opening sentence copied verbatim from the page.
- If nothing qualifies, return {{"exhibitions": []}}.

PAGE TEXT:
{text}"""

DETAIL_USER = """Today is {today} (Europe/Stockholm).
Exhibition: {title} at {venue}, running {start} to {end}.

Below are excerpts from the exhibition's own page around words like vernissage/opening.
Return JSON: {{"opening": {{"date": "YYYY-MM-DD" | null, "start_time": "HH:MM" | null,
"end_time": "HH:MM" | null, "text": string | null}}, "access": "public" | "invite" | "unknown",
"confidence": number}}
Fill "opening" only if an opening reception for THIS exhibition in Stockholm is stated. Copy the sentence verbatim into "text".

EXCERPTS:
{text}"""


class LLMUnavailable(Exception):
    pass


def _parse(resp) -> dict:
    """Workers AI returns either a parsed object or a JSON string in result.response."""
    if isinstance(resp, dict):
        return resp
    if isinstance(resp, str):
        m = re.search(r"\{.*\}", resp, re.S)
        if m:
            return json.loads(m.group(0))
    raise ValueError("no JSON object in model output")


class LLM:
    def __init__(self) -> None:
        self.token = API_TOKEN if ACCOUNT_ID else ""
        self.calls = 0
        self.neurons = 0.0
        self.exhausted = False
        self._last = 0.0

    @property
    def available(self) -> bool:
        return (bool(self.token) and not self.exhausted and self.calls < MAX_CALLS
                and self.neurons + RESERVE <= NEURON_BUDGET)

    def _why_unavailable(self) -> str:
        if not self.token:
            return "no credentials"
        if self.exhausted:
            return "daily allocation used"
        return f"run budget reached ({self.neurons:,.0f} of {NEURON_BUDGET:,} neurons, {self.calls} calls)"

    def _charge(self, data, prompt: str, reply) -> None:
        """Add this call's neurons, from the token counts Workers AI returns (else a length estimate)."""
        result = data.get("result") if isinstance(data, dict) else None
        usage = (result.get("usage") if isinstance(result, dict) else None) or {}
        tin = usage.get("prompt_tokens") or len(prompt) / 3.5
        tout = usage.get("completion_tokens")
        if tout is None:
            tout = len(json.dumps(reply, ensure_ascii=False)) / 3.5 if reply else 0
        self.neurons += tin * NEURONS_PER_M_IN / 1e6 + tout * NEURONS_PER_M_OUT / 1e6

    def json(self, system: str, user: str, max_tokens: int = 2500) -> dict:
        if not self.available:
            raise LLMUnavailable(self._why_unavailable())
        schema = DETAIL_SCHEMA if "EXCERPTS:" in user else LISTING_SCHEMA
        use_schema = True
        for attempt in range(3):
            wait = self._last + SPACING - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.calls += 1
            body = {
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": max_tokens,
                "temperature": 0,
            }
            if use_schema:
                body["response_format"] = {"type": "json_schema", "json_schema": schema}
            try:
                r = requests.post(ENDPOINT, headers={"Authorization": f"Bearer {self.token}"},
                                  json=body, timeout=120)
            except requests.RequestException as e:
                raise LLMUnavailable(f"network: {e}") from e
            try:
                data = r.json()
            except ValueError:
                raise LLMUnavailable(f"HTTP {r.status_code}, non-JSON body: {r.text[:200]!r}")
            if r.ok:
                self._charge(data, system + user, (data.get("result") or {}).get("response"))

            if r.status_code == 429 or "neuron" in r.text.lower() and r.status_code >= 400:
                self.exhausted = True  # daily free allocation used; stop calling until tomorrow
                raise LLMUnavailable(f"HTTP {r.status_code}: {r.text[:200]}")
            if not data.get("success", r.ok):
                msg = json.dumps(data.get("errors", []))[:300]
                if use_schema and "json mode" in msg.lower():
                    use_schema = False  # schema could not be met; retry as plain text and parse
                    continue
                raise LLMUnavailable(f"HTTP {r.status_code}: {msg}")
            try:
                return _parse((data.get("result") or {}).get("response"))
            except (ValueError, json.JSONDecodeError):
                use_schema = False
                continue
        raise LLMUnavailable("no valid JSON after retries")
