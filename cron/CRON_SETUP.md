# Cron setup (ready-to-run, not applied)

Timezone: America/New_York (`CRON_TZ=America/New_York`)

```cron
CRON_TZ=America/New_York

# Daily 5:50pm ET pre-session risk map
50 17 * * * cd /c/Users/carlo/Repositories/gold-wirewatch && uv run python scripts/tasks.py pre-session-risk-map

# Every 15 min from 6:00pm-1:00am ET rolling digest if new relevant info
*/15 18-23 * * * cd /c/Users/carlo/Repositories/gold-wirewatch && uv run python scripts/tasks.py rolling-digest --window-min 15
*/15 0-1 * * * cd /c/Users/carlo/Repositories/gold-wirewatch && uv run python scripts/tasks.py rolling-digest --window-min 15

# Daily 1:05am ET post-session recap
5 1 * * * cd /c/Users/carlo/Repositories/gold-wirewatch && uv run python scripts/tasks.py post-session-recap

# Weekly Sunday 4:00pm ET source/keyword/threshold health check
0 16 * * 0 cd /c/Users/carlo/Repositories/gold-wirewatch && uv run python scripts/tasks.py weekly-health-check
```

For Windows Task Scheduler, map each entry to equivalent trigger and command.
