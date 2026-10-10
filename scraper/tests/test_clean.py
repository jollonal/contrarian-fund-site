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
