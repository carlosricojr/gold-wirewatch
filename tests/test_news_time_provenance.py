"""Tests for trustworthy news-release-time provenance.

Covers:
- FeedItem.updated_at field
- AlertPayload.news_time / news_time_kind / wirewatch_seen_time
- format_compact() shows a labeled time line
- feeds.py parses updated_at separately from published_at
- Safe parsing / sanity checks for absurd timestamps
"""
from datetime import UTC, datetime, timedelta

import pytest

from gold_wirewatch.alert_payload import build_alert_payload
from gold_wirewatch.confirmers import (
    ConfirmerName,
    ConfirmerReading,
    ConfirmerSnapshot,
    ConfirmerStatus,
)
from gold_wirewatch.evidence_gate import DecisionState, EvidenceVerdict
from gold_wirewatch.models import FeedItem, ScoreResult
from gold_wirewatch.source_tier import CorroborationState, SourceMeta, SourceTier


def _snap(fresh: int = 3) -> ConfirmerSnapshot:
    readings = []
    for i, name in enumerate(ConfirmerName):
        if i < fresh:
            readings.append(ConfirmerReading(name, ConfirmerStatus.FRESH, 100.0, datetime.now(UTC)))
        else:
            readings.append(ConfirmerReading(name, ConfirmerStatus.UNAVAILABLE))
    return ConfirmerSnapshot(readings=readings)


def _verdict() -> EvidenceVerdict:
    return EvidenceVerdict(
        DecisionState.CONDITIONAL, "ok", 3, SourceTier.B,
        CorroborationState.SINGLE_VERIFIED, False,
    )


def _meta() -> SourceMeta:
    return SourceMeta(SourceTier.B, CorroborationState.SINGLE_VERIFIED, 1, ("Reuters",))


def _score() -> ScoreResult:
    return ScoreResult(0.7, 0.6, ["fed", "rate"])


# ── FeedItem.updated_at field ──────────────────────────────────────────

class TestFeedItemUpdatedAt:
    def test_feeditem_has_updated_at_field(self):
        now = datetime.now(UTC)
        item = FeedItem(
            source="test",
            title="t",
            summary="s",
            url="u",
            guid="g",
            published_at=now,
            fetched_at=now,
            updated_at=now - timedelta(minutes=5),
        )
        assert item.updated_at is not None

    def test_feeditem_updated_at_defaults_none(self):
        now = datetime.now(UTC)
        item = FeedItem(
            source="test",
            title="t",
            summary="s",
            url="u",
            guid="g",
            published_at=now,
            fetched_at=now,
        )
        assert item.updated_at is None


# ── AlertPayload provenance fields ─────────────────────────────────────

