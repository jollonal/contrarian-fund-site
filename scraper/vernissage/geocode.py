"""Venue coordinates for the map, from OpenStreetMap's Nominatim, cached in state/geocode.json.

Nominatim usage policy: at most one request per second, an identifying User-Agent,
and results cached. Each address is looked up once; a miss is retried after 7 days.
A venue can also carry `coords: [lat, lon]` in venues.yaml, which always wins.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import re
import time
from pathlib import Path

import requests

from .fetch import UA

log = logging.getLogger("vernissage")

ENDPOINT = "https://nominatim.openstreetmap.org/search"
RETRY_MISS_DAYS = 7
SPREAD = 0.00012  # degrees; pins at the same address are fanned out by about 13 m


def candidates(address: str) -> list[str]:
    """Queries to try, most specific first: as written, without leading place names
    ("Färgkontoret, ..."), then street and number with the city only."""
    parts = [p.strip() for p in address.split(",") if p.strip()]
    out = [", ".join(parts)]
    while parts and not re.search(r"\d", parts[0]):
        parts = parts[1:]
    if parts:
        out.append(", ".join(parts))
        street = parts[0]
        city = re.sub(r"^\d{3}\s?\d{2}\s*", "", parts[-1]) if len(parts) > 1 else "Stockholm"
        out.append(f"{street}, {city or 'Stockholm'}")
    seen, uniq = set(), []
    for q in out:
        if q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq


def _lookup(session, query: str) -> tuple[float, float] | None:
    r = session.get(ENDPOINT, params={"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "se",
                                      "email": "office@contrarian.fund"}, timeout=30)
    time.sleep(1.1)
    if r.status_code != 200:
        return None
    hits = r.json()
    if not hits:
        return None
    return round(float(hits[0]["lat"]), 6), round(float(hits[0]["lon"]), 6)


def locate(venues: list[dict], cache_path: Path, today: dt.date, session=None) -> dict[str, tuple[float, float]]:
    """Return {venue_id: (lat, lon)} for venues on the map, geocoding new addresses."""
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    out: dict[str, tuple[float, float]] = {}
    looked = missing = 0
    offline = False
    for v in venues:
        if v.get("coords"):
            out[v["id"]] = tuple(v["coords"])
            continue
        addr = (v.get("address") or "").strip()
        if not addr:
            continue
        hit = cache.get(addr)
        stale_miss = hit and hit.get("miss") and (today - dt.date.fromisoformat(hit["miss"])).days >= RETRY_MISS_DAYS
        if (hit is None or stale_miss) and not offline:
            if session is None:
                session = requests.Session()
                session.headers["User-Agent"] = UA
            found = None
            try:
                for q in candidates(addr):
                    found = _lookup(session, q)
                    if found:
                        break
            except (requests.RequestException, ValueError, KeyError) as e:
                log.warning("geocode unavailable: %s", type(e).__name__)
                offline = True  # use cached locations only for the rest of this run
            else:
                looked += 1
                hit = {"lat": found[0], "lon": found[1]} if found else {"miss": today.isoformat()}
                cache[addr] = hit
        if hit and hit.get("lat") is not None:
            out[v["id"]] = (hit["lat"], hit["lon"])
        else:
            missing += 1
            log.warning("geocode: no location for %s", v["id"])
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    log.info("geocode: %d on map, %d looked up, %d missing", len(out), looked, missing)
    return spread(out)


def spread(coords: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
    """Fan out venues that share an address so every pin can be clicked."""
    groups: dict[tuple[float, float], list[str]] = {}
    for vid, (lat, lon) in coords.items():
        groups.setdefault((round(lat, 5), round(lon, 5)), []).append(vid)
    out = dict(coords)
    for ids in groups.values():
        if len(ids) < 2:
            continue
        for i, vid in enumerate(sorted(ids)):
            lat, lon = coords[vid]
            a = 2 * math.pi * i / len(ids)
            out[vid] = (round(lat + SPREAD * math.sin(a), 6),
                        round(lon + SPREAD * 2 * math.cos(a), 6))  # longitude degrees are shorter at 59°N
    return out
