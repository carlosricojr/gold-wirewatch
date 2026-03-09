"""Source-tier classification and corroboration metadata for events."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SourceTier(str, Enum):
    A = "A"  # Official / primary (Fed, Treasury, BIS, OFAC)
    B = "B"  # Major wire services (Reuters, Bloomberg, AP)
    C = "C"  # Secondary / aggregator / social

    @classmethod
    def from_source_name(cls, name: str) -> SourceTier:
        low = name.lower()
        if any(k in low for k in ("federal reserve", "treasury", "bis", "ofac", "pboc", "boj")):
            return cls.A
        if any(k in low for k in ("reuters", "bloomberg", "ap news", "dow jones", "wsj", "ft ")):
            return cls.B
        return cls.C


class CorroborationState(str, Enum):
    MULTI_SOURCE = "multi_source"       # >=2 distinct sources confirm
    SINGLE_VERIFIED = "single_verified"  # 1 source, tier A or B
    SINGLE_UNVERIFIED = "single_unverified"  # 1 source, tier C only
    NONE = "none"


@dataclass(frozen=True)
class SourceMeta:
    tier: SourceTier
    corroboration: CorroborationState
    source_count: int
    source_names: tuple[str, ...]


def classify_source(source_name: str, *, config_tier: str | None = None) -> SourceTier:
    """Classify a source, preferring config-driven tier when provided.

    The config tier is used unless the heuristic already assigns a *better*
    (lower ordinal) tier — e.g. a Tier-A heuristic match is never downgraded
    to B by config.
    """
    heuristic = SourceTier.from_source_name(source_name)
    if config_tier is None:
        return heuristic
    try:
        cfg = SourceTier(config_tier)
    except ValueError:
        return heuristic
    # Return the better (lower ordinal) of heuristic vs config
    tier_order = list(SourceTier)
    return cfg if tier_order.index(cfg) < tier_order.index(heuristic) else heuristic


def corroborate(
    source_names: list[str],
    *,
    config_tiers: dict[str, str] | None = None,
) -> SourceMeta:
    """Determine corroboration state from a list of source names covering the same event.

    Args:
        source_names: Names of sources reporting the event.
        config_tiers: Optional mapping of source name → trust tier string
            (e.g. from FeedConfig.trust_tier). Overrides heuristic tier
            when it would upgrade (never downgrades).
    """
    if not source_names:
        return SourceMeta(
            tier=SourceTier.C,
            corroboration=CorroborationState.NONE,
            source_count=0,
            source_names=(),
        )
    ct = config_tiers or {}
    tiers = [classify_source(n, config_tier=ct.get(n)) for n in source_names]
    best_tier = min(tiers, key=lambda t: list(SourceTier).index(t))
    unique_sources = tuple(dict.fromkeys(source_names))  # preserve order, dedupe

    if len(unique_sources) >= 2:
        corr = CorroborationState.MULTI_SOURCE
    elif best_tier in (SourceTier.A, SourceTier.B):
        corr = CorroborationState.SINGLE_VERIFIED
    else:
        corr = CorroborationState.SINGLE_UNVERIFIED

    return SourceMeta(
        tier=best_tier,
        corroboration=corr,
        source_count=len(unique_sources),
        source_names=unique_sources,
    )
