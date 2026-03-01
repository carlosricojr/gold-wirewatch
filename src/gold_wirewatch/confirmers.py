"""Confirmer fetch module with fallback providers for cross-asset confirmation.

Confirmers: DXY, US10Y (real yield proxy), Oil (WTI/Brent), USDJPY, Equities risk tone.
Each provider is abstract with graceful unavailable state so tests pass without live data.

Live providers use Yahoo Finance CSV endpoint (no API key needed, stable).
"""
from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

import httpx

logger = logging.getLogger(__name__)


class ConfirmerName(str, Enum):
    DXY = "DXY"
    US10Y = "US10Y"
    OIL = "OIL"
    USDJPY = "USDJPY"
    EQUITIES = "EQUITIES"


class ConfirmerStatus(str, Enum):
    FRESH = "fresh"          # Data fetched within freshness window
    STALE = "stale"          # Data exists but older than freshness window
    UNAVAILABLE = "unavailable"  # Could not fetch


FRESHNESS_SECONDS = 300  # 5 minutes default freshness window


@dataclass(frozen=True)
class ConfirmerReading:
    name: ConfirmerName
    status: ConfirmerStatus
    value: float | None = None
    timestamp: datetime | None = None
    source_label: str = ""

    @property
    def is_fresh(self) -> bool:
        return self.status == ConfirmerStatus.FRESH

    def age_seconds(self) -> float | None:
        if self.timestamp is None:
            return None
        return (datetime.now(UTC) - self.timestamp).total_seconds()

    def summary_str(self) -> str:
        if self.status == ConfirmerStatus.UNAVAILABLE:
            return f"{self.name.value}=N/A"
        val_str = f"{self.value:.2f}" if self.value is not None else "?"
        age = self.age_seconds()
        age_str = f"{int(age)}s" if age is not None else "?"
        return f"{self.name.value}={val_str}({age_str},{self.status.value})"


@dataclass
class ConfirmerSnapshot:
    readings: list[ConfirmerReading] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def fresh_count(self) -> int:
        return sum(1 for r in self.readings if r.is_fresh)

    @property
    def available_count(self) -> int:
        return sum(1 for r in self.readings if r.status != ConfirmerStatus.UNAVAILABLE)

    def summary_line(self) -> str:
        parts = [r.summary_str() for r in self.readings]
        ts = self.fetched_at.strftime("%H:%M:%S")
        return f"[{ts}] {' | '.join(parts)} (fresh={self.fresh_count}/{len(self.readings)})"


class ConfirmerProvider(ABC):
    """Abstract base for a single confirmer data provider."""

    @abstractmethod
    def fetch(self) -> ConfirmerReading: ...


class StubProvider(ConfirmerProvider):
    """Always returns unavailable. Used as fallback or in tests."""

    def __init__(self, name: ConfirmerName) -> None:
        self.name = name

    def fetch(self) -> ConfirmerReading:
        return ConfirmerReading(
            name=self.name,
            status=ConfirmerStatus.UNAVAILABLE,
            source_label="stub",
        )


class StaticProvider(ConfirmerProvider):
    """Returns a fixed value. Useful for testing."""

    def __init__(self, name: ConfirmerName, value: float, source_label: str = "static") -> None:
        self.name = name
        self.value = value
        self.source_label = source_label

    def fetch(self) -> ConfirmerReading:
        return ConfirmerReading(
            name=self.name,
            status=ConfirmerStatus.FRESH,
            value=self.value,
            timestamp=datetime.now(UTC),
            source_label=self.source_label,
        )


class FallbackProvider(ConfirmerProvider):
    """Tries a list of providers in order, returns first non-unavailable result."""

    def __init__(self, providers: list[ConfirmerProvider], name: ConfirmerName) -> None:
        self.providers = providers
        self.name = name

    def fetch(self) -> ConfirmerReading:
        for p in self.providers:
            try:
                reading = p.fetch()
                if reading.status != ConfirmerStatus.UNAVAILABLE:
                    return reading
            except Exception:
                continue
        return ConfirmerReading(
            name=self.name,
            status=ConfirmerStatus.UNAVAILABLE,
            source_label="all-fallbacks-failed",
        )


