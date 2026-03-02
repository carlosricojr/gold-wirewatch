# Dedupe Architecture — WireWatch Alert Pipeline

## Problem
Duplicate/near-duplicate user pings from:
1. **Content-level**: Same event rephrased across feeds or live-update title churn
2. **Delivery-level**: Replayed hooks from heartbeat/system replay behavior

## Architecture

### Three-Layer Dedupe Stack

```
Feed Items
    │
    ▼
[Layer 1] Exact Dedupe ─── stable_item_key (existing: source|guid|url|published)
    │
    ▼
[Layer 2] Content Dedupe ─── canonicalize title → event fingerprint → cooldown TTL
    │
    ▼
[Layer 3] Delta-State Dedupe ─── suppression_key (existing: tier|corroboration|confirmers|decision)
    │
    ▼
[Layer 4] Delivery Dedupe ─── delivery_id guard (session-scoped, prevents replay)
    │
    ▼
  Alert Emitted
```

### Layer 2: Content Dedupe (NEW)

**Title Canonicalization** (`canonicalize_title`):
- Lowercase, collapse whitespace
- Strip known boilerplate wrappers ("here's the latest", "breaking:", "update:", "live updates" etc.)
- Normalize punctuation (strip trailing ellipsis, quotes, dashes)
- Strip leading/trailing articles

**Event Fingerprint** (`event_fingerprint`):
- Extract key tokens from canonicalized title (sorted, deduped)
- SHA256 hash of sorted tokens → 16-char hex fingerprint
- Keyed by source-agnostic content (same event from Reuters vs Bloomberg → same fingerprint)

**Cooldown TTL**:
- After emitting for a fingerprint, suppress same fingerprint for configurable TTL (default 600s)
- Material delta bypasses cooldown (tier upgrade, decision state change, confirmer alignment shift)

### Layer 4: Delivery Dedupe (NEW)

**Delivery ID Guard** (`DeliveryDeduplicator`):
- Each alert emission generates a delivery_id = hash(event_fingerprint + suppression_key)
- Tracks recent delivery_ids with TTL
- Prevents identical deliveries from system replay/heartbeat re-triggering

### Material Delta Passthrough

Even within cooldown, alerts pass through if:
- Source tier upgrades (C→B, B→A)
- Decision state changes materially (Headline only → Conditional, Conditional → Actionable)
- Confirmer fresh count crosses threshold (0→3+)

## Module Boundaries

- `dedupe.py` — All new dedupe logic (canonicalization, fingerprinting, cooldown, delivery guard)
- `suppression.py` — Existing delta-state suppression (unchanged)
- `service.py` — Integration point (adds Layer 2 + Layer 4 checks)

## No-Tech-Debt Rationale

- Pure functions for canonicalization/fingerprinting (easy to test, no side effects)
- TTL-based expiry via simple dict + timestamp (no external deps)
- Clear separation: content dedupe vs state dedupe vs delivery dedupe
- All layers independent and individually testable
