from __future__ import annotations

from pathlib import Path

import yaml

import re

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
    "tariff",
    "trade",
    "supreme court",
    "scotus",
    "injunction",
)

# --- Catalyst lane: AI/hyperscaler capex, semiconductor, corporate capex, financing stress ---

# Entity terms that must co-occur with action terms for catalyst detection
CATALYST_ENTITIES = (
    "openai",
    "google",
    "microsoft",
    "amazon",
    "meta",
    "oracle",
    "nvidia",
    "tsmc",
    "amd",
    "intel",
    "samsung",
    "asml",
    "hyperscaler",
    "data center",
    "data centre",
    "semiconductor",
    "chip",
    "ai spending",
    "ai investment",
    "cloud capex",
)

# Context terms that indicate negative catalyst action
CATALYST_CONTEXT_TERMS = (
    "capex",
    "investment",
    "spending",
    "guidance",
    "demand",
    "balance sheet",
)

# Negative action verbs/adjectives (must co-occur with context + entity)
CATALYST_NEGATIVE_ACTIONS = (
    "cut",
    "slash",
    "reduc",
    "delay",
    "suspend",
    "pullback",
    "freeze",
    "cancel",
    "lower",
    "miss",
    "warn",
    "shortfall",
    "weak",
    "downgrade",
    "stress",
    "concern",
    "breach",
    "default",
    "overrun",
    "writedown",
    "write down",
    "impairment",
    "halt",
)

# Standalone phrases strong enough without entity co-occurrence
CATALYST_STANDALONE = (
    "hyperscaler capex",
    "ai capex cut",
    "ai capex delay",
    "ai spending cut",
    "data center investment cut",
    "chip demand warning",
    "semiconductor demand shock",
    "semiconductor downturn",
    "tech capex pullback",
    "cloud spending cut",
)


