"""Tests for model pricing lookup — verifies dated model IDs resolve correctly."""

import pytest
from app import _lookup_pricing, MODEL_PRICING, FALLBACK_PRICING


# Dated IDs actually seen in JSONL data → expected base key
DATED_IDS = [
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


def test_prefix_match_does_not_overshoot():
    """A model that shares a prefix but isn't a dated variant shouldn't match wrong."""
    # "claude-haiku-3-special" starts with "claude-haiku-3" but the closest
    # key is "claude-haiku-3" — verify it resolves there, not to haiku-3-5.
    result = _lookup_pricing("claude-haiku-3-special")
    assert result == MODEL_PRICING["claude-haiku-3"]


def test_all_pricing_keys_are_non_negative():
    """Sanity: no negative prices in the table."""
    for key, prices in MODEL_PRICING.items():
        assert all(p >= 0 for p in prices), f"Negative price in {key}"
    assert all(p >= 0 for p in FALLBACK_PRICING)
