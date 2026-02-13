from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    timezone: str = "America/New_York"
    db_path: str = "./data/wirewatch.db"
    state_path: str = "./data/state.json"
    feeds_path: str = "./sources.yaml"
    keywords_path: str = "./keywords.yaml"
    thresholds_path: str = "./thresholds.yaml"

    poll_interval_active_seconds: int = 20
    poll_interval_idle_seconds: int = 90
    active_window_start_hour: int = 18
    active_window_end_hour: int = 1

    relevance_threshold: float = 0.55
    severity_threshold: float = 0.45

    openclaw_base_url: str = "http://127.0.0.1:7331"
    openclaw_agent_id: str = "quant"
    openclaw_token: str = ""
    openclaw_timeout_seconds: float = 5.0

    webhook_host: str = "127.0.0.1"
    webhook_port: int = 8787
    webhook_path: str = "/webhook/market-move"

    market_move_symbol: str = "GC1!"
    market_move_delta_usd: float = 8.0
    market_move_window_seconds: int = 120

    enable_ibkr_reuters: bool = False
    enable_financialjuice_pro: bool = False

    retry_max_attempts: int = 3
    retry_backoff_seconds: float = 0.5


@dataclass(frozen=True)
class FeedConfig:
    name: str
    url: str
    kind: str
    enabled: bool = True


class Thresholds(BaseModel):
    relevance_threshold: float
    severity_threshold: float
    market_move_delta_usd: float
    market_move_window_seconds: int


def load_settings() -> Settings:
    load_dotenv(override=False)
    return Settings()


def load_feeds(path: str) -> list[FeedConfig]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return [FeedConfig(**x) for x in data["feeds"] if x.get("enabled", True)]


def load_thresholds(path: str) -> Thresholds:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Thresholds(**data)
