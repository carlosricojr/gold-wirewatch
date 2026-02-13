# Troubleshooting

## No alerts firing
- Check thresholds too high
- Ensure feed entries include gold-driver keywords
- Confirm OpenClaw token and base URL

## Webhook returns triggered=false
- Payload symbol mismatch (`MARKET_MOVE_SYMBOL`)
- Delta below configured threshold
- Window exceeds configured max

## Feed parsing errors
- Verify source is RSS/JSON and reachable
- Disable broken source in `config/feeds.json`

## Type/lint failures
- Run: `uv run ruff check .`, `uv run mypy src`, `uv run pytest`
