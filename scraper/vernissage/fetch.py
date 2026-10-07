"""Polite HTTP and headless fetching with robots.txt and per-host throttling."""
from __future__ import annotations

import time
import urllib.parse
import urllib.robotparser

import requests

UA = (
    "VernissageBot/0.1 "
    "(+https://www.contrarian.fund/projects/vernissage/; office@contrarian.fund)"
)


class FetchError(Exception):
    pass


class Fetcher:
    def __init__(self, delay: float = 4.0, timeout: float = 30.0):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "sv,en;q=0.8"})
        self.delay = delay
        self.timeout = timeout
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._pw = None
        self._browser = None

    # ---------- politeness ----------
    def _throttle(self, host: str) -> None:
        wait = self._last.get(host, 0) + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        p = urllib.parse.urlsplit(url)
        base = f"{p.scheme}://{p.netloc}"
        rp = self._robots.get(base)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                self._throttle(p.netloc)
                r = self.s.get(base + "/robots.txt", timeout=self.timeout)
                if r.status_code in (401, 403) or r.status_code == 429 or r.status_code >= 500:
                    rp.disallow_all = True  # unknown or hostile: stay out this run
                elif r.status_code >= 400:
                    rp.parse([])  # no robots.txt: allowed
                else:
                    rp.parse(r.text.splitlines())
            except requests.RequestException:
                rp.disallow_all = True
            self._robots[base] = rp
        return rp.can_fetch(UA, url)

    # ---------- fetching ----------
    def get(self, url: str) -> str:
        if not self.allowed(url):
            raise FetchError(f"robots.txt disallows {url}")
        host = urllib.parse.urlsplit(url).netloc
        for attempt in range(2):
            self._throttle(host)
            try:
                r = self.s.get(url, timeout=self.timeout)
            except requests.RequestException as e:
                raise FetchError(f"{url}: {e}") from e
            if r.status_code == 429 and attempt == 0:
                retry = r.headers.get("Retry-After", "30")
                time.sleep(min(int(retry) if retry.isdigit() else 30, 60))
                continue
            if r.status_code >= 400:
                raise FetchError(f"{url}: HTTP {r.status_code}")
            r.encoding = r.apparent_encoding if not r.encoding else r.encoding
            return r.text
        raise FetchError(f"{url}: rate limited")

    def render(self, url: str) -> str:
        """Render with headless Chromium for client-side sites."""
        if not self.allowed(url):
            raise FetchError(f"robots.txt disallows {url}")
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch()
        host = urllib.parse.urlsplit(url).netloc
        self._throttle(host)
        page = self._browser.new_page(user_agent=UA, locale="sv-SE")
        try:
            page.goto(url, wait_until="networkidle", timeout=45_000)
            return page.content()
        except Exception as e:  # playwright raises its own types
            raise FetchError(f"{url}: {e}") from e
        finally:
            page.close()

    def close(self) -> None:
        if self._browser:
            self._browser.close()
            self._pw.stop()
