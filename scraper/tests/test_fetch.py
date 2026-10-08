import requests

from vernissage.fetch import Fetcher, FetchError


class Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


class Session:
    def __init__(self, robots):
        self.robots = robots
        self.headers = {}

    def get(self, url, timeout=None):
        if url.endswith("/robots.txt"):
            if isinstance(self.robots, Exception):
                raise self.robots
            return self.robots
        return Resp(200, "<p>ok</p>")


def fetcher(robots):
    f = Fetcher(delay=0)
    f.s = Session(robots)
    return f


def test_403_robots_is_allowed():
    assert fetcher(Resp(403)).allowed("https://g.se/x")


def test_404_robots_is_allowed():
    assert fetcher(Resp(404)).allowed("https://g.se/x")


def test_rules_are_obeyed():
    f = fetcher(Resp(200, "User-agent: *\nDisallow: /private/"))
    assert f.allowed("https://g.se/show")
    assert not f.allowed("https://g.se/private/a")


def test_unreachable_stays_out_and_says_why():
    for robots, why in [(Resp(503), "HTTP 503"), (Resp(429), "HTTP 429"),
                        (requests.ConnectionError(), "ConnectionError")]:
        f = fetcher(robots)
        try:
            f.get("https://g.se/x")
            raise AssertionError("should have refused")
        except FetchError as e:
            assert "unreachable" in str(e) and why in str(e)


def test_real_disallow_is_reported_as_such():
    f = fetcher(Resp(200, "User-agent: *\nDisallow: /"))
    try:
        f.get("https://g.se/x")
        raise AssertionError("should have refused")
    except FetchError as e:
        assert str(e).startswith("robots.txt disallows")
