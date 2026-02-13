# Wire Skill: Gold Asia Session Wirewatch

## Commands
- `/wire now` → trigger immediate `uv run python -m gold_wirewatch.cli poll-once`
- `/wire digest 15m` → summarize last 15 minutes from SQLite seen_items table
- `/wire digest 2h` → summarize last 120 minutes from SQLite seen_items table
- `/wire on` → set service enabled true (implementation contract: set runtime flag via control endpoint or restart with enabled default)
- `/wire off` → set service enabled false
- `/wire status` → return polling state, webhook bind, thresholds, and last trigger timestamps

## Prompt Contract
When OpenClaw receives wire events, prompt should include:
1. Event kind (`wire` or `market-move`)
2. relevanceScore + severityScore
3. reasons keyword list
4. source + URL
5. action hint: monitor / hedge / ignore

## Data Retrieval Paths
- Storage: `./data/wirewatch.db`
- News rows: `seen_items`
- Trigger rows: `events`
- Config: `.env` and `./config/feeds.json`
