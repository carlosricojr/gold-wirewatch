"""Confirmer fetch module with fallback providers for cross-asset confirmation.

Confirmers: DXY, US10Y (real yield preferred, with nominal proxy fallback),
Oil (WTI/Brent), USDJPY, Equities risk tone.
Each provider is abstract with graceful unavailable state so tests pass without live data.
"""
from __future__ import annotations

import csv
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

    def fresh_timestamps(self) -> list[datetime]:
        return [r.timestamp for r in self.readings if r.is_fresh and r.timestamp is not None]

    def fresh_time_spread_seconds(self) -> float | None:
        timestamps = self.fresh_timestamps()
        if len(timestamps) < 2:
            return 0.0 if timestamps else None
        return (max(timestamps) - min(timestamps)).total_seconds()

    def has_synchronized_fresh(self, min_fresh: int = 3, max_skew_seconds: int = 120) -> bool:
        if self.fresh_count < min_fresh:
            return False
        spread = self.fresh_time_spread_seconds()
        if spread is None:
            return False
        return spread <= max_skew_seconds


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
    """Fetches latest price from Yahoo Finance v8 chart API (no key needed)."""

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


class StooqProvider(ConfirmerProvider):
    """Fallback provider via Stooq CSV endpoint."""

    URL = "https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
    HEADERS = {"User-Agent": "Mozilla/5.0 gold-wirewatch/0.1"}
    TIMEOUT = 5.0

    def __init__(self, name: ConfirmerName, symbol: str, source_label: str = "") -> None:
        self.name = name
        self.symbol = symbol
        self.source_label = source_label or f"stooq:{symbol}"

    def fetch(self) -> ConfirmerReading:
        try:
            resp = httpx.get(
                self.URL.format(symbol=self.symbol),
                headers=self.HEADERS,
                timeout=self.TIMEOUT,
                follow_redirects=True,
            )
            resp.raise_for_status()
            return self.parse_response(resp.text)
        except Exception as exc:
            logger.debug("StooqProvider(%s) failed: %s", self.symbol, exc)
            return ConfirmerReading(
                name=self.name,
                status=ConfirmerStatus.UNAVAILABLE,
                source_label=self.source_label,
            )

    def parse_response(self, payload: str) -> ConfirmerReading:
        reader = csv.DictReader(payload.splitlines())
        row = next(reader)
        close = float(row["Close"])
        dt = datetime.fromisoformat(f"{row['Date']}T{row['Time']}").replace(tzinfo=UTC)
        age = (datetime.now(UTC) - dt).total_seconds()
        status = ConfirmerStatus.FRESH if age < FRESHNESS_SECONDS else ConfirmerStatus.STALE
        return ConfirmerReading(
            name=self.name,
            status=status,
            value=close,
            timestamp=dt,
            source_label=self.source_label,
        )


class FredSeriesProvider(ConfirmerProvider):
    """Fetches a FRED series via CSV endpoint (no API key)."""

    URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    TIMEOUT = 5.0

    def __init__(self, name: ConfirmerName, series_id: str, source_label: str = "") -> None:
        self.name = name
        self.series_id = series_id
        self.source_label = source_label or f"fred:{series_id}"

    def fetch(self) -> ConfirmerReading:
        try:
            resp = httpx.get(self.URL.format(series_id=self.series_id), timeout=self.TIMEOUT)
            resp.raise_for_status()
            return self.parse_response(resp.text)
        except Exception as exc:
            logger.debug("FredSeriesProvider(%s) failed: %s", self.series_id, exc)
            return ConfirmerReading(
                name=self.name,
                status=ConfirmerStatus.UNAVAILABLE,
                source_label=self.source_label,
            )

    def parse_response(self, payload: str) -> ConfirmerReading:
        rows = list(csv.DictReader(payload.splitlines()))
        for row in reversed(rows):
            value_raw = (row.get(self.series_id) or "").strip()
            if not value_raw or value_raw == ".":
                continue
            value = float(value_raw)
            ts = datetime.fromisoformat(str(row["DATE"]) + "T00:00:00+00:00")
            age = (datetime.now(UTC) - ts).total_seconds()
            status = ConfirmerStatus.FRESH if age < FRESHNESS_SECONDS else ConfirmerStatus.STALE
            return ConfirmerReading(
                name=self.name,
                status=status,
                value=value,
                timestamp=ts,
                source_label=self.source_label,
            )
        raise ValueError("No valid FRED datapoint found")


# --- Concrete provider factories for each confirmer ---

def make_dxy_provider() -> ConfirmerProvider:
    """DXY via Yahoo primary, Stooq and UUP fallback."""
    return FallbackProvider(
        [YahooFinanceProvider(ConfirmerName.DXY, "DX-Y.NYB", "yahoo:DX-Y.NYB"),
         StooqProvider(ConfirmerName.DXY, "dx.f", "stooq:dx.f"),
         YahooFinanceProvider(ConfirmerName.DXY, "UUP", "yahoo:UUP"),
         StubProvider(ConfirmerName.DXY)],
        ConfirmerName.DXY,
    )


def make_us10y_provider() -> ConfirmerProvider:
    """US 10Y real yield (FRED DFII10) with nominal proxy fallback."""
    return FallbackProvider(
        [FredSeriesProvider(ConfirmerName.US10Y, "DFII10", "fred:DFII10"),
         YahooFinanceProvider(ConfirmerName.US10Y, "^TNX", "yahoo:^TNX"),
         StubProvider(ConfirmerName.US10Y)],
        ConfirmerName.US10Y,
    )


def make_oil_provider() -> ConfirmerProvider:
    """WTI crude oil, fallback to Brent, with Stooq redundancy."""
    return FallbackProvider(
        [YahooFinanceProvider(ConfirmerName.OIL, "CL=F", "yahoo:CL=F"),
         StooqProvider(ConfirmerName.OIL, "cl.f", "stooq:cl.f"),
         YahooFinanceProvider(ConfirmerName.OIL, "BZ=F", "yahoo:BZ=F"),
         StubProvider(ConfirmerName.OIL)],
        ConfirmerName.OIL,
    )


def make_usdjpy_provider() -> ConfirmerProvider:
    """USDJPY spot with Stooq fallback."""
    return FallbackProvider(
        [YahooFinanceProvider(ConfirmerName.USDJPY, "JPY=X", "yahoo:JPY=X"),
         StooqProvider(ConfirmerName.USDJPY, "usdjpy", "stooq:usdjpy"),
         StubProvider(ConfirmerName.USDJPY)],
        ConfirmerName.USDJPY,
    )


def make_equities_provider() -> ConfirmerProvider:
    """Equities risk tone via ES futures, fallback to SPY + Stooq."""
    return FallbackProvider(
        [YahooFinanceProvider(ConfirmerName.EQUITIES, "ES=F", "yahoo:ES=F"),
         YahooFinanceProvider(ConfirmerName.EQUITIES, "SPY", "yahoo:SPY"),
         StooqProvider(ConfirmerName.EQUITIES, "spy.us", "stooq:spy.us"),
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
