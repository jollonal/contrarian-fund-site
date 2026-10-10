import datetime as dt
import json

from vernissage import geocode as G
from vernissage.build import map_points


def test_candidates_strip_place_names_and_fall_back_to_street():
    assert G.candidates("Färgkontoret, Lövholmsgränd 12, 117 43 Stockholm") == [
        "Färgkontoret, Lövholmsgränd 12, 117 43 Stockholm",
        "Lövholmsgränd 12, 117 43 Stockholm",
        "Lövholmsgränd 12, Stockholm",
    ]
    assert G.candidates("Sjökvarnsbacken 15, 131 71 Nacka")[-1] == "Sjökvarnsbacken 15, Nacka"


def test_locate_caches_retries_and_honours_coords(monkeypatch):
    import tempfile, pathlib
    path = pathlib.Path(tempfile.mkdtemp()) / "geocode.json"
    calls = []

    class Resp:
        def __init__(self, hits): self.status_code, self._h = 200, hits
        def json(self): return self._h

    class Session:
        headers = {}
        def get(self, url, params, timeout):
            calls.append(params["q"])
            return Resp([{"lat": "59.33", "lon": "18.06"}] if params["q"].startswith("Lövholmsgränd") else [])

    monkeypatch.setattr(G.time, "sleep", lambda s: None)
    venues = [{"id": "p", "address": "Färgkontoret, Lövholmsgränd 12, 117 43 Stockholm"},
              {"id": "x", "address": "Nowhere 1, 000 00 Void"},
              {"id": "c", "address": "Anything", "coords": [59.1, 18.1]}]
    out = G.locate(venues, path, dt.date(2026, 10, 10), Session())
    assert out["p"] == (59.33, 18.06) and out["c"] == (59.1, 18.1) and "x" not in out
    n = len(calls)
    G.locate(venues, path, dt.date(2026, 10, 12), Session())        # cached hit and fresh miss: no calls
    assert len(calls) == n
    G.locate(venues, path, dt.date(2026, 10, 20), Session())        # miss older than 7 days: retried
    assert len(calls) > n
    assert json.loads(path.read_text())["Nowhere 1, 000 00 Void"]["miss"] == "2026-10-20"


def test_shared_address_is_fanned_out():
    out = G.spread({"a": (59.3, 18.0), "b": (59.3, 18.0), "c": (59.4, 18.1)})
    assert out["a"] != out["b"] and out["c"] == (59.4, 18.1)


def test_map_points_numbered_by_district_with_openings():
    venues = [{"id": "z", "name": "Zeta", "district": "Södermalm", "status": "active", "lat": 59.31, "lon": 18.07,
               "address": "Gatan 1", "homepage": "https://z.se/"},
              {"id": "a", "name": "Alfa", "district": "Östermalm", "status": "active", "lat": 59.34, "lon": 18.08},
              {"id": "n", "name": "Nope", "district": "Vasastan", "status": "excluded", "lat": 59.34, "lon": 18.05}]
    ev = {"venue_id": "z", "date": "2026-10-21", "title": "Show", "title_en": None, "gloss": None,
          "opening": {"confirmed": True, "start_time": "17:00", "end_time": "19:00"}}
    pts = map_points([ev], venues)
    assert [p["id"] for p in pts] == ["z", "a"] and [p["n"] for p in pts] == [1, 2]
    assert pts[0]["openings"][0] == {"date": "Wed 21 Oct", "title": "Show", "time": "17:00 to 19:00"}
    assert pts[0]["map_url"].startswith("https://www.google.com/maps/search/") and pts[0]["central"]
