from datetime import UTC, datetime

from typer.testing import CliRunner

from gold_wirewatch.cli import app
from gold_wirewatch.config import FeedConfig, Settings
from gold_wirewatch.models import FeedItem
from gold_wirewatch.service import WireWatchService
from gold_wirewatch.storage import Storage

KEYWORDS = {"fed": (0.35, 0.5), "treasury": (0.3, 0.3), "risk-off": (0.25, 0.45)}


def test_process_items_triggers_and_dedupes(tmp_path) -> None:
    settings = Settings(openclaw_token="tok", relevance_threshold=0.1, severity_threshold=0.1)
    svc = WireWatchService(settings, [], Storage(str(tmp_path / "a.db")), KEYWORDS)

    fired: list[str] = []
    svc.oc.trigger = lambda text, context=None: fired.append(text)  # type: ignore[method-assign]

    item = FeedItem(
        source="s",
        title="Gold Fed treasury",
        summary="risk-off",
        url="u",
        guid="g1",
        published_at=None,
        fetched_at=datetime.now(UTC),
    )
    assert svc.process_items([item]) == 1
    assert svc.process_items([item]) == 0
    assert len(fired) == 1


def test_poll_once_handles_feed_errors(tmp_path, monkeypatch) -> None:
    settings = Settings(openclaw_token="tok")
    feeds = [FeedConfig("a", "u", "rss")]
    svc = WireWatchService(settings, feeds, Storage(str(tmp_path / "b.db")), KEYWORDS)

    def boom(client, feed, cfg):
        _ = (client, feed, cfg)
        raise ValueError("bad")

    monkeypatch.setattr("gold_wirewatch.service.poll_feed", boom)
    assert svc.poll_once() == 0


def test_process_items_geo_watch_path_triggers_even_below_main_gate(tmp_path) -> None:
    settings = Settings(openclaw_token="tok", relevance_threshold=0.9, severity_threshold=0.9)
    svc = WireWatchService(settings, [], Storage(str(tmp_path / "geo.db")), KEYWORDS)

    fired: list[tuple[str, object]] = []
    svc.oc.trigger = lambda text, context=None: fired.append((text, context))  # type: ignore[method-assign]

    item = FeedItem(
        source="geo",
        title="Iran and aircraft carrier standoff raises military tension",
        summary="Middle East escalation risk with oil shipping concern",
        url="u",
        guid="geo-1",
        published_at=None,
        fetched_at=datetime.now(UTC),
    )
    assert svc.process_items([item]) == 1
    assert len(fired) == 1


def test_geo_watch_cooldown_blocks_repeated_geo_alerts(tmp_path) -> None:
    settings = Settings(openclaw_token="tok", relevance_threshold=0.9, severity_threshold=0.9)
    svc = WireWatchService(settings, [], Storage(str(tmp_path / "geo2.db")), KEYWORDS)

    fired: list[str] = []
    svc.oc.trigger = lambda text, context=None: fired.append(text)  # type: ignore[method-assign]

    item1 = FeedItem(
        source="geo",
        title="Iran carrier movement raises shipping/oil concern",
        summary="Middle East military escalation",
        url="u1",
        guid="geo-11",
        published_at=None,
        fetched_at=datetime.now(UTC),
    )
    item2 = FeedItem(
        source="geo",
        title="Iran warns over Hormuz as oil shipping risk rises",
        summary="Further escalation",
        url="u2",
        guid="geo-22",
        published_at=None,
        fetched_at=datetime.now(UTC),
    )

    assert svc.process_items([item1]) == 1
    assert svc.process_items([item2]) == 0
    assert len(fired) == 1


def test_cli_status_and_poll_once(tmp_path, monkeypatch) -> None:
    runner = CliRunner()

    class DummySvc:
        def poll_once(self) -> int:
            return 2

    monkeypatch.setattr("gold_wirewatch.cli.build_service", lambda: DummySvc())
    result = runner.invoke(app, ["poll-once"])
    assert result.exit_code == 0
    assert "triggered=2" in result.stdout

    env = {
        "OPENCLAW_TOKEN": "tok",
        "DB_PATH": str(tmp_path / "w.db"),
        "FEEDS_PATH": str(tmp_path / "feeds.json"),
    }
    (tmp_path / "feeds.json").write_text('{"feeds": []}', encoding="utf-8")
    result2 = runner.invoke(app, ["status"], env=env)
    assert result2.exit_code == 0
    assert "timezone=" in result2.stdout
