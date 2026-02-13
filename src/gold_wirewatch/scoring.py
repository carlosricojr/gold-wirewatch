from __future__ import annotations

from pathlib import Path

import yaml

from .models import FeedItem, ScoreResult

KeywordMap = dict[str, tuple[float, float]]


def load_keywords(path: str) -> KeywordMap:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    out: KeywordMap = {}
    for row in data.get("keywords", []):
        key = str(row["term"]).lower()
        out[key] = (float(row["relevance"]), float(row["severity"]))
    return out


def score_item(item: FeedItem, keywords: KeywordMap) -> ScoreResult:
    text = f"{item.title} {item.summary}".lower()
    rel = 0.0
    sev = 0.0
    reasons: list[str] = []
    for key, (r_weight, s_weight) in keywords.items():
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
