"""Reduce gallery HTML to compact text with absolute links, sized for a small LLM context."""
from __future__ import annotations

import json
import re
import urllib.parse

from bs4 import BeautifulSoup

DROP_TAGS = ["script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "button"]
BLOCK_TAGS = ["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "section", "article"]
NOISE = re.compile(r"cookie|consent|newsletter|subscribe|gdpr", re.I)
MAX_CHARS = 12_000  # about 3.5k tokens; keeps each call near 250 neurons on the free tier

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


SKIP_KEYS = {"component", "id", "uuid", "filename", "focus", "copyright", "fieldtype", "linktype", "cached_url",
             # media and biographies: captions and CVs name other shows that are not on here
             "images", "images_exhibition", "video", "video_teaser", "portrait", "press", "products",
             "meta_data", "info"}
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def next_data_text(html: str, keys: list[str] | None = None) -> str:
    """Text from a Next.js page's embedded __NEXT_DATA__ JSON, for sites whose listings exist
    only as data until the browser draws them. `keys` picks pageProps entries, in order."""
    tag = BeautifulSoup(html, "html.parser").find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return ""
    try:
        props = json.loads(tag.string)["props"]["pageProps"]
    except (ValueError, KeyError, TypeError):
        return ""
    out: list[str] = []
    for k in keys or list(props):
        if k in props:
            _walk(props[k], k, out)
    return "\n".join(out)[:MAX_CHARS]


def _walk(node, key: str, out: list[str]) -> None:
    if isinstance(node, dict):
        if node.get("type") == "doc":  # rich text: one line per paragraph
            for block in node.get("content") or []:
                line = "".join(t.get("text", "") for t in _leaves(block)).strip()
                if line:
                    out.append(line)
            return
        for k, v in node.items():
            if not k.startswith("_") and k not in SKIP_KEYS:
                _walk(v, k, out)
    elif isinstance(node, list):
        for x in node:
            _walk(x, key, out)
    elif isinstance(node, str):
        s = node.strip()
        if s and not s.startswith(("http://", "https://", "//")) and not UUID.match(s):
            out.append(f"{key}: {s}")


def _leaves(node) -> list[dict]:
    if isinstance(node, dict):
        if "text" in node and isinstance(node["text"], str):
            return [node]
        return [x for c in node.get("content") or [] for x in _leaves(c)]
    return []


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
