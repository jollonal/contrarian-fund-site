import datetime as dt
from email.message import EmailMessage

from vernissage.build import normalize
from vernissage.mail import match_venue, message_text, venue_domains

VENUES = [
    {"id": "hedenius", "name": "Galleri Hedenius", "homepage": "https://www.gallerihedenius.com/",
     "mail_domains": ["hedenius.se"], "status": "active", "address": "Sturegatan 36", "district": "Östermalm"},
    {"id": "berg", "name": "Berg Gallery", "homepage": "https://www.berggallery.se/", "status": "active"},
]


def fake_imap(messages: dict[int, bytes], calls: dict | None = None, validity: int = 7):
    """A read-only IMAP server holding {uid: raw message}."""
    calls = calls if calls is not None else {}

    class FakeIMAP:
        def __init__(self, host): calls["host"] = host
        def login(self, u, p): calls["login"] = (u, p)
        def select(self, box, readonly=False): calls["select"] = (box, readonly); return "OK", [b"1"]
        def response(self, name): return name, [str(validity).encode()]
        def uid(self, command, *args):
            if command == "SEARCH":
                lo = int(args[1].split(":")[0])
                hits = [u for u in messages if u >= lo] or [max(messages)]  # like a real server for "N:*"
                return "OK", [" ".join(map(str, hits)).encode()]
            calls.setdefault("fetched", []).append(int(args[0]))
            calls["what"] = args[1]
            return "OK", [(b"1", messages[int(args[0])])]
        def logout(self): pass
    return FakeIMAP


def mail_env(monkeypatch, read="true"):
    monkeypatch.setenv("MAIL_ADDRESS", "a@gmx.com")
    monkeypatch.setenv("MAIL_PASSWORD", "abcd efgh")
    monkeypatch.setenv("READ_MAIL", read)
    monkeypatch.delenv("MAIL_HOST", raising=False)


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
                      '<p>Contact listings@gmx.com</p>', subtype="html")
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


def _msg(sender, mid, body, subject=None):
    m = EmailMessage()
    m["From"] = sender
    if subject:
        m["Subject"] = subject
    m["Message-ID"] = mid
    m["Date"] = "Thu, 08 Oct 2026 10:00:00 +0200"
    m.set_content(body)
    return m.as_bytes()


VALV = {"exhibitions": [{"title": "Valv", "artists": ["Juri Markkula"], "start_date": "2026-10-22",
                         "detail_url": "https://track.example/x", "kind": "exhibition",
                         "opening": {"date": "2026-10-22", "start_time": "17:00"}, "confidence": 0.9}]}


class CountingLLM:
    available = True

    def __init__(self):
        self.calls = 0

    def json(self, system, user, max_tokens=2500):
        self.calls += 1
        return VALV


def test_inbox_read_is_readonly_and_stores_only_facts(monkeypatch):
    import json
    from vernissage import mail as M

    raw = _msg("Galleri Hedenius <info@gallerihedenius.com>", "<abc@x>",
               "VERNISSAGE 22 OKTOBER KL. 17 - 19", subject="Private subject")
    calls = {}
    monkeypatch.setattr(M.imaplib, "IMAP4_SSL", fake_imap({1: raw}, calls))
    mail_env(monkeypatch)
    cache = {}
    llm = CountingLLM()
    found = M.read_newsletters(VENUES, cache, dt.date(2026, 10, 8), llm)
    assert calls["host"] == "imap.gmx.com" and calls["select"] == ('"INBOX"', True) and "PEEK" in calls["what"]
    assert calls["login"] == ("a@gmx.com", "abcdefgh")
    assert found["hedenius"][0]["detail_url"] is None and llm.calls == 1
    stored = json.dumps(cache)
    assert "Private subject" not in stored and "gallerihedenius.com" not in stored and "abc@x" not in stored
    assert "a@gmx.com" not in stored
    # second run: nothing new on the server, so nothing is fetched and the model is not called
    calls.clear()
    assert M.read_newsletters(VENUES, cache, dt.date(2026, 10, 8), llm)["hedenius"]
    assert llm.calls == 1 and "fetched" not in calls


def test_only_new_messages_fetched_and_renumbering_resets(monkeypatch):
    from vernissage import mail as M

    msgs = {1: _msg("info@gallerihedenius.com", "<1@x>", "Vernissage 22 oktober kl 17-19")}
    calls = {}
    monkeypatch.setattr(M.imaplib, "IMAP4_SSL", fake_imap(msgs, calls))
    mail_env(monkeypatch)
    cache = {}
    M.read_newsletters(VENUES, cache, dt.date(2026, 10, 8), CountingLLM())
    assert cache["mail_state"] == {"uidvalidity": 7, "last_uid": 1}
    msgs[2] = _msg("news@mail.berggallery.se", "<2@x>", "Vernissage 23 oktober kl 17-19")
    calls.clear()
    found = M.read_newsletters(VENUES, cache, dt.date(2026, 10, 8), CountingLLM())
    assert calls["fetched"] == [2] and cache["mail_state"]["last_uid"] == 2 and "berg" in found
    # the server renumbers the folder (new UIDVALIDITY): read from the start again, no duplicates
    calls.clear()
    monkeypatch.setattr(M.imaplib, "IMAP4_SSL", fake_imap(msgs, calls, validity=8))
    llm = CountingLLM()
    M.read_newsletters(VENUES, cache, dt.date(2026, 10, 8), llm)
    assert calls["fetched"] == [1, 2] and llm.calls == 0 and len(cache["mail"]) == 2


