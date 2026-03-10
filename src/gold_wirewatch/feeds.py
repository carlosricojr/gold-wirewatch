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
            try:
                response = client.get(
                    url,
                    timeout=settings.openclaw_timeout_seconds,
                    headers={"User-Agent": settings.feed_user_agent},
                )
            except TypeError:
                # Test doubles may not accept headers kwarg.
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
            pub_raw = entry.get("published")
            upd_raw = entry.get("updated")
            # Only use updated as published fallback if no dedicated published field
            published_at = _parse_dt(pub_raw) if pub_raw else None
            updated_at = _parse_dt(upd_raw) if upd_raw else None
            # feedparser auto-maps published↔updated; collapse duplicates
            if published_at is not None and updated_at is not None and published_at == updated_at:
                updated_at = None
            # Legacy fallback: if no published but updated exists, use updated as published
            if published_at is None and updated_at is not None and pub_raw is None:
                published_at = updated_at
                updated_at = None
            items.append(
                FeedItem(
                    source=feed.name,
                    title=str(entry.get("title", "")).strip(),
                    summary=str(entry.get("summary", "")).strip(),
                    url=str(entry.get("link", "")).strip(),
                    guid=guid.strip(),
                    published_at=published_at,
                    fetched_at=now,
                    updated_at=updated_at,
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
        pub_str = str(row.get("published_at") or row.get("published") or "")
        upd_str = str(row.get("updated_at") or row.get("updated") or "")
        out.append(
            FeedItem(
                source=feed.name,
                title=str(row.get("title", "")).strip(),
                summary=str(row.get("summary") or row.get("description") or "").strip(),
                url=str(row.get("url", "")).strip(),
                guid=guid.strip(),
                published_at=_parse_dt(pub_str),
                fetched_at=now,
                updated_at=_parse_dt(upd_str),
            )
        )
    return out
