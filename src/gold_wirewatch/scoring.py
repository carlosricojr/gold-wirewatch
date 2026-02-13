from __future__ import annotations

from pathlib import Path

import yaml

from .models import FeedItem, ScoreResult

KeywordMap = dict[str, tuple[float, float]]

GEO_CORE_TERMS = (
    "iran",
    "middle east",
    "red sea",
    "hormuz",
    "carrier",
    "missile",
    "drone",
    "warship",
)

GEO_PRIORITY_TERMS = (
    "iran",
    "hormuz",
    "strait of hormuz",
    "red sea",
)

GEO_MATERIALITY_TERMS = (
    "sanction",
    "nuclear",
    "shipping",
    "strait",
    "oil",
    "military",
    "escalat",
    "brent",
    "wti",
    "dxy",
    "yield",
)


def load_keywords(path: str) -> KeywordMap:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    out: KeywordMap = {}
    for row in data.get("keywords", []):
        key = str(row["term"]).lower()
        out[key] = (float(row["relevance"]), float(row["severity"]))
    return out


def geo_watch_reasons(item: FeedItem) -> list[str]:
    text = f"{item.title} {item.summary}".lower()
    core_hits = [term for term in GEO_CORE_TERMS if term in text]
    priority_hits = [term for term in GEO_PRIORITY_TERMS if term in text]
    material_hits = [term for term in GEO_MATERIALITY_TERMS if term in text]

    is_high_value_geo = bool(priority_hits) and len(material_hits) >= 1
    is_broad_geo_cluster = len(core_hits) >= 2 and len(material_hits) >= 2

    if is_high_value_geo or is_broad_geo_cluster:
        hits = [f"geo:{h}" for h in core_hits] + [f"material:{h}" for h in material_hits]
        return hits[:6]
    return []


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

    geo_reasons = geo_watch_reasons(item)
    if geo_reasons:
        reasons.extend(geo_reasons)

    rel = min(rel, 1.0)
    sev = min(sev, 1.0)
    if not reasons:
        reasons = ["no-strong-driver"]
    return ScoreResult(relevance_score=rel, severity_score=sev, reasons=reasons)
