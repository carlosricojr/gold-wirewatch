from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .models import FeedItem, ScoreResult


def _bias(score: ScoreResult) -> tuple[str, str]:
    if score.severity_score >= 0.75:
        return ("bullish", "high")
    if score.severity_score >= 0.45:
        return ("ambiguous", "med")
    return ("bearish", "low")


def format_news_alert(item: FeedItem, score: ScoreResult, tz_name: str) -> str:
    from .alert_payload import _resolve_news_time

    news_time, kind = _resolve_news_time(
        item.published_at,
        getattr(item, "updated_at", None),
        item.fetched_at,
        tz_name,
    )
    if kind == "published":
        ts = f"{news_time}"
    elif kind == "updated":
        ts = f"{news_time} (updated)"
    else:
        ts = f"{news_time} (first seen)"
    bias, conf = _bias(score)
    summary = item.summary.strip() if item.summary.strip() else "(no feed summary provided)"
    why = "Rates/USD/risk/geopolitics/China signal from keyword hits: " + ", ".join(score.reasons)
    lines = [
        f"1) Timestamp ET: {ts}",
        f"2) HEADLINE: {item.title}",
        f"3) SUMMARY: {summary}",
        f"4) Risk interpretation for GC: {why}",
        f"5) Expected bias: {bias} | confidence: {conf}",
        "6) Cross-asset confirmers: DXY, UST yields, JPY, oil, equities",
        "7) What I'd watch next:",
        "   - DXY + US10Y real yield reaction in next 5-15m",
        "   - Risk sentiment follow-through across equities/oil",
    ]
    return "\n".join(lines)


def format_market_move_alert(symbol: str, delta: float, window: int, tz_name: str) -> str:
    ts = datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        f"1) Timestamp ET: {ts}",
        f"2) HEADLINE: {symbol} moved ${delta:.2f} in {window}s",
        "3) Why it matters for GC: potential fast repricing across rates/USD/risk",
        "4) Expected bias: ambiguous | confidence: med",
        "5) Cross-asset confirmers: DXY, UST yields, JPY, oil, equities",
        "6) What I'd watch next:",
        "   - Whether move holds beyond first retrace",
        "   - Any concurrent policy/geopolitical headline",
    ]
    return "\n".join(lines)
