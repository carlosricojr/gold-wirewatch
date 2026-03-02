"""Content-level and delivery-level deduplication for the alert pipeline.

Three concerns handled here:
1. Title canonicalization — normalize headlines to detect near-duplicates
2. Event fingerprinting with cooldown TTL — suppress same-event alerts within window
3. Delivery deduplication — prevent replayed hook deliveries
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Title canonicalization
# ---------------------------------------------------------------------------

# Boilerplate prefixes/suffixes to strip (case-insensitive)
_STRIP_PREFIXES = [
    r"breaking\s*:?\s*",
    r"update\s*:?\s*",
    r"just\s+in\s*:?\s*",
    r"live\s+updates?\s*:?\s*",
    r"here'?s?\s+the\s+latest\s*:?\s*",
    r"developing\s*:?\s*",
    r"flash\s*:?\s*",
    r"alert\s*:?\s*",
    r"exclusive\s*:?\s*",
    r"urgent\s*:?\s*",
    r"watch\s*:?\s*",
    r"new\s*:?\s*",
]

_STRIP_SUFFIXES = [
    r"\s*[-–—]\s*live\s+updates?$",
    r"\s*[-–—]\s*developing(\s+story)?$",
    r"\s*\|\s*live$",
    r"\s*\.\.\.$",
    r"\s*…$",
]

_PREFIX_RE = re.compile(
    r"^(?:" + "|".join(_STRIP_PREFIXES) + r")", re.IGNORECASE
)
_SUFFIX_RE = re.compile(
    r"(?:" + "|".join(_STRIP_SUFFIXES) + r")", re.IGNORECASE
)

# Noise tokens to remove entirely
_NOISE_TOKENS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "has", "have", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "its", "it", "this", "that",
    "as", "if", "not", "no", "so", "up", "out", "about", "into", "over",
    "after", "before", "between", "under", "again", "then", "once", "here",
    "there", "when", "where", "why", "how", "all", "each", "every", "both",
    "few", "more", "most", "other", "some", "such", "than", "too", "very",
    "just", "also", "now", "says", "said", "say", "new", "us",
})


def canonicalize_title(title: str) -> str:
    """Normalize a headline for near-duplicate comparison.

    Steps: lowercase → strip boilerplate wrappers → normalize whitespace/punctuation.
    Returns the canonical form (may be empty for pure-boilerplate titles).
    """
    t = title.lower().strip()

    # Strip boilerplate prefixes (may be nested, apply twice)
    for _ in range(2):
        t = _PREFIX_RE.sub("", t).strip()

    # Strip boilerplate suffixes
    t = _SUFFIX_RE.sub("", t).strip()

    # Normalize quotes and dashes (including Unicode variants)
    t = re.sub(r"[\u2018\u2019\u0060\u00b4\u2032]", "'", t)
    t = re.sub(r'[\u201c\u201d\u00ab\u00bb\u2033]', '"', t)
    t = re.sub(r"[\u2013\u2014]", "-", t)

    # Strip remaining leading/trailing punctuation
    t = t.strip("'\"-:;,. ")

    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()

    return t


def _content_tokens(canonical_title: str) -> list[str]:
    """Extract meaningful sorted tokens from a canonical title."""
    words = re.findall(r"[a-z0-9]+(?:'[a-z]+)?", canonical_title.lower())
    meaningful = [w for w in words if w not in _NOISE_TOKENS and len(w) > 1]
    return sorted(set(meaningful))


# ---------------------------------------------------------------------------
# Event fingerprinting
# ---------------------------------------------------------------------------

def event_fingerprint(canonical_title: str) -> str:
    """Generate a source-agnostic fingerprint from a canonicalized title.

    Same event across different sources should produce the same fingerprint.
    """
    tokens = _content_tokens(canonical_title)
    raw = " ".join(tokens)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Cooldown tracker with TTL
# ---------------------------------------------------------------------------

@dataclass
class _CooldownEntry:
    fingerprint: str
    last_tier: str
    last_decision: str
    last_fresh_bucket: str
    emitted_at: float  # time.monotonic()


class ContentDeduplicator:
    """Tracks emitted event fingerprints with cooldown TTL.

    Suppresses near-duplicate titles within the cooldown window unless
    a material delta is detected (tier upgrade, decision change, confirmer shift).
    """

    def __init__(self, cooldown_seconds: float = 600.0) -> None:
        self.cooldown_seconds = cooldown_seconds
        self._entries: dict[str, _CooldownEntry] = {}

    def should_suppress(
        self,
        fingerprint: str,
        tier: str,
        decision: str,
        fresh_bucket: str,
    ) -> bool:
        """Return True if this fingerprint should be suppressed (duplicate within cooldown)."""
        self._expire()
        entry = self._entries.get(fingerprint)
        if entry is None:
            return False

        # Check if material delta → allow through
        if self._is_material_delta(entry, tier, decision, fresh_bucket):
            return False

        return True

    def record(
        self,
        fingerprint: str,
        tier: str,
        decision: str,
        fresh_bucket: str,
    ) -> None:
        """Record an emission for this fingerprint."""
        self._entries[fingerprint] = _CooldownEntry(
            fingerprint=fingerprint,
            last_tier=tier,
            last_decision=decision,
            last_fresh_bucket=fresh_bucket,
            emitted_at=time.monotonic(),
        )

    def _expire(self) -> None:
        """Remove entries older than cooldown."""
        now = time.monotonic()
        expired = [
            fp for fp, e in self._entries.items()
            if now - e.emitted_at > self.cooldown_seconds
        ]
        for fp in expired:
            del self._entries[fp]

    @staticmethod
    def _is_material_delta(
        entry: _CooldownEntry,
        new_tier: str,
        new_decision: str,
        new_fresh_bucket: str,
    ) -> bool:
        """Check if the new state represents a material change worth alerting."""
        # Tier upgrade (C→B, B→A, C→A)
        tier_order = {"A": 0, "B": 1, "C": 2}
        old_rank = tier_order.get(entry.last_tier, 99)
        new_rank = tier_order.get(new_tier, 99)
        if new_rank < old_rank:
            return True

        # Decision state escalation
        decision_order = {
            "Actionable long": 0,
            "Conditional": 1,
            "Fade": 2,
            "Neutral": 3,
            "Headline only": 4,
            "Insufficient tape": 5,
        }
        old_dec = decision_order.get(entry.last_decision, 99)
        new_dec = decision_order.get(new_decision, 99)
        if new_dec < old_dec:
            return True

        # Fresh confirmer bucket upgrade
        bucket_order = {"4+": 0, "3": 1, "1-2": 2, "0": 3}
        old_bucket = bucket_order.get(entry.last_fresh_bucket, 99)
        new_bucket = bucket_order.get(new_fresh_bucket, 99)
        if new_bucket < old_bucket:
            return True

        return False

    def clear(self) -> None:
        self._entries.clear()


# ---------------------------------------------------------------------------
# Delivery deduplication (replay guard)
# ---------------------------------------------------------------------------

class DeliveryDeduplicator:
    """Prevents identical deliveries from system replay/heartbeat behavior.

    Tracks delivery IDs (hash of fingerprint + suppression key) with TTL.
    """

    def __init__(self, ttl_seconds: float = 1800.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._seen: dict[str, float] = {}  # delivery_id -> monotonic time

    @staticmethod
    def make_delivery_id(event_fingerprint: str, suppression_key: str) -> str:
        """Create a unique delivery ID from event fingerprint + suppression key."""
        raw = f"{event_fingerprint}|{suppression_key}"
        return hashlib.sha256(raw.encode()).hexdigest()[:20]

    def is_duplicate(self, delivery_id: str) -> bool:
        """Return True if this delivery ID was already seen within TTL."""
        self._expire()
        return delivery_id in self._seen

    def record(self, delivery_id: str) -> None:
        """Record a delivery."""
        self._seen[delivery_id] = time.monotonic()

    def _expire(self) -> None:
        now = time.monotonic()
        expired = [did for did, t in self._seen.items() if now - t > self.ttl_seconds]
        for did in expired:
            del self._seen[did]

    def clear(self) -> None:
        self._seen.clear()
