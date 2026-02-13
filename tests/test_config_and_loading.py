from pathlib import Path

from gold_wirewatch.config import load_feeds, load_thresholds
from gold_wirewatch.scoring import load_keywords


def test_yaml_loaders(tmp_path: Path) -> None:
    (tmp_path / "sources.yaml").write_text(
        "feeds:\n  - name: A\n    url: https://x\n    kind: rss\n    enabled: true\n",
        encoding="utf-8",
    )
    (tmp_path / "keywords.yaml").write_text(
        "keywords:\n  - term: fed\n    relevance: 0.4\n    severity: 0.5\n",
        encoding="utf-8",
    )
    (tmp_path / "thresholds.yaml").write_text(
        (
            "relevance_threshold: 0.55\n"
            "severity_threshold: 0.45\n"
            "market_move_delta_usd: 8\n"
            "market_move_window_seconds: 120\n"
        ),
        encoding="utf-8",
    )

    feeds = load_feeds(str(tmp_path / "sources.yaml"))
    keywords = load_keywords(str(tmp_path / "keywords.yaml"))
    th = load_thresholds(str(tmp_path / "thresholds.yaml"))

    assert feeds[0].name == "A"
    assert keywords["fed"][0] == 0.4
    assert th.market_move_window_seconds == 120
