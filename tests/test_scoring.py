from datetime import UTC, datetime

from gold_wirewatch.models import FeedItem
from gold_wirewatch.scoring import score_item

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
