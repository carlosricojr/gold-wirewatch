"""Tests for alert_payload module."""
from datetime import UTC, datetime

from gold_wirewatch.alert_payload import build_alert_payload, build_market_move_payload
from gold_wirewatch.confirmers import (
    ConfirmerName,
    ConfirmerReading,
    ConfirmerSnapshot,
    ConfirmerStatus,
)
from gold_wirewatch.evidence_gate import DecisionState, EvidenceVerdict
from gold_wirewatch.models import FeedItem, ScoreResult
from gold_wirewatch.source_tier import CorroborationState, SourceMeta, SourceTier


def _make_snapshot(fresh: int = 4) -> ConfirmerSnapshot:
    readings = []
    for i, name in enumerate(ConfirmerName):
        if i < fresh:
            readings.append(ConfirmerReading(name, ConfirmerStatus.FRESH, 100.0, datetime.now(UTC)))
        else:
            readings.append(ConfirmerReading(name, ConfirmerStatus.UNAVAILABLE))
    return ConfirmerSnapshot(readings=readings)


def test_build_alert_payload_basic():
    item = FeedItem(
        source="Federal Reserve Press",
        title="Fed cuts rates 50bp",
        summary="Emergency rate cut announced",
        url="https://fed.gov/release",
        guid="fed-001",
        published_at=datetime.now(UTC),
        fetched_at=datetime.now(UTC),
    )
    score = ScoreResult(0.9, 0.8, ["fed", "rate-cut", "gold-direct"])
    meta = SourceMeta(SourceTier.A, CorroborationState.SINGLE_VERIFIED, 1, ("Fed",))
    verdict = EvidenceVerdict(
        DecisionState.ACTIONABLE_LONG, "passed", 4, SourceTier.A,
        CorroborationState.SINGLE_VERIFIED, False,
    )
    snap = _make_snapshot(4)

    payload = build_alert_payload(item, score, meta, verdict, snap, "main_gate", "America/New_York")

    assert payload.headline == "Fed cuts rates 50bp"
    assert payload.source_tier == "A"
    assert payload.decision == "Actionable long"
    assert not payload.gated
    assert "fed" in payload.reason_line
    assert payload.invalidator  # non-empty


def test_build_alert_payload_gated():
    item = FeedItem(
        source="Random Blog",
        title="Gold might go up",
        summary="Unverified rumor",
        url="https://blog.example.com",
        guid="blog-001",
        published_at=datetime.now(UTC),
        fetched_at=datetime.now(UTC),
    )
    score = ScoreResult(0.3, 0.2, ["no-strong-driver"])
    meta = SourceMeta(SourceTier.C, CorroborationState.SINGLE_UNVERIFIED, 1, ("Blog",))
    verdict = EvidenceVerdict(
        DecisionState.HEADLINE_ONLY, "gated", 0, SourceTier.C,
        CorroborationState.SINGLE_UNVERIFIED, True,
    )
    snap = _make_snapshot(0)

    payload = build_alert_payload(item, score, meta, verdict, snap, "main_gate", "America/New_York")

    assert payload.gated
    assert payload.decision == "Headline only"


def test_payload_to_dict():
    item = FeedItem("src", "title", "sum", "url", "g", datetime.now(UTC), datetime.now(UTC))
    score = ScoreResult(0.5, 0.5, ["test"])
    meta = SourceMeta(SourceTier.B, CorroborationState.SINGLE_VERIFIED, 1, ("Reuters",))
    verdict = EvidenceVerdict(
        DecisionState.CONDITIONAL, "ok", 3, SourceTier.B,
        CorroborationState.SINGLE_VERIFIED, False,
    )
    payload = build_alert_payload(item, score, meta, verdict, _make_snapshot(3), "main_gate", "UTC")
    d = payload.to_dict()
    assert isinstance(d, dict)
    assert "headline" in d
    assert "decision" in d


def test_format_compact():
    item = FeedItem("src", "Big News", "summary", "url", "g", datetime.now(UTC), datetime.now(UTC))
    score = ScoreResult(0.9, 0.9, ["test"])
    meta = SourceMeta(SourceTier.A, CorroborationState.MULTI_SOURCE, 2, ("Fed", "Reuters"))
    verdict = EvidenceVerdict(
        DecisionState.ACTIONABLE_LONG, "ok", 5, SourceTier.A,
        CorroborationState.MULTI_SOURCE, False,
    )
    payload = build_alert_payload(item, score, meta, verdict, _make_snapshot(5), "main_gate", "UTC")
    text = payload.format_compact()
    assert "Big News" in text
    assert "Actionable long" in text
    assert "GATED" not in text


def test_market_move_payload():
    snap = _make_snapshot(3)
    verdict = EvidenceVerdict(
        DecisionState.CONDITIONAL, "ok", 3, SourceTier.A,
        CorroborationState.SINGLE_VERIFIED, False,
    )
    payload = build_market_move_payload("GC1!", 12.5, 60, 2050.0, snap, verdict, "UTC")
    assert "GC1!" in payload.headline
    assert "$12.50" in payload.headline
    assert payload.trigger_path == "market_move"
