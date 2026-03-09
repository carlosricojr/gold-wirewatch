"""Tests for news routing trust and suppression fixes.

Covers:
1. Config-driven trust tier on FeedConfig
2. Source tier respects config trust_tier override
3. Suppression scoped per event fingerprint (different headlines don't collide)
4. Same/near-duplicate headlines still suppress correctly
5. Reputable configured feed gets Tier B, not Tier C
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from gold_wirewatch.config import FeedConfig, load_feeds
from gold_wirewatch.confirmers import (
    ConfirmerName,
    ConfirmerReading,
    ConfirmerSnapshot,
    ConfirmerStatus,
)
from gold_wirewatch.dedupe import canonicalize_title, event_fingerprint
from gold_wirewatch.evidence_gate import DecisionState, EvidenceVerdict
from gold_wirewatch.source_tier import (
    CorroborationState,
    SourceMeta,
    SourceTier,
    classify_source,
    corroborate,
)
from gold_wirewatch.suppression import SuppressionState, suppression_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snapshot(fresh: int) -> ConfirmerSnapshot:
    readings = []
    for i, name in enumerate(ConfirmerName):
        if i < fresh:
            readings.append(ConfirmerReading(name, ConfirmerStatus.FRESH, 100.0, datetime.now(UTC)))
        else:
            readings.append(ConfirmerReading(name, ConfirmerStatus.UNAVAILABLE))
    return ConfirmerSnapshot(readings=readings)


def _verdict(decision: DecisionState) -> EvidenceVerdict:
    return EvidenceVerdict(decision, "test", 3, SourceTier.B, CorroborationState.SINGLE_VERIFIED, False)


# ---------------------------------------------------------------------------
# 1. FeedConfig trust_tier field
# ---------------------------------------------------------------------------


class TestFeedConfigTrustTier:
    def test_feedconfig_accepts_trust_tier(self):
        """FeedConfig should accept an optional trust_tier field."""
        fc = FeedConfig(name="BBC World", url="https://x", kind="rss", trust_tier="B")
        assert fc.trust_tier == "B"

    def test_feedconfig_trust_tier_defaults_none(self):
        """FeedConfig trust_tier should default to None."""
        fc = FeedConfig(name="BBC World", url="https://x", kind="rss")
        assert fc.trust_tier is None

    def test_load_feeds_with_trust_tier(self, tmp_path: Path):
        """load_feeds should parse trust_tier from YAML."""
        yaml_content = (
            "feeds:\n"
            "  - name: BBC World\n"
            "    url: https://feeds.bbci.co.uk/news/world/rss.xml\n"
            "    kind: rss\n"
            "    trust_tier: B\n"
            "    enabled: true\n"
            "  - name: Random Blog\n"
            "    url: https://example.com/feed\n"
            "    kind: rss\n"
            "    enabled: true\n"
        )
        (tmp_path / "sources.yaml").write_text(yaml_content, encoding="utf-8")
        feeds = load_feeds(str(tmp_path / "sources.yaml"))
        assert len(feeds) == 2
        assert feeds[0].trust_tier == "B"
        assert feeds[1].trust_tier is None


# ---------------------------------------------------------------------------
# 2. Source tier respects config-driven override
# ---------------------------------------------------------------------------


class TestSourceTierConfigOverride:
    def test_classify_source_with_config_tier_b(self):
        """classify_source should return Tier B when config_tier='B', even for unknown source."""
        tier = classify_source("BBC World", config_tier="B")
        assert tier == SourceTier.B

    def test_classify_source_without_config_tier_falls_back(self):
        """classify_source should fall back to heuristic when no config_tier."""
        tier = classify_source("BBC World")
        assert tier == SourceTier.C  # BBC isn't in the hardcoded list

    def test_classify_source_config_tier_does_not_override_tier_a(self):
        """Config tier B should not downgrade a heuristic Tier A source."""
        tier = classify_source("Federal Reserve Press", config_tier="B")
        # Tier A from heuristic is better than config B, keep A
        assert tier == SourceTier.A

    def test_corroborate_with_config_tiers(self):
        """corroborate should use config_tiers mapping for trust classification."""
        config_tiers = {"BBC World": "B", "CNBC World": "B"}
        meta = corroborate(["BBC World"], config_tiers=config_tiers)
        assert meta.tier == SourceTier.B
        assert meta.corroboration == CorroborationState.SINGLE_VERIFIED

    def test_corroborate_without_config_tiers_bbc_is_c(self):
        """Without config_tiers, BBC falls to Tier C / single_unverified."""
        meta = corroborate(["BBC World"])
        assert meta.tier == SourceTier.C
        assert meta.corroboration == CorroborationState.SINGLE_UNVERIFIED


# ---------------------------------------------------------------------------
# 3. Suppression scoped per event fingerprint
# ---------------------------------------------------------------------------


class TestSuppressionPerEvent:
    def test_different_headlines_same_state_both_fire(self):
        """Two different headlines with the same decision state should NOT suppress each other."""
        state = SuppressionState()
        meta = SourceMeta(SourceTier.B, CorroborationState.SINGLE_VERIFIED, 1, ("BBC",))
        snap = _make_snapshot(3)
        verdict = _verdict(DecisionState.CONDITIONAL)
        sup_key = suppression_key(meta, snap, verdict)

        # Headline 1: "Fed raises rates"
        fp1 = event_fingerprint(canonicalize_title("Fed raises rates by 25bps"))
        group1 = f"main_gate:{fp1}"
        assert not state.should_suppress(group1, sup_key)
        state.record(group1, sup_key)

        # Headline 2: "China gold reserves increase" — different event, same state
        fp2 = event_fingerprint(canonicalize_title("China gold reserves increase sharply"))
        group2 = f"main_gate:{fp2}"
        assert not state.should_suppress(group2, sup_key), (
            "Different headline should NOT be suppressed even with same state key"
        )

    def test_same_headline_same_state_suppresses(self):
        """Same headline with same state SHOULD suppress (correct delta behavior)."""
        state = SuppressionState()
        meta = SourceMeta(SourceTier.B, CorroborationState.SINGLE_VERIFIED, 1, ("BBC",))
        snap = _make_snapshot(3)
        verdict = _verdict(DecisionState.CONDITIONAL)
        sup_key = suppression_key(meta, snap, verdict)

        fp = event_fingerprint(canonicalize_title("Fed raises rates by 25bps"))
        group = f"main_gate:{fp}"
        assert not state.should_suppress(group, sup_key)
        state.record(group, sup_key)
        assert state.should_suppress(group, sup_key), (
            "Same headline + same state should be suppressed"
        )

    def test_same_headline_different_state_fires(self):
        """Same headline with changed state should fire again (delta detected)."""
        state = SuppressionState()
        meta = SourceMeta(SourceTier.B, CorroborationState.SINGLE_VERIFIED, 1, ("BBC",))
        snap = _make_snapshot(3)

        fp = event_fingerprint(canonicalize_title("Fed raises rates by 25bps"))
        group = f"main_gate:{fp}"

        v1 = _verdict(DecisionState.CONDITIONAL)
        k1 = suppression_key(meta, snap, v1)
        state.record(group, k1)

        v2 = _verdict(DecisionState.ACTIONABLE_LONG)
        k2 = suppression_key(meta, snap, v2)
        assert not state.should_suppress(group, k2), (
            "State change should fire even for same headline"
        )


# ---------------------------------------------------------------------------
# 4. Reputable configured feeds remain evidence-gated but not Tier C
# ---------------------------------------------------------------------------


class TestReputableFeedEvidenceGating:
    def test_bbc_with_config_tier_is_not_headline_only(self):
        """A BBC item with config tier B should get SINGLE_VERIFIED, not SINGLE_UNVERIFIED.

        This means the evidence gate won't force it to HEADLINE_ONLY just for
        being Tier C single-unverified.
        """
        config_tiers = {"BBC World": "B"}
        meta = corroborate(["BBC World"], config_tiers=config_tiers)
        # With Tier B + SINGLE_VERIFIED, the evidence gate won't force HEADLINE_ONLY
        assert meta.tier == SourceTier.B
        assert meta.corroboration == CorroborationState.SINGLE_VERIFIED

    def test_unknown_feed_without_config_stays_tier_c(self):
        """An unconfigured feed should still default to Tier C behavior."""
        config_tiers = {"BBC World": "B"}
        meta = corroborate(["Random Unknown Blog"], config_tiers=config_tiers)
        assert meta.tier == SourceTier.C
        assert meta.corroboration == CorroborationState.SINGLE_UNVERIFIED
