"""Hard evidence gate: determines decision state from source tier + confirmers."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .confirmers import ConfirmerSnapshot
from .source_tier import CorroborationState, SourceMeta, SourceTier


class DecisionState(str, Enum):
    ACTIONABLE_LONG = "Actionable long"
    CONDITIONAL = "Conditional"
    FADE = "Fade"
    NEUTRAL = "Neutral"
    HEADLINE_ONLY = "Headline only"
    INSUFFICIENT_TAPE = "Insufficient tape"


MIN_FRESH_CONFIRMERS = 3  # Hard gate: need >= 3 fresh confirmers for actionable


@dataclass(frozen=True)
class EvidenceVerdict:
    decision: DecisionState
    reason: str
    confirmer_fresh_count: int
    source_tier: SourceTier
    corroboration: CorroborationState
    gated: bool  # True if evidence gate forced a downgrade


def apply_evidence_gate(
    source_meta: SourceMeta,
    confirmers: ConfirmerSnapshot,
    raw_decision: DecisionState,
) -> EvidenceVerdict:
    """Apply hard evidence gate. If <3 confirmers fresh, cannot be Actionable long."""
    fresh = confirmers.fresh_count
    gated = False
    decision = raw_decision

    # Hard gate: insufficient confirmers
    if fresh < MIN_FRESH_CONFIRMERS:
        if decision == DecisionState.ACTIONABLE_LONG:
            decision = DecisionState.HEADLINE_ONLY
            gated = True
        elif decision == DecisionState.CONDITIONAL:
            decision = DecisionState.INSUFFICIENT_TAPE
            gated = True

    # Tier C single-source cannot be actionable without corroboration + confirmers
    if (
        source_meta.tier == SourceTier.C
        and source_meta.corroboration == CorroborationState.SINGLE_UNVERIFIED
    ):
        if decision in (DecisionState.ACTIONABLE_LONG, DecisionState.CONDITIONAL):
            decision = DecisionState.HEADLINE_ONLY
            gated = True

    reason_parts = []
    if gated:
        reason_parts.append(f"gated: fresh={fresh}/{MIN_FRESH_CONFIRMERS}req")
        if source_meta.corroboration == CorroborationState.SINGLE_UNVERIFIED:
            reason_parts.append(f"tier={source_meta.tier.value}/single-unverified")
    else:
        reason_parts.append(f"passed: fresh={fresh}")

    return EvidenceVerdict(
        decision=decision,
        reason=", ".join(reason_parts),
        confirmer_fresh_count=fresh,
        source_tier=source_meta.tier,
        corroboration=source_meta.corroboration,
        gated=gated,
    )


def decide_from_scores(
    relevance: float,
    severity: float,
    geo_hit: bool = False,
    policy_hit: bool = False,
) -> DecisionState:
    """Map raw scores to a preliminary decision state before evidence gating."""
    if severity >= 0.75 and relevance >= 0.55:
        return DecisionState.ACTIONABLE_LONG
    if severity >= 0.45 and relevance >= 0.45:
        return DecisionState.CONDITIONAL
    if severity >= 0.3 and (geo_hit or policy_hit):
        return DecisionState.CONDITIONAL
    if severity < 0.2:
        return DecisionState.NEUTRAL
    return DecisionState.FADE
