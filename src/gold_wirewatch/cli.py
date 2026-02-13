from __future__ import annotations

from datetime import UTC, datetime

import typer

from .config import load_feeds, load_settings
from .service import WireWatchService, start_service_with_webhook
from .storage import Storage

app = typer.Typer(help="Gold wirewatch CLI")


def build_service() -> WireWatchService:
    settings = load_settings()
    feeds = load_feeds(settings.feeds_path)
    storage = Storage(settings.db_path)
    return WireWatchService(settings, feeds, storage)


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
    typer.echo(f"timezone={settings.timezone}")
    typer.echo(f"now_utc={datetime.now(UTC).isoformat()}")
    typer.echo(f"db_path={settings.db_path}")
    typer.echo(f"feeds_path={settings.feeds_path}")


if __name__ == "__main__":
    app()
