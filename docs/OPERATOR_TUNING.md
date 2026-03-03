# Gold WireWatch — Operator Tuning Guide

## Alert Pipeline Overview

```
Feed Items → Score → Critical Bypass Check → Score Gate → Evidence Gate → Dedup → Alert
```

### Pipeline Stages

1. **Scoring** (`scoring.py`): keyword-based relevance + severity scores
2. **Critical Bypass** (`critical_bypass.py`): pattern-matched high-importance events skip gating
3. **Score Gate**: relevance ≥ threshold AND severity ≥ threshold (or geo/policy watch)
4. **Evidence Gate** (`evidence_gate.py`): deterministic state machine using confirmers + source tier
5. **Dedup** (`dedupe.py`, `suppression.py`): delta-only, content-level, and delivery-level dedup
6. **Alert Delivery**: structured payload via OpenClaw

---

## Tunable Thresholds

### Evidence Gate (`evidence_gate.py`)

| Constant | Default | Description |
|---|---|---|
| `MIN_FRESH_CONFIRMERS` | 3 | Minimum fresh confirmers for Actionable/Conditional |
| `MAX_FRESH_SKEW_SECONDS` | 120 | Max time spread among strict-fresh confirmers |
| `CONFIDENCE_CAP_INSUFFICIENT` | 0.40 | Confidence cap when fresh < MIN_FRESH |
| `ACTIONABLE_SEVERITY_THRESHOLD` | 0.75 | Severity floor for Actionable Long |
| `ACTIONABLE_RELEVANCE_THRESHOLD` | 0.55 | Relevance floor for Actionable Long |
| `CONDITIONAL_SEVERITY_THRESHOLD` | 0.45 | Severity floor for Conditional |
| `CONDITIONAL_RELEVANCE_THRESHOLD` | 0.45 | Relevance floor for Conditional |
| `GEO_POLICY_SEVERITY_THRESHOLD` | 0.30 | Severity floor for geo/policy Conditional |
| `NEUTRAL_SEVERITY_CEILING` | 0.20 | Below this → Neutral (no alert) |

### Per-Confirmer Freshness Policy (`confirmers.py`)

Each confirmer has its own `FreshnessPolicy` controlling how staleness is evaluated:

| Confirmer | `max_age_seconds` | `delayed_acceptable` | `delayed_max_age_seconds` | Notes |
|---|---|---|---|---|
| DXY | 300 | No | — | Strict real-time |
| US10Y | 300 | **Yes** | **900** | FRED/delayed sources tolerated up to 15min |
| OIL | 300 | No | — | Strict (NYMEX CL/RB) |
| USDJPY | 300 | No | — | Strict FX |
| EQUITIES | 300 | No | — | Strict (ES/SPY) |

**How it works:**
- A reading within `max_age_seconds` → classified as **FRESH** (`within_strict_window`)
- A reading between `max_age_seconds` and `delayed_max_age_seconds` (when `delayed_acceptable=True`) → classified as **FRESH** (`delayed_acceptable`)
- A delayed-acceptable reading counts toward `fresh_count` for evidence gating
- Synchronization check uses relaxed skew (5× `MAX_FRESH_SKEW_SECONDS`) when delayed readings are included

**Customizing:** Edit `DEFAULT_FRESHNESS_POLICIES` in `confirmers.py` to change thresholds.

### SCID Local Source Routing

When Sierra Chart is running locally, SCID files can be used as primary data sources (lowest latency). Configure via `ScidConfig`:

```python
scid = ScidConfig(
    dxy="C:/SierraChart/Data/DX-Y.NYB.scid",
    us10y="C:/SierraChart/Data/ZN.scid",
    oil="C:/SierraChart/Data/CL.scid",
)
engine = ConfirmerEngine.with_live_providers(scid=scid)
```

SCID providers are tried first; Yahoo/Stooq/FRED remain as fallback chain. Source routing is recorded in each reading's `source_label` and `freshness_reason`.

### Cooldowns (`service.py`)

| Constant | Default | Description |
|---|---|---|
| `GEO_WATCH_COOLDOWN_SECONDS` | 600 | Seconds between geo-watch alerts |
| `POLICY_WATCH_COOLDOWN_SECONDS` | 900 | Seconds between policy-watch alerts |

### Dedup (`dedupe.py`)

| Parameter | Default | Description |
|---|---|---|
| `ContentDeduplicator.cooldown_seconds` | 600.0 | Content-level dedup window |
| `DeliveryDeduplicator.ttl_seconds` | 1800.0 | Delivery replay guard window |

---

## Decision State Machine

```
Scores → decide_from_scores() → raw DecisionState → apply_evidence_gate() → final DecisionState
```

**Raw states** (from scores only):
- **Actionable Long**: severity ≥ 0.75 AND relevance ≥ 0.55
- **Conditional**: severity ≥ 0.45 AND relevance ≥ 0.45 (or severity ≥ 0.30 with geo/policy hit)
- **Fade**: severity in [0.20, threshold) without qualifying conditions
- **Neutral**: severity < 0.20

