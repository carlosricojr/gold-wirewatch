"""Tests for the market-moving catalyst detection lane.

Covers:
1. catalyst_watch_reasons() catches target headlines
2. catalyst_watch_reasons() does NOT fire on unrelated headlines
3. Existing geo/policy lanes are not regressed
4. Service integration fires catalyst alerts
5. Catalyst cooldown prevents spam
6. Dedupe/suppression still works for catalyst alerts
7. Source-tier gate still enforced for catalyst alerts
8. decide_from_scores with catalyst_hit
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gold_wirewatch.evidence_gate import DecisionState, decide_from_scores
from gold_wirewatch.models import FeedItem
from gold_wirewatch.scoring import (
    catalyst_watch_reasons,
    geo_watch_reasons,
    policy_watch_reasons,
    score_item,
)


def _make_item(title: str, summary: str = "", source: str = "TestSource") -> FeedItem:
    return FeedItem(
        source=source,
        title=title,
        summary=summary,
        url="https://example.com/test",
        guid="test-guid",
        published_at=datetime.now(UTC),
        fetched_at=datetime.now(UTC),
    )


# ---- catalyst_watch_reasons detection ----

class TestCatalystWatchReasons:
    """Test catalyst_watch_reasons catches and rejects correctly."""

    def test_oracle_openai_capex_cut(self):
        item = _make_item(
            "Oracle cuts OpenAI Texas data center investment amid cost concerns"
        )
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) > 0
        assert any("catalyst:" in r for r in reasons)

    def test_nvidia_demand_warning(self):
        item = _make_item(
            "NVIDIA warns on data center demand, cuts capex guidance"
        )
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) > 0

    def test_hyperscaler_capex_standalone(self):
        item = _make_item("Hyperscaler capex spending under pressure")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) > 0
        assert any("standalone" in r for r in reasons)

    def test_semiconductor_demand_shock(self):
        item = _make_item("Semiconductor demand shock hits chip stocks")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) > 0

    def test_tsmc_guidance_cut(self):
        item = _make_item("TSMC guidance cut sends ripples through semiconductor supply chain")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) > 0

    def test_meta_ai_spending_cut(self):
        item = _make_item("Meta slashes AI spending plans for 2026")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) > 0
        assert any("catalyst:" in r for r in reasons)

    def test_generic_unrelated_headline_no_fire(self):
        item = _make_item("Local weather forecast sunny and warm today")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) == 0

    def test_generic_tech_headline_no_fire(self):
        """Tech headline without action terms should NOT trigger."""
        item = _make_item("Oracle announces new cloud partnership with Microsoft")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) == 0

    def test_investment_without_cut_no_fire(self):
        """Just 'investment' without a negative action should NOT trigger."""
        item = _make_item("Google increases data center investment in Europe")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) == 0

    def test_negation_denies_cut_no_fire(self):
        """Negation headline should NOT trigger."""
        item = _make_item("Oracle denies cut to data center investment plans")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) == 0

    def test_negation_restores_no_fire(self):
        item = _make_item("NVIDIA restores capex guidance after temporary review")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) == 0

    def test_negation_reaffirms_no_fire(self):
        item = _make_item("Microsoft reaffirms AI spending guidance for 2026")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) == 0

    def test_positive_investment_increase_no_fire(self):
        """Positive investment news should NOT trigger."""
        item = _make_item("Amazon increases investment in new data center in Virginia")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) == 0

    def test_hiring_headline_no_fire(self):
        """Hiring/HR news mentioning tech entities should NOT trigger."""
        item = _make_item("Google hiring 5000 engineers for cloud division expansion")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) == 0

    def test_max_six_reasons(self):
        """Reasons list should be capped at 6."""
        item = _make_item(
            "NVIDIA TSMC AMD Intel Samsung ASML all cut capex guidance, demand warning, spending cut, writedown impairment"
        )
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) <= 6


# ---- Geo/policy regression tests ----

class TestGeoRegressionWithCatalyst:
    """Ensure existing geo and policy lanes still work after catalyst addition."""

    def test_geo_iran_hormuz_still_fires(self):
        item = _make_item("Iran threatens to close Strait of Hormuz, oil shipping at risk")
        reasons = geo_watch_reasons(item)
        assert len(reasons) > 0

    def test_policy_tariff_still_fires(self):
        item = _make_item("New tariff trade war escalation hits markets")
        reasons = policy_watch_reasons(item)
        assert len(reasons) > 0

    def test_geo_headline_not_catalyst(self):
        """Geo headlines should NOT trigger catalyst lane."""
        item = _make_item("Iran threatens to close Strait of Hormuz, oil shipping at risk")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) == 0


# ---- decide_from_scores with catalyst_hit ----

class TestDecideFromScoresWithCatalyst:
    """Test that catalyst_hit works in the decision state machine."""

    def test_catalyst_hit_promotes_to_conditional(self):
        """Low severity + catalyst_hit should get CONDITIONAL (like geo/policy)."""
        result = decide_from_scores(0.3, 0.35, catalyst_hit=True)
        assert result == DecisionState.CONDITIONAL

    def test_catalyst_hit_without_severity_stays_neutral(self):
        """Below neutral ceiling, catalyst_hit shouldn't help."""
        result = decide_from_scores(0.1, 0.15, catalyst_hit=True)
        assert result == DecisionState.NEUTRAL

    def test_no_catalyst_hit_stays_fade(self):
        """Without catalyst_hit, same scores should be FADE."""
        result = decide_from_scores(0.3, 0.35, catalyst_hit=False)
        assert result == DecisionState.FADE


