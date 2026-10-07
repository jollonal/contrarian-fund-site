"""Normalize extracted exhibitions into events, then write events.json, .ics files and the page."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

MIN_CONFIDENCE = 0.6
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


def normalize(found: dict[str, list[dict]], venues: list[dict], today: dt.date, window: int):
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
            problems = []
            if start and end and start > end:
                problems.append("start after end")
            if op_date and start and abs((op_date - start).days) > 14:
                problems.append("opening far from start")
            if float(x.get("confidence") or 0) < MIN_CONFIDENCE:
                problems.append("low confidence")
            if problems:
                review.append({"venue": vid, "problems": problems, "item": x})
                continue

            day = op_date or start
            if not day or not (today <= day <= horizon):
                continue

            key = f"{vid}|{_slug(x['title'])}|{start or day}"
            ev = {
                "id": hashlib.sha1(key.encode()).hexdigest()[:12],
                "venue_id": vid,
                "venue": v["name"],
                "address": v.get("address"),
                "district": v.get("district"),
                "title": x["title"].strip(),
                "artists": [a.strip() for a in x.get("artists") or [] if isinstance(a, str) and a.strip()],
                "start_date": start.isoformat() if start else None,
                "end_date": end.isoformat() if end else None,
                "date": day.isoformat(),
                "opening": {
                    "confirmed": bool(op_date),
                    "start_time": _time(op.get("start_time")) if op_date else None,
                    "end_time": _time(op.get("end_time")) if op_date else None,
                    "text": op.get("text") if op_date else None,
                },
                "access": x.get("access") if x.get("access") in ("public", "invite") else "unknown",
                "url": x.get("detail_url") or v["homepage"],
            }
            prev = merged.get(ev["id"])
            if prev is None or (ev["opening"]["confirmed"] and not prev["opening"]["confirmed"]):
                if prev:
                    ev["artists"] = sorted(set(prev["artists"]) | set(ev["artists"]))
                merged[ev["id"]] = ev

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
        "SUMMARY:" + _esc(label + ": " + e["title"] + " at " + e["venue"]),
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

