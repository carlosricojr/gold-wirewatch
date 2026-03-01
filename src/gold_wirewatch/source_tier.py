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


def classify_source(source_name: str) -> SourceTier:
    return SourceTier.from_source_name(source_name)


def corroborate(source_names: list[str]) -> SourceMeta:
    """Determine corroboration state from a list of source names covering the same event."""
    if not source_names:
        return SourceMeta(
            tier=SourceTier.C,
            corroboration=CorroborationState.NONE,
            source_count=0,
            source_names=(),
        )
    tiers = [classify_source(n) for n in source_names]
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
