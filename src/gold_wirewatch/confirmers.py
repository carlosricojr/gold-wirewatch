"""Confirmer fetch module with fallback providers for cross-asset confirmation.

Confirmers: DXY, US10Y (real yield preferred, with nominal proxy fallback),
Oil (WTI/Brent), USDJPY, Equities risk tone.
Each provider is abstract with graceful unavailable state so tests pass without live data.

Per-confirmer freshness policy: each confirmer has its own freshness window.
US10Y (often delayed-source) allows a longer window; strict real-time feeds
(OIL, USDJPY, EQUITIES, DXY) use tight windows.
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
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ConfirmerName(str, Enum):
    """Enumeration of cross-asset confirmer identifiers."""

    DXY = "DXY"
    US10Y = "US10Y"
    OIL = "OIL"
    USDJPY = "USDJPY"
    EQUITIES = "EQUITIES"


class ConfirmerStatus(str, Enum):
    """Freshness status of a confirmer reading."""

    FRESH = "fresh"          # Data fetched within freshness window
    STALE = "stale"          # Data exists but older than freshness window
    UNAVAILABLE = "unavailable"  # Could not fetch


FRESHNESS_SECONDS = 300  # 5 minutes default freshness window (strict feeds)


# ---------------------------------------------------------------------------
# Per-confirmer freshness policy
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FreshnessPolicy:
    """Per-confirmer freshness configuration.

    Attributes:
        max_age_seconds: Maximum age for a reading to be considered FRESH.
        delayed_acceptable: If True, a reading older than max_age_seconds but
            within delayed_max_age_seconds is treated as ACCEPTABLE_DELAYED
            (counts toward evidence gate fresh-enough tally).
        delayed_max_age_seconds: Upper bound for delayed-acceptable readings.
            Only meaningful when delayed_acceptable=True.
    """
    max_age_seconds: int = 300
    delayed_acceptable: bool = False
    delayed_max_age_seconds: int = 900  # 15 min default for delayed feeds


# Immutable default policies — operator can override via config.
DEFAULT_FRESHNESS_POLICIES: dict[ConfirmerName, FreshnessPolicy] = {
    ConfirmerName.DXY: FreshnessPolicy(max_age_seconds=300),
    ConfirmerName.US10Y: FreshnessPolicy(
        max_age_seconds=300,
        delayed_acceptable=True,
        delayed_max_age_seconds=900,
    ),
    ConfirmerName.OIL: FreshnessPolicy(max_age_seconds=300),
    ConfirmerName.USDJPY: FreshnessPolicy(max_age_seconds=300),
    ConfirmerName.EQUITIES: FreshnessPolicy(max_age_seconds=300),
}


def classify_freshness(
    age_seconds: float | None,
    policy: FreshnessPolicy,
) -> tuple[ConfirmerStatus, str]:
    """Classify a reading's freshness per its policy.

    Returns (status, reason) where reason explains the classification.
    """
    if age_seconds is None:
        return ConfirmerStatus.UNAVAILABLE, "no_timestamp"
    if age_seconds <= policy.max_age_seconds:
        return ConfirmerStatus.FRESH, "within_strict_window"
    if policy.delayed_acceptable and age_seconds <= policy.delayed_max_age_seconds:
        return ConfirmerStatus.FRESH, "delayed_acceptable"
    return ConfirmerStatus.STALE, f"age_{int(age_seconds)}s_exceeds_{policy.max_age_seconds}s"


@dataclass(frozen=True)
class ConfirmerReading:
    """A single data point fetched from a confirmer source."""

    name: ConfirmerName
    status: ConfirmerStatus
    value: float | None = None
    timestamp: datetime | None = None
    source_label: str = ""
    freshness_reason: str = ""  # Why this status was assigned

    @property
    def is_fresh(self) -> bool:
        """Return True if this reading has FRESH status."""
        return self.status == ConfirmerStatus.FRESH

    @property
    def is_delayed_acceptable(self) -> bool:
        """Return True if this reading was classified as delayed-acceptable."""
        return self.freshness_reason == "delayed_acceptable"

    def age_seconds(self) -> float | None:
        """Return seconds since timestamp, or None if no timestamp."""
        if self.timestamp is None:
            return None
        return (datetime.now(UTC) - self.timestamp).total_seconds()

    def summary_str(self) -> str:
        """Return a compact human-readable summary of this reading."""
        if self.status == ConfirmerStatus.UNAVAILABLE:
            return f"{self.name.value}=N/A"
        val_str = f"{self.value:.2f}" if self.value is not None else "?"
        age = self.age_seconds()
        age_str = f"{int(age)}s" if age is not None else "?"
        reason_tag = f",{self.freshness_reason}" if self.freshness_reason else ""
        return f"{self.name.value}={val_str}({age_str},{self.status.value}{reason_tag})"


@dataclass
class ConfirmerSnapshot:
    """Immutable collection of confirmer readings taken at a point in time."""

    readings: list[ConfirmerReading] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def fresh_count(self) -> int:
        """Count of readings with FRESH status (includes delayed-acceptable)."""
        return sum(1 for r in self.readings if r.is_fresh)

    @property
    def strict_fresh_count(self) -> int:
        """Count of readings that are fresh via strict (non-delayed) window only."""
        return sum(
            1 for r in self.readings
            if r.is_fresh and not r.is_delayed_acceptable
        )

    @property
    def delayed_acceptable_count(self) -> int:
        """Count of readings classified as delayed-acceptable."""
        return sum(1 for r in self.readings if r.is_delayed_acceptable)

    @property
    def available_count(self) -> int:
        """Count of readings that are not UNAVAILABLE."""
        return sum(1 for r in self.readings if r.status != ConfirmerStatus.UNAVAILABLE)

    def summary_line(self) -> str:
        """Return a one-line summary of all readings with freshness ratio."""
        parts = [r.summary_str() for r in self.readings]
        ts = self.fetched_at.strftime("%H:%M:%S")
        delayed = self.delayed_acceptable_count
        delayed_tag = f",delayed_ok={delayed}" if delayed > 0 else ""
        return f"[{ts}] {' | '.join(parts)} (fresh={self.fresh_count}/{len(self.readings)}{delayed_tag})"

    def fresh_timestamps(self) -> list[datetime]:
        """Return timestamps of fresh readings that have a non-None timestamp."""
        return [r.timestamp for r in self.readings if r.is_fresh and r.timestamp is not None]

    def fresh_time_spread_seconds(self) -> float | None:
        """Return max-min spread in seconds among fresh timestamps, or None if none exist."""
        timestamps = self.fresh_timestamps()
        if len(timestamps) < 2:
            return 0.0 if timestamps else None
        return (max(timestamps) - min(timestamps)).total_seconds()

    def has_synchronized_fresh(self, min_fresh: int = 3, max_skew_seconds: int = 120) -> bool:
        """Check that enough fresh readings exist with timestamps within the skew window.

        For synchronization, only strict-fresh readings count (delayed-acceptable
        readings are excluded from skew check since they are inherently lagged).
        If including delayed-acceptable brings the count to min_fresh but strict
        alone doesn't, the sync check passes with relaxed skew (3x).
        """
        strict_ts = [
            r.timestamp for r in self.readings
            if r.is_fresh and not r.is_delayed_acceptable and r.timestamp is not None
        ]
        delayed_ts = [
            r.timestamp for r in self.readings
            if r.is_delayed_acceptable and r.timestamp is not None
        ]

        # Best case: enough strict-fresh readings within tight skew
        if len(strict_ts) >= min_fresh:
            spread = (max(strict_ts) - min(strict_ts)).total_seconds()
            return spread <= max_skew_seconds

        # Relaxed: strict + delayed together meet threshold, use relaxed skew
        all_fresh_ts = strict_ts + delayed_ts
        if len(all_fresh_ts) >= min_fresh:
            spread = (max(all_fresh_ts) - min(all_fresh_ts)).total_seconds()
            # Delayed sources get 5x skew tolerance (e.g., 120s*5=600s ≈ 10min)
            return spread <= max_skew_seconds * 5

        return False

    def health_diagnostic(self) -> dict[str, Any]:
        """Return a structured diagnostic dict for observability."""
        diag: dict[str, Any] = {
            "fetched_at": self.fetched_at.isoformat(),
            "fresh": self.fresh_count,
            "strict_fresh": self.strict_fresh_count,
            "delayed_acceptable": self.delayed_acceptable_count,
            "available": self.available_count,
            "total": len(self.readings),
            "per_confirmer": {},
        }
        for r in self.readings:
            diag["per_confirmer"][r.name.value] = {
                "status": r.status.value,
                "source": r.source_label,
                "freshness_reason": r.freshness_reason,
                "age_s": int(r.age_seconds()) if r.age_seconds() is not None else None,
                "value": r.value,
            }
        return diag


class ConfirmerProvider(ABC):
    """Abstract base for a single confirmer data provider."""

    @abstractmethod
    def fetch(self) -> ConfirmerReading:
        """Fetch a single confirmer reading from this provider."""
        ...

    def _classify(
        self,
        name: ConfirmerName,
        value: float,
        ts: datetime,
        source_label: str,
        policy: FreshnessPolicy | None = None,
    ) -> ConfirmerReading:
        """Helper: classify a raw (value, timestamp) into a ConfirmerReading using policy."""
        age = (datetime.now(UTC) - ts).total_seconds()
        pol = policy or DEFAULT_FRESHNESS_POLICIES.get(name, FreshnessPolicy())
        status, reason = classify_freshness(age, pol)
        return ConfirmerReading(
            name=name, status=status, value=value, timestamp=ts,
            source_label=source_label, freshness_reason=reason,
        )


class StubProvider(ConfirmerProvider):
    """Always returns unavailable. Used as fallback or in tests."""

    def __init__(self, name: ConfirmerName) -> None:
        self.name = name

    def fetch(self) -> ConfirmerReading:
        """Return an UNAVAILABLE reading."""
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
        """Return a FRESH reading with the configured static value."""
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
        """Try providers in order; prefer FRESH, fall back to first STALE, then UNAVAILABLE."""
        stale_candidate: ConfirmerReading | None = None
        for p in self.providers:
            try:
                reading = p.fetch()
                if reading.status == ConfirmerStatus.FRESH:
                    return reading
                if reading.status == ConfirmerStatus.STALE and stale_candidate is None:
                    stale_candidate = reading
            except Exception:
                continue
        if stale_candidate is not None:
            return stale_candidate
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

    def __init__(
        self, name: ConfirmerName, symbol: str, source_label: str = "",
        freshness_policy: FreshnessPolicy | None = None,
    ) -> None:
        self.name = name
        self.symbol = symbol
        self.source_label = source_label or f"yahoo:{symbol}"
        self.freshness_policy = freshness_policy

    def fetch(self) -> ConfirmerReading:
        """Fetch latest price from Yahoo Finance chart API."""
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
        return self._classify(self.name, price, ts, self.source_label, self.freshness_policy)


class StooqProvider(ConfirmerProvider):
    """Fallback provider via Stooq CSV endpoint."""

    URL = "https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
    HEADERS = {"User-Agent": "Mozilla/5.0 gold-wirewatch/0.1"}
    TIMEOUT = 5.0

    def __init__(
        self, name: ConfirmerName, symbol: str, source_label: str = "",
        freshness_policy: FreshnessPolicy | None = None,
    ) -> None:
        self.name = name
        self.symbol = symbol
        self.source_label = source_label or f"stooq:{symbol}"
        self.freshness_policy = freshness_policy

    def fetch(self) -> ConfirmerReading:
        """Fetch latest price from Stooq CSV endpoint."""
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
        """Parse Stooq CSV response into a ConfirmerReading."""
        reader = csv.DictReader(payload.splitlines())
        row = next(reader)
        close = float(row["Close"])
        dt = datetime.fromisoformat(f"{row['Date']}T{row['Time']}").replace(tzinfo=UTC)
        return self._classify(self.name, close, dt, self.source_label, self.freshness_policy)


class FredSeriesProvider(ConfirmerProvider):
    """Fetches a FRED series via CSV endpoint (no API key)."""

    URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    TIMEOUT = 5.0

    def __init__(
        self, name: ConfirmerName, series_id: str, source_label: str = "",
        freshness_policy: FreshnessPolicy | None = None,
    ) -> None:
        self.name = name
        self.series_id = series_id
        self.source_label = source_label or f"fred:{series_id}"
        self.freshness_policy = freshness_policy

    def fetch(self) -> ConfirmerReading:
        """Fetch latest value from FRED CSV endpoint."""
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
        """Parse FRED CSV response into a ConfirmerReading."""
        rows = list(csv.DictReader(payload.splitlines()))
        for row in reversed(rows):
            value_raw = (row.get(self.series_id) or "").strip()
            if not value_raw or value_raw == ".":
                continue
            value = float(value_raw)
            ts = datetime.fromisoformat(str(row["DATE"]) + "T00:00:00+00:00")
            return self._classify(self.name, value, ts, self.source_label, self.freshness_policy)
        raise ValueError("No valid FRED datapoint found")


# --- SCID local provider for Sierra Chart data ---

class ScidLocalProvider(ConfirmerProvider):
    """Reads the latest bar from a Sierra Chart Intraday Data (.scid) file.

    This is useful when Sierra Chart is running locally and writing real-time
    data to SCID files. The provider reads the last bar's close and timestamp.

    SCID format: 40-byte records after a 56-byte header.
    Each record: DateTime(u64), Open(f32), High(f32), Low(f32), Close(f32),
                 NumTrades(u32), TotalVolume(u32), BidVolume(u32), AskVolume(u32).
    """

    RECORD_SIZE = 40
    HEADER_SIZE = 56
    # Sierra Chart DateTime epoch: 1899-12-30, stored as f64 days
    SC_EPOCH = datetime(1899, 12, 30, tzinfo=UTC)

    def __init__(
        self, name: ConfirmerName, scid_path: str, source_label: str = "",
        freshness_policy: FreshnessPolicy | None = None,
    ) -> None:
        self.name = name
        self.scid_path = scid_path
        self.source_label = source_label or f"scid:{scid_path}"
        self.freshness_policy = freshness_policy

    def fetch(self) -> ConfirmerReading:
        """Read last bar from SCID file."""
        import struct
        from pathlib import Path
        from datetime import timedelta

        try:
            p = Path(self.scid_path)
            if not p.exists():
                return ConfirmerReading(
                    name=self.name, status=ConfirmerStatus.UNAVAILABLE,
                    source_label=self.source_label, freshness_reason="scid_file_missing",
                )
            size = p.stat().st_size
            if size < self.HEADER_SIZE + self.RECORD_SIZE:
                return ConfirmerReading(
                    name=self.name, status=ConfirmerStatus.UNAVAILABLE,
                    source_label=self.source_label, freshness_reason="scid_file_too_small",
                )
            with open(p, "rb") as f:
                f.seek(size - self.RECORD_SIZE)
                record = f.read(self.RECORD_SIZE)
            # Parse: DateTime as u64 (SCDateTime is actually f64 days since epoch)
            dt_raw_us = struct.unpack_from("<q", record, 0)[0]
            # SCID field layout after DateTime(i64): Open(8), High(12), Low(16), Close(20)
            close = struct.unpack_from("<f", record, 20)[0]
            ts = self.SC_EPOCH + timedelta(microseconds=dt_raw_us)
            # Guard against invalid SCID datetime payloads.
            if dt_raw_us <= 0 or ts.year < 2000:
                return ConfirmerReading(
                    name=self.name,
                    status=ConfirmerStatus.UNAVAILABLE,
                    source_label=self.source_label,
                    freshness_reason="scid_invalid_timestamp",
                )
            return self._classify(self.name, float(close), ts, self.source_label, self.freshness_policy)
        except Exception as exc:
            logger.debug("ScidLocalProvider(%s) failed: %s", self.scid_path, exc)
            return ConfirmerReading(
                name=self.name, status=ConfirmerStatus.UNAVAILABLE,
                source_label=self.source_label, freshness_reason=f"scid_error:{exc}",
            )


# --- Per-confirmer metrics tracking ---

@dataclass
class ConfirmerMetrics:
    """Tracks per-confirmer availability and freshness statistics."""

    fetch_count: int = 0
    fresh_count: int = 0
    stale_count: int = 0
    unavailable_count: int = 0
    delayed_acceptable_count: int = 0
    last_status: ConfirmerStatus | None = None
    last_source: str = ""
    last_reason: str = ""

    def record(self, reading: ConfirmerReading) -> None:
        """Record a reading into metrics."""
        self.fetch_count += 1
        self.last_status = reading.status
        self.last_source = reading.source_label
        self.last_reason = reading.freshness_reason
        if reading.status == ConfirmerStatus.FRESH:
            if reading.is_delayed_acceptable:
                self.delayed_acceptable_count += 1
            self.fresh_count += 1
        elif reading.status == ConfirmerStatus.STALE:
            self.stale_count += 1
        else:
            self.unavailable_count += 1


# --- Concrete provider factories for each confirmer ---

def make_dxy_provider(scid_path: str | None = None) -> ConfirmerProvider:
    """DXY via SCID local (if configured), Yahoo primary, Stooq and UUP fallback."""
    providers: list[ConfirmerProvider] = []
    if scid_path:
        providers.append(ScidLocalProvider(ConfirmerName.DXY, scid_path, "scid:DXY"))
    providers.extend([
        YahooFinanceProvider(ConfirmerName.DXY, "DX-Y.NYB", "yahoo:DX-Y.NYB"),
        StooqProvider(ConfirmerName.DXY, "dx.f", "stooq:dx.f"),
        YahooFinanceProvider(ConfirmerName.DXY, "UUP", "yahoo:UUP"),
        StubProvider(ConfirmerName.DXY),
    ])
    return FallbackProvider(providers, ConfirmerName.DXY)


def make_us10y_provider(scid_path: str | None = None) -> ConfirmerProvider:
    """US 10Y with delayed-acceptable policy. SCID local (if configured),
    FRED DFII10, Yahoo ^TNX fallback. All use US10Y's delayed freshness policy."""
    policy = DEFAULT_FRESHNESS_POLICIES[ConfirmerName.US10Y]
    providers: list[ConfirmerProvider] = []
    if scid_path:
        providers.append(ScidLocalProvider(
            ConfirmerName.US10Y, scid_path, "scid:US10Y", freshness_policy=policy,
        ))
    providers.extend([
        FredSeriesProvider(ConfirmerName.US10Y, "DFII10", "fred:DFII10", freshness_policy=policy),
        YahooFinanceProvider(ConfirmerName.US10Y, "^TNX", "yahoo:^TNX", freshness_policy=policy),
        StubProvider(ConfirmerName.US10Y),
    ])
    return FallbackProvider(providers, ConfirmerName.US10Y)


