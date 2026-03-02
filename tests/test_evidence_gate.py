"""Tests for evidence_gate module."""
from datetime import UTC, datetime, timedelta

from gold_wirewatch.confirmers import (
    ConfirmerName,
    ConfirmerReading,
    ConfirmerSnapshot,
    ConfirmerStatus,
)
from gold_wirewatch.evidence_gate import (
    DecisionState,
    apply_evidence_gate,
    decide_from_scores,
)
from gold_wirewatch.source_tier import CorroborationState, SourceMeta, SourceTier


def _make_snapshot(fresh: int, total: int = 5) -> ConfirmerSnapshot:
    readings = []
    for i, name in enumerate(ConfirmerName):
        if i < fresh:
            readings.append(ConfirmerReading(name, ConfirmerStatus.FRESH, 100.0, datetime.now(UTC)))
        else:
            readings.append(ConfirmerReading(name, ConfirmerStatus.UNAVAILABLE))
    return ConfirmerSnapshot(readings=readings)


def _tier_a_meta() -> SourceMeta:
    return SourceMeta(SourceTier.A, CorroborationState.SINGLE_VERIFIED, 1, ("Fed",))


def _tier_c_single() -> SourceMeta:
    return SourceMeta(SourceTier.C, CorroborationState.SINGLE_UNVERIFIED, 1, ("Blog",))


def test_actionable_passes_with_enough_confirmers():
    v = apply_evidence_gate(_tier_a_meta(), _make_snapshot(4), DecisionState.ACTIONABLE_LONG)
    assert v.decision == DecisionState.ACTIONABLE_LONG
    assert not v.gated


def test_actionable_gated_with_few_confirmers():
    v = apply_evidence_gate(_tier_a_meta(), _make_snapshot(2), DecisionState.ACTIONABLE_LONG)
    assert v.decision == DecisionState.HEADLINE_ONLY
    assert v.gated


def test_conditional_gated_with_few_confirmers():
    v = apply_evidence_gate(_tier_a_meta(), _make_snapshot(1), DecisionState.CONDITIONAL)
    assert v.decision == DecisionState.INSUFFICIENT_TAPE
    assert v.gated


def test_actionable_gated_when_fresh_but_unsynchronized():
    now = datetime.now(UTC)
    snap = ConfirmerSnapshot(
        readings=[
            ConfirmerReading(ConfirmerName.DXY, ConfirmerStatus.FRESH, 1.0, now),
            ConfirmerReading(ConfirmerName.US10Y, ConfirmerStatus.FRESH, 1.0, now - timedelta(minutes=4)),
            ConfirmerReading(ConfirmerName.OIL, ConfirmerStatus.FRESH, 1.0, now - timedelta(minutes=4)),
            ConfirmerReading(ConfirmerName.USDJPY, ConfirmerStatus.UNAVAILABLE),
            ConfirmerReading(ConfirmerName.EQUITIES, ConfirmerStatus.UNAVAILABLE),
        ]
    )
    v = apply_evidence_gate(_tier_a_meta(), snap, DecisionState.ACTIONABLE_LONG)
    assert v.decision == DecisionState.INSUFFICIENT_TAPE
    assert v.gated


def test_tier_c_single_always_gated_for_actionable():
    v = apply_evidence_gate(_tier_c_single(), _make_snapshot(5), DecisionState.ACTIONABLE_LONG)
    assert v.decision == DecisionState.HEADLINE_ONLY
    assert v.gated


def test_tier_c_single_always_gated_for_conditional():
    v = apply_evidence_gate(_tier_c_single(), _make_snapshot(5), DecisionState.CONDITIONAL)
    assert v.decision == DecisionState.HEADLINE_ONLY
    assert v.gated


def test_neutral_passes_through():
    v = apply_evidence_gate(_tier_a_meta(), _make_snapshot(0), DecisionState.NEUTRAL)
    assert v.decision == DecisionState.NEUTRAL
    assert not v.gated


def test_fade_passes_through():
    v = apply_evidence_gate(_tier_a_meta(), _make_snapshot(1), DecisionState.FADE)
    assert v.decision == DecisionState.FADE
    assert not v.gated


def test_decide_from_scores_actionable():
    assert decide_from_scores(0.8, 0.8) == DecisionState.ACTIONABLE_LONG


def test_decide_from_scores_conditional():
    assert decide_from_scores(0.5, 0.5) == DecisionState.CONDITIONAL


def test_decide_from_scores_neutral():
    assert decide_from_scores(0.1, 0.1) == DecisionState.NEUTRAL


def test_decide_from_scores_fade():
    assert decide_from_scores(0.3, 0.3) == DecisionState.FADE


def test_decide_from_scores_geo_conditional():
    assert decide_from_scores(0.2, 0.35, geo_hit=True) == DecisionState.CONDITIONAL
