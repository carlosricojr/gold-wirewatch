# Sprint 08 Review — Geo-Event Coverage & Feed Reliability

## Objective
Increase capture of gold-relevant geopolitical shocks (e.g., Iran/carrier escalations) while preserving low false-positive noise and legal/public-source constraints.

## Why now
Empirical validation showed:
- Price-move webhook path works end-to-end.
- News polling ingests data, but current source mix is weak for fast geopolitical headlines.
- Existing enabled feeds include multiple unreliable/dead endpoints (timeouts/404), reducing real signal coverage.

## In Scope
1. Replace/add **public, parseable RSS feeds** with empirical endpoint checks.
2. Add **geo-watch classification rule** for conditionally material geopolitical events.
3. Add keyword coverage for geopolitical + macro link terms in `keywords.yaml`.
4. Add tests for geo-watch logic and service behavior.
5. Run empirical checks for feed reachability/parsing and ingestion behavior.

## Out of Scope
- Paid/private wire integrations (Reuters paid terminals, FinancialJuice PRO APIs).
- Model-based NLP classifier.
- Strategy PnL backtest changes.

## Acceptance Criteria
- A1: `uv run pytest` passes.
- A2: Enabled feeds return HTTP 200 and parse at least one entry during validation run.
- A3: Geo-watch rule triggers for synthetic Iran/carrier headline even if below main relevance threshold.
- A4: Non-material generic headlines do not trigger geo-watch alerts.
- A5: Existing market-move webhook trigger behavior remains unchanged.

## TDD-First Test Plan
1. Add unit tests for `geo_watch_reasons(...)` in scoring.
2. Add service test that verifies alert firing on geo-watch classification with thresholds intentionally not met.
3. Keep existing webhook/trigger tests as regression guard.

## Risks
- R1: Added feeds may be noisy (false positives).
- R2: Some feeds may degrade/ratelimit over time.
- R3: Geo keywords may over-trigger non-material military headlines.

## Mitigations
- M1: Use deterministic geo-watch rule requiring multi-term corroboration.
- M2: Preserve dedupe by stable item key and keep current main thresholds unchanged.
- M3: Keep sources configurable in YAML for rapid enable/disable.

## Go / No-Go Gates
- **Go** if A1–A5 pass.
- **No-Go** if tests fail, feeds are unreachable/non-parseable, or alert spam materially increases in smoke test.
