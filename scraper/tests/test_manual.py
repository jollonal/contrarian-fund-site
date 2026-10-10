import datetime as dt
import logging

import yaml

from vernissage.__main__ import manual_events
from vernissage.build import normalize

VENUES = [{"id": "coulisse", "name": "Coulisse Gallery", "homepage": "https://www.coulisse-gallery.com/",
           "address": "Gävlegatan 10 B, 113 30 Stockholm", "district": "Vasastan"}]
LOG = logging.getLogger("test")
OV = yaml.safe_load("""
events:
  - venue: coulisse
    title: Thom Trojanowski
    artists: [Thom Trojanowski]
    start_date: 2026-11-05
  - venue: nowhere
    title: X
    start_date: 2026-11-05
  - venue: coulisse
    title: Opening known
    start_date: 2026-11-12
    opening_date: 2026-11-12
    opening_start: "17:00"
    opening_end: "20:00"
""")


def test_manual_events_shape_and_validation():
    out = manual_events(OV["events"], VENUES, LOG)
    assert [vid for vid, _ in out] == ["coulisse", "coulisse"]  # unknown venue skipped
    item = out[0][1]
    assert item["start_date"] == "2026-11-05" and item["opening"]["date"] is None
    assert out[1][1]["opening"] == {"date": "2026-11-12", "start_time": "17:00", "end_time": "20:00", "text": None}


def test_manual_events_reach_the_page_and_merge_with_scraped_copy():
    found = {"coulisse": [{"title": "Real Title", "artists": ["Thom Trojanowski"], "start_date": "2026-11-05",
                           "kind": "exhibition", "confidence": 1, "opening": {}}]}
    for vid, item in manual_events(OV["events"], VENUES, LOG):
        found.setdefault(vid, []).append(item)
    events, review = normalize(found, VENUES, dt.date(2026, 10, 11), 60)
    assert not review
    titles = sorted(e["title"] for e in events)
    assert titles == ["Opening known", "Real Title"]  # manual Trojanowski merged into the scraped show
    known = next(e for e in events if e["title"] == "Opening known")
    assert known["opening"]["confirmed"] and known["opening"]["start_time"] == "17:00"
