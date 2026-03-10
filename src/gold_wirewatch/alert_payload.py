"""Structured compact alert payload contract.

The payload is deterministic and explicit — Hook/LLM layer is formatter only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .confirmers import ConfirmerSnapshot
from .evidence_gate import DecisionState, EvidenceVerdict
from .models import FeedItem, ScoreResult
from .source_tier import SourceMeta


@dataclass(frozen=True)
class AlertPayload:
    """Structured, deterministic alert contract."""

    # What happened + source verification
    headline: str
    source_name: str
    source_tier: str
    corroboration: str
    source_count: int

    # Decision state
    decision: str  # DecisionState value
    gated: bool

    # One-line reason with raw confirmers + freshness
    reason_line: str
    confirmer_line: str

    # One-line invalidator
    invalidator: str

    # Raw data for downstream
    relevance: float
    severity: float
    trigger_path: str
    url: str
    timestamp: str

    # News-time provenance
    news_time: str = ""
    news_time_kind: str = ""  # "published" | "updated" | "fetched"
    wirewatch_seen_time: str = ""

    confidence_capped: bool = False
    confidence_cap: float | None = None
    is_critical_bypass: bool = False

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "headline": self.headline,
            "source_name": self.source_name,
            "source_tier": self.source_tier,
            "corroboration": self.corroboration,
            "source_count": self.source_count,
            "decision": self.decision,
            "gated": self.gated,
            "reason_line": self.reason_line,
            "confirmer_line": self.confirmer_line,
            "invalidator": self.invalidator,
            "relevance": self.relevance,
            "severity": self.severity,
            "trigger_path": self.trigger_path,
            "url": self.url,
            "timestamp": self.timestamp,
            "news_time": self.news_time,
            "news_time_kind": self.news_time_kind,
            "wirewatch_seen_time": self.wirewatch_seen_time,
            "confidence_capped": self.confidence_capped,
            "is_critical_bypass": self.is_critical_bypass,
        }
        if self.confidence_cap is not None:
            d["confidence_cap"] = self.confidence_cap
        return d

    def format_compact(self) -> str:
        """Human-readable compact format for alert delivery."""
        bypass_tag = "🚨 CRITICAL " if self.is_critical_bypass else ""
        cap_tag = f"  ⚠️ CONF_CAP={self.confidence_cap}" if self.confidence_capped else ""

        # Build the labeled time line
        if self.news_time_kind == "published":
            time_line = f"🕐 News release time: {self.news_time}"
        elif self.news_time_kind == "updated":
            time_line = f"🕐 Source updated time: {self.news_time} (published unavailable)"
        else:
            time_line = f"🕐 First seen by WireWatch: {self.news_time} (source time unavailable)"

        lines = [
            f"📰 {bypass_tag}{self.headline}",
            time_line,
            f"🏷️ Source: {self.source_name} (Tier {self.source_tier}, {self.corroboration})",
            f"🎯 Decision: {self.decision}{'  ⚠️ GATED' if self.gated else ''}{cap_tag}",
            f"📊 Why: {self.reason_line}",
            f"🔬 Confirmers: {self.confirmer_line}",
            f"❌ Invalidator: {self.invalidator}",
        ]
        return "\n".join(lines)


def _is_sane_timestamp(dt: datetime, reference: datetime) -> bool:
    """Check if a timestamp is within a reasonable range of reference time.

    Rejects timestamps more than 7 days old or more than 1 hour in the future.
    """
    if dt.tzinfo is None:
        return False
    delta = dt - reference
    return timedelta(days=-7) <= delta <= timedelta(hours=1)


def _resolve_news_time(
    published_at: datetime | None,
    updated_at: datetime | None,
    fetched_at: datetime,
    tz_name: str,
) -> tuple[str, str]:
    """Resolve the best available news time with provenance label.

    Returns (formatted_time_str, kind) where kind is one of:
    "published", "updated", "fetched".
    """
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(tz_name)
    fmt = "%Y-%m-%d %H:%M:%S %Z"

    if published_at is not None and _is_sane_timestamp(published_at, fetched_at):
        return published_at.astimezone(tz).strftime(fmt), "published"
    if updated_at is not None and _is_sane_timestamp(updated_at, fetched_at):
        return updated_at.astimezone(tz).strftime(fmt), "updated"
    return fetched_at.astimezone(tz).strftime(fmt), "fetched"


def build_alert_payload(
    item: FeedItem,
    score: ScoreResult,
    source_meta: SourceMeta,
    verdict: EvidenceVerdict,
    confirmers: ConfirmerSnapshot,
    trigger_path: str,
    tz_name: str,
) -> AlertPayload:
    """Build a deterministic, structured alert payload from all components."""
    from zoneinfo import ZoneInfo

    ts = item.fetched_at.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S %Z")

    # Build reason line from score reasons
    reason_line = ", ".join(score.reasons[:6])

    # Build confirmer summary
    confirmer_line = confirmers.summary_line()

    # Build invalidator based on decision
    invalidator = _build_invalidator(verdict.decision, score.reasons)

    # Resolve news time provenance
    news_time, news_time_kind = _resolve_news_time(
        item.published_at,
        getattr(item, "updated_at", None),
        item.fetched_at,
        tz_name,
    )
    wirewatch_seen = item.fetched_at.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S %Z")

    return AlertPayload(
        headline=item.title,
        source_name=item.source,
        source_tier=source_meta.tier.value,
        corroboration=source_meta.corroboration.value,
        source_count=source_meta.source_count,
        decision=verdict.decision.value,
        gated=verdict.gated,
        reason_line=reason_line,
        confirmer_line=confirmer_line,
        invalidator=invalidator,
        relevance=score.relevance_score,
        severity=score.severity_score,
        trigger_path=trigger_path,
        url=item.url,
        timestamp=ts,
        news_time=news_time,
        news_time_kind=news_time_kind,
        wirewatch_seen_time=wirewatch_seen,
        confidence_capped=verdict.confidence_capped,
        confidence_cap=verdict.confidence_cap,
        is_critical_bypass=(trigger_path == "critical_bypass"),
    )


def _build_invalidator(decision: DecisionState, reasons: list[str]) -> str:
    """Generate a one-line invalidator based on the decision and drivers."""
    if decision == DecisionState.ACTIONABLE_LONG:
        return "Invalidated if DXY reverses higher + US10Y real yield spikes within 15m"
    if decision == DecisionState.CONDITIONAL:
        return "Invalidated if confirmers fail to align within 10m or headline retracted"
    if decision == DecisionState.FADE:
        return "Fade invalidated if follow-through volume + confirmer alignment emerges"
    if decision == DecisionState.HEADLINE_ONLY:
        return "Headline only — need confirmer data before acting"
    if decision == DecisionState.INSUFFICIENT_TAPE:
        return "Insufficient tape — wait for more data sources and confirmer refresh"
    return "No action implied — monitor only"


def build_market_move_payload(
    symbol: str,
    delta: float,
    window: int,
    current: float | None,
    confirmers: ConfirmerSnapshot,
    verdict: EvidenceVerdict,
    tz_name: str,
) -> AlertPayload:
    """Build payload for market-move alerts."""
    from zoneinfo import ZoneInfo

    ts = datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S %Z")
    headline = f"{symbol} moved ${delta:.2f} in {window}s"

    return AlertPayload(
        headline=headline,
        source_name="market_data",
        source_tier="A",
        corroboration="market_tick",
        source_count=1,
        decision=verdict.decision.value,
        gated=verdict.gated,
        reason_line=f"Price delta ${delta:.2f} in {window}s window",
        confirmer_line=confirmers.summary_line(),
        invalidator=_build_invalidator(verdict.decision, []),
        relevance=0.8,
        severity=min(delta / 15.0, 1.0),
        trigger_path="market_move",
        url="",
        timestamp=ts,
    )