class TestAlertPayloadProvenance:
    def test_payload_has_news_time_fields(self):
        """AlertPayload must expose news_time, news_time_kind, wirewatch_seen_time."""
        pub = datetime(2026, 3, 9, 18, 0, 0, tzinfo=UTC)
        fetched = datetime(2026, 3, 9, 18, 5, 0, tzinfo=UTC)
        item = FeedItem("src", "Headline", "sum", "url", "g", pub, fetched)
        payload = build_alert_payload(
            item, _score(), _meta(), _verdict(), _snap(), "main_gate", "UTC",
        )
        assert hasattr(payload, "news_time")
        assert hasattr(payload, "news_time_kind")
        assert hasattr(payload, "wirewatch_seen_time")

    def test_published_at_yields_published_kind(self):
        pub = datetime(2026, 3, 9, 18, 0, 0, tzinfo=UTC)
        fetched = datetime(2026, 3, 9, 18, 5, 0, tzinfo=UTC)
        item = FeedItem("src", "Headline", "sum", "url", "g", pub, fetched)
        payload = build_alert_payload(
            item, _score(), _meta(), _verdict(), _snap(), "main_gate", "UTC",
        )
        assert payload.news_time_kind == "published"
        assert "2026-03-09 18:00" in payload.news_time

    def test_updated_at_fallback_yields_updated_kind(self):
        """When published_at is None but updated_at exists, use updated."""
        upd = datetime(2026, 3, 9, 17, 30, 0, tzinfo=UTC)
        fetched = datetime(2026, 3, 9, 18, 5, 0, tzinfo=UTC)
        item = FeedItem("src", "Headline", "sum", "url", "g", None, fetched, updated_at=upd)
        payload = build_alert_payload(
            item, _score(), _meta(), _verdict(), _snap(), "main_gate", "UTC",
        )
        assert payload.news_time_kind == "updated"
        assert "2026-03-09 17:30" in payload.news_time

    def test_fetched_at_fallback_yields_fetched_kind(self):
        """When both published_at and updated_at are None, fallback to fetched."""
        fetched = datetime(2026, 3, 9, 18, 5, 0, tzinfo=UTC)
        item = FeedItem("src", "Headline", "sum", "url", "g", None, fetched)
        payload = build_alert_payload(
            item, _score(), _meta(), _verdict(), _snap(), "main_gate", "UTC",
        )
        assert payload.news_time_kind == "fetched"
        assert "2026-03-09 18:05" in payload.news_time

    def test_wirewatch_seen_time_always_set(self):
        pub = datetime(2026, 3, 9, 18, 0, 0, tzinfo=UTC)
        fetched = datetime(2026, 3, 9, 18, 5, 0, tzinfo=UTC)
        item = FeedItem("src", "Headline", "sum", "url", "g", pub, fetched)
        payload = build_alert_payload(
            item, _score(), _meta(), _verdict(), _snap(), "main_gate", "UTC",
        )
        assert "2026-03-09 18:05" in payload.wirewatch_seen_time

    def test_to_dict_includes_provenance(self):
        pub = datetime(2026, 3, 9, 18, 0, 0, tzinfo=UTC)
        fetched = datetime(2026, 3, 9, 18, 5, 0, tzinfo=UTC)
        item = FeedItem("src", "Headline", "sum", "url", "g", pub, fetched)
        payload = build_alert_payload(
            item, _score(), _meta(), _verdict(), _snap(), "main_gate", "UTC",
        )
        d = payload.to_dict()
        assert "news_time" in d
        assert "news_time_kind" in d
        assert "wirewatch_seen_time" in d


# ── format_compact() shows labeled time line ───────────────────────────

class TestCompactTimeLine:
    def test_compact_shows_news_release_time(self):
        pub = datetime(2026, 3, 9, 18, 0, 0, tzinfo=UTC)
        fetched = datetime(2026, 3, 9, 18, 5, 0, tzinfo=UTC)
        item = FeedItem("src", "Big News", "sum", "url", "g", pub, fetched)
        payload = build_alert_payload(
            item, _score(), _meta(), _verdict(), _snap(), "main_gate", "UTC",
        )
        text = payload.format_compact()
        assert "News release time:" in text

    def test_compact_shows_updated_label_when_no_published(self):
        upd = datetime(2026, 3, 9, 17, 30, 0, tzinfo=UTC)
        fetched = datetime(2026, 3, 9, 18, 5, 0, tzinfo=UTC)
        item = FeedItem("src", "Big News", "sum", "url", "g", None, fetched, updated_at=upd)
        payload = build_alert_payload(
            item, _score(), _meta(), _verdict(), _snap(), "main_gate", "UTC",
        )
        text = payload.format_compact()
        assert "Source updated time:" in text
        assert "(published unavailable)" in text

    def test_compact_shows_first_seen_when_no_source_time(self):
        fetched = datetime(2026, 3, 9, 18, 5, 0, tzinfo=UTC)
        item = FeedItem("src", "Big News", "sum", "url", "g", None, fetched)
        payload = build_alert_payload(
            item, _score(), _meta(), _verdict(), _snap(), "main_gate", "UTC",
        )
        text = payload.format_compact()
        assert "First seen by WireWatch:" in text
        assert "(source time unavailable)" in text


# ── feeds.py updated_at parsing ────────────────────────────────────────

