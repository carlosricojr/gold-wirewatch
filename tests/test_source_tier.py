"""Tests for source_tier module."""
from gold_wirewatch.source_tier import (
    CorroborationState,
    SourceTier,
    classify_source,
    corroborate,
)


def test_tier_a_sources():
    assert classify_source("Federal Reserve Press") == SourceTier.A
    assert classify_source("US Treasury Press") == SourceTier.A
    assert classify_source("BIS News") == SourceTier.A
    assert classify_source("OFAC Sanctions") == SourceTier.A


def test_tier_b_sources():
    assert classify_source("Reuters Wire") == SourceTier.B
    assert classify_source("Bloomberg Terminal") == SourceTier.B


def test_tier_c_default():
    assert classify_source("Random Blog") == SourceTier.C
    assert classify_source("Twitter Feed") == SourceTier.C


def test_corroborate_multi_source():
    meta = corroborate(["Federal Reserve Press", "Reuters Wire"])
    assert meta.corroboration == CorroborationState.MULTI_SOURCE
    assert meta.tier == SourceTier.A
    assert meta.source_count == 2


def test_corroborate_single_verified():
    meta = corroborate(["Federal Reserve Press"])
    assert meta.corroboration == CorroborationState.SINGLE_VERIFIED
    assert meta.tier == SourceTier.A


def test_corroborate_single_unverified():
    meta = corroborate(["Random Blog"])
    assert meta.corroboration == CorroborationState.SINGLE_UNVERIFIED
    assert meta.tier == SourceTier.C


def test_corroborate_empty():
    meta = corroborate([])
    assert meta.corroboration == CorroborationState.NONE
    assert meta.source_count == 0


def test_corroborate_dedupes():
    meta = corroborate(["Reuters Wire", "Reuters Wire"])
    assert meta.source_count == 1
    assert meta.corroboration == CorroborationState.SINGLE_VERIFIED
