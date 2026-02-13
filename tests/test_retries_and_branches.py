from datetime import UTC, datetime

import httpx

from gold_wirewatch.config import FeedConfig, Settings
from gold_wirewatch.feeds import _fetch_text, poll_feed
from gold_wirewatch.models import FeedItem
from gold_wirewatch.openclaw_client import OpenClawClient
from gold_wirewatch.scoring import score_item


def test_fetch_text_retry_then_success(monkeypatch) -> None:
    class Resp:
        text = "ok"

        def raise_for_status(self) -> None:
            return None

    class Flaky:
        def __init__(self) -> None:
            self.n = 0

        def get(self, url: str, timeout: float):
            _ = (url, timeout)
            self.n += 1
            if self.n == 1:
                raise httpx.ConnectError("x")
            return Resp()

    s = Settings(openclaw_token="t", retry_max_attempts=2)
    assert _fetch_text(Flaky(), "u", s) == "ok"


def test_openclaw_retry_then_success(monkeypatch) -> None:
    called = {"n": 0}

    class Resp:
        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url: str, headers, content):
            _ = (url, headers, content)
            called["n"] += 1
            if called["n"] == 1:
                raise httpx.ConnectError("x")
            return Resp()

    monkeypatch.setattr("gold_wirewatch.openclaw_client.httpx.Client", lambda timeout: FakeClient())
    client = OpenClawClient(Settings(openclaw_token="tok", retry_max_attempts=2))
    client.trigger("hi")
    assert called["n"] == 2


def test_score_no_strong_driver() -> None:
    item = FeedItem("s", "hello", "world", "u", "g", None, datetime.now(UTC))
    out = score_item(item)
    assert out.reasons == ["no-strong-driver"]


def test_poll_feed_json_skips_non_dict() -> None:
    class Resp:
        text = '[1, {"id":"x","title":"Fed"}]'

        def raise_for_status(self) -> None:
            return None

    class Client:
        def get(self, url: str, timeout: float):
            _ = (url, timeout)
            return Resp()

    items = poll_feed(Client(), FeedConfig("j", "u", "json"), Settings(openclaw_token="t"))
    assert len(items) == 1
