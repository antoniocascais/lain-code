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
