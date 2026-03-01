"""Tests for suppression module."""
from datetime import UTC, datetime

from gold_wirewatch.confirmers import (
    ConfirmerName,
    ConfirmerReading,
    ConfirmerSnapshot,
    ConfirmerStatus,
)
from gold_wirewatch.evidence_gate import DecisionState, EvidenceVerdict
from gold_wirewatch.source_tier import CorroborationState, SourceMeta, SourceTier
from gold_wirewatch.suppression import SuppressionState, suppression_key


def _make_snapshot(fresh: int) -> ConfirmerSnapshot:
    readings = []
    for i, name in enumerate(ConfirmerName):
        if i < fresh:
            readings.append(ConfirmerReading(name, ConfirmerStatus.FRESH, 100.0, datetime.now(UTC)))
        else:
            readings.append(ConfirmerReading(name, ConfirmerStatus.UNAVAILABLE))
    return ConfirmerSnapshot(readings=readings)


def _verdict(decision: DecisionState) -> EvidenceVerdict:
    return EvidenceVerdict(decision, "test", 3, SourceTier.A, CorroborationState.SINGLE_VERIFIED, False)


def _meta() -> SourceMeta:
    return SourceMeta(SourceTier.A, CorroborationState.SINGLE_VERIFIED, 1, ("Fed",))


def test_same_state_produces_same_key():
    meta = _meta()
    snap = _make_snapshot(3)
    v = _verdict(DecisionState.ACTIONABLE_LONG)
    k1 = suppression_key(meta, snap, v)
    k2 = suppression_key(meta, snap, v)
    assert k1 == k2


def test_different_decision_different_key():
    meta = _meta()
    snap = _make_snapshot(3)
    k1 = suppression_key(meta, snap, _verdict(DecisionState.ACTIONABLE_LONG))
    k2 = suppression_key(meta, snap, _verdict(DecisionState.FADE))
    assert k1 != k2


def test_different_fresh_count_may_differ():
    meta = _meta()
    v = _verdict(DecisionState.ACTIONABLE_LONG)
    k1 = suppression_key(meta, _make_snapshot(0), v)
    k2 = suppression_key(meta, _make_snapshot(4), v)
    assert k1 != k2


def test_suppression_state_basic():
    state = SuppressionState()
    assert not state.should_suppress("group1", "key_a")
    state.record("group1", "key_a")
    assert state.should_suppress("group1", "key_a")
    assert not state.should_suppress("group1", "key_b")


def test_suppression_state_clear():
    state = SuppressionState()
    state.record("g1", "k1")
    state.clear("g1")
    assert not state.should_suppress("g1", "k1")


def test_suppression_state_clear_all():
    state = SuppressionState()
    state.record("g1", "k1")
    state.record("g2", "k2")
    state.clear()
    assert not state.should_suppress("g1", "k1")
    assert not state.should_suppress("g2", "k2")
