import datetime as dt
from email.message import EmailMessage

from vernissage.build import normalize
from vernissage.mail import match_venue, message_text, venue_domains

VENUES = [
    {"id": "hedenius", "name": "Galleri Hedenius", "homepage": "https://www.gallerihedenius.com/",
     "mail_domains": ["hedenius.se"], "status": "active", "address": "Sturegatan 36", "district": "Östermalm"},
    {"id": "berg", "name": "Berg Gallery", "homepage": "https://www.berggallery.se/", "status": "active"},
]


def test_sender_domain_subdomain_and_name_matching():
    d = venue_domains(VENUES)
    assert match_venue("Yana <yana@hedenius.se>", "", VENUES, d) == "hedenius"
    assert match_venue("Galleri Hedenius <info@gallerihedenius.com>", "", VENUES, d) == "hedenius"
    assert match_venue("news@mail.berggallery.se", "", VENUES, d) == "berg"
    assert match_venue("noreply@mailchimp.com", "Welcome to Berg Gallery's opening", VENUES, d) == "berg"
    assert match_venue("noreply@mailchimp.com", "Berg Gallery and Galleri Hedenius", VENUES, d) is None


def test_message_text_drops_links_and_addresses():
    m = EmailMessage()
    m["From"] = "info@berggallery.se"
    m.set_content("Vernissage 15 oktober kl 17-19. Unsubscribe: https://track.example/x?u=me")
    m.add_alternative('<p>Vernissage 15 oktober kl 17-19</p><a href="https://track.example/x">Read more</a>'
                      '<p>Contact jollonfamily.collecting+vernissage@gmail.com</p>', subtype="html")
    t = message_text(m)
    assert "Vernissage 15 oktober" in t and "Read more" in t
    assert "track.example" not in t and "@" not in t


def test_newsletter_adds_opening_to_website_listing():
    today = dt.date(2026, 10, 8)
    web = {"title": "Valv", "artists": ["Juri Markkula"], "start_date": "2026-10-22", "end_date": "2026-11-21",
           "kind": "exhibition", "opening": {"date": None}, "confidence": 0.9,
           "detail_url": "https://www.gallerihedenius.com/utstllningar/currentexhibition"}
    mail = {"title": "Juri Markkula: VALV", "artists": ["Juri Markkula"], "start_date": "2026-10-22",
            "end_date": None, "kind": "exhibition", "confidence": 0.9, "source": "newsletter", "detail_url": None,
            "opening": {"date": "2026-10-22", "start_time": "17:00", "end_time": "19:00", "text": "Vernissage kl 17-19"}}
    events, _ = normalize({"hedenius": [web, mail]}, VENUES, today, 60, {})
    assert len(events) == 1
    e = events[0]
    assert e["title"] == "Valv" and e["url"].endswith("currentexhibition")
    assert e["opening"]["confirmed"] and e["opening"]["end_time"] == "19:00"
    assert e["end_date"] == "2026-11-21" and e["source"] == "web+newsletter"


