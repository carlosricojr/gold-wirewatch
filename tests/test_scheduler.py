from datetime import UTC, datetime

from gold_wirewatch.scheduler import current_poll_interval, in_active_window


def test_active_window_interval() -> None:
    # 23:30 ET (winter) == 04:30 UTC next day, should be active
    dt = datetime(2026, 2, 14, 4, 30, tzinfo=UTC)
    assert in_active_window(dt, "America/New_York", 18, 1)
    assert current_poll_interval(dt, "America/New_York", 18, 1, 20, 90) == 20


def test_idle_window_interval() -> None:
    dt = datetime(2026, 2, 14, 16, 0, tzinfo=UTC)
    assert not in_active_window(dt, "America/New_York", 18, 1)
    assert current_poll_interval(dt, "America/New_York", 18, 1, 20, 90) == 90
