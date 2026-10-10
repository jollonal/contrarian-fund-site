"""Normalize extracted exhibitions into events, then write events.json, .ics files and the page."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import unicodedata
import urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

MIN_CONFIDENCE = 0.6
OPENING_TEXT = re.compile(r"(vernissage|opening|öppning|invigning|reception).*\d", re.I | re.S)
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _date(s) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def _time(s) -> str | None:
    return s if isinstance(s, str) and TIME_RE.match(s) else None


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


RANGE_RE = re.compile(r"(?<![\d.])(\d{1,2})(?:[:.](\d{2}))?\s*(?:-|\u2013|\u2014|to|till)\s*(\d{1,2})(?:[:.](\d{2}))?(?![\d.])")


def end_time_from_text(text: str | None, start: str) -> str | None:
    """Recover a missing end time from the quoted opening sentence, e.g. 'kl. 17-19' or '5-7 pm'.

    Only a range whose first hour matches the known start hour is trusted, so date
    ranges such as '10-16 oktober' are ignored."""
    sh, sm = int(start[:2]), int(start[3:])
    for m in RANGE_RE.finditer(text or ""):
        h1, m1, h2, m2 = int(m[1]), int(m[2] or 0), int(m[3]), int(m[4] or 0)
        shift = 0 if (h1, m1) == (sh, sm) else 12 if (h1 + 12, m1) == (sh, sm) else None
        if shift is None:
            continue
        h2 += shift if h2 + shift <= 23 else 0
        if h2 > 23 or m2 > 59 or (h2, m2) <= (sh, sm):
            continue
        return f"{h2:02d}:{m2:02d}"
    return None


# Chicago Manual of Style title case: lowercase articles, coordinating conjunctions
# and prepositions unless first or last; capitalize everything else.
MINOR_WORDS = set("""a an the and but for nor or so yet as at by down for from in into like near of off on
onto out over past per since than till to toward towards under until up upon via with within without
about above across after against along among around before behind below beneath beside between beyond
despite during except inside outside through throughout underneath""".split())


def title_case(text: str) -> str:
    words = text.split()
    out = []
    for i, w in enumerate(words):
        core = re.sub(r"^[^\w]+|[^\w]+$", "", w)
        first = i == 0 or re.search(r"[:\u2013\u2014-]$", words[i - 1] or "")
        last = i == len(words) - 1
        if not core or core != core.lower():
            out.append(w)  # keep names, acronyms and existing capitals as given
        elif core in MINOR_WORDS and not first and not last:
            out.append(w)
        else:
            out.append(w.replace(core, core[:1].upper() + core[1:], 1))
    return " ".join(out)


def clean_gloss(gloss: str, title: str) -> str | None:
    """Strip model noise from a machine gloss: stray quotes and commas, notes in
    parentheses, alternatives after a slash. Drop it if it reads like commentary."""
    g = re.sub(r"\s*\(.*$", "", gloss, flags=re.S)        # "(VALV is Swedish for ...)" and after
    if "/" in g and "/" not in title:
        g = g.split("/")[0]                                 # "The Gate/Valve" -> "The Gate"
    g = g.strip().strip("\"'\u201c\u201d\u201e\u2018\u2019,;: ").strip()
    if g.endswith(".") and not g.endswith("..."):
        g = g[:-1].rstrip()
    if not g or len(g.split()) > max(8, 2 * len(title.split()) + 3):
        return None
    return title_case(g)


def map_url(venue: str, address: str | None) -> str | None:
    if not address:
        return None
    return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(f"{venue}, {address}")


def english_title(title: str, title_en, gloss, overrides: dict | None = None):
    """Return (title_en, gloss, gloss_source) for display.

    A gallery's own English title wins and is shown without brackets. Otherwise the
    machine gloss is shown in brackets, unless overrides.yaml corrects or blanks it."""
    norm = lambda t: re.sub(r"\s+", " ", t or "").strip().lower()
    en = title_en.strip() if isinstance(title_en, str) and title_en.strip() else None
    if en and norm(en) in norm(title):
        en = None  # already part of the title, e.g. "Nya målningar / New Paintings"
    if en:
        return en, None, None
    overrides = overrides or {}
    if title in overrides:
        fixed = (overrides[title] or "").strip()
        return None, (fixed or None), ("override" if fixed else None)
    g = clean_gloss(gloss, title) if isinstance(gloss, str) and gloss.strip() else None
    if not g or norm(g) == norm(title):
        return None, None, None
    return None, g, "machine"


def same_show(a: dict, b: dict) -> bool:
    """The same exhibition seen twice (website and newsletter, or two pages of one site):
    same gallery, same start (or opening) date, and a shared artist or overlapping title."""
    if a["venue_id"] != b["venue_id"]:
        return False
    if (a["start_date"] or a["date"]) != (b["start_date"] or b["date"]):
        return False
    arts = lambda e: {x.lower() for x in e["artists"]}
    if arts(a) & arts(b):
        return True
    ta, tb = _slug(a["title"]), _slug(b["title"])
    return bool(ta and tb) and (ta in tb or tb in ta)


def merge_events(a: dict, b: dict) -> dict:
    """Keep the website's title and link; take a confirmed opening from whichever has one."""
    web, other = (a, b) if a.get("source") != "newsletter" or b.get("source") == "newsletter" else (b, a)
    out = dict(web)
    out["artists"] = sorted(set(a["artists"]) | set(b["artists"]))
    if not web["opening"]["confirmed"] and other["opening"]["confirmed"]:
        out["opening"] = other["opening"]
        out["date"] = other["date"]
    if out["access"] == "unknown":
        out["access"] = other["access"]
    out["end_date"] = out["end_date"] or other["end_date"]
    if a.get("source") != b.get("source"):
        out["source"] = "web+newsletter"
    return out


