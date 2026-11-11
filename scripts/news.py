"""NBA headlines from ESPN's official RSS feed, for the "NBA news" box on the home page.

Follows ESPN's RSS terms (espn.com RSS FAQ): we show only what's in the feed, unchanged (headline,
summary, date), link each item to its espn.com URL exactly as given, credit ESPN, and put no ads in it.
Converting the XML to JSON doesn't change the content; we don't edit, shorten or rewrite anything.
Writes OUT_DIR/news.json -> {"updated", "source", "items": [{"title", "summary", "link", "date"}]}
"""
import json, os, sys, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

FEED = "https://www.espn.com/espn/rss/nba/news"
OUT = os.environ.get("OUT_DIR", "news-data")
MAX = 8


def main():
    req = urllib.request.Request(FEED, headers={"User-Agent": "Mozilla/5.0 (overlay-tips news box; RSS reader)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        root = ET.fromstring(r.read())
    items = []
    for it in root.iter("item"):
        t = lambda tag: (it.findtext(tag) or "").strip()
        if t("title") and t("link"):
            items.append({"title": t("title"), "summary": t("description"), "link": t("link"), "date": t("pubDate")})
        if len(items) >= MAX:
            break
    if not items:
        sys.exit("feed came back empty, keeping the previous news.json")
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "news.json"), "w") as f:
        json.dump({"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": FEED, "items": items}, f)
    print(f"news.json: {len(items)} headlines")


if __name__ == "__main__":
    main()