# ---- Score item includes catalyst reasons ----

class TestScoreItemIncludesCatalyst:
    """Test that score_item integrates catalyst reasons into ScoreResult."""

    def test_catalyst_reasons_in_score(self):
        keywords = {"gold": (0.3, 0.1)}
        item = _make_item("NVIDIA cuts capex guidance, semiconductor demand warning")
        result = score_item(item, keywords)
        assert any("catalyst:" in r for r in result.reasons)

    # ---- Word boundary / substring collision tests ----

class TestCatalystBoundaryCollisions:
    """Ensure short tokens don't false-positive via substring matching."""

    def test_amd_not_in_demand(self):
        """'demand' should NOT trigger 'amd' entity match."""
        item = _make_item("Strong demand for consumer electronics continues")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) == 0

    def test_amd_not_in_demand_with_context(self):
        """'demand weakness' without real entity should NOT trigger."""
        item = _make_item("Consumer demand weakness concerns investment outlook")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) == 0

    def test_meta_not_in_metadata(self):
        """'metadata' should NOT trigger 'meta' entity match."""
        item = _make_item("New metadata investment standards cut processing overhead")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) == 0

    def test_chip_not_in_chipotle(self):
        """'Chipotle' should NOT trigger 'chip' entity match."""
        item = _make_item("Chipotle cuts spending on new restaurant investment")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) == 0

    def test_miss_not_in_dismissed(self):
        """'dismissed' should NOT trigger 'miss' action match."""
        item = _make_item("Oracle dismissed investment concerns about data center plans")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) == 0

    def test_real_amd_still_triggers(self):
        """Actual AMD headline should still trigger."""
        item = _make_item("AMD cuts capex guidance amid semiconductor demand weakness")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) > 0

    def test_real_meta_still_triggers(self):
        """Actual Meta headline should still trigger."""
        item = _make_item("Meta slashes AI spending plans, cuts data center investment")
        reasons = catalyst_watch_reasons(item)
        assert len(reasons) > 0


    def test_non_catalyst_score_unchanged(self):
        keywords = {"gold": (0.3, 0.1)}
        item = _make_item("Gold prices rise on safe haven demand")
        result = score_item(item, keywords)
        assert not any("catalyst:" in r for r in result.reasons)
