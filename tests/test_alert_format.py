from datetime import UTC, datetime

from gold_wirewatch.alerts import format_market_move_alert, format_news_alert
from gold_wirewatch.models import FeedItem, ScoreResult


def test_news_alert_format_max_10_lines() -> None:
    item = FeedItem("src", "Verbatim Headline", "fed treasury", "u", "g", None, datetime.now(UTC))
    score = ScoreResult(0.8, 0.7, ["fed", "treasury"])
    text = format_news_alert(item, score, "America/New_York")
    lines = text.splitlines()
    assert len(lines) <= 10
    assert lines[0].startswith("1) Timestamp ET:")
    assert lines[1].startswith("2) HEADLINE:")
    assert "Cross-asset confirmers" in text


def test_market_alert_format_max_10_lines() -> None:
    text = format_market_move_alert("GC1!", 9.2, 120, "America/New_York")
    assert len(text.splitlines()) <= 10
