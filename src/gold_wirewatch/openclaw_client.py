from __future__ import annotations

import json
import time

import httpx

from .config import Settings


class OpenClawClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _post_with_retry(self, payload: dict[str, object]) -> None:
        headers = {
            "Authorization": f"Bearer {self.settings.openclaw_token}",
            "Content-Type": "application/json",
        }
        delay = self.settings.retry_backoff_seconds
        url = f"{self.settings.openclaw_base_url.rstrip('/')}/hooks/agent"
        for attempt in range(1, self.settings.retry_max_attempts + 1):
            try:
                with httpx.Client(timeout=self.settings.openclaw_timeout_seconds) as client:
                    res = client.post(url, headers=headers, content=json.dumps(payload))
                    res.raise_for_status()
                return
            except (httpx.HTTPError, httpx.TimeoutException):
                if attempt >= self.settings.retry_max_attempts:
                    raise
                time.sleep(delay)
                delay *= 2

    def trigger(self, text: str, context: dict[str, object] | None = None) -> None:
        payload: dict[str, object] = {
            "agentId": self.settings.openclaw_agent_id,
            "wakeMode": "now",
            "message": text,
        }
        if context:
            text_with_context = f"{text}\n\nContext:\n{json.dumps(context, ensure_ascii=False)}"
            payload["message"] = text_with_context
        self._post_with_retry(payload)
