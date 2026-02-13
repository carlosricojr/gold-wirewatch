from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FeedItem:
    source: str
    title: str
    summary: str
    url: str
    guid: str
    published_at: datetime | None
    fetched_at: datetime


@dataclass(frozen=True)
class ScoreResult:
    relevance_score: float
    severity_score: float
    reasons: list[str]


@dataclass(frozen=True)
class MarketMoveEvent:
    symbol: str
    price_change: float
    window_seconds: int
    now_price: float | None = None
    previous_price: float | None = None
    metadata: dict[str, str] | None = None
