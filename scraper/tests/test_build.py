import datetime as dt

from vernissage.build import _fold, ics, normalize
from vernissage.clean import html_to_text, opening_snippets

TODAY = dt.date(2026, 10, 7)
VENUES = [
    {"id": "a", "name": "Galleri A", "homepage": "https://a.se/", "address": "Gatan 1", "district": "Östermalm", "status": "active"},
    {"id": "b", "name": "Galleri B", "homepage": "https://b.se/", "district": "Vasastan", "status": "active", "city_filter": "Stockholm"},
]


def ex(**kw):
    base = {"title": "Show", "artists": ["X"], "start_date": "2026-10-15", "end_date": "2026-11-15",
            "kind": "exhibition", "opening": {"date": None}, "confidence": 0.9}
    base.update(kw)
    return base


def test_window_kind_city_and_review():
    found = {
        "a": [ex(), ex(title="Past", start_date="2026-09-01"), ex(title="Far", start_date="2027-02-01", end_date="2027-03-01"),
              ex(title="Fair", kind="fair"), ex(title="Unsure", confidence=0.3),
              ex(title="Backwards", start_date="2026-12-01", end_date="2026-11-01")],
        "b": [ex(title="Paris show", city="Paris"), ex(title="Sthlm show", city="Stockholm")],
    }
    events, review = normalize(found, VENUES, TODAY, 60)
    assert [e["title"] for e in events] == ["Show", "Sthlm show"]
    assert {r["item"]["title"] for r in review} == {"Unsure", "Backwards"}


def test_dedupe_prefers_confirmed_opening():
    plain = ex()
    confirmed = ex(opening={"date": "2026-10-15", "start_time": "17:00", "end_time": "19:00", "text": "Vernissage"})
    events, _ = normalize({"a": [plain, confirmed]}, VENUES, TODAY, 60)
    assert len(events) == 1
    assert events[0]["opening"] == {"confirmed": True, "start_time": "17:00", "end_time": "19:00", "text": "Vernissage"}


def test_bad_time_dropped_and_opening_date_used():
    e = ex(start_date="2026-10-16", opening={"date": "2026-10-15", "start_time": "5pm"})
    events, _ = normalize({"a": [e]}, VENUES, TODAY, 60)
    assert events[0]["date"] == "2026-10-15" and events[0]["opening"]["start_time"] is None


def test_ics_valid_lines():
    events, _ = normalize({"a": [ex(title="Ö" * 60, opening={"date": "2026-10-15", "start_time": "17:00"})]}, VENUES, TODAY, 60)
    out = ics(events, "t")
    assert out.startswith("BEGIN:VCALENDAR") and "DTEND;TZID=Europe/Stockholm:20261015T190000" in out
    assert all(len(line.encode()) <= 75 for line in out.split("\r\n"))
    assert _fold("x" * 200).count("\r\n ") == 2


def test_clean_keeps_links_and_drops_chrome():
    html = """<html><header><a href="/">Home</a></header><nav>Menu</nav><main>
      <div class="cookie-banner">Accept cookies</div>
      <li><a href="/exhibitions/12-show/"><span>Show</span></a><span>15 Oct - 15 Nov 2026</span></li>
      <p>Vernissage torsdag 15 oktober, kl. 17-19</p></main><footer>Address</footer></html>"""
    t = html_to_text(html, "https://a.se/exhibitions/")
    assert "Show <https://a.se/exhibitions/12-show/>" in t
    assert "Menu" not in t and "cookies" not in t and "Address" not in t
    assert "Vernissage torsdag" in opening_snippets(t)
    assert opening_snippets("nothing here") == ""


def test_end_time_recovered_from_opening_text():
    from vernissage.build import end_time_from_text as f
    assert f("Vernissage i konstnärens närvaro, torsdagen den 8 oktober kl. 17-19", "17:00") == "19:00"
    assert f("Vernissage: 16–19 (konstnärerna närvarar)", "16:00") == "19:00"
    assert f("Reception Thursday October 15, 5–7 pm", "17:00") == "19:00"
    assert f("VERNISSAGE 22 OKTOBER KL. 17 - 19", "17:00") == "19:00"
    assert f("Vernissage 17.30-20.00", "17:30") == "20:00"
    assert f("2026.10.10-2026.11.08 Opening Saturday 10 October 12-16", "12:00") == "16:00"
    assert f("Utställning 10-16 oktober", "17:00") is None
    assert f(None, "17:00") is None


def test_end_time_filled_in_events_and_past_items_not_reviewed():
    item = ex(opening={"date": "2026-10-15", "start_time": "17:00", "end_time": None, "text": "kl. 17-19"})
    past_unsure = ex(title="Old", start_date="2026-09-10", end_date="2026-10-10", confidence=0)
    events, review = normalize({"a": [item, past_unsure]}, VENUES, TODAY, 60)
    assert events[0]["opening"]["end_time"] == "19:00"
    assert review == []


def test_restore_spacing_uses_page_spelling():
    from vernissage.pipeline import restore_spacing
    page = "Upcoming\nJockum Nordström | Hålet i väggen\n28 November 2026 - 23 January 2027"
    assert restore_spacing("Hålet iväggen", page) == "Hålet i väggen"
    assert restore_spacing("Not on page", page) == "Not on page"
    assert restore_spacing(None, page) is None


def test_english_title_rules():
    from vernissage.build import english_title as f
    # gallery's own English title wins, no brackets
    assert f("Nya målningar", "New Paintings", "New paintings", {}) == ("New Paintings", None, None)
    # already inside the title: show nothing extra
    assert f("Nya målningar / New Paintings", "New Paintings", None, {}) == (None, None, None)
    # machine gloss
    assert f("Hålet i väggen", None, "The Hole in the Wall", {}) == (None, "The Hole in the Wall", "machine")
    # gloss identical to title (English title or a name): nothing
    assert f("Ayan Farah", None, "Ayan Farah", {}) == (None, None, None)
    # override corrects, empty override suppresses
    ov = {"Hålet i väggen": "A Hole in the Wall", "Valv": ""}
    assert f("Hålet i väggen", None, "The Hole", ov) == (None, "A Hole in the Wall", "override")
    assert f("Valv", None, "Vault", ov) == (None, None, None)


def test_gloss_flows_to_events_report_and_calendar():
    from vernissage.build import display_title, gloss_report
    x = ex(title="Hålet i väggen", title_gloss="The Hole in the Wall")
    events, _ = normalize({"a": [x]}, VENUES, TODAY, 60, {})
    e = events[0]
    assert display_title(e) == "Hålet i väggen [The Hole in the Wall]"
    assert "| Hålet i väggen | The Hole in the Wall | machine |" in gloss_report(events)
    assert "Hålet i väggen [The Hole in the Wall]" in ics(events, "t").replace("\r\n ", "")
