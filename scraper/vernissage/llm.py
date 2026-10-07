"""Structured extraction via GitHub Models (free, OpenAI-compatible, auth with GITHUB_TOKEN)."""
from __future__ import annotations

import json
import os
import time

import requests

ENDPOINT = os.environ.get("LLM_ENDPOINT", "https://models.github.ai/inference/chat/completions")
MODEL = os.environ.get("LLM_MODEL", "openai/gpt-4.1-mini")
MAX_CALLS = int(os.environ.get("LLM_MAX_CALLS", "120"))  # free tier: 150/day on low-tier models
SPACING = 4.5  # free tier: 15 requests/minute

PROMPT_VERSION = "2026-10-07.1"  # bump to invalidate cached extractions

LISTING_SYSTEM = """You extract art exhibition listings from the text of a Stockholm gallery web page.
Return a single JSON object and nothing else. Never invent facts that are not in the text."""

LISTING_USER = """Today is {today} (Europe/Stockholm).
Gallery: {venue}
Page URL: {url}

Return JSON of the form:
{{"exhibitions": [{{
  "title": string,                     // exhibition title; if none, the artist name(s)
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


class LLM:
    def __init__(self) -> None:
        self.token = os.environ.get("GITHUB_TOKEN")
        self.calls = 0
        self._last = 0.0

    @property
    def available(self) -> bool:
        return bool(self.token) and self.calls < MAX_CALLS

    def json(self, system: str, user: str, max_tokens: int = 2500) -> dict:
        if not self.available:
            raise LLMUnavailable("no token or call budget exhausted")
        for attempt in range(3):
            wait = self._last + SPACING - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.calls += 1
            r = requests.post(
                ENDPOINT,
                headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
                json={
                    "model": MODEL,
                    "temperature": 0,
                    "max_tokens": max_tokens,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=120,
            )
            if r.status_code == 429:
                if attempt == 2:
                    raise LLMUnavailable("rate limited")
                time.sleep(int(r.headers.get("Retry-After", "60")) if r.headers.get("Retry-After", "").isdigit() else 60)
                continue
            if r.status_code == 413 or (r.status_code == 400 and "token" in r.text.lower()):
                # input too large for the free tier: halve the page text and retry
                head, sep, page = user.rpartition("TEXT:\n") if "TEXT:\n" in user else user.rpartition("EXCERPTS:\n")
                user = head + sep + page[: len(page) // 2]
                continue
            if r.status_code >= 400:
                raise LLMUnavailable(f"HTTP {r.status_code}: {r.text[:200]}")
            content = r.json()["choices"][0]["message"]["content"]
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                continue
        raise LLMUnavailable("no valid JSON after retries")
