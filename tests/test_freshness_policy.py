"""Tests for per-confirmer freshness policy and delayed-acceptable classification.

Covers:
  - classify_freshness with strict and delayed policies
  - ConfirmerSnapshot with mixed strict/delayed readings
  - Evidence gate behavior with delayed-acceptable US10Y
  - Regression: strict feeds still enforced
  - Health diagnostics
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from gold_wirewatch.confirmers import (
    ConfirmerEngine,
    ConfirmerMetrics,
    ConfirmerName,
    ConfirmerReading,
    ConfirmerSnapshot,
    ConfirmerStatus,
    DEFAULT_FRESHNESS_POLICIES,
    FreshnessPolicy,
    ScidConfig,
    StaticProvider,
    StubProvider,
    classify_freshness,
)
from gold_wirewatch.evidence_gate import (
    CONFIDENCE_CAP_INSUFFICIENT,
    DecisionState,
    apply_evidence_gate,
)
from gold_wirewatch.source_tier import CorroborationState, SourceMeta, SourceTier


# ---------------------------------------------------------------------------
# classify_freshness unit tests
# ---------------------------------------------------------------------------

class TestClassifyFreshness:
    """Unit tests for the classify_freshness function."""

    def test_strict_within_window(self):
        status, reason = classify_freshness(200.0, FreshnessPolicy(max_age_seconds=300))
        assert status == ConfirmerStatus.FRESH
        assert reason == "within_strict_window"

    def test_strict_beyond_window(self):
        status, reason = classify_freshness(400.0, FreshnessPolicy(max_age_seconds=300))
        assert status == ConfirmerStatus.STALE
        assert "exceeds" in reason

    def test_delayed_acceptable_within_extended(self):
        policy = FreshnessPolicy(max_age_seconds=300, delayed_acceptable=True, delayed_max_age_seconds=900)
        status, reason = classify_freshness(600.0, policy)
        assert status == ConfirmerStatus.FRESH
        assert reason == "delayed_acceptable"

    def test_delayed_beyond_extended(self):
        policy = FreshnessPolicy(max_age_seconds=300, delayed_acceptable=True, delayed_max_age_seconds=900)
        status, reason = classify_freshness(1000.0, policy)
        assert status == ConfirmerStatus.STALE

    def test_delayed_within_strict(self):
        """Within strict window, classify as strict fresh even if delayed_acceptable is on."""
        policy = FreshnessPolicy(max_age_seconds=300, delayed_acceptable=True, delayed_max_age_seconds=900)
        status, reason = classify_freshness(100.0, policy)
        assert status == ConfirmerStatus.FRESH
        assert reason == "within_strict_window"

    def test_no_timestamp(self):
        status, reason = classify_freshness(None, FreshnessPolicy())
        assert status == ConfirmerStatus.UNAVAILABLE

    def test_exact_boundary_strict(self):
        status, _ = classify_freshness(300.0, FreshnessPolicy(max_age_seconds=300))
        assert status == ConfirmerStatus.FRESH

    def test_exact_boundary_delayed(self):
        policy = FreshnessPolicy(max_age_seconds=300, delayed_acceptable=True, delayed_max_age_seconds=900)
        status, _ = classify_freshness(900.0, policy)
        assert status == ConfirmerStatus.FRESH


# ---------------------------------------------------------------------------
# Default policies
# ---------------------------------------------------------------------------

class TestDefaultPolicies:
    """Verify default policies are correct."""

    def test_us10y_is_delayed_acceptable(self):
        p = DEFAULT_FRESHNESS_POLICIES[ConfirmerName.US10Y]
        assert p.delayed_acceptable is True
        assert p.delayed_max_age_seconds == 900

    def test_strict_feeds_not_delayed(self):
        for name in [ConfirmerName.DXY, ConfirmerName.OIL, ConfirmerName.USDJPY, ConfirmerName.EQUITIES]:
            p = DEFAULT_FRESHNESS_POLICIES[name]
            assert p.delayed_acceptable is False
            assert p.max_age_seconds == 300


# ---------------------------------------------------------------------------
# ConfirmerReading helpers
# ---------------------------------------------------------------------------

class TestConfirmerReadingFlags:
    def test_is_delayed_acceptable(self):
        r = ConfirmerReading(
            ConfirmerName.US10Y, ConfirmerStatus.FRESH, 4.25,
            datetime.now(UTC), "yahoo:^TNX", freshness_reason="delayed_acceptable",
        )
        assert r.is_delayed_acceptable is True
        assert r.is_fresh is True

    def test_strict_fresh_not_delayed(self):
        r = ConfirmerReading(
            ConfirmerName.DXY, ConfirmerStatus.FRESH, 104.0,
            datetime.now(UTC), "yahoo:DX-Y.NYB", freshness_reason="within_strict_window",
        )
        assert r.is_delayed_acceptable is False
        assert r.is_fresh is True


# ---------------------------------------------------------------------------
# Snapshot with delayed-acceptable
# ---------------------------------------------------------------------------

def _make_mixed_snapshot() -> ConfirmerSnapshot:
    """4 strict-fresh + 1 delayed-acceptable US10Y = 5 fresh total."""
    now = datetime.now(UTC)
    return ConfirmerSnapshot(
        readings=[
            ConfirmerReading(ConfirmerName.DXY, ConfirmerStatus.FRESH, 104.0, now, "yahoo:DXY", "within_strict_window"),
            ConfirmerReading(ConfirmerName.US10Y, ConfirmerStatus.FRESH, 4.25, now - timedelta(minutes=8), "yahoo:^TNX", "delayed_acceptable"),
            ConfirmerReading(ConfirmerName.OIL, ConfirmerStatus.FRESH, 72.0, now, "yahoo:CL=F", "within_strict_window"),
            ConfirmerReading(ConfirmerName.USDJPY, ConfirmerStatus.FRESH, 148.0, now, "yahoo:JPY=X", "within_strict_window"),
            ConfirmerReading(ConfirmerName.EQUITIES, ConfirmerStatus.FRESH, 5200.0, now, "yahoo:ES=F", "within_strict_window"),
        ],
        fetched_at=now,
    )


class TestSnapshotDelayedAcceptable:
    def test_fresh_count_includes_delayed(self):
        snap = _make_mixed_snapshot()
        assert snap.fresh_count == 5

    def test_strict_fresh_count_excludes_delayed(self):
        snap = _make_mixed_snapshot()
        assert snap.strict_fresh_count == 4

    def test_delayed_acceptable_count(self):
        snap = _make_mixed_snapshot()
        assert snap.delayed_acceptable_count == 1

    def test_summary_line_includes_delayed_tag(self):
        snap = _make_mixed_snapshot()
        line = snap.summary_line()
        assert "delayed_ok=1" in line

    def test_sync_check_relaxed_with_delayed(self):
        """Sync check should pass even when delayed-acceptable reading has large skew."""
        snap = _make_mixed_snapshot()
        assert snap.has_synchronized_fresh(min_fresh=3, max_skew_seconds=120)

    def test_health_diagnostic_has_per_confirmer(self):
        snap = _make_mixed_snapshot()
        diag = snap.health_diagnostic()
        assert "US10Y" in diag["per_confirmer"]
        assert diag["per_confirmer"]["US10Y"]["freshness_reason"] == "delayed_acceptable"
        assert diag["delayed_acceptable"] == 1


# ---------------------------------------------------------------------------
# CONTRACT: Delayed US10Y accepted while strict feeds enforced
# ---------------------------------------------------------------------------

def _tier_a_meta() -> SourceMeta:
    return SourceMeta(SourceTier.A, CorroborationState.SINGLE_VERIFIED, 1, ("Fed",))


class TestDelayedUS10YGateContract:
    """Contract: delayed-acceptable US10Y should not cause false insufficient-tape."""

    def test_mixed_snapshot_passes_evidence_gate(self):
        """4 strict + 1 delayed = 5 fresh → passes gate for Actionable."""
        snap = _make_mixed_snapshot()
        verdict = apply_evidence_gate(_tier_a_meta(), snap, DecisionState.ACTIONABLE_LONG)
        assert verdict.decision == DecisionState.ACTIONABLE_LONG
        assert not verdict.gated

    def test_delayed_only_insufficient_for_actionable(self):
        """If only delayed-acceptable readings exist (2 strict + 1 delayed = 3),
        but strict count alone < 3, the relaxed sync still passes."""
        now = datetime.now(UTC)
        snap = ConfirmerSnapshot(readings=[
            ConfirmerReading(ConfirmerName.DXY, ConfirmerStatus.FRESH, 104.0, now, "y", "within_strict_window"),
            ConfirmerReading(ConfirmerName.US10Y, ConfirmerStatus.FRESH, 4.25, now - timedelta(minutes=8), "y", "delayed_acceptable"),
            ConfirmerReading(ConfirmerName.OIL, ConfirmerStatus.FRESH, 72.0, now, "y", "within_strict_window"),
            ConfirmerReading(ConfirmerName.USDJPY, ConfirmerStatus.STALE, 148.0, now - timedelta(hours=1), "y", ""),
            ConfirmerReading(ConfirmerName.EQUITIES, ConfirmerStatus.UNAVAILABLE),
        ])
        # 3 fresh (2 strict + 1 delayed) → passes min_fresh=3
        assert snap.fresh_count == 3
        verdict = apply_evidence_gate(_tier_a_meta(), snap, DecisionState.CONDITIONAL)
        assert verdict.decision == DecisionState.CONDITIONAL
        assert not verdict.gated

    def test_strict_feeds_still_enforced_stale(self):
        """Strict feeds beyond 5min → STALE, not rescued by delayed policy."""
        now = datetime.now(UTC)
        snap = ConfirmerSnapshot(readings=[
            ConfirmerReading(ConfirmerName.DXY, ConfirmerStatus.STALE, 104.0, now - timedelta(minutes=10), "y", "age_600s_exceeds_300s"),
            ConfirmerReading(ConfirmerName.US10Y, ConfirmerStatus.FRESH, 4.25, now - timedelta(minutes=8), "y", "delayed_acceptable"),
            ConfirmerReading(ConfirmerName.OIL, ConfirmerStatus.STALE, 72.0, now - timedelta(minutes=10), "y", "age_600s_exceeds_300s"),
            ConfirmerReading(ConfirmerName.USDJPY, ConfirmerStatus.STALE, 148.0, now - timedelta(minutes=10), "y", ""),
            ConfirmerReading(ConfirmerName.EQUITIES, ConfirmerStatus.UNAVAILABLE),
        ])
        # Only US10Y is fresh (delayed_acceptable). fresh_count=1 < 3.
        assert snap.fresh_count == 1
        verdict = apply_evidence_gate(_tier_a_meta(), snap, DecisionState.ACTIONABLE_LONG)
        assert verdict.decision == DecisionState.HEADLINE_ONLY
        assert verdict.gated
        assert verdict.confidence_capped


# ---------------------------------------------------------------------------
# ConfirmerMetrics
# ---------------------------------------------------------------------------

class TestConfirmerMetrics:
    def test_record_fresh(self):
        m = ConfirmerMetrics()
        r = ConfirmerReading(ConfirmerName.DXY, ConfirmerStatus.FRESH, 104.0, datetime.now(UTC), "y", "within_strict_window")
        m.record(r)
        assert m.fetch_count == 1
        assert m.fresh_count == 1

    def test_record_delayed_acceptable(self):
        m = ConfirmerMetrics()
        r = ConfirmerReading(ConfirmerName.US10Y, ConfirmerStatus.FRESH, 4.25, datetime.now(UTC), "y", "delayed_acceptable")
        m.record(r)
        assert m.delayed_acceptable_count == 1
        assert m.fresh_count == 1

    def test_record_stale(self):
        m = ConfirmerMetrics()
        r = ConfirmerReading(ConfirmerName.OIL, ConfirmerStatus.STALE, 72.0, datetime.now(UTC), "y", "")
        m.record(r)
        assert m.stale_count == 1


# ---------------------------------------------------------------------------
# Engine health report
# ---------------------------------------------------------------------------

class TestEngineHealthReport:
    def test_health_report_structure(self):
        engine = ConfirmerEngine()
        engine.fetch_all()  # All stubs → unavailable
        report = engine.health_report()
        assert set(report.keys()) == {n.value for n in ConfirmerName}
        for name, data in report.items():
            assert "fetches" in data
            assert "availability_pct" in data


# ---------------------------------------------------------------------------
# Regression: previous gating behavior preserved
# ---------------------------------------------------------------------------

class TestGatingRegression:
    """Ensure all previous evidence-gate behaviors still hold."""

    def test_zero_confirmers_still_gates_actionable(self):
        snap = ConfirmerSnapshot(readings=[
            ConfirmerReading(n, ConfirmerStatus.UNAVAILABLE) for n in ConfirmerName
        ])
        verdict = apply_evidence_gate(_tier_a_meta(), snap, DecisionState.ACTIONABLE_LONG)
        assert verdict.decision == DecisionState.HEADLINE_ONLY
        assert verdict.gated

    def test_zero_confirmers_still_gates_conditional(self):
        snap = ConfirmerSnapshot(readings=[
            ConfirmerReading(n, ConfirmerStatus.UNAVAILABLE) for n in ConfirmerName
        ])
        verdict = apply_evidence_gate(_tier_a_meta(), snap, DecisionState.CONDITIONAL)
        assert verdict.decision == DecisionState.INSUFFICIENT_TAPE
        assert verdict.gated

    def test_fade_not_gated(self):
        snap = ConfirmerSnapshot(readings=[
            ConfirmerReading(n, ConfirmerStatus.UNAVAILABLE) for n in ConfirmerName
        ])
        verdict = apply_evidence_gate(_tier_a_meta(), snap, DecisionState.FADE)
        assert verdict.decision == DecisionState.FADE
        assert not verdict.gated

    def test_critical_bypass_still_skips_gate(self):
        snap = ConfirmerSnapshot(readings=[
            ConfirmerReading(n, ConfirmerStatus.UNAVAILABLE) for n in ConfirmerName
        ])
        verdict = apply_evidence_gate(
            _tier_a_meta(), snap, DecisionState.ACTIONABLE_LONG, is_critical_bypass=True,
        )
        assert verdict.decision == DecisionState.ACTIONABLE_LONG
        assert not verdict.gated

    def test_confidence_cap_at_two_fresh(self):
        now = datetime.now(UTC)
        snap = ConfirmerSnapshot(readings=[
            ConfirmerReading(ConfirmerName.DXY, ConfirmerStatus.FRESH, 104.0, now, "y", "within_strict_window"),
            ConfirmerReading(ConfirmerName.US10Y, ConfirmerStatus.FRESH, 4.25, now, "y", "within_strict_window"),
            ConfirmerReading(ConfirmerName.OIL, ConfirmerStatus.UNAVAILABLE),
            ConfirmerReading(ConfirmerName.USDJPY, ConfirmerStatus.UNAVAILABLE),
            ConfirmerReading(ConfirmerName.EQUITIES, ConfirmerStatus.UNAVAILABLE),
        ])
        verdict = apply_evidence_gate(_tier_a_meta(), snap, DecisionState.ACTIONABLE_LONG)
        assert verdict.confidence_capped
        assert verdict.confidence_cap == CONFIDENCE_CAP_INSUFFICIENT


# ---------------------------------------------------------------------------
# ScidConfig defaults
# ---------------------------------------------------------------------------

class TestScidConfig:
    def test_default_all_none(self):
        s = ScidConfig()
        assert s.dxy is None
        assert s.us10y is None
