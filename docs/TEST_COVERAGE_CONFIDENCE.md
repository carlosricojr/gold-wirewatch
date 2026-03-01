# Test Coverage & Confidence Report

Generated: 2026-03-01

## Overall Coverage: 96% (787 statements, 34 missed)

## Unit-Tested Deterministically

| Module | Coverage | What's Tested |
|--------|----------|---------------|
| `confirmers.py` | 98% | Stub/Static/Fallback/Yahoo providers, parser with fixture payloads, timeout/HTTP error/malformed JSON failure paths, fallback chain ordering, factory functions, engine construction |
| `evidence_gate.py` | 100% | All decision state transitions, MIN_FRESH_CONFIRMERS gate, tier C single-unverified gate, decide_from_scores all branches |
| `suppression.py` | 97% | Same-key suppression, different-key passthrough, clear/clear-all, suppression_key deterministic hashing |
| `source_tier.py` | 100% | Tier classification (A/B/C), corroboration states, multi-source/single-verified/single-unverified |
| `scoring.py` | 100% | Keyword matching, geo_watch_reasons, policy_watch_reasons |
| `alert_payload.py` | 98% | build_alert_payload, build_market_move_payload, format_compact, to_dict, all invalidator branches |
| `storage.py` | 100% | SQLite CRUD, dedup, recent event queries |
| `config.py` | 100% | Settings loading, feed config, threshold loading |
| `models.py` | 100% | All dataclasses |

## Service-Level Tests (Deterministic, No Network)

- **Suppression branch**: duplicate trigger on unchanged state key is suppressed; changed state key fires
- **market_move false paths**: wrong symbol, None previous/current, insufficient delta, window too long
- **market_move true path**: valid move triggers with evidence gate applied, delta >= 12 gated correctly
- **Webhook exception path**: exception in handle_market_move returns `{"ok": false, "error": "..."}` 
- **poll_once exception handling**: disabled service returns 0, feed errors skip to next, config reload failure keeps last-good
- **End-to-end**: synthetic headline → score → source tier → evidence gate → compact payload assertions

## Live Provider Architecture (New)

- `YahooFinanceProvider`: HTTP provider using Yahoo Finance v8 chart API (no API key)
- `FallbackProvider` chain for each confirmer: primary symbol → alternate → stub
- Provider mapping:
  - DXY: `DX-Y.NYB` → `UUP` → stub
  - US10Y: `^TNX` → stub
  - OIL: `CL=F` → `BZ=F` → stub
  - USDJPY: `JPY=X` → stub
  - EQUITIES: `ES=F` → `SPY` → stub
- All parsers tested with fixture JSON (no live network in tests)
- All failure paths tested: timeout, HTTP 429, connection error, malformed JSON

## Integration-Risky (Not Tested in CI)

| Risk Area | Why | Mitigation |
|-----------|-----|------------|
| Yahoo Finance endpoint availability | Rate limits, format changes, geo-blocking | Fallback chain + stub; parse tests use fixture payloads |
| Yahoo Finance response format changes | API is unofficial, could change without notice | Parser tests document exact expected shape; will fail fast |
| Market hours staleness | Outside market hours, `regularMarketTime` may be old → STALE status | Handled by FRESHNESS_SECONDS check in parser |
| Network latency in live deployment | 5s timeout per provider; 5 providers = up to 25s worst case | Sequential fetch acceptable for 5-min poll cycle |
| OpenClaw client delivery | `trigger()` is mocked in all tests | Covered by openclaw_client unit tests |
| SQLite concurrent access | Service is single-threaded poll loop | No issue in current architecture |

## Tested vs Untested in Live Market Conditions

### ✅ Tested Deterministically
- Confirmer data parsing (all 5 asset classes)
- Fallback chain behavior (primary fails → secondary → stub)
- Evidence gate decision logic (all state transitions)
- Suppression dedup (same state = suppress, changed state = fire)
- Alert payload construction and formatting
- Market move threshold logic (all 5 false paths + true path)
- Webhook error handling
- Config hot-reload with failure fallback

### ⚠️ Untested in Live Conditions (Requires Manual/Integration Testing)
- Actual Yahoo Finance HTTP responses in production
- Real market-hours vs after-hours staleness behavior  
- True end-to-end: live RSS feed → score → live confirmers → OpenClaw delivery
- Webhook under concurrent load
- Long-running `run_forever` stability
- Network partition recovery (provider returns after extended outage)

## Module-Level Weak Spots

| Module | Coverage | Gap |
|--------|----------|-----|
| `cli.py` | 67% | `run` and `serve` commands untested (require running services) |
| `service.py` | 94% | `run_forever` loop untested (infinite loop); lines 149-159 (run_forever body) |
| `alerts.py` | 90% | Legacy format functions partially covered |
| `feeds.py` | 93% | Some RSS parsing edge cases |
