# gold-wirewatch

Low-latency **Gold Futures Asia Session News Wire + Triage** for Carlos.

## Build order implemented
1. **FREE baseline end-to-end first** (official/public feeds + scoring + dedupe + alerts + webhook)
2. **Paid upgrade toggles second** (IBKR Reuters / FinancialJuice PRO kept disabled until subscribed)

## Required layout delivered
- `skills/gold_wire/SKILL.md`
- `wirewatch/` python package (`src/wirewatch`)
- `wirewatch.py` runner
- `sources.yaml`, `keywords.yaml`, `thresholds.yaml`
- SQLite schema + migration notes doc
- `docker-compose.yml`
- OpenClaw cron add commands doc
- User setup checklist doc

## Alert format (enforced, ~8 lines)
1. Timestamp ET
2. HEADLINE (verbatim)
3. Why it matters for GC
4. Expected bias + confidence
5. Cross-asset confirmers (DXY, UST yields, JPY, oil, equities)
6. What I'd watch next (1-2 bullets)

## Run
```bash
cd C:/Users/carlo/Repositories/gold-wirewatch
uv sync --extra dev
copy .env.example .env
uv run python wirewatch.py status
uv run python wirewatch.py poll-once
uv run python wirewatch.py run
```

## Quality gates
```bash
uv run ruff check .
uv run mypy src
uv run pytest
```
