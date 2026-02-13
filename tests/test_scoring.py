from datetime import UTC, datetime

from gold_wirewatch.models import FeedItem
from gold_wirewatch.scoring import geo_watch_reasons, score_item

KEYWORDS = {
    "fed": (0.35, 0.5),
    "treasury": (0.3, 0.3),
    "risk-off": (0.25, 0.45),
    "usd": (0.3, 0.25),
}


def test_scoring_detects_gold_drivers() -> None:
    item = FeedItem(
        source="x",
        title="Fed and Treasury comments lift USD, gold volatile",
        summary="real yield rises amid risk-off tone",
        url="u",
        guid="g",
        published_at=None,
        fetched_at=datetime.now(UTC),
    )
    score = score_item(item, KEYWORDS)
    assert score.relevance_score >= 0.55
    assert score.severity_score >= 0.45
    assert "fed" in score.reasons


def test_geo_watch_detects_iran_carrier_escalation() -> None:
    item = FeedItem(
        source="geo",
        title="US sends second aircraft carrier as Iran tensions rise",
        summary="Military escalation concern in the Middle East",
        url="u",
        guid="g2",
        published_at=None,
        fetched_at=datetime.now(UTC),
    )
    reasons = geo_watch_reasons(item)
    assert reasons
    assert any("geo:iran" == r for r in reasons)


def test_geo_watch_ignores_non_material_generic_story() -> None:
    item = FeedItem(
        source="misc",
        title="City council debate over local park renovation",
        summary="No macro or geopolitical implications",
        url="u",
        guid="g3",
        published_at=None,
        fetched_at=datetime.now(UTC),
    )
    assert geo_watch_reasons(item) == []


def test_geo_watch_ignores_non_iran_regional_violence_headline() -> None:
    item = FeedItem(
        source="geo",
        title="Israeli settlers injure dozens in West Bank attacks",
        summary="Regional violence update",
        url="u",
        guid="g4",
        published_at=None,
        fetched_at=datetime.now(UTC),
    )
    assert geo_watch_reasons(item) == []
