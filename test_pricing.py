"""Tests for model pricing lookup — verifies dated model IDs resolve correctly."""

import pytest
from app import (
    _lookup_pricing,
    estimate_cost,
    ALIASES,
    MODEL_PRICING,
    SCHEDULED_PRICING,
    FALLBACK_PRICING,
)


# Dated IDs actually seen in JSONL data → expected base key
DATED_IDS = [
    ("claude-opus-4-7-20260201",   "claude-opus-4-7"),
    ("claude-opus-4-5-20251101",   "claude-opus-4-5"),
    ("claude-sonnet-4-5-20250929", "claude-sonnet-4-5"),
    ("claude-sonnet-4-20250514",   "claude-sonnet-4"),
    ("claude-haiku-4-5-20251001",  "claude-haiku-4-5"),
    ("claude-haiku-3-5-20241022",  "claude-haiku-3-5"),
]


@pytest.mark.parametrize("dated_id, base_key", DATED_IDS)
def test_dated_id_resolves_to_base(dated_id, base_key):
    assert _lookup_pricing(dated_id) == MODEL_PRICING[base_key]


@pytest.mark.parametrize("model_id", MODEL_PRICING.keys())
def test_exact_ids_match(model_id):
    assert _lookup_pricing(model_id) == MODEL_PRICING[model_id]


def test_unknown_model_returns_fallback():
    assert _lookup_pricing("claude-mystery-9000") == FALLBACK_PRICING


# --- Boundary / edge cases ---

def test_empty_string_returns_fallback():
    assert _lookup_pricing("") == FALLBACK_PRICING


def test_prefix_match_prefers_longer_key():
    """Dated opus-4-5 must not accidentally match opus-4."""
    result = _lookup_pricing("claude-opus-4-5-20251101")
    assert result == MODEL_PRICING["claude-opus-4-5"]
    assert result != MODEL_PRICING.get("claude-opus-4")


def test_opus_4_7_not_billed_as_opus_4():
    """Opus 4.7 at $5/$25 must not fall through to Opus 4 at $15/$75."""
    result = _lookup_pricing("claude-opus-4-7")
    assert result == MODEL_PRICING["claude-opus-4-7"]
    assert result != MODEL_PRICING["claude-opus-4"]


def test_opus_4_8_not_billed_as_opus_4():
    """Opus 4.8 at $5/$25 must not fall through to Opus 4 at $15/$75."""
    result = _lookup_pricing("claude-opus-4-8")
    assert result == MODEL_PRICING["claude-opus-4-8"]
    assert result != MODEL_PRICING["claude-opus-4"]


def test_opus_4_8_1m_suffix_resolves_to_base():
    """The `[1m]` context-window suffix must prefix-match the base key."""
    assert _lookup_pricing("claude-opus-4-8[1m]") == MODEL_PRICING["claude-opus-4-8"]


def test_fable_5_not_billed_as_fallback():
    """Fable 5 at $10/$50 must resolve to its own entry, not the Sonnet fallback."""
    result = _lookup_pricing("claude-fable-5")
    assert result == MODEL_PRICING["claude-fable-5"]
    assert result != FALLBACK_PRICING


def test_prefix_match_does_not_overshoot():
    """A model that shares a prefix but isn't a dated variant shouldn't match wrong."""
    # "claude-haiku-3-special" starts with "claude-haiku-3" but the closest
    # key is "claude-haiku-3" — verify it resolves there, not to haiku-3-5.
    result = _lookup_pricing("claude-haiku-3-special")
    assert result == MODEL_PRICING["claude-haiku-3"]


SONNET_5_INTRO = (2, 10, 0.20, 2.50)
SONNET_5_STANDARD = (3, 15, 0.30, 3.75)


@pytest.mark.parametrize("when", ["2026-06-09", "2026-07-19", "2026-08-31"])
def test_sonnet_5_intro_pricing_before_september(when):
    """Introductory $2/$10 applies through 2026-08-31 inclusive."""
    assert _lookup_pricing("claude-sonnet-5", when=when) == SONNET_5_INTRO


