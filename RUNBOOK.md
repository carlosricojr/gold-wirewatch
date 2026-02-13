# Runbook

## Start
1. `uv sync`
2. Configure `.env`
3. `uv run python -m gold_wirewatch.cli run`

## Health checks
- `GET /health`
- `uv run python -m gold_wirewatch.cli status`

## Failure handling
- Feed failures are isolated per source; other feeds continue
- HTTP retries use exponential backoff
- If OpenClaw down: retries then exception in logs, service continues polling next cycles

## Incident triage
- Verify DB updates in `data/wirewatch.db`
- Verify webhook payload format and token
- Lower thresholds temporarily for smoke tests, then revert