def test_inbox_read_is_readonly_and_stores_only_facts(monkeypatch):
    import json
    from vernissage import mail as M

    m = EmailMessage()
    m["From"] = "Galleri Hedenius <info@gallerihedenius.com>"
    m["Subject"] = "Private subject"
    m["Message-ID"] = "<abc@x>"
    m["Date"] = "Thu, 08 Oct 2026 10:00:00 +0200"
    m.set_content("VERNISSAGE 22 OKTOBER KL. 17 - 19")
    raw = m.as_bytes()
    calls = {}

    class FakeIMAP:
        def __init__(self, host): pass
        def login(self, u, p): calls["login"] = (u, p)
        def select(self, box, readonly=False): calls["select"] = (box, readonly); return "OK", [b"1"]
        def search(self, *a): return "OK", [b"1"]
        def fetch(self, num, what): calls["fetch"] = what; return "OK", [(b"1", raw)]
        def logout(self): pass

    class FakeLLM:
        available = True
        def json(self, system, user, max_tokens=2500):
            assert "VERNISSAGE 22 OKTOBER" in user
            return {"exhibitions": [{"title": "Valv", "artists": ["Juri Markkula"], "start_date": "2026-10-22",
                                     "detail_url": "https://track.example/x", "kind": "exhibition",
                                     "opening": {"date": "2026-10-22", "start_time": "17:00"}, "confidence": 0.9}]}

    monkeypatch.setattr(M.imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setenv("GMAIL_ADDRESS", "a@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh")
    cache = {}
    found = M.read_newsletters(VENUES, cache, dt.date(2026, 10, 8), FakeLLM())
    assert calls["select"] == ('"vernissage"', True) and "PEEK" in calls["fetch"]
    assert calls["login"] == ("a@gmail.com", "abcdefgh")
    assert found["hedenius"][0]["detail_url"] is None
    stored = json.dumps(cache)
    assert "Private subject" not in stored and "gallerihedenius.com" not in stored and "abc@x" not in stored
    # second run: already read, no model call
    class NoLLM:
        available = True
        def json(self, *a, **k): raise AssertionError("should not be called")
    assert M.read_newsletters(VENUES, cache, dt.date(2026, 10, 8), NoLLM())["hedenius"]


def test_excluded_venues_ignored_unless_newsletter_flag(monkeypatch):
    from vernissage import mail as M
    venues = VENUES + [
        {"id": "loyal", "name": "Loyal Gallery", "homepage": "https://www.loyalgallery.com/", "status": "excluded"},
        {"id": "larsen-warner", "name": "Larsen / Warner", "homepage": "https://larsenwarner.com/",
         "status": "excluded", "newsletter": True},
    ]
    seen = {}
    monkeypatch.setattr(M, "_fetch_new", lambda a, p, v, s, t, l: seen.setdefault("ids", [x["id"] for x in v]))
    monkeypatch.setenv("GMAIL_ADDRESS", "a@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "x")
    M.read_newsletters(venues, {}, dt.date(2026, 10, 8), None)
    assert "loyal" not in seen["ids"] and "larsen-warner" in seen["ids"]


def test_unmatched_email_is_retried_after_domain_added(monkeypatch):
    from vernissage import mail as M

    m = EmailMessage()
    m["From"] = "News <hello@mail.sendservice.example>"
    m["Message-ID"] = "<u1@x>"
    m["Date"] = "Thu, 08 Oct 2026 10:00:00 +0200"
    m.set_content("Welcome to our autumn programme. Vernissage 22 oktober kl. 17-19.")
    raw = m.as_bytes()

    class FakeIMAP:
        def __init__(self, host): pass
        def login(self, u, p): pass
        def select(self, box, readonly=False): return "OK", [b"1"]
        def search(self, *a): return "OK", [b"1"]
        def fetch(self, num, what): return "OK", [(b"1", raw)]
        def logout(self): pass

    class LLM:
        available = True
        calls = 0
        def json(self, system, user, max_tokens=2500):
            LLM.calls += 1
            return {"exhibitions": [{"title": "Valv", "start_date": "2026-10-22", "kind": "exhibition",
                                     "opening": {"date": "2026-10-22", "start_time": "17:00"}, "confidence": 0.9}]}

    monkeypatch.setattr(M.imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setenv("GMAIL_ADDRESS", "a@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "x")
    cache = {}
    assert M.read_newsletters(VENUES, cache, dt.date(2026, 10, 8), LLM()) == {}
    assert LLM.calls == 0 and list(cache["mail"].values())[0]["venue"] is None
    venues = [dict(v, mail_domains=["sendservice.example"]) if v["id"] == "hedenius" else v for v in VENUES]
    found = M.read_newsletters(venues, cache, dt.date(2026, 10, 8), LLM())
    assert LLM.calls == 1 and found["hedenius"][0]["title"] == "Valv"
    assert len(cache["mail"]) == 1