@pytest.mark.parametrize("when", ["2026-09-01", "2026-12-25", "2027-03-04"])
def test_sonnet_5_standard_pricing_from_september(when):
    """Standard $3/$15 takes effect 2026-09-01."""
    assert _lookup_pricing("claude-sonnet-5", when=when) == SONNET_5_STANDARD


def test_sonnet_5_not_billed_as_fallback():
    """Sonnet 5 needs a real entry — the fallback silently matched its old rate."""
    assert _lookup_pricing("claude-sonnet-5", when="2026-07-19") != FALLBACK_PRICING


def test_sonnet_5_suffixed_id_resolves_to_base():
    """Dated / context-window suffixes must still hit the schedule."""
    assert _lookup_pricing("claude-sonnet-5[1m]", when="2026-07-19") == SONNET_5_INTRO


def test_scheduled_lookup_defaults_to_today():
    """Omitting `when` must not crash or fall through to the fallback."""
    assert _lookup_pricing("claude-sonnet-5") in (SONNET_5_INTRO, SONNET_5_STANDARD)


def test_schedules_are_chronologically_sorted():
    """Resolution takes the last matching entry, so order is load-bearing."""
    for key, schedule in SCHEDULED_PRICING.items():
        dates = [effective_from for effective_from, _ in schedule]
        assert dates == sorted(dates), f"{key} schedule is out of order"


def test_estimate_cost_honours_session_date():
    """Cost must follow the session's own date, not today's."""
    intro = estimate_cost(1_000_000, 0, 0, 0, "claude-sonnet-5", when="2026-07-19")
    standard = estimate_cost(1_000_000, 0, 0, 0, "claude-sonnet-5", when="2026-09-01")
    assert intro == pytest.approx(2.0)
    assert standard == pytest.approx(3.0)


MYTHOS_IDS = ["claude-mythos-5", "claude-mythos-preview"]


@pytest.mark.parametrize("model_id", MYTHOS_IDS)
def test_mythos_priced_at_fable_tier(model_id):
    """Mythos 5 / Mythos Preview share Fable 5 pricing, not the Sonnet fallback."""
    result = _lookup_pricing(model_id)
    assert result == MODEL_PRICING["claude-fable-5"]
    assert result != FALLBACK_PRICING


def test_all_pricing_keys_are_non_negative():
    """Sanity: no negative prices in the table."""
    for key, prices in MODEL_PRICING.items():
        assert all(p >= 0 for p in prices), f"Negative price in {key}"
    for key, schedule in SCHEDULED_PRICING.items():
        for effective_from, prices in schedule:
            assert all(p >= 0 for p in prices), f"Negative price in {key}@{effective_from}"
    assert all(p >= 0 for p in FALLBACK_PRICING)


# --- Bare aliases: Claude Code writes these instead of a full model ID ---

@pytest.mark.parametrize("alias, expected_key", [
    ("opus",  "claude-opus-4-8"),
    ("haiku", "claude-haiku-4-5"),
    ("fable", "claude-fable-5"),
])
def test_bare_alias_resolves_to_current_model(alias, expected_key):
    assert _lookup_pricing(alias) == MODEL_PRICING[expected_key]


def test_bare_sonnet_alias_resolves_to_sonnet_5():
    """`sonnet` targets a scheduled model, so it needs the date too."""
    assert _lookup_pricing("sonnet", when="2026-07-19") == SONNET_5_INTRO


def test_opus_alias_not_billed_as_sonnet():
    """Bare `opus` fell through to the Sonnet fallback — a 3x undercharge."""
    result = _lookup_pricing("opus")
    assert result != FALLBACK_PRICING
    assert result == MODEL_PRICING["claude-opus-4-8"]


def test_alias_targets_exist_in_pricing_tables():
    """Guards against an alias pointing at a key that was renamed or removed."""
    for alias, target in ALIASES.items():
        assert target in MODEL_PRICING or target in SCHEDULED_PRICING, \
            f"alias {alias!r} points at unknown key {target!r}"
