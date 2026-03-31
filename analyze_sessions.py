#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["bm25s==0.3.0"]
# ///
# NOTE: bm25s version here is for standalone `uv run`. Container version
# is pinned in requirements.txt — keep them in sync.
#
# Standalone session analyzer — accepts specific JSONL file paths.
# Test harness for the lain-code workflow-review integration.

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


PATTERNS: dict[str, dict[str, str]] = {
    "permission_fatigue": {
        "query": "permission denied approved allow",
        "detects": "Repeatedly approving the same tools",
    },
    "bash_for_file_ops": {
        "query": "bash cat grep find sed awk echo",
        "detects": "Bash used instead of dedicated tools (Read/Edit/Grep/Glob)",
    },
    "recurring_errors": {
        "query": "error retry failed traceback exception",
        "detects": "Recurring errors and retries",
    },
    "subagent_issues": {
        "query": "task subagent fork context denied",
        "detects": "Subagent context starvation or access failures",
    },
    "context_pressure": {
        "query": "compact context token limit",
        "detects": "Context window pressure and compaction events",
    },
    "glob_via_bash": {
        "query": "find . -name ls -la ls -r find / -type",
        "detects": "Using find/ls instead of Glob tool",
    },
    "grep_via_bash": {
        "query": "grep -r grep -n grep -i rg ripgrep",
        "detects": "Using shell grep/rg instead of Grep tool",
    },
    "edit_via_heredoc": {
        "query": "cat > << EOF tee sed -i awk -i",
        "detects": "Writing files via bash instead of Edit/Write tools",
    },
    "revert_churn": {
        "query": "revert undo restore original rollback go back previous",
        "detects": "Frequent reversals — poor planning or scope disagreement",
    },
    "clarification_loop": {
        "query": "what do you mean clarify which file which directory",
        "detects": "Excessive clarification — under-specified project context",
    },
    "debug_loop": {
        "query": "still failing same error tried that already doesn't work",
        "detects": "Stuck debugging loops",
    },
    "hallucinated_api": {
        "query": "doesn't exist no such attribute ImportError ModuleNotFoundError",
        "detects": "Claude using non-existent APIs",
    },
}


def _parse_conversation(jsonl_path: Path) -> tuple[list[dict], dict]:
    session_id = jsonl_path.stem
    project = jsonl_path.parent.name
    turns: list[dict] = []
    slug = ""
    first_ts = ""
    last_ts = ""
    summary = ""

    current_user_text = ""
    current_assistant_text = ""
    current_tool_names: set[str] = set()
    current_ts = ""
    in_turn = False

    def _save_turn():
        nonlocal current_user_text, current_assistant_text, current_tool_names, current_ts
        if not current_user_text:
            return
        text_parts = [current_user_text, current_assistant_text]
        if current_tool_names:
            text_parts.append("tools: " + " ".join(sorted(current_tool_names)))
        turns.append({
            "text": "\n".join(text_parts),
            "turn_number": len(turns),
            "session_id": session_id,
            "project": project,
            "slug": slug,
            "timestamp": current_ts,
            "tool_names": sorted(current_tool_names),
        })

    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue

                if record.get("slug") and not slug:
                    slug = record["slug"]

                ts = record.get("timestamp", "")
                if ts:
                    if not first_ts:
                        first_ts = ts
                    last_ts = ts

                msg_type = record.get("type")
                if msg_type not in ("user", "assistant"):
                    continue

                message = record.get("message", {})
                content = message.get("content")

                if msg_type == "user" and isinstance(content, str):
                    if in_turn:
                        _save_turn()
                    current_user_text = content
                    current_assistant_text = ""
                    current_tool_names = set()
                    current_ts = ts
                    in_turn = True
                    if not summary:
                        summary = content[:200]

                elif msg_type == "user" and isinstance(content, list):
                    continue

                elif msg_type == "assistant" and isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text":
                            current_assistant_text += block.get("text", "") + "\n"
                        elif block.get("type") == "tool_use":
                            name = block.get("name", "")
                            if name:
                                current_tool_names.add(name)

        if in_turn:
            _save_turn()

    except OSError:
        pass

    metadata = {
        "session_id": session_id,
        "slug": slug,
        "summary": summary,
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
        "turn_count": len(turns),
        "project": project,
    }

    return turns, metadata


def _build_index(corpus: list[dict]):
    import bm25s

    retriever = None
    if corpus:
        corpus_tokens = bm25s.tokenize([e["text"] for e in corpus], stopwords="en")
        retriever = bm25s.BM25()
        retriever.index(corpus_tokens)

    return retriever


def _extract_hits(results, scores, corpus) -> list[dict]:
    hits: list[dict] = []
    for i in range(results.shape[1]):
        doc_idx = results[0, i]
        score = float(scores[0, i])
        if score <= 0:
            continue
        entry = corpus[doc_idx]
        hits.append({
            "session_id": entry["session_id"],
            "turn_number": entry["turn_number"],
            "score": round(score, 4),
            "snippet": entry["text"][:300],
        })
    return hits


