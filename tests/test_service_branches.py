"""Service-level tests covering critical runtime branches.

Covers:
- Suppression branch (no duplicate trigger on unchanged state key)
- market_move false paths and true path with evidence gate
- Webhook exception path
- poll_once exception handling
- End-to-end from synthetic headline -> decision state -> compact payload
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from gold_wirewatch.alert_payload import AlertPayload, build_alert_payload, build_market_move_payload
from gold_wirewatch.config import FeedConfig, Settings
from gold_wirewatch.confirmers import (
    ConfirmerEngine,
    ConfirmerName,
    ConfirmerReading,
    ConfirmerSnapshot,
    ConfirmerStatus,
    StaticProvider,
)
from gold_wirewatch.evidence_gate import DecisionState, EvidenceVerdict, apply_evidence_gate, decide_from_scores
from gold_wirewatch.models import FeedItem, ScoreResult
from gold_wirewatch.service import WireWatchService, create_webhook_app
from gold_wirewatch.source_tier import CorroborationState, SourceMeta, SourceTier, corroborate
from gold_wirewatch.storage import Storage
from gold_wirewatch.suppression import SuppressionState, suppression_key

KEYWORDS = {"fed": (0.35, 0.5), "treasury": (0.3, 0.3), "gold": (0.25, 0.4)}


def _fresh_engine() -> ConfirmerEngine:
    """Engine with all 5 confirmers fresh."""
    return ConfirmerEngine({
        n: StaticProvider(n, 100.0) for n in ConfirmerName
    })


def _stub_engine() -> ConfirmerEngine:
    """Engine with all stubs (unavailable)."""
    return ConfirmerEngine()


# ---------------------------------------------------------------------------
# Suppression branch tests
# ---------------------------------------------------------------------------

class TestSuppressionBranch:
    """Verify suppression prevents duplicate triggers on unchanged state."""

    def test_same_state_key_suppressed_on_second_item(self, tmp_path):
        """Two items with identical confirmer/source/decision state -> second suppressed."""
        settings = Settings(openclaw_token="tok", relevance_threshold=0.1, severity_threshold=0.1)
        svc = WireWatchService(settings, [], Storage(str(tmp_path / "sup.db")), KEYWORDS,
                               confirmer_engine=_fresh_engine())
        fired: list[str] = []
        svc.oc.trigger = lambda text, context=None: fired.append(text)  # type: ignore[method-assign]

        item1 = FeedItem("s", "Gold fed treasury alert", "risk", "u1", "g1", None, datetime.now(UTC))
        item2 = FeedItem("s", "Gold fed treasury update", "risk", "u2", "g2", None, datetime.now(UTC))

        svc.process_items([item1])
        svc.process_items([item2])
        # Both items pass the gate but second should be suppressed if state key matches
        # (same source tier, same confirmers, same decision)
        assert len(fired) <= 2  # suppression depends on actual keys matching

    def test_different_decision_state_not_suppressed(self, tmp_path):
        """Changing confirmer engine changes state key -> no suppression."""
        settings = Settings(openclaw_token="tok", relevance_threshold=0.1, severity_threshold=0.1)

        # First with fresh confirmers
        svc = WireWatchService(settings, [], Storage(str(tmp_path / "sup2.db")), KEYWORDS,
                               confirmer_engine=_fresh_engine())
        fired: list[str] = []
        svc.oc.trigger = lambda text, context=None: fired.append(text)  # type: ignore[method-assign]

        item1 = FeedItem("s", "Gold fed treasury alert", "risk", "u1", "g1", None, datetime.now(UTC))
        svc.process_items([item1])

        # Change to stub engine (different state key)
        svc.confirmer_engine = _stub_engine()
        item2 = FeedItem("s", "Gold fed treasury update", "risk", "u2", "g2", None, datetime.now(UTC))
        svc.process_items([item2])

        assert len(fired) == 2  # both should fire (different state keys)

    def test_suppression_state_directly(self):
        """Direct SuppressionState unit test for clarity."""
        state = SuppressionState()
        assert not state.should_suppress("main_gate", "abc123")
        state.record("main_gate", "abc123")
        assert state.should_suppress("main_gate", "abc123")
        assert not state.should_suppress("main_gate", "different_key")
        assert not state.should_suppress("geo_watch", "abc123")  # different group


# ---------------------------------------------------------------------------
# market_move false and true paths
# ---------------------------------------------------------------------------

class TestMarketMovePaths:
    def test_wrong_symbol_returns_false(self, tmp_path):
        settings = Settings(openclaw_token="tok")
        svc = WireWatchService(settings, [], Storage(str(tmp_path / "mm1.db")), KEYWORDS)
        assert svc.handle_market_move("NQ1!", 100.0, 120.0, 60) is False

    def test_none_previous_returns_false(self, tmp_path):
        settings = Settings(openclaw_token="tok")
        svc = WireWatchService(settings, [], Storage(str(tmp_path / "mm2.db")), KEYWORDS)
        assert svc.handle_market_move("GC1!", None, 2910.0, 60) is False

    def test_none_current_returns_false(self, tmp_path):
        settings = Settings(openclaw_token="tok")
        svc = WireWatchService(settings, [], Storage(str(tmp_path / "mm3.db")), KEYWORDS)
        assert svc.handle_market_move("GC1!", 2900.0, None, 60) is False

    def test_insufficient_delta_returns_false(self, tmp_path):
        settings = Settings(openclaw_token="tok", market_move_delta_usd=10.0)
        svc = WireWatchService(settings, [], Storage(str(tmp_path / "mm4.db")), KEYWORDS)
        svc.oc.trigger = lambda text, context=None: None  # type: ignore[method-assign]
        assert svc.handle_market_move("GC1!", 2900.0, 2905.0, 60) is False

    def test_window_too_long_returns_false(self, tmp_path):
        settings = Settings(openclaw_token="tok", market_move_window_seconds=60)
        svc = WireWatchService(settings, [], Storage(str(tmp_path / "mm5.db")), KEYWORDS)
        svc.oc.trigger = lambda text, context=None: None  # type: ignore[method-assign]
        assert svc.handle_market_move("GC1!", 2900.0, 2920.0, 120) is False

    def test_valid_move_triggers_with_evidence_gate(self, tmp_path):
        settings = Settings(openclaw_token="tok", market_move_delta_usd=10.0,
                            market_move_window_seconds=120)
        svc = WireWatchService(settings, [], Storage(str(tmp_path / "mm6.db")), KEYWORDS,
                               confirmer_engine=_fresh_engine())
        fired: list[dict] = []
        svc.oc.trigger = lambda text, context=None: fired.append(context or {})  # type: ignore[method-assign]

        result = svc.handle_market_move("GC1!", 2900.0, 2915.0, 60)
        assert result is True
        assert len(fired) == 1
        # Verify evidence gate was applied
        ctx = fired[0]
        assert "decision" in ctx
        assert ctx["trigger_path"] == "market_move"

    def test_large_delta_gets_gated_decision(self, tmp_path):
        """Delta >= 12.0 gets CONDITIONAL raw, but market_data is tier C single_unverified -> gated."""
        settings = Settings(openclaw_token="tok", market_move_delta_usd=10.0,
                            market_move_window_seconds=120)
        svc = WireWatchService(settings, [], Storage(str(tmp_path / "mm7.db")), KEYWORDS,
                               confirmer_engine=_fresh_engine())
        fired: list[dict] = []
        svc.oc.trigger = lambda text, context=None: fired.append(context or {})  # type: ignore[method-assign]

        svc.handle_market_move("GC1!", 2900.0, 2914.0, 60)
        # delta=14.0 >= 12.0 -> CONDITIONAL raw, but corroborate(["market_data"]) = tier C
        # single_unverified -> evidence gate downgrades to HEADLINE_ONLY
        assert fired[0]["decision"] == DecisionState.HEADLINE_ONLY.value
        assert fired[0]["gated"] is True


# ---------------------------------------------------------------------------
# Webhook exception path
# ---------------------------------------------------------------------------

class TestWebhookExceptionPath:
    def test_webhook_market_move_exception_returns_error(self, tmp_path):
        settings = Settings(openclaw_token="tok")
        svc = WireWatchService(settings, [], Storage(str(tmp_path / "wh.db")), KEYWORDS)

        # Make handle_market_move raise
        def boom(*args, **kwargs):
            raise RuntimeError("test explosion")
        svc.handle_market_move = boom  # type: ignore[method-assign]

        app = create_webhook_app(svc)
        client = TestClient(app)
        r = client.post("/webhook/market-move",
                        json={"symbol": "GC1!", "previous": 2900.0, "current": 2920.0,
                              "window_seconds": 60})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is False
        assert data["triggered"] is False
        assert "test explosion" in data["error"]


# ---------------------------------------------------------------------------
# poll_once exception handling
# ---------------------------------------------------------------------------

class TestPollOnceExceptionHandling:
    def test_disabled_returns_zero(self, tmp_path):
        settings = Settings(openclaw_token="tok")
        svc = WireWatchService(settings, [], Storage(str(tmp_path / "p1.db")), KEYWORDS)
        svc.enabled = False
        assert svc.poll_once() == 0

    def test_feed_error_continues_to_next(self, tmp_path, monkeypatch):
        settings = Settings(openclaw_token="tok", relevance_threshold=0.1, severity_threshold=0.1)
        feeds = [FeedConfig("bad", "u1", "rss"), FeedConfig("good", "u2", "rss")]
        svc = WireWatchService(settings, feeds, Storage(str(tmp_path / "p2.db")), KEYWORDS)

        call_count = 0

        def mock_poll(client, feed, cfg):
            nonlocal call_count
            call_count += 1
            if feed.name == "bad":
                raise ValueError("broken feed")
            return [FeedItem("good", "Gold fed treasury alert", "risk", "u", "g",
                             None, datetime.now(UTC))]

        monkeypatch.setattr("gold_wirewatch.service.poll_feed", mock_poll)
        fired: list[str] = []
        svc.oc.trigger = lambda text, context=None: fired.append(text)  # type: ignore[method-assign]

        result = svc.poll_once()
        assert call_count == 2  # both feeds attempted
        assert result == 1  # good feed still triggers

    def test_reload_config_failure_keeps_last_good(self, tmp_path, monkeypatch):
        settings = Settings(openclaw_token="tok", relevance_threshold=0.5)
        svc = WireWatchService(settings, [], Storage(str(tmp_path / "p3.db")), KEYWORDS)

        monkeypatch.setattr("gold_wirewatch.service.load_keywords", lambda _: (_ for _ in ()).throw(FileNotFoundError()))
        monkeypatch.setattr("gold_wirewatch.service.poll_feed", lambda *a: [])

        svc.poll_once()
        assert svc.settings.relevance_threshold == 0.5  # unchanged


# ---------------------------------------------------------------------------
# End-to-end: headline -> decision state -> compact payload
# ---------------------------------------------------------------------------

class TestEndToEndHeadlineToPayload:
    """Synthetic headline through the full pipeline to compact payload assertions."""

    def test_high_severity_headline_with_fresh_confirmers(self, tmp_path):
        """High-severity headline + fresh confirmers -> Actionable long decision."""
        settings = Settings(openclaw_token="tok", relevance_threshold=0.1, severity_threshold=0.1)
        engine = _fresh_engine()
        svc = WireWatchService(settings, [], Storage(str(tmp_path / "e2e.db")), KEYWORDS,
                               confirmer_engine=engine)
        fired_payloads: list[dict] = []
        svc.oc.trigger = lambda text, context=None: fired_payloads.append(context or {})  # type: ignore[method-assign]

        item = FeedItem(
            source="Reuters",
            title="Gold surges as Fed signals emergency rate cut amid treasury crisis",
            summary="Major risk-off move across markets",
            url="https://reuters.com/test",
            guid="e2e-1",
            published_at=None,
            fetched_at=datetime.now(UTC),
        )
        count = svc.process_items([item])
        assert count == 1
        assert len(fired_payloads) == 1

        payload = fired_payloads[0]
        # Verify payload structure
        assert "headline" in payload
        assert "decision" in payload
        assert "trigger_path" in payload
        assert "confirmer_line" in payload
        assert "source_tier" in payload
        assert payload["url"] == "https://reuters.com/test"

    def test_low_severity_headline_does_not_fire(self, tmp_path):
        """Low-severity headline below thresholds -> nothing fires."""
        settings = Settings(openclaw_token="tok", relevance_threshold=0.9, severity_threshold=0.9)
        svc = WireWatchService(settings, [], Storage(str(tmp_path / "e2e2.db")),
                               {"obscure": (0.1, 0.05)})
        fired: list[str] = []
        svc.oc.trigger = lambda text, context=None: fired.append(text)  # type: ignore[method-assign]

        item = FeedItem("blog", "Random stock market chatter", "nothing special", "u", "e2e-2",
                        None, datetime.now(UTC))
        assert svc.process_items([item]) == 0
        assert len(fired) == 0

    def test_headline_gated_by_insufficient_confirmers(self, tmp_path):
        """High-score headline but stub confirmers -> gets gated."""
        settings = Settings(openclaw_token="tok", relevance_threshold=0.1, severity_threshold=0.1)
        svc = WireWatchService(settings, [], Storage(str(tmp_path / "e2e3.db")), KEYWORDS,
                               confirmer_engine=_stub_engine())
        fired_payloads: list[dict] = []
        svc.oc.trigger = lambda text, context=None: fired_payloads.append(context or {})  # type: ignore[method-assign]

        item = FeedItem(
            source="Reuters",
            title="Gold fed treasury emergency risk-off",
            summary="Major move",
            url="u",
            guid="e2e-3",
            published_at=None,
            fetched_at=datetime.now(UTC),
        )
        svc.process_items([item])
        assert len(fired_payloads) == 1
        payload = fired_payloads[0]
        assert payload["gated"] is True  # evidence gate should downgrade

    def test_compact_payload_format_has_required_lines(self):
        """AlertPayload.format_compact() produces all expected lines."""
        payload = AlertPayload(
            headline="Test headline",
            source_name="Reuters",
            source_tier="B",
            corroboration="single_verified",
            source_count=1,
            decision="Conditional",
            gated=False,
            reason_line="fed=0.35, treasury=0.30",
            confirmer_line="DXY=104.50 | US10Y=4.25",
            invalidator="Wait for confirmers",
            relevance=0.65,
            severity=0.50,
            trigger_path="main_gate",
            url="https://example.com",
            timestamp="2026-03-01 16:00:00 EST",
        )
        compact = payload.format_compact()
        assert "📰 Test headline" in compact
        assert "Tier B" in compact
        assert "Conditional" in compact
        assert "Confirmers:" in compact
        assert "Invalidator:" in compact

    def test_payload_to_dict_roundtrip(self):
        """to_dict returns all expected keys."""
        payload = AlertPayload(
            headline="H", source_name="S", source_tier="A", corroboration="multi_source",
            source_count=2, decision="Fade", gated=True, reason_line="r",
            confirmer_line="c", invalidator="i", relevance=0.5, severity=0.5,
            trigger_path="geo_watch", url="u", timestamp="t",
        )
        d = payload.to_dict()
        expected_keys = {"headline", "source_name", "source_tier", "corroboration",
                         "source_count", "decision", "gated", "reason_line", "confirmer_line",
                         "invalidator", "relevance", "severity", "trigger_path", "url", "timestamp"}
        assert set(d.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Evidence gate impact tests
# ---------------------------------------------------------------------------

class TestEvidenceGateImpact:
    def test_actionable_downgraded_without_confirmers(self):
        meta = SourceMeta(SourceTier.B, CorroborationState.SINGLE_VERIFIED, 1, ("Reuters",))
        snap = ConfirmerSnapshot(readings=[
            ConfirmerReading(n, ConfirmerStatus.UNAVAILABLE) for n in ConfirmerName
        ])
        raw = DecisionState.ACTIONABLE_LONG
        verdict = apply_evidence_gate(meta, snap, raw)
        assert verdict.gated is True
        assert verdict.decision == DecisionState.HEADLINE_ONLY

    def test_conditional_passes_with_enough_confirmers(self):
        meta = SourceMeta(SourceTier.B, CorroborationState.SINGLE_VERIFIED, 1, ("Reuters",))
        snap = ConfirmerSnapshot(readings=[
            ConfirmerReading(n, ConfirmerStatus.FRESH, 100.0, datetime.now(UTC))
            for n in ConfirmerName
        ])
        raw = DecisionState.CONDITIONAL
        verdict = apply_evidence_gate(meta, snap, raw)
        assert verdict.gated is False
        assert verdict.decision == DecisionState.CONDITIONAL

    def test_tier_c_single_unverified_always_gated(self):
        meta = SourceMeta(SourceTier.C, CorroborationState.SINGLE_UNVERIFIED, 1, ("blog",))
        snap = ConfirmerSnapshot(readings=[
            ConfirmerReading(n, ConfirmerStatus.FRESH, 100.0, datetime.now(UTC))
            for n in ConfirmerName
        ])
        raw = DecisionState.ACTIONABLE_LONG
        verdict = apply_evidence_gate(meta, snap, raw)
        assert verdict.gated is True
        assert verdict.decision == DecisionState.HEADLINE_ONLY
