"""Tests for analyze_sessions.py — conversation parsing, stats, prompt building."""

import json
import pytest
from pathlib import Path

from analyze_sessions import _parse_conversation, run_stats, build_analysis_prompt


# ---------------------------------------------------------------------------
# _parse_conversation
# ---------------------------------------------------------------------------

class TestParseConversation:
    def test_basic_user_assistant_turn(self, make_jsonl):
        path = make_jsonl([
            {"type": "user", "timestamp": "T1", "message": {"content": "hello"}},
            {"type": "assistant", "timestamp": "T2", "message": {
                "content": [{"type": "text", "text": "hi back"}],
            }},
        ])
        turns, meta = _parse_conversation(path)
        assert len(turns) == 1
        assert "hello" in turns[0]["text"]
        assert "hi back" in turns[0]["text"]
        assert meta["turn_count"] == 1
        assert meta["first_timestamp"] == "T1"
        assert meta["last_timestamp"] == "T2"

    def test_tool_use_captured(self, make_jsonl):
        path = make_jsonl([
            {"type": "user", "timestamp": "T1", "message": {"content": "read file"}},
            {"type": "assistant", "timestamp": "T2", "message": {
                "content": [
                    {"type": "text", "text": "reading..."},
                    {"type": "tool_use", "name": "Read"},
                    {"type": "tool_use", "name": "Grep"},
                ],
            }},
        ])
        turns, _ = _parse_conversation(path)
        assert "Read" in turns[0]["tool_names"]
        assert "Grep" in turns[0]["tool_names"]
        assert "tools:" in turns[0]["text"]

    def test_consecutive_user_messages_create_separate_turns(self, make_jsonl):
        path = make_jsonl([
            {"type": "user", "timestamp": "T1", "message": {"content": "first"}},
            {"type": "user", "timestamp": "T2", "message": {"content": "second"}},
            {"type": "assistant", "timestamp": "T3", "message": {
                "content": [{"type": "text", "text": "reply"}],
            }},
        ])
        turns, _ = _parse_conversation(path)
        assert len(turns) == 2
        assert "first" in turns[0]["text"]
        assert "second" in turns[1]["text"]

    def test_empty_file(self, make_jsonl):
        path = make_jsonl([])
        turns, meta = _parse_conversation(path)
        assert turns == []
        assert meta["turn_count"] == 0

    def test_malformed_lines_skipped(self, tmp_path):
        p = tmp_path / "bad.jsonl"
        p.write_text('not json\n{"type":"user","timestamp":"T1","message":{"content":"ok"}}\n')
        turns, _ = _parse_conversation(p)
        # The valid user line creates an in-progress turn, saved at end
        assert len(turns) == 1

    def test_non_user_assistant_types_ignored(self, make_jsonl):
        path = make_jsonl([
            {"type": "system", "timestamp": "T0", "message": {"content": "sys"}},
            {"type": "user", "timestamp": "T1", "message": {"content": "hi"}},
            {"type": "custom-title", "customTitle": "title"},
            {"type": "assistant", "timestamp": "T2", "message": {
                "content": [{"type": "text", "text": "yo"}],
            }},
        ])
        turns, _ = _parse_conversation(path)
        assert len(turns) == 1

    def test_slug_captured(self, make_jsonl):
        path = make_jsonl([
            {"slug": "my-project", "type": "user", "timestamp": "T1",
             "message": {"content": "hi"}},
        ])
        turns, meta = _parse_conversation(path)
        assert meta["slug"] == "my-project"
        assert turns[0]["slug"] == "my-project"

    def test_summary_from_first_user_message(self, make_jsonl):
        path = make_jsonl([
            {"type": "user", "timestamp": "T1",
             "message": {"content": "Please refactor the auth module"}},
            {"type": "assistant", "timestamp": "T2", "message": {
                "content": [{"type": "text", "text": "ok"}],
            }},
        ])
        _, meta = _parse_conversation(path)
        assert meta["summary"] == "Please refactor the auth module"

    def test_user_list_content_skipped(self, make_jsonl):
        """User messages with list content (e.g. images) shouldn't start a turn."""
        path = make_jsonl([
            {"type": "user", "timestamp": "T1",
             "message": {"content": [{"type": "image"}]}},
            {"type": "user", "timestamp": "T2",
             "message": {"content": "real message"}},
            {"type": "assistant", "timestamp": "T3", "message": {
                "content": [{"type": "text", "text": "reply"}],
            }},
        ])
        turns, _ = _parse_conversation(path)
        assert len(turns) == 1
        assert "real message" in turns[0]["text"]

    def test_nonexistent_file(self, tmp_path):
        fake = tmp_path / "nope.jsonl"
        turns, meta = _parse_conversation(fake)
        assert turns == []
        assert meta["turn_count"] == 0