def normalize(found: dict[str, list[dict]], venues: list[dict], today: dt.date, window: int,
              overrides: dict | None = None):
    """Return (events, review). Events are deduped, in-window, sorted."""
    by_id = {v["id"]: v for v in venues}
    horizon = today + dt.timedelta(days=window)
    merged: dict[str, dict] = {}
    review: list[dict] = []

    for vid, items in found.items():
        v = by_id[vid]
        for x in items:
            if x.get("kind", "exhibition") != "exhibition" or not x.get("title"):
                continue
            city_filter = v.get("city_filter")
            if city_filter and x.get("city") and city_filter.lower() not in x["city"].lower():
                continue

            start, end = _date(x.get("start_date")), _date(x.get("end_date"))
            op = x.get("opening") or {}
            op_date = _date(op.get("date"))
            day = op_date or start
            if not day or not (today <= day <= horizon):
                continue  # outside the window: never shown, so never worth reviewing

            problems = []
            if start and end and start > end:
                problems.append("start after end")
            if op_date and start and abs((op_date - start).days) > 14:
                problems.append("opening far from start")
            explicit = bool(op_date and _time(op.get("start_time")) and OPENING_TEXT.search(op.get("text") or ""))
            if float(x.get("confidence") or 0) < MIN_CONFIDENCE and not explicit:
                problems.append("low confidence")  # a stated vernissage date and time is enough
            if problems:
                review.append({"venue": vid, "problems": problems, "item": x})
                continue

            st = _time(op.get("start_time")) if op_date else None
            et = _time(op.get("end_time")) if op_date else None
            if st and not et:
                et = end_time_from_text(op.get("text"), st)

            title = x["title"].strip()
            title_en, gloss, gloss_source = english_title(title, x.get("title_en"), x.get("title_gloss"), overrides)
            key = f"{vid}|{_slug(x['title'])}|{start or day}"
            ev = {
                "id": hashlib.sha1(key.encode()).hexdigest()[:12],
                "venue_id": vid,
                "venue": v["name"],
                "address": v.get("address"),
                "map_url": map_url(v["name"], v.get("address")),
                "district": v.get("district"),
                "title": title,
                "title_en": title_en,
                "gloss": gloss,
                "gloss_source": gloss_source,
                "artists": [a.strip() for a in x.get("artists") or [] if isinstance(a, str) and a.strip()],
                "start_date": start.isoformat() if start else None,
                "end_date": end.isoformat() if end else None,
                "date": day.isoformat(),
                "opening": {
                    "confirmed": bool(op_date),
                    "start_time": st,
                    "end_time": et,
                    "text": op.get("text") if op_date else None,
                },
                "access": x.get("access") if x.get("access") in ("public", "invite") else "unknown",
                "url": x.get("detail_url") or v["homepage"],
                "source": "newsletter" if x.get("source") == "newsletter" else "web",
            }
            prev_id = ev["id"] if ev["id"] in merged else next(
                (k for k, e in merged.items() if same_show(e, ev)), None)
            if prev_id is None:
                merged[ev["id"]] = ev
            else:
                merged[prev_id] = merge_events(merged[prev_id], ev)

    events = sorted(merged.values(), key=lambda e: (e["date"], e["opening"]["start_time"] or "99", e["venue"]))
    return events, review


# ---------- iCalendar ----------
VTIMEZONE = """BEGIN:VTIMEZONE
TZID:Europe/Stockholm
BEGIN:DAYLIGHT
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CEST
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE"""


