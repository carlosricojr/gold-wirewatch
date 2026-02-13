# Threat Model

## Assets
- OpenClaw bearer token
- Event integrity for market-moving alerts
- Local SQLite history

## Threats
- Unauthorized webhook submissions
- Token leakage from `.env`
- Feed poisoning / malformed data
- Service interruption from flaky sources

## Mitigations
- Bind webhook to localhost or tailnet-only interface
- Keep `.env` out of VCS
- Validate and sanitize webhook payload shape
- Use retries + dedupe + per-feed failure isolation
- Prefer allowlisted official/public feeds only