def make_oil_provider(scid_path: str | None = None) -> ConfirmerProvider:
    """WTI crude oil, fallback to Brent, with SCID local + Stooq redundancy."""
    providers: list[ConfirmerProvider] = []
    if scid_path:
        providers.append(ScidLocalProvider(ConfirmerName.OIL, scid_path, "scid:OIL"))
    providers.extend([
        YahooFinanceProvider(ConfirmerName.OIL, "CL=F", "yahoo:CL=F"),
        StooqProvider(ConfirmerName.OIL, "cl.f", "stooq:cl.f"),
        YahooFinanceProvider(ConfirmerName.OIL, "BZ=F", "yahoo:BZ=F"),
        StubProvider(ConfirmerName.OIL),
    ])
    return FallbackProvider(providers, ConfirmerName.OIL)


def make_usdjpy_provider(scid_path: str | None = None) -> ConfirmerProvider:
    """USDJPY spot with SCID local + Stooq fallback."""
    providers: list[ConfirmerProvider] = []
    if scid_path:
        providers.append(ScidLocalProvider(ConfirmerName.USDJPY, scid_path, "scid:USDJPY"))
    providers.extend([
        YahooFinanceProvider(ConfirmerName.USDJPY, "JPY=X", "yahoo:JPY=X"),
        StooqProvider(ConfirmerName.USDJPY, "usdjpy", "stooq:usdjpy"),
        StubProvider(ConfirmerName.USDJPY),
    ])
    return FallbackProvider(providers, ConfirmerName.USDJPY)


