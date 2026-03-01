"""Tests for live confirmer providers — parser/unit tests with fixture payloads, no network."""
from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest

from gold_wirewatch.confirmers import (
    ConfirmerEngine,
    ConfirmerName,
    ConfirmerReading,
    ConfirmerStatus,
    FallbackProvider,
    StubProvider,
    YahooFinanceProvider,
    make_dxy_provider,
    make_equities_provider,
    make_live_providers,
    make_oil_provider,
    make_us10y_provider,
    make_usdjpy_provider,
    FRESHNESS_SECONDS,
)

# ---------------------------------------------------------------------------
# Fixture payloads
# ---------------------------------------------------------------------------

YAHOO_CHART_RESPONSE_FRESH = {
    "chart": {
        "result": [
            {
                "meta": {
                    "currency": "USD",
                    "symbol": "DX-Y.NYB",
                    "regularMarketPrice": 104.32,
                    "regularMarketTime": int(datetime.now(UTC).timestamp()) - 30,
                },
                "timestamp": [],
                "indicators": {"quote": [{}]},
            }
        ],
        "error": None,
    }
}

YAHOO_CHART_RESPONSE_STALE = {
    "chart": {
        "result": [
            {
                "meta": {
                    "currency": "USD",
                    "symbol": "^TNX",
                    "regularMarketPrice": 4.25,
                    "regularMarketTime": int(datetime.now(UTC).timestamp()) - FRESHNESS_SECONDS - 100,
                },
                "timestamp": [],
                "indicators": {"quote": [{}]},
            }
        ],
        "error": None,
    }
}

YAHOO_CHART_MALFORMED_NO_RESULT = {"chart": {"result": [], "error": None}}
YAHOO_CHART_MALFORMED_NO_META = {"chart": {"result": [{"foo": "bar"}], "error": None}}
YAHOO_CHART_MALFORMED_MISSING_PRICE = {
    "chart": {"result": [{"meta": {"symbol": "X", "regularMarketTime": 1000000}}]}
}


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestYahooFinanceProviderParser:
    """Tests for parse_response with fixture payloads — no network needed."""

    def test_parse_fresh_response(self):
        p = YahooFinanceProvider(ConfirmerName.DXY, "DX-Y.NYB")
        reading = p.parse_response(YAHOO_CHART_RESPONSE_FRESH)
        assert reading.name == ConfirmerName.DXY
        assert reading.status == ConfirmerStatus.FRESH
        assert reading.value == pytest.approx(104.32)
        assert reading.timestamp is not None
        assert reading.source_label == "yahoo:DX-Y.NYB"

    def test_parse_stale_response(self):
        p = YahooFinanceProvider(ConfirmerName.US10Y, "^TNX")
        reading = p.parse_response(YAHOO_CHART_RESPONSE_STALE)
        assert reading.status == ConfirmerStatus.STALE
        assert reading.value == pytest.approx(4.25)

    def test_parse_malformed_no_result_raises(self):
        p = YahooFinanceProvider(ConfirmerName.OIL, "CL=F")
        with pytest.raises((IndexError, KeyError)):
            p.parse_response(YAHOO_CHART_MALFORMED_NO_RESULT)

    def test_parse_malformed_no_meta_raises(self):
        p = YahooFinanceProvider(ConfirmerName.OIL, "CL=F")
        with pytest.raises(KeyError):
            p.parse_response(YAHOO_CHART_MALFORMED_NO_META)

    def test_parse_missing_price_raises(self):
        p = YahooFinanceProvider(ConfirmerName.USDJPY, "JPY=X")
        with pytest.raises(KeyError):
            p.parse_response(YAHOO_CHART_MALFORMED_MISSING_PRICE)


# ---------------------------------------------------------------------------
# Failure path tests
# ---------------------------------------------------------------------------

