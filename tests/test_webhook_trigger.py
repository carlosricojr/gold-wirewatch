from gold_wirewatch.config import FeedConfig, Settings
from gold_wirewatch.service import WireWatchService
from gold_wirewatch.storage import Storage

KEYWORDS = {"fed": (0.35, 0.5)}


def _svc(tmp_path, feeds: list[FeedConfig]) -> WireWatchService:
    settings = Settings(
        openclaw_token="tok",
        market_move_delta_usd=8.0,
        market_move_window_seconds=120,
    )
    return WireWatchService(settings, feeds, Storage(str(tmp_path / "x.db")), KEYWORDS)


def test_market_move_trigger(tmp_path) -> None:
    service = _svc(tmp_path, [FeedConfig("a", "b", "rss")])
    called: list[str] = []
    service.oc.trigger = lambda text, context=None: called.append(text)  # type: ignore[method-assign]

    assert service.handle_market_move("GC1!", 2900.0, 2909.0, 120)
    assert called


def test_market_move_no_trigger(tmp_path) -> None:
    service = _svc(tmp_path, [])
    service.oc.trigger = lambda text, context=None: None  # type: ignore[method-assign]
    assert not service.handle_market_move("GC1!", 2900.0, 2904.0, 120)
