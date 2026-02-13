from datetime import UTC, datetime

from fastapi.testclient import TestClient

from gold_wirewatch.config import FeedConfig, Settings
from gold_wirewatch.models import FeedItem
from gold_wirewatch.service import WireWatchService, create_webhook_app
from gold_wirewatch.storage import Storage


def test_poll_once_success_path(tmp_path, monkeypatch) -> None:
    settings = Settings(openclaw_token="tok", relevance_threshold=0.1, severity_threshold=0.1)
    svc = WireWatchService(settings, [FeedConfig("a", "u", "rss")], Storage(str(tmp_path / "d.db")))

    item = FeedItem("a", "Gold fed", "treasury risk-off", "u", "g", None, datetime.now(UTC))
    monkeypatch.setattr("gold_wirewatch.service.poll_feed", lambda client, feed, cfg: [item])

    fired: list[str] = []
    svc.oc.trigger = lambda text, context=None: fired.append(text)  # type: ignore[method-assign]
    assert svc.poll_once() == 1
    assert fired


def test_webhook_app_health_and_post(tmp_path) -> None:
    settings = Settings(openclaw_token="tok")
    svc = WireWatchService(settings, [], Storage(str(tmp_path / "e.db")))
    svc.oc.trigger = lambda text, context=None: None  # type: ignore[method-assign]

    app = create_webhook_app(svc)
    client = TestClient(app)

    r1 = client.get("/health")
    assert r1.status_code == 200
    assert r1.json()["status"] == "ok"

    r2 = client.post(
        "/webhook/market-move",
        json={"symbol": "GC1!", "previous": 2900.0, "current": 2910.1, "window_seconds": 120},
    )
    assert r2.status_code == 200
    assert r2.json()["ok"] is True
