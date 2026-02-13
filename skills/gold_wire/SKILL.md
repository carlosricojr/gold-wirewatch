# Gold Wire Skill

Commands:
- `/wire now` -> `uv run python wirewatch.py poll-once`
- `/wire digest 15m` -> summarize recent `seen_items` over 15m
- `/wire digest 2h` -> summarize recent `seen_items` over 120m
- `/wire on` -> run/start service mode
- `/wire off` -> stop service mode
- `/wire status` -> `uv run python wirewatch.py status`

## Alert contract (max ~10 lines)
1. Timestamp ET
2. HEADLINE (verbatim)
3. Why it matters for GC (rates/USD/risk/geopolitics/China)
4. Expected bias + confidence
5. Cross-asset confirmers (DXY, UST yields, JPY, oil, equities)
6. What I'd watch next (1-2 bullets)

Data paths:
- `sources.yaml`
- `keywords.yaml`
- `thresholds.yaml`
- `data/wirewatch.db`