def parse_all(files: list[Path]) -> tuple[list[dict], list[dict]]:
    """Parse all files once, return (corpus, sessions)."""
    corpus: list[dict] = []
    sessions: list[dict] = []
    for jsonl_path in files:
        turns, metadata = _parse_conversation(jsonl_path)
        sessions.append(metadata)
        corpus.extend(turns)
    return corpus, sessions


def run_stats(files: list[Path], *, _parsed: tuple[list[dict], list[dict]] | None = None) -> dict:
    corpus, sessions = _parsed if _parsed else parse_all(files)

    tool_counter: Counter[str] = Counter()
    for turn in corpus:
        for name in turn.get("tool_names", []):
            tool_counter[name] += 1

    return {
        "total_sessions": len(sessions),
        "total_turns": len(corpus),
        "tool_frequency": dict(tool_counter.most_common()),
        "sessions": sessions,
    }


def run_patterns(files: list[Path], top_k: int = 5, *, _parsed: tuple[list[dict], list[dict]] | None = None) -> dict:
    corpus, sessions = _parsed if _parsed else parse_all(files)
    retriever = _build_index(corpus)

    if retriever is None or not corpus:
        return {"patterns": {}, "sessions": sessions}

    import bm25s

    results_by_pattern: dict[str, dict] = {}
    for name, info in PATTERNS.items():
        query_tokens = bm25s.tokenize([info["query"]], stopwords="en")
        results, scores = retriever.retrieve(query_tokens, k=min(top_k, len(corpus)))
        hits = _extract_hits(results, scores, corpus)

        if hits:
            results_by_pattern[name] = {
                "detects": info["detects"],
                "hits": len(hits),
                "top_score": hits[0]["score"],
                "results": hits,
            }

    sorted_patterns = dict(
        sorted(results_by_pattern.items(), key=lambda x: x[1]["top_score"], reverse=True)
    )

    return {"patterns": sorted_patterns, "sessions": sessions}


def run_search(files: list[Path], query: str, top_k: int = 10, *, _parsed: tuple[list[dict], list[dict]] | None = None) -> dict:
    corpus, sessions = _parsed if _parsed else parse_all(files)
    retriever = _build_index(corpus)

    if retriever is None or not corpus:
        return {"results": [], "query": query, "sessions": sessions}

    import bm25s

    query_tokens = bm25s.tokenize([query], stopwords="en")
    results, scores = retriever.retrieve(query_tokens, k=min(top_k, len(corpus)))
    search_results = _extract_hits(results, scores, corpus)

    return {"results": search_results, "query": query, "sessions": sessions}


def build_analysis_prompt(patterns: dict, stats: dict) -> str:
    """Format BM25 pattern results + stats into a prompt for Claude."""
    lines = [
        "You are analyzing Claude Code session transcripts for workflow anti-patterns.",
        "Below are BM25 pattern-match results and usage stats from the selected sessions.",
        "Produce numbered, actionable recommendations. Be concise — this renders in a dashboard panel.",
        "Only mention patterns with meaningful signal. Skip noise. Focus on what the user should change.",
        "",
        f"## Stats",
        f"- Sessions analyzed: {stats['total_sessions']}",
        f"- Total turns: {stats['total_turns']}",
    ]

    if stats.get("tool_frequency"):
        top_tools = list(stats["tool_frequency"].items())[:10]
        lines.append(f"- Top tools: {', '.join(f'{n}({c})' for n, c in top_tools)}")

    lines.append("")
    lines.append("## Pattern Results")

    if not patterns.get("patterns"):
        lines.append("No significant anti-patterns detected.")
    else:
        for name, info in patterns["patterns"].items():
            lines.append(f"\n### {name} (top score: {info['top_score']:.1f}, {info['hits']} hits)")
            lines.append(f"Detects: {info['detects']}")
            for hit in info["results"][:3]:
                snippet = hit["snippet"].replace("\n", " ")[:200]
                lines.append(f"  - [{hit['session_id'][:8]} t{hit['turn_number']}] (score {hit['score']:.1f}) {snippet}")

    lines.append("")
    lines.append("Give 3-7 numbered recommendations. Each should have a one-line title and 1-2 sentences of explanation.")
    lines.append("If no meaningful patterns exist, say so briefly.")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze specific Claude Code session JSONL files",
    )
    parser.add_argument("files", nargs="+", help="One or more .jsonl file paths")
    parser.add_argument(
        "--mode",
        choices=["stats", "patterns", "search"],
        default="patterns",
        help="Analysis mode (default: patterns)",
    )
    parser.add_argument("--query", default=None, help="Search query (required for search mode)")
    parser.add_argument("--top-k", type=int, default=5, help="Max results per pattern (default: 5)")
    args = parser.parse_args()

    paths: list[Path] = []
    for f in args.files:
        p = Path(f).resolve()
        if not p.is_file():
            print(f"Not a file: {p}", file=sys.stderr)
            sys.exit(1)
        paths.append(p)

    if args.mode == "stats":
        result = run_stats(paths)
    elif args.mode == "patterns":
        result = run_patterns(paths, args.top_k)
    elif args.mode == "search":
        if not args.query:
            print("Error: --query required for search mode", file=sys.stderr)
            sys.exit(1)
        result = run_search(paths, args.query, args.top_k)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
