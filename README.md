# gold-wirewatch

Production-ready low-latency **Gold Futures Asia Session News Wire + Triage**.

## Features
- Legal/public feed allowlist (RSS/JSON only; no paywalled scraping)
- Active session polling (default 20s in 18:00-01:00 ET, slower outside)
- SQLite dedupe by stable item key + source timestamps
- Pre-LLM scoring (`relevanceScore`, `severityScore`) for gold macro drivers
- OpenClaw trigger via `POST /hooks/agent` (`wakeMode=now`)
- Market move webhook (TradingView/generic) with fast move rule (default $8 in 120s)
- Graceful degradation: retries/backoff + per-feed isolation

## Quickstart
```bash
cd C:/Users/carlo/Repositories/gold-wirewatch
uv sync
copy .env.example .env
uv run python -m gold_wirewatch.cli status
uv run python -m gold_wirewatch.cli poll-once
uv run python -m gold_wirewatch.cli run
```

## Tests and quality
```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

Coverage gate is enforced at >=90%.

## TradingView webhook
Send JSON to `http://127.0.0.1:8787/webhook/market-move`:
```json
{"symbol":"GC1!","previous":2895.0,"current":2903.4,"window_seconds":120}
```
A trigger fires when abs(current-previous) >= `MARKET_MOVE_DELTA_USD` and window <= `MARKET_MOVE_WINDOW_SECONDS`.

Fallback without TradingView webhooks: keep normal feed polling enabled and optionally run
`uv run python scripts/tasks.py rolling-digest --window-min 15` on schedule.

See `CONFIG.md`, `RUNBOOK.md`, `SOURCE_POLICY.md`, `BASELINE_UPGRADES.md`, and `cron/CRON_SETUP.md`.
