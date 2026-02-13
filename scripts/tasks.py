from __future__ import annotations

from datetime import UTC, datetime

import typer

from gold_wirewatch.cli import build_service

app = typer.Typer()


@app.command("pre-session-risk-map")
def pre_session_risk_map() -> None:
    svc = build_service()
    svc.oc.trigger("[wire] pre-session risk map: summarize latest macro risks for gold")
    typer.echo("sent pre-session risk map")


@app.command("rolling-digest")
def rolling_digest(window_min: int = 15) -> None:
    svc = build_service()
    rows = svc.storage.latest_items(window_min)
    if not rows:
        typer.echo("no new relevant info")
        return
    top = rows[:5]
    summary = "\n".join(f"- {r['source']}: {r['title']}" for r in top)
    svc.oc.trigger(f"[wire] rolling digest {window_min}m\n{summary}")
    typer.echo(f"sent rolling digest {len(top)} items")


@app.command("post-session-recap")
def post_session_recap() -> None:
    svc = build_service()
    rows = svc.storage.latest_items(480)
    summary = "\n".join(f"- {r['source']}: {r['title']}" for r in rows[:10])
    svc.oc.trigger(f"[wire] post-session recap\n{summary}")
    typer.echo("sent post-session recap")


@app.command("weekly-health-check")
def weekly_health_check() -> None:
    svc = build_service()
    msg = (
        "[wire] weekly health check: review feed uptime, keyword hit rates, "
        "threshold precision/recall"
    )
    svc.oc.trigger(msg)
    typer.echo(f"sent weekly health check at {datetime.now(UTC).isoformat()}")


if __name__ == "__main__":
    app()
