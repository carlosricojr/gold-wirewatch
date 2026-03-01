"""Delta-only suppression keyed by (source tier, corroboration, confirmer state, decision)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .confirmers import ConfirmerSnapshot
from .evidence_gate import DecisionState, EvidenceVerdict
from .source_tier import CorroborationState, SourceMeta, SourceTier


def suppression_key(
    source_meta: SourceMeta,
    confirmers: ConfirmerSnapshot,
    verdict: EvidenceVerdict,
) -> str:
    """Create a composite key for dedup/suppression.

    Two events with the same key should be suppressed (delta-only alerting).
    Key components: tier + corroboration + fresh confirmer count bucket + decision state.
    """
    fresh_bucket = _bucket_fresh(confirmers.fresh_count)
    raw = (
        f"{source_meta.tier.value}"
        f"|{source_meta.corroboration.value}"
        f"|fresh:{fresh_bucket}"
        f"|{verdict.decision.value}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _bucket_fresh(count: int) -> str:
    """Bucket fresh confirmer counts to avoid spurious state changes."""
    if count >= 4:
        return "4+"
    if count >= 3:
        return "3"
    if count >= 1:
        return "1-2"
    return "0"


@dataclass
class SuppressionState:
    """Tracks last-emitted suppression keys to implement delta-only alerting."""

    _last_keys: dict[str, str]  # event_group -> suppression_key

    def __init__(self) -> None:
        self._last_keys = {}

    def should_suppress(self, event_group: str, new_key: str) -> bool:
        """Return True if the new key matches the last-emitted key for this group."""
        last = self._last_keys.get(event_group)
        return last == new_key

    def record(self, event_group: str, key: str) -> None:
        self._last_keys[event_group] = key

    def clear(self, event_group: str | None = None) -> None:
        if event_group is None:
            self._last_keys.clear()
        else:
            self._last_keys.pop(event_group, None)
