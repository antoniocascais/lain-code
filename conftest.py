"""Shared test fixtures for lain-code."""

import json
import pytest
from pathlib import Path


@pytest.fixture
def make_jsonl(tmp_path):
    """Create a temp JSONL file from a list of dicts."""
    def _make(records: list[dict], name: str = "test.jsonl") -> Path:
        p = tmp_path / name
        with open(p, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        return p
    return _make


@pytest.fixture
def assistant_record():
    """Factory for a minimal assistant JSONL record."""
    def _make(
        model: str = "claude-sonnet-4",
        input_tokens: int = 100,
        output_tokens: int = 50,
        cache_read: int = 0,
        cache_create: int = 0,
        timestamp: str = "2025-01-15T10:00:00Z",
        session_id: str = "sess-001",
    ) -> dict:
        return {
            "type": "assistant",
            "timestamp": timestamp,
            "sessionId": session_id,
            "message": {
                "model": model,
                "content": [{"type": "text", "text": "response"}],
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_create,
                },
            },
        }
    return _make


@pytest.fixture
def user_record():
    """Factory for a minimal user JSONL record."""
    def _make(
        text: str = "hello",
        timestamp: str = "2025-01-15T09:59:00Z",
        session_id: str = "sess-001",
    ) -> dict:
        return {
            "type": "user",
            "timestamp": timestamp,
            "sessionId": session_id,
            "message": {"content": text},
        }
    return _make
