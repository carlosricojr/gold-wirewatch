from datetime import UTC, datetime

from gold_wirewatch.models import FeedItem, ScoreResult
from gold_wirewatch.storage import Storage


def test_storage_dedupe(tmp_path) -> None:
    db = tmp_path / "wire.db"
    store = Storage(str(db))
    key = "abc"
    item = FeedItem("src", "t", "s", "u", "g", None, datetime.now(UTC))
    score = ScoreResult(0.7, 0.8, ["fed"])
    assert not store.is_seen(key)
    store.save_item(key, item, score)
    assert store.is_seen(key)
    assert len(store.latest_items(120)) >= 1
