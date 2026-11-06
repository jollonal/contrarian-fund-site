import json

from vernissage.clean import next_data_text
from vernissage.pipeline import page_text

PROPS = {
    "news": "05.11.2026 solo exhibition by Thom Trojanowski; solo by Evelina Jonsson",
    "current": [{
        "id": 1, "name": "Ghosts and Gods", "slug": "ghosts-and-gods",
        "content": {
            "component": "Exhibition",
            "date": [{"_uid": "ea7ba729-5e5a-4c6c-9e47-f17e182a54e2", "start": "2026-10-01 00:00",
                      "end": "2026-10-24 00:00", "component": "Block_Date_From_Till"}],
            "text": {"type": "doc", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Brings together "},
                                                  {"type": "text", "text": "two artists."}]},
                {"type": "paragraph"}]},
            "images": [{"filename": "https://a.storyblok.com/x.jpg", "name": ""}],
            "location": ["Coulisse Gallery"],
            "artists_relations": [{"name": "Nils Alix Tabeling", "content": {
                "info": {"type": "doc", "content": [{"type": "paragraph", "content": [
                    {"type": "text", "text": "Recent exhibitions include Blue of Noon, Berlin (2022)."}]}]},
                "video": [{"images": [{"name": "Precatory Key, 2025", "filename": "https://a.storyblok.com/y.jpg"}],
                           "component": "Block_Slideshow"}],
                "component": "Artist"}}],
        },
    }],
    "past": [{"name": "Organ"}],
}
HTML = ('<html><body><div id="__next"></div><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps({"props": {"pageProps": PROPS}}) + "</script></body></html>")


def test_next_data_reads_chosen_keys_in_order():
    text = next_data_text(HTML, ["news", "current"])
    lines = text.splitlines()
    assert lines[0] == "news: 05.11.2026 solo exhibition by Thom Trojanowski; solo by Evelina Jonsson"
    assert "name: Ghosts and Gods" in lines
    assert "start: 2026-10-01 00:00" in lines and "end: 2026-10-24 00:00" in lines
    assert "Brings together two artists." in lines
    assert "Organ" not in text
    assert "name: Nils Alix Tabeling" in lines and "location: Coulisse Gallery" in lines
    assert "Blue of Noon" not in text and "Precatory" not in text  # biographies and captions are skipped
    assert "storyblok" not in text and "ea7ba729" not in text and "Block_Date" not in text


def test_next_data_default_reads_everything():
    assert "name: Organ" in next_data_text(HTML)


def test_next_data_missing_falls_back_to_html():
    v = {"adapter": "next_data", "next_data_keys": ["news"]}
    assert page_text(v, "<html><body><p>Hello show</p></body></html>", "https://x.se/") == "Hello show"
    assert page_text(v, HTML, "https://x.se/").startswith("news: 05.11.2026")


FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>gallery weekend 2026 – jasmin daryani</title>
    <link rel="alternate" type="text/html" href="https://ed-art.se/blogs/news/gallery-weekend-2026-jasmin-daryani"/>
    <published>2026-10-27T11:00:00+01:00</published>
    <content type="html">&lt;p&gt;Gallery Weekend startar fredag den 6:e november och pågår till och med söndag den 8:de november.&lt;/p&gt;</content>
  </entry>
  <entry>
    <title>glödande landskap</title>
    <link rel="alternate" type="text/html" href="https://ed-art.se/blogs/news/glodande-landskap"/>
    <published>2026-03-24T10:00:00+01:00</published>
    <content type="html">&lt;p&gt;Old post.&lt;/p&gt;</content>
  </entry>
</feed>"""


def test_shopify_atom_feed_keeps_recent_posts_with_full_text():
    import datetime as dt
    v = {"id": "ed-art", "adapter": "shopify"}
    text = page_text(v, FEED, "https://ed-art.se/blogs/news.atom", dt.date(2026, 11, 6))
    assert text.startswith("Post: gallery weekend 2026 – jasmin daryani <https://ed-art.se/blogs/news/gallery-weekend-2026-jasmin-daryani>")
    assert "Published: 2026-10-27" in text and "8:de november" in text
    assert "glödande" not in text


def test_shopify_atom_feed_with_no_recent_posts():
    import datetime as dt
    v = {"id": "ed-art", "adapter": "shopify"}
    assert page_text(v, FEED, "https://ed-art.se/blogs/news.atom", dt.date(2027, 6, 1)) == "No posts in the last 60 days."
