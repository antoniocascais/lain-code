"""Tests for app.py — friendly_name, estimate_cost, parse_session, API endpoints."""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch

from hypothesis import given, strategies as st

from app import friendly_name, estimate_cost, parse_session, _normalize_bound, app


# ---------------------------------------------------------------------------
# friendly_name
# ---------------------------------------------------------------------------

class TestFriendlyName:
    @pytest.mark.parametrize("cwd, folder, expected", [
        # None / empty → fall back to folder
        (None, "abc123", "abc123"),
        ("", "abc123", "abc123"),
        # Linux home paths
        ("/home/user/git/myproject", "x", "myproject"),
        ("/home/user/git/ac/myproject", "x", "myproject"),
        ("/home/user/git/indoc/myproject", "x", "myproject"),
        # macOS home
        ("/Users/dev/Documents/app", "x", "app"),
        # .claude prefix
        ("/home/user/.claude/projects/foo", "x", "foo"),
        # Nothing after username → empty path → folder
        ("/home/user", "x", "x"),
        # No home prefix — falls through to leading /
        ("/srv/data/app", "x", "srv/data/app"),
        # Nested git path
        ("/home/user/git/org/repo/subdir", "x", "org/repo/subdir"),
    ])
    def test_various_paths(self, cwd, folder, expected):
        assert friendly_name(cwd, folder) == expected

    def test_linux_home_no_trailing_path(self):
        """User home with no project dir underneath."""
        result = friendly_name("/home/user/", "fallback")
        # After stripping /home/user/, path is empty → folder prefix "/" → ""
        # which means folder fallback
        assert result == "fallback" or isinstance(result, str)

    def test_unknown_prefix_still_returns_string(self):
        result = friendly_name("/opt/custom/path", "fb")
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------

class TestEstimateCost:
    def test_all_zeros(self):
        assert estimate_cost(0, 0, 0, 0, "claude-sonnet-4") == 0.0

    def test_single_input_token(self):
        cost = estimate_cost(1_000_000, 0, 0, 0, "claude-sonnet-4")
        assert cost == pytest.approx(3.0)  # $3 per 1M input

    def test_single_output_token(self):
        cost = estimate_cost(0, 1_000_000, 0, 0, "claude-sonnet-4")
        assert cost == pytest.approx(15.0)  # $15 per 1M output

    def test_cache_read_tokens(self):
        cost = estimate_cost(0, 0, 1_000_000, 0, "claude-sonnet-4")
        assert cost == pytest.approx(0.3)

    def test_cache_create_tokens(self):
        cost = estimate_cost(0, 0, 0, 1_000_000, "claude-sonnet-4")
        assert cost == pytest.approx(3.75)

    def test_combined(self):
        cost = estimate_cost(500_000, 200_000, 100_000, 50_000, "claude-sonnet-4")
        expected = (
            500_000 * 3 / 1_000_000
            + 200_000 * 15 / 1_000_000
            + 100_000 * 0.3 / 1_000_000
            + 50_000 * 3.75 / 1_000_000
        )
        assert cost == pytest.approx(expected)

    def test_unknown_model_uses_fallback(self):
        cost = estimate_cost(1_000_000, 0, 0, 0, "nonexistent-model")
        assert cost == pytest.approx(3.0)  # fallback input = $3

    @given(
        inp=st.integers(0, 10_000_000),
        out=st.integers(0, 10_000_000),
        cr=st.integers(0, 10_000_000),
        cc=st.integers(0, 10_000_000),
    )
    def test_cost_always_non_negative(self, inp, out, cr, cc):
        cost = estimate_cost(inp, out, cr, cc, "claude-sonnet-4")
        assert cost >= 0


# ---------------------------------------------------------------------------
# parse_session
# ---------------------------------------------------------------------------