# ---------------------------------------------------------------------------
# run_stats
# ---------------------------------------------------------------------------

class TestRunStats:
    def test_zero_files(self):
        result = run_stats([])
        assert result["total_sessions"] == 0
        assert result["total_turns"] == 0
        assert result["tool_frequency"] == {}

    def test_single_file(self, make_jsonl):
        path = make_jsonl([
            {"type": "user", "timestamp": "T1", "message": {"content": "hi"}},
            {"type": "assistant", "timestamp": "T2", "message": {
                "content": [
                    {"type": "text", "text": "yo"},
                    {"type": "tool_use", "name": "Read"},
                ],
            }},
        ])
        result = run_stats([path])
        assert result["total_sessions"] == 1
        assert result["total_turns"] == 1
        assert result["tool_frequency"]["Read"] == 1

    def test_tool_frequency_aggregates(self, make_jsonl):
        path = make_jsonl([
            {"type": "user", "timestamp": "T1", "message": {"content": "q1"}},
            {"type": "assistant", "timestamp": "T2", "message": {
                "content": [{"type": "tool_use", "name": "Read"}],
            }},
            {"type": "user", "timestamp": "T3", "message": {"content": "q2"}},
            {"type": "assistant", "timestamp": "T4", "message": {
                "content": [
                    {"type": "tool_use", "name": "Read"},
                    {"type": "tool_use", "name": "Edit"},
                ],
            }},
        ])
        result = run_stats([path])
        assert result["tool_frequency"]["Read"] == 2
        assert result["tool_frequency"]["Edit"] == 1


# ---------------------------------------------------------------------------
# build_analysis_prompt
# ---------------------------------------------------------------------------

class TestBuildAnalysisPrompt:
    def test_empty_patterns(self):
        prompt = build_analysis_prompt(
            {"patterns": {}, "sessions": []},
            {"total_sessions": 0, "total_turns": 0, "tool_frequency": {}},
        )
        assert "No significant anti-patterns" in prompt
        assert "Sessions analyzed: 0" in prompt

    def test_with_patterns(self):
        patterns = {
            "patterns": {
                "bash_for_file_ops": {
                    "detects": "Bash used instead of dedicated tools",
                    "hits": 3,
                    "top_score": 5.2,
                    "results": [
                        {"session_id": "abc12345", "turn_number": 2,
                         "score": 5.2, "snippet": "cat file.txt"},
                    ],
                },
            },
            "sessions": [],
        }
        stats = {
            "total_sessions": 2,
            "total_turns": 15,
            "tool_frequency": {"Read": 10, "Bash": 5},
        }
        prompt = build_analysis_prompt(patterns, stats)
        assert "bash_for_file_ops" in prompt
        assert "5.2" in prompt
        assert "Sessions analyzed: 2" in prompt
        assert "Read(10)" in prompt

    def test_tool_frequency_display(self):
        stats = {
            "total_sessions": 1,
            "total_turns": 5,
            "tool_frequency": {"Glob": 3, "Grep": 2, "Read": 1},
        }
        prompt = build_analysis_prompt({"patterns": {}, "sessions": []}, stats)
        assert "Glob(3)" in prompt
        assert "Grep(2)" in prompt
