# Sprint: Market-Moving Catalyst Lane

## Objective
Add a deterministic "market-moving catalyst" detection lane alongside the existing geo/policy lanes. This lane catches AI/hyperscaler capex fragility, semiconductor demand shocks, and major corporate capex pullbacks that transmit to NQ/ES/risk assets.

## Problem Statement
Current wirewatch is over-indexed on geo/oil gating. Headlines like "Oracle cuts OpenAI Texas plant investment" (AI hyperscaler balance-sheet fragility) are invisible to the scanner despite being market-moving for equities/macro.

## In Scope
1. New `catalyst_watch_reasons()` function in `scoring.py` with deterministic keyword patterns
2. New catalyst keyword terms in `keywords.yaml`
3. New `CatalystCategory` enum and patterns (analogous to `CriticalCategory` for critical bypass)
4. Integration into `service.py` `process_items()` as a new trigger path `catalyst_watch`
5. Cooldown for catalyst alerts (separate from geo/policy cooldowns)
6. Decision-state integration via existing evidence gate (no new states needed)
7. Test suite covering all new behavior

## Out of Scope
- LLM-based classification (rule-based only)
- New confirmer providers (use existing 5 confirmers)
- New feed sources (use existing RSS feeds)
- Changes to alert payload format (reuse existing compact format)
- New decision states (reuse existing state machine)

## Catalyst Categories & Patterns

### Category: AI/Hyperscaler Capex Fragility
- Triggers: capex + (cut/delay/cancel/reduce/slash/pullback/suspend)
- Entities: OpenAI, Google, Microsoft, Amazon, Meta, Oracle, NVIDIA, hyperscaler, data center
- Transmission: NQ↓, NVDA↓, risk-off

### Category: Semiconductor Demand Shock
- Triggers: semiconductor/chip + (demand/guidance/downgrade/miss/warning/shortfall)
- Entities: NVIDIA, TSMC, AMD, Intel, Samsung, ASML, chip
- Transmission: NQ↓, SOX↓

### Category: Corporate Capex Pullback (Index-Level)
- Triggers: capex/investment/spending + (cut/freeze/halt/reduce/pullback)
- Context: multiple companies, sector-wide, broad-based
- Transmission: ES↓, risk-off

### Category: Financing/Balance-Sheet Stress
- Triggers: credit/debt/covenant/liquidity + (stress/crisis/default/downgrade/breach)
- Context: tech, hyperscaler, major corporate
- Transmission: HY spreads↑, ES↓

## Classification Schema Extension
Each catalyst match produces tags like `catalyst:ai_capex_fragility`, `catalyst:semi_demand_shock`, etc. These integrate into existing `ScoreResult.reasons` list.

## Acceptance Criteria
1. ✅ Headline "Oracle cuts OpenAI Texas data center investment amid cost concerns" triggers catalyst lane
2. ✅ Headline "NVIDIA warns on data center demand, cuts capex guidance" triggers catalyst lane
3. ✅ Existing geo headlines still trigger geo lane (no regression)
4. ✅ Catalyst alerts go through same evidence gate, dedupe, suppression pipeline
5. ✅ Catalyst cooldown prevents spam (separate 900s cooldown)
6. ✅ Source-tier gate still enforced (Tier C single-source gets gated)
7. ✅ Delta-only suppression still works for catalyst alerts

## TDD Plan
1. Test `catalyst_watch_reasons()` catches target headlines → FAIL
2. Implement `catalyst_watch_reasons()` → PASS
3. Test service integration fires on catalyst headlines → FAIL
4. Wire into `service.py` → PASS
5. Test geo regression (existing headlines still work) → should already PASS
6. Test cooldown for catalyst lane → FAIL then PASS
7. Test dedupe/suppression for catalyst lane → should already PASS

## Risks
- False positives from broad terms like "investment" or "spending" (mitigated by requiring entity co-occurrence)
- Keyword overlap with existing policy lane (mitigated by distinct trigger paths)

## Go/No-Go Gates
- Gate 1: Research audit complete → GO
- Gate 2: Adversarial review of plan passes → GO
- Gate 3: All tests pass, no regressions → GO
- Gate 4: Adversarial final review passes → MERGE
