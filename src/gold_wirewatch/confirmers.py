"""Confirmer fetch module with fallback providers for cross-asset confirmation.

Confirmers: DXY, US10Y (real yield proxy), Oil (WTI/Brent), USDJPY, Equities risk tone.
Each provider is abstract with graceful unavailable state so tests pass without live data.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


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


class ConfirmerEngine:
    """Fetches all confirmers and produces a snapshot."""

    def __init__(self, providers: dict[ConfirmerName, ConfirmerProvider] | None = None) -> None:
        self.providers: dict[ConfirmerName, ConfirmerProvider] = providers or {
            name: StubProvider(name) for name in ConfirmerName
        }

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
