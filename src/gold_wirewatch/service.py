from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from .alert_payload import build_alert_payload, build_market_move_payload
from .alerts import format_market_move_alert, format_news_alert
from .config import FeedConfig, Settings, load_thresholds
from .confirmers import ConfirmerEngine
from .evidence_gate import DecisionState, apply_evidence_gate, decide_from_scores
from .dedupe import ContentDeduplicator, DeliveryDeduplicator, canonicalize_title, event_fingerprint
from .feeds import poll_feed, stable_item_key
from .models import FeedItem
from .openclaw_client import OpenClawClient
from .scheduler import current_poll_interval
from .scoring import KeywordMap, geo_watch_reasons, load_keywords, policy_watch_reasons, score_item
from .source_tier import corroborate
from .storage import Storage
from .suppression import SuppressionState, suppression_key


class MarketWebhookPayload(BaseModel):
    """Pydantic model for incoming market-move webhook requests."""

    symbol: str = "GC1!"
    previous: float | None = None
    current: float | None = None
    window_seconds: int = 120


def _bucket_fresh_for_dedupe(count: int) -> str:
    if count >= 4:
        return "4+"
    if count >= 3:
        return "3"
    if count >= 1:
        return "1-2"
    return "0"


GEO_WATCH_COOLDOWN_SECONDS = 600
POLICY_WATCH_COOLDOWN_SECONDS = 900


@dataclass
class ServiceMetrics:
    """Counters tracking service activity and suppression statistics."""

    batches: int = 0
    alerts_sent: int = 0
    suppressed_delta: int = 0
    suppressed_content: int = 0
    suppressed_delivery: int = 0
    insufficient_tape_snapshots: int = 0


class WireWatchService:
    """Core polling service that fetches feeds, scores items, and dispatches alerts."""

    def __init__(
        self,
        settings: Settings,
        feeds: list[FeedConfig],
        storage: Storage,
        keywords: KeywordMap,
        confirmer_engine: ConfirmerEngine | None = None,
    ) -> None:
        self.settings = settings
        self.feeds = feeds
        self.storage = storage
        self.keywords = keywords
        self.oc = OpenClawClient(settings)
        self.enabled = True
        self.confirmer_engine = confirmer_engine or ConfirmerEngine()
        self.suppression = SuppressionState()
        self.content_dedup = ContentDeduplicator(cooldown_seconds=600.0)
        self.delivery_dedup = DeliveryDeduplicator(ttl_seconds=1800.0)
        self.metrics = ServiceMetrics()

    def _reload_runtime_config(self) -> None:
        try:
            self.keywords = load_keywords(self.settings.keywords_path)
            thresholds = load_thresholds(self.settings.thresholds_path)
            self.settings.relevance_threshold = thresholds.relevance_threshold
            self.settings.severity_threshold = thresholds.severity_threshold
            self.settings.market_move_delta_usd = thresholds.market_move_delta_usd
            self.settings.market_move_window_seconds = thresholds.market_move_window_seconds
        except Exception:
            # Keep last-known-good runtime config if reload fails.
            pass

    def process_items(self, items: list[FeedItem]) -> int:
        """Score, gate, deduplicate, and alert on a batch of feed items. Returns alert count."""
        fired = 0
        self.metrics.batches += 1
        # Fetch confirmers once per batch for efficiency
        confirmers = self.confirmer_engine.fetch_all()
        if confirmers.fresh_count < 3:
            self.metrics.insufficient_tape_snapshots += 1

        for item in items:
            item_key = stable_item_key(item)
            if self.storage.is_seen(item_key):
                continue
            score = score_item(item, self.keywords)
            self.storage.save_item(item_key, item, score)
            meets_main_gate = (
                score.relevance_score >= self.settings.relevance_threshold
                and score.severity_score >= self.settings.severity_threshold
            )
            geo_reasons = geo_watch_reasons(item)
            policy_reasons = policy_watch_reasons(item)
            geo_gate = bool(geo_reasons)
            policy_gate = bool(policy_reasons)
            should_fire = meets_main_gate or geo_gate or policy_gate
            if geo_gate and self.storage.has_recent_event("geo_watch", GEO_WATCH_COOLDOWN_SECONDS):
                should_fire = False
            if policy_gate and self.storage.has_recent_event("policy_watch", POLICY_WATCH_COOLDOWN_SECONDS):
                should_fire = False

            if should_fire:
                if meets_main_gate:
                    trigger_path = "main_gate"
                elif geo_gate:
                    trigger_path = "geo_watch"
                else:
                    trigger_path = "policy_watch"

                # --- Phase-1 hardening: source tier + evidence gate ---
                source_meta = corroborate([item.source])
                raw_decision = decide_from_scores(
                    score.relevance_score, score.severity_score,
                    geo_hit=geo_gate, policy_hit=policy_gate,
                )
                verdict = apply_evidence_gate(source_meta, confirmers, raw_decision)

                # Delta-only suppression
                sup_key = suppression_key(source_meta, confirmers, verdict)
                if self.suppression.should_suppress(trigger_path, sup_key):
                    self.metrics.suppressed_delta += 1
                    continue
                self.suppression.record(trigger_path, sup_key)

                # Content-level dedupe (near-duplicate titles)
                canon = canonicalize_title(item.title)
                fp = event_fingerprint(canon)
                fresh_bucket = _bucket_fresh_for_dedupe(confirmers.fresh_count)
                if self.content_dedup.should_suppress(
                    fp, source_meta.tier.value, verdict.decision.value, fresh_bucket,
                ):
                    self.metrics.suppressed_content += 1
                    continue
                self.content_dedup.record(
                    fp, source_meta.tier.value, verdict.decision.value, fresh_bucket,
                )

                # Delivery-level dedupe (replay guard)
                delivery_id = DeliveryDeduplicator.make_delivery_id(fp, sup_key)
                if self.delivery_dedup.is_duplicate(delivery_id):
                    self.metrics.suppressed_delivery += 1
                    continue
                self.delivery_dedup.record(delivery_id)

                # Build structured payload
                payload = build_alert_payload(
                    item, score, source_meta, verdict, confirmers,
                    trigger_path, self.settings.timezone,
                )

                # Send structured payload (compact format as text, full dict as context)
                context = payload.to_dict()
                idempotency_key = hashlib.sha256(
                    f"{item_key}|{trigger_path}|{verdict.decision.value}|{sup_key}".encode("utf-8")
                ).hexdigest()[:24]
                context["idempotency_key"] = idempotency_key
                self.oc.trigger(
                    text=payload.format_compact(),
                    context=context,
                )
                self.metrics.alerts_sent += 1
                if trigger_path in {"geo_watch", "policy_watch"}:
                    self.storage.save_event(
                        trigger_path,
                        json.dumps({"source": item.source, "title": item.title, "url": item.url}),
                    )
                fired += 1
        return fired

    def poll_once(self) -> int:
        """Run one polling cycle across all feeds. Returns total alerts fired."""
        if not self.enabled:
            return 0
        self._reload_runtime_config()
        fired = 0
        with httpx.Client() as client:
            for feed in self.feeds:
                try:
                    items = poll_feed(client, feed, self.settings)
                except (httpx.HTTPError, ValueError):
                    continue
                fired += self.process_items(items)
        return fired

    def run_forever(self) -> None:
        """Poll feeds in an infinite loop with adaptive sleep intervals."""
        while True:
            self.poll_once()
            interval = current_poll_interval(
                now=datetime.now(UTC),
                tz_name=self.settings.timezone,
                start_hour=self.settings.active_window_start_hour,
                end_hour=self.settings.active_window_end_hour,
                active_seconds=self.settings.poll_interval_active_seconds,
                idle_seconds=self.settings.poll_interval_idle_seconds,
            )
            time.sleep(interval)

    def handle_market_move(
        self,
        symbol: str,
        previous: float | None,
        current: float | None,
        window: int,
    ) -> bool:
        """Process a market-move webhook event. Returns True if an alert was sent."""
        if symbol != self.settings.market_move_symbol:
            return False
        if previous is None or current is None:
            return False
        delta = abs(current - previous)
        enough_delta = delta >= self.settings.market_move_delta_usd
        quick_enough = window <= self.settings.market_move_window_seconds
        if not (enough_delta and quick_enough):
            return False
        raw_payload = {
            "symbol": symbol,
            "delta": delta,
            "window": window,
            "current": current,
        }
        self.storage.save_event("market_move", json.dumps(raw_payload))

        # Phase-1 hardening for market moves
        confirmers = self.confirmer_engine.fetch_all()
        raw_decision = DecisionState.CONDITIONAL if delta >= 12.0 else DecisionState.FADE
        source_meta = corroborate(["market_data"])
        verdict = apply_evidence_gate(source_meta, confirmers, raw_decision)

        payload = build_market_move_payload(
            symbol, delta, window, current, confirmers, verdict, self.settings.timezone,
        )
        context = payload.to_dict()
        idempotency_key = hashlib.sha256(
            f"market:{symbol}:{window}:{previous}:{current}:{verdict.decision.value}".encode("utf-8")
        ).hexdigest()[:24]
        context["idempotency_key"] = idempotency_key
        self.oc.trigger(
            text=payload.format_compact(),
            context=context,
        )
        self.metrics.alerts_sent += 1
        return True


