"""Gallery Weekend page: a hand-kept program (scraper/weekend.yaml) rendered as its own page,
day by day, with talks, tours and performances that the main list leaves out.

The main page links to it from `announce_days` before the weekend until its last day.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .build import district_rank, map_url


def load(path: Path) -> dict | None:
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if data.get("start") and data.get("end") else None


def is_announced(wk: dict | None, today: dt.date) -> bool:
    """True while the main page should link to the weekend page."""
    if not wk:
        return False
    start, end = _d(wk["start"]), _d(wk["end"])
    return start - dt.timedelta(days=int(wk.get("announce_days", 14))) <= today <= end


def _d(x) -> dt.date:
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x))


def _place(item: dict, by_id: dict) -> dict:
    """Gallery name, link, address and district: from venues.yaml when the item names a
    venue id, with any address or district given in the item taking precedence."""
    v = by_id.get(item.get("venue") or "", {})
    name = item.get("gallery") or v.get("name") or ""
    address = item.get("address") or v.get("address")
    return {
        "gallery": name,
        "site": item.get("site") or v.get("homepage"),
        "address": address,
        "district": item.get("district") or v.get("district"),
        "map_url": map_url(name, address) if address else None,
        "floor": item.get("floor") or (v.get("floor") if not item.get("address") else None),
    }


OPENING_KINDS = {"Opening", "Reception", "Preview"}


def main_list_events(wk: dict | None, venues: list[dict], hand: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    """Openings, receptions and previews from the Gallery Weekend program, shaped like verified
    hand entries (corrects: true) so they join the main list and win over a scraped copy's
    opening. Skipped: galleries not in venues.yaml, and shows already given a hand entry in
    overrides.yaml (that entry is more specific). A reception for a show that opened earlier
    needs `show_start` (and ideally `show_end`) so it merges with the site's copy."""
    if not wk:
        return []
    ids = {v["id"] for v in venues}
    low = lambda xs: {str(x).lower() for x in xs or []}
    slug = lambda t: "".join(c for c in str(t or "").lower() if c.isalnum())

    def covered(vid, item):
        for hv, h in hand:
            if hv != vid:
                continue
            if low(h["artists"]) & low(item.get("artists")):
                return True
            a, b = slug(h["title"]), slug(item.get("title"))
            if a and b and (a in b or b in a):
                return True
        return False

    out = []
    for item in wk.get("events", []):
        vid = item.get("venue")
        if item.get("kind") not in OPENING_KINDS or vid not in ids or not item.get("title") or covered(vid, item):
            continue
        day = _d(item["date"])
        kind = item["kind"]
        out.append((vid, {
            "source": "manual",
            "corrects": True,
            "title": str(item["title"]),
            "title_en": None,
            "venue_name": item.get("gallery"),
            "artists": [str(x) for x in item.get("artists") or []],
            "start_date": str(item.get("show_start") or day),
            "end_date": str(item["show_end"]) if item.get("show_end") else None,
            "kind": "exhibition",
            "detail_url": item.get("url"),
            "opening": {"date": day.isoformat(), "start_time": item.get("start"), "end_time": item.get("end"),
                        "text": f"{kind} (Gallery Weekend program)",
                        "label": "Reception" if kind == "Reception" else None},
            "access": "unknown",
            "confidence": 1,
        }))
    return out


def _time(item: dict) -> str:
    s, e = item.get("start"), item.get("end")
    return f"{s}–{e}" if s and e else (s or "")


def render(wk: dict, venues: list[dict], today: dt.date) -> str:
    by_id = {v["id"]: v for v in venues}
    days: dict[dt.date, list[dict]] = {}
    for item in wk.get("events", []):
        day = _d(item["date"])
        days.setdefault(day, []).append({**item, **_place(item, by_id), "time": _time(item)})
    day_list = []
    for day in sorted(days):
        items = sorted(days[day], key=lambda x: (x.get("start") or "99", district_rank(x["district"]), x["gallery"]))
        day_list.append({"date": day, "past": day < today, "items": items,
                         "hours": (wk.get("hours") or {}).get(day.isoformat()) or (wk.get("hours") or {}).get(day)})
    on_view = [{**item, **_place(item, by_id)} for item in wk.get("on_view", [])]
    on_view.sort(key=lambda x: (district_rank(x["district"]), x["gallery"]))
    groups: dict[str, list[dict]] = {}
    for x in on_view:
        groups.setdefault(x["district"] or "Other", []).append(x)

    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                      autoescape=select_autoescape(["html", "j2"]))
    start, end = _d(wk["start"]), _d(wk["end"])
    return env.get_template("weekend.html.j2").render(
        wk=wk, start=start, end=end, days=day_list, on_view=list(groups.items()),
        checked=wk.get("checked"), today=today)


def write(wk: dict | None, venues: list[dict], out_dir: Path, today: dt.date) -> None:
    if not wk:
        return
    page = out_dir / wk.get("slug", "gallery-weekend")
    page.mkdir(parents=True, exist_ok=True)
    (page / "index.html").write_text(render(wk, venues, today), encoding="utf-8")