**Gating rules** (evidence gate):
- Fresh confirmers < 3 → Actionable demoted to Headline Only, Conditional to Insufficient Tape
- Confirmers not synchronized (spread > 120s) → Insufficient Tape
- Single tier-C source → Actionable/Conditional demoted to Headline Only
- Confidence capped at 0.40 when fresh confirmers < 3

---

## Critical Bypass

Events matching critical patterns bypass all gating and emit immediately. Categories:

| Category | Example Triggers |
|---|---|
| `hormuz_shipping` | "Iran seizes tanker in Strait of Hormuz" |
| `us_force_posture` | "Pentagon deploys carrier strike group to Gulf" |
| `confirmed_strike` | "Iran confirms missile strike kills 3 in Syria" |
| `embassy_closure` | "US embassy ordered to evacuate" |
| `direct_military` | "Israel confirms retaliatory strike on IRGC" |

Critical bypass events:
- Skip confirmer gating entirely
- Preserve raw decision state
- Still go through content/delivery dedup (but as `critical_bypass` trigger path)
- Are tracked separately in metrics (`critical_bypass_fired`)

---

## Dedup: Material Delta Filter

An event within cooldown is only re-emitted if a **material delta** is detected:
- Source tier upgrade (C→B, B→A)
- Decision state escalation (Fade→Conditional, Conditional→Actionable)
- Fresh confirmer bucket upgrade (0→1-2, 1-2→3, 3→4+)

---

## Metrics Endpoint

`GET /metrics` returns:
```json
{
  "batches": 42,
  "alerts_sent": 5,
  "suppressed_delta": 12,
  "suppressed_content": 8,
  "suppressed_delivery": 3,
  "insufficient_tape_snapshots": 30,
  "critical_bypass_fired": 1,
  "duplicate_suppression_rate": 0.8214,
  "confirmer_health": {
    "DXY": {"fetches": 42, "fresh": 40, "stale": 1, "unavailable": 1, "delayed_acceptable": 0, "availability_pct": 97.6, "last_status": "fresh", "last_source": "yahoo:DX-Y.NYB", "last_reason": "within_strict_window"},
    "US10Y": {"fetches": 42, "fresh": 38, "stale": 2, "unavailable": 2, "delayed_acceptable": 12, "availability_pct": 95.2, "last_status": "fresh", "last_source": "fred:DFII10", "last_reason": "delayed_acceptable"},
    "OIL": {"fetches": 42, "fresh": 41, "stale": 0, "unavailable": 1, "delayed_acceptable": 0, "availability_pct": 97.6, "last_status": "fresh", "last_source": "yahoo:CL=F", "last_reason": "within_strict_window"},
    "USDJPY": {"fetches": 42, "fresh": 42, "stale": 0, "unavailable": 0, "delayed_acceptable": 0, "availability_pct": 100.0, "last_status": "fresh", "last_source": "yahoo:JPY=X", "last_reason": "within_strict_window"},
    "EQUITIES": {"fetches": 42, "fresh": 39, "stale": 2, "unavailable": 1, "delayed_acceptable": 0, "availability_pct": 97.6, "last_status": "fresh", "last_source": "yahoo:ES=F", "last_reason": "within_strict_window"}
  }
}
```

**Key ratio**: `duplicate_suppression_rate` = total_suppressed / total_events. High (>0.8) is normal during active geo events. If `insufficient_tape_snapshots` is consistently high, confirmer providers may be down.

**Per-confirmer health**: Check `confirmer_health` for per-feed diagnostics. Key signals:
- `delayed_acceptable > 0` on US10Y is normal (FRED data is daily, often 6+ hours old)
- `unavailable` consistently high on any feed → check that provider endpoint is reachable
- `availability_pct < 80%` → investigate network or upstream issues
- `last_reason` shows exactly why the last reading was classified as it was

---

## Rollout Checklist

- [ ] Run full test suite: `uv run python -m pytest tests/ --no-cov`
- [ ] Verify critical bypass patterns don't over-fire on benign headlines
- [ ] Monitor `critical_bypass_fired` metric after deploy — should be rare (< 1/day normally)
- [ ] Monitor `insufficient_tape_snapshots` — should decrease significantly with per-confirmer freshness
- [ ] Check `confirmer_health.US10Y.delayed_acceptable` — should show non-zero during market hours
- [ ] Review `duplicate_suppression_rate` — should be 0.5-0.9 during active events
- [ ] Confirm existing thresholds.yaml still loads correctly
- [ ] Verify backward compatibility: no config changes needed for existing deployments
- [ ] If using SCID local sources, configure `ScidConfig` paths and verify `last_source` shows `scid:*`
- [ ] Restart service: `Get-ScheduledTaskInfo -TaskName "Gold WireWatch"`
