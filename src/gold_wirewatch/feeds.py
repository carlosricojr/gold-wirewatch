from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import feedparser  # type: ignore[import-untyped]
import httpx

from .config import FeedConfig, Settings
from .models import FeedItem


def stable_item_key(item: FeedItem) -> str:
    published = item.published_at.isoformat() if item.published_at else ""
    base = f"{item.source}|{item.guid}|{item.url}|{published}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        return dt.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def _fetch_text(client: httpx.Client, url: str, settings: Settings) -> str:
    delay = settings.retry_backoff_seconds
    for attempt in range(1, settings.retry_max_attempts + 1):
        try:
            response = client.get(url, timeout=settings.openclaw_timeout_seconds)
            response.raise_for_status()
            return response.text
        except (httpx.HTTPError, httpx.TimeoutException):
            if attempt >= settings.retry_max_attempts:
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def poll_feed(client: httpx.Client, feed: FeedConfig, settings: Settings) -> list[FeedItem]:
    text = _fetch_text(client, feed.url, settings)
    now = datetime.now(UTC)
    if feed.kind.lower() == "rss":
        parsed = feedparser.parse(text)
        items: list[FeedItem] = []
        for entry in parsed.entries:
            guid = str(
                entry.get("id")
                or entry.get("guid")
                or entry.get("link")
                or entry.get("title")
            )
            items.append(
                FeedItem(
                    source=feed.name,
                    title=str(entry.get("title", "")).strip(),
                    summary=str(entry.get("summary", "")).strip(),
                    url=str(entry.get("link", "")).strip(),
                    guid=guid.strip(),
                    published_at=_parse_dt(entry.get("published") or entry.get("updated")),
                    fetched_at=now,
                )
            )
        return items

    payload = json.loads(text)
    records = payload if isinstance(payload, list) else payload.get("items", [])
    out: list[FeedItem] = []
    for row in records:
        if not isinstance(row, dict):
            continue
        guid = str(row.get("id") or row.get("guid") or row.get("url") or row.get("title"))
        out.append(
            FeedItem(
                source=feed.name,
                title=str(row.get("title", "")).strip(),
                summary=str(row.get("summary") or row.get("description") or "").strip(),
                url=str(row.get("url", "")).strip(),
                guid=guid.strip(),
                published_at=_parse_dt(str(row.get("published_at") or row.get("published") or "")),
                fetched_at=now,
            )
        )
    return out
