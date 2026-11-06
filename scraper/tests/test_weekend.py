import datetime as dt

import yaml

from vernissage import weekend as W
from vernissage.build import normalize, write_outputs

VENUES = [{"id": "hera", "name": "Galleri Hera", "homepage": "https://gallerihera.se/", "status": "active",
           "address": "Hornsgatan 36, 118 20 Stockholm", "district": "Södermalm"}]
WK = yaml.safe_load("""
name: Gallery Weekend Stockholm
url: https://gallery-weekend-stockholm.com
start: 2026-11-06
end: 2026-11-08
announce_days: 14
hours: {"2026-11-06": "12:00–19:00"}
events:
  - {date: 2026-11-05, venue: hera, kind: Preview, title: Thursday Show, start: "17:00"}
  - {date: 2026-11-06, venue: hera, kind: Opening, title: Das andere Zuhause, artists: [Kai Savelsberg],
     start: "17:00", end: "19:00"}
  - {date: 2026-11-06, gallery: "Guest (Gothenburg)", address: "Hudiksvallsgatan 6, 113 30 Stockholm",
     district: Vasastan, kind: Talk, title: Guest Talk, start: "16:00"}
on_view:
  - {gallery: "Visitor (Malmö)", address: "Hudiksvallsgatan 6, 113 30 Stockholm", district: Vasastan,
     artists: [A, B]}
""")


def test_announced_from_two_weeks_before_until_last_day():
    assert not W.is_announced(WK, dt.date(2026, 10, 22))
    assert W.is_announced(WK, dt.date(2026, 10, 23)) and W.is_announced(WK, dt.date(2026, 11, 8))
    assert not W.is_announced(WK, dt.date(2026, 11, 9)) and not W.is_announced(None, dt.date(2026, 11, 6))


def test_weekend_page_by_day_with_guests_and_past_days(tmp_path):
    W.write(WK, VENUES, tmp_path, dt.date(2026, 11, 6))
    html = (tmp_path / "gallery-weekend" / "index.html").read_text(encoding="utf-8")
    assert html.index("Thursday") < html.index("Friday")
    assert 'class="day past" data-date="2026-11-05"' in html          # Thursday is past on Friday
    assert "Galleries open 12:00–19:00" in html
    assert html.index("Guest Talk") < html.index("Das andere Zuhause")  # by start time
    assert "Opening, 17:00–19:00" in html and "Talk, 16:00" in html
    assert "Hornsgatan 36" in html and "https://gallerihera.se/" in html  # venue details from venues.yaml
    assert "Visitor (Malmö)" in html and "google.com/maps" in html
    assert "\u2014" not in html  # no em dashes on the page


def test_main_page_links_weekend_and_has_day_buttons(tmp_path):
    events, _ = normalize({"hera": [{"title": "Show", "artists": ["X"], "start_date": "2026-11-06",
                                     "kind": "exhibition", "confidence": 1, "opening": {}}]},
                          VENUES, dt.date(2026, 11, 6), 60, {})
    write_outputs(events, VENUES, tmp_path, dt.date(2026, 11, 6), 60, None, weekend=WK)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'href="gallery-weekend/"' in html and "6–8 November" in html
    assert 'data-when="d0"' in html and 'data-when="m2"' in html and 'data-date="2026-11-06"' in html
    write_outputs(events, VENUES, tmp_path, dt.date(2026, 11, 6), 60, None)
    assert 'href="gallery-weekend/"' not in (tmp_path / "index.html").read_text(encoding="utf-8")


def test_hand_correction_wins_but_placeholder_yields():
    import logging
    from vernissage.__main__ import manual_events
    scraped = {"title": "Våra ...", "artists": ["Britta Marakatt-Labba"], "start_date": "2026-11-06",
               "kind": "exhibition", "confidence": 1, "detail_url": "https://gallerihelle.com/x",
               "opening": {"date": "2026-11-06", "start_time": "16:00", "end_time": "19:00", "text": "Vernissage 16–19"}}
    fix = yaml.safe_load("""
- {venue: hera, corrects: true, title: Våra historier, title_en: Our Stories,
   artists: [Britta Marakatt-Labba], start_date: 2026-11-06}
""")
    found = {"hera": [dict(scraped)]}
    for vid, item in manual_events(fix, VENUES, logging.getLogger("t")):
        found[vid].append(item)
    (e,), _ = normalize(found, VENUES, dt.date(2026, 11, 6), 60, {})
    assert e["title"] == "Våra historier" and e["title_en"] == "Our Stories"
    assert e["url"] == "https://gallerihelle.com/x"           # no url given: the scraped link stays
    assert e["opening"]["start_time"] == "16:00" and e["source"] == "web"
    alone, _ = normalize({"hera": [dict(scraped)]}, VENUES, dt.date(2026, 11, 6), 60, {})
    assert e["id"] == alone[0]["id"]                           # calendar id unchanged
    fix[0]["corrects"] = False
    found = {"hera": [dict(scraped)]}
    for vid, item in manual_events(fix, VENUES, logging.getLogger("t")):
        found[vid].append(item)
    (e,), _ = normalize(found, VENUES, dt.date(2026, 11, 6), 60, {})
    assert e["title"] == "Våra ..."                            # placeholder: the site's title wins


def test_weekend_openings_join_main_list_unless_hand_entry_exists():
    from vernissage import weekend as W
    venues = [{"id": "w", "name": "W"}, {"id": "a", "name": "A"}, {"id": "h", "name": "H"}]
    wk = {"events": [
        {"date": dt.date(2026, 11, 6), "venue": "w", "kind": "Reception", "start": "16:00", "end": "18:00",
         "title": "HERE I AM", "artists": ["N"], "show_start": dt.date(2026, 10, 7), "show_end": dt.date(2026, 11, 15)},
        {"date": dt.date(2026, 11, 6), "venue": "a", "gallery": "A, project space", "kind": "Opening",
         "start": "16:00", "end": "19:00", "title": "Mini", "artists": ["D"]},
        {"date": dt.date(2026, 11, 6), "venue": "h", "kind": "Opening", "start": "17:00", "title": "Covered", "artists": ["K"]},
        {"date": dt.date(2026, 11, 7), "venue": "a", "kind": "Talk", "start": "13:00", "title": "Talk", "artists": ["D"]},
        {"date": dt.date(2026, 11, 5), "gallery": "Visiting", "kind": "Preview", "title": "X"},
    ]}
    hand = [("h", {"title": "Covered show", "artists": ["K"]})]
    out = dict(W.main_list_events(wk, venues, hand))
    assert set(out) == {"w", "a"}
    w = out["w"]
    assert w["corrects"] and w["start_date"] == "2026-10-07" and w["end_date"] == "2026-11-15"
    assert w["opening"]["date"] == "2026-11-06" and w["opening"]["label"] == "Reception"
    assert out["a"]["venue_name"] == "A, project space" and out["a"]["opening"]["label"] is None