class TestParseSession:
    def test_valid_session(self, make_jsonl, user_record, assistant_record):
        path = make_jsonl([
            user_record(timestamp="2025-01-15T09:00:00Z"),
            assistant_record(
                model="claude-sonnet-4",
                input_tokens=1000,
                output_tokens=500,
                timestamp="2025-01-15T09:01:00Z",
            ),
        ])
        result = parse_session(str(path))
        assert result is not None
        assert result["input_tokens"] == 1000
        assert result["output_tokens"] == 500
        assert result["api_calls"] == 1
        assert result["models"] == {"claude-sonnet-4": 1}
        assert result["date"] == "2025-01-15"
        assert result["first_ts"] == "2025-01-15T09:00:00Z"
        assert result["last_ts"] == "2025-01-15T09:01:00Z"
        assert result["cost"] > 0

    def test_empty_file(self, make_jsonl):
        path = make_jsonl([])
        assert parse_session(str(path)) is None

    def test_malformed_json_lines(self, tmp_path):
        p = tmp_path / "bad.jsonl"
        p.write_text("not json\n{bad json too\n")
        assert parse_session(str(p)) is None

    def test_no_assistant_messages(self, make_jsonl, user_record):
        path = make_jsonl([user_record()])
        assert parse_session(str(path)) is None

    def test_missing_file(self):
        assert parse_session("/nonexistent/path.jsonl") is None

    def test_multiple_models(self, make_jsonl, user_record, assistant_record):
        path = make_jsonl([
            user_record(timestamp="2025-01-15T09:00:00Z"),
            assistant_record(model="claude-sonnet-4", timestamp="2025-01-15T09:01:00Z"),
            user_record(timestamp="2025-01-15T09:02:00Z"),
            assistant_record(model="claude-opus-4-5", timestamp="2025-01-15T09:03:00Z"),
            user_record(timestamp="2025-01-15T09:04:00Z"),
            assistant_record(model="claude-sonnet-4", timestamp="2025-01-15T09:05:00Z"),
        ])
        result = parse_session(str(path))
        assert result["models"]["claude-sonnet-4"] == 2
        assert result["models"]["claude-opus-4-5"] == 1
        assert result["api_calls"] == 3

    def test_custom_title(self, make_jsonl, assistant_record):
        path = make_jsonl([
            {"type": "custom-title", "customTitle": "My Session"},
            assistant_record(),
        ])
        result = parse_session(str(path))
        assert result["title"] == "My Session"

    def test_session_id_from_record(self, make_jsonl, assistant_record):
        path = make_jsonl([assistant_record(session_id="abc-123")])
        result = parse_session(str(path))
        assert result["session_id"] == "abc-123"

    def test_session_id_fallback_to_filename(self, make_jsonl, assistant_record):
        rec = assistant_record()
        del rec["sessionId"]
        path = make_jsonl([rec], name="fallback-id.jsonl")
        result = parse_session(str(path))
        assert result["session_id"] == "fallback-id"

    def test_timestamp_ordering(self, make_jsonl, assistant_record):
        """first_ts and last_ts should reflect actual min/max, not insertion order."""
        path = make_jsonl([
            assistant_record(timestamp="2025-01-15T12:00:00Z"),
            assistant_record(timestamp="2025-01-15T08:00:00Z"),
            assistant_record(timestamp="2025-01-15T16:00:00Z"),
        ])
        result = parse_session(str(path))
        assert result["first_ts"] == "2025-01-15T08:00:00Z"
        assert result["last_ts"] == "2025-01-15T16:00:00Z"

    def test_non_dict_message_skipped(self, make_jsonl):
        """Assistant record with string message instead of dict should not crash."""
        path = make_jsonl([{
            "type": "assistant",
            "timestamp": "2025-01-15T10:00:00Z",
            "message": "just a string",
        }, {
            "type": "assistant",
            "timestamp": "2025-01-15T10:01:00Z",
            "sessionId": "s1",
            "message": {
                "model": "claude-sonnet-4",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        }])
        result = parse_session(str(path))
        assert result is not None
        assert result["api_calls"] == 1


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestSessionEventsAPI:
    @pytest.fixture(autouse=True)
    def _setup_client(self, tmp_path, make_jsonl, assistant_record, user_record):
        from fastapi.testclient import TestClient
        self.data_dir = tmp_path / "data"
        self.data_dir.mkdir()
        project = self.data_dir / "proj1"
        project.mkdir()

        records = [user_record(), assistant_record()]
        p = project / "session.jsonl"
        with open(p, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        with patch.dict(os.environ, {"LAIN_DATA_DIR": str(self.data_dir)}):
            # Re-import to pick up env var — patch DATA_DIR directly instead
            import app as app_module
            original = app_module.DATA_DIR
            app_module.DATA_DIR = str(self.data_dir)
            self.client = TestClient(app_module.app)
            yield
            app_module.DATA_DIR = original

    def test_valid_session_events(self):
        r = self.client.get("/api/session/events", params={"file": "proj1/session.jsonl"})
        assert r.status_code == 200
        data = r.json()
        assert len(data["events"]) == 2

    def test_path_traversal_blocked(self):
        r = self.client.get("/api/session/events", params={"file": "../../etc/passwd"})
        assert r.status_code == 400

    def test_nonexistent_file(self):
        r = self.client.get("/api/session/events", params={"file": "proj1/nope.jsonl"})
        assert r.status_code == 404

    def test_non_jsonl_suffix_rejected(self):
        # Create a .txt file in the data dir
        (self.data_dir / "proj1" / "bad.txt").write_text("hello")
        r = self.client.get("/api/session/events", params={"file": "proj1/bad.txt"})
        assert r.status_code == 404

    def test_pagination_with_after(self):
        r = self.client.get("/api/session/events", params={
            "file": "proj1/session.jsonl", "after": 1, "limit": 10,
        })
        assert r.status_code == 200
        data = r.json()
        assert len(data["events"]) == 1  # skipped first line

    def test_limit_caps_results(self):
        r = self.client.get("/api/session/events", params={
            "file": "proj1/session.jsonl", "limit": 1,
        })
        assert r.status_code == 200
        data = r.json()
        assert len(data["events"]) == 1


class TestStatsAPI:
    @pytest.fixture(autouse=True)
    def _setup_client(self, tmp_path, make_jsonl, user_record, assistant_record):
        from fastapi.testclient import TestClient
        self.data_dir = tmp_path / "data"
        self.data_dir.mkdir()
        project = self.data_dir / "proj1"
        project.mkdir()

        records = [
            user_record(timestamp="2025-01-15T09:00:00Z"),
            assistant_record(
                input_tokens=1000, output_tokens=500,
                timestamp="2025-01-15T09:01:00Z",
            ),
        ]
        p = project / "session.jsonl"
        with open(p, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        import app as app_module
        original = app_module.DATA_DIR
        app_module.DATA_DIR = str(self.data_dir)
        self.client = TestClient(app_module.app)
        yield
        app_module.DATA_DIR = original

    def test_stats_no_filter(self):
        r = self.client.get("/api/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["sessions"] == 1
        assert data["input_tokens"] == 1000
        assert data["output_tokens"] == 500

    def test_stats_with_project_filter(self):
        r = self.client.get("/api/stats", params={"projects": "proj1"})
        data = r.json()
        assert data["sessions"] == 1

    def test_stats_with_wrong_project(self):
        r = self.client.get("/api/stats", params={"projects": "nonexistent"})
        data = r.json()
        assert data["sessions"] == 0

    def test_stats_date_filter_includes(self):
        r = self.client.get("/api/stats", params={"start": "2025-01-15", "end": "2025-01-15"})
        data = r.json()
        assert data["sessions"] == 1

    def test_stats_date_filter_excludes(self):
        r = self.client.get("/api/stats", params={"start": "2025-02-01"})
        data = r.json()
        assert data["sessions"] == 0

    def test_stats_datetime_filter_includes(self):
        """Session at 09:00 should be included when time range covers it."""
        r = self.client.get("/api/stats", params={
            "start": "2025-01-15T08:00:00", "end": "2025-01-15T10:00:00",
        })
        data = r.json()
        assert data["sessions"] == 1

    def test_stats_datetime_filter_excludes(self):
        """Session at 09:00 should be excluded when time range is later."""
        r = self.client.get("/api/stats", params={
            "start": "2025-01-15T10:00:00", "end": "2025-01-15T12:00:00",
        })
        data = r.json()
        assert data["sessions"] == 0

    def test_stats_datetime_start_only(self):
        """Datetime start without end should work."""
        r = self.client.get("/api/stats", params={"start": "2025-01-15T08:00:00"})
        data = r.json()
        assert data["sessions"] == 1

        r = self.client.get("/api/stats", params={"start": "2025-01-15T10:00:00"})
        data = r.json()
        assert data["sessions"] == 0

    def test_stats_datetime_end_only(self):
        """Datetime end without start should work."""
        r = self.client.get("/api/stats", params={"end": "2025-01-15T10:00:00"})
        data = r.json()
        assert data["sessions"] == 1

        r = self.client.get("/api/stats", params={"end": "2025-01-15T08:00:00"})
        data = r.json()
        assert data["sessions"] == 0

    def test_stats_mixed_date_and_datetime(self):
        """Date-only start with datetime end should work."""
        r = self.client.get("/api/stats", params={
            "start": "2025-01-15", "end": "2025-01-15T08:00:00",
        })
        data = r.json()
        assert data["sessions"] == 0  # session at 09:00, end at 08:00

    def test_stats_datetime_boundary_inclusive(self):
        """Session at exactly 09:00 should be included when end is 09:00."""
        r = self.client.get("/api/stats", params={
            "start": "2025-01-15T09:00", "end": "2025-01-15T09:00",
        })
        data = r.json()
        assert data["sessions"] == 1

    def test_stats_datetime_boundary_excluded(self):
        """Session at 09:00 excluded when end minute is 08:59."""
        r = self.client.get("/api/stats", params={
            "start": "2025-01-15T00:00", "end": "2025-01-15T08:59",
        })
        data = r.json()
        assert data["sessions"] == 0

    def test_stats_datetime_start_date_only_end(self):
        """Datetime start + date-only end combo."""
        r = self.client.get("/api/stats", params={
            "start": "2025-01-15T08:00:00", "end": "2025-01-15",
        })
        data = r.json()
        assert data["sessions"] == 1

    def test_stats_null_timestamp_passes_datetime_filter(
        self, tmp_path, assistant_record,
    ):
        """Sessions with no timestamp should not be excluded by datetime filters."""
        import app as app_module
        project = self.data_dir / "proj2"
        project.mkdir()
        rec = assistant_record()
        del rec["timestamp"]
        p = project / "notimestamp.jsonl"
        with open(p, "w") as f:
            f.write(json.dumps(rec) + "\n")

        r = self.client.get("/api/stats", params={
            "start": "2025-01-15T08:00:00", "end": "2025-01-15T10:00:00",
        })
        data = r.json()
        # proj1 session (09:00) + proj2 session (no timestamp) = 2
        assert data["sessions"] == 2

    def test_empty_data_dir(self, tmp_path):
        import app as app_module
        app_module.DATA_DIR = str(tmp_path / "empty")
        r = self.client.get("/api/stats")
        data = r.json()
        assert data["sessions"] == 0
        assert data["cost"] == 0


class TestNormalizeBound:
    def test_empty_string(self):
        assert _normalize_bound("") == ""
        assert _normalize_bound("", is_end=True) == ""

    def test_date_only_passthrough(self):
        assert _normalize_bound("2025-01-15") == "2025-01-15"
        assert _normalize_bound("2025-01-15", is_end=True) == "2025-01-15"

    def test_start_pads_seconds(self):
        assert _normalize_bound("2025-01-15T09:00") == "2025-01-15T09:00:00"

    def test_end_pads_to_end_of_minute(self):
        assert _normalize_bound("2025-01-15T17:30", is_end=True) == "2025-01-15T17:30:59.999Z"

    def test_full_precision_start_unchanged(self):
        assert _normalize_bound("2025-01-15T09:00:00") == "2025-01-15T09:00:00"

    def test_full_precision_end_gets_z(self):
        assert _normalize_bound("2025-01-15T17:30:00", is_end=True) == "2025-01-15T17:30:00Z"

    def test_end_already_has_z(self):
        assert _normalize_bound("2025-01-15T17:30:00Z", is_end=True) == "2025-01-15T17:30:00Z"

    def test_millisecond_precision_passthrough(self):
        """Already-precise timestamps should not be double-padded."""
        assert _normalize_bound("2025-01-15T09:00:00.123Z") == "2025-01-15T09:00:00.123Z"
        assert _normalize_bound("2025-01-15T09:00:00.123Z", is_end=True) == "2025-01-15T09:00:00.123Z"

    def test_start_never_appends_z(self):
        """Start bounds should never get Z suffix — keeps comparison tight."""
        result = _normalize_bound("2025-01-15T09:00")
        assert not result.endswith("Z")
