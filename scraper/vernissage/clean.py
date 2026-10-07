"""Reduce gallery HTML to compact text with absolute links, sized for a small LLM context."""
from __future__ import annotations

import re
import urllib.parse

from bs4 import BeautifulSoup

DROP_TAGS = ["script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "button"]
BLOCK_TAGS = ["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "section", "article"]
NOISE = re.compile(r"cookie|consent|newsletter|subscribe|gdpr", re.I)
MAX_CHARS = 18_000  # about 5k tokens; GitHub Models free tier caps input at 8k

OPENING_WORDS = re.compile(
    r"vernissage|öppning|öppnar|opening|opens|reception|invigning|release", re.I
)


def html_to_text(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(DROP_TAGS):
        t.decompose()
    for t in soup("header"):  # site header only; keep <header> inside articles
        if not t.find_parent(["main", "article"]):
            t.decompose()
    for t in soup.find_all(attrs={"class": NOISE}):
        t.decompose()
    for t in soup.find_all(attrs={"id": NOISE}):
        t.decompose()

    host = urllib.parse.urlsplit(base_url).netloc
    for a in soup.find_all("a", href=True):
        href = urllib.parse.urljoin(base_url, a["href"])
        label = a.get_text(" ", strip=True)
        same_host = urllib.parse.urlsplit(href).netloc == host
        if label and same_host and not href.startswith(("mailto:", "tel:")):
            a.replace_with(f"{label} <{href}>")
        else:
            a.replace_with(label)

    for t in soup.find_all(BLOCK_TAGS):
        t.insert_before("\n")

    text = soup.get_text(" ")
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    out, seen = [], set()
    for ln in lines:
        if not ln or ln in seen and len(ln) < 80:
            continue
        seen.add(ln)
        out.append(ln)
    return "\n".join(out)[:MAX_CHARS]


def opening_snippets(text: str, radius: int = 250, limit: int = 3000) -> str:
    """Windows of text around opening/vernissage keywords. Empty if none."""
    spans: list[tuple[int, int]] = []
    for m in OPENING_WORDS.finditer(text):
        a, b = max(0, m.start() - radius), min(len(text), m.end() + radius)
        if spans and a <= spans[-1][1]:
            spans[-1] = (spans[-1][0], b)
        else:
            spans.append((a, b))
    return "\n...\n".join(text[a:b] for a, b in spans)[:limit]