POLICY_WATCH_TERMS = (
    "tariff",
    "trade war",
    "import duty",
    "supreme court",
    "scotus",
    "court ruling",
    "injunction",
    # energy-policy shock terms
    "fuel-export control",
    "export control",
    "refinery mandate",
    "emergency stockpiling",
    "strategic petroleum reserve",
    "spr",
    "treasury buy",
    "government buying oil",
    "buy more oil",
    "russian oil",
    "chokepoint",
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


def policy_watch_reasons(item: FeedItem) -> list[str]:
    text = f"{item.title} {item.summary}".lower()
    hits = [term for term in POLICY_WATCH_TERMS if term in text]
    return [f"policy:{h}" for h in hits][:6]


# Tokens that need word-boundary matching to avoid substring collisions
# Entity tokens: strict word boundary both sides (no suffix allowed)
_STRICT_ENTITY_TOKENS = frozenset({"amd", "meta", "chip", "intel"})

# Action tokens: word boundary prefix, allow verb suffixes (-s, -ed, -ing, etc.)
_VERB_ACTION_TOKENS = frozenset({"cut", "miss", "halt", "weak", "slash", "freeze"})


def _compile_entity_patterns(terms: tuple[str, ...]) -> list[tuple[str, re.Pattern[str]]]:
    """Compile entity terms — strict boundaries for short tokens."""
    patterns = []
    for term in terms:
        if term in _STRICT_ENTITY_TOKENS:
            patterns.append((term, re.compile(r"\b" + re.escape(term) + r"\b", re.I)))
        else:
            patterns.append((term, re.compile(re.escape(term), re.I)))
    return patterns


def _compile_action_patterns(terms: tuple[str, ...]) -> list[tuple[str, re.Pattern[str]]]:
    """Compile action terms — boundary prefix + optional verb suffixes for short tokens."""
    patterns = []
    for term in terms:
        if term in _VERB_ACTION_TOKENS:
            patterns.append((term, re.compile(r"\b" + re.escape(term) + r"(?:e?s|ed|ing|ted|ting)?\b", re.I)))
        elif len(term) <= 4:
            patterns.append((term, re.compile(r"\b" + re.escape(term) + r"\b", re.I)))
        else:
            patterns.append((term, re.compile(re.escape(term), re.I)))
    return patterns


def _compile_boundary_patterns(terms: tuple[str, ...]) -> list[tuple[str, re.Pattern[str]]]:
    """Generic: simple substring match for most, boundary for very short."""
    patterns = []
    for term in terms:
        if len(term) <= 4:
            patterns.append((term, re.compile(r"\b" + re.escape(term) + r"\b", re.I)))
        else:
            patterns.append((term, re.compile(re.escape(term), re.I)))
    return patterns


_ENTITY_PATTERNS = _compile_entity_patterns(CATALYST_ENTITIES)
_CONTEXT_PATTERNS = _compile_boundary_patterns(CATALYST_CONTEXT_TERMS)
_ACTION_PATTERNS = _compile_action_patterns(CATALYST_NEGATIVE_ACTIONS)
_STANDALONE_PATTERNS = _compile_boundary_patterns(CATALYST_STANDALONE)


# Negation/reversal patterns that invalidate catalyst detection
CATALYST_NEGATION_TERMS = (
    "denies cut",
    "denied cut",
    "restores",
    "restored",
    "increases investment",
    "raises guidance",
    "not reduced",
    "not cut",
    "no cut",
    "reaffirms",
    "reaffirmed",
    "maintains guidance",
    "beats expectations",
    "exceeds expectations",
    "allays concern",
    "eases concern",
)

# Regex negation patterns for cases where negation and subject may be separated
_NEGATION_PATTERNS = [
    re.compile(r"\bdismiss\w*\b.{0,40}\bconcern", re.I),
    re.compile(r"\bdismiss\w*\b.{0,40}\bworr", re.I),
    re.compile(r"\bdismiss\w*\b.{0,40}\bfear", re.I),
    re.compile(r"\bdeni\w*\b.{0,40}\bcut", re.I),
    re.compile(r"\brul\w*\s+out\b.{0,40}\bcut", re.I),
]


def catalyst_watch_reasons(item: FeedItem) -> list[str]:
    """Detect market-moving catalyst headlines (AI/hyperscaler capex, semi demand, etc.).

    Uses 3-way AND logic: entity + context + negative action, OR standalone phrases.
    Negation patterns suppress false positives from reversal/denial headlines.
    Returns tagged reasons like 'catalyst:nvidia+capex+cut'.
    """
    text = f"{item.title} {item.summary}".lower()

    # Check for negation/reversal patterns first
    for neg in CATALYST_NEGATION_TERMS:
        if neg in text:
            return []
    for neg_pat in _NEGATION_PATTERNS:
        if neg_pat.search(text):
            return []

    hits: list[str] = []

    # Check standalone phrases first (strong enough alone)
    for term, pat in _STANDALONE_PATTERNS:
        if pat.search(text):
            hits.append(f"catalyst:standalone:{term}")

    if hits:
        return hits[:6]

    # 3-way AND: entity + context + negative action (word-boundary safe)
    entity_hits = [t for t, p in _ENTITY_PATTERNS if p.search(text)]
    context_hits = [t for t, p in _CONTEXT_PATTERNS if p.search(text)]
    action_hits = [t for t, p in _ACTION_PATTERNS if p.search(text)]

    if entity_hits and context_hits and action_hits:
        hits.append(
            f"catalyst:{entity_hits[0]}+{context_hits[0]}+{action_hits[0]}"
        )
        if len(entity_hits) > 1:
            hits.append(f"catalyst:{entity_hits[1]}+{context_hits[0]}+{action_hits[0]}")

    return hits[:6]


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

    policy_reasons = policy_watch_reasons(item)
    if policy_reasons:
        reasons.extend(policy_reasons)

    catalyst_reasons = catalyst_watch_reasons(item)
    if catalyst_reasons:
        reasons.extend(catalyst_reasons)

    rel = min(rel, 1.0)
    sev = min(sev, 1.0)
    if not reasons:
        reasons = ["no-strong-driver"]
    return ScoreResult(relevance_score=rel, severity_score=sev, reasons=reasons)
