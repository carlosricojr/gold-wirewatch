"""Critical-event bypass lane: immediate alert for high-importance events.

These events bypass confirmer completeness requirements and emit immediately.
The bypass is deterministic — keyword pattern matching only, no LLM involved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class CriticalCategory(str, Enum):
    """Categories of critical events that bypass normal gating."""

    HORMUZ_SHIPPING = "hormuz_shipping"
    US_FORCE_POSTURE = "us_force_posture"
    CONFIRMED_STRIKE = "confirmed_strike"
    EMBASSY_CLOSURE = "embassy_closure"
    DIRECT_MILITARY = "direct_military"


# Each pattern set: ALL patterns in a group must match (AND logic within group).
# Any group matching triggers the bypass (OR logic across groups).
_CRITICAL_PATTERNS: dict[CriticalCategory, list[list[re.Pattern[str]]]] = {
    CriticalCategory.HORMUZ_SHIPPING: [
        # Group: Hormuz + shipping disruption
        [re.compile(r"\bhormuz\b", re.I), re.compile(r"\b(?:ship|shipping|tanker|vessel|blockade|closure|disrupt|seiz|attack|mine)\b", re.I)],
        # Group: Strait + closure/blockade
        [re.compile(r"\bstrait\b", re.I), re.compile(r"\b(?:clos|block|mine|seiz|attack)\w*\b", re.I), re.compile(r"\b(?:iran|hormuz|persian)\b", re.I)],
    ],
    CriticalCategory.US_FORCE_POSTURE: [
        # Group: US military deployment/posture change
        [re.compile(r"\b(?:us|u\.s\.|american|pentagon|centcom)\b", re.I), re.compile(r"\b(?:deploy|redeploy|carrier|strike group|troops|forces|posture|mobiliz)\w*\b", re.I), re.compile(r"\b(?:middle east|gulf|iran|persian|hormuz|red sea)\b", re.I)],
        # Group: Carrier group movement
        [re.compile(r"\b(?:carrier|strike group|uss)\b", re.I), re.compile(r"\b(?:deploy|order|dispatch|sail|transit|gulf|hormuz)\w*\b", re.I)],
    ],
    CriticalCategory.CONFIRMED_STRIKE: [
        # Group: Confirmed military strike with casualties
        [re.compile(r"\b(?:strike|attack|bomb|shell|missile)\w*\b", re.I), re.compile(r"\b(?:confirm|report|kill|casualt|dead|wound|injur)\w*\b", re.I), re.compile(r"\b(?:iran|iraq|syria|lebanon|yemen|houthi|hezbollah|israel)\b", re.I)],
    ],
    CriticalCategory.EMBASSY_CLOSURE: [
        # Group: Embassy/consulate closure or evacuation
        [re.compile(r"\b(?:embassy|consulate|diplomatic)\b", re.I), re.compile(r"\b(?:clos|evacuat|withdraw|shut)\w*\b", re.I)],
    ],
    CriticalCategory.DIRECT_MILITARY: [
        # Group: Direct military action between state actors
        [re.compile(r"\b(?:iran|israel|us|u\.s\.)\b", re.I), re.compile(r"\b(?:attack|retali|launch|strike|fire|shoot|intercept)\w*\b", re.I), re.compile(r"\b(?:confirm|official|military|defense|idf|irgc|centcom)\b", re.I)],
    ],
}


@dataclass(frozen=True)
class CriticalBypassResult:
    """Result of critical-event bypass check."""

    is_critical: bool
    categories: tuple[CriticalCategory, ...]
    matched_category_names: tuple[str, ...]

    @property
    def reason(self) -> str:
        if not self.is_critical:
            return ""
        return f"CRITICAL_BYPASS: {', '.join(self.matched_category_names)}"


_NOT_CRITICAL = CriticalBypassResult(
    is_critical=False, categories=(), matched_category_names=()
)


def check_critical_bypass(title: str, summary: str = "") -> CriticalBypassResult:
    """Check if a headline + summary matches any critical-event bypass pattern.

    Returns a CriticalBypassResult. If is_critical=True, the event should
    bypass normal confirmer gating and emit immediately.
    """
    text = f"{title} {summary}"
    matched: list[CriticalCategory] = []

    for category, pattern_groups in _CRITICAL_PATTERNS.items():
        for group in pattern_groups:
            if all(pattern.search(text) for pattern in group):
                matched.append(category)
                break  # One group match per category is enough

    if not matched:
        return _NOT_CRITICAL

    return CriticalBypassResult(
        is_critical=True,
        categories=tuple(matched),
        matched_category_names=tuple(c.value for c in matched),
    )
