"""Tests for confirmers module."""
from datetime import UTC, datetime

from gold_wirewatch.confirmers import (
    ConfirmerEngine,
    ConfirmerName,
    ConfirmerReading,
    ConfirmerSnapshot,
    ConfirmerStatus,
    FallbackProvider,
    StaticProvider,
    StubProvider,
)


def test_stub_provider_returns_unavailable():
    p = StubProvider(ConfirmerName.DXY)
    r = p.fetch()
    assert r.status == ConfirmerStatus.UNAVAILABLE
    assert r.name == ConfirmerName.DXY


def test_static_provider_returns_fresh():
    p = StaticProvider(ConfirmerName.DXY, 104.5)
    r = p.fetch()
    assert r.status == ConfirmerStatus.FRESH
    assert r.value == 104.5
    assert r.is_fresh


def test_fallback_provider_tries_in_order():
    stub = StubProvider(ConfirmerName.OIL)
    static = StaticProvider(ConfirmerName.OIL, 72.0, "backup")
    fb = FallbackProvider([stub, static], ConfirmerName.OIL)
    r = fb.fetch()
    assert r.status == ConfirmerStatus.FRESH
    assert r.value == 72.0
    assert r.source_label == "backup"


def test_fallback_all_fail():
    fb = FallbackProvider(
        [StubProvider(ConfirmerName.USDJPY), StubProvider(ConfirmerName.USDJPY)],
        ConfirmerName.USDJPY,
    )
    r = fb.fetch()
    assert r.status == ConfirmerStatus.UNAVAILABLE


def test_engine_default_all_stubs():
    engine = ConfirmerEngine()
    snap = engine.fetch_all()
    assert len(snap.readings) == len(ConfirmerName)
    assert snap.fresh_count == 0
    assert snap.available_count == 0


def test_engine_with_static_providers():
    providers = {
        ConfirmerName.DXY: StaticProvider(ConfirmerName.DXY, 104.5),
        ConfirmerName.US10Y: StaticProvider(ConfirmerName.US10Y, 4.25),
        ConfirmerName.OIL: StaticProvider(ConfirmerName.OIL, 72.0),
        ConfirmerName.USDJPY: StaticProvider(ConfirmerName.USDJPY, 148.5),
        ConfirmerName.EQUITIES: StaticProvider(ConfirmerName.EQUITIES, 5200.0),
    }
    engine = ConfirmerEngine(providers)
    snap = engine.fetch_all()
    assert snap.fresh_count == 5
    assert snap.available_count == 5


def test_snapshot_summary_line():
    snap = ConfirmerSnapshot(
        readings=[
            ConfirmerReading(ConfirmerName.DXY, ConfirmerStatus.FRESH, 104.5, datetime.now(UTC)),
            ConfirmerReading(ConfirmerName.US10Y, ConfirmerStatus.UNAVAILABLE),
        ]
    )
    line = snap.summary_line()
    assert "DXY=104.50" in line
    assert "US10Y=N/A" in line
    assert "fresh=1/2" in line
