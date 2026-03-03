"""Contract tests for the news-alert pipeline hardening.

These tests enforce invariants that must never be violated:
1. Single-source cannot produce high confidence
2. Duplicate events are suppressed within cooldown
3. Critical bypass always emits (regardless of confirmer state)
4. No-new-info => no alert (delta-only suppression)
5. Insufficient tape forces confidence cap
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gold_wirewatch.confirmers import (
    ConfirmerEngine,
    ConfirmerName,
    ConfirmerReading,
    ConfirmerSnapshot,
    ConfirmerStatus,
    StaticProvider,
    StubProvider,
)
from gold_wirewatch.critical_bypass import CriticalCategory, check_critical_bypass
from gold_wirewatch.dedupe import ContentDeduplicator, canonicalize_title, event_fingerprint
from gold_wirewatch.evidence_gate import (
    CONFIDENCE_CAP_INSUFFICIENT,
    DecisionState,
    apply_evidence_gate,
    decide_from_scores,
)
from gold_wirewatch.source_tier import CorroborationState, SourceMeta, SourceTier, corroborate
from gold_wirewatch.suppression import SuppressionState, suppression_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snapshot(fresh: int = 0, stale: int = 0) -> ConfirmerSnapshot:
    """Build a ConfirmerSnapshot with the given number of fresh/stale readings."""
    readings = []
    names = list(ConfirmerName)
    now = datetime.now(UTC)
    for i in range(fresh):
        readings.append(ConfirmerReading(
            name=names[i % len(names)],
            status=ConfirmerStatus.FRESH,
            value=100.0 + i,
            timestamp=now,
            source_label="test",
        ))
    for i in range(stale):
        readings.append(ConfirmerReading(
            name=names[(fresh + i) % len(names)],
            status=ConfirmerStatus.STALE,
            value=99.0,
            timestamp=now,
            source_label="test",
        ))
    return ConfirmerSnapshot(readings=readings, fetched_at=now)


def _single_source_c() -> SourceMeta:
    return corroborate(["random-blog.com"])


def _single_source_b() -> SourceMeta:
    return corroborate(["Reuters"])


# ---------------------------------------------------------------------------
# CONTRACT 1: Single-source cannot be high confidence
# ---------------------------------------------------------------------------

class TestSingleSourceCannotBeHigh:
    """A single tier-C source must never produce Actionable Long or Conditional."""

    def test_single_c_actionable_demoted(self):
        source = _single_source_c()
        confirmers = _make_snapshot(fresh=5)
        raw = DecisionState.ACTIONABLE_LONG
        verdict = apply_evidence_gate(source, confirmers, raw)
        assert verdict.decision == DecisionState.HEADLINE_ONLY
        assert verdict.gated is True

    def test_single_c_conditional_demoted(self):
        source = _single_source_c()
        confirmers = _make_snapshot(fresh=5)
        raw = DecisionState.CONDITIONAL
        verdict = apply_evidence_gate(source, confirmers, raw)
        assert verdict.decision == DecisionState.HEADLINE_ONLY
        assert verdict.gated is True

    def test_single_b_not_demoted_for_tier(self):
        """Tier B single source is allowed through (not tier-gated)."""
        source = _single_source_b()
        confirmers = _make_snapshot(fresh=4)
        raw = DecisionState.CONDITIONAL
        verdict = apply_evidence_gate(source, confirmers, raw)
        assert verdict.decision == DecisionState.CONDITIONAL
        assert verdict.gated is False


# ---------------------------------------------------------------------------
# CONTRACT 2: Duplicate events suppressed
# ---------------------------------------------------------------------------

class TestDuplicateSuppression:
    """Same event emitted twice within cooldown must be suppressed."""

    def test_exact_duplicate_suppressed(self):
        dedup = ContentDeduplicator(cooldown_seconds=600.0)
        fp = event_fingerprint(canonicalize_title("Iran threatens to close Strait of Hormuz"))
        tier, decision, bucket = "C", "Conditional", "0"

        assert dedup.should_suppress(fp, tier, decision, bucket) is False
        dedup.record(fp, tier, decision, bucket)
        assert dedup.should_suppress(fp, tier, decision, bucket) is True

    def test_near_duplicate_titles(self):
        dedup = ContentDeduplicator(cooldown_seconds=600.0)
        t1 = canonicalize_title("BREAKING: Iran threatens to close Hormuz strait")
        t2 = canonicalize_title("Update: Iran threatens to close Hormuz Strait")
        fp1 = event_fingerprint(t1)
        fp2 = event_fingerprint(t2)
        # Near-duplicate titles should produce same fingerprint
        assert fp1 == fp2

    def test_material_delta_allows_through(self):
        """If tier upgrades, same event should emit again."""
        dedup = ContentDeduplicator(cooldown_seconds=600.0)
        fp = event_fingerprint(canonicalize_title("Iran threatens to close Hormuz"))

        dedup.record(fp, "C", "Headline only", "0")
        # Same state → suppressed
        assert dedup.should_suppress(fp, "C", "Headline only", "0") is True
        # Tier upgrade → allowed
        assert dedup.should_suppress(fp, "B", "Headline only", "0") is False

    def test_delta_only_suppression_state(self):
        """SuppressionState blocks same key, allows different key."""
        ss = SuppressionState()
        source = _single_source_c()
        confirmers = _make_snapshot(fresh=0)
        verdict_a = apply_evidence_gate(source, confirmers, DecisionState.FADE)
        key_a = suppression_key(source, confirmers, verdict_a)

        assert ss.should_suppress("geo_watch", key_a) is False
        ss.record("geo_watch", key_a)
        assert ss.should_suppress("geo_watch", key_a) is True

        # Different key (e.g., more confirmers) → allowed
        confirmers2 = _make_snapshot(fresh=3)
        verdict_b = apply_evidence_gate(source, confirmers2, DecisionState.FADE)
        key_b = suppression_key(source, confirmers2, verdict_b)
        assert key_b != key_a
        assert ss.should_suppress("geo_watch", key_b) is False


# ---------------------------------------------------------------------------
# CONTRACT 3: Critical bypass always emits
# ---------------------------------------------------------------------------

class TestCriticalBypassAlwaysEmits:
    """Critical events must bypass confirmer gating and always fire."""

    def test_hormuz_shipping_detected(self):
        result = check_critical_bypass(
            "Iran seizes tanker in Strait of Hormuz", ""
        )
        assert result.is_critical is True
        assert CriticalCategory.HORMUZ_SHIPPING in result.categories

    def test_us_force_posture_detected(self):
        result = check_critical_bypass(
            "Pentagon deploys carrier strike group to Persian Gulf", ""
        )
        assert result.is_critical is True
        assert CriticalCategory.US_FORCE_POSTURE in result.categories

    def test_confirmed_strike_detected(self):
        result = check_critical_bypass(
            "Iran confirms missile strike kills 3 in Syria", ""
        )
        assert result.is_critical is True
        assert CriticalCategory.CONFIRMED_STRIKE in result.categories

    def test_embassy_closure_detected(self):
        result = check_critical_bypass(
            "US embassy in Baghdad ordered to evacuate", ""
        )
        assert result.is_critical is True
        assert CriticalCategory.EMBASSY_CLOSURE in result.categories

    def test_direct_military_detected(self):
        result = check_critical_bypass(
            "Israel confirms retaliatory strike against IRGC targets", ""
        )
        assert result.is_critical is True
        assert CriticalCategory.DIRECT_MILITARY in result.categories

    def test_critical_bypass_skips_gating(self):
        """Critical event bypasses evidence gate even with 0 confirmers."""
        source = _single_source_c()
        confirmers = _make_snapshot(fresh=0)
        raw = DecisionState.ACTIONABLE_LONG
        verdict = apply_evidence_gate(
            source, confirmers, raw, is_critical_bypass=True
        )
        # Should NOT be gated — critical bypass preserves raw decision
        assert verdict.decision == DecisionState.ACTIONABLE_LONG
        assert verdict.gated is False
        assert "CRITICAL_BYPASS" in verdict.reason

    def test_non_critical_not_bypassed(self):
        result = check_critical_bypass(
            "Gold prices rise on mild inflation data", ""
        )
        assert result.is_critical is False
        assert result.categories == ()

    def test_critical_bypass_zero_confirmers(self):
        """Even with zero confirmers, critical bypass emits raw decision."""
        source = corroborate(["some-news-site"])
        snap = _make_snapshot(fresh=0)
        verdict = apply_evidence_gate(
            source, snap, DecisionState.CONDITIONAL, is_critical_bypass=True
        )
        assert verdict.decision == DecisionState.CONDITIONAL
        assert verdict.gated is False


# ---------------------------------------------------------------------------
# CONTRACT 4: No-new-info => no alert
# ---------------------------------------------------------------------------

class TestNoNewInfoNoAlert:
    """If nothing materially changed, no alert should fire."""

    def test_same_suppression_key_blocks(self):
        ss = SuppressionState()
        source = _single_source_c()
        confirmers = _make_snapshot(fresh=1)
        verdict = apply_evidence_gate(source, confirmers, DecisionState.FADE)
        key = suppression_key(source, confirmers, verdict)

        ss.record("geo_watch", key)
        # Same info again → suppressed
        assert ss.should_suppress("geo_watch", key) is True

    def test_content_dedup_same_event_blocked(self):
        dedup = ContentDeduplicator(cooldown_seconds=600.0)
        fp = event_fingerprint(canonicalize_title("Iran threatens Hormuz closure"))

        dedup.record(fp, "C", "Fade", "0")
        # Same tier, decision, bucket → suppressed
        assert dedup.should_suppress(fp, "C", "Fade", "0") is True

    def test_content_dedup_decision_escalation_allowed(self):
        """Decision escalation counts as material delta."""
        dedup = ContentDeduplicator(cooldown_seconds=600.0)
        fp = event_fingerprint(canonicalize_title("Iran threatens Hormuz closure"))

        dedup.record(fp, "C", "Fade", "0")
        # Decision escalated from Fade to Conditional → allowed
        assert dedup.should_suppress(fp, "C", "Conditional", "0") is False


# ---------------------------------------------------------------------------
# CONTRACT 5: Insufficient tape forces confidence cap
# ---------------------------------------------------------------------------

class TestInsufficientTapeConfidenceCap:
    """When fresh confirmers < 3, confidence must be capped."""

    def test_zero_confirmers_caps_confidence(self):
        source = _single_source_b()
        confirmers = _make_snapshot(fresh=0)
        raw = DecisionState.CONDITIONAL
        verdict = apply_evidence_gate(source, confirmers, raw)
        assert verdict.confidence_capped is True
        assert verdict.confidence_cap == CONFIDENCE_CAP_INSUFFICIENT
        assert verdict.decision == DecisionState.INSUFFICIENT_TAPE

    def test_two_confirmers_caps_confidence(self):
        source = _single_source_b()
        confirmers = _make_snapshot(fresh=2)
        raw = DecisionState.ACTIONABLE_LONG
        verdict = apply_evidence_gate(source, confirmers, raw)
        assert verdict.confidence_capped is True
        assert verdict.confidence_cap == CONFIDENCE_CAP_INSUFFICIENT
        assert verdict.decision == DecisionState.HEADLINE_ONLY

    def test_three_confirmers_no_cap(self):
        source = _single_source_b()
        confirmers = _make_snapshot(fresh=3)
        raw = DecisionState.CONDITIONAL
        verdict = apply_evidence_gate(source, confirmers, raw)
        assert verdict.confidence_capped is False
        assert verdict.confidence_cap is None


# ---------------------------------------------------------------------------
# Deterministic state machine thresholds
# ---------------------------------------------------------------------------

class TestDecisionStateMachine:
    """Verify the deterministic state machine transitions."""

    def test_actionable_long_threshold(self):
        assert decide_from_scores(0.55, 0.75) == DecisionState.ACTIONABLE_LONG
        # Just below threshold
        assert decide_from_scores(0.54, 0.75) != DecisionState.ACTIONABLE_LONG
        assert decide_from_scores(0.55, 0.74) != DecisionState.ACTIONABLE_LONG

    def test_conditional_threshold(self):
        assert decide_from_scores(0.45, 0.45) == DecisionState.CONDITIONAL

    def test_geo_policy_conditional(self):
        assert decide_from_scores(0.1, 0.30, geo_hit=True) == DecisionState.CONDITIONAL
        assert decide_from_scores(0.1, 0.29, geo_hit=True) != DecisionState.CONDITIONAL

    def test_neutral_threshold(self):
        assert decide_from_scores(0.5, 0.19) == DecisionState.NEUTRAL

    def test_fade_default(self):
        assert decide_from_scores(0.3, 0.25) == DecisionState.FADE
