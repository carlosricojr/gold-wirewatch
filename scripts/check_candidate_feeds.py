import httpx
import feedparser

candidates = [
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("Al Jazeera All", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("AP Top News", "https://feeds.apnews.com/apnews/topnews"),
    ("AP World", "https://feeds.apnews.com/apf-worldnews"),
    ("NYTimes World", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    ("CNBC World", "https://www.cnbc.com/id/100727362/device/rss/rss.html"),
    ("Investing Commodities", "https://www.investing.com/rss/news_commodities.rss"),
]

for name, url in candidates:
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True, headers={"User-Agent":"Mozilla/5.0"})
        p = feedparser.parse(r.text)
        print(f"{name} | {r.status_code} | entries {len(getattr(p,'entries',[]))} | {str(r.url)[:95]}", flush=True)
    except Exception as ex:
        print(f"{name} | ERR {type(ex).__name__} {ex}", flush=True)
