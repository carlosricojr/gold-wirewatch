# OpenClaw cron add commands (copy/paste)

> If your local CLI differs, run `openclaw help` and adjust flags.

```bash
openclaw cron add --id gold-pre-session --schedule "50 17 * * *" --tz "America/New_York" --command "cd C:/Users/carlo/Repositories/gold-wirewatch && uv run python scripts/tasks.py pre-session-risk-map"

openclaw cron add --id gold-roll-15m-evening --schedule "*/15 18-23 * * *" --tz "America/New_York" --command "cd C:/Users/carlo/Repositories/gold-wirewatch && uv run python scripts/tasks.py rolling-digest --window-min 15"

openclaw cron add --id gold-roll-15m-midnight --schedule "*/15 0-1 * * *" --tz "America/New_York" --command "cd C:/Users/carlo/Repositories/gold-wirewatch && uv run python scripts/tasks.py rolling-digest --window-min 15"

openclaw cron add --id gold-post-session --schedule "5 1 * * *" --tz "America/New_York" --command "cd C:/Users/carlo/Repositories/gold-wirewatch && uv run python scripts/tasks.py post-session-recap"

openclaw cron add --id gold-weekly-health --schedule "0 16 * * 0" --tz "America/New_York" --command "cd C:/Users/carlo/Repositories/gold-wirewatch && uv run python scripts/tasks.py weekly-health-check"
```
