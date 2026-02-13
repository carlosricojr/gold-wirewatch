from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def in_active_window(now: datetime, tz_name: str, start_hour: int, end_hour: int) -> bool:
    local = now.astimezone(ZoneInfo(tz_name))
    hour = local.hour
    if start_hour <= end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def current_poll_interval(
    now: datetime,
    tz_name: str,
    start_hour: int,
    end_hour: int,
    active_seconds: int,
    idle_seconds: int,
) -> int:
    return active_seconds if in_active_window(now, tz_name, start_hour, end_hour) else idle_seconds