class YahooFinanceProvider(ConfirmerProvider):
    """Fetches latest price from Yahoo Finance v8 chart API (no key needed).

    Uses the chart endpoint which returns JSON with current market price.
    """

    CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    HEADERS = {"User-Agent": "Mozilla/5.0 gold-wirewatch/0.1"}
    TIMEOUT = 5.0

    def __init__(self, name: ConfirmerName, symbol: str, source_label: str = "") -> None:
        self.name = name
        self.symbol = symbol
        self.source_label = source_label or f"yahoo:{symbol}"

    def fetch(self) -> ConfirmerReading:
        try:
            resp = httpx.get(
                self.CHART_URL.format(symbol=self.symbol),
                headers=self.HEADERS,
                timeout=self.TIMEOUT,
                follow_redirects=True,
            )
            resp.raise_for_status()
            return self.parse_response(resp.json())
        except Exception as exc:
            logger.debug("YahooFinanceProvider(%s) failed: %s", self.symbol, exc)
            return ConfirmerReading(
                name=self.name,
                status=ConfirmerStatus.UNAVAILABLE,
                source_label=self.source_label,
            )

    def parse_response(self, data: dict) -> ConfirmerReading:  # type: ignore[type-arg]
        """Parse Yahoo Finance chart JSON response. Raises on malformed data."""
        result = data["chart"]["result"][0]
        meta = result["meta"]
        price = float(meta["regularMarketPrice"])
        ts = datetime.fromtimestamp(int(meta["regularMarketTime"]), tz=UTC)

        age = (datetime.now(UTC) - ts).total_seconds()
        status = ConfirmerStatus.FRESH if age < FRESHNESS_SECONDS else ConfirmerStatus.STALE

        return ConfirmerReading(
            name=self.name,
            status=status,
            value=price,
            timestamp=ts,
            source_label=self.source_label,
        )


# --- Concrete provider factories for each confirmer ---

def make_dxy_provider() -> ConfirmerProvider:
    """DXY via UUP ETF (tracks DXY)."""
    return FallbackProvider(
        [YahooFinanceProvider(ConfirmerName.DXY, "DX-Y.NYB", "yahoo:DX-Y.NYB"),
         YahooFinanceProvider(ConfirmerName.DXY, "UUP", "yahoo:UUP"),
         StubProvider(ConfirmerName.DXY)],
        ConfirmerName.DXY,
    )


def make_us10y_provider() -> ConfirmerProvider:
    """US 10Y yield as real-yield proxy."""
    return FallbackProvider(
        [YahooFinanceProvider(ConfirmerName.US10Y, "^TNX", "yahoo:^TNX"),
         StubProvider(ConfirmerName.US10Y)],
        ConfirmerName.US10Y,
    )


def make_oil_provider() -> ConfirmerProvider:
    """WTI crude oil, fallback to Brent."""
    return FallbackProvider(
        [YahooFinanceProvider(ConfirmerName.OIL, "CL=F", "yahoo:CL=F"),
         YahooFinanceProvider(ConfirmerName.OIL, "BZ=F", "yahoo:BZ=F"),
         StubProvider(ConfirmerName.OIL)],
        ConfirmerName.OIL,
    )


def make_usdjpy_provider() -> ConfirmerProvider:
    """USDJPY spot."""
    return FallbackProvider(
        [YahooFinanceProvider(ConfirmerName.USDJPY, "JPY=X", "yahoo:JPY=X"),
         StubProvider(ConfirmerName.USDJPY)],
        ConfirmerName.USDJPY,
    )


def make_equities_provider() -> ConfirmerProvider:
    """Equities risk tone via ES futures, fallback to SPY."""
    return FallbackProvider(
        [YahooFinanceProvider(ConfirmerName.EQUITIES, "ES=F", "yahoo:ES=F"),
         YahooFinanceProvider(ConfirmerName.EQUITIES, "SPY", "yahoo:SPY"),
         StubProvider(ConfirmerName.EQUITIES)],
        ConfirmerName.EQUITIES,
    )


def make_live_providers() -> dict[ConfirmerName, ConfirmerProvider]:
    """Build the full live provider chain with fallbacks."""
    return {
        ConfirmerName.DXY: make_dxy_provider(),
        ConfirmerName.US10Y: make_us10y_provider(),
        ConfirmerName.OIL: make_oil_provider(),
        ConfirmerName.USDJPY: make_usdjpy_provider(),
        ConfirmerName.EQUITIES: make_equities_provider(),
    }


class ConfirmerEngine:
    """Fetches all confirmers and produces a snapshot."""

    def __init__(self, providers: dict[ConfirmerName, ConfirmerProvider] | None = None) -> None:
        self.providers: dict[ConfirmerName, ConfirmerProvider] = providers or {
            name: StubProvider(name) for name in ConfirmerName
        }

    @classmethod
    def with_live_providers(cls) -> ConfirmerEngine:
        """Factory: build engine with live Yahoo Finance providers + fallback stubs."""
        return cls(providers=make_live_providers())

    def fetch_all(self) -> ConfirmerSnapshot:
        readings: list[ConfirmerReading] = []
        for name in ConfirmerName:
            provider = self.providers.get(name, StubProvider(name))
            try:
                readings.append(provider.fetch())
            except Exception:
                readings.append(ConfirmerReading(
                    name=name,
                    status=ConfirmerStatus.UNAVAILABLE,
                    source_label="error",
                ))
        return ConfirmerSnapshot(readings=readings, fetched_at=datetime.now(UTC))
