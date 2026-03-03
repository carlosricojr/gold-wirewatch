"""Hard evidence gate: determines decision state from source tier + confirmers.

State machine thresholds (deterministic, no LLM):
  - ACTIONABLE_LONG: severity >= 0.75 AND relevance >= 0.55 AND fresh confirmers >= 3
  - CONDITIONAL:     severity >= 0.45 AND relevance >= 0.45 (or geo/policy hit with severity >= 0.3)
  - FADE:            severity in [0.2, 0.45) or failed confirmer gate
  - NEUTRAL:         severity < 0.2
  - HEADLINE_ONLY:   Actionable demoted due to insufficient confirmers or single-source tier C
  - INSUFFICIENT_TAPE: Conditional demoted due to insufficient/desynchronized confirmers

Confidence cap: when fresh confirmers < MIN_FRESH_CONFIRMERS, confidence is capped
at CONFIDENCE_CAP_INSUFFICIENT and state forced to headline_only_insufficient_tape.

Per-confirmer freshness: the fresh_count on ConfirmerSnapshot includes readings
classified as delayed-acceptable (e.g., US10Y within its extended window). This
prevents false "insufficient tape" gating when only delayed-acceptable confirmers
are beyond the strict 5-minute window but within their configured tolerance.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .confirmers import ConfirmerSnapshot
from .source_tier import CorroborationState, SourceMeta, SourceTier


class DecisionState(str, Enum):
    """Possible decision states from the evidence gate."""

    ACTIONABLE_LONG = "Actionable long"
    CONDITIONAL = "Conditional"
    FADE = "Fade"
    NEUTRAL = "Neutral"
    HEADLINE_ONLY = "Headline only"
    INSUFFICIENT_TAPE = "Insufficient tape"


# --- Configurable thresholds (operator-tunable) ---
MIN_FRESH_CONFIRMERS = 3  # Hard gate: need >= 3 fresh confirmers for actionable
MAX_FRESH_SKEW_SECONDS = 120  # Hard gate: confirmers must be from a coherent reaction window
CONFIDENCE_CAP_INSUFFICIENT = 0.40  # Max confidence when fresh confirmers < MIN_FRESH_CONFIRMERS

# Score thresholds for decide_from_scores (deterministic state machine)
ACTIONABLE_SEVERITY_THRESHOLD = 0.75
ACTIONABLE_RELEVANCE_THRESHOLD = 0.55
CONDITIONAL_SEVERITY_THRESHOLD = 0.45
CONDITIONAL_RELEVANCE_THRESHOLD = 0.45
GEO_POLICY_SEVERITY_THRESHOLD = 0.30
NEUTRAL_SEVERITY_CEILING = 0.20


@dataclass(frozen=True)
class EvidenceVerdict:
    """Result of applying the evidence gate to a raw decision."""

    decision: DecisionState
    reason: str
    confirmer_fresh_count: int
    source_tier: SourceTier
    corroboration: CorroborationState
    gated: bool  # True if evidence gate forced a downgrade
    confidence_capped: bool = False  # True if confidence was capped due to insufficient tape
    confidence_cap: float | None = None  # The cap value if applied


def apply_evidence_gate(
    source_meta: SourceMeta,
    confirmers: ConfirmerSnapshot,
    raw_decision: DecisionState,
    *,
    is_critical_bypass: bool = False,
) -> EvidenceVerdict:
    """Apply hard evidence gate. If <3 confirmers fresh, cannot be Actionable long.

    If is_critical_bypass=True, the event is a critical bypass and skips gating
    (but still records confirmer state for transparency).
    """
    fresh = confirmers.fresh_count
    gated = False
    confidence_capped = False
    confidence_cap: float | None = None
    decision = raw_decision

    # Critical bypass: skip all gating, preserve raw decision
    if is_critical_bypass:
        reason = f"CRITICAL_BYPASS: fresh={fresh}, raw_decision={raw_decision.value}"
        return EvidenceVerdict(
            decision=raw_decision,
            reason=reason,
            confirmer_fresh_count=fresh,
            source_tier=source_meta.tier,
            corroboration=source_meta.corroboration,
            gated=False,
            confidence_capped=False,
            confidence_cap=None,
        )

    # Hard gate: insufficient confirmers → force state + confidence cap
    if fresh < MIN_FRESH_CONFIRMERS:
        confidence_capped = True
        confidence_cap = CONFIDENCE_CAP_INSUFFICIENT
        if decision == DecisionState.ACTIONABLE_LONG:
            decision = DecisionState.HEADLINE_ONLY
            gated = True
        elif decision == DecisionState.CONDITIONAL:
            decision = DecisionState.INSUFFICIENT_TAPE
            gated = True

    # Hard gate: fresh confirmers exist but are not synchronized in time window
    if decision in (DecisionState.ACTIONABLE_LONG, DecisionState.CONDITIONAL) and not confirmers.has_synchronized_fresh(
        min_fresh=MIN_FRESH_CONFIRMERS,
        max_skew_seconds=MAX_FRESH_SKEW_SECONDS,
    ):
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
    skew = confirmers.fresh_time_spread_seconds()
    skew_text = "na" if skew is None else f"{int(skew)}s"

    if gated:
        reason_parts.append(f"gated: fresh={fresh}/{MIN_FRESH_CONFIRMERS}req,skew={skew_text}")
        if confidence_capped:
            reason_parts.append(f"confidence_cap={confidence_cap}")
        if source_meta.corroboration == CorroborationState.SINGLE_UNVERIFIED:
            reason_parts.append(f"tier={source_meta.tier.value}/single-unverified")
    else:
        reason_parts.append(f"passed: fresh={fresh},skew={skew_text}")

    return EvidenceVerdict(
        decision=decision,
        reason=", ".join(reason_parts),
        confirmer_fresh_count=fresh,
        source_tier=source_meta.tier,
        corroboration=source_meta.corroboration,
        gated=gated,
        confidence_capped=confidence_capped,
        confidence_cap=confidence_cap,
    )


def decide_from_scores(
    relevance: float,
    severity: float,
    geo_hit: bool = False,
    policy_hit: bool = False,
) -> DecisionState:
    """Map raw scores to a preliminary decision state before evidence gating.

    Deterministic state machine with explicit thresholds:
      ACTIONABLE_LONG: severity >= 0.75 AND relevance >= 0.55
      CONDITIONAL:     severity >= 0.45 AND relevance >= 0.45
                       OR severity >= 0.30 with geo/policy hit
      NEUTRAL:         severity < 0.20
      FADE:            everything else
    """
    if severity >= ACTIONABLE_SEVERITY_THRESHOLD and relevance >= ACTIONABLE_RELEVANCE_THRESHOLD:
        return DecisionState.ACTIONABLE_LONG
    if severity >= CONDITIONAL_SEVERITY_THRESHOLD and relevance >= CONDITIONAL_RELEVANCE_THRESHOLD:
        return DecisionState.CONDITIONAL
    if severity >= GEO_POLICY_SEVERITY_THRESHOLD and (geo_hit or policy_hit):
        return DecisionState.CONDITIONAL
    if severity < NEUTRAL_SEVERITY_CEILING:
        return DecisionState.NEUTRAL
    return DecisionState.FADE
