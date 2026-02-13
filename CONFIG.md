# Configuration Guide

All vars are documented in `.env.example`.

## Core controls
- `TIMEZONE=America/New_York`
- `POLL_INTERVAL_ACTIVE_SECONDS=15..30` recommended during Asia session
- `ACTIVE_WINDOW_START_HOUR=18`
- `ACTIVE_WINDOW_END_HOUR=1`

## Threshold tuning
- `RELEVANCE_THRESHOLD` and `SEVERITY_THRESHOLD` gate OpenClaw triggers for news
- Market move bypasses score gate and uses dedicated fast-move rule vars

## OpenClaw integration
- Endpoint: `${OPENCLAW_BASE_URL}/hooks/agent`
- Required header: `Authorization: Bearer <OPENCLAW_TOKEN>`
- Payload fields: `agentId`, `wakeMode=now`, `text`, optional `context`

## Feed policy
Use only legal/public endpoints in `config/feeds.json`.
Do not scrape authenticated or paywalled content.