def test_stop_early_leaves_unread_messages_for_next_run(monkeypatch):
    from vernissage import mail as M

    msgs = {1: _msg("info@gallerihedenius.com", "<1@x>", "Vernissage 22 oktober"),
            2: _msg("news@mail.berggallery.se", "<2@x>", "Vernissage 23 oktober")}
    monkeypatch.setattr(M.imaplib, "IMAP4_SSL", fake_imap(msgs))
    mail_env(monkeypatch)

    class OneCall(CountingLLM):
        @property
        def available(self):
            return self.calls < 1

    cache = {}
    M.read_newsletters(VENUES, cache, dt.date(2026, 10, 8), OneCall())
    assert cache["mail_state"]["last_uid"] == 1
    found = M.read_newsletters(VENUES, cache, dt.date(2026, 10, 8), CountingLLM())
    assert "berg" in found and cache["mail_state"]["last_uid"] == 2


def test_manual_runs_do_not_log_in(monkeypatch):
    from vernissage import mail as M

    calls = {}
    monkeypatch.setattr(M.imaplib, "IMAP4_SSL", fake_imap({1: b""}, calls))
    mail_env(monkeypatch, read="false")
    remembered = {"mail": {"k": {"date": "2026-10-08", "venue": "berg", "result": [{"title": "Kept"}]}}}
    found = M.read_newsletters(VENUES, remembered, dt.date(2026, 10, 8), None)
    assert "host" not in calls and found == {"berg": [{"title": "Kept"}]}


def test_excluded_venues_ignored_unless_newsletter_flag(monkeypatch):
    from vernissage import mail as M
    venues = VENUES + [
        {"id": "loyal", "name": "Loyal Gallery", "homepage": "https://www.loyalgallery.com/", "status": "excluded"},
        {"id": "larsen-warner", "name": "Larsen / Warner", "homepage": "https://larsenwarner.com/",
         "status": "excluded", "newsletter": True},
    ]
    seen = {}
    monkeypatch.setattr(M, "_fetch_new", lambda h, a, p, v, c, t, l: seen.setdefault("ids", [x["id"] for x in v]))
    mail_env(monkeypatch)
    M.read_newsletters(venues, {}, dt.date(2026, 10, 8), None)
    assert "loyal" not in seen["ids"] and "larsen-warner" in seen["ids"]


def test_unmatched_email_is_retried_after_domain_added(monkeypatch):
    from vernissage import mail as M

    raw = _msg("News <hello@mail.sendservice.example>", "<u1@x>",
               "Welcome to our autumn programme. Vernissage 22 oktober kl. 17-19.")
    calls = {}
    monkeypatch.setattr(M.imaplib, "IMAP4_SSL", fake_imap({1: raw}, calls))
    mail_env(monkeypatch)
    cache = {}
    llm = CountingLLM()
    assert M.read_newsletters(VENUES, cache, dt.date(2026, 10, 8), llm) == {}
    assert llm.calls == 0 and list(cache["mail"].values())[0]["venue"] is None
    venues = [dict(v, mail_domains=["sendservice.example"]) if v["id"] == "hedenius" else v for v in VENUES]
    calls.clear()
    found = M.read_newsletters(venues, cache, dt.date(2026, 10, 8), llm)
    assert calls["fetched"] == [1]  # an old message is fetched again only because it was unmatched
    assert llm.calls == 1 and found["hedenius"][0]["title"] == "Valv"
    assert len(cache["mail"]) == 1


def test_own_messages_skipped_and_removed(monkeypatch):
    from vernissage import mail as M

    raw = _msg("Alfred <a+x@gmx.com>", "<own@x>", "Hej Galleri Hedenius, please add me to your newsletter.")
    monkeypatch.setattr(M.imaplib, "IMAP4_SSL", fake_imap({1: raw}))
    mail_env(monkeypatch)

    class NoLLM:
        available = True
        def json(self, *a, **k): raise AssertionError("own messages must not reach the model")

    key = M._key("<own@x>")  # same key the reader builds
    cache = {"mail": {key: {"date": "2026-10-08", "venue": None, "result": []}}}
    assert M.read_newsletters(VENUES, cache, dt.date(2026, 10, 8), NoLLM()) == {}
    assert cache["mail"] == {}


def test_refused_login_logged_without_details(monkeypatch):
    import logging
    from vernissage import mail as M

    class Refusing:
        def __init__(self, host): pass
        def login(self, u, p): raise M.imaplib.IMAP4.error("[AUTHENTICATIONFAILED] a@gmx.com authentication failed")
        def logout(self): pass

    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logging.getLogger("vernissage").addHandler(handler)
    try:
        monkeypatch.setattr(M.imaplib, "IMAP4_SSL", Refusing)
        mail_env(monkeypatch)
        M.read_newsletters(VENUES, {}, dt.date(2026, 10, 8), None)
    finally:
        logging.getLogger("vernissage").removeHandler(handler)
    msg = " ".join(r.getMessage() for r in records)
    assert "login refused" in msg and "POP3/IMAP" in msg and "a@gmx.com" not in msg


def test_social_homepages_do_not_claim_sender_domains():
    from vernissage.mail import venue_domains
    d = venue_domains([{"id": "ps", "homepage": "https://www.instagram.com/public_service_gallery/",
                        "mail_domains": ["public-service.com"], "status": "listed"}])
    assert "instagram.com" not in d and d["public-service.com"] == "ps"
