"""Comprehensive tests for the dedupe module.

Covers:
- Exact duplicate suppression
- Near-duplicate suppression (same event phrased differently)
- Replayed delivery suppression (same session/event wrapper)
- Allow-through on material delta (source tier upgrade, confirmer change, state change)
- No-regression for existing alert path
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from gold_wirewatch.dedupe import (
    ContentDeduplicator,
    DeliveryDeduplicator,
    canonicalize_title,
    event_fingerprint,
    _content_tokens,
)


# ===========================================================================
# Title canonicalization
# ===========================================================================

class TestCanonicalizeTitle:
    def test_basic_lowercase_and_strip(self):
        assert canonicalize_title("  FED RAISES RATES  ") == "fed raises rates"

    def test_strip_breaking_prefix(self):
        assert canonicalize_title("BREAKING: Fed raises rates") == "fed raises rates"
        assert canonicalize_title("Breaking — Fed raises rates") == "fed raises rates"

    def test_strip_update_prefix(self):
        assert canonicalize_title("Update: Gold surges past $2000") == "gold surges past $2000"

    def test_strip_just_in(self):
        assert canonicalize_title("Just In: Treasury yields spike") == "treasury yields spike"

    def test_strip_live_updates_suffix(self):
        result = canonicalize_title("Gold market reaction - live updates")
        assert "live update" not in result

    def test_strip_developing_suffix(self):
        result = canonicalize_title("Fed decision — Developing Story")
        assert "developing" not in result

    def test_strip_ellipsis(self):
        assert canonicalize_title("Gold rises amid...") == "gold rises amid"
        assert canonicalize_title("Gold rises amid…") == "gold rises amid"

    def test_normalize_quotes_and_dashes(self):
        result = canonicalize_title("Fed\u2019s \u201chawkish\u201d stance \u2013 rates up")
        assert "\u2019" not in result  # smart quote normalized
        assert "\u2013" not in result  # em-dash normalized

    def test_nested_boilerplate(self):
        """Nested boilerplate like 'BREAKING: Update: ...' should be fully stripped."""
        result = canonicalize_title("BREAKING: Update: Gold hits record")
        assert result == "gold hits record"

    def test_empty_after_strip(self):
        """Pure boilerplate title returns empty."""
        result = canonicalize_title("BREAKING:")
        assert result == ""

    def test_heres_the_latest(self):
        result = canonicalize_title("Here's the latest: Gold surges")
        assert result == "gold surges"


class TestContentTokens:
    def test_removes_noise_words(self):
        tokens = _content_tokens("the fed is raising rates for the economy")
        assert "the" not in tokens
        assert "is" not in tokens
        assert "for" not in tokens
        assert "fed" in tokens
        assert "raising" in tokens
        assert "rates" in tokens
        assert "economy" in tokens

    def test_sorted_and_deduped(self):
        tokens = _content_tokens("gold gold gold prices rise")
        assert tokens == sorted(set(tokens))
        assert tokens.count("gold") == 1


# ===========================================================================
# Event fingerprinting
# ===========================================================================

class TestEventFingerprint:
    def test_identical_titles_same_fingerprint(self):
        fp1 = event_fingerprint(canonicalize_title("Fed raises rates by 25bps"))
        fp2 = event_fingerprint(canonicalize_title("Fed raises rates by 25bps"))
        assert fp1 == fp2

    def test_near_duplicate_same_fingerprint(self):
        """Same event with different boilerplate wrappers → same fingerprint."""
        fp1 = event_fingerprint(canonicalize_title("BREAKING: Fed raises rates by 25bps"))
        fp2 = event_fingerprint(canonicalize_title("Update: Fed raises rates by 25bps"))
        assert fp1 == fp2

    def test_same_event_different_articles(self):
        """Minor word differences (articles, connectors) → same fingerprint."""
        fp1 = event_fingerprint(canonicalize_title("The Fed raises rates"))
        fp2 = event_fingerprint(canonicalize_title("Fed raises the rates"))
        assert fp1 == fp2

    def test_different_events_different_fingerprint(self):
        fp1 = event_fingerprint(canonicalize_title("Fed raises rates by 25bps"))
        fp2 = event_fingerprint(canonicalize_title("Gold hits all-time high $3000"))
        assert fp1 != fp2

    def test_title_churn_same_event(self):
        """Live-update title churn for the same underlying event."""
        titles = [
            "BREAKING: Fed holds rates steady",
            "Update: Fed holds rates steady, signals patience",
            "Just in: Fed holds rates steady",
            "Fed holds rates steady - Live Updates",
        ]
        fps = [event_fingerprint(canonicalize_title(t)) for t in titles]
        # First and third should be identical (same core)
        assert fps[0] == fps[2]
        # The one with extra "signals patience" will differ (new info)
        # That's correct behavior — new info = new fingerprint

    def test_fingerprint_length(self):
        fp = event_fingerprint("some canonical title")
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)


# ===========================================================================
# Content deduplicator (cooldown TTL)
# ===========================================================================

class TestContentDeduplicator:
    def test_first_emission_not_suppressed(self):
        dd = ContentDeduplicator(cooldown_seconds=600)
        fp = event_fingerprint(canonicalize_title("Fed raises rates"))
        assert not dd.should_suppress(fp, "B", "Conditional", "1-2")

    def test_exact_duplicate_suppressed(self):
        """Same fingerprint + same state within cooldown → suppressed."""
        dd = ContentDeduplicator(cooldown_seconds=600)
        fp = event_fingerprint(canonicalize_title("Fed raises rates"))
        dd.record(fp, "B", "Conditional", "1-2")
        assert dd.should_suppress(fp, "B", "Conditional", "1-2")

    def test_near_duplicate_suppressed(self):
        """Near-duplicate titles (same fingerprint) within cooldown → suppressed."""
        dd = ContentDeduplicator(cooldown_seconds=600)
        t1 = "BREAKING: Fed raises rates"
        t2 = "Update: Fed raises rates"
        fp1 = event_fingerprint(canonicalize_title(t1))
        fp2 = event_fingerprint(canonicalize_title(t2))
        assert fp1 == fp2  # same fingerprint
        dd.record(fp1, "B", "Conditional", "1-2")
        assert dd.should_suppress(fp2, "B", "Conditional", "1-2")

    def test_cooldown_expiry_allows_through(self):
        """After cooldown expires, same fingerprint is allowed."""
        dd = ContentDeduplicator(cooldown_seconds=0.05)  # 50ms
        fp = event_fingerprint(canonicalize_title("Fed raises rates"))
        dd.record(fp, "B", "Conditional", "1-2")
        assert dd.should_suppress(fp, "B", "Conditional", "1-2")
        time.sleep(0.1)
        assert not dd.should_suppress(fp, "B", "Conditional", "1-2")

    def test_material_delta_tier_upgrade(self):
        """Tier upgrade (C→B) within cooldown → allowed through."""
        dd = ContentDeduplicator(cooldown_seconds=600)
        fp = event_fingerprint(canonicalize_title("Gold surges"))
        dd.record(fp, "C", "Headline only", "0")
        # Same fingerprint but tier upgraded to B
        assert not dd.should_suppress(fp, "B", "Headline only", "0")

    def test_material_delta_decision_escalation(self):
        """Decision escalation within cooldown → allowed through."""
        dd = ContentDeduplicator(cooldown_seconds=600)
        fp = event_fingerprint(canonicalize_title("Gold surges"))
        dd.record(fp, "B", "Conditional", "1-2")
        # Same fingerprint but decision escalated to Actionable long
        assert not dd.should_suppress(fp, "B", "Actionable long", "1-2")

    def test_material_delta_confirmer_upgrade(self):
        """Fresh confirmer bucket upgrade within cooldown → allowed through."""
        dd = ContentDeduplicator(cooldown_seconds=600)
        fp = event_fingerprint(canonicalize_title("Gold surges"))
        dd.record(fp, "B", "Conditional", "0")
        # Confirmers now fresh
        assert not dd.should_suppress(fp, "B", "Conditional", "3")

    def test_no_material_delta_suppressed(self):
        """Same state or downgrade → still suppressed."""
        dd = ContentDeduplicator(cooldown_seconds=600)
        fp = event_fingerprint(canonicalize_title("Gold surges"))
        dd.record(fp, "B", "Conditional", "1-2")
        # Same state
        assert dd.should_suppress(fp, "B", "Conditional", "1-2")
        # Tier downgrade (B→C) — not an upgrade, suppressed
        assert dd.should_suppress(fp, "C", "Conditional", "1-2")
        # Decision downgrade (Conditional→Fade) — suppressed
        assert dd.should_suppress(fp, "B", "Fade", "1-2")

    def test_different_events_independent(self):
        """Different fingerprints are tracked independently."""
        dd = ContentDeduplicator(cooldown_seconds=600)
        fp1 = event_fingerprint(canonicalize_title("Fed raises rates"))
        fp2 = event_fingerprint(canonicalize_title("Gold hits record"))
        dd.record(fp1, "B", "Conditional", "1-2")
        assert dd.should_suppress(fp1, "B", "Conditional", "1-2")
        assert not dd.should_suppress(fp2, "B", "Conditional", "1-2")

    def test_clear(self):
        dd = ContentDeduplicator(cooldown_seconds=600)
        fp = event_fingerprint(canonicalize_title("Fed raises rates"))
        dd.record(fp, "B", "Conditional", "1-2")
        assert dd.should_suppress(fp, "B", "Conditional", "1-2")
        dd.clear()
        assert not dd.should_suppress(fp, "B", "Conditional", "1-2")


# ===========================================================================
# Delivery deduplicator (replay guard)
# ===========================================================================

class TestDeliveryDeduplicator:
    def test_first_delivery_not_duplicate(self):
        dd = DeliveryDeduplicator(ttl_seconds=600)
        did = DeliveryDeduplicator.make_delivery_id("fp123", "sk456")
        assert not dd.is_duplicate(did)

    def test_replayed_delivery_detected(self):
        """Same delivery ID replayed → detected as duplicate."""
        dd = DeliveryDeduplicator(ttl_seconds=600)
        did = DeliveryDeduplicator.make_delivery_id("fp123", "sk456")
        dd.record(did)
        assert dd.is_duplicate(did)

    def test_different_delivery_ids_independent(self):
        dd = DeliveryDeduplicator(ttl_seconds=600)
        did1 = DeliveryDeduplicator.make_delivery_id("fp123", "sk456")
        did2 = DeliveryDeduplicator.make_delivery_id("fp789", "sk456")
        dd.record(did1)
        assert dd.is_duplicate(did1)
        assert not dd.is_duplicate(did2)

    def test_ttl_expiry(self):
        dd = DeliveryDeduplicator(ttl_seconds=0.05)
        did = DeliveryDeduplicator.make_delivery_id("fp123", "sk456")
        dd.record(did)
        assert dd.is_duplicate(did)
        time.sleep(0.1)
        assert not dd.is_duplicate(did)

    def test_delivery_id_deterministic(self):
        did1 = DeliveryDeduplicator.make_delivery_id("fp", "sk")
        did2 = DeliveryDeduplicator.make_delivery_id("fp", "sk")
        assert did1 == did2

    def test_delivery_id_length(self):
        did = DeliveryDeduplicator.make_delivery_id("fp", "sk")
        assert len(did) == 20

    def test_simulated_heartbeat_replay(self):
        """Simulate a heartbeat/system replaying the same alert batch."""
        dd = DeliveryDeduplicator(ttl_seconds=600)
        # First delivery batch
        events = [("fp1", "sk1"), ("fp2", "sk2"), ("fp3", "sk3")]
        delivery_ids = [DeliveryDeduplicator.make_delivery_id(fp, sk) for fp, sk in events]
        
        # First pass: all new
        for did in delivery_ids:
            assert not dd.is_duplicate(did)
            dd.record(did)
        
        # Replay: all duplicates
        for did in delivery_ids:
            assert dd.is_duplicate(did)

    def test_clear(self):
        dd = DeliveryDeduplicator(ttl_seconds=600)
        did = DeliveryDeduplicator.make_delivery_id("fp", "sk")
        dd.record(did)
        assert dd.is_duplicate(did)
        dd.clear()
        assert not dd.is_duplicate(did)


# ===========================================================================
# Integration scenario: synthetic replay harness
# ===========================================================================

class TestSyntheticReplayHarness:
    """End-to-end scenario simulating real duplicate patterns."""

    def test_full_pipeline_scenario(self):
        """Simulate a batch of items through all dedupe layers."""
        content_dd = ContentDeduplicator(cooldown_seconds=600)
        delivery_dd = DeliveryDeduplicator(ttl_seconds=600)

        # Simulated alert events with metadata
        events = [
            # (title, tier, decision, fresh_bucket, suppression_key)
            ("BREAKING: Fed raises rates by 25bps", "B", "Conditional", "1-2", "sk1"),
            ("Update: Fed raises rates by 25bps", "B", "Conditional", "1-2", "sk1"),      # near-dup
            ("Fed raises rates by 25bps", "B", "Conditional", "1-2", "sk1"),               # exact
            ("Gold hits all-time high at $3000", "C", "Headline only", "0", "sk2"),        # new event
            ("BREAKING: Gold hits all-time high at $3000", "C", "Headline only", "0", "sk2"),  # near-dup
            ("Gold hits all-time high at $3000", "B", "Conditional", "3", "sk3"),           # MATERIAL DELTA (tier+decision+confirmer)
        ]

        emitted = []
        suppressed_total = 0

        for title, tier, decision, fresh_bucket, sup_key in events:
            ct = canonicalize_title(title)
            fp = event_fingerprint(ct)
            did = DeliveryDeduplicator.make_delivery_id(fp, sup_key)

            # Layer: delivery dedupe
            if delivery_dd.is_duplicate(did):
                suppressed_total += 1
                continue

            # Layer: content dedupe
            if content_dd.should_suppress(fp, tier, decision, fresh_bucket):
                suppressed_total += 1
                continue

            # Emit
            content_dd.record(fp, tier, decision, fresh_bucket)
            delivery_dd.record(did)
            emitted.append(title)

        # Expected: 3 emitted (first Fed, first Gold, Gold with material delta)
        assert len(emitted) == 3
        assert suppressed_total == 3  # 3 near-dups/exact-dups suppressed
        assert emitted[0] == "BREAKING: Fed raises rates by 25bps"
        assert emitted[1] == "Gold hits all-time high at $3000"
        assert emitted[2] == "Gold hits all-time high at $3000"  # material delta version

    def test_duplicate_rate_measurement(self):
        """Measure before/after duplicate rates with synthetic data."""
        content_dd = ContentDeduplicator(cooldown_seconds=600)
        delivery_dd = DeliveryDeduplicator(ttl_seconds=600)

        # Simulate 20 items: 10 unique events, each with 1 near-duplicate
        unique_events = [
            "Fed raises rates by 25bps unexpectedly",
            "Gold hits all-time high at $3000 per ounce",
            "China central bank increases gold reserves significantly",
            "Treasury yields spike after inflation report released",
            "Dollar index drops sharply following employment data",
            "Oil prices surge after OPEC production cuts announced",
            "Swiss National Bank intervenes in currency markets",
            "Japan BOJ ends negative interest rate policy",
            "European Central Bank signals hawkish pivot ahead",
            "Russia sanctions expand to precious metals exports",
        ]
        all_items = []
        for evt in unique_events:
            all_items.append(evt)
            all_items.append(f"BREAKING: {evt}")  # near-duplicate

        # WITHOUT dedupe: all 20 would fire
        total_without_dedupe = len(all_items)

        # WITH dedupe:
        emitted = 0
        for title in all_items:
            ct = canonicalize_title(title)
            fp = event_fingerprint(ct)
            did = DeliveryDeduplicator.make_delivery_id(fp, "sk_default")
            if delivery_dd.is_duplicate(did):
                continue
            if content_dd.should_suppress(fp, "B", "Conditional", "1-2"):
                continue
            content_dd.record(fp, "B", "Conditional", "1-2")
            delivery_dd.record(did)
            emitted += 1

        # Should emit exactly 10 (one per unique event)
        assert emitted == 10
        duplicate_rate_before = 0.5  # 10 of 20 are duplicates
        duplicate_rate_after = 0.0   # all duplicates suppressed
        assert emitted == total_without_dedupe / 2
