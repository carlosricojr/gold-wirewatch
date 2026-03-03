from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import os

import typer

from .config import load_feeds, load_settings, load_thresholds
from .confirmers import ConfirmerEngine, ScidConfig
from .scoring import load_keywords
from .service import WireWatchService, start_service_with_webhook
from .storage import Storage

app = typer.Typer(help="Gold wirewatch CLI")


def _first_existing(root: Path, patterns: list[str]) -> str | None:
    for pattern in patterns:
        for p in root.glob(pattern):
            if p.is_file():
                return str(p)
    return None


def _discover_scid_config() -> ScidConfig:
    data_dir = Path(os.getenv("SIERRA_CHART_DATA_DIR", "C:/SierraChart/Data"))
    if not data_dir.exists():
        return ScidConfig()

    return ScidConfig(
        dxy=_first_existing(data_dir, ["USDX.scid", "DX*.scid"]),
        us10y=_first_existing(data_dir, ["10Y*.scid", "TNX*.scid"]),
        oil=_first_existing(data_dir, ["CL*.scid"]),
        usdjpy=_first_existing(data_dir, ["USDJPY*.scid", "*USDJPY*.scid"]),
        equities=_first_existing(data_dir, ["NQ*.scid", "ES*.scid", "SP*.scid"]),
    )


def build_service() -> WireWatchService:
    settings = load_settings()
    thresholds = load_thresholds(settings.thresholds_path)
    settings.relevance_threshold = thresholds.relevance_threshold
    settings.severity_threshold = thresholds.severity_threshold
    settings.market_move_delta_usd = thresholds.market_move_delta_usd
    settings.market_move_window_seconds = thresholds.market_move_window_seconds

    feeds = load_feeds(settings.feeds_path)
    keywords = load_keywords(settings.keywords_path)
    storage = Storage(settings.db_path)
    scid = _discover_scid_config()
    confirmer_engine = ConfirmerEngine.with_live_providers(scid=scid)
    return WireWatchService(settings, feeds, storage, keywords, confirmer_engine=confirmer_engine)


@app.command("run")
def run_service() -> None:
    """Run poller + webhook server."""
    start_service_with_webhook(build_service())


@app.command("poll-once")
def poll_once() -> None:
    service = build_service()
    fired = service.poll_once()
    typer.echo(f"processed; triggered={fired}")


@app.command("status")
def status() -> None:
    settings = load_settings()
    scid = _discover_scid_config()
    typer.echo(f"timezone={settings.timezone}")
    typer.echo(f"now_utc={datetime.now(UTC).isoformat()}")
    typer.echo(f"db_path={settings.db_path}")
    typer.echo(f"feeds_path={settings.feeds_path}")
    typer.echo(f"scid_dxy={scid.dxy}")
    typer.echo(f"scid_us10y={scid.us10y}")
    typer.echo(f"scid_oil={scid.oil}")
    typer.echo(f"scid_usdjpy={scid.usdjpy}")
    typer.echo(f"scid_equities={scid.equities}")


if __name__ == "__main__":
    app()
