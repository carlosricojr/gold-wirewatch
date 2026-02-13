# User Setup Checklist (short)

1. **Enable OpenClaw hooks + token**
   - Ensure OpenClaw gateway is running and `/hooks/agent` is reachable.
   - Create/obtain Bearer token.

2. **Put secrets in env vars**
   - Copy `.env.example` -> `.env`
   - Set `OPENCLAW_TOKEN`
   - Set optional paid toggles (`ENABLE_IBKR_REUTERS`, `ENABLE_FINANCIALJUICE_PRO`)

3. **Optional paid sources**
   - IBKR Reuters: enable only if subscribed in IBKR account/TWS.
   - FinancialJuice PRO: add legal endpoint/credentials only if subscribed.

4. **Test procedure**
   - Simulate headline: `uv run python wirewatch.py poll-once`
   - Simulate price spike:
     - POST `{"symbol":"GC1!","previous":2900,"current":2909,"window_seconds":120}` to `http://127.0.0.1:8787/webhook/market-move`
   - Confirm OpenClaw receives alert in required 6-section format.