def _esc(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    out, b = [], line.encode()
    while len(b) > 75:
        cut = 75
        while (b[cut] & 0xC0) == 0x80:  # do not split a UTF-8 character
            cut -= 1
        out.append(b[:cut].decode())
        b = b" " + b[cut:]
    out.append(b.decode())
    return "\r\n".join(out)


def display_title(e: dict) -> str:
    t = e["title"]
    if e.get("title_en"):
        t += " / " + e["title_en"]
    if e.get("gloss"):
        t += " [" + e["gloss"] + "]"
    return t


def gloss_report(events: list[dict]) -> str:
    """Markdown table of every English title on the page, for review on GitHub."""
    rows = ["# English titles on the live page", "",
            "Correct a machine gloss in `scraper/overrides.yaml`.", "",
            "| Date | Gallery | Title | English | Source |", "|---|---|---|---|---|"]
    for e in events:
        en = e.get("title_en") or e.get("gloss")
        if not en:
            continue
        src = "gallery" if e.get("title_en") else e.get("gloss_source")
        cell = lambda v: str(v).replace("|", "\\|")
        rows.append(f"| {e['date']} | {cell(e['venue'])} | {cell(e['title'])} | {cell(en)} | {src} |")
    if len(rows) == 6:
        rows.append("| | | | | |")
    return "\n".join(rows) + "\n"


def _vevent(e: dict, stamp: str) -> list[str]:
    d = e["date"].replace("-", "")
    op = e["opening"]
    if op["start_time"]:
        st = op["start_time"].replace(":", "")
        et = (op["end_time"] or f"{min(int(op['start_time'][:2]) + 2, 23):02d}:{op['start_time'][3:]}").replace(":", "")
        when = [f"DTSTART;TZID=Europe/Stockholm:{d}T{st}00", f"DTEND;TZID=Europe/Stockholm:{d}T{et}00"]
    else:
        nxt = (dt.date.fromisoformat(e["date"]) + dt.timedelta(days=1)).isoformat().replace("-", "")
        when = [f"DTSTART;VALUE=DATE:{d}", f"DTEND;VALUE=DATE:{nxt}"]
    label = "Vernissage" if op["confirmed"] else "Opens"
    desc = ", ".join(e["artists"])
    if e["end_date"]:
        desc += f"\nOn view until {e['end_date']}."
    return [
        "BEGIN:VEVENT",
        f"UID:{e['id']}@contrarian.fund",
        f"DTSTAMP:{stamp}",
        *when,
        "SUMMARY:" + _esc(label + ": " + display_title(e) + " at " + e["venue"]),
        f"LOCATION:{_esc(e['address'] or e['venue'])}",
        f"DESCRIPTION:{_esc(desc.strip())}",
        f"URL:{e['url']}",
        "END:VEVENT",
    ]


def ics(events: list[dict], name: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//contrarian.fund//vernissage//EN",
             "CALSCALE:GREGORIAN", f"X-WR-CALNAME:{_esc(name)}", *VTIMEZONE.splitlines()]
    for e in events:
        lines += _vevent(e, stamp)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(ln) for ln in lines) + "\r\n"


# ---------- outputs ----------
def write_outputs(events: list[dict], venues: list[dict], out_dir: Path, today: dt.date,
                  window: int, repo_url: str | None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    generated = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    active = [v for v in venues if v.get("status") == "active"]

    (out_dir / "events.json").write_text(json.dumps({
        "generated_at": generated, "window_days": window,
        "venues_tracked": len(active), "events": events,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    (out_dir / "events.ics").write_text(ics(events, "Stockholm Vernissages"), encoding="utf-8")
    ics_dir = out_dir / "ics"
    ics_dir.mkdir(exist_ok=True)
    for old in ics_dir.glob("*.ics"):
        old.unlink()
    for e in events:
        (ics_dir / f"{e['id']}.ics").write_text(ics([e], e["title"]), encoding="utf-8")

    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                      autoescape=select_autoescape(["html", "j2"]))
    env.filters["longdate"] = lambda s: (lambda d: f"{d.day} {d.strftime('%B')}")(dt.date.fromisoformat(s))
    days: dict[str, list[dict]] = {}
    for e in events:
        days.setdefault(e["date"], []).append(e)
    districts = sorted({e["district"] for e in events if e["district"]})
    stockholm_now = dt.datetime.now(ZoneInfo("Europe/Stockholm"))
    html = env.get_template("page.html.j2").render(
        days=[(dt.date.fromisoformat(d), es) for d, es in days.items()],
        today=today, window=window, districts=districts,
        n_events=len(events), n_confirmed=sum(e["opening"]["confirmed"] for e in events),
        n_venues=len(active), updated=stockholm_now.strftime("%-d %B %Y, %H:%M"),
        repo_url=repo_url,
    )
    (out_dir / "index.html").write_text(html, encoding="utf-8")