class TestYahooFinanceProviderFailurePaths:
    """Tests for network failures — mocked, no live network."""

    def test_timeout_returns_unavailable(self):
        p = YahooFinanceProvider(ConfirmerName.DXY, "DX-Y.NYB")
        with patch.object(httpx, "get", side_effect=httpx.TimeoutException("timeout")):
            reading = p.fetch()
        assert reading.status == ConfirmerStatus.UNAVAILABLE
        assert reading.name == ConfirmerName.DXY

    def test_http_error_returns_unavailable(self):
        p = YahooFinanceProvider(ConfirmerName.US10Y, "^TNX")
        mock_resp = httpx.Response(status_code=429, request=httpx.Request("GET", "http://x"))
        with patch.object(httpx, "get", return_value=mock_resp):
            reading = p.fetch()
        assert reading.status == ConfirmerStatus.UNAVAILABLE

    def test_connection_error_returns_unavailable(self):
        p = YahooFinanceProvider(ConfirmerName.OIL, "CL=F")
        with patch.object(httpx, "get", side_effect=httpx.ConnectError("conn refused")):
            reading = p.fetch()
        assert reading.status == ConfirmerStatus.UNAVAILABLE

    def test_malformed_json_returns_unavailable(self):
        p = YahooFinanceProvider(ConfirmerName.EQUITIES, "ES=F")
        mock_resp = httpx.Response(200, json={"chart": {"result": []}},
                                   request=httpx.Request("GET", "http://x"))
        with patch.object(httpx, "get", return_value=mock_resp):
            reading = p.fetch()
        assert reading.status == ConfirmerStatus.UNAVAILABLE

    def test_successful_fetch_with_mock(self):
        p = YahooFinanceProvider(ConfirmerName.DXY, "DX-Y.NYB")
        mock_resp = httpx.Response(200, json=YAHOO_CHART_RESPONSE_FRESH,
                                   request=httpx.Request("GET", "http://x"))
        with patch.object(httpx, "get", return_value=mock_resp):
            reading = p.fetch()
        assert reading.status == ConfirmerStatus.FRESH
        assert reading.value == pytest.approx(104.32)


# ---------------------------------------------------------------------------
# Fallback chain tests
# ---------------------------------------------------------------------------

class TestFallbackChainBehavior:
    """Tests for the fallback provider chain with partial availability."""

    def test_first_provider_succeeds_skips_rest(self):
        calls = []

        class TrackingProvider(YahooFinanceProvider):
            def fetch(self):
                calls.append(self.symbol)
                return super().parse_response(YAHOO_CHART_RESPONSE_FRESH)

        p1 = TrackingProvider(ConfirmerName.DXY, "DX-Y.NYB")
        p2 = TrackingProvider(ConfirmerName.DXY, "UUP")
        fb = FallbackProvider([p1, p2], ConfirmerName.DXY)
        r = fb.fetch()
        assert r.status == ConfirmerStatus.FRESH
        assert calls == ["DX-Y.NYB"]  # second never called

    def test_first_fails_second_succeeds(self):
        p1 = StubProvider(ConfirmerName.OIL)  # returns unavailable

        class FixedProvider(YahooFinanceProvider):
            def fetch(self):
                return super().parse_response(YAHOO_CHART_RESPONSE_FRESH)

        p2 = FixedProvider(ConfirmerName.OIL, "BZ=F")
        fb = FallbackProvider([p1, p2], ConfirmerName.OIL)
        r = fb.fetch()
        assert r.status == ConfirmerStatus.FRESH

    def test_exception_in_provider_caught_by_fallback(self):
        class ExplodingProvider(YahooFinanceProvider):
            def fetch(self):
                raise RuntimeError("boom")

        from gold_wirewatch.confirmers import StaticProvider
        fb = FallbackProvider(
            [ExplodingProvider(ConfirmerName.USDJPY, "JPY=X"),
             StaticProvider(ConfirmerName.USDJPY, 148.5)],
            ConfirmerName.USDJPY,
        )
        r = fb.fetch()
        assert r.status == ConfirmerStatus.FRESH
        assert r.value == pytest.approx(148.5)


# ---------------------------------------------------------------------------
# Provider factory tests
# ---------------------------------------------------------------------------

class TestProviderFactories:
    """Ensure factory functions produce valid fallback chains."""

    def test_make_dxy_provider_type(self):
        p = make_dxy_provider()
        assert isinstance(p, FallbackProvider)

    def test_make_us10y_provider_type(self):
        p = make_us10y_provider()
        assert isinstance(p, FallbackProvider)

    def test_make_oil_provider_type(self):
        p = make_oil_provider()
        assert isinstance(p, FallbackProvider)

    def test_make_usdjpy_provider_type(self):
        p = make_usdjpy_provider()
        assert isinstance(p, FallbackProvider)

    def test_make_equities_provider_type(self):
        p = make_equities_provider()
        assert isinstance(p, FallbackProvider)

    def test_make_live_providers_covers_all_names(self):
        providers = make_live_providers()
        for name in ConfirmerName:
            assert name in providers

    def test_engine_with_live_providers_factory(self):
        engine = ConfirmerEngine.with_live_providers()
        for name in ConfirmerName:
            assert name in engine.providers
            assert isinstance(engine.providers[name], FallbackProvider)