class TestFeedUpdatedAtParsing:
    def test_rss_parses_updated_separately(self):
        from gold_wirewatch.config import FeedConfig, Settings
        from gold_wirewatch.feeds import poll_feed

        rss = (
            "<?xml version='1.0'?><rss><channel><item>"
            "<title>Test</title><link>https://x</link><guid>1</guid>"
            "<pubDate>Fri, 13 Feb 2026 14:00:00 GMT</pubDate>"
            "<updated>Fri, 13 Feb 2026 15:00:00 GMT</updated>"
            "<description>desc</description>"
            "</item></channel></rss>"
        )

        class _Resp:
            def __init__(self, t): self.text = t
            def raise_for_status(self): pass

        class _Client:
            def __init__(self, b): self.body = b
            def get(self, url, timeout): return _Resp(self.body)

        settings = Settings(openclaw_token="x")
        items = poll_feed(_Client(rss), FeedConfig("f", "u", "rss"), settings)
        assert len(items) == 1
        assert items[0].published_at is not None
        assert items[0].updated_at is not None
        assert items[0].published_at != items[0].updated_at

    def test_rss_no_updated_field_gives_none(self):
        from gold_wirewatch.config import FeedConfig, Settings
        from gold_wirewatch.feeds import poll_feed

        rss = (
            "<?xml version='1.0'?><rss><channel><item>"
            "<title>Test</title><link>https://x</link><guid>1</guid>"
            "<pubDate>Fri, 13 Feb 2026 14:00:00 GMT</pubDate>"
            "<description>desc</description>"
            "</item></channel></rss>"
        )

        class _Resp:
            def __init__(self, t): self.text = t
            def raise_for_status(self): pass

        class _Client:
            def __init__(self, b): self.body = b
            def get(self, url, timeout): return _Resp(self.body)

        settings = Settings(openclaw_token="x")
        items = poll_feed(_Client(rss), FeedConfig("f", "u", "rss"), settings)
        assert items[0].updated_at is None

    def test_json_parses_updated_at(self):
        import json
        from gold_wirewatch.config import FeedConfig, Settings
        from gold_wirewatch.feeds import poll_feed

        body = json.dumps([{
            "id": "1", "title": "Test", "url": "https://x",
            "published": "Fri, 13 Feb 2026 14:00:00 GMT",
            "updated_at": "Fri, 13 Feb 2026 15:00:00 GMT",
        }])

        class _Resp:
            def __init__(self, t): self.text = t
            def raise_for_status(self): pass

        class _Client:
            def __init__(self, b): self.body = b
            def get(self, url, timeout): return _Resp(self.body)

        settings = Settings(openclaw_token="x")
        items = poll_feed(_Client(body), FeedConfig("j", "u", "json"), settings)
        assert items[0].updated_at is not None


# ── Sanity checks for absurd timestamps ────────────────────────────────

class TestTimestampSanity:
    def test_future_published_at_rejected(self):
        """A published_at far in the future should be treated as invalid."""
        from gold_wirewatch.alert_payload import _resolve_news_time

        now = datetime(2026, 3, 9, 18, 0, 0, tzinfo=UTC)
        future = now + timedelta(days=365)
        t, kind = _resolve_news_time(future, None, now, "UTC")
        # Should NOT use the absurd future timestamp
        assert kind == "fetched"

    def test_ancient_published_at_rejected(self):
        """A published_at from decades ago should be treated as invalid."""
        from gold_wirewatch.alert_payload import _resolve_news_time

        now = datetime(2026, 3, 9, 18, 0, 0, tzinfo=UTC)
        ancient = datetime(2000, 1, 1, tzinfo=UTC)
        t, kind = _resolve_news_time(ancient, None, now, "UTC")
        assert kind == "fetched"

    def test_reasonable_published_at_accepted(self):
        from gold_wirewatch.alert_payload import _resolve_news_time

        now = datetime(2026, 3, 9, 18, 0, 0, tzinfo=UTC)
        pub = now - timedelta(hours=2)
        t, kind = _resolve_news_time(pub, None, now, "UTC")
        assert kind == "published"
