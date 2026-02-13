from gold_wirewatch.config import FeedConfig, Settings
from gold_wirewatch.feeds import poll_feed


class DummyResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class DummyClient:
    def __init__(self, body: str) -> None:
        self.body = body

    def get(self, url: str, timeout: float) -> DummyResponse:
        _ = (url, timeout)
        return DummyResponse(self.body)


def test_poll_feed_rss_parsing() -> None:
    rss = (
        "<?xml version='1.0'?><rss><channel><item>"
        "<title>Gold and Fed</title><link>https://x</link><guid>1</guid>"
        "<pubDate>Fri, 13 Feb 2026 14:00:00 GMT</pubDate>"
        "<description>USD and treasury</description>"
        "</item></channel></rss>"
    )
    settings = Settings(openclaw_token="x")
    items = poll_feed(DummyClient(rss), FeedConfig("f", "u", "rss"), settings)
    assert len(items) == 1
    assert items[0].guid == "1"
    assert "Gold" in items[0].title


def test_poll_feed_json_parsing() -> None:
    body = (
        "[{\"id\":\"1\",\"title\":\"Fed update\","
        "\"description\":\"risk-off\",\"url\":\"https://x\","
        "\"published\":\"Fri, 13 Feb 2026 14:00:00 GMT\"}]"
    )
    settings = Settings(openclaw_token="x")
    items = poll_feed(DummyClient(body), FeedConfig("j", "u", "json"), settings)
    assert len(items) == 1
    assert items[0].source == "j"
