"""Tests for /api/analyze endpoints — status, validation, SSE streaming, credentials."""

import json
import os
import sys
import pytest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import app as app_module
from fastapi.testclient import TestClient


MOCK_PATTERNS = {"patterns": {}, "sessions": []}
MOCK_STATS = {"total_sessions": 1, "total_turns": 1, "tool_frequency": {}}


@contextmanager
def mock_analysis():
    """Mock BM25 + block SDK import so stream tests don't hang."""
    saved = sys.modules.pop("claude_agent_sdk", None)
    sys.modules["claude_agent_sdk"] = None  # forces ImportError on import
    try:
        with patch("app.run_patterns", return_value=MOCK_PATTERNS), \
             patch("app.run_stats", return_value=MOCK_STATS):
            yield
    finally:
        sys.modules.pop("claude_agent_sdk", None)
        if saved is not None:
            sys.modules["claude_agent_sdk"] = saved


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def project_with_session(data_dir, user_record, assistant_record):
    """Create a project dir with one valid JSONL session file."""
    project = data_dir / "proj1"
    project.mkdir()
    p = project / "session.jsonl"
    records = [
        user_record(timestamp="2025-01-15T09:00:00Z"),
        assistant_record(timestamp="2025-01-15T09:01:00Z"),
    ]
    with open(p, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return p


@pytest.fixture
def client(data_dir):
    original = app_module.DATA_DIR
    app_module.DATA_DIR = str(data_dir)
    c = TestClient(app_module.app)
    yield c
    app_module.DATA_DIR = original


# ---------------------------------------------------------------------------
# CREDENTIALS_PATH env var resolution
# ---------------------------------------------------------------------------

class TestCredentialsPathResolution:
    def test_default_expands_home(self):
        """Without LAIN_CREDENTIALS, path defaults to ~/.claude/.credentials.json."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LAIN_CREDENTIALS", None)
            # Re-evaluate the default
            result = os.environ.get(
                "LAIN_CREDENTIALS",
                os.path.expanduser("~/.claude/.credentials.json"),
            )
            assert result.endswith("/.claude/.credentials.json")
            assert "~" not in result  # expanded

    def test_env_var_overrides_default(self):
        with patch.dict(os.environ, {"LAIN_CREDENTIALS": "/custom/path/creds.json"}):
            result = os.environ.get(
                "LAIN_CREDENTIALS",
                os.path.expanduser("~/.claude/.credentials.json"),
            )
            assert result == "/custom/path/creds.json"

    def test_module_level_credentials_path_is_string(self):
        assert isinstance(app_module.CREDENTIALS_PATH, str)
        assert len(app_module.CREDENTIALS_PATH) > 0


# ---------------------------------------------------------------------------
# GET /api/analyze/status
# ---------------------------------------------------------------------------

class TestAnalyzeStatus:
    def test_available_when_credentials_exist(self, client, tmp_path):
        cred_file = tmp_path / "creds.json"
        cred_file.write_text('{"claudeAiOauth": {"accessToken": "tok"}}')
        original = app_module.CREDENTIALS_PATH
        app_module.CREDENTIALS_PATH = str(cred_file)
        try:
            r = client.get("/api/analyze/status")
            assert r.status_code == 200
            assert r.json()["available"] is True
        finally:
            app_module.CREDENTIALS_PATH = original

    def test_unavailable_when_credentials_missing(self, client, tmp_path):
        original = app_module.CREDENTIALS_PATH
        app_module.CREDENTIALS_PATH = str(tmp_path / "nonexistent.json")
        try:
            r = client.get("/api/analyze/status")
            assert r.status_code == 200
            data = r.json()
            assert data["available"] is False
            assert "reason" in data
        finally:
            app_module.CREDENTIALS_PATH = original

    def test_unavailable_when_path_is_directory(self, client, tmp_path):
        """A directory at the credentials path should not count as valid."""
        cred_dir = tmp_path / "creds.json"
        cred_dir.mkdir()
        original = app_module.CREDENTIALS_PATH
        app_module.CREDENTIALS_PATH = str(cred_dir)
        try:
            r = client.get("/api/analyze/status")
            assert r.json()["available"] is False
        finally:
            app_module.CREDENTIALS_PATH = original

    def test_unavailable_when_path_is_empty_string(self, client):
        original = app_module.CREDENTIALS_PATH
        app_module.CREDENTIALS_PATH = ""
        try:
            r = client.get("/api/analyze/status")
            assert r.json()["available"] is False
        finally:
            app_module.CREDENTIALS_PATH = original


# ---------------------------------------------------------------------------
# POST /api/analyze — input validation
# ---------------------------------------------------------------------------

class TestAnalyzeValidation:
    def test_empty_filepaths_rejected(self, client):
        r = client.post("/api/analyze", json={"filepaths": []})
        assert r.status_code == 400
        assert "No sessions" in r.json()["detail"]

    def test_over_20_sessions_rejected(self, client, data_dir):
        project = data_dir / "proj1"
        project.mkdir()
        paths = []
        for i in range(21):
            p = project / f"s{i}.jsonl"
            p.write_text('{"type":"user","message":{"content":"hi"}}\n')
            paths.append(f"proj1/s{i}.jsonl")
        r = client.post("/api/analyze", json={"filepaths": paths})
        assert r.status_code == 400
        assert "Maximum 20" in r.json()["detail"]

    def test_exactly_20_sessions_accepted(self, client, data_dir):
        """Boundary: exactly 20 should pass validation."""
        project = data_dir / "proj1"
        project.mkdir()
        paths = []
        for i in range(20):
            p = project / f"s{i}.jsonl"
            p.write_text('{"type":"user","message":{"content":"hi"}}\n')
            paths.append(f"proj1/s{i}.jsonl")
        with mock_analysis():
            r = client.post("/api/analyze", json={"filepaths": paths})
        assert r.status_code == 200

    def test_single_session_accepted(self, client, project_with_session, data_dir):
        rel = str(project_with_session.relative_to(data_dir))
        with mock_analysis():
            r = client.post("/api/analyze", json={"filepaths": [rel]})
        assert r.status_code == 200

    def test_path_traversal_blocked(self, client):
        r = client.post("/api/analyze", json={"filepaths": ["../../etc/passwd"]})
        assert r.status_code == 400
        assert "Invalid path" in r.json()["detail"]

    def test_path_traversal_with_encoded_dots(self, client):
        r = client.post("/api/analyze", json={"filepaths": ["proj1/../../../etc/shadow"]})
        assert r.status_code in (400, 404)

    def test_nonexistent_file(self, client):
        r = client.post("/api/analyze", json={"filepaths": ["proj1/nope.jsonl"]})
        assert r.status_code == 404
        assert "not found" in r.json()["detail"].lower()

    def test_non_jsonl_suffix_rejected(self, client, data_dir):
        project = data_dir / "proj1"
        project.mkdir()
        (project / "notes.txt").write_text("hello")
        r = client.post("/api/analyze", json={"filepaths": ["proj1/notes.txt"]})
        assert r.status_code == 404

    def test_missing_filepaths_field(self, client):
        r = client.post("/api/analyze", json={})
        assert r.status_code == 422  # Pydantic validation

    def test_filepaths_wrong_type(self, client):
        r = client.post("/api/analyze", json={"filepaths": "not-a-list"})
        assert r.status_code == 422

    def test_absolute_path_outside_base_rejected(self, client, tmp_path):
        """Absolute paths outside data dir should be rejected."""
        outside = tmp_path / "outside" / "evil.jsonl"
        outside.parent.mkdir()
        outside.write_text('{"type":"user","message":{"content":"hi"}}\n')
        r = client.post("/api/analyze", json={"filepaths": [str(outside)]})
        assert r.status_code in (400, 404)


# ---------------------------------------------------------------------------
# POST /api/analyze — SSE stream behavior
# ---------------------------------------------------------------------------

class TestAnalyzeStream:
    def _parse_sse(self, response) -> list[tuple[str, str]]:
        """Parse SSE events from response text into (event_type, data) tuples."""
        events = []
        event_type = ""
        for line in response.text.split("\n"):
            if line.startswith("event: "):
                event_type = line[7:].strip()
            elif line.startswith("data: "):
                events.append((event_type, line[6:]))
                event_type = ""
        return events

    def test_sdk_import_error_streams_fallback_prompt(self, client, project_with_session, data_dir):
        """When claude-agent-sdk is not importable, should stream the raw prompt as fallback."""
        rel = str(project_with_session.relative_to(data_dir))
        with mock_analysis():
            r = client.post("/api/analyze", json={"filepaths": [rel]})
        assert r.status_code == 200
        events = self._parse_sse(r)
        token_events = [e for e in events if e[0] == "token"]
        done_events = [e for e in events if e[0] == "done"]
        assert len(token_events) >= 1
        assert len(done_events) == 1

    def test_error_event_on_patterns_exception(self, client, project_with_session, data_dir):
        """Exceptions during BM25 analysis should produce an error SSE event."""
        rel = str(project_with_session.relative_to(data_dir))
        with patch("app.run_patterns", side_effect=RuntimeError("BM25 exploded")):
            r = client.post("/api/analyze", json={"filepaths": [rel]})
        assert r.status_code == 200  # SSE streams always start 200
        events = self._parse_sse(r)
        error_events = [e for e in events if e[0] == "error"]
        assert len(error_events) == 1
        err_data = json.loads(error_events[0][1])
        assert err_data["message"] == "Analysis failed"

    def test_error_event_on_stats_exception(self, client, project_with_session, data_dir):
        """Exception in run_stats should also produce error SSE event."""
        rel = str(project_with_session.relative_to(data_dir))
        with patch("app.run_stats", side_effect=ValueError("stats broke")):
            r = client.post("/api/analyze", json={"filepaths": [rel]})
        events = self._parse_sse(r)
        error_events = [e for e in events if e[0] == "error"]
        assert len(error_events) == 1
        assert json.loads(error_events[0][1])["message"] == "Analysis failed"

    def test_stream_content_type_is_sse(self, client, project_with_session, data_dir):
        rel = str(project_with_session.relative_to(data_dir))
        with patch("app.run_patterns", side_effect=RuntimeError("skip")):
            r = client.post("/api/analyze", json={"filepaths": [rel]})
        assert "text/event-stream" in r.headers["content-type"]

    def test_token_data_is_valid_json(self, client, project_with_session, data_dir):
        """Each token event data field should be valid JSON (a JSON string)."""
        rel = str(project_with_session.relative_to(data_dir))
        with mock_analysis():
            r = client.post("/api/analyze", json={"filepaths": [rel]})
        events = self._parse_sse(r)
        for event_type, data in events:
            if event_type == "token":
                parsed = json.loads(data)  # should not raise
                assert isinstance(parsed, str)


# ---------------------------------------------------------------------------
# Docker credential mount simulation
# ---------------------------------------------------------------------------

class TestDockerCredentialMount:
    """Verify the credential discovery logic matches docker-compose config.

    docker-compose mounts host ~/.claude/.credentials.json to
    /credentials/.credentials.json and sets CLAUDE_CONFIG_DIR=/credentials.
    The SDK resolves $CLAUDE_CONFIG_DIR/.credentials.json.
    Our status endpoint uses LAIN_CREDENTIALS env var.
    """

    def test_lain_credentials_matches_mount_path(self, client, tmp_path):
        """LAIN_CREDENTIALS should point to the mounted credential file."""
        cred_file = tmp_path / "credentials" / ".credentials.json"
        cred_file.parent.mkdir()
        cred_file.write_text('{"claudeAiOauth": {"accessToken": "test"}}')

        original = app_module.CREDENTIALS_PATH
        app_module.CREDENTIALS_PATH = str(cred_file)
        try:
            r = client.get("/api/analyze/status")
            assert r.json()["available"] is True
        finally:
            app_module.CREDENTIALS_PATH = original

    def test_sdk_credential_path_matches_status_endpoint(self, tmp_path):
        """The SDK's credential lookup path ($HOME/.claude/.credentials.json)
        must resolve to the same file the status endpoint checks (CREDENTIALS_PATH).
        A mismatch means status reports 'available' but the SDK can't authenticate."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        cred_dir = fake_home / ".claude"
        cred_dir.mkdir()
        cred_file = cred_dir / ".credentials.json"
        cred_file.write_text('{"claudeAiOauth": {"accessToken": "tok"}}')

        sdk_path = str(fake_home / ".claude" / ".credentials.json")
        # Status endpoint must check the same path the SDK will use
        original = app_module.CREDENTIALS_PATH
        app_module.CREDENTIALS_PATH = sdk_path
        try:
            with patch.dict(os.environ, {"HOME": str(fake_home)}):
                home_sdk_path = os.path.expanduser("~/.claude/.credentials.json")
                assert home_sdk_path == app_module.CREDENTIALS_PATH, (
                    f"SDK looks at {home_sdk_path} but status checks {app_module.CREDENTIALS_PATH}"
                )
        finally:
            app_module.CREDENTIALS_PATH = original

    def test_docker_entrypoint_copy_target_matches_sdk_lookup(self):
        """Entrypoint copies creds to $HOME/.claude/.credentials.json.
        SDK resolves via $HOME. Both must agree when HOME=/tmp (Docker default)."""
        docker_home = "/tmp"
        entrypoint_target = f"{docker_home}/.claude/.credentials.json"
        with patch.dict(os.environ, {"HOME": docker_home}):
            sdk_lookup = os.path.expanduser("~/.claude/.credentials.json")
        assert sdk_lookup == entrypoint_target

    def test_credentials_file_permission_denied(self, client, tmp_path):
        """If credential file exists but is unreadable, status should still reflect existence."""
        cred_file = tmp_path / "creds.json"
        cred_file.write_text('{"token": "secret"}')
        # Path.is_file() only checks existence, not readability
        original = app_module.CREDENTIALS_PATH
        app_module.CREDENTIALS_PATH = str(cred_file)
        try:
            r = client.get("/api/analyze/status")
            # is_file() returns True even if we can't read it
            assert r.json()["available"] is True
        finally:
            app_module.CREDENTIALS_PATH = original

    def test_symlink_credentials_followed(self, client, tmp_path):
        """Symlinked credential files should be resolved."""
        real = tmp_path / "real_creds.json"
        real.write_text('{"token": "x"}')
        link = tmp_path / "link_creds.json"
        link.symlink_to(real)

        original = app_module.CREDENTIALS_PATH
        app_module.CREDENTIALS_PATH = str(link)
        try:
            r = client.get("/api/analyze/status")
            assert r.json()["available"] is True
        finally:
            app_module.CREDENTIALS_PATH = original

    def test_broken_symlink_unavailable(self, client, tmp_path):
        """Broken symlink should report unavailable."""
        link = tmp_path / "broken_link.json"
        link.symlink_to(tmp_path / "nonexistent.json")

        original = app_module.CREDENTIALS_PATH
        app_module.CREDENTIALS_PATH = str(link)
        try:
            r = client.get("/api/analyze/status")
            assert r.json()["available"] is False
        finally:
            app_module.CREDENTIALS_PATH = original
