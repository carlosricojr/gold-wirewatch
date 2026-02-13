from __future__ import annotations

from .models import FeedItem, ScoreResult

KEYWORDS: dict[str, tuple[float, float]] = {
    "real yield": (0.35, 0.4),
    "treasury": (0.3, 0.3),
    "fed": (0.35, 0.5),
    "ecb": (0.2, 0.25),
    "boj": (0.2, 0.2),
    "pboc": (0.25, 0.3),
    "usd": (0.3, 0.25),
    "dollar": (0.3, 0.25),
    "sanctions": (0.25, 0.6),
    "war": (0.25, 0.8),
    "geopolit": (0.35, 0.8),
    "china": (0.25, 0.35),
    "risk-off": (0.25, 0.45),
    "volatility": (0.2, 0.3),
}


def score_item(item: FeedItem) -> ScoreResult:
    text = f"{item.title} {item.summary}".lower()
    rel = 0.0
    sev = 0.0
    reasons: list[str] = []
    for key, (r_weight, s_weight) in KEYWORDS.items():
        if key in text:
            rel += r_weight
            sev += s_weight
            reasons.append(key)
    if "gold" in text or "xau" in text:
        rel += 0.3
        reasons.append("gold-direct")
    rel = min(rel, 1.0)
    sev = min(sev, 1.0)
    if not reasons:
        reasons = ["no-strong-driver"]
    return ScoreResult(relevance_score=rel, severity_score=sev, reasons=reasons)
