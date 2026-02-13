from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from .alerts import format_market_move_alert, format_news_alert
from .config import FeedConfig, Settings
from .feeds import poll_feed, stable_item_key
from .models import FeedItem
from .openclaw_client import OpenClawClient
from .scheduler import current_poll_interval
from .scoring import KeywordMap, geo_watch_reasons, score_item
from .storage import Storage


class MarketWebhookPayload(BaseModel):
    symbol: str = "GC1!"
    previous: float | None = None
    current: float | None = None
    window_seconds: int = 120


GEO_WATCH_COOLDOWN_SECONDS = 600


class WireWatchService:
    def __init__(
        self,
        settings: Settings,
        feeds: list[FeedConfig],
        storage: Storage,
        keywords: KeywordMap,
    ) -> None:
        self.settings = settings
        self.feeds = feeds
        self.storage = storage
        self.keywords = keywords
        self.oc = OpenClawClient(settings)
        self.enabled = True

    def process_items(self, items: list[FeedItem]) -> int:
        fired = 0
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
            geo_gate = bool(geo_reasons)
            should_fire = meets_main_gate or geo_gate
            if geo_gate and self.storage.has_recent_event("geo_watch", GEO_WATCH_COOLDOWN_SECONDS):
                should_fire = False

            if should_fire:
                trigger_path = "main_gate" if meets_main_gate else "geo_watch"
                alert_text = format_news_alert(item, score, self.settings.timezone)
                self.oc.trigger(
                    text=alert_text,
                    context={
                        "source": item.source,
                        "url": item.url,
                        "relevanceScore": score.relevance_score,
                        "severityScore": score.severity_score,
                        "reasons": score.reasons,
                        "triggerPath": trigger_path,
                    },
                )
                if trigger_path == "geo_watch":
                    self.storage.save_event(
                        "geo_watch",
                        json.dumps({"source": item.source, "title": item.title, "url": item.url}),
                    )
                fired += 1
        return fired

    def poll_once(self) -> int:
        if not self.enabled:
            return 0
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
        if symbol != self.settings.market_move_symbol:
            return False
        if previous is None or current is None:
            return False
        delta = abs(current - previous)
        enough_delta = delta >= self.settings.market_move_delta_usd
        quick_enough = window <= self.settings.market_move_window_seconds
        if not (enough_delta and quick_enough):
            return False
        payload = {
            "symbol": symbol,
            "delta": delta,
            "window": window,
            "current": current,
        }
        self.storage.save_event("market_move", json.dumps(payload))
        self.oc.trigger(
            text=format_market_move_alert(symbol, delta, window, self.settings.timezone),
            context={
                "symbol": symbol,
                "delta": delta,
                "windowSeconds": window,
                "current": current,
            },
        )
        return True


def create_webhook_app(service: WireWatchService) -> FastAPI:
    app = FastAPI(title="gold-wirewatch-webhook")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook/market-move")
    def market_move(payload: MarketWebhookPayload) -> dict[str, object]:
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
    import uvicorn

    thread = threading.Thread(target=service.run_forever, daemon=True)
    thread.start()
    uvicorn.run(
        create_webhook_app(service),
        host=service.settings.webhook_host,
        port=service.settings.webhook_port,
        log_level="info",
    )