def create_webhook_app(service: WireWatchService) -> FastAPI:
    """Create the FastAPI webhook application with health, metrics, and market-move endpoints."""
    app = FastAPI(title="gold-wirewatch-webhook")

    @app.get("/health")
    def health() -> dict[str, str]:
        """Return basic health check response."""
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics() -> dict[str, float | int]:
        """Return service metrics including suppression and duplicate rates."""
        total_suppressed = (
            service.metrics.suppressed_delta
            + service.metrics.suppressed_content
            + service.metrics.suppressed_delivery
        )
        total_events = service.metrics.alerts_sent + total_suppressed
        duplicate_rate = (total_suppressed / total_events) if total_events else 0.0
        return {
            "batches": service.metrics.batches,
            "alerts_sent": service.metrics.alerts_sent,
            "suppressed_delta": service.metrics.suppressed_delta,
            "suppressed_content": service.metrics.suppressed_content,
            "suppressed_delivery": service.metrics.suppressed_delivery,
            "insufficient_tape_snapshots": service.metrics.insufficient_tape_snapshots,
            "duplicate_suppression_rate": round(duplicate_rate, 4),
        }

    @app.post("/webhook/market-move")
    def market_move(payload: MarketWebhookPayload) -> dict[str, object]:
        """Handle incoming market-move webhook POST."""
        try:
            triggered = service.handle_market_move(
                symbol=payload.symbol,
                previous=payload.previous,
                current=payload.current,
                window=payload.window_seconds,
            )
            return {"ok": True, "triggered": triggered}
        except Exception as exc:
            return {"ok": False, "triggered": False, "error": str(exc)}

    return app


def start_service_with_webhook(service: WireWatchService) -> None:
    """Start the polling service in a background thread and serve the webhook API."""
    import uvicorn

    thread = threading.Thread(target=service.run_forever, daemon=True)
    thread.start()
    uvicorn.run(
        create_webhook_app(service),
        host=service.settings.webhook_host,
        port=service.settings.webhook_port,
        log_level="info",
    )