def make_equities_provider(scid_path: str | None = None) -> ConfirmerProvider:
    """Equities risk tone via SCID local + ES futures, fallback to SPY + Stooq."""
    providers: list[ConfirmerProvider] = []
    if scid_path:
        providers.append(ScidLocalProvider(ConfirmerName.EQUITIES, scid_path, "scid:EQUITIES"))
    providers.extend([
        YahooFinanceProvider(ConfirmerName.EQUITIES, "ES=F", "yahoo:ES=F"),
        YahooFinanceProvider(ConfirmerName.EQUITIES, "SPY", "yahoo:SPY"),
        StooqProvider(ConfirmerName.EQUITIES, "spy.us", "stooq:spy.us"),
        StubProvider(ConfirmerName.EQUITIES),
    ])
    return FallbackProvider(providers, ConfirmerName.EQUITIES)


@dataclass
class ScidConfig:
    """Optional SCID local file paths for each confirmer."""
    dxy: str | None = None
    us10y: str | None = None
    oil: str | None = None
    usdjpy: str | None = None
    equities: str | None = None


def make_live_providers(scid: ScidConfig | None = None) -> dict[ConfirmerName, ConfirmerProvider]:
    """Build the full live provider chain with fallbacks and optional SCID local sources."""
    s = scid or ScidConfig()
    return {
        ConfirmerName.DXY: make_dxy_provider(s.dxy),
        ConfirmerName.US10Y: make_us10y_provider(s.us10y),
        ConfirmerName.OIL: make_oil_provider(s.oil),
        ConfirmerName.USDJPY: make_usdjpy_provider(s.usdjpy),
        ConfirmerName.EQUITIES: make_equities_provider(s.equities),
    }


