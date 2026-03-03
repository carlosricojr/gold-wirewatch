import yaml
import httpx
import feedparser

with open('sources.yaml', 'r', encoding='utf-8') as f:
    data = yaml.safe_load(f)

for feed in data['feeds']:
    if not feed.get('enabled', True):
        continue
    name = feed['name']
    url = feed['url']
    try:
        r = httpx.get(url, timeout=8, follow_redirects=True)
        p = feedparser.parse(r.text)
        print(f"{name} | {r.status_code} | entries {len(getattr(p, 'entries', []))} | {str(r.url)[:90]}", flush=True)
    except Exception as ex:
        print(f"{name} | ERR {type(ex).__name__} {ex}", flush=True)