class ConfirmerEngine:
    """Fetches all confirmers and produces a snapshot with per-confirmer metrics."""

    def __init__(self, providers: dict[ConfirmerName, ConfirmerProvider] | None = None) -> None:
        self.providers: dict[ConfirmerName, ConfirmerProvider] = providers or {
            name: StubProvider(name) for name in ConfirmerName
        }
        self.metrics: dict[ConfirmerName, ConfirmerMetrics] = {
            name: ConfirmerMetrics() for name in ConfirmerName
        }

    @classmethod
    def with_live_providers(cls, scid: ScidConfig | None = None) -> ConfirmerEngine:
        """Factory: build engine with live providers + fallback stubs."""
        return cls(providers=make_live_providers(scid))

    def fetch_all(self) -> ConfirmerSnapshot:
        """Fetch all confirmers and return a snapshot."""
        readings: list[ConfirmerReading] = []
        for name in ConfirmerName:
            provider = self.providers.get(name, StubProvider(name))
            try:
                reading = provider.fetch()
            except Exception:
                reading = ConfirmerReading(
                    name=name,
                    status=ConfirmerStatus.UNAVAILABLE,
                    source_label="error",
                    freshness_reason="fetch_exception",
                )
            self.metrics[name].record(reading)
            readings.append(reading)
        snap = ConfirmerSnapshot(readings=readings, fetched_at=datetime.now(UTC))
        logger.info("Confirmer snapshot: %s", snap.summary_line())
        return snap

    def health_report(self) -> dict[str, Any]:
        """Return a structured health report of all confirmer metrics."""
        return {
            name.value: {
                "fetches": m.fetch_count,
                "fresh": m.fresh_count,
                "stale": m.stale_count,
                "unavailable": m.unavailable_count,
                "delayed_acceptable": m.delayed_acceptable_count,
                "last_status": m.last_status.value if m.last_status else None,
                "last_source": m.last_source,
                "last_reason": m.last_reason,
                "availability_pct": round(
                    (m.fresh_count + m.stale_count) / max(m.fetch_count, 1) * 100, 1
                ),
            }
            for name, m in self.metrics.items()
        }
